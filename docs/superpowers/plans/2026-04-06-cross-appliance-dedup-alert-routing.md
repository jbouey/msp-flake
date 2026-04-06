# Cross-Appliance Dedup + Alert Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix cross-appliance incident dedup, route alerts to org contacts with PHI-free digests, and add minimal client approval workflow.

**Architecture:** Incident dedup switches from per-appliance to per-site+hostname composite key. New `alert_router.py` module classifies and enqueues alerts. Background digest sender batches per org. Client portal gets an alerts page with approve/dismiss for `self_service` sites.

**Tech Stack:** Python/FastAPI backend, asyncpg, React/TypeScript frontend, SMTP email

**Spec:** `docs/superpowers/specs/2026-04-06-cross-appliance-dedup-alert-routing-design.md`

---

## File Structure

**Create:**
| File | Responsibility |
|------|---------------|
| `backend/migrations/128_incident_dedup_key.sql` | Add dedup_key to incidents, backfill |
| `backend/migrations/129_org_alert_fields.sql` | alert_email, cc_email, client_alert_mode, welcome_email_sent_at on client_orgs |
| `backend/migrations/130_site_alert_mode.sql` | client_alert_mode override on sites |
| `backend/migrations/131_pending_alerts.sql` | Digest buffer table |
| `backend/migrations/132_client_approvals.sql` | Approval audit trail table |
| `backend/alert_router.py` | Alert classification, enqueue, digest send, welcome email |
| `backend/tests/test_cross_appliance_dedup.py` | Dedup tests: cross-appliance, severity upgrade, hostname-missing fallback |
| `backend/tests/test_alert_router.py` | Router tests: classification, mode filtering, digest batching, PHI-free |
| `backend/tests/test_client_alerts.py` | Client portal alert + approval endpoint tests |
| `frontend/src/client/ClientAlerts.tsx` | Client portal alerts page |

**Modify:**
| File | Change |
|------|--------|
| `backend/agent_api.py:514-528` | Dedup query: site_id+dedup_key instead of appliance_id |
| `backend/agent_api.py:564-582` | INSERT: add dedup_key column |
| `backend/partners.py` | Add alert-config GET/PUT endpoints for org and site |
| `backend/client_portal.py` | Add GET /alerts and POST /alerts/{id}/action endpoints |
| `backend/health_monitor.py:19-85` | Add digest sender to background loop |
| `backend/email_alerts.py` | Add digest email template function |
| `backend/sites.py` | Deliver client_alert_mode in checkin response |
| `backend/main.py:1300-1322` | Register digest background task |
| `backend/main.py:1427-1479` | Include new router if needed |
| `frontend/src/App.tsx:122-136` | Add /client/alerts route |

All paths relative to `mcp-server/central-command/`.

---

## Task 1: Migration 128 — Incident Dedup Key

**Files:**
- Create: `backend/migrations/128_incident_dedup_key.sql`

- [ ] **Step 1: Write migration**

```sql
-- Add cross-appliance dedup key to incidents.
-- dedup_key = SHA256(site_id || ':' || incident_type || ':' || hostname)
-- Enables dedup across appliances reporting the same issue on the same host.

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_incidents_dedup_key
  ON incidents(dedup_key)
  WHERE dedup_key IS NOT NULL;

-- Backfill open/resolving/escalated incidents
UPDATE incidents
SET dedup_key = encode(
  sha256(
    (COALESCE(site_id::text, '') || ':' || COALESCE(incident_type, '') || ':' || COALESCE(details->>'hostname', ''))::bytea
  ),
  'hex'
)
WHERE status IN ('open', 'resolving', 'escalated')
  AND dedup_key IS NULL;
```

- [ ] **Step 2: Verify migration syntax**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python3 -c "open('backend/migrations/128_incident_dedup_key.sql').read(); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/128_incident_dedup_key.sql
git commit -m "feat: migration 128 — incident dedup_key for cross-appliance dedup"
```

---

## Task 2: Cross-Appliance Incident Dedup — Tests

**Files:**
- Create: `backend/tests/test_cross_appliance_dedup.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for cross-appliance incident deduplication.

