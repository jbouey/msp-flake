# Checkin Handler Decomposition Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break `appliance_checkin` (1,373 lines) into focused, testable helpers without changing behavior.

**Architecture:** The handler remains the orchestrator. Each STEP group moves to a dedicated helper module. All helpers take a shared `CheckinContext` dataclass to avoid passing 20+ parameters. Transaction savepoints preserved exactly as-is — each helper wraps its own `async with conn.transaction():`.

**Tech Stack:** Python 3.11, FastAPI, asyncpg, SQLAlchemy (shared engine only).

---

## File Structure

**Before:**
- `sites.py::appliance_checkin` (lines 2877-4250, ~1,373 lines)

**After:**
- `sites.py::appliance_checkin` (~150 lines, orchestration only)
- `checkin/context.py` — `CheckinContext` dataclass + helpers
- `checkin/identity.py` — STEP 0, 0.9, 1, 2, 2.9, 3, 3.3 (deploy results, ghost detection, appliance upsert, display_name)
- `checkin/subsystems.py` — STEP 3.4, 3.5, 3.6, 3.6b (stale cleanup, appliances table, signing key, WG status)
- `checkin/devices.py` — STEP 3.7, 3.7b, 3.7c (Go agents, discovered devices, workstations sync)
- `checkin/mesh.py` — STEP 3.8, 3.8b, 3.8c, 3.9 (app protection, mesh peers, target assignment, witnessing)
- `checkin/orders.py` — STEP 4, 4.5 (pending orders, fleet-wide orders)
- `checkin/targets.py` — STEP 5 PRELUDE, 5, 5b (discovery filter, Windows targets, Linux targets)
- `checkin/config.py` — STEP 6, 6b, 6b-2, 6c (runbooks, drift config, alert mode, maintenance)
- `checkin/deployment.py` — STEP 7, 7b, 7c (enumeration triggers, billing, pending devices)

All helpers return the data the response needs, mutate `context` for shared state, and handle their own transaction savepoints.

---

## Task 1: Create CheckinContext dataclass

**Files:**
- Create: `mcp-server/central-command/backend/checkin/__init__.py`
- Create: `mcp-server/central-command/backend/checkin/context.py`

- [ ] **Step 1: Create the package init**

```python
# mcp-server/central-command/backend/checkin/__init__.py
"""Checkin handler decomposition — extracted from sites.py for maintainability.

Each module handles a logical group of STEP blocks from the original handler.
All helpers operate on a shared CheckinContext and use explicit transaction
savepoints (NEVER bare queries — poisoned transactions cascade).
"""
```

- [ ] **Step 2: Create CheckinContext dataclass with all mutable state**

```python
# mcp-server/central-command/backend/checkin/context.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any


@dataclass
class CheckinContext:
    """Shared state across all checkin STEPs.

    The original handler mutates ~30 local variables across 1,300 lines.
    This dataclass makes the contract explicit.
    """
    # From request
    checkin: Any  # ApplianceCheckin model
    request_ip: str
    user_agent: str
    auth_site_id: str
    now: datetime

    # Computed during identity steps
    appliance_id: str = ""
    canonical_appliance_id: str = ""
    canonical_id: str = ""
    site_id: str = ""
    is_ghost: bool = False
    merge_from_ids: List[str] = field(default_factory=list)
    earliest_first_checkin: Optional[datetime] = None
    display_name: str = ""
    rotated_api_key: Optional[str] = None

    # Computed during subsystem steps
    signing_key_registered: bool = False
    agent_public_key_hash: str = ""

    # Accumulated for response
    windows_targets: List[Dict[str, Any]] = field(default_factory=list)
    linux_targets: List[Dict[str, Any]] = field(default_factory=list)
    pending_orders: List[Dict[str, Any]] = field(default_factory=list)
    fleet_orders: List[Dict[str, Any]] = field(default_factory=list)
    disabled_checks: List[str] = field(default_factory=list)
    runbook_config: Dict[str, Any] = field(default_factory=dict)
    mesh_peers: List[Dict[str, Any]] = field(default_factory=list)
    peer_bundle_hashes: List[str] = field(default_factory=list)
    target_assignment: Dict[str, Any] = field(default_factory=dict)
    alert_mode: str = "standard"
    maintenance_window: Optional[Dict[str, Any]] = None
    deployment_triggers: Dict[str, Any] = field(default_factory=dict)
    billing_status: Dict[str, Any] = field(default_factory=dict)
    pending_devices: List[Dict[str, Any]] = field(default_factory=list)
    all_mac_addresses: List[str] = field(default_factory=list)
    boot_source: str = ""
```

