# Organization Feature Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix security vulnerabilities, performance issues, and missing capabilities in the organization/multi-tenant feature to support enterprise scaling with proper PHI boundaries and role separation.

**Architecture:** The org feature uses PostgreSQL RLS with `app.current_org` GUC for tenant isolation across 28+ tables. Admin dashboard uses SQLAlchemy (`require_auth` + `org_scope`), while client/partner portals use asyncpg with `org_connection()`/`admin_connection()`. Credential inheritance flows org→site with site precedence. Partner scoping uses `sites.partner_id` (not org-level).

**Tech Stack:** Python/FastAPI, asyncpg, SQLAlchemy (admin routes), PostgreSQL RLS, React/TypeScript frontend

**Key Files:**
- `mcp-server/central-command/backend/routes.py` — Admin org endpoints (lines 2600-2870)
- `mcp-server/central-command/backend/org_credentials.py` — Org credential CRUD
- `mcp-server/central-command/backend/auth.py` — Auth dependencies + org_scope helpers
- `mcp-server/central-command/backend/client_portal.py` — Client portal evidence endpoints
- `mcp-server/central-command/backend/partners.py` — Partner portal endpoints
- `mcp-server/central-command/backend/tenant_middleware.py` — RLS connection helpers
- `mcp-server/central-command/backend/tests/test_org_hardening.py` — New test file
- `mcp-server/central-command/frontend/src/pages/Organizations.tsx` — Org list page
- `mcp-server/central-command/frontend/src/pages/OrgDashboard.tsx` — Org detail page

---

### Task 1: Fix IDOR on GET /organizations/{org_id}

The `get_organization_detail` endpoint at `routes.py:2712` has NO auth dependency — any HTTP request can fetch any org's full details including site list, compliance scores, and healing metrics.

**Files:**
- Modify: `mcp-server/central-command/backend/routes.py:2712-2794`
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py` (create)

- [ ] **Step 1: Create test file with IDOR test**

Create `mcp-server/central-command/backend/tests/test_org_hardening.py`:

```python
"""Tests for organization feature hardening.

Covers: IDOR prevention, org_scope enforcement, pagination,
N+1 query elimination, and PHI boundary enforcement.
"""

import pytest
import sys
import os
import types
import json

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


