# Terraform Module: Client VM Deployment
# Deploys NixOS-based MSP monitoring stations for clients

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

# Variables
variable "client_id" {
  description = "Unique client identifier (e.g., clinic-001)"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.client_id))
    error_message = "client_id must contain only lowercase letters, numbers, and hyphens"
  }
}

variable "client_name" {
  description = "Human-readable client name"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "msp-platform"
}

# Networking
variable "vpc_id" {
  description = "VPC ID for deployment"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for VM deployment"
  type        = string
}

variable "allowed_ssh_cidr_blocks" {
  description = "CIDR blocks allowed to SSH to client VM"
  type        = list(string)
  default     = []
}

# Instance Configuration
variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 50
}

variable "enable_public_ip" {
  description = "Assign public IP to instance"
  type        = bool
  default     = false
}

# MSP Configuration
variable "mcp_server_url" {
  description = "URL of the MCP server"
  type        = string
}

variable "event_queue_url" {
  description = "URL of the event queue (Redis/NATS)"
  type        = string
}

variable "mcp_api_key_secret_arn" {
  description = "ARN of secret containing MCP API key"
  type        = string
  sensitive   = true
}

variable "flake_git_url" {
  description = "Git URL for client flake"
  type        = string
  default     = "github:yourorg/msp-platform"
}

variable "flake_ref" {
  description = "Git ref (branch/tag) for flake"
  type        = string
  default     = "main"
}

# Monitoring Configuration
variable "subnets_to_discover" {
  description = "Subnets to scan for device discovery"
  type        = list(string)
  default     = []
}

variable "enable_discovery" {
  description = "Enable network discovery service"
  type        = bool
  default     = true
}

# Security
variable "enable_encryption" {
  description = "Enable full-disk encryption (LUKS)"
  type        = bool
  default     = true
}

variable "ssh_ca_public_key" {
  description = "SSH CA public key for certificate authentication"
  type        = string
  default     = ""
}

# Tags
variable "tags" {
  description = "Additional tags for resources"
  type        = map(string)
  default     = {}
}

# Local variables
locals {
  common_tags = merge(
    {
      Environment = var.environment
      Project     = var.project_name
      Client      = var.client_name
      ClientId    = var.client_id
      ManagedBy   = "terraform"
      Module      = "client-vm"
      HIPAA       = "compliant"
    },
    var.tags
  )

  name_prefix = "${var.project_name}-${var.environment}-${var.client_id}"
}

# Data Sources

# Get latest NixOS AMI (if available, otherwise use Amazon Linux 2023)
data "aws_ami" "nixos" {
  most_recent = true
  owners      = ["427812963091"] # NixOS official

  filter {
    name   = "name"
    values = ["nixos-23.11-*-x86_64-linux"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Fallback to Amazon Linux 2023 if NixOS AMI not found
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security Group
resource "aws_security_group" "client_vm" {
  name_prefix = "${local.name_prefix}-vm-"
  description = "Security group for MSP client VM - ${var.client_name}"
  vpc_id      = var.vpc_id

  # SSH access
  dynamic "ingress" {
    for_each = length(var.allowed_ssh_cidr_blocks) > 0 ? [1] : []
    content {
      description = "SSH access"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.allowed_ssh_cidr_blocks
    }
  }

  # Watcher health check endpoint (internal only)
  ingress {
    description = "Watcher health check"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
  }

  # Egress - allow all outbound
  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.name_prefix}-vm-sg"
    }
  )

  lifecycle {
    create_before_destroy = true
  }
}

data "aws_vpc" "selected" {
  id = var.vpc_id
}

# IAM Role for EC2 Instance
resource "aws_iam_role" "client_vm" {
  name_prefix = "${local.name_prefix}-vm-"
  description = "IAM role for MSP client VM - ${var.client_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

# IAM Policy for Secrets Manager access
resource "aws_iam_role_policy" "secrets_access" {
  name_prefix = "secrets-access-"
  role        = aws_iam_role.client_vm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = [
          var.mcp_api_key_secret_arn
        ]
      }
    ]
  })
}

