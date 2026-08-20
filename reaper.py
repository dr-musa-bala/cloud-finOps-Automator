import os
import time
import boto3
import requests

# Pointing boto3 to Floci AWS Emulator on port 4566
FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT", "http://localhost:4566")
REGION = "us-east-1"

# Initialize DynamoDB Resource Client
dynamodb = boto3.resource(
    "dynamodb",
    endpoint_url=FLOCI_ENDPOINT,
    region_name=REGION,
    aws_access_key_id="floci",
    aws_secret_access_key="floci"
)

# Connect to the table created by Terraform
table = dynamodb.Table("idle-resources-tracker")

def scan_idle_resources():
    """
    Simulates scanning AWS/K8s for un-tagged or idle resources.
    """
    print("🔍 Scanning infrastructure for idle/unmanaged resources...")
    
    detected_resources = [
        {
            "ResourceId": "i-098a7b6c54321dev",
            "ResourceType": "EC2 Instance",
            "Owner": "DevTeam-Alpha",
            "DailyCost": "$8.50",
            "Status": "IDLE"
        },
        {
            "ResourceId": "vol-0123456789unused",
            "ResourceType": "EBS Volume",
            "Owner": "Unassigned",
            "DailyCost": "$3.20",
            "Status": "UNATTACHED"
        }
    ]
    return detected_resources

def record_and_alert(resources):
    """
    Records flagged resources in DynamoDB with a 24-hour TTL and prints alert payload.
    """
    deletion_deadline = int(time.time()) + (24 * 60 * 60)
    
    for item in resources:
        resource_id = item["ResourceId"]
        
        # 1. Store item state in DynamoDB
        table.put_item(
            Item={
                "ResourceId": resource_id,
                "ResourceType": item["ResourceType"],
                "Owner": item["Owner"],
                "DailyCost": item["DailyCost"],
                "Status": "FLAGGED_FOR_DELETION",
                "DeletionDeadline": deletion_deadline
            }
        )
        print(f"✅ Recorded {resource_id} into Floci DynamoDB.")

        # 2. Print Alert Notification
        webhook_payload = {
            "text": f"⚠️ *FinOps Alert: Idle Resource Detected*\n"
                    f"• *ID:* `{resource_id}` ({item['ResourceType']})\n"
                    f"• *Owner:* {item['Owner']}\n"
                    f"• *Wasted Cost:* {item['DailyCost']}/day\n"
                    f"• *Action:* Will be automatically purged in 24 hours unless acknowledged."
        }
        
        print(f"📢 Notification Payload Ready for {resource_id}:")
        print(webhook_payload["text"])
        print("-" * 50)

if __name__ == "__main__":
    idle_items = scan_idle_resources()
    record_and_alert(idle_items)
