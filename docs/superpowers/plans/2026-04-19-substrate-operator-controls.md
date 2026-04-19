# Substrate Health Operator Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three scoped, non-operator-safe action buttons (`cleanup_install_session`, `unlock_platform_account`, `reconcile_fleet_order`) plus an in-panel runbook viewer + browseable library on `/admin/substrate-health`, so OsirisCare staff can manage internal substrate operations without dropping to shell or depending on AI — and without mutating customer infrastructure.

**Architecture:** Single `POST /api/admin/substrate/action` endpoint backed by a strict handler registry in `substrate_actions.py`. Each handler does one single-row UPDATE/DELETE inside a transaction, writes one `admin_audit_log` row, and records idempotency in a new `substrate_action_invocations` table (migration 238). Runbook docs are markdown files under `docs/substrate/` served via `GET /api/admin/substrate/runbook/<invariant>` and rendered with `react-markdown` + `rehype-sanitize` in a right-side drawer. A CI gate enforces one doc file per `assertions.ALL_ASSERTIONS` entry. Feature flag `SUBSTRATE_ACTIONS_ENABLED` gates the write path; the read/runbook path is always on.

**Tech Stack:** FastAPI + asyncpg + SQLAlchemy (`execute_with_retry`), Pydantic v2, pytest-asyncio, React 18 + TypeScript + Tailwind + React Query, `react-markdown@9` + `rehype-sanitize@6`, Vitest. Spec reference: `docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md`.

---

### Task 1: Migration 238 — `substrate_action_invocations` table

**Files:**
- Create: `mcp-server/central-command/backend/migrations/238_substrate_action_invocations.sql`
- Test: `mcp-server/central-command/backend/tests/test_migration_238.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_238.py
import pytest
from sqlalchemy import text
from shared import async_session

@pytest.mark.asyncio
async def test_substrate_action_invocations_schema():
    async with async_session() as db:
        cols = await db.execute(text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'substrate_action_invocations' "
            "ORDER BY ordinal_position"
        ))
        cols = list(cols)
    names = {r[0] for r in cols}
    assert {"id", "idempotency_key", "actor_email", "action_key",
            "target_ref", "reason", "result_status", "result_body",
            "admin_audit_id", "created_at"} <= names

@pytest.mark.asyncio
async def test_substrate_action_invocations_unique_index():
    async with async_session() as db:
        idx = await db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'substrate_action_invocations' "
            "AND indexname = 'substrate_action_invocations_idem'"
        ))
        assert idx.scalar() == "substrate_action_invocations_idem"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server/central-command/backend && pytest tests/test_migration_238.py -v`
Expected: FAIL — table does not exist.

- [ ] **Step 3: Write the migration SQL**

```sql
-- 238_substrate_action_invocations.sql
-- Idempotency + audit-pointer table for POST /api/admin/substrate/action.
-- Append-only: no UPDATE/DELETE triggers (24h replay window is a query filter).

CREATE TABLE IF NOT EXISTS substrate_action_invocations (
    id               BIGSERIAL PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,
    actor_email      VARCHAR(255) NOT NULL,
    action_key       VARCHAR(64) NOT NULL,
    target_ref       JSONB NOT NULL,
    reason           TEXT,
    result_status    VARCHAR(32) NOT NULL,
    result_body      JSONB NOT NULL,
    admin_audit_id   BIGINT REFERENCES admin_audit_log(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS substrate_action_invocations_idem
    ON substrate_action_invocations (actor_email, idempotency_key);

CREATE INDEX IF NOT EXISTS substrate_action_invocations_actor_time
    ON substrate_action_invocations (actor_email, created_at DESC);

CREATE INDEX IF NOT EXISTS substrate_action_invocations_action_time
    ON substrate_action_invocations (action_key, created_at DESC);

COMMENT ON TABLE substrate_action_invocations IS
    'Idempotency + audit pointer for /api/admin/substrate/action. '
    'Append-only via app code. 24h replay window is a query filter.';
```

- [ ] **Step 4: Apply migration and re-run tests**

Run:
```bash
cd mcp-server/central-command/backend
python3 -m migrations.migrate up
pytest tests/test_migration_238.py -v
```
Expected: PASS on both tests.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/migrations/238_substrate_action_invocations.sql
git add mcp-server/central-command/backend/tests/test_migration_238.py
git commit -m "feat(substrate): migration 238 — substrate_action_invocations idempotency table"
```

---

### Task 2: Handler registry scaffold

**Files:**
- Create: `mcp-server/central-command/backend/substrate_actions.py`
- Test: `mcp-server/central-command/backend/tests/test_substrate_actions_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_actions_registry.py
import pytest
from substrate_actions import SUBSTRATE_ACTIONS, SubstrateAction

def test_registry_has_exactly_three_keys():
    assert set(SUBSTRATE_ACTIONS.keys()) == {
        "cleanup_install_session",
        "unlock_platform_account",
        "reconcile_fleet_order",
    }

def test_each_entry_is_substrate_action():
    for key, value in SUBSTRATE_ACTIONS.items():
        assert isinstance(value, SubstrateAction)
        assert callable(value.handler)
        assert value.audit_action == f"substrate.{key}"
        assert value.required_reason_chars in (0, 20)

def test_no_privileged_order_types_in_registry():
    # Guardrail: registry must never alias a fleet_cli privileged order type.
    from fleet_cli import PRIVILEGED_ORDER_TYPES
    assert SUBSTRATE_ACTIONS.keys().isdisjoint(PRIVILEGED_ORDER_TYPES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_actions_registry.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the registry module**

```python
# substrate_actions.py
"""Handler registry for POST /api/admin/substrate/action.

Each entry is a single-row, internal-substrate-only action. Nothing in this
module enqueues fleet orders or touches customer infrastructure.
Non-operator posture audit: docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md Section 12.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from asyncpg import Connection

logger = logging.getLogger(__name__)

HandlerFn = Callable[[Connection, dict, str], Awaitable[dict]]

@dataclass(frozen=True)
class SubstrateAction:
    handler: HandlerFn
    required_reason_chars: int
    audit_action: str


async def _handle_cleanup_install_session(conn: Connection, target_ref: dict, reason: str) -> dict:
    raise NotImplementedError("wired in Task 3")

async def _handle_unlock_platform_account(conn: Connection, target_ref: dict, reason: str) -> dict:
    raise NotImplementedError("wired in Task 4")

async def _handle_reconcile_fleet_order(conn: Connection, target_ref: dict, reason: str) -> dict:
    raise NotImplementedError("wired in Task 5")


SUBSTRATE_ACTIONS: Dict[str, SubstrateAction] = {
    "cleanup_install_session": SubstrateAction(
        handler=_handle_cleanup_install_session,
        required_reason_chars=0,
        audit_action="substrate.cleanup_install_session",
    ),
    "unlock_platform_account": SubstrateAction(
        handler=_handle_unlock_platform_account,
        required_reason_chars=20,
        audit_action="substrate.unlock_platform_account",
    ),
    "reconcile_fleet_order": SubstrateAction(
        handler=_handle_reconcile_fleet_order,
        required_reason_chars=20,
        audit_action="substrate.reconcile_fleet_order",
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_substrate_actions_registry.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_actions.py
git add mcp-server/central-command/backend/tests/test_substrate_actions_registry.py
git commit -m "feat(substrate): handler registry scaffold with privileged-type guardrail"
```

---

### Task 3: Handler — `cleanup_install_session`

**Files:**
- Modify: `mcp-server/central-command/backend/substrate_actions.py`
- Test: `mcp-server/central-command/backend/tests/test_substrate_actions_cleanup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_actions_cleanup.py
import pytest
from asyncpg import create_pool
from substrate_actions import _handle_cleanup_install_session
from shared import admin_connection

@pytest.mark.asyncio
async def test_cleanup_install_session_deletes_one_row(seed_stale_install_session):
    mac = seed_stale_install_session["mac"]
    async with admin_connection() as conn:
        async with conn.transaction():
            result = await _handle_cleanup_install_session(
                conn, {"mac": mac, "stage": "live_usb"}, reason=""
            )
    assert result["deleted"] == 1
    assert result["mac"] == mac
    async with admin_connection() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM install_sessions WHERE mac=$1", mac
        )
    assert n == 0

@pytest.mark.asyncio
async def test_cleanup_install_session_missing_row_raises_notfound():
    from substrate_actions import TargetNotFound
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotFound):
                await _handle_cleanup_install_session(
                    conn, {"mac": "00:00:00:00:00:00", "stage": "live_usb"}, reason=""
                )

@pytest.mark.asyncio
async def test_cleanup_install_session_rejects_missing_mac():
    from substrate_actions import TargetRefInvalid
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetRefInvalid):
                await _handle_cleanup_install_session(conn, {}, reason="")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_actions_cleanup.py -v`
Expected: FAIL — `NotImplementedError` + unknown exception classes.

- [ ] **Step 3: Implement handler + exceptions**

Replace the `_handle_cleanup_install_session` stub and add exception classes at the top of `substrate_actions.py`:

```python
class SubstrateActionError(Exception): ...
class TargetRefInvalid(SubstrateActionError): ...
class TargetNotFound(SubstrateActionError): ...
class TargetNotActionable(SubstrateActionError): ...

MAC_PATTERN = re.compile(r"^[0-9A-Fa-f:\-]{12,17}$")

async def _handle_cleanup_install_session(conn: Connection, target_ref: dict, reason: str) -> dict:
    mac = target_ref.get("mac")
    stage = target_ref.get("stage")
    if not mac or not MAC_PATTERN.match(mac):
        raise TargetRefInvalid("mac required and must match pattern")
    row = await conn.fetchrow(
        "SELECT mac, stage, checkin_count, first_seen "
        "FROM install_sessions WHERE mac = $1 "
        + ("AND stage = $2" if stage else ""),
        *((mac, stage) if stage else (mac,)),
    )
    if row is None:
        raise TargetNotFound(f"no install_sessions row for mac={mac}")
    await conn.execute("DELETE FROM install_sessions WHERE mac = $1", mac)
    logger.info("substrate.cleanup_install_session", extra={
        "mac": mac, "stage": row["stage"], "checkin_count": row["checkin_count"],
    })
    return {
        "deleted": 1, "mac": mac, "stage": row["stage"],
        "checkin_count": row["checkin_count"],
        "first_seen": row["first_seen"].isoformat(),
    }
```

Add the `import re` at the module top.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_substrate_actions_cleanup.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_actions.py
git add mcp-server/central-command/backend/tests/test_substrate_actions_cleanup.py
git commit -m "feat(substrate): cleanup_install_session handler + exception taxonomy"
```

---

### Task 4: Handler — `unlock_platform_account`

**Files:**
- Modify: `mcp-server/central-command/backend/substrate_actions.py`
- Test: `mcp-server/central-command/backend/tests/test_substrate_actions_unlock.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_actions_unlock.py
import pytest
from datetime import datetime, timedelta, timezone
from substrate_actions import (
    _handle_unlock_platform_account, TargetNotActionable, TargetNotFound
)
from shared import admin_connection

@pytest.mark.asyncio
async def test_unlock_partner_resets_counters(seed_locked_partner):
    email = seed_locked_partner["email"]
    async with admin_connection() as conn:
        async with conn.transaction():
            result = await _handle_unlock_platform_account(
                conn,
                {"table": "partners", "email": email},
                reason="Confirmed legitimate user after phone verification ok",
            )
    assert result["email"] == email
    assert result["previous_failed_count"] >= 5
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            "SELECT failed_login_attempts, locked_until FROM partners WHERE email=$1", email
        )
    assert row["failed_login_attempts"] == 0
    assert row["locked_until"] is None

