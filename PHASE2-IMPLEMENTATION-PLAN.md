# Phase 2 Implementation Plan - Agent Core & Self-Healing

**Status:** Ready to Start
**Estimated Duration:** 10-12 days (2 weeks)
**Current Date:** 2025-11-11

---

## Executive Summary

Phase 1 delivered the NixOS infrastructure foundation with all guardrails locked. Phase 2 brings it to life with:

1. **Agent Core** - The heart of the compliance appliance
2. **Drift Detection** - 6 automated checks for compliance violations
3. **Self-Healing** - Automated remediation with rollback safety
4. **Evidence Generation** - Cryptographically signed proof of operations
5. **Testing Infrastructure** - Docker Compose demo + VM tests

**Bonus Completed:** Self-Learning Runbook System (10,519 lines) - not in original plan but adds competitive moat

---

## What We Have (Phase 1 Complete)

✅ **Infrastructure Foundation**
- `flake-compliance.nix` - Production NixOS flake
- `modules/compliance-agent.nix` - 546 lines, 27 configuration options
- Systemd hardening + nftables egress filtering
- Pull-only architecture (no listening sockets)
- Dual deployment modes (reseller/direct)
- All 10 guardrails locked

✅ **Testing Infrastructure**
- VM integration tests (7 test cases)
- Examples for reseller and direct configs
- SOPS integration scaffolded

✅ **Bonus: Learning System**
- Self-improving runbooks (2,532 lines of code)
- LLM-powered improvement engine
- Human review workflow with web dashboard
- Complete documentation (7,987 lines)

---

## What We Need (Phase 2)

### Critical Path Components

```
┌─────────────────────────────────────────────────────────────┐
│                     PHASE 2 ARCHITECTURE                     │
└─────────────────────────────────────────────────────────────┘

┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ Agent Core   │ ───> │ Drift        │ ───> │ Self-Healing │
│ (agent.py)   │      │ Detection    │      │ (healer.py)  │
│              │      │ (6 checks)   │      │              │
│ - Main loop  │      │              │      │ - Remediate  │
│ - MCP client │      │ - Patching   │      │ - Rollback   │
│ - Queue mgmt │      │ - Services   │      │ - Evidence   │
└──────────────┘      │ - Backups    │      └──────────────┘
                      │ - Firewall   │
                      │ - Logs       │
                      │ - Encryption │
                      └──────────────┘
                             │
                             ↓
                      ┌──────────────┐
                      │ Evidence     │
                      │ Generation   │
                      │              │
                      │ - JSON       │
                      │ - Ed25519    │
                      │ - Outcomes   │
                      └──────────────┘
```

---

## Implementation Roadmap

### Week 1: Core Infrastructure (Days 1-5)

#### Day 1-2: Agent Core Foundation
**Files to Create:**
- `packages/compliance-agent/agent.py` (main loop)
- `packages/compliance-agent/mcp_client.py` (HTTP client with mTLS)
- `packages/compliance-agent/queue.py` (SQLite offline queue)

**Key Features:**
- Poll loop with jitter (60s ±10%)
- Order verification (Ed25519 signature + TTL)
- Queue persistence (SQLite WAL)
- Health check endpoint

**Success Criteria:**
- Agent starts and polls MCP server
- Orders validated before execution
- Offline queue stores orders when MCP unavailable
- Clean shutdown on SIGTERM

#### Day 3-4: Drift Detection Engine
**Files to Create:**
- `packages/compliance-agent/drift_detector.py`
- `packages/compliance-agent/checks/` (individual check modules)
  - `patching.py`
  - `services.py`
  - `backups.py`
  - `firewall.py`
  - `logging.py`
  - `encryption.py`

**Key Features:**
- Detect NixOS generation drift
- Service health monitoring
- Backup verification (timestamp + checksum)
- Firewall ruleset hash comparison
- Log continuity checks
- LUKS status verification

**Success Criteria:**
- All 6 checks detect drift correctly
- False positive rate <5%
- Check execution time <10s total
- Pre/post state captured

#### Day 5: Self-Healing Logic
**Files to Create:**
- `packages/compliance-agent/healer.py`
- `packages/compliance-agent/rollback.py`

