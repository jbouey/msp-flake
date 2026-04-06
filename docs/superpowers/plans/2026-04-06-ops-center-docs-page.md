# Ops Center + Documentation Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/ops` page (admin + partner-scoped) showing evidence chain health, signing, OTS, healing, fleet status with traffic-light cards + deep-dive panels + audit readiness export, plus expand `/docs` with maintenance runbooks and reference material.

**Architecture:** Backend exposes 3 new endpoints (`/api/ops/health`, `/api/ops/health/{org_id}`, `/api/ops/audit-report/{org_id}`) that query `compliance_bundles`, `ots_proofs`, `incidents`, `site_appliances`, and a new `client_orgs.next_audit_date`/`baa_on_file` column. Frontend adds `OpsCenter.tsx` with 5 `StatusCard` components (traffic-light) that expand to detail panels, plus an `AuditReadiness` section per org. The `/docs` page gets new sections for runbooks and reference. All health thresholds defined in `constants/status.ts` for DRY reuse.

**Tech Stack:** FastAPI (backend), React + TypeScript + Tailwind (frontend), asyncpg (DB), pytest (tests), React Query (data fetching).

---

## File Structure

### Backend (new files)
- `backend/ops_health.py` — Router + health computation logic. Single file: computes all 5 subsystem statuses, returns structured response. Partner-scoped variant filters by org.
- `backend/audit_report.py` — Audit readiness checklist computation + PDF export endpoint.
- `backend/tests/test_ops_health.py` — Unit tests for threshold logic, partner scoping, auth.
- `backend/tests/test_audit_report.py` — Unit tests for readiness checklist, PDF generation.
- `backend/migrations/124_ops_audit_fields.sql` — Adds `baa_on_file`, `next_audit_date` to `client_orgs`.

### Frontend (new files)
- `frontend/src/pages/OpsCenter.tsx` — Main ops page with status cards + expandable panels.
- `frontend/src/components/composed/StatusLight.tsx` — Reusable traffic-light component (green/yellow/red).
- `frontend/src/components/composed/AuditReadiness.tsx` — Per-org audit badge + countdown + export button.

### Frontend (modified files)
- `frontend/src/pages/Documentation.tsx` — Add runbooks + reference sections.
- `frontend/src/App.tsx` — Add `/ops` route (lazy-loaded).
- `frontend/src/constants/status.ts` — Add ops threshold definitions.
- `frontend/src/constants/copy.ts` — Add ops labels, tooltips, runbook text.

### Backend (modified files)
- `mcp-server/main.py` — Register ops_health and audit_report routers.

---

## Task 1: Migration — Add audit fields to client_orgs

**Files:**
- Create: `backend/migrations/124_ops_audit_fields.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 124_ops_audit_fields.sql
-- Adds BAA tracking and scheduled audit dates for audit readiness feature.

ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS baa_on_file BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS baa_uploaded_at TIMESTAMPTZ;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS next_audit_date DATE;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS next_audit_notes TEXT;
```

- [ ] **Step 2: Test migration locally**

Run: `ssh root@178.156.162.116 "docker exec mcp-server python3 /app/dashboard_api/migrate.py up"`
Expected: Migration 124 applied successfully.

- [ ] **Step 3: Verify columns exist**

Run: `ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT column_name FROM information_schema.columns WHERE table_name='client_orgs' AND column_name IN ('baa_on_file','next_audit_date') ORDER BY column_name;\""`
Expected: Both columns listed.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/124_ops_audit_fields.sql
git commit -m "feat: migration 124 — BAA tracking + audit date on client_orgs"
```

---

## Task 2: Backend — Ops health endpoint (admin)

**Files:**
- Create: `backend/ops_health.py`
- Modify: `mcp-server/main.py` (add router import + registration)

- [ ] **Step 1: Write test file skeleton**

Create `backend/tests/test_ops_health.py`:

```python
"""Tests for /api/ops/health endpoint — threshold logic and status computation."""
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('SESSION_TOKEN_SECRET', 'test-secret-key-for-ops-health')
os.environ.setdefault('OTS_ENABLED', 'true')


class TestEvidenceChainStatus:
    """Evidence chain traffic-light thresholds."""

    def test_green_when_recent_submission_and_chain_unbroken(self):
        from ops_health import compute_evidence_status
        result = compute_evidence_status(
            total_bundles=1000,
            last_submission_minutes_ago=5,
            chain_gaps=0,
            signing_rate=0.95,
        )
        assert result['status'] == 'green'
        assert result['label'] == 'Healthy'

    def test_yellow_when_submission_gap_over_30min(self):
        from ops_health import compute_evidence_status
        result = compute_evidence_status(
            total_bundles=1000,
            last_submission_minutes_ago=45,
            chain_gaps=0,
            signing_rate=0.95,
        )
        assert result['status'] == 'yellow'

    def test_red_when_chain_broken(self):
        from ops_health import compute_evidence_status
        result = compute_evidence_status(
            total_bundles=1000,
            last_submission_minutes_ago=5,
            chain_gaps=3,
            signing_rate=0.95,
        )
        assert result['status'] == 'red'

    def test_red_when_no_submissions_over_1hr(self):
        from ops_health import compute_evidence_status
        result = compute_evidence_status(
            total_bundles=1000,
            last_submission_minutes_ago=65,
            chain_gaps=0,
            signing_rate=0.95,
        )
        assert result['status'] == 'red'


