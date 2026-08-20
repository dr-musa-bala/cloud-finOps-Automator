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
  
  # Force path-style addressing for S3 in local emulation
  s3_use_path_style           = true

  endpoints {
    dynamodb = "http://127.0.0.1:4566"
    s3       = "http://127.0.0.1:4566"
    iam      = "http://127.0.0.1:4566"
    sqs      = "http://127.0.0.1:4566"
  }
}

# DynamoDB Table
resource "aws_dynamodb_table" "idle_resources" {
  name         = "idle-resources-tracker"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ResourceId"

  attribute {
    name = "ResourceId"
    type = "S"
  }

  tags = {
    Environment = "Local-Floci"
    Project     = "Cloud-FinOps-Automator"
  }
}

# S3 Bucket for archiving cost audit logs
resource "aws_s3_bucket" "audit_logs" {
  bucket = "finops-audit-logs-local"
}
