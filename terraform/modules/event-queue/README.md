# Event Queue Terraform Module

Deploys a multi-tenant event queue for the MSP Automation Platform using either Redis Streams or NATS JetStream.

## Features

- **Multi-tenant support** - Isolated streams per client
- **Encryption** - At rest and in transit
- **Authentication** - Token/password-based auth
- **High availability** - Multi-node clustering support
- **Monitoring** - CloudWatch integration
- **Backup** - Automated snapshots

## Usage

### Redis Deployment (Recommended for simplicity)

```hcl
module "event_queue" {
  source = "./modules/event-queue"

  environment  = "prod"
  project_name = "msp-platform"
  queue_type   = "redis"

  # Redis configuration
  redis_node_type        = "cache.t3.small"
  redis_num_cache_nodes  = 2  # HA with failover
  redis_engine_version   = "7.0"

  # Networking
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnet_ids

  allowed_cidr_blocks = [
    "10.0.0.0/8",  # Internal VPC
  ]

  # Security
  enable_encryption = true
  enable_auth       = true

  # Monitoring
  enable_cloudwatch       = true
  backup_retention_days   = 7

  tags = {
    Owner = "ops-team"
  }
}
```

### NATS Deployment (For advanced messaging patterns)

```hcl
module "event_queue" {
  source = "./modules/event-queue"

  environment  = "prod"
  project_name = "msp-platform"
  queue_type   = "nats"

  # NATS configuration
  nats_instance_type = "t3.medium"
  nats_cluster_size  = 3  # Cluster for HA

  # Networking
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnet_ids

  allowed_cidr_blocks = [
    "10.0.0.0/8",
  ]

  # Security
  enable_encryption = true
  enable_auth       = true

  # Monitoring
  enable_cloudwatch = true

  tags = {
    Owner = "ops-team"
  }
}
```

## Outputs

- `connection_string` - Full connection string with credentials (sensitive)
- `endpoint` - Queue endpoint address
- `port` - Queue port
- `security_group_id` - Security group ID
- `auth_secret_arn` - ARN of AWS Secrets Manager secret containing credentials

## Connection Examples

### Python (Redis)

```python
import redis

# Get connection string from Terraform output
redis_client = redis.from_url("rediss://default:password@endpoint:6379")

# Publish to client-specific stream
redis_client.xadd(
    "tenant:clinic-001:incidents",
    {
        "snippet": "ERROR: backup failed",
        "hostname": "server01",
        "timestamp": "2025-10-24T12:00:00Z"
    }
)

# Read from stream
messages = redis_client.xread(
    {"tenant:clinic-001:incidents": "$"},
    count=10,
    block=1000
)
```

### Python (NATS)

```python
import asyncio
import nats

async def main():
    # Get connection string from Terraform output
    nc = await nats.connect("nats://token@endpoint:4222")

    # Create JetStream context
    js = nc.jetstream()

    # Publish to client-specific stream
    await js.publish(
        "tenant.clinic-001.incidents",
        b'{"snippet": "ERROR: backup failed", ...}'
    )

    # Subscribe
    sub = await js.subscribe("tenant.clinic-001.incidents")
    msg = await sub.next_msg()
    print(msg.data)

    await nc.close()

asyncio.run(main())
```

## Multi-Tenant Patterns

### Redis Streams

```
# Stream naming convention:
tenant:{client_id}:{stream_type}

# Examples:
tenant:clinic-001:incidents
tenant:clinic-001:evidence
tenant:clinic-002:incidents
```

### NATS JetStream

```
# Subject naming convention:
tenant.{client_id}.{stream_type}

# Examples:
tenant.clinic-001.incidents
tenant.clinic-001.evidence
tenant.clinic-002.incidents
```

## Security

### Encryption

- **At rest**: AWS KMS encryption for Redis, disk encryption for NATS
- **In transit**: TLS for all connections
- **Auth tokens**: Stored in AWS Secrets Manager

### Access Control

- Security groups restrict access to VPC CIDR blocks
- Authentication required by default
- Separate credentials per client (implement in application layer)

### Compliance (HIPAA)

- ✅ Encryption at rest and in transit
- ✅ Access logging via CloudWatch
- ✅ Authentication enforced
- ✅ Network isolation
- ✅ Automated backups
- ✅ Audit trail

**HIPAA Controls:**
- §164.312(a)(1) - Access Control
- §164.312(a)(2)(iv) - Encryption and Decryption
- §164.312(e)(1) - Transmission Security
- §164.312(b) - Audit Controls

## Monitoring

### CloudWatch Metrics

**Redis:**
- CPUUtilization
- DatabaseMemoryUsagePercentage
- NetworkBytesIn/Out
- CurrConnections
- Evictions

**NATS:**
- CPU usage
- Memory usage
- Disk usage
- Connection count (via HTTP monitoring endpoint)

### Alarms

- High CPU (>75% for 10 minutes)
- High memory (>80% for 10 minutes)
- Connection failures

### Logs

- Redis: Slow log via CloudWatch
- NATS: Server logs via CloudWatch Logs

## Backup and Recovery

### Redis

- Automated snapshots daily
- Retention: 7 days (configurable)
- Point-in-time recovery available

### NATS

- JetStream data in persistent storage
- EBS snapshots for disaster recovery
- Manual backup of configuration

## Cost Optimization

### Small Deployment (1-10 clients)

```hcl
redis_node_type = "cache.t3.micro"  # ~$12/month
redis_num_cache_nodes = 1
```

### Medium Deployment (10-50 clients)

```hcl
redis_node_type = "cache.t3.small"  # ~$25/month
redis_num_cache_nodes = 2  # HA
```

### Large Deployment (50+ clients)

```hcl
redis_node_type = "cache.m5.large"  # ~$120/month
redis_num_cache_nodes = 2
```

## Troubleshooting

### Cannot connect to Redis

1. Check security group rules
2. Verify VPC routing
3. Check authentication token
4. Verify encryption settings match

### NATS server not starting

1. Check EC2 instance logs: `systemctl status nats`
2. View NATS logs: `journalctl -u nats -f`
3. Verify configuration: `/etc/nats/nats-server.conf`

### High memory usage

1. Check stream retention policies
2. Implement message expiration
3. Scale up node size
4. Enable eviction policy

## Development vs Production

### Development

- Single node
- Smaller instance size
- Shorter backup retention
- No clustering

### Production

- Multi-node with automatic failover
- Larger instance sizes
- 7+ day backup retention
- Clustering enabled
- Enhanced monitoring

## Maintenance

### Upgrading Redis

1. Test in staging environment
2. During maintenance window:
   ```bash
   terraform plan -var redis_engine_version="7.1"
   terraform apply
   ```
3. Monitor for issues
4. Rollback if needed (restore from snapshot)

### Upgrading NATS

1. Update `NATS_VERSION` in `nats-userdata.sh`
2. Create new launch template version
3. Rolling update of instances
4. Monitor cluster health

## References

- [Redis Streams Documentation](https://redis.io/topics/streams-intro)
- [NATS JetStream Documentation](https://docs.nats.io/nats-concepts/jetstream)
- [AWS ElastiCache Best Practices](https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/BestPractices.html)
