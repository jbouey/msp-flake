# Client VM Deployment Module

Deploys a NixOS-based MSP monitoring station for individual clients with automated configuration and service deployment.

## Features

- **Automated NixOS deployment** with client-specific configuration
- **MSP watcher service** for log monitoring and event publishing
- **Network discovery service** for automated device inventory
- **Secure secrets management** via AWS Secrets Manager
- **Full-disk encryption** (LUKS) support
- **SSH certificate authentication** support
- **CloudWatch integration** for centralized logging and monitoring
- **IMDSv2 enforcement** for enhanced security
- **Automatic updates** via NixOS flakes

## Usage

### Basic Deployment

```hcl
module "client_vm" {
  source = "./modules/client-vm"

  client_id   = "clinic-001"
  client_name = "Sunset Medical Clinic"
  environment = "prod"

  # Networking
  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.private_subnet_ids[0]

  # MSP Configuration
  mcp_server_url          = "https://mcp.your-msp.com"
  event_queue_url         = module.event_queue.connection_string
  mcp_api_key_secret_arn  = aws_secretsmanager_secret.client_api_key.arn

  # Flake Configuration
  flake_git_url = "github:yourorg/msp-platform"
  flake_ref     = "main"

  # Security
  enable_encryption = true
  allowed_ssh_cidr_blocks = [
    "10.0.0.0/8"  # Internal VPC only
  ]

  tags = {
    Owner      = "ops-team"
    CostCenter = "healthcare-it"
  }
}
```

### With Network Discovery

```hcl
module "client_vm" {
  source = "./modules/client-vm"

  client_id   = "clinic-002"
  client_name = "Valley Health Center"
  environment = "prod"

  # Networking
  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.private_subnet_ids[0]

  # MSP Configuration
  mcp_server_url          = "https://mcp.your-msp.com"
  event_queue_url         = module.event_queue.connection_string
  mcp_api_key_secret_arn  = aws_secretsmanager_secret.client_api_key.arn

  # Network Discovery
  enable_discovery = true
  subnets_to_discover = [
    "192.168.1.0/24",   # Main office network
    "192.168.10.0/24",  # Server VLAN
    "10.0.1.0/24"       # Medical devices VLAN
  ]

  # Security
  enable_encryption   = true
  ssh_ca_public_key   = file("~/.ssh/ca-key.pub")

  tags = {
    Owner = "ops-team"
  }
}
```

### Development Environment

```hcl
module "client_vm_dev" {
  source = "./modules/client-vm"

  client_id   = "clinic-dev-001"
  client_name = "Development Test Clinic"
  environment = "dev"

  # Networking
  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.public_subnet_ids[0]

  # MSP Configuration
  mcp_server_url          = "https://mcp-dev.your-msp.com"
  event_queue_url         = "redis://dev-queue.internal:6379"
  mcp_api_key_secret_arn  = aws_secretsmanager_secret.dev_api_key.arn

  # Instance Configuration
  instance_type      = "t3.micro"  # Smaller for dev
  root_volume_size   = 30          # Less storage
  enable_public_ip   = true        # Public access for testing

  # Security (relaxed for dev)
  enable_encryption = false
  allowed_ssh_cidr_blocks = [
    "0.0.0.0/0"  # Open for development (NOT for production!)
  ]

  # Discovery disabled for dev
  enable_discovery = false

  tags = {
    Owner       = "dev-team"
    Environment = "development"
  }
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Client VM (NixOS)                        │
│                                                              │
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │  MSP Watcher   │  │ Network Discovery│  │ CloudWatch  │ │
│  │   Service      │  │     Service      │  │   Agent     │ │
│  └────────┬───────┘  └────────┬─────────┘  └──────┬──────┘ │
│           │                   │                    │        │
│           │    ┌──────────────┴─────────┐         │        │
│           │    │  Flake Configuration   │         │        │
│           │    │  - Client ID            │         │        │
│           │    │  - Security Modules     │         │        │
│           │    │  - Auto-updates         │         │        │
│           │    └────────────────────────┘         │        │
└───────────┼────────────────────────────────────────┼────────┘
            │                                        │
            │ Events                                 │ Logs/Metrics
            ▼                                        ▼
    ┌───────────────┐                      ┌──────────────────┐
    │  Event Queue  │                      │   CloudWatch     │
    │ Redis / NATS  │                      │ Logs & Metrics   │
    └───────┬───────┘                      └──────────────────┘
            │
            │ Events
            ▼
    ┌───────────────┐
    │  MCP Server   │
    │  (Planner +   │
    │   Executor)   │
    └───────────────┘
```

