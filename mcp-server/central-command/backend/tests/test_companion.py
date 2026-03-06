"""Integration tests for the Companion Portal API.

Tests the /api/companion/ endpoints:
- GET /companion/clients — list client orgs
- GET /companion/clients/{org_id}/overview — client overview
- POST /companion/clients/{org_id}/sra — create SRA
- POST /companion/clients/{org_id}/policies — create policy
- POST /companion/clients/{org_id}/training — add training record
- POST /companion/clients/{org_id}/baas — add BAA
- GET/POST /companion/clients/{org_id}/notes/{module_key} — notes CRUD
- GET/POST /companion/clients/{org_id}/alerts — alerts CRUD
- POST /companion/clients/{org_id}/documents/upload — document upload
- Auth gating via require_companion
"""

import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")
os.environ.setdefault("API_KEY_SECRET", "test-api-key-secret")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Restore real fastapi/sqlalchemy/pydantic if earlier tests stubbed them.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

import httpx
from httpx import ASGITransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COMPANION_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "username": "companion-user",
    "displayName": "Companion User",
    "role": "companion",
    "org_scope": None,
}

ADMIN_USER = {
    "id": "00000000-0000-0000-0000-bbbbbbbbbbbb",
    "username": "admin-user",
    "displayName": "Admin User",
    "role": "admin",
    "org_scope": None,
}

READONLY_USER = {
    "id": "00000000-0000-0000-0000-cccccccccccc",
    "username": "readonly-user",
    "displayName": "Readonly User",
    "role": "readonly",
    "org_scope": None,
}

ORG_ID = "11111111-1111-1111-1111-111111111111"
ORG_UUID = uuid.UUID(ORG_ID)
NOTE_ID = "22222222-2222-2222-2222-222222222222"
ALERT_ID = "33333333-3333-3333-3333-333333333333"


class FakeConn:
    """Fake asyncpg connection that records queries and returns canned data."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.executed = []

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        for key, val in self._responses.items():
            if key in query:
                return val if isinstance(val, list) else [val]
        return []

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        for key, val in self._responses.items():
            if key in query:
                if isinstance(val, list):
                    return val[0] if val else None
                return val
        return None

    async def fetchval(self, query, *args):
        self.executed.append(("fetchval", query, args))
        for key, val in self._responses.items():
            if key in query:
                return val
        return 0

    async def execute(self, query, *args):
        self.executed.append(("execute", query, args))
        return "INSERT 0 1"


class FakePool:
    """Fake asyncpg pool that yields a FakeConn."""

    def __init__(self, conn=None):
        self._conn = conn or FakeConn()

    def acquire(self):
        return _FakeAcquire(self._conn)


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class FakeRecord(dict):
    """Mimics asyncpg Record — subscriptable by name and attribute access."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def _pool_patches(pool):
    """Patch get_pool everywhere it's imported (called directly, not via Depends)."""
    async def _get_pool():
        return pool

    @contextmanager
    def _patches():
        with patch("dashboard_api.companion.get_pool", new=_get_pool):
            yield

    return _patches()


def _org_record():
    return FakeRecord(id=ORG_UUID, name="Test Clinic", status="active")


def _make_overview_conn():
    """Build a FakeConn whose responses satisfy _compute_overview queries."""
    now = datetime.now(timezone.utc)
    responses = {
        "hipaa_sra_assessments": FakeRecord(
            status="completed", overall_risk_score=25, expires_at=now, findings_count=2
        ),
        "hipaa_policies": FakeRecord(total=5, active=3, review_due=1),
        "hipaa_training_records": FakeRecord(total=10, compliant=8, overdue=1),
        "hipaa_baas": FakeRecord(total=3, active=2, expiring_soon=0),
        "hipaa_ir_plans": FakeRecord(status="active", last_tested=now.date()),
        "hipaa_breach_log": 0,  # fetchval
        "hipaa_contingency_plans": FakeRecord(plans=2, all_tested=True),
        "hipaa_workforce_access": FakeRecord(active=5, pending_termination=0),
        "hipaa_physical_safeguards": FakeRecord(assessed=4, compliant=3, gaps=1),
        "hipaa_officers": [
            FakeRecord(role_type="privacy_officer", name="Jane"),
            FakeRecord(role_type="security_officer", name="Bob"),
        ],
        "hipaa_gap_responses": FakeRecord(answered=15, total=20, maturity_avg=3.5),
        "hipaa_documents": [],
        "companion_activity_log": "INSERT 0 1",
        "client_orgs": _org_record(),
    }
    return FakeConn(responses)