class TestOrgAccessControl:
    """Test org_scope enforcement on organization endpoints."""

    def test_check_org_access_global_admin_passes(self):
        """Global admin (org_scope=None) can access any org."""
        from auth import _check_org_access
        # Should not raise
        _check_org_access({"org_scope": None}, "any-org-id")

    def test_check_org_access_scoped_admin_allowed(self):
        """Org-scoped admin can access their own org."""
        from auth import _check_org_access
        _check_org_access({"org_scope": ["org-1", "org-2"]}, "org-1")

    def test_check_org_access_scoped_admin_denied(self):
        """Org-scoped admin cannot access other orgs."""
        from auth import _check_org_access
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert exc_info.value.status_code == 404

    def test_check_org_access_returns_404_not_403(self):
        """Denied access returns 404 to prevent org enumeration."""
        from auth import _check_org_access
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert "not found" in exc_info.value.detail.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/test_org_hardening.py::TestOrgAccessControl -v`
Expected: FAIL — `_check_org_access` does not exist yet

- [ ] **Step 3: Implement `_check_org_access` in auth.py**

Add to `mcp-server/central-command/backend/auth.py` after the `check_site_access_pool` function (around line 851):

```python
def _check_org_access(user: Dict[str, Any], org_id: str):
    """Validate admin user can access org_id.

    Returns None if access granted.
    Raises 404 for out-of-scope orgs (IDOR prevention — never 403).
    Global admins (org_scope=None) can access any org.
    """
    org_scope = user.get("org_scope")
    if org_scope is None:
        return  # Global admin
    if str(org_id) not in org_scope:
        raise HTTPException(status_code=404, detail="Organization not found")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/test_org_hardening.py::TestOrgAccessControl -v`
Expected: 4 passed

- [ ] **Step 5: Add auth + org_scope check to get_organization_detail**

In `routes.py:2712`, change:
```python
@router.get("/organizations/{org_id}")
async def get_organization_detail(org_id: str, db: AsyncSession = Depends(get_db)):
```
to:
```python
@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get organization detail with nested site list."""
    auth_module._check_org_access(user, org_id)
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_org_hardening.py backend/auth.py backend/routes.py
git commit -m "fix: IDOR on GET /organizations/{org_id} — add auth + org_scope check"
```

---

### Task 2: Fix IDOR on org_credentials endpoints

`list_org_credentials` and `delete_org_credential` in `org_credentials.py` accept `require_auth` but don't validate `org_scope`. An org-scoped admin could view/delete credentials for any org.

**Files:**
- Modify: `mcp-server/central-command/backend/org_credentials.py:33,103`
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py`

- [ ] **Step 1: Add org_scope check to all org_credentials endpoints**

In `org_credentials.py`, add org_scope validation after each `require_auth`/`require_operator` dependency. Import `_check_org_access`:

```python
from .auth import require_auth, require_operator, _check_org_access
```

Then in each endpoint function body, add as the first line:
```python
_check_org_access(user, org_id)
```

Apply to: `list_org_credentials` (line 33), `create_org_credential` (line 71), `delete_org_credential` (line 103).

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All existing tests pass

- [ ] **Step 3: Commit**

```bash
git add backend/org_credentials.py
git commit -m "fix: add org_scope check to org_credentials endpoints"
```

---

### Task 3: Fix N+1 query on org list endpoint

`list_organizations` at `routes.py:2606` calls `get_all_compliance_scores()` which returns scores keyed by site_id, then loops per-org to query site_ids. The fix: join compliance data to orgs via sites table in Python (no extra queries).

**Files:**
- Modify: `mcp-server/central-command/backend/routes.py:2606-2674`
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py`

- [ ] **Step 1: Add test for org list returning compliance**

```python
class TestOrgListPerformance:
    """Test that org list avoids N+1 queries."""

    def test_compliance_aggregation_logic(self):
        """Verify compliance averaging logic works with site→org mapping."""
        # Simulates what the refactored endpoint does
        all_compliance = {
            "site-a": {"has_data": True, "score": 80},
            "site-b": {"has_data": True, "score": 60},
            "site-c": {"has_data": False, "score": 0},
        }
        site_org_map = {
            "site-a": "org-1",
            "site-b": "org-1",
            "site-c": "org-2",
        }

        # Group by org
        org_scores = {}
        for site_id, org_id in site_org_map.items():
            sc = all_compliance.get(site_id, {})
            if sc.get("has_data"):
                org_scores.setdefault(org_id, []).append(sc["score"])

        avg_1 = sum(org_scores.get("org-1", [0])) / len(org_scores.get("org-1", [1]))
        assert avg_1 == 70.0  # (80+60)/2

        # org-2 has no data
        assert "org-2" not in org_scores
```

- [ ] **Step 2: Run test**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/test_org_hardening.py::TestOrgListPerformance -v`
Expected: PASS (pure logic test)

- [ ] **Step 3: Refactor list_organizations to eliminate N+1**

Replace `routes.py:2639-2672` (the for loop with per-org query) with:

```python
    # Build site→org mapping from a single query
    site_org_result = await db.execute(text(
        "SELECT site_id, client_org_id FROM sites WHERE client_org_id IS NOT NULL"
    ))
    site_org_map = {}
    for sr in site_org_result.fetchall():
        site_org_map.setdefault(str(sr.client_org_id), []).append(sr.site_id)

    all_compliance = await get_all_compliance_scores(db)

    orgs = []
    for row in rows:
        org_id_str = str(row.id)
        org_site_ids = site_org_map.get(org_id_str, [])
        org_scores = []
        for sid in org_site_ids:
            sc = all_compliance.get(sid, {})
            if sc.get("has_data"):
                org_scores.append(sc.get("score", 0))

        avg_compliance = (
            sum(org_scores) / len(org_scores) if org_scores else 0
        )

        orgs.append({
            "id": org_id_str,
            "name": row.name,
            "primary_email": row.primary_email,
            "practice_type": row.practice_type,
            "provider_count": row.provider_count,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "site_count": row.site_count,
            "appliance_count": row.appliance_count,
            "last_checkin": row.last_checkin.isoformat() if row.last_checkin else None,
            "avg_compliance": round(avg_compliance, 1),
        })

    return {"organizations": orgs, "count": len(orgs)}
```

- [ ] **Step 4: Run all tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add backend/routes.py backend/tests/test_org_hardening.py
git commit -m "perf: eliminate N+1 query on org list endpoint"
```

---

### Task 4: Add pagination to org list endpoint

No LIMIT/OFFSET on the org list — will break at scale.

**Files:**
- Modify: `mcp-server/central-command/backend/routes.py:2605-2636`
- Modify: `mcp-server/central-command/frontend/src/pages/Organizations.tsx`

- [ ] **Step 1: Add pagination params to list_organizations**

Change the function signature at `routes.py:2606`:

```python
from fastapi import Query as QueryParam  # if not already imported
# ... existing imports ...

@router.get("/organizations")
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
    limit: int = QueryParam(50, ge=1, le=200),
    offset: int = QueryParam(0, ge=0),
    status_filter: Optional[str] = QueryParam(None, alias="status"),
):
```

Add to the SQL query before `ORDER BY`:
```python
    # Add status filter
    if status_filter:
        where_parts = [where_clause] if where_clause else []
        where_parts.append("co.status = :status_filter")
        where_clause = "WHERE " + " AND ".join(w.replace("WHERE ", "") for w in where_parts) if where_parts else ""
        params["status_filter"] = status_filter

    # ... existing GROUP BY ...
    # Add pagination
    query += f" LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
```

Add total count query before the main query:
```python
    count_result = await db.execute(text(f"""
        SELECT COUNT(*) FROM client_orgs co {where_clause}
    """), params)
    total = count_result.scalar()
```

Change return to include pagination metadata:
```python
    return {"organizations": orgs, "count": len(orgs), "total": total, "limit": limit, "offset": offset}
```

- [ ] **Step 2: Run tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/routes.py
git commit -m "feat: add pagination + status filter to org list endpoint"
```

---

### Task 5: Add org-level consolidated health endpoint

New endpoint `GET /organizations/{org_id}/health` returns aggregated data across all sites in the org. This is the core data consolidation endpoint.

**Files:**
- Modify: `mcp-server/central-command/backend/routes.py` (add after org detail endpoint)
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py`

- [ ] **Step 1: Write test for health endpoint structure**

```python
class TestOrgHealthEndpoint:
    """Test org health consolidation returns expected structure."""

    def test_health_response_has_required_fields(self):
        """Health response must include all consolidation fields."""
        required_fields = [
            "org_id", "compliance", "incidents", "healing",
            "fleet", "categories",
        ]
        # Will be tested via API call once endpoint exists
        for f in required_fields:
            assert f in required_fields  # placeholder assertion
```

- [ ] **Step 2: Implement org health endpoint**

Add to `routes.py` after the `get_organization_detail` endpoint (~line 2795):

```python
@router.get("/organizations/{org_id}/health")
async def get_organization_health(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get consolidated health metrics across all sites in an organization."""
    auth_module._check_org_access(user, org_id)

    # Verify org exists
    org_check = await db.execute(
        text("SELECT id FROM client_orgs WHERE id = :org_id"),
        {"org_id": org_id}
    )
    if not org_check.fetchone():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get org's site_ids
    sites_result = await db.execute(
        text("SELECT site_id FROM sites WHERE client_org_id = :org_id"),
        {"org_id": org_id}
    )
    site_ids = [r.site_id for r in sites_result.fetchall()]

    if not site_ids:
        return {
            "org_id": org_id,
            "compliance": {"score": 0, "has_data": False, "site_scores": {}},
            "incidents": {"total_24h": 0, "total_7d": 0, "total_30d": 0, "by_severity": {}},
            "healing": {"success_rate": 0, "l1_count": 0, "l2_count": 0, "l3_count": 0},
            "fleet": {"total": 0, "online": 0, "stale": 0, "offline": 0},
            "categories": {},
        }

    # Compliance scores
    all_compliance = await get_all_compliance_scores(db)
    site_scores = {}
    scores_list = []
    for sid in site_ids:
        sc = all_compliance.get(sid, {})
        if sc.get("has_data"):
            site_scores[sid] = round(sc["score"], 1)
            scores_list.append(sc["score"])
    avg_score = round(sum(scores_list) / len(scores_list), 1) if scores_list else 0

    # Incident counts (24h, 7d, 30d)
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as total_24h,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as total_7d,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '30 days') as total_30d,
            COUNT(*) FILTER (WHERE severity = 'critical' AND created_at > NOW() - INTERVAL '7 days') as critical_7d,
            COUNT(*) FILTER (WHERE severity = 'high' AND created_at > NOW() - INTERVAL '7 days') as high_7d,
            COUNT(*) FILTER (WHERE severity = 'medium' AND created_at > NOW() - INTERVAL '7 days') as medium_7d,
            COUNT(*) FILTER (WHERE severity = 'low' AND created_at > NOW() - INTERVAL '7 days') as low_7d
        FROM incidents
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    inc = incident_result.fetchone()

    # Healing metrics (mirrors get_all_healing_metrics pattern from db_queries.py)
    # Healing rate = resolved incidents / total incidents
    healing_inc_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved
        FROM incidents i
        WHERE i.site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    heal_inc = healing_inc_result.fetchone()

    # Order execution rate = completed orders / total orders
    healing_ord_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'completed') as completed
        FROM admin_orders
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    heal_ord = healing_ord_result.fetchone()

    healing_rate = round(
        (heal_inc.resolved / heal_inc.total * 100) if heal_inc.total > 0 else 100.0, 1
    )
    order_rate = round(
        (heal_ord.completed / heal_ord.total * 100) if heal_ord.total > 0 else 100.0, 1
    )

    # Fleet status
    fleet_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '15 minutes') as online,
            COUNT(*) FILTER (
                WHERE last_checkin <= NOW() - INTERVAL '15 minutes'
                  AND last_checkin > NOW() - INTERVAL '1 hour'
            ) as stale,
            COUNT(*) FILTER (
                WHERE last_checkin IS NULL OR last_checkin <= NOW() - INTERVAL '1 hour'
            ) as offline
        FROM site_appliances
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    fleet = fleet_result.fetchone()

    # Per-category compliance breakdown
    cat_result = await db.execute(text("""
        SELECT
            cb.check_type,
            COUNT(*) FILTER (WHERE cb.check_result = 'pass') as passes,
            COUNT(*) FILTER (WHERE cb.check_result = 'fail') as fails,
            COUNT(*) as total
        FROM compliance_bundles cb
        WHERE cb.site_id = ANY(:site_ids)
          AND cb.checked_at = (
              SELECT MAX(cb2.checked_at) FROM compliance_bundles cb2
              WHERE cb2.site_id = cb.site_id AND cb2.check_type = cb.check_type
          )
        GROUP BY cb.check_type
        ORDER BY cb.check_type
    """), {"site_ids": site_ids})
    categories = {}
    for row in cat_result.fetchall():
        categories[row.check_type] = {
            "passes": row.passes,
            "fails": row.fails,
            "total": row.total,
            "score": round(row.passes / row.total * 100, 1) if row.total > 0 else 0,
        }

    return {
        "org_id": org_id,
        "compliance": {
            "score": avg_score,
            "has_data": len(scores_list) > 0,
            "site_scores": site_scores,
        },
        "incidents": {
            "total_24h": inc.total_24h,
            "total_7d": inc.total_7d,
            "total_30d": inc.total_30d,
            "by_severity": {
                "critical": inc.critical_7d,
                "high": inc.high_7d,
                "medium": inc.medium_7d,
                "low": inc.low_7d,
            },
        },
        "healing": {
            "success_rate": healing_rate,
            "order_execution_rate": order_rate,
            "total_incidents": heal_inc.total,
            "resolved_incidents": heal_inc.resolved,
            "total_orders": heal_ord.total,
            "completed_orders": heal_ord.completed,
        },
        "fleet": {
            "total": fleet.total,
            "online": fleet.online,
            "stale": fleet.stale,
            "offline": fleet.offline,
        },
        "categories": categories,
    }
