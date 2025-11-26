"""
Tests for Web UI module.

Tests cover:
- API endpoints for compliance status
- Evidence browser endpoints
- Dashboard data methods
- Flywheel metrics

Requires: fastapi, httpx (optional dependencies)
"""

import pytest
import tempfile
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, AsyncMock

# Skip entire module if FastAPI not installed
pytest.importorskip("fastapi", reason="FastAPI not installed")
pytest.importorskip("httpx", reason="httpx not installed (needed for TestClient)")

from fastapi.testclient import TestClient

from compliance_agent.web_ui import (
    ComplianceWebUI,
    ComplianceStatus,
    ControlStatus,
    IncidentSummary,
    FlywheelMetrics
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_dirs(tmp_path):
    """Create temporary directories for testing."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    hash_chain_dir = tmp_path / "hash-chain"
    hash_chain_dir.mkdir()

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()

    static_dir = tmp_path / "static"
    static_dir.mkdir()

    # Create minimal templates
    (templates_dir / "dashboard.html").write_text("""
<!DOCTYPE html>
<html><body>
<h1>Dashboard</h1>
<div>Site: {{ site_id }}</div>
<div>Status: {{ status.status }}</div>
</body></html>
""")

    (templates_dir / "evidence.html").write_text("""
<!DOCTYPE html>
<html><body>
<h1>Evidence Browser</h1>
<div>Count: {{ bundles|length }}</div>
</body></html>
""")

    return {
        "evidence_dir": evidence_dir,
        "hash_chain_dir": hash_chain_dir,
        "templates_dir": templates_dir,
        "static_dir": static_dir,
        "incident_db": str(tmp_path / "incidents.db")
    }


@pytest.fixture
def incident_db(temp_dirs):
    """Create incident database with test data."""
    db_path = temp_dirs["incident_db"]
    conn = sqlite3.connect(db_path)

    # Create tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            check_type TEXT,
            severity TEXT,
            resolution_level TEXT,
            status TEXT,
            created_at TEXT,
            resolved_at TEXT,
            resolution_time_sec REAL
        )
    """)

    # Insert test incidents
    now = datetime.now(timezone.utc)
    incidents = [
        ("inc-001", "patching", "high", "L1", "resolved",
         (now - timedelta(hours=2)).isoformat(), now.isoformat(), 120.5),
        ("inc-002", "backup", "critical", "L2", "resolved",
         (now - timedelta(hours=4)).isoformat(), (now - timedelta(hours=3)).isoformat(), 3600.0),
        ("inc-003", "logging", "medium", "L1", "resolved",
         (now - timedelta(hours=6)).isoformat(), (now - timedelta(hours=5)).isoformat(), 300.0),
        ("inc-004", "firewall", "high", "L3", "escalated",
         (now - timedelta(hours=1)).isoformat(), None, None),
    ]

    for inc in incidents:
        conn.execute(
            "INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            inc
        )

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def sample_evidence(temp_dirs):
    """Create sample evidence bundles."""
    evidence_dir = temp_dirs["evidence_dir"]

    bundles = []
    now = datetime.now(timezone.utc)

    for i in range(5):
        bundle_id = f"EB-TEST-{i:03d}"
        bundle = {
            "bundle_id": bundle_id,
            "site_id": "test-site",
            "check": "patching" if i % 2 == 0 else "backup",
            "outcome": "success" if i < 3 else "failed",
            "timestamp_start": (now - timedelta(hours=i*2)).isoformat(),
            "timestamp_end": (now - timedelta(hours=i*2-1)).isoformat(),
            "pre_state": {"version": f"1.{i}"},
            "post_state": {"version": f"1.{i+1}"},
            "actions": [{"action": "test", "result": "ok"}],
            "hipaa_controls": ["164.308(a)(5)(ii)(B)"]
        }

        bundle_path = evidence_dir / f"{bundle_id}.json"
        bundle_path.write_text(json.dumps(bundle, indent=2))
        bundles.append(bundle)

    return bundles


