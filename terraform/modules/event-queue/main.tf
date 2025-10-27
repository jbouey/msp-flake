# Terraform Module: Event Queue (Redis Streams / NATS JetStream)
# Multi-tenant event queue for MSP automation platform

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

variable "queue_type" {
  description = "Queue type: redis or nats"
  type        = string
  default     = "redis"

  validation {
    condition     = contains(["redis", "nats"], var.queue_type)
    error_message = "queue_type must be either 'redis' or 'nats'"
  }
}

# Redis Configuration
variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"  # Small/cheap for dev, scale up for prod
}

variable "redis_num_cache_nodes" {
  description = "Number of cache nodes"
  type        = number
  default     = 1
}

variable "redis_engine_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.0"
}

variable "redis_port" {
  description = "Redis port"
  type        = number
  default     = 6379
}

# NATS Configuration
variable "nats_instance_type" {
  description = "EC2 instance type for NATS"
  type        = string
  default     = "t3.small"
}

variable "nats_cluster_size" {
  description = "Number of NATS nodes"
  type        = number
  default     = 1
}

# Networking
variable "vpc_id" {
  description = "VPC ID for deployment"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for deployment"
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access queue"
  type        = list(string)
  default     = []
}

# Security
variable "enable_encryption" {
  description = "Enable encryption at rest and in transit"
  type        = bool
  default     = true
}

variable "enable_auth" {
  description = "Enable authentication"
  type        = bool
  default     = true
}

# Monitoring
variable "enable_cloudwatch" {
  description = "Enable CloudWatch monitoring"
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Number of days to retain backups"
  type        = number
  default     = 7
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
      ManagedBy   = "terraform"
      Purpose     = "event-queue"
      HIPAA       = "compliant"
    },
    var.tags
  )

  name_prefix = "${var.project_name}-${var.environment}"
}

# Security Group for Queue
resource "aws_security_group" "queue" {
  name_prefix = "${local.name_prefix}-queue-"
  description = "Security group for ${var.queue_type} event queue"
  vpc_id      = var.vpc_id

  # Redis/NATS ingress
  ingress {
    description = "${upper(var.queue_type)} port"
    from_port   = var.queue_type == "redis" ? var.redis_port : 4222
    to_port     = var.queue_type == "redis" ? var.redis_port : 4222
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # NATS additional ports (if NATS)
  dynamic "ingress" {
    for_each = var.queue_type == "nats" ? [1] : []
    content {
      description = "NATS cluster port"
      from_port   = 6222
      to_port     = 6222
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidr_blocks
    }
  }

  dynamic "ingress" {
    for_each = var.queue_type == "nats" ? [1] : []
    content {
      description = "NATS monitoring port"
      from_port   = 8222
      to_port     = 8222
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidr_blocks
    }
  }

  # Egress
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
      Name = "${local.name_prefix}-queue-sg"
    }
  )

  lifecycle {
    create_before_destroy = true
  }
}

# Generate random password for authentication
resource "random_password" "queue_auth" {
  count   = var.enable_auth ? 1 : 0
  length  = 32
  special = true
}

