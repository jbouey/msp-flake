# Complete MSP Platform Deployment Example
# Deploys full stack: VPC, Event Queue, Client VM with Discovery

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "msp-platform-terraform-state"
    key    = "clients/clinic-001/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "MSP-Platform"
      Environment = var.environment
      ManagedBy   = "Terraform"
      HIPAA       = "Compliant"
    }
  }
}

# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "client_id" {
  description = "Client identifier"
  type        = string
  default     = "clinic-001"
}

variable "client_name" {
  description = "Client name"
  type        = string
  default     = "Sunset Medical Clinic"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

# Data sources
data "aws_availability_zones" "available" {
  state = "available"
}

# VPC Module
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "msp-${var.environment}-${var.client_id}-vpc"
  cidr = var.vpc_cidr

  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = var.environment == "prod" ? false : true
  enable_dns_hostnames = true
  enable_dns_support   = true

  # Flow logs for HIPAA compliance
  enable_flow_log                      = true
  create_flow_log_cloudwatch_iam_role  = true
  create_flow_log_cloudwatch_log_group = true
  flow_log_retention_in_days           = 90

  tags = {
    Client = var.client_name
  }
}

# Event Queue Module
module "event_queue" {
  source = "../../modules/event-queue"

  environment  = var.environment
  project_name = "msp-platform"
  queue_type   = "redis"

  # Redis configuration (small for pilot)
  redis_node_type        = "cache.t3.micro"
  redis_num_cache_nodes  = 2
  redis_engine_version   = "7.0"

  # Networking
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  allowed_cidr_blocks = [
    module.vpc.vpc_cidr_block
  ]

  # Security (HIPAA requirements)
  enable_encryption = true
  enable_auth       = true

  # Monitoring
  enable_cloudwatch     = true
  backup_retention_days = 7

  tags = {
    Client = var.client_name
  }
}

# Generate MCP API key
resource "random_password" "mcp_api_key" {
  length  = 32
  special = true
}

resource "aws_secretsmanager_secret" "mcp_api_key" {
  name_prefix = "msp-${var.environment}-${var.client_id}-mcp-key-"
  description = "MCP API key for ${var.client_name}"

  tags = {
    Client = var.client_name
  }
}

resource "aws_secretsmanager_secret_version" "mcp_api_key" {
  secret_id = aws_secretsmanager_secret.mcp_api_key.id

  secret_string = jsonencode({
    api_key   = random_password.mcp_api_key.result
    client_id = var.client_id
  })
}

# Client VM Module
module "client_vm" {
  source = "../../modules/client-vm"

  client_id   = var.client_id
  client_name = var.client_name
  environment = var.environment

  # Networking
  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.private_subnets[0]

  # Allow SSH from VPC only
  allowed_ssh_cidr_blocks = [module.vpc.vpc_cidr_block]

  # MSP Configuration
  mcp_server_url         = "https://mcp.your-msp.com"
  event_queue_url        = module.event_queue.connection_string
  mcp_api_key_secret_arn = aws_secretsmanager_secret.mcp_api_key.arn

  # Flake Configuration
  flake_git_url = "github:yourorg/msp-platform"
  flake_ref     = "main"

  # Instance Configuration
  instance_type    = "t3.small"
  root_volume_size = 50

  # Network Discovery
  enable_discovery = true
  subnets_to_discover = [
    "192.168.1.0/24",   # Client's main office network
    "192.168.10.0/24",  # Server VLAN
    "10.0.1.0/24"       # Medical devices VLAN
  ]

  # Security (HIPAA requirements)
  enable_encryption = true
  ssh_ca_public_key = file("~/.ssh/msp-ca.pub")

  tags = {
    Client = var.client_name
  }

  depends_on = [
    module.event_queue
  ]
}

# CloudWatch Dashboard
resource "aws_cloudwatch_dashboard" "client" {
  dashboard_name = "msp-${var.environment}-${var.client_id}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/EC2", "CPUUtilization", { stat = "Average" }],
            [".", "NetworkIn", { stat = "Sum" }],
            [".", "NetworkOut", { stat = "Sum" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Client VM Metrics"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/ElastiCache", "CPUUtilization", {
              ReplicationGroupId = module.event_queue.endpoint
            }],
            [".", "DatabaseMemoryUsagePercentage", {
              ReplicationGroupId = module.event_queue.endpoint
            }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Event Queue Metrics"
        }
      }
    ]
  })
}

# Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "event_queue_endpoint" {
  description = "Event queue endpoint"
  value       = module.event_queue.endpoint
}

output "event_queue_connection_string" {
  description = "Event queue connection string"
  value       = module.event_queue.connection_string
  sensitive   = true
}

output "client_vm_instance_id" {
  description = "Client VM instance ID"
  value       = module.client_vm.instance_id
}

output "client_vm_private_ip" {
  description = "Client VM private IP"
  value       = module.client_vm.private_ip
}

output "ssh_command" {
  description = "SSH command to connect to client VM"
  value       = module.client_vm.ssh_command
}

output "cloudwatch_dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.client.dashboard_name}"
}

output "mcp_api_key_secret_arn" {
  description = "MCP API key secret ARN"
  value       = aws_secretsmanager_secret.mcp_api_key.arn
}