## Variables

### Required Variables

| Name | Description | Type |
|------|-------------|------|
| `client_id` | Unique client identifier (e.g., clinic-001) | `string` |
| `client_name` | Human-readable client name | `string` |
| `vpc_id` | VPC ID for deployment | `string` |
| `subnet_id` | Subnet ID for VM deployment | `string` |
| `mcp_server_url` | URL of the MCP server | `string` |
| `event_queue_url` | URL of the event queue | `string` |
| `mcp_api_key_secret_arn` | ARN of secret containing MCP API key | `string` |

### Optional Variables

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `environment` | Environment name | `string` | `"prod"` |
| `project_name` | Project name for resource naming | `string` | `"msp-platform"` |
| `instance_type` | EC2 instance type | `string` | `"t3.small"` |
| `root_volume_size` | Root volume size in GB | `number` | `50` |
| `enable_public_ip` | Assign public IP | `bool` | `false` |
| `flake_git_url` | Git URL for client flake | `string` | `"github:yourorg/msp-platform"` |
| `flake_ref` | Git ref (branch/tag) | `string` | `"main"` |
| `subnets_to_discover` | Subnets to scan for devices | `list(string)` | `[]` |
| `enable_discovery` | Enable network discovery | `bool` | `true` |
| `enable_encryption` | Enable LUKS encryption | `bool` | `true` |
| `ssh_ca_public_key` | SSH CA public key | `string` | `""` |
| `allowed_ssh_cidr_blocks` | CIDR blocks for SSH access | `list(string)` | `[]` |
| `tags` | Additional tags | `map(string)` | `{}` |

## Outputs

| Name | Description |
|------|-------------|
| `instance_id` | EC2 instance ID |
| `private_ip` | Private IP address |
| `public_ip` | Public IP address (if enabled) |
| `security_group_id` | Security group ID |
| `iam_role_arn` | IAM role ARN |
| `log_group_name` | CloudWatch log group name |
| `ssh_command` | SSH command to connect |

## Instance Sizing Recommendations

### Small Clinic (1-5 providers, <10 devices)

```hcl
instance_type    = "t3.micro"   # 2 vCPU, 1 GB RAM (~$7.50/month)
root_volume_size = 30           # 30 GB storage
```

**Expected Load:**
- 5-10 log sources
- 1-2 discovery scans per day
- 10-50 events per hour

### Medium Clinic (6-15 providers, 10-50 devices)

```hcl
instance_type    = "t3.small"   # 2 vCPU, 2 GB RAM (~$15/month)
root_volume_size = 50           # 50 GB storage
```

**Expected Load:**
- 15-30 log sources
- 4-8 discovery scans per day
- 50-200 events per hour

### Large Clinic (15+ providers, 50-200 devices)

```hcl
instance_type    = "t3.medium"  # 2 vCPU, 4 GB RAM (~$30/month)
root_volume_size = 100          # 100 GB storage
```

**Expected Load:**
- 30-100 log sources
- 12+ discovery scans per day
- 200-1000 events per hour

## Bootstrap Process

The cloud-init script performs the following steps:

1. **System Preparation**
   - Update packages
   - Install base utilities (curl, git, nmap, etc.)
   - Configure hostname and timezone