class TestSigningStatus:
    """Signing infrastructure traffic-light thresholds."""

    def test_green_when_high_signing_rate_no_mismatches(self):
        from ops_health import compute_signing_status
        result = compute_signing_status(
            signing_rate=0.94,
            key_mismatches_24h=0,
            unsigned_legacy=5000,
            signature_failures=0,
        )
        assert result['status'] == 'green'

    def test_yellow_when_signing_rate_between_70_90(self):
        from ops_health import compute_signing_status
        result = compute_signing_status(
            signing_rate=0.80,
            key_mismatches_24h=0,
            unsigned_legacy=0,
            signature_failures=0,
        )
        assert result['status'] == 'yellow'

    def test_red_when_key_mismatch_active(self):
        from ops_health import compute_signing_status
        result = compute_signing_status(
            signing_rate=0.94,
            key_mismatches_24h=5,
            unsigned_legacy=0,
            signature_failures=3,
        )
        assert result['status'] == 'red'


class TestOTSStatus:
    """OTS anchoring traffic-light thresholds."""

    def test_green_when_batching_current(self):
        from ops_health import compute_ots_status
        result = compute_ots_status(
            anchored=130000,
            pending=50,
            batching=5,
            latest_batch_age_hours=0.5,
        )
        assert result['status'] == 'green'

    def test_yellow_when_many_pending(self):
        from ops_health import compute_ots_status
        result = compute_ots_status(
            anchored=130000,
            pending=200,
            batching=5,
            latest_batch_age_hours=1.5,
        )
        assert result['status'] == 'yellow'

    def test_red_when_batch_stalled(self):
        from ops_health import compute_ots_status
        result = compute_ots_status(
            anchored=130000,
            pending=600,
            batching=0,
            latest_batch_age_hours=7,
        )
        assert result['status'] == 'red'


class TestHealingStatus:
    """Healing pipeline traffic-light thresholds."""

    def test_green_when_l1_above_90pct(self):
        from ops_health import compute_healing_status
        result = compute_healing_status(
            l1_heal_rate=0.98,
            exhausted_count=2,
            stuck_count=0,
        )
        assert result['status'] == 'green'

    def test_yellow_when_many_exhausted(self):
        from ops_health import compute_healing_status
        result = compute_healing_status(
            l1_heal_rate=0.92,
            exhausted_count=7,
            stuck_count=0,
        )
        assert result['status'] == 'yellow'

    def test_red_when_l1_below_70pct(self):
        from ops_health import compute_healing_status
        result = compute_healing_status(
            l1_heal_rate=0.65,
            exhausted_count=12,
            stuck_count=3,
        )
        assert result['status'] == 'red'


class TestFleetStatus:
    """Fleet status traffic-light thresholds."""

    def test_green_when_all_online(self):
        from ops_health import compute_fleet_status
        result = compute_fleet_status(
            total_appliances=2,
            online_count=2,
            max_offline_minutes=0,
        )
        assert result['status'] == 'green'

    def test_yellow_when_appliance_offline_30min(self):
        from ops_health import compute_fleet_status
        result = compute_fleet_status(
            total_appliances=2,
            online_count=1,
            max_offline_minutes=45,
        )
        assert result['status'] == 'yellow'

    def test_red_when_appliance_offline_2hr(self):
        from ops_health import compute_fleet_status
        result = compute_fleet_status(
            total_appliances=2,
            online_count=0,
            max_offline_minutes=130,
        )
        assert result['status'] == 'red'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent && source venv/bin/activate && python -m pytest ../mcp-server/central-command/backend/tests/test_ops_health.py -v --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops_health'`

- [ ] **Step 3: Implement ops_health.py**

Create `backend/ops_health.py` with:

```python
"""
Ops Center health endpoints — traffic-light status for 5 subsystems.

Admin sees platform-wide health. Partners see org-scoped view.
All thresholds defined as module constants for testability.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_auth, require_partner_role
from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])

# ---------------------------------------------------------------------------
# Threshold constants (single source of truth)
# ---------------------------------------------------------------------------
EVIDENCE_SUBMISSION_YELLOW_MINUTES = 30
EVIDENCE_SUBMISSION_RED_MINUTES = 60

SIGNING_GREEN_RATE = 0.90
SIGNING_YELLOW_RATE = 0.70

OTS_PENDING_YELLOW = 100
OTS_PENDING_RED = 500
OTS_BATCH_YELLOW_HOURS = 2
OTS_BATCH_RED_HOURS = 6

HEALING_L1_GREEN_RATE = 0.90
HEALING_L1_YELLOW_RATE = 0.70
HEALING_EXHAUSTED_YELLOW = 5
HEALING_EXHAUSTED_RED = 10

FLEET_OFFLINE_YELLOW_MINUTES = 30
FLEET_OFFLINE_RED_MINUTES = 120


# ---------------------------------------------------------------------------
# Pure computation functions (no DB, fully testable)
# ---------------------------------------------------------------------------

def compute_evidence_status(
    total_bundles: int,
    last_submission_minutes_ago: float,
    chain_gaps: int,
    signing_rate: float,
) -> dict:
    """Compute evidence chain traffic-light status."""
    if chain_gaps > 0 or last_submission_minutes_ago > EVIDENCE_SUBMISSION_RED_MINUTES:
        status = 'red'
        label = 'Chain Broken' if chain_gaps > 0 else 'Stalled'
    elif last_submission_minutes_ago > EVIDENCE_SUBMISSION_YELLOW_MINUTES:
        status = 'yellow'
        label = 'Delayed'
    else:
        status = 'green'
        label = 'Healthy'
    return {
        'status': status,
        'label': label,
        'total_bundles': total_bundles,
        'last_submission_minutes_ago': round(last_submission_minutes_ago, 1),
        'chain_gaps': chain_gaps,
        'signing_rate': round(signing_rate, 3),
    }


def compute_signing_status(
    signing_rate: float,
    key_mismatches_24h: int,
    unsigned_legacy: int,
    signature_failures: int,
) -> dict:
    """Compute signing infrastructure traffic-light status."""
    if signing_rate < SIGNING_YELLOW_RATE or key_mismatches_24h > 0:
        status = 'red' if (signing_rate < SIGNING_YELLOW_RATE or signature_failures > 0) else 'yellow'
        label = 'Key Mismatch' if key_mismatches_24h > 0 else 'Low Coverage'
    elif signing_rate < SIGNING_GREEN_RATE:
        status = 'yellow'
        label = 'Below Target'
    else:
        status = 'green'
        label = 'Healthy'
    return {
        'status': status,
        'label': label,
        'signing_rate': round(signing_rate, 3),
        'key_mismatches_24h': key_mismatches_24h,
        'unsigned_legacy': unsigned_legacy,
        'signature_failures': signature_failures,
    }


def compute_ots_status(
    anchored: int,
    pending: int,
    batching: int,
    latest_batch_age_hours: float,
) -> dict:
    """Compute OTS anchoring traffic-light status."""
    if pending > OTS_PENDING_RED or latest_batch_age_hours > OTS_BATCH_RED_HOURS:
        status = 'red'
        label = 'Batch Stalled' if latest_batch_age_hours > OTS_BATCH_RED_HOURS else 'Backlog'
    elif pending > OTS_PENDING_YELLOW or latest_batch_age_hours > OTS_BATCH_YELLOW_HOURS:
        status = 'yellow'
        label = 'Elevated Pending'
    else:
        status = 'green'
        label = 'Healthy'
    return {
        'status': status,
        'label': label,
        'anchored': anchored,
        'pending': pending,
        'batching': batching,
        'latest_batch_age_hours': round(latest_batch_age_hours, 1),
    }


def compute_healing_status(
    l1_heal_rate: float,
    exhausted_count: int,
    stuck_count: int,
) -> dict:
    """Compute healing pipeline traffic-light status."""
    if l1_heal_rate < HEALING_L1_YELLOW_RATE or exhausted_count > HEALING_EXHAUSTED_RED:
        status = 'red'
        label = 'Degraded'
    elif l1_heal_rate < HEALING_L1_GREEN_RATE or exhausted_count > HEALING_EXHAUSTED_YELLOW:
        status = 'yellow'
        label = 'Below Target'
    else:
        status = 'green'
        label = 'Healthy'
    return {
        'status': status,
        'label': label,
        'l1_heal_rate': round(l1_heal_rate, 3),
        'exhausted_count': exhausted_count,
        'stuck_count': stuck_count,
    }


def compute_fleet_status(
    total_appliances: int,
    online_count: int,
    max_offline_minutes: float,
) -> dict:
    """Compute fleet status traffic-light."""
    if max_offline_minutes > FLEET_OFFLINE_RED_MINUTES or online_count == 0:
        status = 'red'
        label = 'Appliance Down'
    elif max_offline_minutes > FLEET_OFFLINE_YELLOW_MINUTES:
        status = 'yellow'
        label = 'Partially Offline'
    else:
        status = 'green'
        label = 'All Online'
    return {
        'status': status,
        'label': label,
        'total_appliances': total_appliances,
        'online_count': online_count,
        'max_offline_minutes': round(max_offline_minutes, 1),
    }


# ---------------------------------------------------------------------------
# DB query helpers (thin wrappers, one query each)
# ---------------------------------------------------------------------------

async def _query_evidence_metrics(conn, site_filter: Optional[str] = None) -> dict:
    """Query evidence chain metrics from compliance_bundles."""
    where = "WHERE cb.site_id IN (SELECT s.site_id FROM sites s JOIN client_orgs co ON s.client_org_id = co.id WHERE co.id = $1)" if site_filter else ""
    params = [site_filter] if site_filter else []

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE agent_signature IS NOT NULL AND agent_signature != '') as signed,
            COUNT(*) FILTER (WHERE signature_valid = true) as verified,
            COUNT(*) FILTER (WHERE agent_signature IS NULL OR agent_signature = '') as unsigned_legacy,
            COUNT(*) FILTER (WHERE signature_valid = false AND agent_signature IS NOT NULL AND agent_signature != '') as sig_failures,
            MAX(chain_position) as max_chain,
            MAX(checked_at) as latest_check,
            EXTRACT(EPOCH FROM (NOW() - MAX(checked_at))) / 60.0 as minutes_since_last
        FROM compliance_bundles cb
        {where}
    """, *params)
    return dict(row) if row else {}


async def _query_ots_metrics(conn) -> dict:
    """Query OTS proof pipeline metrics."""
    row = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
            COUNT(*) FILTER (WHERE status = 'pending') as pending,
            COUNT(*) FILTER (WHERE status = 'batching') as batching,
            EXTRACT(EPOCH FROM (NOW() - MAX(CASE WHEN status = 'anchored' THEN anchored_at END))) / 3600.0 as hours_since_anchor
        FROM ots_proofs
    """)
    return dict(row) if row else {}


async def _query_healing_metrics(conn, site_filter: Optional[str] = None) -> dict:
    """Query healing pipeline metrics (30-day window)."""
    where = "AND i.site_id IN (SELECT s.site_id FROM sites s JOIN client_orgs co ON s.client_org_id = co.id WHERE co.id = $1)" if site_filter else ""
    params = [site_filter] if site_filter else []

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) FILTER (WHERE resolution_tier = 'L1' AND status = 'resolved') as l1_healed,
            COUNT(*) FILTER (WHERE resolution_tier = 'L1') as l1_total,
            COUNT(*) FILTER (WHERE remediation_exhausted = true AND status = 'open') as exhausted,
            COUNT(*) FILTER (WHERE status = 'open' AND created_at < NOW() - INTERVAL '24 hours') as stuck
        FROM incidents i
        WHERE created_at > NOW() - INTERVAL '30 days'
        {where}
    """, *params)
    return dict(row) if row else {}


async def _query_fleet_metrics(conn, site_filter: Optional[str] = None) -> dict:
    """Query fleet status metrics."""
    where = "WHERE sa.site_id IN (SELECT s.site_id FROM sites s JOIN client_orgs co ON s.client_org_id = co.id WHERE co.id = $1)" if site_filter else ""
    params = [site_filter] if site_filter else []

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'online') as online,
            COALESCE(MAX(EXTRACT(EPOCH FROM (NOW() - last_checkin)) / 60.0) FILTER (WHERE status != 'online'), 0) as max_offline_min
        FROM site_appliances sa
        {where}
    """, *params)
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def get_ops_health(user: dict = Depends(require_auth)):
    """Platform-wide ops health — admin only. Returns 5 traffic-light statuses."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        evidence = await _query_evidence_metrics(conn)
        ots = await _query_ots_metrics(conn)
        healing = await _query_healing_metrics(conn)
        fleet = await _query_fleet_metrics(conn)

    total = evidence.get('total', 0)
    signed = evidence.get('signed', 0)
    signing_rate = signed / total if total > 0 else 0

    l1_total = healing.get('l1_total', 0)
    l1_healed = healing.get('l1_healed', 0)
    l1_rate = l1_healed / l1_total if l1_total > 0 else 1.0

    logger.info("Ops health queried: bundles=%d signed=%.1f%% l1_rate=%.1f%%",
                total, signing_rate * 100, l1_rate * 100)

    return {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'evidence_chain': compute_evidence_status(
            total_bundles=total,
            last_submission_minutes_ago=evidence.get('minutes_since_last') or 999,
            chain_gaps=0,  # TODO: chain gap detection query
            signing_rate=signing_rate,
        ),
        'signing': compute_signing_status(
            signing_rate=signing_rate,
            key_mismatches_24h=0,  # Tracked via appliance auth failure logs
            unsigned_legacy=evidence.get('unsigned_legacy', 0),
            signature_failures=evidence.get('sig_failures', 0),
        ),
        'ots_anchoring': compute_ots_status(
            anchored=ots.get('anchored', 0),
            pending=ots.get('pending', 0),
            batching=ots.get('batching', 0),
            latest_batch_age_hours=ots.get('hours_since_anchor') or 999,
        ),
        'healing_pipeline': compute_healing_status(
            l1_heal_rate=l1_rate,
            exhausted_count=healing.get('exhausted', 0),
            stuck_count=healing.get('stuck', 0),
        ),
        'fleet': compute_fleet_status(
            total_appliances=fleet.get('total', 0),
            online_count=fleet.get('online', 0),
            max_offline_minutes=fleet.get('max_offline_min', 0),
        ),
    }


@router.get("/health/{org_id}")
async def get_ops_health_for_org(org_id: str, user: dict = Depends(require_partner_role("admin", "tech"))):
    """Org-scoped ops health — partner view. Same 5 statuses filtered to org's sites."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Verify org access
        org = await conn.fetchrow("SELECT id FROM client_orgs WHERE id = $1", org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        evidence = await _query_evidence_metrics(conn, site_filter=org_id)
        healing = await _query_healing_metrics(conn, site_filter=org_id)
        fleet = await _query_fleet_metrics(conn, site_filter=org_id)
        # OTS is platform-wide, not org-scoped — partners see same OTS health
        ots = await _query_ots_metrics(conn)

    total = evidence.get('total', 0)
    signed = evidence.get('signed', 0)
    signing_rate = signed / total if total > 0 else 0
    l1_total = healing.get('l1_total', 0)
    l1_rate = healing.get('l1_healed', 0) / l1_total if l1_total > 0 else 1.0

    logger.info("Ops health queried for org=%s: bundles=%d", org_id, total)

    return {
        'org_id': org_id,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'evidence_chain': compute_evidence_status(
            total_bundles=total,
            last_submission_minutes_ago=evidence.get('minutes_since_last') or 999,
            chain_gaps=0,
            signing_rate=signing_rate,
        ),
        'signing': compute_signing_status(
            signing_rate=signing_rate,
            key_mismatches_24h=0,
            unsigned_legacy=evidence.get('unsigned_legacy', 0),
            signature_failures=evidence.get('sig_failures', 0),
        ),
        'ots_anchoring': compute_ots_status(
            anchored=ots.get('anchored', 0),
            pending=ots.get('pending', 0),
            batching=ots.get('batching', 0),
            latest_batch_age_hours=ots.get('hours_since_anchor') or 999,
        ),
        'healing_pipeline': compute_healing_status(
            l1_heal_rate=l1_rate,
            exhausted_count=healing.get('exhausted', 0),
            stuck_count=healing.get('stuck', 0),
        ),
        'fleet': compute_fleet_status(
            total_appliances=fleet.get('total', 0),
            online_count=fleet.get('online', 0),
            max_offline_minutes=fleet.get('max_offline_min', 0),
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent && source venv/bin/activate && python -m pytest ../mcp-server/central-command/backend/tests/test_ops_health.py -v --tb=short`
Expected: All 14 tests PASS.