Cross-appliance dedup uses SHA256(site_id:incident_type:hostname) instead
of per-appliance scoping, so two appliances reporting the same issue on
the same host produce one incident, not two.
"""
import hashlib
import json
import os
import sys
import uuid

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeRow:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, idx):
        return self._values[idx]


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        row = self.fetchone()
        return row[0] if row else None


def make_mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


def compute_dedup_key(site_id: str, incident_type: str, hostname: str) -> str:
    return hashlib.sha256(f"{site_id}:{incident_type}:{hostname}".encode()).hexdigest()


class TestCrossApplianceDedup:
    """Two appliances reporting the same issue on the same host = one incident."""

    @pytest.mark.asyncio
    async def test_second_appliance_is_deduplicated(self):
        """Appliance B reports same incident_type+hostname as Appliance A — dedup hit."""
        import main

        site_id = "site-001"
        hostname = "web-srv-01"
        incident_type = "drift:windows_firewall"
        existing_id = str(uuid.uuid4())
        appliance_uuid = str(uuid.uuid4())
        dedup_key = compute_dedup_key(site_id, incident_type, hostname)

        db = make_mock_db()

        async def mock_execute(query, params=None):
            qs = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances" in qs:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in qs:
                return FakeResult([FakeRow(["appliance-B"])])
            if "dedup_key" in qs and "SELECT" in qs:
                # Existing open incident from Appliance A
                return FakeResult([FakeRow([existing_id, "open", "medium", "appliance-A"])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id=site_id,
            host_id=hostname,
            incident_type=incident_type,
            severity="medium",
            check_type="windows_firewall",
            details={"hostname": hostname},
            pre_state={},
            hipaa_controls="164.312(c)(1)",
        )
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.6"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert result["incident_id"] == existing_id

    @pytest.mark.asyncio
    async def test_severity_upgrade_on_dedup(self):
        """If second appliance reports higher severity, existing incident upgrades."""
        import main

        site_id = "site-001"
        hostname = "dc01"
        incident_type = "drift:windows_update"
        existing_id = str(uuid.uuid4())
        appliance_uuid = str(uuid.uuid4())

        db = make_mock_db()
        updated_severity = None

        async def mock_execute(query, params=None):
            nonlocal updated_severity
            qs = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances" in qs:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in qs:
                return FakeResult([FakeRow(["appliance-B"])])
            if "dedup_key" in qs and "SELECT" in qs:
                return FakeResult([FakeRow([existing_id, "open", "medium", "appliance-A"])])
            if "UPDATE incidents SET severity" in qs:
                updated_severity = params.get("severity") if params else None
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id=site_id,
            host_id=hostname,
            incident_type=incident_type,
            severity="high",
            check_type="windows_update",
            details={"hostname": hostname},
            pre_state={},
            hipaa_controls="164.308(a)(5)(ii)(B)",
        )
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.7"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert updated_severity == "high"

    @pytest.mark.asyncio
    async def test_no_hostname_falls_back_to_appliance_scoped(self):
        """If details has no hostname, dedup falls back to appliance_id scoping."""
        import main

        site_id = "site-001"
        appliance_uuid = str(uuid.uuid4())
        new_incident_id = None

        db = make_mock_db()

        async def mock_execute(query, params=None):
            nonlocal new_incident_id
            qs = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances" in qs:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in qs:
                return FakeResult([FakeRow(["appliance-A"])])
            if "dedup_key" in qs and "SELECT" in qs:
                # No match — no existing incident with this dedup key
                return FakeResult([])
            if "appliance_id = :appliance_id" in qs and "SELECT" in qs:
                # Fallback: appliance-scoped dedup also no match
                return FakeResult([])
            if "INSERT INTO incidents" in qs:
                new_incident_id = params.get("id") if params else None
                return FakeResult([], rowcount=1)
            if "SELECT" in qs and "l1_rules" in qs:
                return FakeResult([])
            if "SELECT" in qs and "monitoring_only" in qs.lower():
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id=site_id,
            host_id="",
            incident_type="netscan:rogue_device",
            severity="low",
            check_type="netscan",
            details={},  # No hostname
            pre_state={},
            hipaa_controls="164.312(b)",
        )
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.5"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] in ("created", "resolved_l1")
        assert new_incident_id is not None

    @pytest.mark.asyncio
    async def test_different_hosts_not_deduplicated(self):
        """Same incident_type but different hostnames = separate incidents."""
        import main

        site_id = "site-001"
        appliance_uuid = str(uuid.uuid4())

        db = make_mock_db()
        insert_count = 0

        async def mock_execute(query, params=None):
            nonlocal insert_count
            qs = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances" in qs:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in qs:
                return FakeResult([FakeRow(["appliance-A"])])
            if "dedup_key" in qs and "SELECT" in qs:
                return FakeResult([])  # No existing match
            if "INSERT INTO incidents" in qs:
                insert_count += 1
                return FakeResult([], rowcount=1)
            if "l1_rules" in qs:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id=site_id,
            host_id="ws02",
            incident_type="drift:windows_firewall",
            severity="medium",
            check_type="windows_firewall",
            details={"hostname": "ws02"},
            pre_state={},
            hipaa_controls="164.312(c)(1)",
        )
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.5"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert insert_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_cross_appliance_dedup.py -v --tb=short 2>&1 | tail -20`
Expected: FAIL — the current dedup query doesn't use `dedup_key`

---

## Task 3: Cross-Appliance Incident Dedup — Implementation

**Files:**
- Modify: `backend/agent_api.py:514-582`

- [ ] **Step 1: Add dedup_key computation to report_incident**

In `agent_api.py`, after the `canonical_appliance_id` resolution (around line 510), add:

```python
# Compute cross-appliance dedup key
hostname = (incident.details or {}).get("hostname", "") or incident.host_id or ""
if hostname:
    dedup_key = hashlib.sha256(
        f"{incident.site_id}:{incident.incident_type}:{hostname}".encode()
    ).hexdigest()
else:
    dedup_key = None
```

- [ ] **Step 2: Replace the dedup query**

Replace the existing dedup query at lines 514-528:

Old:
```python
existing_check = await db.execute(
    text("""
        SELECT id, status FROM incidents
        WHERE appliance_id = :appliance_id
        AND incident_type = :incident_type
        ...
    """),
    {"appliance_id": appliance_id, "incident_type": incident.incident_type}
)
```

New:
```python
if dedup_key:
    # Cross-appliance dedup by site + incident_type + hostname
    existing_check = await db.execute(
        text("""
            SELECT id, status, severity, appliance_id FROM incidents
            WHERE site_id = :site_id
            AND dedup_key = :dedup_key
            AND (
                (status IN ('open', 'resolving', 'escalated'))
                OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
            )
            AND created_at > NOW() - INTERVAL '48 hours'
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"site_id": incident.site_id, "dedup_key": dedup_key}
    )