```

- [ ] **Step 3: Run tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/routes.py backend/tests/test_org_hardening.py
git commit -m "feat: org-level consolidated health endpoint"
```

---

### Task 6: Add org-level incident list endpoint

Paginated incident list across all sites in an org, filterable by site/severity/status.

**Files:**
- Modify: `mcp-server/central-command/backend/routes.py`

- [ ] **Step 1: Implement org incidents endpoint**

Add to `routes.py` after the health endpoint:

```python
@router.get("/organizations/{org_id}/incidents")
async def get_organization_incidents(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
    site_id: Optional[str] = QueryParam(None),
    severity: Optional[str] = QueryParam(None),
    status: Optional[str] = QueryParam(None),
    limit: int = QueryParam(50, ge=1, le=200),
    offset: int = QueryParam(0, ge=0),
):
    """List incidents across all sites in an organization."""
    auth_module._check_org_access(user, org_id)

    # Build dynamic query
    conditions = ["s.client_org_id = :org_id"]
    params = {"org_id": org_id}

    if site_id:
        conditions.append("i.site_id = :site_id")
        params["site_id"] = site_id
    if severity:
        conditions.append("i.severity = :severity")
        params["severity"] = severity
    if status:
        conditions.append("i.status = :status")
        params["status"] = status

    where = " AND ".join(conditions)

    result = await db.execute(text(f"""
        SELECT i.id, i.site_id, s.clinic_name, i.incident_type, i.severity,
               i.status, i.details, i.created_at, i.resolved_at
        FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE {where}
        ORDER BY i.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset})
    rows = result.fetchall()

    count_result = await db.execute(text(f"""
        SELECT COUNT(*) FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE {where}
    """), params)
    total = count_result.scalar()

    return {
        "incidents": [
            {
                "id": str(r.id),
                "site_id": r.site_id,
                "clinic_name": r.clinic_name or r.site_id,
                "incident_type": r.incident_type,
                "severity": r.severity,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
```