- [ ] **Step 3: Write context test**

```python
# tests/test_checkin_context.py
from dashboard_api.checkin.context import CheckinContext

def test_context_defaults():
    ctx = CheckinContext(checkin=None, request_ip="1.2.3.4",
                         user_agent="test", auth_site_id="site-1",
                         now=datetime.utcnow())
    assert ctx.windows_targets == []
    assert ctx.is_ghost is False
```

- [ ] **Step 4: Commit**

```bash
git add mcp-server/central-command/backend/checkin/
git commit -m "feat: checkin package skeleton + CheckinContext dataclass"
```

---

## Task 2: Extract identity.py (STEPS 0, 0.9, 1, 2, 2.9, 3, 3.3)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/identity.py`
- Modify: `mcp-server/central-command/backend/sites.py:2914-3154`

**Lines from original:** 2914-3154 (~240 lines)

**Boundaries:**
- STEP 0: process_deploy_results — deploy result scrubbing + storage
- STEP 0.9: detect_ghost_appliance — multi-NIC ghost detection via all_mac_addresses
- STEP 1: find_duplicates — query for existing appliances with same MAC
- STEP 2: delete_duplicates — soft-delete ghost rows
- STEP 2.9: detect_boot_source — live_usb vs installed_disk
- STEP 3: upsert_canonical_appliance — INSERT/UPDATE site_appliances
- STEP 3.3: auto_generate_display_name — hostname-based deduplication

- [ ] **Step 1: Write failing test for ghost detection**

```python
# tests/test_checkin_identity.py
import pytest
from dashboard_api.checkin.identity import detect_ghost_appliance
from dashboard_api.checkin.context import CheckinContext

@pytest.mark.asyncio
async def test_ghost_detection_matches_overlapping_macs(fake_conn):
    # Fake conn with site_appliances having 84:3A:5B:1F:FF:E4 on another appliance
    ctx = CheckinContext(...)
    ctx.all_mac_addresses = ["84:3A:5B:1F:FF:E4", "00:11:22:33:44:55"]
    await detect_ghost_appliance(fake_conn, ctx)
    assert ctx.is_ghost is True
```