# IAM Policy for CloudWatch Logs
resource "aws_iam_role_policy" "cloudwatch_logs" {
  name_prefix = "cloudwatch-logs-"
  role        = aws_iam_role.client_vm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:*:*:log-group:/msp/${var.client_id}/*"
      }
    ]
  })
}

# IAM Policy for EC2 metadata (IMDSv2)
resource "aws_iam_role_policy" "ec2_metadata" {
  name_prefix = "ec2-metadata-"
  role        = aws_iam_role.client_vm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeTags"
        ]
        Resource = "*"
      }
    ]
  })
}

# Instance Profile
resource "aws_iam_instance_profile" "client_vm" {
  name_prefix = "${local.name_prefix}-vm-"
  role        = aws_iam_role.client_vm.name

  tags = local.common_tags
}

# Cloud-Init Configuration
locals {
  cloud_init_config = templatefile("${path.module}/cloud-init.yaml.tpl", {
    client_id           = var.client_id
    client_name         = var.client_name
    mcp_server_url      = var.mcp_server_url
    event_queue_url     = var.event_queue_url
    api_key_secret_arn  = var.mcp_api_key_secret_arn
    flake_git_url       = var.flake_git_url
    flake_ref           = var.flake_ref
    enable_encryption   = var.enable_encryption
    ssh_ca_public_key   = var.ssh_ca_public_key
    subnets_to_discover = jsonencode(var.subnets_to_discover)
    enable_discovery    = var.enable_discovery
  })
}

# EC2 Instance
resource "aws_instance" "client_vm" {
  ami           = try(data.aws_ami.nixos.id, data.aws_ami.amazon_linux_2023.id)
  instance_type = var.instance_type
  subnet_id     = var.subnet_id

  vpc_security_group_ids = [aws_security_group.client_vm.id]
  iam_instance_profile   = aws_iam_instance_profile.client_vm.name

  user_data = local.cloud_init_config

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    encrypted             = var.enable_encryption
    delete_on_termination = true

    tags = merge(
      local.common_tags,
      {
        Name = "${local.name_prefix}-root"
      }
    )
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # Enforce IMDSv2
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  monitoring = true

  tags = merge(
    local.common_tags,
    {
      Name = "${local.name_prefix}-vm"
      Role = "msp-client-station"
    }
  )

  lifecycle {
    ignore_changes = [
      user_data, # Don't recreate on config changes
      ami        # Allow manual AMI updates
    ]
  }
}

# Elastic IP (optional)
resource "aws_eip" "client_vm" {
  count = var.enable_public_ip ? 1 : 0

  instance = aws_instance.client_vm.id
  domain   = "vpc"

  tags = merge(
    local.common_tags,
    {
      Name = "${local.name_prefix}-eip"
    }
  )

  depends_on = [aws_instance.client_vm]
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "client_vm" {
  name              = "/msp/${var.client_id}/vm"
  retention_in_days = 90

  tags = local.common_tags
}

# CloudWatch Alarms

# High CPU alarm
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.name_prefix}-high-cpu"
  alarm_description   = "Client VM CPU utilization is too high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    InstanceId = aws_instance.client_vm.id
  }

  tags = local.common_tags
}

# Status check failed alarm
resource "aws_cloudwatch_metric_alarm" "status_check_failed" {
  alarm_name          = "${local.name_prefix}-status-check-failed"
  alarm_description   = "Client VM status check failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0

  dimensions = {
    InstanceId = aws_instance.client_vm.id
  }

  tags = local.common_tags
}

# Outputs
output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.client_vm.id
}

output "private_ip" {
  description = "Private IP address"
  value       = aws_instance.client_vm.private_ip
}

output "public_ip" {
  description = "Public IP address (if enabled)"
  value       = var.enable_public_ip ? aws_eip.client_vm[0].public_ip : null
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.client_vm.id
}

output "iam_role_arn" {
  description = "IAM role ARN"
  value       = aws_iam_role.client_vm.arn
}

output "log_group_name" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.client_vm.name
}

output "ssh_command" {
  description = "SSH command to connect to instance"
  value       = var.enable_public_ip ? "ssh admin@${aws_eip.client_vm[0].public_ip}" : "ssh admin@${aws_instance.client_vm.private_ip}"
}
