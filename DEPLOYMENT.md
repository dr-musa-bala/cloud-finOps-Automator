# Cloud FinOps Automator — System Architecture & Deployment Guide

An enterprise-grade, local-first FinOps automation system designed to discover, track, and remediate idle or unmanaged cloud resources. The system moves seamlessly from local development through containerized Kubernetes orchestration and automated CI/CD pipeline validation.

---

## 1. System Architecture & High-Level Design

```
+-----------------------------------------------------------------------------------+
|                                  LOCAL / CI ENVIRONMENT                           |
|                                                                                   |
|  +------------------+         +---------------------+        +-----------------+  |
|  |   reaper.py      |         |     cleaner.py      |        |  Terraform IaC  |  |
|  | (Scanner Engine) |         | (Remediator Engine) |        | (AWS Provider)  |  |
|  +--------+---------+         +----------+----------+        +--------+--------+  |
|           |                              |                            |           |
|           | Discover / Flag              | Scan / Purge               | Provision |
|           v                              v                            v           |
|  +-----------------------------------------------------------------------------+  |
|  |                           Floci / AWS Emulator                              |  |
|  |                                                                             |  |
|  |  +---------------------------------+   +---------------------------------+  |  |
|  |  |  DynamoDB: idle-resources-tracker|   |   S3: finops-audit-logs-local   |  |  |
|  |  +---------------------------------+   +---------------------------------+  |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+

```

---

## 2. Core Components & Technical Stack

* **Language & SDK:** Python 3.11 with `boto3` for AWS service interaction.
* **Infrastructure as Code:** Terraform (version `1.7.0`, AWS provider pinned to `5.82.0`).
* **Cloud Emulator:** Floci / LocalStack exposed on IPv4 loopback (`127.0.0.1:4566`).
* **Containerization:** Docker multi-stage builds.
* **Orchestration:** Kubernetes CronJobs running on Minikube (`v1.32+`).
* **CI/CD:** GitHub Actions with health-check readiness loops.

---

## 3. Infrastructure Definition (`main.tf`)

```hcl
terraform {
  required_version = ">= 1.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.82.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "floci"
  secret_key                  = "floci"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    dynamodb = "http://127.0.0.1:4566"
    s3       = "http://127.0.0.1:4566"
  }
}

resource "aws_dynamodb_table" "idle_resources" {
  name         = "idle-resources-tracker"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ResourceId"

  attribute {
    name = "ResourceId"
    type = "S"
  }

  ttl {
    attribute_name = "DeletionDeadline"
    enabled        = true
  }
}

resource "aws_s3_bucket" "audit_logs" {
  bucket        = "finops-audit-logs-local"
  force_destroy = true
}

```

---

## 4. Microservice Implementation

### **Scanner Service (`reaper.py`)**

Finds idle resources and registers them in DynamoDB with a dynamic retention TTL deadline.

```python
import os
import time
import boto3

FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

dynamodb = boto3.resource(
    "dynamodb",
    endpoint_url=FLOCI_ENDPOINT,
    region_name=REGION,
    aws_access_key_id="floci",
    aws_secret_access_key="floci"
)

table = dynamodb.Table("idle-resources-tracker")

def scan_and_flag():
    now = int(time.time())
    ttl_deadline = now + 86400  # 24-hour retention window

    mock_idle_resources = [
        {"ResourceId": "i-098a7b6c54321dev", "ResourceType": "EC2Instance", "State": "stopped"},
        {"ResourceId": "vol-0123456789unused", "ResourceType": "EBSVolume", "State": "available"}
    ]

    for item in mock_idle_resources:
        table.put_item(
            Item={
                "ResourceId": item["ResourceId"],
                "ResourceType": item["ResourceType"],
                "State": item["State"],
                "DiscoveredAt": now,
                "DeletionDeadline": ttl_deadline
            }
        )
        print(f"Flagged idle resource: {item['ResourceId']} with TTL deadline {ttl_deadline}")

if __name__ == "__main__":
    scan_and_flag()

```

---

### **Remediator Service (`cleaner.py`)**

Evaluates tracked items against the current Unix timestamp, purges expired resources, and writes JSON audit receipts to S3.