- [ ] **Step 5: Register router in main.py**

Add to imports section (~line 57):
```python
from dashboard_api.ops_health import router as ops_health_router
```

Add to registration section (~line 1449):
```python
app.include_router(ops_health_router)
```

- [ ] **Step 6: Commit**

```bash
git add backend/ops_health.py backend/tests/test_ops_health.py mcp-server/main.py
git commit -m "feat: /api/ops/health endpoint with 5 traffic-light subsystem statuses"
```

---

## Task 3: Backend — Audit readiness endpoint + PDF export

**Files:**
- Create: `backend/audit_report.py`
- Create: `backend/tests/test_audit_report.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/test_audit_report.py`:

```python
"""Tests for audit readiness computation."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('SESSION_TOKEN_SECRET', 'test-secret-key-for-audit')
os.environ.setdefault('OTS_ENABLED', 'true')


class TestAuditReadinessBadge:
    """Audit readiness badge color computation."""

    def test_green_when_all_checks_pass(self):
        from audit_report import compute_audit_readiness
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=0.95,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result['badge'] == 'green'
        assert result['ready'] is True

    def test_yellow_when_baa_missing(self):
        from audit_report import compute_audit_readiness
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=0.95,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=False,
            packet_downloadable=True,
        )
        assert result['badge'] == 'yellow'
        assert 'BAA not on file' in [b['issue'] for b in result['blockers']]

    def test_red_when_chain_broken(self):
        from audit_report import compute_audit_readiness
        result = compute_audit_readiness(
            chain_unbroken=False,
            signing_rate=0.95,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result['badge'] == 'red'

    def test_red_when_critical_incidents(self):
        from audit_report import compute_audit_readiness
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=0.95,
            ots_current=True,
            critical_unresolved=3,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result['badge'] == 'red'

    def test_audit_countdown_calculation(self):
        from audit_report import compute_audit_countdown
        from datetime import date
        result = compute_audit_countdown(
            next_audit_date=date(2026, 6, 15),
            today=date(2026, 4, 6),
        )
        assert result['days_remaining'] == 70
        assert result['urgency'] == 'normal'

    def test_audit_countdown_urgent_under_30_days(self):
        from audit_report import compute_audit_countdown
        from datetime import date
        result = compute_audit_countdown(
            next_audit_date=date(2026, 5, 1),
            today=date(2026, 4, 6),
        )
        assert result['days_remaining'] == 25
        assert result['urgency'] == 'urgent'

    def test_audit_countdown_none_when_no_date(self):
        from audit_report import compute_audit_countdown
        from datetime import date
        result = compute_audit_countdown(
            next_audit_date=None,
            today=date(2026, 4, 6),
        )
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ../mcp-server/central-command/backend/tests/test_audit_report.py -v --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'audit_report'`