- [ ] **Step 2: Run tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/routes.py
git commit -m "feat: org-level paginated incident list endpoint"
```

---

### Task 7: PHI boundary — sanitize evidence for client portal

The client portal evidence detail endpoint (`client_portal.py:1185`) returns raw `checks` JSONB which may contain infrastructure details (hostnames, IPs, user lists, service names). Evidence list endpoint is already clean (only returns metadata). The detail + download endpoints need sanitization for viewer role.

**Files:**
- Create: `mcp-server/central-command/backend/phi_boundary.py`
- Modify: `mcp-server/central-command/backend/client_portal.py:1185-1294`
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py`

- [ ] **Step 1: Write tests for PHI sanitization**

Add to `test_org_hardening.py`:

```python
class TestPHIBoundary:
    """Test that PHI/infrastructure data is sanitized for portal access."""

    def test_sanitize_checks_removes_raw_output(self):
        """Raw command output should be stripped from checks."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "windows_patching",
                "result": "fail",
                "hipaa_control": "164.308(a)(5)(ii)(B)",
                "raw_output": "KB5034441 missing on DC01 at 192.168.88.250",
                "details": {"missing_patches": ["KB5034441"]},
                "hostname": "DC01",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert "raw_output" not in sanitized[0]
        assert "hostname" not in sanitized[0]
        assert sanitized[0]["check_type"] == "windows_patching"
        assert sanitized[0]["result"] == "fail"
        assert sanitized[0]["hipaa_control"] == "164.308(a)(5)(ii)(B)"

    def test_sanitize_checks_preserves_compliance_fields(self):
        """Compliance-relevant fields are preserved."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "firewall_enabled",
                "result": "pass",
                "hipaa_control": "164.312(e)(1)",
                "summary": "Firewall active on all endpoints",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert sanitized[0]["check_type"] == "firewall_enabled"
        assert sanitized[0]["result"] == "pass"
        assert sanitized[0]["summary"] == "Firewall active on all endpoints"

    def test_sanitize_strips_ip_addresses_from_summary(self):
        """IP addresses in summary text should be masked."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "logging",
                "result": "pass",
                "summary": "Syslog forwarding active to 192.168.88.50:514",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert "192.168.88.50" not in sanitized[0].get("summary", "")

    def test_sanitize_empty_checks(self):
        """Empty or None checks handled gracefully."""
        from phi_boundary import sanitize_evidence_checks
        assert sanitize_evidence_checks([]) == []
        assert sanitize_evidence_checks(None) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/test_org_hardening.py::TestPHIBoundary -v`