```python
import os
import json
import time
import boto3

FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

dynamodb = boto3.resource("dynamodb", endpoint_url=FLOCI_ENDPOINT, region_name=REGION)
s3 = boto3.client("s3", endpoint_url=FLOCI_ENDPOINT, region_name=REGION)

table = dynamodb.Table("idle-resources-tracker")
BUCKET_NAME = "finops-audit-logs-local"

def run_remediation():
    now = int(time.time())
    print(f"⏰ Running Auto-Remediator check at Unix timestamp: {now}")

    response = table.scan()
    items = response.get("Items", [])

    for item in items:
        deadline = int(item.get("DeletionDeadline", 0))
        resource_id = item.get("ResourceId")

        if now >= deadline:
            print(f"🚨 Expired Resource Found: {resource_id} (Deadline: {deadline})")
            
            # Purge record from tracking table
            table.delete_item(Key={"ResourceId": resource_id})
            print(f"🔥 Purged {resource_id} from DynamoDB.")

            # Create audit record in S3
            audit_payload = {
                "ResourceId": resource_id,
                "ResourceType": item.get("ResourceType"),
                "Action": "PURGED",
                "Timestamp": now
            }
            s3_key = f"audit-logs/{resource_id}-{now}.json"
            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=s3_key,
                Body=json.dumps(audit_payload),
                ContentType="application/json"
            )
            print(f"📸 Audit log saved to S3: {s3_key}")

if __name__ == "__main__":
    run_remediation()

```

---

## 5. Kubernetes Orchestration Manifests

### **Reaper CronJob (`k8s/reaper-cronjob.yaml`)**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: finops-reaper-job
spec:
  schedule: "0 * * * *"  # Runs hourly
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: reaper
            image: finops-reaper:latest
            imagePullPolicy: Never
            env:
            - name: FLOCI_ENDPOINT
              value: "http://192.168.49.1:4566"  # Minikube host gateway interface
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"
          restartPolicy: OnFailure

```

---

### **Cleaner CronJob (`k8s/cleaner-cronjob.yaml`)**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: finops-cleaner-job
spec:
  schedule: "30 * * * *"  # Runs hourly at half-past
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: cleaner
            image: finops-cleaner:latest
            imagePullPolicy: Never
            env:
            - name: FLOCI_ENDPOINT
              value: "http://192.168.49.1:4566"
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"
          restartPolicy: OnFailure

```

---

## 6. GitHub Actions CI/CD Pipeline (`.github/workflows/ci.yml`)

```yaml
name: FinOps Automator CI Pipeline

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]

jobs:
  test-finops-pipeline:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout Repository
      uses: actions/checkout@v4

    - name: Set up Python 3.11
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'

    - name: Setup HashiCorp Terraform
      uses: hashicorp/setup-terraform@v3
      with:
        terraform_version: "1.7.0"
        terraform_wrapper: false

    - name: Install Python Dependencies
      run: |
        python -m pip install --upgrade pip
        if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

    - name: Start Floci Container
      run: |
        docker run -d -p 4566:4566 --name floci-emulator floci/floci:latest

    - name: Wait for Floci Readiness
      run: |
        until curl -s http://127.0.0.1:4566/health | grep -q "ok\|running\|200"; do
          echo "Waiting for Floci service to be ready..."
          sleep 2
        done
        echo "Floci is fully ready!"

    - name: Provision Infrastructure via Terraform
      run: |
        terraform init
        terraform apply -auto-approve
      env:
        AWS_ACCESS_KEY_ID: "floci"
        AWS_SECRET_ACCESS_KEY: "floci"
        AWS_DEFAULT_REGION: "us-east-1"

    - name: Test Reaper Microservice
      run: python3 reaper.py
      env:
        FLOCI_ENDPOINT: "http://127.0.0.1:4566"
        AWS_ACCESS_KEY_ID: "floci"
        AWS_SECRET_ACCESS_KEY: "floci"
        AWS_DEFAULT_REGION: "us-east-1"

    - name: Test Cleaner Microservice
      run: python3 cleaner.py
      env:
        FLOCI_ENDPOINT: "http://127.0.0.1:4566"
        AWS_ACCESS_KEY_ID: "floci"
        AWS_SECRET_ACCESS_KEY: "floci"
        AWS_DEFAULT_REGION: "us-east-1"

```

---

## 7. Troubleshooting & Engineering Lessons Learned

* **IPv6 vs. IPv4 Resolution:** Go-based tools (like Terraform) default to resolving `localhost` to IPv6 (`[::1]:4566`). Explicitly setting endpoint URLs to `[http://127.0.0.1:4566](http://127.0.0.1:4566)` forces IPv4 socket binding and prevents `connection refused` errors inside containerized runners.
* **Minikube Networking Gateway:** Pods inside Minikube cannot resolve `localhost` to reach services running on the host machine. Setting `FLOCI_ENDPOINT` to the host gateway IP (`192.168.49.1:4566`) routes cross-namespace requests correctly.
* **Terraform GPG Signature Errors:** Unpinned provider releases download dynamic packages whose signatures can conflict with older runner keyrings. Pinning exact provider versions (`version = "5.82.0"`) and setting `terraform_wrapper: false` ensures reproducible execution.
* **CI Readiness Loops:** Static `sleep` commands cause race conditions when starting emulators in CI. Replacing static delays with active polling (`until curl ...`) guarantees services are accepting connections before downstream jobs execute.