@pytest.fixture
def web_ui(temp_dirs, incident_db):
    """Create ComplianceWebUI instance."""
    return ComplianceWebUI(
        evidence_dir=temp_dirs["evidence_dir"],
        incident_db_path=incident_db,
        hash_chain_path=temp_dirs["hash_chain_dir"],
        templates_dir=temp_dirs["templates_dir"],
        static_dir=temp_dirs["static_dir"],
        site_id="test-site-001",
        host_id="test-host"
    )


@pytest.fixture
def client(web_ui):
    """Create test client for FastAPI app."""
    return TestClient(web_ui.app)


# ============================================================================
# Model Tests
# ============================================================================

def test_compliance_status_model():
    """Test ComplianceStatus model validation."""
    status = ComplianceStatus(
        status="healthy",
        score=95.5,
        last_check="2025-01-01T00:00:00Z",
        checks_passed=8,
        checks_failed=0,
        checks_warning=1
    )

    assert status.status == "healthy"
    assert status.score == 95.5
    assert status.checks_passed == 8


def test_control_status_model():
    """Test ControlStatus model validation."""
    control = ControlStatus(
        control_id="patching",
        name="Critical Patch Timeliness",
        status="pass",
        last_checked="2025-01-01T00:00:00Z",
        evidence_count=10,
        auto_fixed=3,
        hipaa_citation="164.308(a)(5)(ii)(B)"
    )

    assert control.control_id == "patching"
    assert control.status == "pass"


def test_incident_summary_model():
    """Test IncidentSummary model validation."""
    summary = IncidentSummary(
        total_24h=10,
        auto_resolved=8,
        escalated=2,
        l1_handled=6,
        l2_handled=2,
        l3_handled=2,
        avg_mttr_seconds=300.5
    )

    assert summary.total_24h == 10
    assert summary.auto_resolved == 8


def test_flywheel_metrics_model():
    """Test FlywheelMetrics model validation."""
    metrics = FlywheelMetrics(
        status="good",
        l1_percentage=70.0,
        l2_percentage=25.0,
        l3_percentage=5.0,
        patterns_tracked=50,
        promotion_candidates=5,
        rules_promoted=10
    )

    assert metrics.status == "good"
    assert metrics.l1_percentage == 70.0


# ============================================================================
# Web UI Initialization Tests
# ============================================================================

def test_web_ui_initialization(temp_dirs, incident_db):
    """Test ComplianceWebUI initialization."""
    ui = ComplianceWebUI(
        evidence_dir=temp_dirs["evidence_dir"],
        incident_db_path=incident_db,
        hash_chain_path=temp_dirs["hash_chain_dir"],
        templates_dir=temp_dirs["templates_dir"],
        static_dir=temp_dirs["static_dir"],
        site_id="test-site",
        host_id="test-host"
    )

    assert ui.site_id == "test-site"
    assert ui.host_id == "test-host"
    assert ui.app is not None
    assert ui.templates is not None