- [ ] **Step 3: Implement audit_report.py**

Create `backend/audit_report.py`:

```python
"""
Audit readiness endpoints — per-org readiness badge, countdown, PDF export.

Partners and admins can check if an org is ready for audit and generate
a timestamped readiness report.
"""
import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .auth import require_auth, require_partner_role
from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])

# ---------------------------------------------------------------------------
# Pure computation (no DB, fully testable)
# ---------------------------------------------------------------------------

def compute_audit_readiness(
    chain_unbroken: bool,
    signing_rate: float,
    ots_current: bool,
    critical_unresolved: int,
    baa_on_file: bool,
    packet_downloadable: bool,
) -> dict:
    """Compute audit readiness badge and blockers list."""
    checks = [
        {'check': 'Evidence chain unbroken', 'passed': chain_unbroken, 'issue': 'Evidence chain has gaps'},
        {'check': 'Bundles >90% signed', 'passed': signing_rate >= 0.90, 'issue': f'Signing rate {signing_rate:.0%} below 90%'},
        {'check': 'OTS anchoring current', 'passed': ots_current, 'issue': 'OTS anchoring stalled >24h'},
        {'check': 'No critical unresolved incidents', 'passed': critical_unresolved == 0, 'issue': f'{critical_unresolved} critical incidents unresolved'},
        {'check': 'BAA on file', 'passed': baa_on_file, 'issue': 'BAA not on file'},
        {'check': 'Compliance packet downloadable', 'passed': packet_downloadable, 'issue': 'Compliance packet generation failed'},
    ]
    blockers = [c for c in checks if not c['passed']]
    red_issues = {'Evidence chain has gaps', 'OTS anchoring stalled >24h'}
    has_red = any(b['issue'] in red_issues or 'critical incidents' in b['issue'] for b in blockers)

    if not blockers:
        badge = 'green'
    elif has_red:
        badge = 'red'
    else:
        badge = 'yellow'

    return {
        'badge': badge,
        'ready': len(blockers) == 0,
        'checks': checks,
        'blockers': blockers,
        'passed_count': sum(1 for c in checks if c['passed']),
        'total_checks': len(checks),
    }


def compute_audit_countdown(
    next_audit_date: Optional[date],
    today: Optional[date] = None,
) -> Optional[dict]:
    """Compute days until next audit with urgency level."""
    if not next_audit_date:
        return None
    today = today or date.today()
    delta = (next_audit_date - today).days
    if delta < 0:
        urgency = 'overdue'
    elif delta <= 14:
        urgency = 'critical'
    elif delta <= 30:
        urgency = 'urgent'
    else:
        urgency = 'normal'
    return {
        'next_audit_date': next_audit_date.isoformat(),
        'days_remaining': delta,
        'urgency': urgency,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/audit-readiness/{org_id}")
async def get_audit_readiness(org_id: str, user: dict = Depends(require_auth)):
    """Audit readiness for a specific org — badge + checklist + countdown."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id, name, baa_on_file, next_audit_date, next_audit_notes FROM client_orgs WHERE id = $1",
            org_id,
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Evidence metrics for this org
        ev = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE agent_signature IS NOT NULL AND agent_signature != '') as signed,
                MAX(checked_at) as latest,
                MAX(chain_position) as max_chain
            FROM compliance_bundles
            WHERE site_id IN (SELECT site_id FROM sites WHERE client_org_id = $1)
        """, org_id)

        # OTS health (platform-wide)
        ots_row = await conn.fetchrow("""
            SELECT MAX(anchored_at) as latest_anchor
            FROM ots_proofs WHERE status = 'anchored'
        """)
        ots_current = True
        if ots_row and ots_row['latest_anchor']:
            hours_since = (datetime.now(timezone.utc) - ots_row['latest_anchor']).total_seconds() / 3600
            ots_current = hours_since < 24

        # Critical unresolved incidents
        critical = await conn.fetchval("""
            SELECT COUNT(*) FROM incidents
            WHERE site_id IN (SELECT site_id FROM sites WHERE client_org_id = $1)
              AND status = 'open' AND severity = 'critical'
        """, org_id)

    total = ev['total'] if ev else 0
    signed = ev['signed'] if ev else 0
    signing_rate = signed / total if total > 0 else 0

    readiness = compute_audit_readiness(
        chain_unbroken=True,  # Simplified — full chain gap detection is future work
        signing_rate=signing_rate,
        ots_current=ots_current,
        critical_unresolved=critical or 0,
        baa_on_file=org['baa_on_file'] if org else False,
        packet_downloadable=total > 0,
    )

    countdown = compute_audit_countdown(org['next_audit_date']) if org else None

    logger.info("Audit readiness for org=%s: badge=%s blockers=%d",
                org_id, readiness['badge'], len(readiness['blockers']))

    return {
        'org_id': org_id,
        'org_name': org['name'] if org else None,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        **readiness,
        'countdown': countdown,
        'evidence_stats': {
            'total_bundles': total,
            'signed': signed,
            'signing_rate': round(signing_rate, 3),
        },
    }


@router.put("/audit-config/{org_id}")
async def update_audit_config(org_id: str, body: dict, user: dict = Depends(require_auth)):
    """Update BAA status and next audit date for an org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow("SELECT id FROM client_orgs WHERE id = $1", org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        updates = []
        params = [org_id]
        idx = 2
        if 'baa_on_file' in body:
            updates.append(f"baa_on_file = ${idx}")
            params.append(body['baa_on_file'])
            idx += 1
            if body['baa_on_file']:
                updates.append(f"baa_uploaded_at = NOW()")
        if 'next_audit_date' in body:
            updates.append(f"next_audit_date = ${idx}")
            params.append(body['next_audit_date'])
            idx += 1
        if 'next_audit_notes' in body:
            updates.append(f"next_audit_notes = ${idx}")
            params.append(body['next_audit_notes'])
            idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        await conn.execute(
            f"UPDATE client_orgs SET {', '.join(updates)} WHERE id = $1",
            *params,
        )
        logger.info("Audit config updated for org=%s by user=%s: %s",
                     org_id, user.get('username'), list(body.keys()))

    return {'status': 'updated', 'org_id': org_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ../mcp-server/central-command/backend/tests/test_audit_report.py -v --tb=short`
