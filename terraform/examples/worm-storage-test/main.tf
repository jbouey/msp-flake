# WORM Storage Test Deployment
#
# This creates a test WORM storage bucket for evidence bundles
# with S3 Object Lock in COMPLIANCE mode
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Configure AWS provider
# Set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment variables
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "MSP-Compliance-Platform"
      Environment = "test"
      ManagedBy   = "Terraform"
    }
  }
}

variable "aws_region" {
  description = "AWS region for WORM storage"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Name for WORM storage bucket (must be globally unique)"
  type        = string
  default     = "msp-evidence-worm-test"

  # Note: Change this to something unique for your deployment
  # Bucket names must be globally unique across all AWS accounts
}

# Deploy WORM storage module
module "worm_storage" {
  source = "../../modules/worm-storage"

  bucket_name                = var.bucket_name
  retention_days             = 90  # HIPAA minimum
  lifecycle_transition_days  = 30  # Move to Glacier after 30 days

  tags = {
    TestDeployment = "true"
    Client         = "test-clinic"
  }
}

# Create IAM user for evidence uploader service (for testing)
resource "aws_iam_user" "evidence_uploader_test" {
  name = "msp-evidence-uploader-test"
  path = "/service-accounts/"

  tags = {
    Purpose = "WORM storage upload testing"
  }
}

# Attach uploader policy to test user
resource "aws_iam_user_policy_attachment" "uploader_test" {
  user       = aws_iam_user.evidence_uploader_test.name
  policy_arn = module.worm_storage.uploader_policy_arn
}

# Create access key for test user
resource "aws_iam_access_key" "uploader_test" {
  user = aws_iam_user.evidence_uploader_test.name
}

# Outputs
output "worm_bucket_name" {
  description = "Name of WORM storage bucket"
  value       = module.worm_storage.bucket_name
}

output "worm_bucket_arn" {
  description = "ARN of WORM storage bucket"
  value       = module.worm_storage.bucket_arn
}

output "worm_bucket_region" {
  description = "Region where WORM bucket was created"
  value       = module.worm_storage.bucket_region
}

output "object_lock_enabled" {
  description = "Confirmation that Object Lock is enabled"
  value       = module.worm_storage.object_lock_enabled
}

output "retention_days" {
  description = "Default retention period"
  value       = module.worm_storage.retention_days
}

output "uploader_access_key_id" {
  description = "Access key ID for test uploader (SENSITIVE - store in vault)"
  value       = aws_iam_access_key.uploader_test.id
  sensitive   = false  # Set to false for testing only, true in production
}

output "uploader_secret_access_key" {
  description = "Secret access key for test uploader (SENSITIVE - store in vault)"
  value       = aws_iam_access_key.uploader_test.secret
  sensitive   = true
}

# Instructions output
output "next_steps" {
  value = <<-EOT

  ✅ WORM Storage Deployed Successfully!

  Bucket Name: ${module.worm_storage.bucket_name}
  Region: ${module.worm_storage.bucket_region}
  Object Lock: ${module.worm_storage.object_lock_enabled ? "ENABLED" : "DISABLED"}
  Retention: ${module.worm_storage.retention_days} days

  Next Steps:

  1. Store credentials securely:
     export AWS_ACCESS_KEY_ID="${aws_iam_access_key.uploader_test.id}"
     export AWS_SECRET_ACCESS_KEY="<from terraform output -raw uploader_secret_access_key>"

  2. Test evidence upload:
     cd mcp-server/evidence
     python3 test_worm_upload.py

  3. Verify Object Lock:
     aws s3api head-object --bucket ${module.worm_storage.bucket_name} --key evidence/test.json

  4. Try to delete (should fail with AccessDenied):
     aws s3 rm s3://${module.worm_storage.bucket_name}/evidence/test.json

  HIPAA Controls Satisfied:
  - §164.310(d)(2)(iv): Data Backup and Storage (immutable)
  - §164.312(c)(1): Integrity Controls (tamper-evident)

  EOT
}