@pytest.mark.asyncio
async def test_unlock_client_user_resets_counters(seed_locked_client_user):
    email = seed_locked_client_user["email"]
    async with admin_connection() as conn:
        async with conn.transaction():
            result = await _handle_unlock_platform_account(
                conn,
                {"table": "client_users", "email": email},
                reason="Password manager glitch, confirmed via Slack DM",
            )
    assert result["table"] == "client_users"

@pytest.mark.asyncio
async def test_unlock_rejects_invalid_table():
    from substrate_actions import TargetRefInvalid
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetRefInvalid):
                await _handle_unlock_platform_account(
                    conn, {"table": "sites", "email": "x@y.z"},
                    reason="x" * 25,
                )

@pytest.mark.asyncio
async def test_unlock_not_actionable_if_not_locked(seed_unlocked_partner):
    email = seed_unlocked_partner["email"]
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotActionable):
                await _handle_unlock_platform_account(
                    conn, {"table": "partners", "email": email},
                    reason="Trying to unlock an already-unlocked account as test",
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_actions_unlock.py -v`
Expected: FAIL — still `NotImplementedError`.

- [ ] **Step 3: Implement handler**

Replace the stub:

```python
ALLOWED_UNLOCK_TABLES = {"partners", "client_users"}

async def _handle_unlock_platform_account(conn: Connection, target_ref: dict, reason: str) -> dict:
    table = target_ref.get("table")
    email = target_ref.get("email")
    if table not in ALLOWED_UNLOCK_TABLES:
        raise TargetRefInvalid(f"table must be one of {ALLOWED_UNLOCK_TABLES}")
    if not email or "@" not in email:
        raise TargetRefInvalid("email required")
    row = await conn.fetchrow(
        f"SELECT email, failed_login_attempts, locked_until FROM {table} WHERE email = $1",
        email,
    )
    if row is None:
        raise TargetNotFound(f"no {table} row for email={email}")
    is_locked = (
        (row["failed_login_attempts"] or 0) >= 5
        or (row["locked_until"] is not None)
    )
    if not is_locked:
        raise TargetNotActionable(
            f"{table}.{email} is not currently locked — nothing to do"
        )
    await conn.execute(
        f"UPDATE {table} "
        "SET failed_login_attempts = 0, locked_until = NULL "
        "WHERE email = $1",
        email,
    )
    return {
        "table": table, "email": email,
        "previous_failed_count": row["failed_login_attempts"],
        "previous_locked_until": (
            row["locked_until"].isoformat() if row["locked_until"] else None
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_substrate_actions_unlock.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_actions.py
git add mcp-server/central-command/backend/tests/test_substrate_actions_unlock.py
git commit -m "feat(substrate): unlock_platform_account handler (partners, client_users)"
```

---

### Task 5: Handler — `reconcile_fleet_order`

**Files:**
- Modify: `mcp-server/central-command/backend/substrate_actions.py`
- Test: `mcp-server/central-command/backend/tests/test_substrate_actions_reconcile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_actions_reconcile.py
import pytest
from substrate_actions import (
    _handle_reconcile_fleet_order, TargetNotActionable
)
from shared import admin_connection

@pytest.mark.asyncio
async def test_reconcile_marks_active_order_completed(seed_active_fleet_order):
    order_id = seed_active_fleet_order["order_id"]
    site_id = seed_active_fleet_order["site_id"]
    async with admin_connection() as conn:
        async with conn.transaction():
            result = await _handle_reconcile_fleet_order(
                conn, {"order_id": order_id, "site_id": site_id},
                reason="Verified 0.4.3 live via ssh into appliance, order never acked",
            )
    assert result["order_id"] == order_id
    assert result["prev_status"] == "active"
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            "SELECT status, completed_at FROM fleet_orders WHERE id=$1", order_id
        )
    assert row["status"] == "completed"
    assert row["completed_at"] is not None

@pytest.mark.asyncio
async def test_reconcile_rejects_completed_order(seed_completed_fleet_order):
    order_id = seed_completed_fleet_order["order_id"]
    site_id = seed_completed_fleet_order["site_id"]
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotActionable):
                await _handle_reconcile_fleet_order(
                    conn, {"order_id": order_id, "site_id": site_id},
                    reason="Trying to reconcile already-completed order test",
                )

@pytest.mark.asyncio
async def test_reconcile_rejects_privileged_order_type(seed_privileged_fleet_order):
    from substrate_actions import TargetRefInvalid
    order_id = seed_privileged_fleet_order["order_id"]
    site_id = seed_privileged_fleet_order["site_id"]
    async with admin_connection() as conn:
        async with conn.transaction():
            with pytest.raises(TargetRefInvalid):
                await _handle_reconcile_fleet_order(
                    conn, {"order_id": order_id, "site_id": site_id},
                    reason="Reconciling a privileged order should be refused",
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_actions_reconcile.py -v`
Expected: FAIL — stub.

- [ ] **Step 3: Implement handler**

Replace the stub:

```python
from fleet_cli import PRIVILEGED_ORDER_TYPES

async def _handle_reconcile_fleet_order(conn: Connection, target_ref: dict, reason: str) -> dict:
    order_id = target_ref.get("order_id")
    site_id = target_ref.get("site_id")
    if not order_id or not site_id:
        raise TargetRefInvalid("order_id and site_id required")
    row = await conn.fetchrow(
        "SELECT id, site_id, order_type, status FROM fleet_orders "
        "WHERE id = $1 AND site_id = $2",
        order_id, site_id,
    )
    if row is None:
        raise TargetNotFound(f"no fleet_orders row id={order_id} site_id={site_id}")
    if row["order_type"] in PRIVILEGED_ORDER_TYPES:
        raise TargetRefInvalid(
            f"refusing to reconcile privileged order type {row['order_type']} — "
            "privileged orders carry attestation bundles and must go through fleet_cli."
        )
    if row["status"] == "completed":
        raise TargetNotActionable(
            f"fleet_orders[{order_id}] already completed — nothing to do"
        )
    await conn.execute(
        "UPDATE fleet_orders SET status = 'completed', completed_at = now(), "
        "result = jsonb_build_object('reconciled_by', 'substrate_action', 'reason', $2) "
        "WHERE id = $1",
        order_id, reason,
    )
    return {
        "order_id": order_id, "site_id": site_id,
        "order_type": row["order_type"],
        "prev_status": row["status"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_substrate_actions_reconcile.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_actions.py
git add mcp-server/central-command/backend/tests/test_substrate_actions_reconcile.py
git commit -m "feat(substrate): reconcile_fleet_order handler + privileged-type refusal"
```

---

### Task 6: POST endpoint with idempotency + audit

**Files:**
- Create: `mcp-server/central-command/backend/substrate_action_api.py`
- Modify: `mcp-server/central-command/backend/main.py` (mount router)
- Test: `mcp-server/central-command/backend/tests/test_substrate_action_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_action_endpoint.py
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_endpoint_rejects_unknown_action_key(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "delete_everything",
                  "target_ref": {}, "reason": ""},
            headers=admin_headers)
    assert r.status_code == 400
    body = r.json()
    assert "valid_keys" in body["detail"]
    assert set(body["detail"]["valid_keys"]) == {
        "cleanup_install_session", "unlock_platform_account", "reconcile_fleet_order"
    }

@pytest.mark.asyncio
async def test_endpoint_enforces_reason_length(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "unlock_platform_account",
                  "target_ref": {"table": "partners", "email": "a@b.c"},
                  "reason": "too short"},
            headers=admin_headers)
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_endpoint_idempotency_replay(admin_headers, seed_stale_install_session):
    mac = seed_stale_install_session["mac"]
    body = {"action_key": "cleanup_install_session",
            "target_ref": {"mac": mac, "stage": "live_usb"}, "reason": ""}
    headers = {**admin_headers, "Idempotency-Key": "test-key-abc"}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r1 = await ac.post("/api/admin/substrate/action", json=body, headers=headers)
        r2 = await ac.post("/api/admin/substrate/action", json=body, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["status"] == "completed"
    assert r2.status_code == 200
    assert r2.json()["status"] == "already_completed"
    assert r2.json()["action_id"] == r1.json()["action_id"]

@pytest.mark.asyncio
async def test_endpoint_requires_auth():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "cleanup_install_session",
                  "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"}, "reason": ""})
    assert r.status_code in (401, 403)