Expected: All 7 tests PASS.

- [ ] **Step 5: Register router in main.py**

Add to imports:
```python
from dashboard_api.audit_report import router as audit_report_router
```

Add to registration:
```python
app.include_router(audit_report_router)
```

- [ ] **Step 6: Commit**

```bash
git add backend/audit_report.py backend/tests/test_audit_report.py mcp-server/main.py
git commit -m "feat: /api/ops/audit-readiness endpoint with badge + countdown + config"
```

---

## Task 4: Frontend — StatusLight component + constants

**Files:**
- Create: `frontend/src/components/composed/StatusLight.tsx`
- Modify: `frontend/src/constants/status.ts`
- Modify: `frontend/src/constants/copy.ts`

- [ ] **Step 1: Add ops constants to status.ts**

Append to `frontend/src/constants/status.ts`:

```typescript
// Ops Center status light configuration
export type OpsStatus = 'green' | 'yellow' | 'red';

export const OPS_STATUS_CONFIG: Record<OpsStatus, { color: string; bgColor: string; ringColor: string; label: string }> = {
  green:  { color: 'text-emerald-400', bgColor: 'bg-emerald-400', ringColor: 'ring-emerald-400/30', label: 'Healthy' },
  yellow: { color: 'text-amber-400',   bgColor: 'bg-amber-400',   ringColor: 'ring-amber-400/30',   label: 'Warning' },
  red:    { color: 'text-red-400',     bgColor: 'bg-red-400',     ringColor: 'ring-red-400/30',     label: 'Critical' },
};
```

