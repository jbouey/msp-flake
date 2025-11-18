# MSP Compliance Agent

**Pull-Only Autonomous Compliance Agent for Healthcare SMBs**

## Overview

The MSP Compliance Agent is the heart of the compliance platform. It runs at each client site and:

1. **Polls MCP server** for orders (pull-only, no listening sockets)
2. **Detects drift** from baseline configuration (Phase 2 Day 3-4)
3. **Heals drift automatically** with rollback capability (Phase 2 Day 5)
4. **Generates evidence** bundles for all actions (Phase 2 Day 6-7)
5. **Operates offline** with durable queue when MCP unavailable

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Compliance Agent                      │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │  agent   │  │   MCP    │  │  Queue   │            │
│  │  .py     │─▶│  Client  │─▶│  (SQLite)│            │
│  └──────────┘  └──────────┘  └──────────┘            │
│       │              │              │                  │
│       ▼              ▼              ▼                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │  Config  │  │  Crypto  │  │ Evidence │            │
│  │  .py     │  │  .py     │  │ (Phase2) │            │
│  └──────────┘  └──────────┘  └──────────┘            │
└─────────────────────────────────────────────────────────┘
         │                │                │
         ▼                ▼                ▼
   NixOS Config    mTLS to MCP    WORM Storage
```

## Components (Phase 2 Day 1-2 Complete)

### agent.py (497 lines)
Main compliance agent orchestrating all operations:
- Poll loop with jitter (60s ±10%)
- Order verification (Ed25519 + TTL)
- Maintenance window checking
- Graceful shutdown (SIGTERM/SIGINT)
- Statistics tracking
- Placeholder integration points for drift/heal/evidence

**Key Methods:**
- `run()` - Main loop
- `_compliance_cycle()` - One full cycle
- `_fetch_orders()` - Poll MCP or offline queue
- `_verify_order()` - Signature + TTL + field validation
- `_execute_order()` - Run verified order (respects maintenance windows)

### mcp_client.py (277 lines)
MCP client with mTLS for pull-only communication:
- mTLS configuration (client certs + CA verification)
- TLS 1.2+ enforcement (no TLS 1.0/1.1)
- Pull-only architecture (agent initiates all connections)
- Graceful degradation when MCP unavailable

**Key Methods:**
- `poll_orders(site_id)` - Fetch new orders
- `push_evidence(evidence)` - Upload evidence bundle
- `health_check()` - Verify MCP reachability
- `get_config(site_id)` - Optional config updates

**Security:**
- Certificate pinning via CA
- Hostname verification enforced
- Timeout handling (prevents hangs)
- Rate limiting via Redis cooldown

### queue.py (440 lines)
Durable offline queue using SQLite WAL mode:
- Implements Guardrail #9 (queue durability)
- SQLite with WAL mode + PRAGMA synchronous=FULL (fsync)
- Two tables: orders (pending execution), evidence (pending push)
- Exponential backoff for retries
- Automatic cleanup of old data

**Key Methods:**
- `add(order)` - Queue order when MCP unavailable
- `get_pending(limit)` - Retrieve orders ready for execution
- `mark_executed(order_id)` - Mark order as completed
- `add_evidence(evidence)` - Queue evidence for push
- `cleanup_old(days)` - Remove old completed items

### crypto.py (243 lines)
Ed25519 signature verification:
- Implements Guardrail #1 (order authentication)
- Ed25519 (Curve25519) for fast, secure signatures
- Public key verification only (agent never signs)
- Constant-time operations (timing attack prevention)

**Key Methods:**
- `verify(message, signature)` - Verify Ed25519 signature
- `verify_order_signature(order, public_key)` - Convenience function
- `generate_keypair()` - For testing/setup only

### config.py (368 lines)
Configuration loading and validation:
- YAML/JSON config file support
- Pydantic validation with type checking
- Environment variable overrides (MSP_* prefix)
- Maintenance window parsing
- Sensible defaults for all optional fields

**Configuration Sources (priority order):**
1. Environment variables (override everything)
2. Config file (YAML or JSON)
3. Defaults (built-in values)

**Key Configuration Fields:**
- `site_id` - Unique site identifier
- `mcp_base_url` - MCP server URL
- `mcp_public_key` - Ed25519 public key (hex)
- `client_cert`, `client_key`, `ca_cert` - mTLS certificates
- `queue_path` - SQLite database path
- `poll_interval` - Seconds between polls
- `maintenance_window_*` - Maintenance window settings
- `deployment_mode` - direct or reseller

## Example Configuration

```yaml
# config.yaml
site_id: clinic-001
mcp_base_url: https://mcp.example.com
mcp_public_key: abc123...  # 64 hex chars (32 bytes)

# mTLS Certificates (via SOPS/Vault)
client_cert: /run/secrets/client-cert.pem
client_key: /run/secrets/client-key.pem
ca_cert: /run/secrets/ca-cert.pem

# Queue
queue_path: /var/lib/msp/queue.db
max_queue_size: 1000

# Polling
poll_interval: 60  # seconds

# Maintenance Window
maintenance_window_enabled: true
maintenance_window_start: "02:00:00"
maintenance_window_end: "04:00:00"
maintenance_window_days:
  - sunday

# Deployment
deployment_mode: direct

# Logging
log_level: INFO
log_file: /var/log/msp/agent.log
```

## Usage

```bash
# Run agent with config file
python -m src.agent /path/to/config.yaml

# Or with environment overrides
export MSP_SITE_ID=clinic-002
export MSP_POLL_INTERVAL=120
python -m src.agent /path/to/config.yaml
```

## Guardrails Implemented

- ✅ **Guardrail #1:** Order auth with Ed25519 signature verification
- ✅ **Guardrail #2:** Order TTL enforcement (default 15 minutes)
- ✅ **Guardrail #3:** Maintenance window enforcement (disruptive actions only in window)
- ✅ **Guardrail #9:** Queue durability (SQLite WAL + fsync)

**Pending (Phase 2 Day 3-7):**
- Guardrail #4: Health check + rollback
- Guardrail #5: Evidence generation
- Guardrail #6: Rate limiting (via MCP server)
- Guardrail #7: Validation (runbook whitelisting)
- Guardrail #8: Dry-run mode

## Next Steps (Phase 2 Day 3-7)

### Day 3-4: Drift Detection
- [ ] Implement DriftDetector class
- [ ] Add 6 drift checks (flake hash, patch status, backup status, service health, encryption, time sync)
- [ ] Integrate with agent main loop
- [ ] Test drift detection with synthetic violations

### Day 5: Self-Healing
- [ ] Implement Healer class
- [ ] Add runbook execution engine
- [ ] Implement rollback logic
- [ ] Add health check verification
- [ ] Test healing with rollback scenarios

### Day 6-7: Evidence Generation
- [ ] Implement EvidenceGenerator class
- [ ] Add evidence bundle structure
- [ ] Implement signing (cosign)
- [ ] Add WORM storage upload
- [ ] Test evidence generation and verification

### Day 8-10: Testing
- [ ] Create /demo Docker Compose stack
- [ ] Implement 5 test cases from test matrix
- [ ] Integration testing with real MCP server
- [ ] Performance testing (resource usage)
- [ ] Documentation updates

## Dependencies

```
# Python 3.11+
aiohttp>=3.9.0
cryptography>=42.0.0
pydantic>=2.5.0
pyyaml>=6.0.1
```

## License

Proprietary - MSP Compliance Platform

## Status

**Phase 2 Day 1-2: COMPLETE** ✅
- Agent core foundation ready
- All 5 core modules implemented
- Ready for drift detection integration