- [ ] **Step 2: Run test (should fail — module doesn't exist)**

```bash
pytest tests/test_checkin_identity.py::test_ghost_detection_matches_overlapping_macs -v
```

Expected: ImportError / ModuleNotFoundError

- [ ] **Step 3: Extract identity.py with exact logic from sites.py:2914-3154**

```python
# mcp-server/central-command/backend/checkin/identity.py
"""Identity steps of checkin: ghost detection, appliance upsert, display_name.

Each function wraps its step in a SAVEPOINT so failures don't poison
the outer transaction.
"""
import json
import structlog
from asyncpg import Connection

from .context import CheckinContext
from ..phi_boundary import scrub_deploy_results  # or wherever

logger = structlog.get_logger()


async def process_deploy_results(conn: Connection, ctx: CheckinContext) -> None:
    """STEP 0: Process deploy results from previous checkin cycle.
    Scrubs hostnames + errors, inserts into device_deployments."""
    # Exact code from sites.py:2914-2947
    ...


async def detect_ghost_appliance(conn: Connection, ctx: CheckinContext) -> None:
    """STEP 0.9: Multi-NIC ghost detection.
    Method 1: MAC list overlap. Method 2: IP+timing within 30s window."""
    # Exact code from sites.py:2948-3003
    ...


async def find_and_merge_duplicates(conn: Connection, ctx: CheckinContext) -> None:
    """STEPS 1 + 2: Find existing appliances with same MAC, soft-delete duplicates."""
    # Exact code from sites.py:3004-3045
    ...


async def detect_boot_source(conn: Connection, ctx: CheckinContext) -> None:
    """STEP 2.9: live_usb vs installed_disk telemetry for install verification."""
    # Exact code from sites.py:3046-3089
    ...


async def upsert_canonical_appliance(conn: Connection, ctx: CheckinContext) -> None:
    """STEP 3: INSERT/UPDATE site_appliances. Sets canonical_appliance_id.
    Skipped for ghost appliances."""
    # Exact code from sites.py:3090-3125
    ...


async def auto_generate_display_name(conn: Connection, ctx: CheckinContext) -> None:
    """STEP 3.3: Auto-generate display_name for duplicate hostnames.
    First = hostname, subsequent = {hostname}-{N}."""
    # Exact code from sites.py:3126-3154
    ...
```

- [ ] **Step 4: Run test again (should pass)**

```bash
pytest tests/test_checkin_identity.py -v
```

- [ ] **Step 5: Replace STEPS 0-3.3 in sites.py with calls**

```python
# sites.py — in appliance_checkin, replace lines 2914-3154 with:
from .checkin.identity import (
    process_deploy_results,
    detect_ghost_appliance,
    find_and_merge_duplicates,
    detect_boot_source,
    upsert_canonical_appliance,
    auto_generate_display_name,
)

# ... existing setup ...
async with admin_connection(pool) as conn:
    ctx = CheckinContext(
        checkin=checkin, request_ip=request.client.host,
        user_agent=request.headers.get("user-agent", ""),
        auth_site_id=auth_site_id, now=datetime.now(timezone.utc),
    )

    await process_deploy_results(conn, ctx)
    await detect_ghost_appliance(conn, ctx)

    if not ctx.is_ghost:
        await find_and_merge_duplicates(conn, ctx)
        await detect_boot_source(conn, ctx)
        await upsert_canonical_appliance(conn, ctx)
        await auto_generate_display_name(conn, ctx)
```

- [ ] **Step 6: Run full checkin integration test**

```bash
pytest tests/test_checkin.py -v
```

Expected: all existing checkin tests still pass (behavior identical).

- [ ] **Step 7: Commit**

```bash
git add mcp-server/central-command/backend/checkin/identity.py \
        mcp-server/central-command/backend/sites.py \
        mcp-server/central-command/backend/tests/test_checkin_identity.py
git commit -m "refactor: extract checkin identity steps to checkin/identity.py"
```

---

## Task 3: Extract subsystems.py (STEPS 3.4, 3.5, 3.6, 3.6b)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/subsystems.py`
- Modify: `mcp-server/central-command/backend/sites.py:3155-3247`

**Lines from original:** 3155-3247 (~92 lines)

**Functions to extract:**
- `cleanup_stale_devices` (STEP 3.4) — device cleanup on subnet change
- `sync_appliances_table` (STEP 3.5) — mirror to legacy `appliances` table for fleet_updates
- `register_signing_key` (STEP 3.6) — per-appliance Ed25519 key registration
- `update_wireguard_status` (STEP 3.6b) — WG access state from daemon

(Follow the same pattern: write failing test, extract with exact logic, replace in sites.py, run integration tests, commit.)

---

## Task 4: Extract devices.py (STEPS 3.7, 3.7b, 3.7c)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/devices.py`
- Modify: `mcp-server/central-command/backend/sites.py:3248-3419`

**Lines from original:** 3248-3419 (~172 lines)

**Functions:**
- `sync_go_agents` (STEP 3.7) — connected Go agents → `go_agents` table
- `link_discovered_devices` (STEP 3.7b) — discovered_devices → workstations
- `sync_workstations_from_agents` (STEP 3.7c) — Go agent data → workstations + cleanup

---

## Task 5: Extract mesh.py (STEPS 3.8, 3.8b, 3.8c, 3.9)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/mesh.py`
- Modify: `mcp-server/central-command/backend/sites.py:3420-3554, 3840-3936`

**Functions:**
- `handle_app_protection_discovery` (STEP 3.8) — app protection scan results
- `build_mesh_peer_list` (STEP 3.8b) — cross-subnet mesh discovery
- `compute_target_assignment` (STEP 3.8c) — SERVER-SIDE hash ring target assignment
- `exchange_peer_witness_hashes` (STEP 3.9) — peer witnessing

**Critical:** target_assignment uses hash_ring.py. Keep that dependency visible in the function signature.

---

## Task 6: Extract orders.py (STEPS 4, 4.5)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/orders.py`
- Modify: `mcp-server/central-command/backend/sites.py:3555-3630`

**Functions:**
- `fetch_pending_orders` (STEP 4) — per-appliance pending orders
- `fetch_fleet_orders` (STEP 4.5) — site-wide fleet orders

---

## Task 7: Extract targets.py (STEPS 5 PRELUDE, 5, 5b)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/targets.py`
- Modify: `mcp-server/central-command/backend/sites.py:3631-3839`

**Lines from original:** 3631-3839 (~208 lines — this is the biggest extraction)

**Functions:**
- `apply_discovery_target_filter` (STEP 5 PRELUDE) — discovery-based filtering
- `build_windows_targets` (STEP 5) — Windows credentials + target list
- `build_linux_targets` (STEP 5b) — SSH credentials + Linux target list

**Critical:** Credential encryption must be preserved. Use `credential_crypto.decrypt()` explicitly.

---

## Task 8: Extract config.py (STEPS 6, 6b, 6b-2, 6c)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/config.py`
- Modify: `mcp-server/central-command/backend/sites.py:3937-4018`

**Functions:**
- `fetch_runbook_config` (STEP 6) — enabled runbooks pull
- `fetch_drift_config` (STEP 6b) — disabled check types per site
- `resolve_alert_mode` (STEP 6b-2) — effective alert mode for daemon
- `fetch_maintenance_window` (STEP 6c) — active maintenance window

---

## Task 9: Extract deployment.py (STEPS 7, 7b, 7c)

**Files:**
- Create: `mcp-server/central-command/backend/checkin/deployment.py`
- Modify: `mcp-server/central-command/backend/sites.py:4019-4250`

**Functions:**
- `check_enumeration_triggers` (STEP 7) — zero-friction deployment triggers
- `check_billing_status` (STEP 7b) — Stripe subscription status
- `fetch_pending_devices` (STEP 7c) — devices awaiting deployment

---

## Task 10: Final orchestration cleanup

**Files:**
- Modify: `mcp-server/central-command/backend/sites.py:2877-2912` (keep) + the new orchestration body

- [ ] **Step 1: Verify final handler is ~150 lines**

```bash
awk '/^async def appliance_checkin/,/^async def [a-z]/' \
  mcp-server/central-command/backend/sites.py | wc -l
```

Expected: 150-200 lines (was 1,373).

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/test_checkin.py tests/test_checkin_*.py -v
```

Expected: all pass.

- [ ] **Step 3: Run existing healing pipeline integrity tests**

```bash
pytest tests/test_healing_pipeline_integrity.py -v
```

Expected: all pass (no regression to scoring/registry).

- [ ] **Step 4: Manual smoke test against live VPS**

Send a real checkin from the v0.3.89 appliance and verify response matches the old shape.

- [ ] **Step 5: Commit final cleanup**

```bash
git commit -m "refactor: checkin handler now 150 lines orchestrating focused helpers"
```

---

## Risk Mitigation

- **Savepoint preservation is non-negotiable.** Every STEP wraps in `async with conn.transaction():`. Don't drop these.
- **Mutation order matters.** `ctx.appliance_id` is set in STEP 3 and used by STEP 3.5+. Don't reorder.
- **phiscrub calls must stay.** STEP 0 and STEP 3.7 scrub hostnames before storage. Verify every extracted module imports phi_boundary helpers.
- **Per-appliance signing keys.** STEP 3.6 writes to `site_appliances.agent_public_key`, NOT `sites.agent_public_key`. Multi-appliance sites break if wrong.
- **Test after EVERY task.** Don't batch 3 tasks before running tests — the integration test is the safety net.

## Out of Scope

- Changing any STEP logic (pure refactor only)
- Changing the response shape
- Adding new functionality
- Changing database queries