def _build_app(user_override=None):
    """Build a minimal FastAPI app with companion router and mocked auth."""
    from fastapi import FastAPI
    from dashboard_api.companion import router as companion_router
    from dashboard_api.auth import require_companion

    app = FastAPI()
    app.include_router(companion_router, prefix="/api")

    # Override require_companion to return a canned user
    target_user = user_override or COMPANION_USER

    async def _mock_companion():
        return target_user

    app.dependency_overrides[require_companion] = _mock_companion

    return app


# ---------------------------------------------------------------------------
# Tests — Auth gating
# ---------------------------------------------------------------------------


class TestCompanionAuthGating:
    """Endpoints should reject non-companion/admin roles."""

    @pytest.mark.asyncio
    async def test_readonly_rejected(self):
        """A readonly user should get 403 from companion endpoints."""
        from fastapi import FastAPI
        from dashboard_api.companion import router as companion_router
        from dashboard_api.auth import require_auth

        app = FastAPI()
        app.include_router(companion_router, prefix="/api")

        # Override require_auth (not require_companion) so the real
        # require_companion logic checks role and rejects readonly.
        async def _mock_auth():
            return READONLY_USER

        app.dependency_overrides[require_auth] = _mock_auth

        conn = _make_overview_conn()
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/companion/clients")
                assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_allowed(self):
        """An admin user should be allowed through require_companion."""
        app = _build_app(user_override=ADMIN_USER)
        conn = _make_overview_conn()
        # list_clients queries client_orgs with extra fields
        conn._responses["client_orgs"] = [
            FakeRecord(
                id=ORG_UUID, name="Test Clinic",
                primary_email="test@clinic.com", practice_type="dental",
                provider_count=5, status="active",
                onboarded_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
            ),
        ]
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/companion/clients")
                assert resp.status_code == 200
                data = resp.json()
                assert "clients" in data


# ---------------------------------------------------------------------------
# Tests — Client listing and overview
# ---------------------------------------------------------------------------


class TestCompanionClients:

    @pytest.mark.asyncio
    async def test_list_clients(self):
        """GET /companion/clients returns client list with overview."""
        app = _build_app()
        conn = _make_overview_conn()
        conn._responses["client_orgs"] = [
            FakeRecord(
                id=ORG_UUID, name="Test Clinic",
                primary_email="test@clinic.com", practice_type="dental",
                provider_count=5, status="active",
                onboarded_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
            ),
        ]
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/companion/clients")
                assert resp.status_code == 200
                data = resp.json()
                assert "clients" in data
                assert isinstance(data["clients"], list)

    @pytest.mark.asyncio
    async def test_get_client_overview(self):
        """GET /companion/clients/{org_id}/overview returns overview data."""
        app = _build_app()
        conn = _make_overview_conn()
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/companion/clients/{ORG_ID}/overview")
                assert resp.status_code == 200
                data = resp.json()
                assert "sra" in data
                assert "policies" in data
                assert "overall_readiness" in data

    @pytest.mark.asyncio
    async def test_overview_unknown_org_404(self):
        """GET overview for a nonexistent org returns 404."""
        app = _build_app()
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                fake_id = str(uuid.uuid4())
                resp = await client.get(f"/api/companion/clients/{fake_id}/overview")
                assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — SRA CRUD
# ---------------------------------------------------------------------------


