This comprehensive technical documentation details everything built, tested, and resolved for the **Cloud-FinOps Automator & Idle Resource Reaper** project.

---

## Project Overview

The **Cloud-FinOps Automator** is an event-driven, local-first FinOps governance engine. It automatically identifies idle, un-tagged, or unused cloud infrastructure, records flagged assets with a 24-hour expiration timestamp, alerts stakeholders, and auto-remediates expired resources while archiving JSON compliance logs to object storage—all running at $0 cloud cost via local AWS emulation.

---

## Phase 1: Local AWS Emulation (Floci & AWS CLI)

### Objective

Deploy a local AWS API emulator to mock DynamoDB and S3 services without relying on live AWS credentials or incurring charges.

### Implementation Steps

1. Launched the Floci emulator container via Docker, exposing port `4566`.
2. Configured the AWS CLI to point credentials to the local emulator environment.

### Issues Encountered & Resolution Navigation

#### Issue 1.1: Docker Container Name Conflict

* **Error Output:**
```text
docker: Error response from daemon: Conflict. The container name "/floci" is already in use by container "349d03b5bf34...". You have to remove (or rename) that container to be able to reuse that name.

```


* **Root Cause:** A container named `floci` was previously created and remained in a stopped or running state.
* **Navigation/Fix:** Force-removed the existing container before re-running the creation command:
```bash
docker rm -f floci
docker run -d --name floci -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock floci/floci:latest

```



#### Issue 1.2: AWS Credentials File Permission Warning

* **Error Output:**
```text
aws: [WARNING]: The file '/home/dr-musa/.aws/credentials' is accessible by other users. Consider running 'chmod 600 /home/dr-musa/.aws/credentials' to restrict access to only your user.

```


* **Root Cause:** Default file permissions on Linux allowed read access to other system users.
* **Navigation/Fix:** Applied restrictive read/write permissions (`600`) to both the AWS credentials and config files:
```bash
chmod 600 ~/.aws/credentials
chmod 600 ~/.aws/config

```



---

## Phase 2: Infrastructure as Code (Terraform)

### Objective

Declaratively provision the DynamoDB resource-tracking table (`idle-resources-tracker`) and the S3 audit logging bucket (`finops-audit-logs-local`) against the Floci emulator endpoint (`http://localhost:4566`).

### Implementation Steps

1. Created `main.tf` defining the AWS provider with endpoint overrides pointing to port `4566`.
2. Defined `aws_dynamodb_table.idle_resources` with `ResourceId` as the String primary partition key.
3. Defined `aws_s3_bucket.audit_logs` for compliance record storage.

### Issues Encountered & Resolution Navigation

#### Issue 2.1: Local S3 DNS Virtual-Host Lookup Failure

* **Error Output:**
```text
Error: creating S3 Bucket (finops-audit-logs-local): operation error S3: CreateBucket, https response error StatusCode: 0, RequestID: , HostID: , request send failed, Put "http://finops-audit-logs-local.localhost:4566/": dial tcp: lookup finops-audit-logs-local.localhost on 8.8.8.8:53: no such host

```


* **Root Cause:** By default, the AWS Terraform provider uses virtual-hosted style bucket addressing (`bucket.localhost:4566`). System DNS routed this request to Google DNS (`8.8.8.8`), which failed because the domain does not exist on the public internet.
* **Navigation/Fix:** Modified `main.tf` to explicitly enforce path-style S3 routing (`localhost:4566/bucket`):
```hcl
provider "aws" {
  region                      = "us-east-1"
  access_key                  = "floci"
  secret_key                  = "floci"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true # Forces path-style addressing

  endpoints {
    dynamodb = "http://localhost:4566"
    s3       = "http://localhost:4566"
  }
}

```



### Verification & Output

After applying the fix, Terraform executed successfully:

```bash
terraform apply -auto-approve
# Output: Apply complete! Resources: 2 added, 0 changed, 0 destroyed.

```

---

## Phase 3: FinOps Scanner Microservice (`reaper.py`)

### Objective