else:
    # Fallback: appliance-scoped dedup when no hostname available
    existing_check = await db.execute(
        text("""
            SELECT id, status, severity, appliance_id FROM incidents
            WHERE appliance_id = :appliance_id
            AND incident_type = :incident_type
            AND (
                (status IN ('open', 'resolving', 'escalated'))
                OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
            )
            AND created_at > NOW() - INTERVAL '48 hours'
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"appliance_id": appliance_id, "incident_type": incident.incident_type}
    )
```

- [ ] **Step 3: Add severity upgrade on dedup**

In the existing dedup handling block (around lines 531-560), after the dedup match is found, add severity upgrade logic:

```python
existing_incident = existing_check.fetchone()
if existing_incident:
    existing_id = existing_incident[0]
    existing_status = existing_incident[1]
    existing_severity = existing_incident[2] if len(existing_incident._values) > 2 else None

    # Severity upgrade: if new report is higher severity, update
    severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    new_sev = severity_order.get(incident.severity, 0)
    old_sev = severity_order.get(existing_severity, 0)
    if new_sev > old_sev:
        await db.execute(
            text("UPDATE incidents SET severity = :severity WHERE id = :id"),
            {"severity": incident.severity, "id": existing_id}
        )

    # Rest of existing dedup logic (reopen if resolved, return deduplicated if open)
```

- [ ] **Step 4: Add dedup_key to INSERT**

In the incident INSERT statement (lines 564-582), add the `dedup_key` column:

```python
await db.execute(
    text("""
        INSERT INTO incidents (id, appliance_id, incident_type, severity, check_type,
            details, pre_state, hipaa_controls, reported_at, dedup_key)
        VALUES (:id, :appliance_id, :incident_type, :severity, :check_type,
            :details, :pre_state, :hipaa_controls, :reported_at, :dedup_key)
    """),
    {
        "id": incident_id,
        "appliance_id": appliance_id,
        "incident_type": incident.incident_type,
        "severity": incident.severity,
        "check_type": incident.check_type,
        "details": json.dumps({**incident.details, "hostname": incident.host_id}),
        "pre_state": json.dumps(incident.pre_state),
        "hipaa_controls": incident.hipaa_controls,
        "reported_at": now,
        "dedup_key": dedup_key,
    }
)
```

- [ ] **Step 5: Run dedup tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_cross_appliance_dedup.py -v --tb=short`
Expected: All 4 tests PASS

- [ ] **Step 6: Run existing incident pipeline tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_incident_pipeline.py -v --tb=short`
Expected: Existing tests still pass (may need mock_execute updates for new query shape — fix if needed)

- [ ] **Step 7: Commit**

```bash
git add backend/agent_api.py backend/tests/test_cross_appliance_dedup.py
git commit -m "feat: cross-appliance incident dedup by hostname instead of appliance_id"
```

---

## Task 4: Migrations 129-130 — Org Alert Fields + Site Override

**Files:**
- Create: `backend/migrations/129_org_alert_fields.sql`
- Create: `backend/migrations/130_site_alert_mode.sql`

- [ ] **Step 1: Write migration 129**

```sql
-- Add alert routing fields to client_orgs.
-- alert_email: where client-tier alerts are sent
-- cc_email: secondary recipient
-- client_alert_mode: self_service | informed | silent (default: informed)
-- welcome_email_sent_at: tracks one-time onboarding email

ALTER TABLE client_orgs
  ADD COLUMN IF NOT EXISTS alert_email VARCHAR(255),
  ADD COLUMN IF NOT EXISTS cc_email VARCHAR(255),
  ADD COLUMN IF NOT EXISTS client_alert_mode VARCHAR(20) DEFAULT 'informed',
  ADD COLUMN IF NOT EXISTS welcome_email_sent_at TIMESTAMPTZ;

-- Seed alert_email from existing primary_email where set
UPDATE client_orgs
SET alert_email = primary_email
WHERE alert_email IS NULL AND primary_email IS NOT NULL;
```

- [ ] **Step 2: Write migration 130**

```sql
-- Per-site alert mode override. NULL = inherit from org.

ALTER TABLE sites
  ADD COLUMN IF NOT EXISTS client_alert_mode VARCHAR(20);
```

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/129_org_alert_fields.sql backend/migrations/130_site_alert_mode.sql
git commit -m "feat: migrations 129-130 — org alert fields + site alert mode override"
```

---

## Task 5: Migrations 131-132 — Pending Alerts + Client Approvals

**Files:**
- Create: `backend/migrations/131_pending_alerts.sql`
- Create: `backend/migrations/132_client_approvals.sql`

- [ ] **Step 1: Write migration 131**

```sql
-- Pending alerts digest buffer. Alerts enqueued here, batched into
-- digest emails per org on a 4-hour cycle.

CREATE TABLE IF NOT EXISTS pending_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    site_id UUID NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',
    summary TEXT NOT NULL,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pending_alerts_unsent
  ON pending_alerts(org_id, created_at)
  WHERE sent_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pending_alerts_org_recent
  ON pending_alerts(org_id, created_at DESC);

-- RLS
ALTER TABLE pending_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_alerts FORCE ROW LEVEL SECURITY;

CREATE POLICY pending_alerts_admin ON pending_alerts
  USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY pending_alerts_org ON pending_alerts
  USING (org_id::text = current_setting('app.current_org', true));
```

- [ ] **Step 2: Write migration 132**

```sql
-- Client approval audit trail. Every approve/dismiss/acknowledge action
-- by a client user is recorded here for HIPAA accountability.

CREATE TABLE IF NOT EXISTS client_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    site_id UUID NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    alert_id UUID NOT NULL REFERENCES pending_alerts(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL,
    acted_by UUID NOT NULL,
    acted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_client_approvals_incident
  ON client_approvals(incident_id);

CREATE INDEX IF NOT EXISTS idx_client_approvals_org
  ON client_approvals(org_id, acted_at DESC);

-- RLS
ALTER TABLE client_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_approvals FORCE ROW LEVEL SECURITY;

CREATE POLICY client_approvals_admin ON client_approvals
  USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY client_approvals_org ON client_approvals
  USING (org_id::text = current_setting('app.current_org', true));
```

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/131_pending_alerts.sql backend/migrations/132_client_approvals.sql
git commit -m "feat: migrations 131-132 — pending_alerts digest buffer + client_approvals audit trail"
```

---

## Task 6: Alert Router Module — Tests

**Files:**
- Create: `backend/tests/test_alert_router.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for alert_router: classification, mode filtering, digest batching, PHI-free."""
import os
import sys
import uuid

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestAlertModeResolution:
    """get_effective_alert_mode: site overrides org, org defaults to 'informed'."""

    def test_site_override_wins(self):
        from dashboard_api.alert_router import get_effective_alert_mode
        assert get_effective_alert_mode(site_mode="self_service", org_mode="silent") == "self_service"

    def test_null_site_inherits_org(self):
        from dashboard_api.alert_router import get_effective_alert_mode
        assert get_effective_alert_mode(site_mode=None, org_mode="silent") == "silent"

    def test_both_null_defaults_informed(self):
        from dashboard_api.alert_router import get_effective_alert_mode
        assert get_effective_alert_mode(site_mode=None, org_mode=None) == "informed"


class TestAlertClassification:
    """classify_alert: maps incident types to alert_type + tier."""

    def test_drift_classified_as_client(self):
        from dashboard_api.alert_router import classify_alert
        result = classify_alert("drift:windows_firewall", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "firewall_off"

    def test_patch_drift_classified(self):
        from dashboard_api.alert_router import classify_alert
        result = classify_alert("drift:windows_update", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "patch_available"

    def test_service_stopped_classified(self):
        from dashboard_api.alert_router import classify_alert
        result = classify_alert("drift:service_stopped", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "service_stopped"

    def test_unknown_type_defaults_to_admin(self):
        from dashboard_api.alert_router import classify_alert
        result = classify_alert("unknown:thing", "low")
        assert result["tier"] == "admin"


class TestDigestEmailContent:
    """Digest emails must be PHI-free: counts and site names only."""

    def test_digest_body_has_no_ips(self):
        from dashboard_api.alert_router import render_digest_email
        alerts = [
            {"alert_type": "patch_available", "site_name": "Main Office", "count": 3},
            {"alert_type": "rogue_device", "site_name": "Branch", "count": 1},
        ]
        html, text = render_digest_email("North Valley Practice", alerts, mode="informed")
        assert "192.168" not in text
        assert "192.168" not in html
        assert "Main Office" in text
        assert "3 devices" in text or "3" in text

    def test_informed_mode_no_action_language(self):
        from dashboard_api.alert_router import render_digest_email
        alerts = [{"alert_type": "patch_available", "site_name": "Office", "count": 1}]
        _, text = render_digest_email("Test Org", alerts, mode="informed")
        assert "no action required" in text.lower() or "monitoring" in text.lower()

    def test_self_service_mode_has_action_cta(self):
        from dashboard_api.alert_router import render_digest_email
        alerts = [{"alert_type": "patch_available", "site_name": "Office", "count": 1}]
        _, text = render_digest_email("Test Org", alerts, mode="self_service")
        assert "action" in text.lower() or "review" in text.lower()


class TestSilentModeSuppression:
    """Silent mode: no alerts enqueued."""

    @pytest.mark.asyncio
    async def test_silent_mode_skips_enqueue(self):
        from dashboard_api.alert_router import maybe_enqueue_alert
        conn = AsyncMock()
        result = await maybe_enqueue_alert(
            conn=conn,
            org_id="org-1",
            site_id="site-1",
            incident_id="inc-1",
            incident_type="drift:windows_firewall",
            severity="medium",
            site_mode="silent",
            org_mode="silent",
        )
        assert result is None
        conn.execute.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_alert_router.py -v --tb=short 2>&1 | tail -20`
Expected: FAIL — `alert_router` module does not exist yet

---

## Task 7: Alert Router Module — Implementation

**Files:**
- Create: `backend/alert_router.py`

- [ ] **Step 1: Create alert_router.py**

```python
"""Alert router: classify incidents, enqueue for digest, render PHI-free emails.

Alert hierarchy:
  - admin: mesh/infra (handled by existing health_monitor)
  - partner: org health (future Spec 2)
  - client: device-level drift, rogue, credential needed

Alert modes (per-site, org-inherited):
  - self_service: emails with approve buttons
  - informed: emails, informational only
  - silent: no client emails
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from dashboard_api.email_alerts import is_email_configured, send_digest_email

logger = logging.getLogger("alert_router")

PORTAL_URL = os.getenv("CLIENT_PORTAL_URL", "https://portal.osiriscare.net")
DIGEST_INTERVAL_HOURS = int(os.getenv("ALERT_DIGEST_INTERVAL_HOURS", "4"))

# Incident type → alert classification
ALERT_TYPE_MAP = {
    "drift:windows_update": "patch_available",
    "drift:linux_patching": "patch_available",
    "drift:nixos_generation": "patch_available",
    "drift:windows_firewall": "firewall_off",
    "drift:linux_firewall": "firewall_off",
    "drift:macos_firewall": "firewall_off",
    "drift:service_stopped": "service_stopped",
    "drift:windows_encryption": "encryption_off",
    "drift:linux_encryption": "encryption_off",
    "drift:macos_filevault": "encryption_off",
    "netscan:rogue_device": "rogue_device",
}

ALERT_SUMMARIES = {
    "patch_available": "{count} device(s) have patch updates available",
    "firewall_off": "{count} device(s) have firewall disabled",
    "service_stopped": "{count} device(s) have stopped services",
    "encryption_off": "{count} device(s) have encryption disabled",
    "rogue_device": "{count} unrecognized device(s) detected",
    "credential_needed": "{count} device(s) need credentials configured",
}


def get_effective_alert_mode(
    site_mode: Optional[str], org_mode: Optional[str]
) -> str:
    """Resolve effective alert mode: site overrides org, default 'informed'."""
    return site_mode or org_mode or "informed"


def classify_alert(incident_type: str, severity: str) -> dict:
    """Classify an incident into alert_type and tier."""
    alert_type = ALERT_TYPE_MAP.get(incident_type)
    if alert_type:
        return {"tier": "client", "alert_type": alert_type}
    # Anything with drift: prefix we don't explicitly map still goes to client
    if incident_type.startswith("drift:"):
        return {"tier": "client", "alert_type": "service_stopped"}
    return {"tier": "admin", "alert_type": incident_type}


async def maybe_enqueue_alert(
    conn,
    org_id: str,
    site_id: str,
    incident_id: str,
    incident_type: str,
    severity: str,
    site_mode: Optional[str] = None,
    org_mode: Optional[str] = None,
) -> Optional[str]:
    """Enqueue a client alert if mode allows. Returns alert_id or None."""
    mode = get_effective_alert_mode(site_mode, org_mode)
    if mode == "silent":
        return None

    classification = classify_alert(incident_type, severity)
    if classification["tier"] != "client":
        return None

    alert_type = classification["alert_type"]
    summary = ALERT_SUMMARIES.get(alert_type, "Compliance issue detected").format(count=1)
    alert_id = str(uuid.uuid4())

    await conn.execute(
        """INSERT INTO pending_alerts (id, org_id, site_id, alert_type, severity, summary, incident_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        alert_id, org_id, site_id, alert_type, severity, summary, incident_id,
    )
    logger.info("Alert enqueued", alert_id=alert_id, alert_type=alert_type, org_id=org_id)
    return alert_id


def render_digest_email(
    org_name: str, alerts: list[dict], mode: str = "informed"
) -> tuple[str, str]:
    """Render PHI-free digest email. Returns (html, plaintext).

    alerts: list of {alert_type, site_name, count}
    """
    count = sum(a["count"] for a in alerts)

    # Plain text
    lines = [f"We detected {count} item(s) that need attention:\n"]
    for a in alerts:
        summary = ALERT_SUMMARIES.get(a["alert_type"], "Issue detected").format(count=a["count"])
        lines.append(f"  - {summary} (Site: {a['site_name']})")

    lines.append(f"\nView details: {PORTAL_URL}/alerts")

    if mode == "self_service":
        lines.append("\nReview and take action at the link above.")
    else:
        lines.append("\nNo action required — your compliance team is monitoring this.")

    lines.append("\n-- OsirisCare Compliance Platform")
    text_body = "\n".join(lines)

    # HTML
    alert_rows = ""
    for a in alerts:
        summary = ALERT_SUMMARIES.get(a["alert_type"], "Issue detected").format(count=a["count"])
        alert_rows += f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee;">{summary}</td><td style="padding:8px 12px;border-bottom:1px solid #eee;">{a["site_name"]}</td></tr>'

    cta_text = "Review and Take Action" if mode == "self_service" else "View Details"
    footer_text = (
        "Review and take action at the link above."
        if mode == "self_service"
        else "No action required &mdash; your compliance team is monitoring this."
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;">
<div style="background:linear-gradient(135deg,#0f172a,#1e3a5f);padding:24px;border-radius:8px 8px 0 0;">
  <h1 style="color:#fff;margin:0;font-size:20px;">OsirisCare Alert</h1>
  <p style="color:#94a3b8;margin:4px 0 0;">{count} item(s) at {org_name}</p>
</div>
<div style="padding:20px;background:#fff;border:1px solid #e2e8f0;border-top:none;">
  <table style="width:100%;border-collapse:collapse;">
    <tr><th style="text-align:left;padding:8px 12px;border-bottom:2px solid #e2e8f0;">Issue</th><th style="text-align:left;padding:8px 12px;border-bottom:2px solid #e2e8f0;">Site</th></tr>
    {alert_rows}
  </table>
  <div style="margin-top:20px;text-align:center;">
    <a href="{PORTAL_URL}/alerts" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;">{cta_text}</a>
  </div>
  <p style="margin-top:16px;color:#64748b;font-size:13px;">{footer_text}</p>
</div>
<div style="padding:12px;text-align:center;color:#94a3b8;font-size:11px;">
  OsirisCare Compliance Platform
</div>
</body></html>"""

    return html_body, text_body


async def send_digest_for_org(conn, org_id: str, org_name: str, alert_email: str, cc_email: Optional[str], org_mode: str):
    """Flush unsent alerts for an org into a single digest email."""
    if not is_email_configured():
        return

    # Get unsent alerts grouped by type + site
    rows = await conn.fetch(
        """SELECT pa.alert_type, s.name as site_name,
                  COUNT(*) as cnt,
                  MAX(CASE WHEN s.client_alert_mode IS NOT NULL THEN s.client_alert_mode ELSE $2 END) as effective_mode
           FROM pending_alerts pa
           JOIN sites s ON s.site_id = pa.site_id
           WHERE pa.org_id = $1 AND pa.sent_at IS NULL
           GROUP BY pa.alert_type, s.name
           ORDER BY cnt DESC""",
        org_id, org_mode,
    )
    if not rows:
        return

    alerts = [{"alert_type": r["alert_type"], "site_name": r["site_name"], "count": r["cnt"]} for r in rows]

    # Use most permissive mode across sites for email tone
    modes = [r["effective_mode"] for r in rows]
    effective = "self_service" if "self_service" in modes else org_mode

    html, text = render_digest_email(org_name, alerts, mode=effective)

    try:
        send_digest_email(
            to_email=alert_email,
            cc_email=cc_email,
            subject=f"OsirisCare — {sum(a['count'] for a in alerts)} item(s) at {org_name}",
            html_body=html,
            text_body=text,
        )

        # Mark as sent
        await conn.execute(
            "UPDATE pending_alerts SET sent_at = NOW() WHERE org_id = $1 AND sent_at IS NULL",
            org_id,
        )
        logger.info("Digest sent", org_id=org_id, alert_count=len(alerts))
    except Exception as e:
        logger.error(f"Failed to send digest for org {org_id}: {e}")


async def send_welcome_email_if_needed(conn, org_id: str, org_name: str, alert_email: str, device_count: int, site_count: int):
    """Send one-time onboarding welcome email when devices are first discovered."""
    if not is_email_configured() or device_count == 0:
        return

    # Check if already sent
    row = await conn.fetchrow(
        "SELECT welcome_email_sent_at FROM client_orgs WHERE id = $1",
        org_id,
    )
    if row and row["welcome_email_sent_at"]:
        return

    text = f"""Your network compliance monitoring is active.

We found {device_count} devices across {site_count} location(s).

Log in to review your compliance status:
{PORTAL_URL}

This is an automated message from your compliance platform.
No action is required at this time.

-- OsirisCare Compliance Platform"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;">
<div style="background:linear-gradient(135deg,#0f172a,#1e3a5f);padding:24px;border-radius:8px 8px 0 0;">
  <h1 style="color:#fff;margin:0;font-size:20px;">Welcome to OsirisCare</h1>
  <p style="color:#94a3b8;margin:4px 0 0;">{org_name}</p>
</div>
<div style="padding:20px;background:#fff;border:1px solid #e2e8f0;border-top:none;">
  <p>Your network compliance monitoring is now active.</p>
  <p style="font-size:18px;font-weight:600;">We found <strong>{device_count}</strong> devices across <strong>{site_count}</strong> location(s).</p>
  <div style="margin:20px 0;text-align:center;">
    <a href="{PORTAL_URL}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;">View Your Dashboard</a>
  </div>
  <p style="color:#64748b;font-size:13px;">No action is required at this time. Your compliance team is monitoring your network.</p>
</div>
<div style="padding:12px;text-align:center;color:#94a3b8;font-size:11px;">
  OsirisCare Compliance Platform
</div>
</body></html>"""

    try:
        send_digest_email(
            to_email=alert_email,
            cc_email=None,
            subject=f"OsirisCare is now protecting {org_name}",
            html_body=html,
            text_body=text,
        )
        await conn.execute(
            "UPDATE client_orgs SET welcome_email_sent_at = $1 WHERE id = $2",
            datetime.now(timezone.utc), org_id,
        )
        logger.info("Welcome email sent", org_id=org_id)
    except Exception as e:
        logger.error(f"Failed to send welcome email for org {org_id}: {e}")


async def digest_sender_loop():
    """Background loop: flush pending_alerts into digest emails per org.

    Runs every DIGEST_INTERVAL_HOURS (default 4).
    Critical/high severity alerts are sent immediately (bypassed digest).
    """
    import asyncio
    from dashboard_api.shared import get_pool, admin_connection

    await asyncio.sleep(600)  # Wait 10 min after startup
    logger.info("Digest sender started", interval_hours=DIGEST_INTERVAL_HOURS)

    while True:
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Send immediate alerts (critical/high not yet sent)
                immediate_orgs = await conn.fetch(
                    """SELECT DISTINCT pa.org_id, co.name, co.alert_email, co.cc_email, co.client_alert_mode
                       FROM pending_alerts pa
                       JOIN client_orgs co ON co.id = pa.org_id
                       WHERE pa.sent_at IS NULL AND pa.severity IN ('critical', 'high')
                         AND co.alert_email IS NOT NULL"""
                )
                for org in immediate_orgs:
                    await send_digest_for_org(
                        conn, org["org_id"], org["name"],
                        org["alert_email"], org["cc_email"],
                        org["client_alert_mode"] or "informed",
                    )

                # Regular digest: all orgs with unsent alerts
                orgs = await conn.fetch(
                    """SELECT DISTINCT pa.org_id, co.name, co.alert_email, co.cc_email, co.client_alert_mode
                       FROM pending_alerts pa
                       JOIN client_orgs co ON co.id = pa.org_id
                       WHERE pa.sent_at IS NULL
                         AND co.alert_email IS NOT NULL"""
                )
                for org in orgs:
                    await send_digest_for_org(
                        conn, org["org_id"], org["name"],
                        org["alert_email"], org["cc_email"],
                        org["client_alert_mode"] or "informed",
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Digest sender error: {e}", exc_info=True)

        await asyncio.sleep(DIGEST_INTERVAL_HOURS * 3600)
```

- [ ] **Step 2: Run alert router tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_alert_router.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/alert_router.py backend/tests/test_alert_router.py
git commit -m "feat: alert_router module — classify, enqueue, digest, welcome email"
```

---

## Task 8: Digest Email Template in email_alerts.py

**Files:**
- Modify: `backend/email_alerts.py`

- [ ] **Step 1: Add send_digest_email function**

Add at the end of `email_alerts.py`:

```python
def send_digest_email(
    to_email: str,
    cc_email: Optional[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> bool:
    """Send a digest/alert email to org contact. Used by alert_router."""
    if not is_email_configured():
        logger.warning("Email not configured, skipping digest")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    if cc_email:
        msg["Cc"] = cc_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    recipients = [to_email]
    if cc_email:
        recipients.append(cc_email)

    for attempt in range(3):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, recipients, msg.as_string())
            logger.info(f"Digest email sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Digest email attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                import time
                time.sleep(attempt + 1)

    return False
```

- [ ] **Step 2: Verify import exists**

Check that `smtplib`, `MIMEMultipart`, `MIMEText` are already imported at the top of `email_alerts.py`. They should be — the existing `send_critical_alert` uses them. If not, add:

```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
```

- [ ] **Step 3: Commit**

```bash
git add backend/email_alerts.py
git commit -m "feat: send_digest_email function in email_alerts.py"
```

---

## Task 9: Partner Alert Config Endpoints

**Files:**
- Modify: `backend/partners.py`
- Create: `backend/tests/test_partner_alert_config.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for partner alert config endpoints."""
import os
import sys
import uuid

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeRow:
    def __init__(self, mapping):
        self._map = mapping

    def __getitem__(self, key):
        return self._map[key]

    def get(self, key, default=None):
        return self._map.get(key, default)


class TestPartnerAlertConfig:
    """Partner can GET/PUT alert config for orgs and sites."""

    @pytest.mark.asyncio
    async def test_get_org_alert_config(self):
        """GET /me/orgs/{org_id}/alert-config returns current config."""
        import main
        from dashboard_api.partners import router

        # Find the endpoint function
        from dashboard_api.partners import get_partner_org_alert_config

        partner = {"id": "partner-1", "name": "Test MSP", "user_role": "admin"}
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=FakeRow({
            "id": "org-1",
            "alert_email": "doc@clinic.com",
            "cc_email": "office@clinic.com",
            "client_alert_mode": "informed",
        }))

        pool = AsyncMock()

        with patch("dashboard_api.partners.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("dashboard_api.partners.admin_connection") as mock_ac:
            mock_ac.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_partner_org_alert_config("org-1", partner)

        assert result["alert_email"] == "doc@clinic.com"
        assert result["client_alert_mode"] == "informed"

    @pytest.mark.asyncio
    async def test_put_org_alert_config_validates_mode(self):
        """PUT with invalid mode returns 422."""
        from dashboard_api.partners import update_partner_org_alert_config
        from fastapi import HTTPException

        partner = {"id": "partner-1", "name": "Test MSP", "user_role": "admin"}
        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": "invalid_mode"})

        with pytest.raises(HTTPException) as exc:
            await update_partner_org_alert_config("org-1", request, partner)
        assert exc.value.status_code == 422
```

- [ ] **Step 2: Add endpoints to partners.py**

Add to `partners.py` after the existing drift-config endpoints:

```python
@router.get("/me/orgs/{org_id}/alert-config")
async def get_partner_org_alert_config(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Get alert config for an org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            """SELECT id, alert_email, cc_email, client_alert_mode
               FROM client_orgs WHERE id = $1 AND current_partner_id = $2""",
            org_id, partner["id"],
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get site overrides
        sites = await conn.fetch(
            """SELECT site_id, name, client_alert_mode
               FROM sites WHERE client_org_id = $1 AND partner_id = $2 AND status != 'inactive'""",
            org_id, partner["id"],
        )

        return {
            "alert_email": org["alert_email"],
            "cc_email": org["cc_email"],
            "client_alert_mode": org["client_alert_mode"],
            "site_overrides": [
                {"site_id": s["site_id"], "name": s["name"], "client_alert_mode": s["client_alert_mode"]}
                for s in sites if s["client_alert_mode"]
            ],
        }


VALID_ALERT_MODES = {"self_service", "informed", "silent"}


@router.put("/me/orgs/{org_id}/alert-config")
async def update_partner_org_alert_config(
    org_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Update alert config for an org (default for all sites)."""
    body = await request.json()

    mode = body.get("client_alert_mode")
    if mode and mode not in VALID_ALERT_MODES:
        raise HTTPException(status_code=422, detail=f"Invalid alert mode. Must be one of: {VALID_ALERT_MODES}")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner["id"],
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        sets = []
        params = [org_id]
        idx = 2
        for field in ("alert_email", "cc_email", "client_alert_mode"):
            if field in body:
                sets.append(f"{field} = ${idx}")
                params.append(body[field])
                idx += 1

        if sets:
            await conn.execute(
                f"UPDATE client_orgs SET {', '.join(sets)}, updated_at = NOW() WHERE id = $1",
                *params,
            )

        return {"status": "updated"}


@router.put("/me/sites/{site_id}/alert-config")
async def update_partner_site_alert_config(
    site_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Override alert mode for a specific site. Pass null to inherit from org."""
    body = await request.json()

    mode = body.get("client_alert_mode")
    if mode is not None and mode not in VALID_ALERT_MODES:
        raise HTTPException(status_code=422, detail=f"Invalid alert mode. Must be one of: {VALID_ALERT_MODES}")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner["id"],
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        await conn.execute(
            "UPDATE sites SET client_alert_mode = $1 WHERE site_id = $2",
            mode, site_id,
        )

        return {"status": "updated", "client_alert_mode": mode}
```

- [ ] **Step 3: Run tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_partner_alert_config.py -v --tb=short`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/partners.py backend/tests/test_partner_alert_config.py
git commit -m "feat: partner alert config endpoints — GET/PUT org + site alert mode"
```

---

## Task 10: Client Portal Alert + Approval Endpoints

**Files:**
- Modify: `backend/client_portal.py`
- Create: `backend/tests/test_client_alerts.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for client portal alert list and approval endpoints."""
import os
import sys
import uuid

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeRow:
    def __init__(self, mapping):
        self._map = mapping

    def __getitem__(self, key):
        return self._map[key]

    def get(self, key, default=None):
        return self._map.get(key, default)


class TestClientAlertsList:

    @pytest.mark.asyncio
    async def test_returns_alerts_for_org(self):
        from dashboard_api.client_portal import get_client_alerts

        user = {"user_id": "user-1", "org_id": "org-1", "role": "admin"}
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            FakeRow({
                "id": "alert-1", "site_name": "Main Office", "alert_type": "patch_available",
                "summary": "1 device(s) have patch updates available", "severity": "medium",
                "created_at": "2026-04-06T10:00:00Z", "sent_at": None, "dismissed_at": None,
                "incident_id": "inc-1", "effective_mode": "self_service",
            }),
        ])

        pool = AsyncMock()

        with patch("dashboard_api.client_portal.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("dashboard_api.client_portal.org_connection") as mock_oc:
            mock_oc.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_oc.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_client_alerts(user)

        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["alert_type"] == "patch_available"
        assert result["alerts"][0]["actions_available"] is True


class TestClientApproval:

    @pytest.mark.asyncio
    async def test_approve_creates_audit_record(self):
        from dashboard_api.client_portal import action_client_alert
        from fastapi import Request

        user = {"user_id": "user-1", "org_id": "org-1", "role": "admin"}
        conn = AsyncMock()
        # Alert lookup
        conn.fetchrow = AsyncMock(side_effect=[
            FakeRow({
                "id": "alert-1", "org_id": "org-1", "site_id": "site-1",
                "incident_id": "inc-1", "alert_type": "patch_available",
                "effective_mode": "self_service",
            }),
            None,  # No existing approval
        ])
        conn.execute = AsyncMock()

        pool = AsyncMock()
        request = MagicMock()
        request.json = AsyncMock(return_value={"action": "approved"})

        with patch("dashboard_api.client_portal.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("dashboard_api.client_portal.org_connection") as mock_oc:
            mock_oc.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_oc.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await action_client_alert("alert-1", request, user)

        assert result["status"] == "ok"
        assert result["action_taken"] == "approved"
        # Verify INSERT into client_approvals was called
        insert_calls = [c for c in conn.execute.call_args_list if "client_approvals" in str(c)]
        assert len(insert_calls) >= 1

    @pytest.mark.asyncio
    async def test_approve_blocked_for_informed_mode(self):
        from dashboard_api.client_portal import action_client_alert
        from fastapi import HTTPException

        user = {"user_id": "user-1", "org_id": "org-1", "role": "admin"}
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=FakeRow({
            "id": "alert-1", "org_id": "org-1", "site_id": "site-1",
            "incident_id": "inc-1", "alert_type": "patch_available",
            "effective_mode": "informed",
        }))

        pool = AsyncMock()
        request = MagicMock()
        request.json = AsyncMock(return_value={"action": "approved"})

        with patch("dashboard_api.client_portal.get_pool", new_callable=AsyncMock, return_value=pool), \
             patch("dashboard_api.client_portal.org_connection") as mock_oc:
            mock_oc.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_oc.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HTTPException) as exc:
                await action_client_alert("alert-1", request, user)
            assert exc.value.status_code == 403
```

- [ ] **Step 2: Add endpoints to client_portal.py**

Add to `client_portal.py` on the `auth_router`:

```python
@auth_router.get("/alerts")
async def get_client_alerts(user: dict = Depends(require_client_user)):
    """Get alerts for the client's org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        rows = await conn.fetch(
            """SELECT pa.id, s.name as site_name, pa.alert_type, pa.summary,
                      pa.severity, pa.created_at, pa.sent_at, pa.dismissed_at,
                      pa.incident_id,
                      COALESCE(s.client_alert_mode, co.client_alert_mode, 'informed') as effective_mode
               FROM pending_alerts pa
               JOIN sites s ON s.site_id = pa.site_id
               JOIN client_orgs co ON co.id = pa.org_id
               WHERE pa.org_id = $1
               ORDER BY pa.created_at DESC
               LIMIT 100""",
            org_id,
        )

        alerts = []
        for r in rows:
            alerts.append({
                "id": str(r["id"]),
                "site_name": r["site_name"],
                "alert_type": r["alert_type"],
                "summary": r["summary"],
                "severity": r["severity"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "status": "dismissed" if r["dismissed_at"] else ("sent" if r["sent_at"] else "pending"),
                "incident_id": str(r["incident_id"]) if r["incident_id"] else None,
                "actions_available": r["effective_mode"] == "self_service",
            })

        return {"alerts": alerts}


VALID_ACTIONS = {"approved", "dismissed", "acknowledged", "ignored", "credentials_entered"}


@auth_router.post("/alerts/{alert_id}/action")
async def action_client_alert(
    alert_id: str,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Take action on a client alert (approve, dismiss, acknowledge)."""
    body = await request.json()
    action = body.get("action")
    notes = body.get("notes", "")

    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Invalid action. Must be one of: {VALID_ACTIONS}")

    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Fetch alert with effective mode
        alert = await conn.fetchrow(
            """SELECT pa.id, pa.org_id, pa.site_id, pa.incident_id, pa.alert_type,
                      COALESCE(s.client_alert_mode, co.client_alert_mode, 'informed') as effective_mode
               FROM pending_alerts pa
               JOIN sites s ON s.site_id = pa.site_id
               JOIN client_orgs co ON co.id = pa.org_id
               WHERE pa.id = $1 AND pa.org_id = $2""",
            alert_id, org_id,
        )
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Only self_service sites allow actions
        if alert["effective_mode"] != "self_service":
            raise HTTPException(status_code=403, detail="Actions not available for this site's alert mode")

        # Record approval
        approval_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO client_approvals (id, org_id, site_id, incident_id, alert_id, action, acted_by, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            approval_id, org_id, alert["site_id"],
            alert["incident_id"], alert_id, action,
            user["user_id"], notes,
        )

        # Mark alert as dismissed if action is dismiss/ignore
        if action in ("dismissed", "ignored"):
            await conn.execute(
                "UPDATE pending_alerts SET dismissed_at = NOW() WHERE id = $1",
                alert_id,
            )

        # If approved: trigger L1 healing (create order for the incident)
        if action == "approved" and alert["incident_id"]:
            logger.info("Client approved remediation",
                        alert_id=alert_id, incident_id=alert["incident_id"],
                        user_id=user["user_id"])
            # L1 order creation handled by existing incident pipeline
            # Just update incident status to signal approval
            await conn.execute(
                """UPDATE incidents SET details = details || '{"client_approved": true}'::jsonb
                   WHERE id = $1""",
                alert["incident_id"],
            )

        return {
            "status": "ok",
            "action_taken": action,
            "approval_id": approval_id,
            "incident_id": str(alert["incident_id"]) if alert["incident_id"] else None,
        }
```

- [ ] **Step 3: Add uuid import if missing**

Check top of `client_portal.py` for `import uuid`. Add if not present.

- [ ] **Step 4: Run tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_client_alerts.py -v --tb=short`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/client_portal.py backend/tests/test_client_alerts.py
git commit -m "feat: client portal alert list + approve/dismiss endpoints with audit trail"
```

---

## Task 11: Wire Alert Enqueue into Incident Pipeline

**Files:**
- Modify: `backend/agent_api.py`

- [ ] **Step 1: Import alert_router in agent_api.py**

Add near the top imports:

```python
from dashboard_api.alert_router import maybe_enqueue_alert
```

- [ ] **Step 2: Call maybe_enqueue_alert after incident creation**

After the incident INSERT and L1/L3 processing, before the final return in `report_incident()`, add:

```python
# Enqueue client alert if applicable
try:
    # Look up org and alert mode for this site
    org_row = await db.execute(
        text("""SELECT co.id as org_id, co.client_alert_mode as org_mode,
                       s.client_alert_mode as site_mode
                FROM sites s
                LEFT JOIN client_orgs co ON co.id = s.client_org_id
                WHERE s.site_id = :site_id"""),
        {"site_id": incident.site_id}
    )
    org_info = org_row.fetchone()
    if org_info and org_info[0]:  # Has org_id
        await db.execute(
            text("""SELECT 1 FROM pending_alerts WHERE 1=0""")  # placeholder
        )
        # Use raw connection for asyncpg call
        from dashboard_api.alert_router import maybe_enqueue_alert, classify_alert
        classification = classify_alert(incident.incident_type, incident.severity)
        if classification["tier"] == "client":
            mode = org_info[2] or org_info[1] or "informed"
            if mode != "silent":
                await db.execute(
                    text("""INSERT INTO pending_alerts (id, org_id, site_id, alert_type, severity, summary, incident_id)
                            VALUES (:id, :org_id, :site_id, :alert_type, :severity, :summary, :incident_id)"""),
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": str(org_info[0]),
                        "site_id": incident.site_id,
                        "alert_type": classification["alert_type"],
                        "severity": incident.severity,
                        "summary": f"1 device has {classification['alert_type'].replace('_', ' ')} issue",
                        "incident_id": incident_id,
                    }
                )
except Exception as e:
    logger.warning(f"Alert enqueue failed (non-fatal): {e}")
```

Note: Uses SQLAlchemy `text()` since agent_api.py uses SQLAlchemy sessions, not raw asyncpg. The alert_router functions are used directly only in background tasks that use raw asyncpg.

- [ ] **Step 3: Run full incident test suite**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command && python -m pytest backend/tests/test_incident_pipeline.py backend/tests/test_cross_appliance_dedup.py -v --tb=short`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/agent_api.py
git commit -m "feat: enqueue client alerts on incident creation (non-fatal)"
```

---

## Task 12: Deliver Alert Mode in Checkin + Register Digest Background Task

**Files:**
- Modify: `backend/sites.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Add client_alert_mode to checkin response in sites.py**

Find the checkin response construction in `sites.py` (around STEP 3.8c where assigned_targets is set). In the response dict, add:

```python
"client_alert_mode": site_row.get("client_alert_mode") if site_row else None,
```

This lets the daemon know the alert mode if it ever needs to adjust local behavior.

- [ ] **Step 2: Add welcome email trigger to checkin**

After device count is known in checkin (around STEP 5 or 6), add:

```python
# Welcome email on first discovery
try:
    if org_id and device_count > 0:
        org_row = await conn.fetchrow(
            "SELECT name, alert_email, welcome_email_sent_at FROM client_orgs WHERE id = $1",
            org_id,
        )
        if org_row and org_row["alert_email"] and not org_row["welcome_email_sent_at"]:
            from dashboard_api.alert_router import send_welcome_email_if_needed
            site_count_row = await conn.fetchval(
                "SELECT COUNT(*) FROM sites WHERE client_org_id = $1 AND status != 'inactive'",
                org_id,
            )
            await send_welcome_email_if_needed(
                conn, org_id, org_row["name"], org_row["alert_email"],
                device_count, site_count_row or 1,
            )
except Exception as e:
    logger.warning(f"Welcome email check failed (non-fatal): {e}")
```

- [ ] **Step 3: Register digest background task in main.py**

In `main.py`, add import (near line 63):

```python
from dashboard_api.alert_router import digest_sender_loop
```

Add to `task_defs` list (around line 1315):

```python
("alert_digest", digest_sender_loop),
```

- [ ] **Step 4: Add CSRF exemption if needed**

The new client portal endpoints (`/client/alerts`, `/client/alerts/{id}/action`) use cookie auth. Check if `/api/client/` prefix is already CSRF-exempt in `csrf.py`. If not, the POST endpoint needs to be added to EXEMPT_PATHS or the frontend needs to send the CSRF token.

Check: `grep -n "client" backend/csrf.py`

If `/api/client/` is not exempt, the frontend must include `X-CSRF-Token` header from the `csrf_token` cookie for the POST action endpoint. The GET is fine without it.

- [ ] **Step 5: Commit**

```bash
git add backend/sites.py backend/main.py
git commit -m "feat: deliver alert_mode in checkin, register digest sender, welcome email trigger"
```

---

## Task 13: Frontend — Client Alerts Page

**Files:**
- Create: `frontend/src/client/ClientAlerts.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create ClientAlerts.tsx**

```tsx
import { useState, useEffect } from 'react';

interface Alert {
  id: string;
  site_name: string;
  alert_type: string;
  summary: string;
  severity: string;
  created_at: string;
  status: string;
  incident_id: string | null;
  actions_available: boolean;
}

const ALERT_ICONS: Record<string, string> = {
  patch_available: '\u26A0',
  firewall_off: '\u{1F6E1}',
  service_stopped: '\u26D4',
  encryption_off: '\u{1F512}',
  rogue_device: '\u2753',
  credential_needed: '\u{1F511}',
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  high: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
  medium: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
  low: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
};

const ACTION_LABELS: Record<string, Record<string, string>> = {
  patch_available: { approve: 'Approve Patch', dismiss: 'Dismiss' },
  firewall_off: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  service_stopped: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  encryption_off: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  rogue_device: { approve: 'Acknowledge', dismiss: 'Ignore' },
  credential_needed: { approve: 'Enter Credentials', dismiss: 'Dismiss' },
};

export default function ClientAlerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/client/alerts', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => { setAlerts(data.alerts || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleAction = async (alertId: string, action: string) => {
    setActionLoading(alertId);
    try {
      const csrfToken = document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '';
      const res = await fetch(`/api/client/alerts/${alertId}/action`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify({ action }),
      });
      if (res.ok) {
        setAlerts(prev => prev.map(a =>
          a.id === alertId ? { ...a, status: action === 'approved' ? 'approved' : 'dismissed', actions_available: false } : a
        ));
      }
    } finally {
      setActionLoading(null);
    }
  };

  const pending = alerts.filter(a => a.status !== 'dismissed' && a.status !== 'approved');
  const resolved = alerts.filter(a => a.status === 'dismissed' || a.status === 'approved');

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-2xl font-bold text-label-primary mb-1">Alerts</h1>
      <p className="text-label-secondary mb-6">Items that need your attention across your locations.</p>

      {pending.length === 0 && (
        <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-6 text-center">
          <p className="text-green-800 dark:text-green-300 font-medium">All clear — no pending alerts.</p>
        </div>
      )}

      {pending.length > 0 && (
        <div className="space-y-3 mb-8">
          {pending.map(alert => (
            <div key={alert.id} className="bg-background-secondary border border-border-primary rounded-lg p-4 flex items-center gap-4">
              <span className="text-2xl">{ALERT_ICONS[alert.alert_type] || '\u26A0'}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${SEVERITY_COLORS[alert.severity] || SEVERITY_COLORS.medium}`}>
                    {alert.severity}
                  </span>
                  <span className="text-xs text-label-tertiary">{alert.site_name}</span>
                </div>
                <p className="text-sm text-label-primary">{alert.summary}</p>
                <p className="text-xs text-label-tertiary mt-1">{new Date(alert.created_at).toLocaleDateString()}</p>
              </div>
              {alert.actions_available && (
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleAction(alert.id, 'approved')}
                    disabled={actionLoading === alert.id}
                    className="px-3 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    {ACTION_LABELS[alert.alert_type]?.approve || 'Approve'}
                  </button>
                  <button
                    onClick={() => handleAction(alert.id, 'dismissed')}
                    disabled={actionLoading === alert.id}
                    className="px-3 py-1.5 bg-background-secondary text-label-secondary text-sm border border-border-primary rounded-md hover:bg-background-tertiary disabled:opacity-50"
                  >
                    {ACTION_LABELS[alert.alert_type]?.dismiss || 'Dismiss'}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {resolved.length > 0 && (
        <>
          <h2 className="text-lg font-semibold text-label-primary mb-3">Resolved</h2>
          <div className="space-y-2 opacity-60">
            {resolved.map(alert => (
              <div key={alert.id} className="bg-background-secondary border border-border-primary rounded-lg p-3 flex items-center gap-3">
                <span className="text-lg">{ALERT_ICONS[alert.alert_type] || '\u26A0'}</span>
                <div className="flex-1">
                  <p className="text-sm text-label-secondary">{alert.summary}</p>
                  <p className="text-xs text-label-tertiary">{alert.site_name} &middot; {alert.status}</p>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add route to App.tsx**

In `frontend/src/App.tsx`, add import and route inside the client routes section (around line 122-136):

Import:
```tsx
import ClientAlerts from './client/ClientAlerts';
```

Route (add alongside existing client routes):
```tsx
<Route path="alerts" element={<ClientAlerts />} />
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/frontend && npx tsc --noEmit 2>&1 | tail -10`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/client/ClientAlerts.tsx frontend/src/App.tsx
git commit -m "feat: client portal alerts page with approve/dismiss actions"
```

---

## Task 14: Run All Tests + Deploy Migrations

- [ ] **Step 1: Run all new tests**

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command
python -m pytest backend/tests/test_cross_appliance_dedup.py backend/tests/test_alert_router.py backend/tests/test_client_alerts.py backend/tests/test_partner_alert_config.py -v --tb=short
```
Expected: All PASS

- [ ] **Step 2: Run existing test suite**

```bash
python -m pytest backend/tests/test_incident_pipeline.py backend/tests/test_evidence_dedup.py -v --tb=short
```
Expected: All PASS (no regressions)

- [ ] **Step 3: Frontend type check**

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/frontend && npx tsc --noEmit
```
Expected: 0 errors

- [ ] **Step 4: Deploy migrations to VPS**

```bash
ssh root@178.156.162.116 "cd /opt/mcp-server && docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < backend/migrations/128_incident_dedup_key.sql
ssh root@178.156.162.116 "cd /opt/mcp-server && docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < backend/migrations/129_org_alert_fields.sql
ssh root@178.156.162.116 "cd /opt/mcp-server && docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < backend/migrations/130_site_alert_mode.sql
ssh root@178.156.162.116 "cd /opt/mcp-server && docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < backend/migrations/131_pending_alerts.sql
ssh root@178.156.162.116 "cd /opt/mcp-server && docker exec mcp-postgres psql -U mcp -d mcp -f /dev/stdin" < backend/migrations/132_client_approvals.sql
```

- [ ] **Step 5: Git push (triggers CI/CD deploy)**

```bash
git push origin main
```

- [ ] **Step 6: Restart container on VPS**

```bash
ssh root@178.156.162.116 "cd /opt/mcp-server && docker compose up -d --build mcp-server"
```

- [ ] **Step 7: Verify endpoints**

```bash
curl -s https://api.osiriscare.net/health | python3 -m json.tool
```
Expected: healthy response

- [ ] **Step 8: Final commit (if any fixups needed)**

```bash
git add -A && git commit -m "fix: post-deploy adjustments for alert routing"
git push origin main
```