2. **Nix Installation**
   - Install Nix package manager (multi-user mode)
   - Enable flakes and nix-command features
   - Configure trusted users

3. **Secrets Retrieval**
   - Fetch MCP API key from AWS Secrets Manager
   - Create secure configuration files

4. **MSP Watcher Deployment**
   - Install watcher from NixOS flake
   - Create systemd service with security hardening
   - Start and enable service

5. **Network Discovery Deployment** (if enabled)
   - Install discovery service from NixOS flake
   - Configure subnets and scan parameters
   - Start and enable service

6. **CloudWatch Configuration**
   - Install CloudWatch agent
   - Configure log collection and metrics
   - Start monitoring

7. **Health Checks**
   - Verify all services are running
   - Test connectivity to MCP server and event queue
   - Log bootstrap completion

## Security Features

### Network Security

- **Security Groups:** Restrict SSH to allowed CIDR blocks only
- **IMDSv2 Enforcement:** Prevents SSRF attacks
- **Private Subnets:** Recommended for production deployments
- **No Public IP:** Default is private-only access

### Data Encryption

- **At Rest:** Full-disk encryption via LUKS (AES-256-XTS)
- **In Transit:** TLS for all external communications
- **Secrets:** AWS Secrets Manager with IAM-based access

### Authentication

- **SSH Certificate Auth:** Optional certificate-based authentication
- **No Password Auth:** SSH password authentication disabled
- **IAM Roles:** Least-privilege IAM roles for service accounts

### System Hardening

- **Systemd Security:** NoNewPrivileges, PrivateTmp, ProtectSystem
- **NTP Sync:** Enforced time synchronization (HIPAA requirement)
- **Audit Logging:** All service actions logged to CloudWatch

### HIPAA Compliance

The client VM implements these HIPAA Security Rule controls:

- **§164.308(a)(1)(ii)(D)** - Information system activity review (CloudWatch logs)
- **§164.310(d)(1)** - Device and media controls (full-disk encryption)
- **§164.312(a)(1)** - Access control (IAM roles, SSH hardening)
- **§164.312(a)(2)(i)** - Unique user identification (certificate auth)
- **§164.312(a)(2)(iv)** - Encryption and decryption (LUKS, TLS)
- **§164.312(b)** - Audit controls (tamper-evident logs)

## Monitoring & Alerting

### CloudWatch Logs

Logs are automatically collected and forwarded to CloudWatch:

- `/msp/{client_id}/watcher` - MSP watcher service logs
- `/msp/{client_id}/discovery` - Network discovery logs
- `/msp/{client_id}/syslog` - System logs
- `/msp/{client_id}/vm` - General VM logs

**Retention:** 90 days

### CloudWatch Metrics

The following metrics are collected:

- **CPU:** Idle, IOWait
- **Disk:** Usage percentage (/ and /var)
- **Memory:** Usage percentage
- **EC2:** Standard CloudWatch metrics

### CloudWatch Alarms

Pre-configured alarms:

1. **High CPU Alarm**
   - Threshold: 80% average over 10 minutes
   - Action: Log to CloudWatch

2. **Status Check Failed**
   - Threshold: Any failure
   - Action: Log to CloudWatch

**Note:** Connect these alarms to SNS topics for automated notifications.

## Maintenance

### Updating Flake Version

To update the client to a new flake version:

```bash
# SSH into the client VM
ssh admin@<client-vm-ip>

# Pull latest flake
sudo nix flake update /etc/msp/flake

# Rebuild system
sudo nixos-rebuild switch --flake /etc/msp/flake

# Restart services
sudo systemctl restart msp-watcher
sudo systemctl restart msp-discovery
```

### Manual Service Restart

```bash
# Restart watcher
sudo systemctl restart msp-watcher

# Check status
sudo systemctl status msp-watcher

# View logs
sudo journalctl -u msp-watcher -f
```

### Viewing Configuration