**Key Features:**
- `nixos-rebuild switch` with health check
- Service restart with exponential backoff
- Backup re-trigger on stale detection
- Firewall restore from signed baseline
- Automatic rollback on health check failure

**Success Criteria:**
- Remediation respects maintenance window
- Health check validates success
- Rollback triggers on failure
- No partial states left behind

### Week 2: Evidence & Testing (Days 6-10)

#### Day 6-7: Evidence Generation
**Files to Create:**
- `packages/compliance-agent/evidence.py`
- `packages/compliance-agent/crypto.py` (Ed25519 signing)

**Key Features:**
- JSON bundle generation with all required fields
- Detached Ed25519 signature
- Outcome classification (success/failed/reverted/deferred/alert)
- Pre/post state snapshots
- Timestamp with NTP-verified time

**Success Criteria:**
- Evidence bundles verify with public key
- All HIPAA-required fields present
- Bundles stored locally and pushed to MCP
- Evidence pruning respects retention rules

#### Day 8-9: Demo Stack
**Files to Create:**
- `demo/docker-compose.yml`
- `demo/mcp-stub/` (minimal MCP server for testing)
- `demo/nats/` (message queue config)
- `demo/README.md` (DEV ONLY warning)

**Key Features:**
- MCP stub server that issues orders
- NATS for message queueing
- Agent running in container
- Web UI for triggering test scenarios

**Success Criteria:**
- Full end-to-end flow working
- Can trigger drift → detect → heal → evidence
- Clearly labeled "DEV ONLY - NOT FOR PRODUCTION"

#### Day 10: Test Implementation
**Files to Create:**
- `nixosTests/phase2-integration.nix`
- `tests/signature-fail.nix`
- `tests/ttl-expired.nix`
- `tests/mcp-down.nix`
- `tests/rebuild-failure.nix`
- `tests/dns-failure.nix`

**Test Cases (from Master Alignment Brief):**
1. **Signature verify fail** → order discarded, evidence `outcome:"rejected"`
2. **TTL expired** → order discarded, evidence `outcome:"expired"`
3. **MCP down** → local queue, later flush, receipts recorded
4. **Rebuild failure** → automatic rollback + evidence `reverted`
5. **DNS failure** → no egress, evidence `outcome:"alert"`, agent keeps running

**Success Criteria:**
- All 5 test cases pass in VM environment
- Evidence bundles generated correctly
- No crashes or hangs
- Graceful degradation

---

## Implementation Details

### 1. Agent Core (agent.py)

```python
"""
Main agent loop for MSP Compliance Appliance

Architecture:
- Pull-only (no listening sockets)
- Poll MCP server for orders
- Offline queue when MCP unavailable
- Ed25519 signature verification
- Evidence generation for all actions
"""

import asyncio
import time
from typing import Optional
from .mcp_client import MCPClient
from .queue import OfflineQueue
from .drift_detector import DriftDetector
from .healer import Healer
from .evidence import EvidenceGenerator

class ComplianceAgent:
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.mcp_client = MCPClient(
            base_url=self.config['mcp_base_url'],
            cert_file=self.config['client_cert'],
            key_file=self.config['client_key'],
            ca_file=self.config['ca_cert']
        )
        self.queue = OfflineQueue(self.config['queue_path'])
        self.drift_detector = DriftDetector(self.config)
        self.healer = Healer(self.config)
        self.evidence = EvidenceGenerator(
            site_id=self.config['site_id'],
            signing_key=self.config['signing_key']
        )

        self.running = True
        self.poll_interval = self.config.get('poll_interval', 60)

    async def run(self):
        """Main agent loop"""
        print(f"Starting compliance agent for site {self.config['site_id']}")

        while self.running:
            try:
                # Add jitter to avoid thundering herd
                jitter = random.uniform(-0.1, 0.1)
                await asyncio.sleep(self.poll_interval * (1 + jitter))

                # Run compliance cycle
                await self._compliance_cycle()

            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(10)  # Back off on error

    async def _compliance_cycle(self):
        """One complete compliance cycle"""

        # 1. Poll MCP for new orders
        orders = await self._fetch_orders()

        # 2. Detect drift (local checks)
        drift_results = await self.drift_detector.check_all()

        # 3. Heal drift
        for check, result in drift_results.items():
            if result['drift_detected']:
                await self._heal_drift(check, result)

        # 4. Execute orders from MCP
        for order in orders:
            await self._execute_order(order)

        # 5. Push evidence to MCP
        await self._push_evidence()

    async def _fetch_orders(self) -> list:
        """Fetch orders from MCP server"""
        try:
            orders = await self.mcp_client.poll_orders()

            # Verify signatures and TTL
            valid_orders = []
            for order in orders:
                if self._verify_order(order):
                    valid_orders.append(order)
                else:
                    # Generate rejection evidence
                    await self.evidence.record_rejection(order)

            return valid_orders

        except Exception as e:
            print(f"MCP unavailable: {e}")
            # Work from offline queue
            return self.queue.get_pending()

    def _verify_order(self, order: dict) -> bool:
        """Verify Ed25519 signature and TTL"""
        # Check TTL
        if time.time() - order['timestamp'] > order.get('ttl', 900):
            return False

        # Verify signature
        from nacl.signing import VerifyKey
        verify_key = VerifyKey(bytes.fromhex(self.config['mcp_public_key']))

        try:
            message = json.dumps(order['payload'], sort_keys=True).encode()
            verify_key.verify(message, bytes.fromhex(order['signature']))
            return True
        except:
            return False

    async def _heal_drift(self, check: str, result: dict):
        """Heal detected drift"""
        # Check maintenance window
        if not self._in_maintenance_window() and result.get('disruptive', False):
            await self.evidence.record_deferred(check, result, reason="outside_window")
            return

        # Execute healing
        heal_result = await self.healer.heal(check, result)

        # Generate evidence
        await self.evidence.record_healing(check, result, heal_result)
```