# Store password in AWS Secrets Manager
resource "aws_secretsmanager_secret" "queue_auth" {
  count       = var.enable_auth ? 1 : 0
  name_prefix = "${local.name_prefix}-queue-auth-"
  description = "Authentication credentials for ${var.queue_type} event queue"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "queue_auth" {
  count     = var.enable_auth ? 1 : 0
  secret_id = aws_secretsmanager_secret.queue_auth[0].id

  secret_string = jsonencode({
    password = random_password.queue_auth[0].result
    username = var.queue_type == "redis" ? "default" : "admin"
  })
}

# Redis Implementation
resource "aws_elasticache_subnet_group" "redis" {
  count       = var.queue_type == "redis" ? 1 : 0
  name        = "${local.name_prefix}-redis-subnet"
  description = "Subnet group for Redis event queue"
  subnet_ids  = var.subnet_ids

  tags = local.common_tags
}

resource "aws_elasticache_parameter_group" "redis" {
  count       = var.queue_type == "redis" ? 1 : 0
  name        = "${local.name_prefix}-redis-params"
  family      = "redis7"
  description = "Custom parameter group for Redis event queue"

  # Enable append-only file for persistence
  parameter {
    name  = "appendonly"
    value = "yes"
  }

  # Auto-sync every second
  parameter {
    name  = "appendfsync"
    value = "everysec"
  }

  # Enable keyspace notifications for expired events
  parameter {
    name  = "notify-keyspace-events"
    value = "Ex"
  }

  # Max memory policy
  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  tags = local.common_tags
}

resource "aws_elasticache_replication_group" "redis" {
  count = var.queue_type == "redis" ? 1 : 0

  replication_group_id       = "${local.name_prefix}-redis"
  replication_group_description = "Redis event queue for MSP platform"

  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  number_cache_clusters = var.redis_num_cache_nodes
  port                 = var.redis_port

  # Subnet and security
  subnet_group_name  = aws_elasticache_subnet_group.redis[0].name
  security_group_ids = [aws_security_group.queue.id]
  parameter_group_name = aws_elasticache_parameter_group.redis[0].name

  # Encryption
  at_rest_encryption_enabled = var.enable_encryption
  transit_encryption_enabled = var.enable_encryption
  auth_token_enabled         = var.enable_auth && var.enable_encryption
  auth_token                 = var.enable_auth && var.enable_encryption ? random_password.queue_auth[0].result : null

  # Backups
  snapshot_retention_limit = var.backup_retention_days
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:05:00-sun:07:00"

  # Auto failover (requires 2+ nodes)
  automatic_failover_enabled = var.redis_num_cache_nodes > 1
  multi_az_enabled           = var.redis_num_cache_nodes > 1

  # Notifications
  notification_topic_arn = var.enable_cloudwatch ? aws_sns_topic.alerts[0].arn : null

  # Auto minor version upgrade
  auto_minor_version_upgrade = true

  tags = local.common_tags
}

# NATS Implementation (using EC2 with user data)
data "aws_ami" "amazon_linux_2023" {
  count       = var.queue_type == "nats" ? 1 : 0
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

# NATS configuration file
locals {
  nats_config = var.queue_type == "nats" ? templatefile("${path.module}/nats.conf.tpl", {
    cluster_name = "${local.name_prefix}-nats"
    auth_token   = var.enable_auth ? random_password.queue_auth[0].result : ""
    enable_auth  = var.enable_auth
    enable_tls   = var.enable_encryption
  }) : ""
}

resource "aws_instance" "nats" {
  count = var.queue_type == "nats" ? var.nats_cluster_size : 0

  ami           = data.aws_ami.amazon_linux_2023[0].id
  instance_type = var.nats_instance_type
  subnet_id     = element(var.subnet_ids, count.index)

  vpc_security_group_ids = [aws_security_group.queue.id]

  user_data = base64encode(templatefile("${path.module}/nats-userdata.sh", {
    nats_config = local.nats_config
  }))

  tags = merge(
    local.common_tags,
    {
      Name  = "${local.name_prefix}-nats-${count.index + 1}"
      Role  = "nats-server"
      Index = count.index
    }
  )

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = var.enable_encryption
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"  # Enforce IMDSv2
  }
}

# SNS Topic for alerts
resource "aws_sns_topic" "alerts" {
  count       = var.enable_cloudwatch ? 1 : 0
  name_prefix = "${local.name_prefix}-queue-alerts-"

  tags = local.common_tags
}

# CloudWatch alarms for Redis
resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  count = var.queue_type == "redis" && var.enable_cloudwatch ? 1 : 0

  alarm_name          = "${local.name_prefix}-redis-high-cpu"
  alarm_description   = "Redis CPU utilization is too high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.redis[0].id
  }

  alarm_actions = [aws_sns_topic.alerts[0].arn]
  ok_actions    = [aws_sns_topic.alerts[0].arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  count = var.queue_type == "redis" && var.enable_cloudwatch ? 1 : 0

  alarm_name          = "${local.name_prefix}-redis-high-memory"
  alarm_description   = "Redis memory utilization is too high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.redis[0].id
  }

  alarm_actions = [aws_sns_topic.alerts[0].arn]
  ok_actions    = [aws_sns_topic.alerts[0].arn]

  tags = local.common_tags
}

# Outputs
output "queue_type" {
  description = "Type of queue deployed"
  value       = var.queue_type
}

output "connection_string" {
  description = "Connection string for queue (sensitive)"
  value = var.queue_type == "redis" ? (
    var.enable_auth ? (
      "rediss://default:${random_password.queue_auth[0].result}@${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:${var.redis_port}"
    ) : (
      "redis://${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:${var.redis_port}"
    )
  ) : (
    var.enable_auth ? (
      "nats://${random_password.queue_auth[0].result}@${aws_instance.nats[0].private_ip}:4222"
    ) : (
      "nats://${aws_instance.nats[0].private_ip}:4222"
    )
  )
  sensitive = true
}

output "endpoint" {
  description = "Queue endpoint address"
  value = var.queue_type == "redis" ? (
    aws_elasticache_replication_group.redis[0].primary_endpoint_address
  ) : (
    aws_instance.nats[0].private_ip
  )
}

output "port" {
  description = "Queue port"
  value = var.queue_type == "redis" ? var.redis_port : 4222
}

output "security_group_id" {
  description = "Security group ID for queue"
  value       = aws_security_group.queue.id
}

output "auth_secret_arn" {
  description = "ARN of secret containing authentication credentials"
  value       = var.enable_auth ? aws_secretsmanager_secret.queue_auth[0].arn : null
}

output "sns_topic_arn" {
  description = "SNS topic ARN for alerts"
  value       = var.enable_cloudwatch ? aws_sns_topic.alerts[0].arn : null
}
