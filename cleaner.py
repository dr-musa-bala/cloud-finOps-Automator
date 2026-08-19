import time
import json
import boto3

FLOCI_ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"

# Initialize DynamoDB and S3 clients pointing to Floci
dynamodb = boto3.resource(
    "dynamodb",
    endpoint_url=FLOCI_ENDPOINT,
    region_name=REGION,
    aws_access_key_id="floci",
    aws_secret_access_key="floci"
)

s3 = boto3.client(
    "s3",
    endpoint_url=FLOCI_ENDPOINT,
    region_name=REGION,
    aws_access_key_id="floci",
    aws_secret_access_key="floci"
)

table = dynamodb.Table("idle-resources-tracker")
AUDIT_BUCKET = "finops-audit-logs-local"

def process_expired_resources():
    current_time = int(time.time())
    print(f"⏰ Running Auto-Remediator check at Unix timestamp: {current_time}")

    # Scan DynamoDB for flagged items
    response = table.scan()
    items = response.get("Items", [])

    purged_count = 0

    for item in items:
        resource_id = item["ResourceId"]
        deadline = int(item.get("DeletionDeadline", 0))
        status = item.get("Status")

        # Check if the resource is flagged AND its deadline has passed
        # For testing purposes, we check if deadline <= current_time OR if we want to simulate an expired deadline
        if status == "FLAGGED_FOR_DELETION" and current_time >= deadline:
            print(f"🔥 [PURGING] Resource {resource_id} has passed deadline! Executing cleanup...")

            # 1. Update status in DynamoDB to 'TERMINATED'
            table.update_item(
                Key={"ResourceId": resource_id},
                UpdateExpression="SET #s = :term, TerminatedAt = :t",
                ExpressionAttributeNames={"#s": "Status"},
                ExpressionAttributeValues={
                    ":term": "TERMINATED",
                    ":t": current_time
                }
            )

            # 2. Generate Audit Log JSON
            audit_log = {
                "ResourceId": resource_id,
                "ResourceType": item.get("ResourceType"),
                "Owner": item.get("Owner"),
                "DailyCostSaved": item.get("DailyCost"),
                "Action": "AUTOMATED_TERMINATION",
                "Timestamp": current_time
            }

            # 3. Upload Audit Log to S3 in Floci
            log_key = f"audit-logs/{resource_id}-{current_time}.json"
            s3.put_object(
                Bucket=AUDIT_BUCKET,
                Key=log_key,
                Body=json.dumps(audit_log, indent=2),
                ContentType="application/json"
            )
            print(f"📦 Audit log saved to S3: s3://{AUDIT_BUCKET}/{log_key}")
            purged_count += 1

    if purged_count == 0:
        print("ℹ️ No expired resources found ready for termination.")

if __name__ == "__main__":
    process_expired_resources()