```bash
# View MSP configuration
sudo cat /etc/msp/config.yaml

# View watcher config
sudo cat /var/lib/msp-watcher/config.json

# View discovery config
sudo cat /etc/msp-discovery/config.yaml
```

## Troubleshooting

### VM Not Responding

1. Check EC2 status checks in AWS console
2. Review CloudWatch logs for errors
3. Check security group rules allow access
4. Verify IAM role has required permissions

### Watcher Service Not Starting

```bash
# Check service status
sudo systemctl status msp-watcher

# View detailed logs
sudo journalctl -u msp-watcher -n 100

# Verify configuration
sudo cat /var/lib/msp-watcher/config.json

# Test API key
aws secretsmanager get-secret-value --secret-id <secret-arn>
```

### Discovery Service Issues

```bash
# Check service status
sudo systemctl status msp-discovery

# View logs
sudo journalctl -u msp-discovery -n 100

# Test network connectivity
sudo nmap -sn 192.168.1.0/24

# Verify permissions
# Discovery service needs CAP_NET_RAW for raw sockets
```

### CloudWatch Agent Not Sending Logs

```bash
# Check agent status
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a query -m ec2 -c default -s

# Restart agent
sudo systemctl restart amazon-cloudwatch-agent

# Verify IAM permissions for logs:PutLogEvents
```

### Nix Installation Issues

```bash
# Check Nix daemon
sudo systemctl status nix-daemon

# Verify Nix installation
nix --version

# Re-source Nix
. /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh

# Test flake access
nix flake show github:yourorg/msp-platform
```

## Cost Optimization

### Instance Right-Sizing

Monitor CloudWatch metrics for 2 weeks, then adjust:

```bash
# If CPU consistently <20% and Memory <50%
instance_type = "t3.micro"  # Downgrade

# If CPU frequently >80% or Memory >90%
instance_type = "t3.medium"  # Upgrade
```

### Storage Optimization

```bash
# Review disk usage
df -h

# Clean old logs
sudo journalctl --vacuum-time=30d

# Reduce volume size if consistently <30% used
root_volume_size = 30  # Reduce (requires recreation)
```

### Reserved Instances

For stable production workloads (>1 year):

- **t3.small** Reserved Instance (1-year, no upfront): ~$10/month (33% savings)
- **t3.medium** Reserved Instance (1-year, no upup): ~$20/month (33% savings)

## Integration Examples

### With Event Queue Module

```hcl
# Deploy event queue
module "event_queue" {
  source = "./modules/event-queue"

  environment  = "prod"
  project_name = "msp-platform"
  queue_type   = "redis"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnet_ids

  redis_node_type       = "cache.t3.micro"
  redis_num_cache_nodes = 2

  enable_encryption = true
  enable_auth       = true
}

# Deploy client VM
module "client_vm" {
  source = "./modules/client-vm"

  client_id   = "clinic-001"
  client_name = "Sunset Medical"

  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.private_subnet_ids[0]

  mcp_server_url          = "https://mcp.example.com"
  event_queue_url         = module.event_queue.connection_string
  mcp_api_key_secret_arn  = module.event_queue.auth_secret_arn
}
```

### With VPC Module

```hcl
module "vpc" {
  source = "terraform-aws-modules/vpc/aws"

  name = "msp-platform-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["us-east-1a", "us-east-1b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway = true
  enable_vpn_gateway = false

  tags = {
    Environment = "prod"
    Project     = "msp-platform"
  }
}

module "client_vm" {
  source = "./modules/client-vm"

  client_id   = "clinic-001"
  client_name = "Sunset Medical"

  vpc_id    = module.vpc.vpc_id
  subnet_id = module.vpc.private_subnets[0]

  # ... other configuration
}
```

## References

- [NixOS Manual](https://nixos.org/manual/nixos/stable/)
- [NixOS Flakes](https://nixos.wiki/wiki/Flakes)
- [AWS EC2 Best Practices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-best-practices.html)
- [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html)
- [CloudWatch Agent Configuration](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Configuration-File-Details.html)