---

## Technical Documentation: Resolving Prometheus Pushgateway Metric Duplication & Discovery Issues

### **Executive Summary**

During the implementation of the `cloud-finOps-Automator` Kubernetes observability pipeline, custom ephemeral Python CronJobs (`reaper` and `cleaner`) pushed operational FinOps counters (`finops_resources_scanned_total`, `flagged_total`, `purged_total`, `s3_audit_logs_written_total`) to a centralized Prometheus Pushgateway.

While metrics were successfully ingested into the Prometheus Time Series Database (TSDB), two distinct structural issues arose:

1. **Metric Duplicate Series Ingestion:** Prometheus TSDB queried and returned two identical metric series for every target execution (e.g., duplicate `finops_resources_scanned_total` outputs).
2. **Pushgateway Target Resolution Failure:** Scrape target definitions failed due to mismatched label selectors across Kubernetes `Service` and `ServiceMonitor` resources.

---

### **Root Cause Analysis**

#### 1. Redundant Service Declarations for Ephemeral Workloads

The initial Kubernetes configuration declared explicit `ClusterIP` Services (`finops-reaper-metrics` and `finops-cleaner-metrics`) targeting ports `8000` and `8001`. Because `reaper` and `cleaner` run as short-lived `CronJobs` rather than long-running HTTP servers listening on persistent ports, exposing dedicated Kubernetes `Service` endpoints for them was invalid. The metrics were already being pushed directly to Pushgateway.

#### 2. Dual Scrape Loops (Pushgateway Service vs. Pod Discovery)

When inspectable via the Prometheus API endpoint `/api/v1/targets`, Prometheus was discovering and scraping Pushgateway via two overlapping definitions simultaneously:

* **Static Scrape Job (`job: "pushgateway"`):** Defined via Helm default values scraping `pushgateway-prometheus-pushgateway.default.svc:9091`.
* **Dynamic Operator Scrape Job (`job: "pushgateway-prometheus-pushgateway"`):** Defined via a custom `ServiceMonitor` scraping the backing Pod IP (`10.244.7.23:9091`).

Both targets ingested metrics from the exact same Pushgateway instance, duplicating every series in TSDB.

#### 3. Kubernetes Label Selector Mismatch

The custom `ServiceMonitor` applied `matchLabels: app: pushgateway`. However, Helm's standard deployment manifest tagged the underlying deployment and pods with `app.kubernetes.io/name: prometheus-pushgateway`. Consequently, selector matching failed and `kubectl exec` targeting services resulted in resolution timeouts.

---

### **Step-by-Step Resolution Path**

#### **Step 1: Terminate Ephemeral Workload Services**

Deleted non-functional Service manifests targeting ephemeral CronJobs.

```bash
kubectl delete svc -l release=kube-prometheus -l 'app in (finops-reaper, finops-cleaner)' --ignore-not-found

```

#### **Step 2: Consolidate Scraping Topology**

Deleted the redundant custom `ServiceMonitor` to allow Prometheus to rely exclusively on the default static service target (`pushgateway-prometheus-pushgateway.default.svc:9091`).

```bash
kubectl delete servicemonitor pushgateway-servicemonitor -n default

```

#### **Step 3: Flush Prometheus TSDB Active Scrape Memory**

Restarted the Prometheus StatefulSet pod to reset internal scrape targets and clear stale endpoints.

```bash
kubectl delete pod prometheus-kube-prometheus-kube-prome-prometheus-0 -n default

```

#### **Step 4: Execute Verification Cycle & Validate Single-Series TSDB Output**

Triggered manual test jobs for the FinOps automation scripts:

```bash
kubectl delete job test-reaper-check test-cleaner-check --ignore-not-found
kubectl create job --from=cronjob/finops-release-reaper test-reaper-check
kubectl create job --from=cronjob/finops-release-cleaner test-cleaner-check

```

Queried Prometheus TSDB via port-forwarding to confirm single-series ingestion:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=finops_resources_scanned_total' | jq '.data.result[] | {metric: .metric.__name__, job: .metric.job, instance: .metric.instance, value: .value[1]}'

```

**Validated Output (Single Deduplicated Series):**

```json
{
  "metric": "finops_resources_scanned_total",
  "job": "finops-reaper",
  "instance": null,
  "value": "2"
}

```

---

## GitHub Deployment Guide

Follow these steps to stage, commit, and push your resolution changes to your remote repository.

### **1. Inspect Repository Status**