class TestCompanionSRA:

    @pytest.mark.asyncio
    async def test_create_sra(self):
        """POST /companion/clients/{org_id}/sra creates an assessment."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        sra_row = FakeRecord(
            id=uuid.uuid4(), org_id=ORG_UUID, title="Annual SRA",
            status="in_progress", total_questions=42,
            created_by="Companion User", started_at=now,
            answered_questions=0, findings_count=0,
            overall_risk_score=None, completed_at=None, expires_at=None,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "hipaa_sra_assessments": sra_row,
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/sra",
                    json={"title": "Annual SRA"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["title"] == "Annual SRA"
                assert data["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Tests — Policy CRUD
# ---------------------------------------------------------------------------


class TestCompanionPolicies:

    @pytest.mark.asyncio
    async def test_create_policy(self):
        """POST /companion/clients/{org_id}/policies creates a policy."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        policy_row = FakeRecord(
            id=uuid.uuid4(), org_id=ORG_UUID,
            policy_key="access_control", title="Access Control Policy",
            content="Policy content here...", version=1,
            status="draft", hipaa_references=["164.312(a)(1)"],
            approved_by=None, approved_at=None,
            effective_date=None, review_due=None,
            created_at=now, updated_at=now,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "COALESCE(MAX(version)": 0,
            "INSERT INTO hipaa_policies": policy_row,
            "hipaa_officers": [],
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/policies",
                    json={
                        "policy_key": "access_control",
                        "title": "Access Control Policy",
                        "content": "Policy content here...",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["policy_key"] == "access_control"
                assert data["version"] == 1


# ---------------------------------------------------------------------------
# Tests — Training CRUD
# ---------------------------------------------------------------------------


class TestCompanionTraining:

    @pytest.mark.asyncio
    async def test_create_training(self):
        """POST /companion/clients/{org_id}/training adds a training record."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        training_row = FakeRecord(
            id=uuid.uuid4(), org_id=ORG_UUID,
            employee_name="John Doe", employee_email="john@clinic.com",
            employee_role="nurse", training_type="annual",
            training_topic="HIPAA Privacy", completed_date=now.date(),
            due_date=now.date(), status="completed",
            certificate_ref="CERT-001", trainer="External Trainer",
            notes="Completed online", created_at=now, updated_at=now,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "hipaa_training_records": training_row,
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/training",
                    json={
                        "employee_name": "John Doe",
                        "employee_email": "john@clinic.com",
                        "employee_role": "nurse",
                        "training_type": "annual",
                        "training_topic": "HIPAA Privacy",
                        "due_date": "2026-12-31",
                        "status": "completed",
                        "completed_date": "2026-03-01",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["employee_name"] == "John Doe"
                assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# Tests — BAA CRUD
# ---------------------------------------------------------------------------


class TestCompanionBAAs:

    @pytest.mark.asyncio
    async def test_create_baa(self):
        """POST /companion/clients/{org_id}/baas adds a BAA."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        baa_row = FakeRecord(
            id=uuid.uuid4(), org_id=ORG_UUID,
            associate_name="Cloud Provider Inc.",
            associate_type="cloud_service", contact_name="Jane Smith",
            contact_email="jane@cloud.com",
            signed_date=now.date(), expiry_date=now.date(),
            auto_renew=True, status="active",
            phi_types=["ePHI"], services_description="Cloud hosting",
            notes="", created_at=now, updated_at=now,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "hipaa_baas": baa_row,
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/baas",
                    json={
                        "associate_name": "Cloud Provider Inc.",
                        "associate_type": "cloud_service",
                        "contact_name": "Jane Smith",
                        "contact_email": "jane@cloud.com",
                        "signed_date": "2026-01-01",
                        "expiry_date": "2027-01-01",
                        "auto_renew": True,
                        "status": "active",
                        "phi_types": ["ePHI"],
                        "services_description": "Cloud hosting",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["associate_name"] == "Cloud Provider Inc."
                assert data["status"] == "active"


# ---------------------------------------------------------------------------
# Tests — Notes CRUD
# ---------------------------------------------------------------------------


class TestCompanionNotes:

    @pytest.mark.asyncio
    async def test_list_module_notes(self):
        """GET /companion/clients/{org_id}/notes/{module_key} returns notes."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        note_row = FakeRecord(
            id=uuid.UUID(NOTE_ID), companion_user_id=uuid.UUID(COMPANION_USER["id"]),
            org_id=ORG_UUID, module_key="sra", note="Check Q5 again",
            created_at=now, updated_at=now, companion_name="Companion User",
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "companion_notes": [note_row],
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/companion/clients/{ORG_ID}/notes/sra")
                assert resp.status_code == 200
                data = resp.json()
                assert "notes" in data

    @pytest.mark.asyncio
    async def test_create_note(self):
        """POST /companion/clients/{org_id}/notes/{module_key} creates a note."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        note_row = FakeRecord(
            id=uuid.uuid4(), companion_user_id=uuid.UUID(COMPANION_USER["id"]),
            org_id=ORG_UUID, module_key="policies", note="Need to follow up",
            created_at=now, updated_at=now,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "companion_notes": note_row,
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/notes/policies",
                    json={"note": "Need to follow up"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["note"] == "Need to follow up"
                assert data["module_key"] == "policies"


# ---------------------------------------------------------------------------
# Tests — Alerts CRUD
# ---------------------------------------------------------------------------


class TestCompanionAlerts:

    @pytest.mark.asyncio
    async def test_list_alerts(self):
        """GET /companion/clients/{org_id}/alerts returns alerts list."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        alert_row = FakeRecord(
            id=uuid.UUID(ALERT_ID), companion_user_id=uuid.UUID(COMPANION_USER["id"]),
            org_id=ORG_UUID, module_key="sra", expected_status="complete",
            target_date=now.date(), description="Complete SRA by Q2",
            status="active", triggered_at=None, resolved_at=None,
            last_notified_at=None, notification_count=0,
            created_at=now, updated_at=now, companion_name="Companion User",
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "companion_alerts": [alert_row],
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/companion/clients/{ORG_ID}/alerts")
                assert resp.status_code == 200
                data = resp.json()
                assert "alerts" in data

    @pytest.mark.asyncio
    async def test_create_alert(self):
        """POST /companion/clients/{org_id}/alerts creates an alert."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        alert_row = FakeRecord(
            id=uuid.uuid4(), companion_user_id=uuid.UUID(COMPANION_USER["id"]),
            org_id=ORG_UUID, module_key="policies", expected_status="complete",
            target_date=date(2026, 6, 30), description="Finalize all policies",
            status="active", triggered_at=None, resolved_at=None,
            last_notified_at=None, notification_count=0,
            created_at=now, updated_at=now,
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "companion_alerts": alert_row,
            "companion_activity_log": "INSERT 0 1",
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/alerts",
                    json={
                        "module_key": "policies",
                        "expected_status": "complete",
                        "target_date": "2026-06-30",
                        "description": "Finalize all policies",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["module_key"] == "policies"
                assert data["expected_status"] == "complete"

    @pytest.mark.asyncio
    async def test_create_alert_invalid_module_key(self):
        """POST alert with invalid module_key returns 400."""
        app = _build_app()
        conn = FakeConn({"client_orgs": _org_record()})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/alerts",
                    json={
                        "module_key": "nonexistent_module",
                        "expected_status": "complete",
                        "target_date": "2026-06-30",
                    },
                )
                assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_alert_invalid_status(self):
        """POST alert with invalid expected_status returns 400."""
        app = _build_app()
        conn = FakeConn({"client_orgs": _org_record()})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/alerts",
                    json={
                        "module_key": "sra",
                        "expected_status": "bogus_status",
                        "target_date": "2026-06-30",
                    },
                )
                assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests — Document upload
# ---------------------------------------------------------------------------


class TestCompanionDocuments:

    @pytest.mark.asyncio
    async def test_upload_document_invalid_module(self):
        """POST upload with invalid module_key returns 400."""
        app = _build_app()
        conn = FakeConn({"client_orgs": _org_record()})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/companion/clients/{ORG_ID}/documents/upload",
                    data={"module_key": "invalid_module"},
                    files={"file": ("test.pdf", b"PDF content", "application/pdf")},
                )
                assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_documents(self):
        """GET /companion/clients/{org_id}/documents returns document list."""
        app = _build_app()
        now = datetime.now(timezone.utc)
        doc_row = FakeRecord(
            id=str(uuid.uuid4()), module_key="policies",
            file_name="policy_v1.pdf", mime_type="application/pdf",
            size_bytes=12345, description="Initial draft",
            uploaded_by_email="companion@test.com",
            created_at=now.isoformat(),
        )
        conn = FakeConn({
            "client_orgs": _org_record(),
            "hipaa_documents": [doc_row],
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/companion/clients/{ORG_ID}/documents")
                assert resp.status_code == 200
                data = resp.json()
                assert "documents" in data
                assert "total" in data


# ---------------------------------------------------------------------------
# Tests — _evaluate_module_status helper
# ---------------------------------------------------------------------------


class TestEvaluateModuleStatus:
    """Unit tests for the _evaluate_module_status helper function."""

    def test_all_complete(self):
        from dashboard_api.companion import _evaluate_module_status

        overview = {
            "sra": {"status": "completed"},
            "policies": {"active": 5, "total": 5, "review_due": 0},
            "training": {"compliant": 10, "total_employees": 10, "overdue": 0},
            "baas": {"active": 3, "total": 3, "expiring_soon": 0},
            "ir_plan": {"status": "active"},
            "contingency": {"plans": 2, "all_tested": True},
            "workforce": {"active": 5, "pending_termination": 0},
            "physical": {"assessed": 4, "compliant": 4, "gaps": 0},
            "officers": {"privacy_officer": "Jane", "security_officer": "Bob"},
            "gap_analysis": {"completion": 95.0},
        }
        result = _evaluate_module_status(overview)
        assert result["sra"] == "complete"
        assert result["policies"] == "complete"
        assert result["training"] == "complete"
        assert result["officers"] == "complete"
        assert result["gap-analysis"] == "complete"

    def test_empty_overview_not_started(self):
        from dashboard_api.companion import _evaluate_module_status

        overview = {
            "sra": {},
            "policies": {},
            "training": {},
            "baas": {},
            "ir_plan": {},
            "contingency": {},
            "workforce": {},
            "physical": {},
            "officers": {},
            "gap_analysis": {},
        }
        result = _evaluate_module_status(overview)
        assert result["sra"] == "not_started"
        assert result["policies"] == "not_started"
        assert result["training"] == "not_started"

    def test_action_needed_states(self):
        from dashboard_api.companion import _evaluate_module_status

        overview = {
            "sra": {"status": "in_progress"},
            "policies": {"active": 3, "total": 5, "review_due": 2},
            "training": {"compliant": 5, "total_employees": 10, "overdue": 3},
            "baas": {"active": 2, "total": 3, "expiring_soon": 1},
            "ir_plan": {"status": "draft"},
            "contingency": {"plans": 1, "all_tested": False},
            "workforce": {"active": 3, "pending_termination": 1},
            "physical": {"assessed": 2, "compliant": 1, "gaps": 1},
            "officers": {"privacy_officer": "Jane", "security_officer": None},
            "gap_analysis": {"completion": 45.0},
        }
        result = _evaluate_module_status(overview)
        assert result["sra"] == "in_progress"
        assert result["policies"] == "action_needed"
        assert result["training"] == "action_needed"
        assert result["baas"] == "action_needed"
        assert result["workforce"] == "action_needed"
        assert result["physical"] == "action_needed"
        assert result["officers"] == "in_progress"
        assert result["gap-analysis"] == "in_progress"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