Develop a Python microservice using `boto3` that scans infrastructure for idle resources, calculates a 24-hour Unix expiration timestamp (`DeletionDeadline`), logs records to DynamoDB, and generates structured alert payloads.

### Issues Encountered & Resolution Navigation

#### Issue 3.1: System Python Externally Managed Environment (PEP 668)

* **Error Output:**
```text
error: externally-managed-environment
× This environment is externally managed

```


* **Root Cause:** Linux security policies prevent `pip` from modifying global system Python packages directly.
* **Navigation/Fix:** Created and activated an isolated Python Virtual Environment (`venv`):
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install boto3 requests

```



#### Issue 3.2: Missing Script File Execution Error

* **Error Output:**
```text
python3: can't open file '/home/dr-musa/cloud-finOps-Automator/reaper.py': [Errno 2] No such file or directory

```


* **Root Cause:** Attempted to execute `reaper.py` before writing the file contents to disk.
* **Navigation/Fix:** Authored `reaper.py` using `nano`, defining explicit Floci SDK endpoints and a `scan_and_alert` routine.

### Verification & Output

Ran `python3 reaper.py` and verified the resulting DynamoDB state via the AWS CLI:

```bash
aws --endpoint-url=http://localhost:4566 dynamodb scan --table-name idle-resources-tracker

```

```json
{
    "Items": [
        {
            "ResourceId": { "S": "i-098a7b6c54321dev" },
            "ResourceType": { "S": "EC2 Instance" },
            "Owner": { "S": "DevTeam-Alpha" },
            "DailyCost": { "S": "$8.50" },
            "Status": { "S": "FLAGGED_FOR_DELETION" },
            "DeletionDeadline": { "N": "1787176420" }
        },
        {
            "ResourceId": { "S": "vol-0123456789unused" },
            "ResourceType": { "S": "EBS Volume" },
            "Owner": { "S": "Unassigned" },
            "DailyCost": { "S": "$3.20" },
            "Status": { "S": "FLAGGED_FOR_DELETION" },
            "DeletionDeadline": { "N": "1787176420" }
        }
    ],
    "Count": 2
}

```

---

## Phase 4: Automated Remediation Engine (`cleaner.py`)

### Objective

Engineered an auto-remediator script that evaluates DynamoDB records against the current Unix time, transitions expired items to `TERMINATED`, and publishes audit log JSON artifacts to S3.

### Execution & Simulation

1. **Simulated Expiration:** Manually updated the `DeletionDeadline` of `vol-0123456789unused` to `0` via the AWS CLI:
```bash
aws --endpoint-url=http://localhost:4566 dynamodb update-item \
    --table-name idle-resources-tracker \
    --key '{"ResourceId": {"S": "vol-0123456789unused"}}' \
    --update-expression "SET DeletionDeadline = :d" \
    --expression-attribute-values '{":d": {"N": "0"}}'

```


2. **Executed Remediation:**
```bash
python3 cleaner.py

```


*Execution Output:*
```text
⏰ Running Auto-Remediator check at Unix timestamp: 1787129207
🔥 [PURGING] Resource vol-0123456789unused has passed deadline! Executing cleanup...
📦 Audit log saved to S3: s3://finops-audit-logs-local/audit-logs/vol-0123456789unused-1787129207.json

```


3. **S3 Storage Verification:**
```bash
aws --endpoint-url=http://localhost:4566 s3 ls s3://finops-audit-logs-local/audit-logs/
# Output: 2026-08-19 11:46:47 192 vol-0123456789unused-1787129207.json

```



---

## Summary of Architecture State

| Layer | Technology | Status | Verified Function |
| --- | --- | --- | --- |
| **Cloud Emulation** | Docker + Floci | ✅ Operational | Mocks AWS DynamoDB & S3 APIs on port 4566 |
| **IaC** | Terraform (AWS Provider) | ✅ Deployed | Manages state schema with path-style S3 routing |
| **Detection Engine** | Python (`boto3`) + `venv` | ✅ Operational | Identifies $11.70/day in waste & writes state TTLs |
| **Remediation & Audit** | Python + S3 JSON Logger | ✅ Operational | Auto-purges expired resources & archives compliance logs |