### 2. MCP Client (mcp_client.py)

```python
"""
MCP client with mTLS and offline queue support
"""

import aiohttp
import ssl
from typing import Optional, List

class MCPClient:
    def __init__(self, base_url: str, cert_file: str, key_file: str, ca_file: str):
        self.base_url = base_url

        # Configure mTLS
        self.ssl_context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=ca_file
        )
        self.ssl_context.load_cert_chain(cert_file, key_file)
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

        self.session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self):
        """Create session if needed"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)

    async def poll_orders(self) -> List[dict]:
        """Poll MCP for new orders"""
        await self._ensure_session()

        async with self.session.get(f"{self.base_url}/api/v1/orders") as resp:
            if resp.status != 200:
                raise Exception(f"MCP returned {resp.status}")

            return await resp.json()

    async def push_evidence(self, evidence: dict) -> bool:
        """Push evidence bundle to MCP"""
        await self._ensure_session()

        async with self.session.post(
            f"{self.base_url}/api/v1/evidence",
            json=evidence
        ) as resp:
            return resp.status == 200

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()
```

### 3. Drift Detector (drift_detector.py)

```python
"""
Detect drift from desired state across 6 compliance checks
"""

from typing import Dict
import subprocess
import json

class DriftDetector:
    def __init__(self, config: dict):
        self.config = config
        self.checks = {
            'patching': self._check_patching,
            'services': self._check_services,
            'backups': self._check_backups,
            'firewall': self._check_firewall,
            'logging': self._check_logging,
            'encryption': self._check_encryption
        }

    async def check_all(self) -> Dict[str, dict]:
        """Run all drift checks"""
        results = {}

        for check_name, check_func in self.checks.items():
            try:
                results[check_name] = await check_func()
            except Exception as e:
                results[check_name] = {
                    'drift_detected': False,
                    'error': str(e)
                }

        return results

    async def _check_patching(self) -> dict:
        """Check if NixOS generation matches expected"""
        # Get current generation
        result = subprocess.run(
            ['nix', 'flake', 'metadata', '--json'],
            capture_output=True,
            text=True
        )

        current = json.loads(result.stdout)
        expected = self.config.get('expected_flake_hash')

        return {
            'drift_detected': current['locked']['narHash'] != expected,
            'current_hash': current['locked']['narHash'],
            'expected_hash': expected,
            'disruptive': True  # Rebuild is disruptive
        }

    async def _check_services(self) -> dict:
        """Check critical services are running"""
        services = self.config.get('critical_services', ['sshd'])

        down_services = []
        for service in services:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True,
                text=True
            )

            if result.stdout.strip() != 'active':
                down_services.append(service)

        return {
            'drift_detected': len(down_services) > 0,
            'down_services': down_services,
            'disruptive': False  # Service restart is quick
        }

    async def _check_backups(self) -> dict:
        """Check backup recency and integrity"""
        backup_path = self.config.get('backup_status_file', '/var/lib/backup/last-success')

        try:
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)

            # Check if backup is stale (>48 hours)
            age_hours = (time.time() - backup_data['timestamp']) / 3600
            stale = age_hours > 48

            return {
                'drift_detected': stale,
                'backup_age_hours': age_hours,
                'last_checksum': backup_data.get('checksum'),
                'disruptive': False
            }
        except:
            return {
                'drift_detected': True,
                'error': 'No backup status found',
                'disruptive': False
            }

    async def _check_firewall(self) -> dict:
        """Check firewall ruleset matches baseline"""
        result = subprocess.run(
            ['nft', 'list', 'ruleset'],
            capture_output=True,
            text=True
        )

        import hashlib
        current_hash = hashlib.sha256(result.stdout.encode()).hexdigest()
        expected_hash = self.config.get('firewall_ruleset_hash')

        return {
            'drift_detected': current_hash != expected_hash,
            'current_hash': current_hash,
            'expected_hash': expected_hash,
            'disruptive': False
        }

    async def _check_logging(self) -> dict:
        """Check logging services are working"""
        # Check journald
        result = subprocess.run(
            ['systemctl', 'is-active', 'systemd-journald'],
            capture_output=True,
            text=True
        )

        journald_up = result.stdout.strip() == 'active'

        # Send test log and verify it appears
        test_message = f"compliance-agent-canary-{time.time()}"
        subprocess.run(['logger', test_message])

        # Check if canary appears in journal
        result = subprocess.run(
            ['journalctl', '-n', '100', '--no-pager'],
            capture_output=True,
            text=True
        )

        canary_found = test_message in result.stdout

        return {
            'drift_detected': not (journald_up and canary_found),
            'journald_active': journald_up,
            'canary_verified': canary_found,
            'disruptive': False
        }

    async def _check_encryption(self) -> dict:
        """Check LUKS volumes are active"""
        result = subprocess.run(
            ['lsblk', '--json', '-o', 'NAME,TYPE'],
            capture_output=True,
            text=True
        )

        devices = json.loads(result.stdout)

        # Find crypt devices
        crypt_devices = []
        for dev in devices['blockdevices']:
            if dev.get('type') == 'crypt':
                crypt_devices.append(dev['name'])

        expected_count = self.config.get('expected_luks_volumes', 1)

        return {
            'drift_detected': len(crypt_devices) < expected_count,
            'active_volumes': crypt_devices,
            'expected_count': expected_count,
            'disruptive': False  # Can't auto-fix encryption
        }
```

