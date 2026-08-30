import os
import time
import boto3
from prometheus_client import CollectorRegistry, Counter, push_to_gateway, delete_from_gateway

# Define Pushgateway Endpoint & Target Job Name (Ensure http:// scheme is included)
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "http://pushgateway-prometheus-pushgateway.default.svc:9091")
JOB_NAME = "finops-reaper"

# Create a dedicated registry to isolate application metrics from standard Python process metrics
registry = CollectorRegistry()

# Define Prometheus Counters attached to the dedicated registry
SCANNED_COUNTER = Counter(
    'finops_resources_scanned_total', 
    'Total number of cloud resources scanned', 
    registry=registry
)
FLAGGED_COUNTER = Counter(
    'finops_resources_flagged_total', 
    'Total number of idle resources flagged for remediation', 
    registry=registry
)

FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

def get_dynamodb_table():
    """Lazily initialize DynamoDB connection inside function scope."""
    dynamodb = boto3.resource(
        "dynamodb",
        endpoint_url=FLOCI_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "floci"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "floci")
    )
    return dynamodb.Table("idle-resources-tracker")

def scan_idle_resources():
    print("🔍 Scanning infrastructure for idle/unmanaged resources...")
    detected_resources = [
        {"ResourceId": "i-098a7b6c54321dev", "ResourceType": "EC2 Instance", "Owner": "DevTeam-Alpha", "DailyCost": "$8.50"},
        {"ResourceId": "vol-0123456789unused", "ResourceType": "EBS Volume", "Owner": "Unassigned", "DailyCost": "$3.20"}
    ]
    for _ in detected_resources:
        SCANNED_COUNTER.inc()
    return detected_resources

def record_and_alert(resources):
    table = get_dynamodb_table()
    deletion_deadline = int(time.time()) + (24 * 60 * 60)
    
    for item in resources:
        try:
            table.put_item(
                Item={
                    "ResourceId": item["ResourceId"],
                    "ResourceType": item["ResourceType"],
                    "Owner": item["Owner"],
                    "DailyCost": item["DailyCost"],
                    "Status": "FLAGGED_FOR_DELETION",
                    "DeletionDeadline": deletion_deadline
                }
            )
            FLAGGED_COUNTER.inc()
            print(f"✅ Recorded {item['ResourceId']} into DynamoDB tracker.")
        except Exception as e:
            print(f"⚠️ DynamoDB write skipped/failed: {e}")

if __name__ == "__main__":
    # 1. Clear previous metric state from Pushgateway for this job
    try:
        print(f"🧹 Clearing old metrics for job '{JOB_NAME}' from Pushgateway at {PUSHGATEWAY_URL}...")
        delete_from_gateway(PUSHGATEWAY_URL, job=JOB_NAME)
        print("✅ Previous metrics cleared successfully.")
    except Exception as e:
        print(f"ℹ️ Pre-execution metric deletion skipped (gateway empty or initial run): {e}")

    # 2. Run core business logic
    idle_items = scan_idle_resources()
    record_and_alert(idle_items)

    # 3. Push fresh metrics to Pushgateway before exiting
    try:
        print(f"🚀 Pushing metrics to Pushgateway at {PUSHGATEWAY_URL}...")
        push_to_gateway(PUSHGATEWAY_URL, job=JOB_NAME, registry=registry)
        print("✅ Reaper metrics successfully pushed to Pushgateway.")
    except Exception as e:
        print(f"❌ Failed to push metrics to Pushgateway: {e}")