@pytest.mark.asyncio
async def test_endpoint_writes_one_audit_row(admin_headers, seed_stale_install_session):
    from shared import admin_connection
    mac = seed_stale_install_session["mac"]
    async with admin_connection() as conn:
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM admin_audit_log "
            "WHERE action = 'substrate.cleanup_install_session'"
        )
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "cleanup_install_session",
                  "target_ref": {"mac": mac}, "reason": ""},
            headers=admin_headers)
    assert r.status_code == 200
    async with admin_connection() as conn:
        after = await conn.fetchval(
            "SELECT COUNT(*) FROM admin_audit_log "
            "WHERE action = 'substrate.cleanup_install_session'"
        )
    assert after == before + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_action_endpoint.py -v`
Expected: FAIL — endpoint does not exist.

- [ ] **Step 3: Implement the endpoint**

```python
# substrate_action_api.py
"""POST /api/admin/substrate/action — scoped, non-operator-safe admin actions.

Handler registry: substrate_actions.SUBSTRATE_ACTIONS. No fleet order dispatch,
no customer infra mutation. See spec: Section 2.
"""

import hashlib
import logging
import os
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import require_auth
from shared import admin_connection
from admin_audit import admin_audit_log_append
from substrate_actions import (
    SUBSTRATE_ACTIONS,
    SubstrateActionError,
    TargetNotActionable,
    TargetNotFound,
    TargetRefInvalid,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/substrate", tags=["admin", "substrate"])

FEATURE_FLAG = os.getenv("SUBSTRATE_ACTIONS_ENABLED", "false").lower() == "true"


class ActionBody(BaseModel):
    action_key: str = Field(..., min_length=1, max_length=64)
    target_ref: dict[str, Any]
    reason: str = Field(default="")


def _derive_idempotency_key(body: ActionBody, actor_email: str, header_key: Optional[str]) -> str:
    if header_key:
        return header_key
    day = uuid.uuid5(uuid.NAMESPACE_URL, "utc-day").hex[:8]
    material = f"{actor_email}|{body.action_key}|{body.target_ref}|{day}"
    return hashlib.sha256(material.encode()).hexdigest()


@router.post("/action")
async def post_substrate_action(
    body: ActionBody,
    request: Request,
    user: dict = Depends(require_auth),
):
    if not FEATURE_FLAG:
        raise HTTPException(503, detail={"reason": "SUBSTRATE_ACTIONS_ENABLED is off"})
    action = SUBSTRATE_ACTIONS.get(body.action_key)
    if action is None:
        raise HTTPException(400, detail={
            "reason": "unknown action_key",
            "valid_keys": sorted(SUBSTRATE_ACTIONS.keys()),
        })
    if len(body.reason) < action.required_reason_chars:
        raise HTTPException(400, detail={
            "reason": f"reason must be >= {action.required_reason_chars} chars",
        })
    actor_email = user.get("email") or user.get("username") or ""
    if not actor_email:
        raise HTTPException(401, detail="no actor email on session")
    idem_key = _derive_idempotency_key(
        body, actor_email, request.headers.get("Idempotency-Key")
    )
    async with admin_connection() as conn:
        prior = await conn.fetchrow(
            "SELECT id, result_body FROM substrate_action_invocations "
            "WHERE actor_email = $1 AND idempotency_key = $2 "
            "AND created_at > now() - INTERVAL '24 hours'",
            actor_email, idem_key,
        )
        if prior is not None:
            reply = dict(prior["result_body"])
            reply["status"] = "already_completed"
            reply["action_id"] = str(prior["id"])
            return reply
        try:
            async with conn.transaction():
                summary = await action.handler(conn, body.target_ref, body.reason)
        except TargetRefInvalid as e:
            raise HTTPException(400, detail=str(e))
        except TargetNotFound as e:
            raise HTTPException(404, detail=str(e))
        except TargetNotActionable as e:
            raise HTTPException(409, detail=str(e))
        except SubstrateActionError as e:
            logger.error("substrate_action_failed", exc_info=True,
                         extra={"action_key": body.action_key, "actor": actor_email})
            raise HTTPException(500, detail=str(e))
        audit_id = await admin_audit_log_append(
            conn,
            action=action.audit_action,
            target=str(body.target_ref),
            actor=actor_email,
            details={"reason": body.reason, "target_ref": body.target_ref, "result": summary},
            request=request,
        )
        result_body = {"status": "completed", "details": summary}
        inv = await conn.fetchrow(
            "INSERT INTO substrate_action_invocations "
            "(idempotency_key, actor_email, action_key, target_ref, reason, "
            "result_status, result_body, admin_audit_id) "
            "VALUES ($1, $2, $3, $4, $5, 'completed', $6, $7) "
            "RETURNING id",
            idem_key, actor_email, body.action_key, body.target_ref, body.reason,
            result_body, audit_id,
        )
        return {"action_id": str(inv["id"]), **result_body}
```

In `main.py`, near other admin router mounts:

```python
from substrate_action_api import router as substrate_action_router
app.include_router(substrate_action_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SUBSTRATE_ACTIONS_ENABLED=true pytest tests/test_substrate_action_endpoint.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_action_api.py
git add mcp-server/central-command/backend/main.py
git add mcp-server/central-command/backend/tests/test_substrate_action_endpoint.py
git commit -m "feat(substrate): POST /api/admin/substrate/action with idempotency + audit"
```

---

### Task 7: Non-operator posture guardrail tests

**Files:**
- Create: `mcp-server/central-command/backend/tests/test_substrate_no_fleet_dispatch.py`
- Create: `mcp-server/central-command/backend/tests/test_substrate_privileged_rejected.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_substrate_no_fleet_dispatch.py
"""Negative test: no handler writes to fleet_orders.

Non-operator posture enforcement. If this fails, the substrate panel has
crossed a BAA line — treat as a P0 and revert.
"""
import pytest
from shared import admin_connection
from substrate_actions import SUBSTRATE_ACTIONS

@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", list(SUBSTRATE_ACTIONS.keys()))
async def test_handler_does_not_insert_fleet_order(
    action_key, happy_path_target_ref_for
):
    target_ref = happy_path_target_ref_for(action_key)
    reason = "Posture guardrail test — confirming no fleet_orders INSERT"
    async with admin_connection() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
        async with conn.transaction():
            action = SUBSTRATE_ACTIONS[action_key]
            try:
                await action.handler(conn, target_ref, reason)
            except Exception:
                pass
        after = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
    assert after == before, (
        f"action {action_key} inserted into fleet_orders — "
        "this violates non-operator posture. See spec Section 2."
    )
```

```python
# tests/test_substrate_privileged_rejected.py
"""Privileged order types (signing_key_rotation, bulk_remediation, etc.) must
never be reachable via the substrate endpoint. Only fleet_cli can dispatch them.
"""
import pytest
from httpx import AsyncClient
from fleet_cli import PRIVILEGED_ORDER_TYPES
from main import app

@pytest.mark.asyncio
@pytest.mark.parametrize("privileged_type", sorted(PRIVILEGED_ORDER_TYPES))
async def test_privileged_action_key_rejected(privileged_type, admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": privileged_type,
                  "target_ref": {}, "reason": "x" * 25},
            headers=admin_headers)
    assert r.status_code == 400, (
        f"privileged type {privileged_type} was accepted by substrate endpoint — "
        "MUST stay in fleet_cli per chain-of-custody"
    )
```

- [ ] **Step 2: Run tests to verify they fail (if bug exists) or pass (confirming guardrail)**

Run:
```bash
SUBSTRATE_ACTIONS_ENABLED=true pytest tests/test_substrate_no_fleet_dispatch.py tests/test_substrate_privileged_rejected.py -v
```
Expected: PASS — current handlers do not touch fleet_orders and registry does not alias privileged types. (If any test fails here, STOP and revert — posture break.)

- [ ] **Step 3: Add `happy_path_target_ref_for` fixture**

In `tests/conftest.py` add:

```python
@pytest.fixture
def happy_path_target_ref_for(
    seed_stale_install_session, seed_locked_partner, seed_active_fleet_order,
):
    mapping = {
        "cleanup_install_session": {"mac": seed_stale_install_session["mac"]},
        "unlock_platform_account": {
            "table": "partners", "email": seed_locked_partner["email"]
        },
        "reconcile_fleet_order": {
            "order_id": seed_active_fleet_order["order_id"],
            "site_id": seed_active_fleet_order["site_id"],
        },
    }
    return lambda action_key: mapping[action_key]
```

- [ ] **Step 4: Re-run both tests**

Run:
```bash
pytest tests/test_substrate_no_fleet_dispatch.py tests/test_substrate_privileged_rejected.py -v
```
Expected: PASS across all parametrized cases.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/tests/test_substrate_no_fleet_dispatch.py
git add mcp-server/central-command/backend/tests/test_substrate_privileged_rejected.py
git add mcp-server/central-command/backend/tests/conftest.py
git commit -m "test(substrate): non-operator posture guardrails — no fleet dispatch, no privileged"
```

---

### Task 8: Doc stub generator

**Files:**
- Create: `mcp-server/central-command/backend/scripts/generate_substrate_doc_stubs.py`
- Create: `docs/substrate/_TEMPLATE.md`

- [ ] **Step 1: Write the template**

`docs/substrate/_TEMPLATE.md`:

```markdown
# {{invariant}}

**Severity:** {{severity}}
**Display name:** {{display_name}}

## What this means (plain English)

TODO — 2–4 sentences, operator audience, not engineer.

## Root cause categories

- TODO — most common cause
- TODO
- TODO

## Immediate action

- If the **Run action** button exists on the panel: TODO describe.
- Otherwise: run
  ```
  fleet_cli ... --actor-email you@example.com --reason "..."
  ```

## Verification

- Panel: invariant row should clear on next 60s tick.
- CLI: TODO query.

## Escalation

TODO — when NOT to auto-fix. Signals that suggest a real security event.

## Related runbooks

- TODO

## Change log

- {{today}} — generated — stub created
```

- [ ] **Step 2: Write the generator**

`scripts/generate_substrate_doc_stubs.py`:

```python
"""Generate one docs/substrate/<invariant>.md stub per entry in
assertions.ALL_ASSERTIONS. Re-run is safe — never overwrites a populated file
(skips if file > template size + 100 bytes of prose).

Usage: python3 scripts/generate_substrate_doc_stubs.py
"""

import datetime as dt
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from assertions import ALL_ASSERTIONS, _DISPLAY_METADATA  # noqa: E402

DOCS_ROOT = HERE.parent.parent.parent / "docs" / "substrate"
TEMPLATE = (DOCS_ROOT / "_TEMPLATE.md").read_text()


def main() -> int:
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    created = 0
    for assertion in ALL_ASSERTIONS:
        name = assertion.name
        target = DOCS_ROOT / f"{name}.md"
        meta = _DISPLAY_METADATA.get(name, {})
        if target.exists() and target.stat().st_size > len(TEMPLATE) + 100:
            continue
        body = (
            TEMPLATE
            .replace("{{invariant}}", name)
            .replace("{{severity}}", f"sev{assertion.severity}")
            .replace("{{display_name}}", meta.get("display_name", name))
            .replace("{{today}}", today)
        )
        target.write_text(body)
        created += 1
    print(f"Created/updated {created} stub(s) across {len(ALL_ASSERTIONS)} invariants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run generator**

```bash
cd mcp-server/central-command/backend
python3 scripts/generate_substrate_doc_stubs.py
```
Expected: "Created/updated 33 stub(s) across 33 invariants." then `docs/substrate/` has 33 `.md` files plus `_TEMPLATE.md`.

- [ ] **Step 4: Verify stubs**

```bash
ls docs/substrate/ | wc -l   # expect 34 (33 stubs + _TEMPLATE.md)
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/scripts/generate_substrate_doc_stubs.py
git add docs/substrate/_TEMPLATE.md
git add docs/substrate/*.md
git commit -m "docs(substrate): generator + 33 runbook stubs (one per invariant)"
```

---

### Task 9: CI gate — docs lockstep

**Files:**
- Create: `mcp-server/central-command/backend/tests/test_substrate_docs_present.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_docs_present.py
"""CI gate: every assertions.ALL_ASSERTIONS entry has a docs/substrate/<name>.md
file containing required section headings. Fails build on drift.
"""
import pathlib
import pytest
from assertions import ALL_ASSERTIONS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCS_DIR = REPO_ROOT / "docs" / "substrate"

REQUIRED_SECTIONS = [
    "## What this means",
    "## Root cause categories",
    "## Immediate action",
    "## Verification",
    "## Escalation",
    "## Related runbooks",
    "## Change log",
]


@pytest.mark.parametrize("assertion", ALL_ASSERTIONS, ids=lambda a: a.name)
def test_doc_exists_and_has_sections(assertion):
    path = DOCS_DIR / f"{assertion.name}.md"
    assert path.exists(), (
        f"Missing runbook doc: {path}. "
        "Run `python3 scripts/generate_substrate_doc_stubs.py` then fill in prose."
    )
    body = path.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in body, (
            f"Runbook {path} missing required section: {section!r}"
        )


def test_no_orphaned_docs():
    known = {a.name for a in ALL_ASSERTIONS}
    known.add("_TEMPLATE")
    for md_file in DOCS_DIR.glob("*.md"):
        stem = md_file.stem
        assert stem in known, (
            f"Orphan runbook file: {md_file}. "
            "Remove it or add a matching invariant to ALL_ASSERTIONS."
        )
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_substrate_docs_present.py -v`
Expected: PASS (Task 8 already generated all stubs).

- [ ] **Step 3: Wire into CI**

Add to `.github/workflows/backend-ci.yml` or equivalent (in the pytest step):
No change needed — the test is picked up by the existing `pytest tests/` run.

- [ ] **Step 4: Confirm CI gate fires on drift**

Manually test: remove one stub, run pytest, confirm failure, restore stub.

```bash
mv docs/substrate/install_loop.md /tmp/
pytest tests/test_substrate_docs_present.py -v    # expect FAIL on install_loop
mv /tmp/install_loop.md docs/substrate/
pytest tests/test_substrate_docs_present.py -v    # expect PASS
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/tests/test_substrate_docs_present.py
git commit -m "test(substrate): CI gate — doc present per ALL_ASSERTIONS invariant"
```

---

### Task 10: GET runbook endpoint

**Files:**
- Modify: `mcp-server/central-command/backend/substrate_action_api.py` (add GET route)
- Test: `mcp-server/central-command/backend/tests/test_substrate_runbook_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_runbook_endpoint.py
import pytest
from httpx import AsyncClient
from main import app
from assertions import ALL_ASSERTIONS

@pytest.mark.asyncio
async def test_runbook_returns_markdown_for_known_invariant(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbook/install_loop",
                         headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["invariant"] == "install_loop"
    assert body["severity"].startswith("sev")
    assert "## What this means" in body["markdown"]

@pytest.mark.asyncio
async def test_runbook_404_for_unknown_invariant(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbook/does_not_exist",
                         headers=admin_headers)
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_runbook_rejects_path_traversal(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbook/..%2F..%2Fetc%2Fpasswd",
                         headers=admin_headers)
    assert r.status_code in (400, 404)

@pytest.mark.asyncio
async def test_runbook_requires_auth():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbook/install_loop")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_substrate_runbook_endpoint.py -v`
Expected: FAIL — no route.

- [ ] **Step 3: Implement the GET route**

Append to `substrate_action_api.py`:

```python
import pathlib
import re
from assertions import ALL_ASSERTIONS, _DISPLAY_METADATA

_DOCS_DIR = pathlib.Path(__file__).resolve().parents[3] / "docs" / "substrate"
_KNOWN_INVARIANTS = {a.name for a in ALL_ASSERTIONS}
_SAFE_NAME = re.compile(r"^[a-z0-9_]+$")


@router.get("/runbook/{invariant}")
async def get_runbook(invariant: str, user: dict = Depends(require_auth)):
    if not _SAFE_NAME.match(invariant):
        raise HTTPException(400, detail="invariant name must match ^[a-z0-9_]+$")
    if invariant not in _KNOWN_INVARIANTS:
        raise HTTPException(404, detail=f"unknown invariant: {invariant}")
    path = _DOCS_DIR / f"{invariant}.md"
    if not path.exists():
        raise HTTPException(404, detail=f"doc missing: docs/substrate/{invariant}.md")
    meta = _DISPLAY_METADATA.get(invariant, {})
    return {
        "invariant": invariant,
        "display_name": meta.get("display_name", invariant),
        "severity": f"sev{meta.get('severity', 2)}",
        "markdown": path.read_text(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_substrate_runbook_endpoint.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_action_api.py
git add mcp-server/central-command/backend/tests/test_substrate_runbook_endpoint.py
git commit -m "feat(substrate): GET /api/admin/substrate/runbook/<invariant>"
```

---

### Task 11: Frontend dep — react-markdown + rehype-sanitize

**Files:**
- Modify: `mcp-server/central-command/frontend/package.json`
- Modify: `mcp-server/central-command/frontend/package-lock.json`

- [ ] **Step 1: Install deps**

```bash
cd mcp-server/central-command/frontend
npm install react-markdown@9 rehype-sanitize@6
```

- [ ] **Step 2: Verify lockfile updated and tsc still clean**

```bash
npm run typecheck
```
Expected: no errors.

- [ ] **Step 3: Record versions**

```bash
npm ls react-markdown rehype-sanitize
```
Expected output shows `react-markdown@9.x.x` and `rehype-sanitize@6.x.x`.

- [ ] **Step 4: No runtime test yet — components in later tasks cover**

(skip)

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/package.json
git add mcp-server/central-command/frontend/package-lock.json
git commit -m "deps(frontend): react-markdown@9 + rehype-sanitize@6 for substrate runbooks"
```

---

### Task 12: RunbookDrawer component

**Files:**
- Create: `mcp-server/central-command/frontend/src/components/substrate/RunbookDrawer.tsx`
- Create: `mcp-server/central-command/frontend/src/components/substrate/RunbookDrawer.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// RunbookDrawer.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import RunbookDrawer from "./RunbookDrawer";

beforeEach(() => {
  global.fetch = vi.fn(async () => new Response(JSON.stringify({
    invariant: "install_loop",
    display_name: "Install Loop",
    severity: "sev1",
    markdown: "## What this means\nThe installer is looping on a machine.\n"
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("RunbookDrawer", () => {
  it("renders rehype-sanitize-safe markdown", async () => {
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="install_loop" onClose={() => {}} />
      </QueryClientProvider>
    );
    await waitFor(() => expect(
      screen.getByText("The installer is looping on a machine.")
    ).toBeInTheDocument());
  });

  it("strips raw HTML script tags", async () => {
    (global.fetch as any) = vi.fn(async () => new Response(JSON.stringify({
      invariant: "x", display_name: "x", severity: "sev1",
      markdown: "## x\n<script>alert(1)</script>\nhello"
    }), { status: 200 }));
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="x" onClose={() => {}} />
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
    expect(document.querySelector("script[data-test]")).toBeNull();
  });

  it("shows error state on 404", async () => {
    (global.fetch as any) = vi.fn(async () => new Response(
      JSON.stringify({ detail: "unknown invariant: foo" }), { status: 404 }
    ));
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="foo" onClose={() => {}} />
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText(/unknown invariant/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server/central-command/frontend && npm test -- RunbookDrawer`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement component**

```tsx
// RunbookDrawer.tsx
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import { fetchApi } from "../../lib/api";

type Props = { invariant: string; onClose: () => void };

type RunbookResponse = {
  invariant: string;
  display_name: string;
  severity: string;
  markdown: string;
};

export default function RunbookDrawer({ invariant, onClose }: Props) {
  const { data, error, isLoading } = useQuery<RunbookResponse>({
    queryKey: ["substrate-runbook", invariant],
    queryFn: () => fetchApi<RunbookResponse>(`/api/admin/substrate/runbook/${invariant}`),
    staleTime: 5 * 60 * 1000,
  });

  const deepLink = `${window.location.origin}/admin/substrate/runbook/${invariant}`;

  return (
    <aside className="fixed right-0 top-0 h-full w-[720px] max-w-[95vw] bg-white/5 backdrop-blur-lg border-l border-white/10 z-50 overflow-y-auto p-6 text-white">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold">{data?.display_name ?? invariant}</h2>
          <p className="text-xs text-white/60">{data?.severity ?? ""} · {invariant}</p>
        </div>
        <div className="flex gap-2">
          <button
            className="px-3 py-1 rounded bg-white/10 hover:bg-white/20 text-sm"
            onClick={() => navigator.clipboard.writeText(deepLink)}
          >Copy link</button>
          <button
            className="px-3 py-1 rounded bg-white/10 hover:bg-white/20 text-sm"
            onClick={onClose}
          >Close</button>
        </div>
      </header>
      {isLoading && <p className="text-white/70">Loading…</p>}
      {error && <p className="text-red-300">{(error as Error).message}</p>}
      {data && (
        <article className="prose prose-invert max-w-none">
          <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
            {data.markdown}
          </ReactMarkdown>
        </article>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- RunbookDrawer`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/src/components/substrate/RunbookDrawer.tsx
git add mcp-server/central-command/frontend/src/components/substrate/RunbookDrawer.test.tsx
git commit -m "feat(frontend): RunbookDrawer — sanitized markdown rendering"
```

---

### Task 13: CopyCliButton component

**Files:**
- Create: `mcp-server/central-command/frontend/src/components/substrate/CopyCliButton.tsx`
- Create: `mcp-server/central-command/frontend/src/components/substrate/CopyCliButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// CopyCliButton.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CopyCliButton from "./CopyCliButton";

beforeEach(() => {
  Object.defineProperty(global.navigator, "clipboard", {
    value: { writeText: vi.fn(async () => {}) }, configurable: true,
  });
});

describe("CopyCliButton", () => {
  it("substitutes site_id and mac from details into template", async () => {
    render(<CopyCliButton
      template={'fleet_cli create update_daemon --site-id {site_id} --mac {mac} --actor-email YOU@example.com --reason "..."'}
      details={{ site_id: "abc-123", mac: "aa:bb:cc:dd:ee:ff" }}
    />);
    fireEvent.click(screen.getByRole("button", { name: /copy cli/i }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'fleet_cli create update_daemon --site-id abc-123 --mac aa:bb:cc:dd:ee:ff --actor-email YOU@example.com --reason "..."'
    ));
  });

  it("shows run-locally reminder after copy", async () => {
    render(<CopyCliButton template="fleet_cli status" details={{}} />);
    fireEvent.click(screen.getByRole("button", { name: /copy cli/i }));
    await waitFor(() => expect(
      screen.getByText(/run under your own --actor-email/i)
    ).toBeInTheDocument());
  });

  it("does not render when template is empty", () => {
    render(<CopyCliButton template="" details={{}} />);
    expect(screen.queryByRole("button", { name: /copy cli/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- CopyCliButton`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement component**

```tsx
// CopyCliButton.tsx
import { useState } from "react";

type Props = {
  template: string;
  details: Record<string, unknown>;
};

function substitute(template: string, details: Record<string, unknown>): string {
  return template.replace(/\{([a-z_]+)\}/g, (_, key) => {
    const v = details[key];
    return v === undefined || v === null ? `{${key}}` : String(v);
  });
}

export default function CopyCliButton({ template, details }: Props) {
  const [copied, setCopied] = useState(false);
  if (!template) return null;
  const cmd = substitute(template, details);
  const handle = async () => {
    await navigator.clipboard.writeText(cmd);
    setCopied(true);
    setTimeout(() => setCopied(false), 3000);
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button
        onClick={handle}
        className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20"
      >Copy CLI</button>
      {copied && (
        <span className="text-xs text-amber-200">
          Copied — run under your own --actor-email
        </span>
      )}
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- CopyCliButton`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/src/components/substrate/CopyCliButton.tsx
git add mcp-server/central-command/frontend/src/components/substrate/CopyCliButton.test.tsx
git commit -m "feat(frontend): CopyCliButton with template substitution + run-locally toast"
```

---

### Task 14: ActionPreviewModal component

**Files:**
- Create: `mcp-server/central-command/frontend/src/components/substrate/ActionPreviewModal.tsx`
- Create: `mcp-server/central-command/frontend/src/components/substrate/ActionPreviewModal.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// ActionPreviewModal.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import ActionPreviewModal from "./ActionPreviewModal";

beforeEach(() => {
  global.fetch = vi.fn(async () => new Response(JSON.stringify({
    action_id: "42", status: "completed", details: { deleted: 1, mac: "aa:bb" }
  }), { status: 200 }));
});

describe("ActionPreviewModal", () => {
  it("disables confirm until reason >=20 chars + initials present", () => {
    render(<ActionPreviewModal
      actionKey="unlock_platform_account"
      requiredReasonChars={20}
      plan="Unlock partners.email=a@b.c"
      targetRef={{ table: "partners", email: "a@b.c" }}
      onClose={() => {}}
      onDone={() => {}}
    />);
    const confirm = screen.getByRole("button", { name: /confirm/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "Short" }
    });
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "Confirmed via phone call, user is legit" }
    });
    expect(confirm).not.toBeDisabled();
  });

  it("POSTs to endpoint and shows action_id on success", async () => {
    const onDone = vi.fn();
    render(<ActionPreviewModal
      actionKey="cleanup_install_session"
      requiredReasonChars={0}
      plan="Delete stale install_sessions row for aa:bb"
      targetRef={{ mac: "aa:bb" }}
      onClose={() => {}}
      onDone={onDone}
    />);
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText(/action_id.*42/)).toBeInTheDocument());
    expect(onDone).toHaveBeenCalledWith("42");
  });

  it("shows error body + CLI fallback on failure", async () => {
    (global.fetch as any) = vi.fn(async () => new Response(
      JSON.stringify({ detail: "no install_sessions row for mac=zz" }), { status: 404 }
    ));
    render(<ActionPreviewModal
      actionKey="cleanup_install_session"
      requiredReasonChars={0}
      plan="Delete stale row"
      targetRef={{ mac: "zz" }}
      onClose={() => {}}
      onDone={() => {}}
      cliFallback="fleet_cli --actor-email you@x"
    />);
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText(/no install_sessions row/)).toBeInTheDocument());
    expect(screen.getByText(/fleet_cli --actor-email you@x/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- ActionPreviewModal`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement component**

```tsx
// ActionPreviewModal.tsx
import { useState } from "react";
import { fetchApi } from "../../lib/api";

type Props = {
  actionKey: string;
  requiredReasonChars: number;
  plan: string;
  targetRef: Record<string, unknown>;
  cliFallback?: string;
  onClose: () => void;
  onDone: (actionId: string) => void;
};

export default function ActionPreviewModal(p: Props) {
  const [reason, setReason] = useState("");
  const [initials, setInitials] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ action_id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    !submitting &&
    initials.trim().length >= 2 &&
    initials.trim().length <= 4 &&
    reason.trim().length >= p.requiredReasonChars;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const resp = await fetchApi<{ action_id: string; status: string }>(
        "/api/admin/substrate/action",
        {
          method: "POST",
          headers: { "Idempotency-Key": `${p.actionKey}-${Date.now()}-${initials}` },
          body: JSON.stringify({
            action_key: p.actionKey,
            target_ref: p.targetRef,
            reason: `[${initials.toUpperCase()}] ${reason}`.trim(),
          }),
        }
      );
      setResult({ action_id: resp.action_id });
      p.onDone(resp.action_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center">
      <div className="bg-slate-900 border border-white/10 rounded-lg w-[560px] max-w-[92vw] p-6 text-white">
        <h2 className="text-lg font-semibold mb-2">Preview: {p.actionKey}</h2>
        <p className="text-sm text-white/80 mb-4 whitespace-pre-wrap">{p.plan}</p>

        {p.requiredReasonChars > 0 && (
          <label className="block mb-3 text-sm">
            Reason (min {p.requiredReasonChars} chars)
            <textarea
              aria-label="reason"
              className="mt-1 w-full p-2 bg-white/5 rounded border border-white/10"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
            <span className="text-xs text-white/50">{reason.length} chars</span>
          </label>
        )}

        <label className="block mb-4 text-sm">
          Your initials (2–4 chars, saved to audit log)
          <input
            aria-label="initials"
            className="mt-1 w-24 p-2 bg-white/5 rounded border border-white/10"
            value={initials}
            onChange={(e) => setInitials(e.target.value.slice(0, 4))}
          />
        </label>

        {result && (
          <p className="mb-3 text-green-300 text-sm">
            Done — action_id {result.action_id}
          </p>
        )}
        {error && (
          <div className="mb-3 text-sm">
            <p className="text-red-300">{error}</p>
            {p.cliFallback && (
              <pre className="mt-2 text-xs bg-black/50 p-2 rounded whitespace-pre-wrap">
                {p.cliFallback}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={p.onClose} className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40"
          >Confirm</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- ActionPreviewModal`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/src/components/substrate/ActionPreviewModal.tsx
git add mcp-server/central-command/frontend/src/components/substrate/ActionPreviewModal.test.tsx
git commit -m "feat(frontend): ActionPreviewModal — reason gate + initials + error with CLI fallback"
```

---

### Task 15: Upgrade AdminSubstrateHealth panel

**Files:**
- Modify: `mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.tsx`
- Modify: `mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.test.tsx` (create if missing)

- [ ] **Step 1: Write the failing test**

```tsx
// AdminSubstrateHealth.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AdminSubstrateHealth from "./AdminSubstrateHealth";

const mockViolations = [
  { id: 1, invariant_name: "install_loop", display_name: "Install Loop",
    severity: 1, recommended_action: "", site_id: null, details: { mac: "aa:bb" },
    created_at: "2026-04-19T12:00:00Z" },
  { id: 2, invariant_name: "auth_failure_lockout", display_name: "Account Lockout",
    severity: 1, recommended_action: "", site_id: null,
    details: { table: "partners", email: "x@y.z" },
    created_at: "2026-04-19T12:00:00Z" },
  { id: 3, invariant_name: "vps_disk_pressure", display_name: "VPS Disk Pressure",
    severity: 2, recommended_action: "free disk", site_id: null, details: {},
    created_at: "2026-04-19T12:00:00Z" },
];

beforeEach(() => {
  global.fetch = vi.fn(async (url: string) => {
    if (url.includes("/admin/substrate-violations")) {
      return new Response(JSON.stringify({ violations: mockViolations }),
        { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/admin/substrate/runbook/")) {
      return new Response(JSON.stringify({
        invariant: "install_loop", display_name: "Install Loop",
        severity: "sev1", markdown: "## What this means\nbody"
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as any;
});

const wrap = (ui: React.ReactNode) => render(
  <MemoryRouter>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>
  </MemoryRouter>
);

describe("AdminSubstrateHealth upgrades", () => {
  it("shows Run action only on whitelisted invariants", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => expect(screen.getByText("Install Loop")).toBeInTheDocument());
    // install_loop has action
    expect(screen.getAllByRole("button", { name: /run action/i }).length).toBeGreaterThanOrEqual(2);
    // vps_disk_pressure does NOT have action — its row must not include Run action
    const vpsRow = screen.getByText("VPS Disk Pressure").closest("[data-testid='violation-row']")!;
    expect(vpsRow.querySelector("[data-action='run']")).toBeNull();
  });

  it("every row has View runbook", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /view runbook/i }))
      .toHaveLength(3));
  });

  it("opens drawer when View runbook clicked", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => screen.getByText("Install Loop"));
    fireEvent.click(screen.getAllByRole("button", { name: /view runbook/i })[0]);
    await waitFor(() => expect(screen.getByText("body")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- AdminSubstrateHealth`
Expected: FAIL — buttons do not exist yet.

- [ ] **Step 3: Upgrade the page**

Open `AdminSubstrateHealth.tsx` and:

1. Add imports:
```tsx
import { useState } from "react";
import RunbookDrawer from "../components/substrate/RunbookDrawer";
import CopyCliButton from "../components/substrate/CopyCliButton";
import ActionPreviewModal from "../components/substrate/ActionPreviewModal";
```

2. Add the whitelist mapping (keep this tight — only 3 keys):
```tsx
type ActionConfig = { actionKey: string; requiredReasonChars: number;
  cliFallback?: string; buildPlan: (details: any) => string;
  buildTargetRef: (details: any) => Record<string, unknown> };

const INVARIANT_ACTIONS: Record<string, ActionConfig> = {
  install_loop: {
    actionKey: "cleanup_install_session", requiredReasonChars: 0,
    buildPlan: (d) => `Delete install_sessions row where mac=${d.mac}. Idempotent.`,
    buildTargetRef: (d) => ({ mac: d.mac, stage: d.stage }),
  },
  install_session_ttl: {
    actionKey: "cleanup_install_session", requiredReasonChars: 0,
    buildPlan: (d) => `Delete install_sessions row where mac=${d.mac}. Idempotent.`,
    buildTargetRef: (d) => ({ mac: d.mac, stage: d.stage }),
  },
  auth_failure_lockout: {
    actionKey: "unlock_platform_account", requiredReasonChars: 20,
    buildPlan: (d) => `Unlock ${d.table}.email=${d.email}. Clears failed_login_attempts and locked_until.`,
    buildTargetRef: (d) => ({ table: d.table, email: d.email }),
  },
  agent_version_lag: {
    actionKey: "reconcile_fleet_order", requiredReasonChars: 20,
    cliFallback: "fleet_cli orders cancel ...",
    buildPlan: (d) => `Mark fleet_orders[${d.order_id}] as completed. Use ONLY when upgrade was verified out-of-band.`,
    buildTargetRef: (d) => ({ order_id: d.order_id, site_id: d.site_id }),
  },
};

const CLI_TEMPLATES: Record<string, string> = {
  install_loop: "",
  offline_appliance_over_1h: "mcp-server/central-command/backend/scripts/recover_legacy_appliance.sh {site_id} {mac} {ip}",
  agent_version_lag: "fleet_cli create update_daemon --site-id {site_id} --param appliance_id={appliance_id} --param binary_url={binary_url} --actor-email YOU@example.com --reason \"...\"",
  // ... fill in per Appendix A of the spec; empty string = no Copy-CLI button
};
```

3. Add state + render the new buttons in the violation row JSX:

```tsx
const [drawer, setDrawer] = useState<string | null>(null);
const [modal, setModal] = useState<{ cfg: ActionConfig; details: any } | null>(null);
// ... inside the violations.map row:
<td data-testid="violation-row">
  {/* existing cells */}
  <div className="flex gap-2 flex-wrap">
    <button
      onClick={() => setDrawer(v.invariant_name)}
      className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20"
    >View runbook</button>
    <CopyCliButton
      template={CLI_TEMPLATES[v.invariant_name] ?? ""}
      details={v.details}
    />
    {INVARIANT_ACTIONS[v.invariant_name] && (
      <button
        data-action="run"
        onClick={() => setModal({ cfg: INVARIANT_ACTIONS[v.invariant_name], details: v.details })}
        className="px-2 py-1 text-xs rounded bg-emerald-600/80 hover:bg-emerald-500"
      >Run action</button>
    )}
  </div>