---

## Success Metrics

### Phase 2 Definition of Done

- [ ] Agent starts and runs stable for 24+ hours
- [ ] All 6 drift checks working correctly
- [ ] Self-healing executes with rollback on failure
- [ ] Evidence bundles generated and signed
- [ ] Maintenance window enforcement working
- [ ] 5 test cases passing in VM environment
- [ ] /demo stack running end-to-end
- [ ] Documentation updated

### Performance Targets

- **Drift detection:** <10s for all 6 checks
- **Evidence generation:** <1s per bundle
- **Memory footprint:** <50MB resident
- **Poll loop:** 60s ±10% jitter
- **Offline queue:** Survive 24h MCP outage

---

## Next Steps

### This Week (Days 1-5)

**Start with Agent Core:**
1. Create `packages/compliance-agent/agent.py` skeleton
2. Implement basic poll loop with jitter
3. Add Ed25519 signature verification
4. Test with stub MCP server

**Then Drift Detection:**
5. Implement 6 check modules
6. Test each check individually
7. Integration test all checks together

### Next Week (Days 6-10)

**Evidence & Testing:**
8. Implement evidence generation
9. Create /demo Docker Compose stack
10. Implement 5 VM test cases
11. Full end-to-end smoke test

---

**Ready to start? Let me know which component you want to tackle first!**

Options:
A. Agent Core (agent.py + mcp_client.py + queue.py)
B. Drift Detection (6 check modules)
C. Demo Stack (Docker Compose for testing)
D. Something else from the roadmap