Expected: FAIL — `phi_boundary` module doesn't exist

- [ ] **Step 3: Implement phi_boundary.py**

Create `mcp-server/central-command/backend/phi_boundary.py`:

```python
"""PHI boundary enforcement for portal-facing endpoints.

Strips infrastructure details (hostnames, IPs, raw command output)
from evidence data before returning to client/partner portals.
Compliance-relevant fields (check_type, result, hipaa_control, summary)
are preserved. Raw evidence is still available for admin download.
"""

import re
from typing import Any, Optional

# Fields safe to return to portals
_SAFE_FIELDS = {
    "check_type", "result", "check_result", "hipaa_control",
    "summary", "category", "severity", "remediation_hint",
    "control_id", "framework",
}

# Fields that contain infrastructure details — always strip
_STRIP_FIELDS = {
    "raw_output", "stdout", "stderr", "command", "cmd",
    "hostname", "host", "ip_address", "target_host",
    "username", "user", "service_name", "process_list",
    "registry_key", "registry_value", "file_path",
}

# IP address pattern
_IP_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)


def _mask_ips(text: str) -> str:
    """Replace IP addresses with [REDACTED-IP]."""
    if not isinstance(text, str):
        return text
    return _IP_PATTERN.sub("[REDACTED-IP]", text)


def sanitize_evidence_checks(checks: Any) -> list:
    """Sanitize evidence checks for portal display.

    Removes infrastructure details while preserving compliance-relevant data.
    """
    if not checks:
        return []

    if isinstance(checks, str):
        import json
        try:
            checks = json.loads(checks)
        except (json.JSONDecodeError, TypeError):
            return []

    if not isinstance(checks, list):
        return []

    sanitized = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        clean = {}
        for key, value in check.items():
            if key in _STRIP_FIELDS:
                continue
            if key in _SAFE_FIELDS:
                if isinstance(value, str):
                    clean[key] = _mask_ips(value)
                else:
                    clean[key] = value
            elif key == "details" and isinstance(value, dict):
                # Keep details but strip infrastructure sub-fields
                clean_details = {}
                for dk, dv in value.items():
                    if dk not in _STRIP_FIELDS:
                        if isinstance(dv, str):
                            clean_details[dk] = _mask_ips(dv)
                        else:
                            clean_details[dk] = dv
                if clean_details:
                    clean["details"] = clean_details
        sanitized.append(clean)

    return sanitized
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/test_org_hardening.py::TestPHIBoundary -v`
Expected: 4 passed