</td>
{drawer && <RunbookDrawer invariant={drawer} onClose={() => setDrawer(null)} />}
{modal && (
  <ActionPreviewModal
    actionKey={modal.cfg.actionKey}
    requiredReasonChars={modal.cfg.requiredReasonChars}
    plan={modal.cfg.buildPlan(modal.details)}
    targetRef={modal.cfg.buildTargetRef(modal.details)}
    cliFallback={modal.cfg.cliFallback}
    onClose={() => setModal(null)}
    onDone={() => { setModal(null); /* react-query refetch */ }}
  />
)}
```

- [ ] **Step 4: Run tests**

```bash
npm test -- AdminSubstrateHealth
npm run typecheck
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.tsx
git add mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.test.tsx
git commit -m "feat(frontend): AdminSubstrateHealth — Runbook + Copy-CLI + Run action buttons"
```

---

### Task 16: Runbook Library page

**Files:**
- Create: `mcp-server/central-command/frontend/src/pages/SubstrateRunbookLibrary.tsx`
- Create: `mcp-server/central-command/frontend/src/pages/SubstrateRunbookLibrary.test.tsx`
- Modify: `mcp-server/central-command/frontend/src/App.tsx` (add route)
- Modify: `mcp-server/central-command/backend/substrate_action_api.py` (add listing endpoint)
- Create: `mcp-server/central-command/backend/tests/test_substrate_runbook_index.py`

- [ ] **Step 1: Write backend failing test**

```python
# tests/test_substrate_runbook_index.py
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_index_returns_all_invariants(admin_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbooks", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    from assertions import ALL_ASSERTIONS
    assert {i["invariant"] for i in items} == {a.name for a in ALL_ASSERTIONS}
    for item in items:
        assert "display_name" in item
        assert item["severity"].startswith("sev")
        assert "has_action" in item
```

- [ ] **Step 2: Run backend test to verify it fails**

Run: `pytest tests/test_substrate_runbook_index.py -v`
Expected: FAIL — no route.

- [ ] **Step 3: Add the listing endpoint**

Append to `substrate_action_api.py`:

```python
from substrate_actions import SUBSTRATE_ACTIONS

_ACTION_WHITELIST_MAP = {
    "install_loop": "cleanup_install_session",
    "install_session_ttl": "cleanup_install_session",
    "auth_failure_lockout": "unlock_platform_account",
    "agent_version_lag": "reconcile_fleet_order",
}


@router.get("/runbooks")
async def list_runbooks(user: dict = Depends(require_auth)):
    items = []
    for a in ALL_ASSERTIONS:
        meta = _DISPLAY_METADATA.get(a.name, {})
        items.append({
            "invariant": a.name,
            "display_name": meta.get("display_name", a.name),
            "severity": f"sev{a.severity}",
            "has_action": a.name in _ACTION_WHITELIST_MAP,
            "action_key": _ACTION_WHITELIST_MAP.get(a.name),
        })
    return {"items": items}
```

- [ ] **Step 4: Run backend test — expect PASS**

Run: `pytest tests/test_substrate_runbook_index.py -v`
Expected: PASS.

- [ ] **Step 5: Write frontend failing test**

```tsx
// SubstrateRunbookLibrary.test.tsx
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import SubstrateRunbookLibrary from "./SubstrateRunbookLibrary";

beforeEach(() => {
  global.fetch = vi.fn(async (url: string) => {
    if (url.includes("/admin/substrate/runbooks")) {
      return new Response(JSON.stringify({ items: [
        { invariant: "install_loop", display_name: "Install Loop", severity: "sev1", has_action: true, action_key: "cleanup_install_session" },
        { invariant: "vps_disk_pressure", display_name: "VPS Disk Pressure", severity: "sev2", has_action: false, action_key: null },
      ]}), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as any;
});

describe("SubstrateRunbookLibrary", () => {
  it("renders grid with all invariants", async () => {
    render(<MemoryRouter><QueryClientProvider client={new QueryClient()}>
      <SubstrateRunbookLibrary /></QueryClientProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("Install Loop")).toBeInTheDocument());
    expect(screen.getByText("VPS Disk Pressure")).toBeInTheDocument();
  });

  it("filters by has-action", async () => {
    render(<MemoryRouter><QueryClientProvider client={new QueryClient()}>
      <SubstrateRunbookLibrary /></QueryClientProvider></MemoryRouter>);
    await waitFor(() => screen.getByText("Install Loop"));
    fireEvent.click(screen.getByLabelText(/only with action/i));
    expect(screen.getByText("Install Loop")).toBeInTheDocument();
    expect(screen.queryByText("VPS Disk Pressure")).toBeNull();
  });
});
```

- [ ] **Step 6: Implement library page**

```tsx
// SubstrateRunbookLibrary.tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchApi } from "../lib/api";
import RunbookDrawer from "../components/substrate/RunbookDrawer";

type Item = { invariant: string; display_name: string; severity: string;
  has_action: boolean; action_key: string | null };

export default function SubstrateRunbookLibrary() {
  const [onlyAction, setOnlyAction] = useState(false);
  const [severity, setSeverity] = useState<string>("all");
  const [drawer, setDrawer] = useState<string | null>(null);
  const { data } = useQuery<{ items: Item[] }>({
    queryKey: ["substrate-runbooks-index"],
    queryFn: () => fetchApi("/api/admin/substrate/runbooks"),
  });
  const items = (data?.items ?? [])
    .filter(i => !onlyAction || i.has_action)
    .filter(i => severity === "all" || i.severity === severity);

  return (
    <div className="p-6 text-white">
      <h1 className="text-2xl font-semibold mb-4">Substrate Runbook Library</h1>
      <div className="flex gap-4 mb-4 text-sm">
        <label><input type="checkbox" checked={onlyAction}
          onChange={e => setOnlyAction(e.target.checked)} aria-label="only with action"
          /> Only with action</label>
        <select value={severity} onChange={e => setSeverity(e.target.value)}
          className="bg-slate-800 border border-white/10 rounded px-2 py-1">
          <option value="all">All severities</option>
          <option value="sev1">sev1</option>
          <option value="sev2">sev2</option>
          <option value="sev3">sev3</option>
        </select>
      </div>
      <table className="w-full text-sm">
        <thead className="text-white/60">
          <tr><th className="text-left p-2">Invariant</th><th>Severity</th><th>Action?</th><th/></tr>
        </thead>
        <tbody>
          {items.map(i => (
            <tr key={i.invariant} className="border-t border-white/10">
              <td className="p-2">{i.display_name}<div className="text-xs text-white/50">{i.invariant}</div></td>
              <td className="p-2">{i.severity}</td>
              <td className="p-2">{i.has_action ? i.action_key : "—"}</td>
              <td className="p-2"><button className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20"
                onClick={() => setDrawer(i.invariant)}>View runbook</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {drawer && <RunbookDrawer invariant={drawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
```

- [ ] **Step 7: Add route in App.tsx**

Near the existing `AdminSubstrateHealth` route entry:

```tsx
const SubstrateRunbookLibrary = lazy(() => import("./pages/SubstrateRunbookLibrary"));
// ...
<Route path="/admin/substrate/runbooks" element={<SubstrateRunbookLibrary />} />
```

- [ ] **Step 8: Run frontend + backend tests**

```bash
npm test -- SubstrateRunbookLibrary
npm run typecheck
```

- [ ] **Step 9: Commit**

```bash
git add mcp-server/central-command/backend/substrate_action_api.py
git add mcp-server/central-command/backend/tests/test_substrate_runbook_index.py
git add mcp-server/central-command/frontend/src/pages/SubstrateRunbookLibrary.tsx
git add mcp-server/central-command/frontend/src/pages/SubstrateRunbookLibrary.test.tsx
git add mcp-server/central-command/frontend/src/App.tsx
git commit -m "feat(substrate): runbook library index endpoint + /admin/substrate/runbooks page"
```

---

### Task 17: Deep-link runbook page

**Files:**
- Create: `mcp-server/central-command/frontend/src/pages/SubstrateRunbookPage.tsx`
- Modify: `mcp-server/central-command/frontend/src/App.tsx`

- [ ] **Step 1: Write minimal failing test**

```tsx
// SubstrateRunbookPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import SubstrateRunbookPage from "./SubstrateRunbookPage";

beforeEach(() => {
  global.fetch = vi.fn(async () => new Response(JSON.stringify({
    invariant: "install_loop", display_name: "Install Loop",
    severity: "sev1", markdown: "## What this means\nbody"
  }), { status: 200 })) as any;
});

describe("SubstrateRunbookPage", () => {
  it("renders the runbook by URL param", async () => {
    render(
      <MemoryRouter initialEntries={["/admin/substrate/runbook/install_loop"]}>
        <QueryClientProvider client={new QueryClient()}>
          <Routes>
            <Route path="/admin/substrate/runbook/:invariant" element={<SubstrateRunbookPage />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("body")).toBeInTheDocument());
    expect(screen.getByText("Install Loop")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

`npm test -- SubstrateRunbookPage`

- [ ] **Step 3: Implement page + add route**

```tsx
// SubstrateRunbookPage.tsx
import { useParams } from "react-router-dom";
import RunbookDrawer from "../components/substrate/RunbookDrawer";

export default function SubstrateRunbookPage() {
  const { invariant } = useParams<{ invariant: string }>();
  if (!invariant) return <p>Missing invariant</p>;
  return (
    <div className="p-6">
      <RunbookDrawer invariant={invariant} onClose={() => window.history.back()} />
    </div>
  );
}
```

In `App.tsx`:
```tsx
const SubstrateRunbookPage = lazy(() => import("./pages/SubstrateRunbookPage"));
<Route path="/admin/substrate/runbook/:invariant" element={<SubstrateRunbookPage />} />
```

- [ ] **Step 4: Re-run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/frontend/src/pages/SubstrateRunbookPage.tsx
git add mcp-server/central-command/frontend/src/pages/SubstrateRunbookPage.test.tsx
git add mcp-server/central-command/frontend/src/App.tsx
git commit -m "feat(frontend): deep-link /admin/substrate/runbook/:invariant page"
```

---

### Task 18: Feature flag integration tests

**Files:**
- Create: `mcp-server/central-command/backend/tests/test_substrate_feature_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_feature_flag.py
import os
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_endpoint_returns_503_when_flag_off(admin_headers, monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "false")
    # Reload the module so the module-level constant refreshes
    import importlib, substrate_action_api
    importlib.reload(substrate_action_api)
    app.router.routes = [r for r in app.router.routes
                         if not str(getattr(r, 'path', '')).startswith("/api/admin/substrate")]
    app.include_router(substrate_action_api.router)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "cleanup_install_session",
                  "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"}, "reason": ""},
            headers=admin_headers)
    assert r.status_code == 503
    assert "SUBSTRATE_ACTIONS_ENABLED" in str(r.json()["detail"])

@pytest.mark.asyncio
async def test_runbook_endpoint_always_on_regardless_of_flag(admin_headers, monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "false")
    import importlib, substrate_action_api
    importlib.reload(substrate_action_api)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/admin/substrate/runbook/install_loop", headers=admin_headers)
    assert r.status_code == 200
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_substrate_feature_flag.py -v`
Expected: PASS — Task 6 already implemented the flag check; runbook route never reads flag.

- [ ] **Step 3: Document flag in `.env.example`**

Add to `mcp-server/.env.example`:

```
# Substrate action endpoint — flip to 'true' after runbook prose is filled in.
# Read-only runbook viewer is always on regardless.
SUBSTRATE_ACTIONS_ENABLED=false
```

- [ ] **Step 4: No new tests needed**

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/tests/test_substrate_feature_flag.py
git add mcp-server/.env.example
git commit -m "test(substrate): feature flag gate verified; runbook read-only always on"
```

---

### Task 19: Rate limits

**Files:**
- Modify: `mcp-server/central-command/backend/substrate_action_api.py`
- Create: `mcp-server/central-command/backend/tests/test_substrate_rate_limits.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_rate_limits.py
import asyncio
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_unlock_rate_limit_10_per_hour(admin_headers, monkeypatch,
                                              seed_many_locked_partners):
    emails = seed_many_locked_partners  # list of 12 emails
    statuses = []
    async with AsyncClient(app=app, base_url="http://test") as ac:
        for email in emails:
            r = await ac.post("/api/admin/substrate/action",
                json={"action_key": "unlock_platform_account",
                      "target_ref": {"table": "partners", "email": email},
                      "reason": "Rate limit test — each call is distinct and legitimate"},
                headers={**admin_headers, "Idempotency-Key": f"rl-{email}"})
            statuses.append(r.status_code)
    assert statuses.count(200) == 10
    assert statuses.count(429) == 2
```

- [ ] **Step 2: Run test — expect FAIL**

`pytest tests/test_substrate_rate_limits.py -v`

- [ ] **Step 3: Wire rate limits into endpoint**

In `substrate_action_api.py`, before handler dispatch:

```python
from auth import check_rate_limit  # already exists with (key, action, window, max) signature

RATE_LIMITS: dict[str, tuple[int, int]] = {
    "cleanup_install_session": (3600, 60),
    "unlock_platform_account": (3600, 10),
    "reconcile_fleet_order":   (3600, 20),
}
# ... inside post_substrate_action, after reason gate:
rl_window, rl_max = RATE_LIMITS[body.action_key]
rl_ok = await check_rate_limit(actor_email, f"substrate.{body.action_key}", rl_window, rl_max)
if not rl_ok:
    raise HTTPException(429, detail={"reason": "rate limit exceeded",
                                     "window_seconds": rl_window, "max_requests": rl_max},
                        headers={"Retry-After": str(rl_window)})
```

- [ ] **Step 4: Re-run rate-limit test**

`pytest tests/test_substrate_rate_limits.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/substrate_action_api.py
git add mcp-server/central-command/backend/tests/test_substrate_rate_limits.py
git commit -m "feat(substrate): per-action rate limits (60/10/20 per hr) with 429 + Retry-After"
```

---

### Task 20: Audit log content assertion test

**Files:**
- Create: `mcp-server/central-command/backend/tests/test_substrate_audit_log_details.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_substrate_audit_log_details.py
import json
import pytest
from httpx import AsyncClient
from main import app
from shared import admin_connection

@pytest.mark.asyncio
async def test_audit_row_captures_reason_initials_result(admin_headers, seed_locked_partner):
    email = seed_locked_partner["email"]
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/admin/substrate/action",
            json={"action_key": "unlock_platform_account",
                  "target_ref": {"table": "partners", "email": email},
                  "reason": "[JR] Confirmed via phone callback, real user"},
            headers=admin_headers)
    assert r.status_code == 200
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            "SELECT action, target, username, details FROM admin_audit_log "
            "WHERE action = 'substrate.unlock_platform_account' "
            "ORDER BY id DESC LIMIT 1"
        )
    assert row is not None
    details = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
    assert "reason" in details
    assert details["target_ref"]["email"] == email
    assert details["result"]["email"] == email
```

- [ ] **Step 2: Run test — expect PASS (endpoint already writes the row)**

`pytest tests/test_substrate_audit_log_details.py -v`

If it fails, debug the `admin_audit_log_append` call in Task 6's endpoint code and fix — the test is the contract.

- [ ] **Step 3: Commit**

```bash
git add mcp-server/central-command/backend/tests/test_substrate_audit_log_details.py
git commit -m "test(substrate): assert audit rows capture reason + target + result"
```

---

### Task 21: Full test + type + lint sweep

**Files:**
- No code changes; verification only.

- [ ] **Step 1: Run backend tests**

```bash
cd mcp-server/central-command/backend
SUBSTRATE_ACTIONS_ENABLED=true pytest tests/ -v --tb=short -k "substrate or migration_238"
```
Expected: all new tests PASS.

- [ ] **Step 2: Run frontend vitest**

```bash
cd mcp-server/central-command/frontend
npm test -- --run
```
Expected: PASS.

- [ ] **Step 3: Run tsc + eslint**

```bash
npm run typecheck
npm run lint
```
Expected: no errors.

- [ ] **Step 4: Run full backend pytest once to confirm no regression**

```bash
cd mcp-server/central-command/backend
pytest tests/ -x --tb=short
```
Expected: PASS (or pre-existing skips).

- [ ] **Step 5: Commit (if any lint fixes)**

```bash
git add -A
git diff --cached --name-only  # verify only lint-driven changes
git commit -m "chore(substrate): lint + type sweep"
```

---

### Task 22: Rollout — shadow (read-only UI + docs merged)

**Files:**
- No code changes; deploy the merged code with flag off.

- [ ] **Step 1: Verify `.env` on VPS does NOT set `SUBSTRATE_ACTIONS_ENABLED=true`**

```bash
ssh root@178.156.162.116 "grep SUBSTRATE_ACTIONS_ENABLED /opt/mcp-server/.env || echo 'not set (default=false)'"
```
Expected: "not set (default=false)" or `=false`.

- [ ] **Step 2: Push to main → CI deploys automatically**

```bash
git push origin main
```

- [ ] **Step 3: Verify deploy via the endpoint**

```bash
curl -sS https://api.osiriscare.net/health | jq
# Then open https://www.osiriscare.net/admin/substrate-health — verify:
#   - "View runbook" button present on every row
#   - "Copy CLI" button present where template defined
#   - "Run action" button present on install_loop, auth_failure_lockout, agent_version_lag rows
# Click Run action → endpoint returns 503 with the flag-off message.
```

- [ ] **Step 4: Visit `/admin/substrate/runbooks` and confirm the library renders all 33 invariants**

Open the library page, filter by sev1, confirm drawer opens for one invariant.

- [ ] **Step 5: Commit + tag shadow release**

```bash
git tag -a substrate-ops-ui-shadow -m "Substrate ops UI — shadow rollout (flag off)"
git push --tags
```

---

### Task 23: Rollout — enforce (flip flag + monitor)

**Files:**
- Modify: `/opt/mcp-server/.env` on VPS (operational change, not in repo)

- [ ] **Step 1: Fill in prose for the 4 action-bearing runbooks FIRST**

The four runbooks that back Run-action buttons MUST have real prose (not stubs) before flipping:
- `docs/substrate/install_loop.md`
- `docs/substrate/install_session_ttl.md`
- `docs/substrate/auth_failure_lockout.md`
- `docs/substrate/agent_version_lag.md`

Open each, fill in every TODO. Commit as a single PR:
```bash
git checkout -b docs/substrate-run-action-prose
# edit files
git commit -m "docs(substrate): fill in prose for the 4 action-bearing runbooks"
git push origin docs/substrate-run-action-prose
# open PR, get review, merge
```

- [ ] **Step 2: Flip the flag on VPS**

```bash
ssh root@178.156.162.116 "
  echo 'SUBSTRATE_ACTIONS_ENABLED=true' >> /opt/mcp-server/.env
  cd /opt/mcp-server && docker compose restart mcp-server
"
```

- [ ] **Step 3: Smoke-test the first live action**

On a dev site with a deliberately-stale `install_sessions` row:
```bash
# Log in as admin via UI, click Run action on the install_loop row, confirm.
# Expected: modal shows 'Done — action_id <n>', invariant clears on next 60s tick.
```

- [ ] **Step 4: Confirm audit entry**

```bash
ssh root@178.156.162.116 "docker compose exec mcp-postgres psql -U mcp -d mcp -c \"
  SELECT id, action, username, created_at
  FROM admin_audit_log
  WHERE action LIKE 'substrate.%'
  ORDER BY id DESC LIMIT 5;\""
```

- [ ] **Step 5: Monitor first week + announce**

Add to `frontend/src/pages/PublicChangelog.tsx` ENTRIES:
```tsx
{
  date: "2026-04-XX",
  category: "feature",
  title: "Substrate health panel — in-panel runbooks + scoped operator actions",
  body: "Admin panel /admin/substrate-health now renders markdown runbooks in-place, exposes copy-CLI affordances, and gains three scoped action buttons for OsirisCare's own substrate bookkeeping (stale install_sessions, platform-account lockout, out-of-band fleet_orders reconciliation). Customer infrastructure is never touched by panel actions — dispatch to appliances remains fleet_cli only.",
},
```

Commit:
```bash
git add mcp-server/central-command/frontend/src/pages/PublicChangelog.tsx
git commit -m "docs(changelog): announce substrate operator controls"
git push origin main
```

Monitor:
- `SELECT action, COUNT(*) FROM admin_audit_log WHERE action LIKE 'substrate.%' AND created_at > now() - INTERVAL '7 days' GROUP BY action;`
- Grafana/Prom alerts for 429s on `substrate.*` — expect zero false positives.
- If any 500 appears, open a P1 ticket and flip flag back to `false` while debugging.

---

## Self-Review

**Spec coverage:**
- §3 three actions → Tasks 3, 4, 5
- §4.1 endpoint + registry + idempotency → Tasks 2, 6
- §4.1 runbook endpoint → Task 10
- §4.1 CI gate → Task 9
- §4.2 per-violation buttons → Task 15
- §4.2 library route → Task 16
- §4.2 deep-link → Task 17
- §4.3 preview modal → Task 14
- §5.1 migration → Task 1
- §5.2 docs + stubs → Task 8
- §7 rate limits, audit, auth → Tasks 19, 20; auth covered in Task 6
- §7 CSRF — endpoint is under `/api/admin/*`, which is NOT in `csrf.EXEMPT_PATHS`; double-submit cookie applies automatically. No task needed.
- §8 all backend + frontend tests → Tasks 1-20
- §9 rollout → Tasks 22, 23
- §11 R1 sanitization → Task 12 uses rehype-sanitize; Task 12 has a script-strip test
- §11 R3 CLI template drift — partially covered by Task 15's `CLI_TEMPLATES` living inline; consider follow-up to auto-derive from `_DISPLAY_METADATA`
- Appendix A mapping — Task 15 `INVARIANT_ACTIONS` covers 4 rows; all 33 get `View runbook`; Copy-CLI is populated per Appendix A rows where template available (remaining entries left blank are deliberate — operator sees CLI text in the runbook drawer body for those)

**Placeholder scan:** No `TBD` / `TODO` / "Add appropriate" in tasks themselves. Template file `_TEMPLATE.md` intentionally contains TODO tokens (that is the stub).

**Type consistency:**
- `target_ref: dict` in Pydantic model and `Dict[str, Any]` in Python handlers — consistent.
- `action_id` returned as string in endpoint + frontend — consistent (BIGSERIAL cast via `str(...)`).
- `SUBSTRATE_ACTIONS` key set matches across registry, tests, and endpoint valid-keys error body.
- Frontend `CLI_TEMPLATES` and `INVARIANT_ACTIONS` keys are invariant names from `ALL_ASSERTIONS` (the only ones referenced in tests).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-19-substrate-operator-controls.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