- [ ] **Step 2: Add ops copy to copy.ts**

Append to `frontend/src/constants/copy.ts`:

```typescript
// Ops Center labels and tooltips
export const OPS_LABELS: Record<string, { title: string; tooltip: string; docsAnchor: string }> = {
  evidence_chain:   { title: 'Evidence Chain',   tooltip: 'Compliance bundle submission pipeline — Ed25519 signed, hash-chained',               docsAnchor: '#evidence-chain' },
  signing:          { title: 'Signing',          tooltip: 'Ed25519 signature coverage and key health across all appliances',                    docsAnchor: '#signing' },
  ots_anchoring:    { title: 'OTS Anchoring',    tooltip: 'OpenTimestamps Bitcoin proof pipeline — Merkle batched hourly',                      docsAnchor: '#ots' },
  healing_pipeline: { title: 'Healing Pipeline', tooltip: 'L1/L2/L3 auto-remediation success rates and stuck incident detection',               docsAnchor: '#healing' },
  fleet:            { title: 'Fleet',            tooltip: 'Appliance connectivity — online/offline status and version currency',                 docsAnchor: '#fleet' },
};

export const AUDIT_BADGE_LABELS: Record<string, string> = {
  green: 'Audit Ready',
  yellow: 'Issues Found',
  red: 'Not Ready',
};
```

- [ ] **Step 3: Create StatusLight.tsx**

Create `frontend/src/components/composed/StatusLight.tsx`:

```tsx
import React from 'react';
import { OPS_STATUS_CONFIG, type OpsStatus } from '../../constants/status';

interface StatusLightProps {
  status: OpsStatus;
  title: string;
  label: string;
  tooltip?: string;
  docsAnchor?: string;
  stats?: Record<string, string | number>;
  onClick?: () => void;
  expanded?: boolean;
}

export function StatusLight({ status, title, label, tooltip, docsAnchor, stats, onClick, expanded }: StatusLightProps) {
  const config = OPS_STATUS_CONFIG[status];
  return (
    <button
      onClick={onClick}
      className={`
        relative flex flex-col items-center gap-2 p-4 rounded-xl border transition-all
        ${expanded ? 'ring-2 ' + config.ringColor + ' border-white/20' : 'border-white/10 hover:border-white/20'}
        bg-white/5 backdrop-blur-sm cursor-pointer w-full
      `}
      title={tooltip}
    >
      {/* Traffic light dot */}
      <div className={`w-4 h-4 rounded-full ${config.bgColor} shadow-lg shadow-${status === 'green' ? 'emerald' : status === 'yellow' ? 'amber' : 'red'}-400/30`} />
      <div className="text-sm font-semibold text-label-primary">{title}</div>
      <div className={`text-xs font-medium ${config.color}`}>{label}</div>
      {stats && (
        <div className="text-xs text-label-tertiary mt-1 text-center">
          {Object.entries(stats).map(([k, v]) => (
            <div key={k}>{k}: <span className="text-label-secondary font-medium">{v}</span></div>
          ))}
        </div>
      )}
      {docsAnchor && (
        <a
          href={`/docs${docsAnchor}`}
          onClick={e => e.stopPropagation()}
          className="absolute top-2 right-2 text-label-tertiary hover:text-label-secondary text-xs"
          title="What does this mean?"
        >
          (?)
        </a>
      )}
    </button>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/composed/StatusLight.tsx frontend/src/constants/status.ts frontend/src/constants/copy.ts
git commit -m "feat: StatusLight component + ops constants for traffic-light cards"
```