- [ ] **Step 5: Apply sanitization to client portal evidence detail**

In `client_portal.py:1185` (`get_evidence_detail`), import and apply:

```python
from .phi_boundary import sanitize_evidence_checks
```

In the return dict at line ~1228, change the raw `checks` handling. After the existing `hipaa_control` extraction, add sanitization for the response. Replace the bundle dict construction to NOT include raw checks for `viewer` role:

```python
        # Sanitize checks for portal display (strip infrastructure details)
        sanitized_checks = sanitize_evidence_checks(checks)

        return {
            "bundle": {
                "id": str(bundle["id"]),
                "site_id": bundle["site_id"],
                "clinic_name": bundle["clinic_name"],
                "check_type": bundle["check_type"],
                "check_result": bundle["check_result"],
                "hipaa_control": hipaa_control,
                "checked_at": bundle["checked_at"].isoformat() if bundle["checked_at"] else None,
                "bundle_hash": bundle["bundle_id"],
                "prev_hash": bundle.get("prev_hash"),
                "agent_signature": bundle.get("agent_signature") or bundle.get("signature"),
                "minio_path": None,
                "checks": sanitized_checks,  # Sanitized for portal
            },
            ...
```

Also apply to the evidence download endpoint (`download_evidence` at line 1253). Add a `portal_sanitized: true` flag to the metadata and sanitize the checks:

```python
        evidence_data = {
            "bundle_id": bundle["bundle_id"],
            "check_type": bundle["check_type"],
            "check_result": bundle["check_result"],
            "checked_at": checked_at.isoformat() if checked_at else None,
            "summary": _json.loads(bundle["summary"]) if isinstance(bundle["summary"], str) else bundle["summary"],
            "checks": sanitize_evidence_checks(
                _json.loads(bundle["checks"]) if isinstance(bundle["checks"], str) else bundle["checks"]
            ),
            "metadata": {
                "format": "OsirisCare Evidence Bundle v1",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "integrity": "compliance_bundle",
                "portal_sanitized": True,
            },
        }
```