def test_web_ui_default_paths(tmp_path):
    """Test WebUI with default paths."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    ui = ComplianceWebUI(
        evidence_dir=evidence_dir,
        site_id="test"
    )

    # Should create default templates/static dirs
    assert ui.templates_dir.exists()


# ============================================================================
# API Endpoint Tests
# ============================================================================

def test_api_status_endpoint(client, sample_evidence):
    """Test /api/status endpoint."""
    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert "score" in data
    assert "last_check" in data
    assert "checks_passed" in data
    assert "checks_failed" in data


def test_api_controls_endpoint(client, sample_evidence):
    """Test /api/controls endpoint."""
    response = client.get("/api/controls")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) > 0

    # Check control structure
    control = data[0]
    assert "control_id" in control
    assert "name" in control
    assert "status" in control
    assert "hipaa_citation" in control


def test_api_incidents_endpoint(client):
    """Test /api/incidents endpoint."""
    response = client.get("/api/incidents")

    assert response.status_code == 200
    data = response.json()

    assert "total_24h" in data
    assert "auto_resolved" in data
    assert "l1_handled" in data
    assert "l2_handled" in data
    assert "l3_handled" in data


def test_api_incidents_with_hours_param(client):
    """Test /api/incidents with hours parameter."""
    response = client.get("/api/incidents?hours=48")

    assert response.status_code == 200
    data = response.json()
    assert "total_24h" in data  # Key name is misleading but matches param


def test_api_flywheel_endpoint(client):
    """Test /api/flywheel endpoint."""
    response = client.get("/api/flywheel")

    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert "l1_percentage" in data
    assert "l2_percentage" in data
    assert "l3_percentage" in data
    assert "patterns_tracked" in data


def test_api_evidence_list(client, sample_evidence):
    """Test /api/evidence endpoint."""
    response = client.get("/api/evidence")

    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    assert "pagination" in data
    assert isinstance(data["items"], list)


def test_api_evidence_list_with_filter(client, sample_evidence):
    """Test /api/evidence with filter."""
    response = client.get("/api/evidence?check_type=patching")

    assert response.status_code == 200
    data = response.json()

    # All returned items should match filter
    for item in data["items"]:
        assert item["check"] == "patching"


def test_api_evidence_detail(client, sample_evidence):
    """Test /api/evidence/{bundle_id} endpoint."""
    bundle_id = sample_evidence[0]["bundle_id"]
    response = client.get(f"/api/evidence/{bundle_id}")

    assert response.status_code == 200
    data = response.json()

    assert data["bundle_id"] == bundle_id


def test_api_evidence_detail_not_found(client):
    """Test /api/evidence/{bundle_id} with invalid ID."""
    response = client.get("/api/evidence/nonexistent-bundle")

    assert response.status_code == 404


def test_api_health_endpoint(client):
    """Test /api/health endpoint."""
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert "site_id" in data


# ============================================================================
# Dashboard Page Tests
# ============================================================================

def test_dashboard_page(client, sample_evidence):
    """Test main dashboard page."""
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard" in response.text


def test_evidence_browser_page(client, sample_evidence):
    """Test evidence browser page."""
    response = client.get("/evidence")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Evidence" in response.text


# ============================================================================
# Data Method Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_compliance_status(web_ui, sample_evidence):
    """Test _get_compliance_status method."""
    status = await web_ui._get_compliance_status()

    assert "status" in status
    assert status["status"] in ["healthy", "warning", "critical"]
    assert 0 <= status["score"] <= 100


@pytest.mark.asyncio
async def test_get_control_statuses(web_ui, sample_evidence):
    """Test _get_control_statuses method."""
    controls = await web_ui._get_control_statuses()

    assert isinstance(controls, list)
    assert len(controls) > 0

    # Check expected controls exist
    control_ids = [c["control_id"] for c in controls]
    assert "patching" in control_ids
    assert "backup" in control_ids
    assert "logging" in control_ids


@pytest.mark.asyncio
async def test_get_incident_summary(web_ui):
    """Test _get_incident_summary method."""
    summary = await web_ui._get_incident_summary()

    assert "total_24h" in summary
    assert summary["total_24h"] >= 0
    assert "l1_handled" in summary
    assert "l2_handled" in summary
    assert "l3_handled" in summary


@pytest.mark.asyncio
async def test_get_incident_summary_empty_db(temp_dirs):
    """Test _get_incident_summary with no database."""
    ui = ComplianceWebUI(
        evidence_dir=temp_dirs["evidence_dir"],
        incident_db_path="/nonexistent/path.db",
        site_id="test"
    )

    summary = await ui._get_incident_summary()

    # Should return empty summary
    assert summary["total_24h"] == 0


@pytest.mark.asyncio
async def test_get_flywheel_metrics(web_ui):
    """Test _get_flywheel_metrics method."""
    metrics = await web_ui._get_flywheel_metrics()

    assert "status" in metrics
    assert "l1_percentage" in metrics
    assert "l2_percentage" in metrics
    assert "l3_percentage" in metrics


@pytest.mark.asyncio
async def test_list_evidence(web_ui, sample_evidence):
    """Test _list_evidence method."""
    result = await web_ui._list_evidence()

    assert "items" in result
    assert "pagination" in result
    assert len(result["items"]) == len(sample_evidence)


@pytest.mark.asyncio
async def test_list_evidence_pagination(web_ui, sample_evidence):
    """Test _list_evidence with pagination."""
    result = await web_ui._list_evidence(page=1, per_page=2)

    assert len(result["items"]) == 2
    assert result["pagination"]["page"] == 1
    assert result["pagination"]["per_page"] == 2


@pytest.mark.asyncio
async def test_list_evidence_filter_by_check_type(web_ui, sample_evidence):
    """Test _list_evidence with check_type filter."""
    result = await web_ui._list_evidence(check_type="patching")

    for item in result["items"]:
        assert item["check"] == "patching"


@pytest.mark.asyncio
async def test_list_evidence_filter_by_outcome(web_ui, sample_evidence):
    """Test _list_evidence with outcome filter."""
    result = await web_ui._list_evidence(outcome="success")

    for item in result["items"]:
        assert item["outcome"] == "success"


@pytest.mark.asyncio
async def test_get_evidence_bundle(web_ui, sample_evidence):
    """Test _get_evidence_bundle method."""
    bundle_id = sample_evidence[0]["bundle_id"]
    bundle = await web_ui._get_evidence_bundle(bundle_id)

    assert bundle is not None
    assert bundle["bundle_id"] == bundle_id


@pytest.mark.asyncio
async def test_get_evidence_bundle_not_found(web_ui):
    """Test _get_evidence_bundle with nonexistent ID."""
    bundle = await web_ui._get_evidence_bundle("nonexistent")

    assert bundle is None


@pytest.mark.asyncio
async def test_get_evidence_stats(web_ui, sample_evidence):
    """Test _get_evidence_stats method."""
    stats = await web_ui._get_evidence_stats()

    assert "total_bundles" in stats
    assert "success_count" in stats
    assert "failed_count" in stats
    assert stats["total_bundles"] == len(sample_evidence)


# ============================================================================
# Hash Chain Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_hash_chain_status_empty(web_ui):
    """Test _get_hash_chain_status with empty chain."""
    status = await web_ui._get_hash_chain_status()

    assert "verified" in status
    # Empty chain should still be valid
    assert status.get("total_links", 0) == 0


@pytest.mark.asyncio
async def test_get_hash_chain_status_with_data(web_ui, temp_dirs):
    """Test _get_hash_chain_status with chain data."""
    # Create chain file
    chain_file = temp_dirs["hash_chain_dir"] / "chain.jsonl"

    links = [
        {"timestamp": "2025-01-01T00:00:00Z", "hash": "abc123", "prev_hash": "0" * 64},
        {"timestamp": "2025-01-01T01:00:00Z", "hash": "def456", "prev_hash": "abc123"},
    ]

    with open(chain_file, 'w') as f:
        for link in links:
            f.write(json.dumps(link) + "\n")

    status = await web_ui._get_hash_chain_status()

    assert status["total_links"] == 2


# ============================================================================
# Error Handling Tests
# ============================================================================

def test_api_handles_invalid_page(client):
    """Test API handles invalid page parameter."""
    response = client.get("/api/evidence?page=-1")

    # Should either return error or default to page 1
    assert response.status_code in [200, 400, 422]


def test_api_handles_invalid_per_page(client):
    """Test API handles invalid per_page parameter."""
    response = client.get("/api/evidence?per_page=0")

    # Should either return error or use default
    assert response.status_code in [200, 400, 422]


# ============================================================================
# Integration Tests
# ============================================================================

def test_full_workflow(client, sample_evidence):
    """Test complete workflow: status -> controls -> evidence."""
    # Get status
    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200

    # Get controls
    controls_resp = client.get("/api/controls")
    assert controls_resp.status_code == 200
    controls = controls_resp.json()

    # Get evidence for a specific check
    if controls:
        check_type = controls[0]["control_id"]
        evidence_resp = client.get(f"/api/evidence?check_type={check_type}")
        assert evidence_resp.status_code == 200


def test_dashboard_data_consistency(client, sample_evidence):
    """Test that dashboard data is consistent."""
    status_resp = client.get("/api/status")
    controls_resp = client.get("/api/controls")

    status = status_resp.json()
    controls = controls_resp.json()

    # Count should match
    passed = sum(1 for c in controls if c["status"] == "pass")
    failed = sum(1 for c in controls if c["status"] == "fail")

    assert status["checks_passed"] == passed
    assert status["checks_failed"] == failed
