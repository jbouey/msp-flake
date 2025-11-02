# WORM Storage Module - S3 with Object Lock
#
# Creates HIPAA-compliant immutable evidence storage using AWS S3 Object Lock
# in COMPLIANCE mode. Once objects are uploaded with retention, they cannot be
# deleted or modified by anyone (including root account) until retention expires.
#
# HIPAA Controls:
# - §164.310(d)(2)(iv) - Data Backup and Storage
# - §164.312(c)(1) - Integrity Controls
#
# Author: MSP Compliance Platform
# Version: 1.0.0

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "bucket_name" {
  description = "Name for the WORM storage bucket (must be globally unique)"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Bucket name must be 3-63 characters, lowercase, and DNS-compatible"
  }
}

variable "retention_days" {
  description = "Minimum retention period for evidence bundles (HIPAA recommends 90+ days)"
  type        = number
  default     = 90

  validation {
    condition     = var.retention_days >= 90
    error_message = "HIPAA evidence should be retained for at least 90 days"
  }
}

variable "lifecycle_transition_days" {
  description = "Days before transitioning to cheaper storage class (Glacier)"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    ManagedBy = "Terraform"
    Purpose   = "HIPAA-Compliance-Evidence"
  }
}

# S3 Bucket with Object Lock
resource "aws_s3_bucket" "worm_storage" {
  bucket = var.bucket_name

  # Object Lock MUST be enabled at bucket creation
  # Cannot be added to existing bucket
  object_lock_enabled = true

  tags = merge(var.tags, {
    Name = var.bucket_name
  })
}

# Block all public access (HIPAA requirement)
resource "aws_s3_bucket_public_access_block" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning (required for Object Lock)
resource "aws_s3_bucket_versioning" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption (HIPAA requirement)
resource "aws_s3_bucket_server_side_encryption_configuration" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Object Lock configuration
# Default retention mode: COMPLIANCE (cannot be shortened or removed)
resource "aws_s3_bucket_object_lock_configuration" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.retention_days
    }
  }
}

# Lifecycle policy: Transition to Glacier after 30 days
resource "aws_s3_bucket_lifecycle_configuration" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    transition {
      days          = var.lifecycle_transition_days
      storage_class = "GLACIER"
    }

    # Expire objects after retention period + grace period
    expiration {
      days = var.retention_days + 30
    }

    filter {
      prefix = "evidence/"
    }
  }
}

# Bucket policy: Enforce SSL/TLS and deny insecure transport
resource "aws_s3_bucket_policy" "worm_storage" {
  bucket = aws_s3_bucket.worm_storage.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyInsecureTransport"
        Effect = "Deny"
        Principal = "*"
        Action = "s3:*"
        Resource = [
          "${aws_s3_bucket.worm_storage.arn}",
          "${aws_s3_bucket.worm_storage.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# IAM policy for evidence uploader service
resource "aws_iam_policy" "evidence_uploader" {
  name        = "${var.bucket_name}-uploader-policy"
  description = "Policy for MCP evidence uploader service"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEvidenceUpload"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectRetention",
          "s3:PutObjectLegalHold",
          "s3:GetObject",
          "s3:GetObjectRetention",
          "s3:GetObjectLegalHold"
        ]
        Resource = "${aws_s3_bucket.worm_storage.arn}/evidence/*"
      },
      {
        Sid    = "AllowBucketOperations"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetObjectLockConfiguration"
        ]
        Resource = aws_s3_bucket.worm_storage.arn
      }
    ]
  })

  tags = var.tags
}

# Outputs
output "bucket_name" {
  description = "Name of the WORM storage bucket"
  value       = aws_s3_bucket.worm_storage.id
}

output "bucket_arn" {
  description = "ARN of the WORM storage bucket"
  value       = aws_s3_bucket.worm_storage.arn
}

output "bucket_region" {
  description = "AWS region where bucket is created"
  value       = aws_s3_bucket.worm_storage.region
}

output "uploader_policy_arn" {
  description = "ARN of IAM policy for evidence uploader service"
  value       = aws_iam_policy.evidence_uploader.arn
}

output "object_lock_enabled" {
  description = "Confirmation that Object Lock is enabled"
  value       = aws_s3_bucket.worm_storage.object_lock_enabled
}

output "retention_days" {
  description = "Default retention period in days"
  value       = var.retention_days
}