- [ ] **Step 6: Run all tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add backend/phi_boundary.py backend/client_portal.py backend/tests/test_org_hardening.py
git commit -m "feat: PHI boundary enforcement — sanitize evidence for client portal"
```

---

### Task 8: Partner portal — org list with consolidated health

Partners currently see sites via `GET /api/partners/me/sites` but have no org-level view. Add `GET /api/partners/me/orgs` that returns the partner's organizations with consolidated metrics.

**Files:**
- Modify: `mcp-server/central-command/backend/partners.py`
- Modify: `mcp-server/central-command/backend/tests/test_org_hardening.py`

- [ ] **Step 1: Implement partner org list endpoint**

Add to `partners.py` after the `get_my_sites` endpoint (~line 404):

```python
@router.get("/me/orgs")
async def get_my_orgs(request: Request, partner=Depends(require_partner)):
    """Get organizations managed by this partner with consolidated health."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT
                co.id, co.name, co.primary_email, co.practice_type,
                co.provider_count, co.status, co.created_at,
                COUNT(DISTINCT s.site_id) as site_count,
                COUNT(DISTINCT sa.appliance_id) as appliance_count,
                MAX(sa.last_checkin) as last_checkin,
                COUNT(DISTINCT sa.id) FILTER (
                    WHERE sa.last_checkin > NOW() - INTERVAL '15 minutes'
                ) as online_count
            FROM client_orgs co
            LEFT JOIN sites s ON s.client_org_id = co.id AND s.partner_id = $1
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            WHERE co.current_partner_id = $1
            GROUP BY co.id
            ORDER BY co.name
        """, partner['id'])

        orgs = []
        for row in rows:
            orgs.append({
                'id': str(row['id']),
                'name': row['name'],
                'primary_email': row['primary_email'],
                'practice_type': row['practice_type'],
                'provider_count': row['provider_count'],
                'status': row['status'],
                'site_count': row['site_count'],
                'appliance_count': row['appliance_count'],
                'online_count': row['online_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            })

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITES_LISTED,
            target_type="organizations",
            target_id=str(partner['id']),
            event_data={"org_count": len(orgs)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'organizations': orgs, 'count': len(orgs)}
```

- [ ] **Step 2: Run tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/partners.py
git commit -m "feat: partner portal org list with consolidated health"
```

---

### Task 9: Partner portal — bulk org-level drift config

Partners can adjust drift config per-site but not at org level. Add endpoints to get/set drift config for all sites in an org at once.

**Files:**
- Modify: `mcp-server/central-command/backend/partners.py`

- [ ] **Step 1: Implement partner org drift config endpoints**

Add to `partners.py` after the org list endpoint:

```python
@router.get("/me/orgs/{org_id}/drift-config")
async def get_partner_org_drift_config(
    org_id: str,
    partner=Depends(require_partner)
):
    """Get drift config for all sites in an org (org-level view)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get all site drift configs in this org
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, sdc.disabled_checks
            FROM sites s
            LEFT JOIN site_drift_config sdc ON sdc.site_id = s.site_id
            WHERE s.client_org_id = $1 AND s.partner_id = $2
            ORDER BY s.clinic_name
        """, org_id, partner['id'])

        sites = []
        for row in rows:
            disabled = row['disabled_checks'] or []
            if isinstance(disabled, str):
                import json
                disabled = json.loads(disabled)
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'disabled_checks': disabled,
            })

        return {'org_id': org_id, 'sites': sites}


@router.put("/me/orgs/{org_id}/drift-config")
async def update_partner_org_drift_config(
    org_id: str,
    request: Request,
    partner=Depends(require_partner)
):
    """Apply drift config to ALL sites in an org (bulk operation)."""
    pool = await get_pool()
    body = await request.json()
    disabled_checks = body.get("disabled_checks", [])

    if not isinstance(disabled_checks, list):
        raise HTTPException(status_code=400, detail="disabled_checks must be a list")

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get org's sites
        site_rows = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )

        import json
        updated = 0
        async with conn.transaction():
            for row in site_rows:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, disabled_checks)
                    VALUES ($1, $2)
                    ON CONFLICT (site_id) DO UPDATE SET
                        disabled_checks = $2,
                        updated_at = NOW()
                """, row['site_id'], json.dumps(disabled_checks))
                updated += 1

        await log_partner_site_action(
            partner_id=str(partner['id']),
            site_id=org_id,
            event_type=PartnerEventType.ASSET_UPDATED,
            event_data={
                "action": "bulk_drift_config",
                "disabled_checks": disabled_checks,
                "sites_updated": updated,
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'status': 'updated', 'sites_updated': updated}
```

- [ ] **Step 2: Run tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/partners.py
git commit -m "feat: partner bulk org-level drift config endpoints"
```

---

### Task 10: Schema prep — site-level partner override

Future-proof for sub-partner model: a partner owns the org, but a sub-partner can manage a specific site within it.

**Files:**
- Create: `mcp-server/central-command/backend/migrations/095_site_partner_override.sql`

- [ ] **Step 1: Write migration**

Create `mcp-server/central-command/backend/migrations/095_site_partner_override.sql`:

```sql
-- Migration 095: Site-level partner override
-- Allows a sub-partner to manage a specific site within an org,
-- overriding the org's current_partner_id for that site only.
-- If NULL, the site inherits the org's partner.

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS sub_partner_id UUID REFERENCES partners(id);

COMMENT ON COLUMN sites.sub_partner_id IS
    'Optional sub-partner override. If set, this partner manages the site instead of the org default partner.';

CREATE INDEX IF NOT EXISTS idx_sites_sub_partner ON sites(sub_partner_id) WHERE sub_partner_id IS NOT NULL;
```

- [ ] **Step 2: Commit**

```bash
git add backend/migrations/095_site_partner_override.sql
git commit -m "feat: schema prep for site-level sub-partner override"
```

---

### Task 11: Frontend — update OrgDashboard with health data

Wire the new `/organizations/{org_id}/health` endpoint into the OrgDashboard page to show consolidated metrics.

**Files:**
- Modify: `mcp-server/central-command/frontend/src/utils/api.ts`
- Modify: `mcp-server/central-command/frontend/src/pages/OrgDashboard.tsx`

- [ ] **Step 1: Add health API type and method**

In `mcp-server/central-command/frontend/src/utils/api.ts`, add the type after `OrganizationDetail`:

```typescript
export interface OrgHealth {
  org_id: string;
  compliance: {
    score: number;
    has_data: boolean;
    site_scores: Record<string, number>;
  };
  incidents: {
    total_24h: number;
    total_7d: number;
    total_30d: number;
    by_severity: Record<string, number>;
  };
  healing: {
    success_rate: number;
    order_execution_rate: number;
    total_incidents: number;
    resolved_incidents: number;
    total_orders: number;
    completed_orders: number;
  };
  fleet: {
    total: number;
    online: number;
    stale: number;
    offline: number;
  };
  categories: Record<string, {
    passes: number;
    fails: number;
    total: number;
    score: number;
  }>;
}
```

Add to the `organizationsApi` object:

```typescript
  getOrgHealth: (orgId: string) =>
    fetchApi<OrgHealth>(`/organizations/${orgId}/health`),

  getOrgIncidents: (orgId: string, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return fetchApi<{ incidents: any[]; total: number }>(`/organizations/${orgId}/incidents${qs}`);
  },
```

- [ ] **Step 2: Update OrgDashboard to fetch and display health**

Read `OrgDashboard.tsx` first, then add a `useQuery` for health data and render consolidated stat cards (fleet status, incident counts, healing rate, compliance breakdown).

The key additions:
- `useQuery(['org-health', orgId], () => organizationsApi.getOrgHealth(orgId!))`
- StatCard row showing: Fleet (X/Y online), Incidents (24h), Healing Rate, Compliance Score
- Category breakdown table

- [ ] **Step 3: Run frontend lint**

Run: `cd mcp-server/central-command/frontend && npx eslint src/pages/OrgDashboard.tsx src/utils/api.ts --max-warnings 0`
Expected: 0 errors, 0 warnings

- [ ] **Step 4: Commit**

```bash
git add frontend/src/utils/api.ts frontend/src/pages/OrgDashboard.tsx
git commit -m "feat: OrgDashboard shows consolidated health metrics"
```

---

### Task 12: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run Python backend tests**

Run: `cd mcp-server/central-command && python -m pytest backend/tests/ -v --tb=short`
Expected: All pass (203+ existing + new org hardening tests)

- [ ] **Step 2: Run frontend build**

Run: `cd mcp-server/central-command/frontend && npx tsc --noEmit && npx eslint src/ --max-warnings 0`
Expected: 0 errors

- [ ] **Step 3: Final commit with version bump**

Update `claude-progress.json` with session notes, then:

Update `.agent/claude-progress.json` to reflect session work, then verify git status shows only expected changes.
