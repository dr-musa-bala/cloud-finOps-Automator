import os
import json
import time
import boto3
from prometheus_client import (
    CollectorRegistry,
    Counter,
    push_to_gateway,
    delete_from_gateway,
)

# Define Pushgateway Endpoint & Target Job Name (Ensure http:// scheme is included)
PUSHGATEWAY_URL = os.getenv(
    "PUSHGATEWAY_URL", "http://pushgateway-prometheus-pushgateway.default.svc:9091"
)
JOB_NAME = "finops-cleaner"

# Create a dedicated registry to isolate application metrics from standard Python process metrics
registry = CollectorRegistry()

# Define Prometheus Counters attached to the dedicated registry
PURGED_COUNTER = Counter(
    "finops_resources_purged_total",
    "Total number of idle resources purged",
    registry=registry,
)
AUDIT_LOG_COUNTER = Counter(
    "finops_s3_audit_logs_written_total",
    "Total number of audit logs saved to S3",
    registry=registry,
)

FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BUCKET_NAME = "finops-audit-logs-local"


def get_aws_clients():
    """Lazily initialize AWS resources."""
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "floci")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "floci")

    dynamodb = boto3.resource(
        "dynamodb",
        endpoint_url=FLOCI_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    s3 = boto3.client(
        "s3",
        endpoint_url=FLOCI_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return dynamodb.Table("idle-resources-tracker"), s3


def run_remediation():
    now = int(time.time())
    print(f"⏰ Running Auto-Remediator check at timestamp: {now}")
    try:
        table, s3 = get_aws_clients()
        response = table.scan()
        items = response.get("Items", [])

        for item in items:
            deadline = int(item.get("DeletionDeadline", 0))
            resource_id = item.get("ResourceId")

            if now >= deadline:
                table.delete_item(Key={"ResourceId": resource_id})
                PURGED_COUNTER.inc()

                audit_payload = {
                    "ResourceId": resource_id,
                    "Action": "PURGED",
                    "Timestamp": now,
                }
                s3.put_object(
                    Bucket=BUCKET_NAME,
                    Key=f"audit-logs/{resource_id}-{now}.json",
                    Body=json.dumps(audit_payload),
                )
                AUDIT_LOG_COUNTER.inc()
                print(f"🔥 Purged {resource_id} and recorded audit log.")
    except Exception as e:
        print(f"⚠️ Remediation run skipped/failed: {e}")


if __name__ == "__main__":
    # 1. Clear previous metric state from Pushgateway for this job
    try:
        print(
            f"🧹 Clearing old metrics for job '{JOB_NAME}' from Pushgateway at {PUSHGATEWAY_URL}..."
        )
        delete_from_gateway(PUSHGATEWAY_URL, job=JOB_NAME)
        print("✅ Previous metrics cleared successfully.")
    except Exception as e:
        print(
            f"ℹ️ Pre-execution metric deletion skipped (gateway empty or initial run): {e}"
        )

    # 2. Execute core business logic
    run_remediation()

    # 3. Push fresh metrics to Pushgateway before exiting
    try:
        print(f"🚀 Pushing metrics to Pushgateway at {PUSHGATEWAY_URL}...")
        push_to_gateway(PUSHGATEWAY_URL, job=JOB_NAME, registry=registry)
        print("✅ Cleaner metrics successfully pushed to Pushgateway.")
    except Exception as e:
        print(f"❌ Failed to push metrics to Pushgateway: {e}")