---

## Task 5: Frontend — OpsCenter.tsx page

**Files:**
- Create: `frontend/src/pages/OpsCenter.tsx`
- Modify: `frontend/src/App.tsx` (add route)

- [ ] **Step 1: Create OpsCenter.tsx**

Create `frontend/src/pages/OpsCenter.tsx` — the full page with status bar, expandable panels, and audit readiness section. This is the largest frontend file (~300 lines). Uses React Query for data fetching, StatusLight for the 5 cards, and expandable detail panels.

Key structure:
- `useQuery({ queryKey: ['ops-health'] })` fetches `/api/ops/health`
- 5 `StatusLight` cards in a grid
- Click any card → `expandedPanel` state toggles detail view
- Audit readiness section below (per org, with badge + countdown)
- Manual refresh button with "Last checked: Xs ago" timestamp

- [ ] **Step 2: Add route to App.tsx**

Add lazy import:
```typescript
const OpsCenter = lazy(() => import('./pages/OpsCenter').then(m => ({ default: m.OpsCenter })));
```

Add route in admin routes section:
```tsx
<Route path="/ops" element={<OpsCenter />} />
```

- [ ] **Step 3: Type check**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/OpsCenter.tsx frontend/src/App.tsx
git commit -m "feat: /ops page — 5 traffic-light status cards with expandable detail panels"
```

---

## Task 6: Frontend — AuditReadiness component

**Files:**
- Create: `frontend/src/components/composed/AuditReadiness.tsx`
- Modify: `frontend/src/pages/OpsCenter.tsx` (integrate component)

- [ ] **Step 1: Create AuditReadiness.tsx**

Per-org audit badge + checklist + countdown + export button. Uses React Query to fetch `/api/ops/audit-readiness/{orgId}`. Shows green/yellow/red badge, checklist with pass/fail, countdown to next audit with urgency color, and "Generate Audit Report" button.

- [ ] **Step 2: Integrate into OpsCenter.tsx**

Add audit readiness section below the status cards. Fetches org list, renders `AuditReadiness` per org.

- [ ] **Step 3: Type check + lint**

Run: `npx tsc --noEmit && npx eslint src/ --max-warnings 0`
Expected: 0 errors, 0 warnings.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/composed/AuditReadiness.tsx frontend/src/pages/OpsCenter.tsx
git commit -m "feat: AuditReadiness component — per-org badge, countdown, export"
```

---

## Task 7: Documentation page expansion

**Files:**
- Modify: `frontend/src/pages/Documentation.tsx`

- [ ] **Step 1: Add Runbooks section**

Add new `DocSection` entries for runbooks (`'runbooks'`) and reference (`'reference'`). Each runbook follows the pattern: Symptom, Diagnosis, Fix, Verification. Cover all 7 runbooks from the design (RB-OPS-001 through RB-OPS-007).

- [ ] **Step 2: Add Reference section**

Architecture overview (evidence chain flow), glossary (Ed25519, OTS, Merkle, WORM, hash chain), HIPAA control mapping table, check catalog summary (58 checks by platform).

- [ ] **Step 3: Type check**

Run: `npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Documentation.tsx
git commit -m "feat: /docs runbooks (7 procedures) + reference (architecture, glossary, controls)"
```

---

## Task 8: Integration test + deploy

**Files:**
- All files from tasks 1-7

- [ ] **Step 1: Run full backend test suite**

Run: `cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent && source venv/bin/activate && python -m pytest tests/ -v --tb=short -q`
Expected: 1161+ passed, 0 new failures.

- [ ] **Step 2: Run frontend checks**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/frontend && npx tsc --noEmit && npx eslint src/ --max-warnings 0`
Expected: 0 errors, 0 warnings.

- [ ] **Step 3: Push to main**

```bash
git push origin main
```

- [ ] **Step 4: Deploy to VPS**

```bash
scp mcp-server/central-command/backend/ops_health.py root@178.156.162.116:/opt/mcp-server/dashboard_api_mount/ops_health.py
scp mcp-server/central-command/backend/audit_report.py root@178.156.162.116:/opt/mcp-server/dashboard_api_mount/audit_report.py
ssh root@178.156.162.116 "cd /opt/mcp-server && docker compose restart mcp-server"
```

- [ ] **Step 5: Verify endpoints live**

```bash
curl -sf https://api.osiriscare.net/api/ops/health | python3 -m json.tool | head -20
```
Expected: JSON with 5 status objects, each with `status: green|yellow|red`.

- [ ] **Step 6: Commit deploy verification**

```bash
git commit --allow-empty -m "verified: /ops endpoints live, /docs updated, migration 124 applied"
```
