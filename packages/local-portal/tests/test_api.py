"""Tests for Local Portal API."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from local_portal.main import create_app
from local_portal.config import PortalConfig


@pytest.fixture
def temp_db():
    """Create a temporary database with test data."""
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create schema (from network-scanner)
    conn.executescript("""
        CREATE TABLE devices (
            id TEXT PRIMARY KEY,
            hostname TEXT,
            ip_address TEXT NOT NULL UNIQUE,
            mac_address TEXT,
            device_type TEXT DEFAULT 'unknown',
            os_name TEXT,
            os_version TEXT,
            manufacturer TEXT,
            model TEXT,
            medical_device INTEGER DEFAULT 0,
            scan_policy TEXT DEFAULT 'standard',
            manually_opted_in INTEGER DEFAULT 0,
            phi_access_flag INTEGER DEFAULT 0,
            discovery_source TEXT DEFAULT 'nmap',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_scan_at TEXT,
            status TEXT DEFAULT 'discovered',
            online INTEGER DEFAULT 0,
            compliance_status TEXT DEFAULT 'unknown',
            last_compliance_check TEXT,
            synced_to_central INTEGER DEFAULT 0,
            sync_version INTEGER DEFAULT 0
        );

        CREATE TABLE device_ports (
            device_id TEXT,
            port INTEGER,
            protocol TEXT DEFAULT 'tcp',
            service_name TEXT,
            service_version TEXT,
            state TEXT DEFAULT 'open',
            last_seen_at TEXT,
            UNIQUE(device_id, port, protocol)
        );

        CREATE TABLE scan_history (
            id TEXT PRIMARY KEY,
            scan_type TEXT DEFAULT 'full',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT DEFAULT 'running',
            devices_found INTEGER DEFAULT 0,
            new_devices INTEGER DEFAULT 0,
            changed_devices INTEGER DEFAULT 0,
            medical_devices_excluded INTEGER DEFAULT 0,
            methods_used TEXT,
            network_ranges TEXT,
            error_message TEXT,
            triggered_by TEXT DEFAULT 'manual'
        );

        CREATE TABLE device_compliance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            check_type TEXT NOT NULL,
            hipaa_control TEXT,
            status TEXT DEFAULT 'unknown',
            details TEXT,
            checked_at TEXT NOT NULL
        );

        CREATE TABLE device_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT NOT NULL
        );
    """)

    # Insert test data
    conn.execute("""
        INSERT INTO devices (id, hostname, ip_address, device_type, status, compliance_status,
                            medical_device, scan_policy, first_seen_at, last_seen_at)
        VALUES ('dev-001', 'workstation-01', '192.168.1.100', 'workstation', 'monitored', 'compliant',
                0, 'standard', '2024-01-01T00:00:00Z', '2024-01-15T00:00:00Z')
    """)
    conn.execute("""
        INSERT INTO devices (id, hostname, ip_address, device_type, status, compliance_status,
                            medical_device, scan_policy, first_seen_at, last_seen_at)
        VALUES ('dev-002', 'server-01', '192.168.1.10', 'server', 'monitored', 'drifted',
                0, 'standard', '2024-01-01T00:00:00Z', '2024-01-15T00:00:00Z')
    """)
    conn.execute("""
        INSERT INTO devices (id, hostname, ip_address, device_type, status, compliance_status,
                            medical_device, scan_policy, first_seen_at, last_seen_at)
        VALUES ('dev-003', 'dicom-server', '192.168.1.50', 'medical', 'excluded', 'excluded',
                1, 'excluded', '2024-01-01T00:00:00Z', '2024-01-15T00:00:00Z')
    """)

    conn.execute("""
        INSERT INTO scan_history (id, scan_type, started_at, completed_at, status,
                                 devices_found, new_devices, medical_devices_excluded, triggered_by)
        VALUES ('scan-001', 'full', '2024-01-15T02:00:00Z', '2024-01-15T02:05:00Z', 'completed',
                3, 0, 1, 'schedule')
    """)

    conn.execute("""
        INSERT INTO device_compliance (device_id, check_type, hipaa_control, status, checked_at)
        VALUES ('dev-001', 'firewall', '164.312(a)(1)', 'pass', '2024-01-15T00:00:00Z')
    """)

    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)


@pytest.fixture
def test_client(temp_db):
    """Create test client with mocked database."""
    app = create_app()

    # Override configuration
    config = PortalConfig()
    config.scanner_db_path = temp_db
    config.site_name = "Test Site"
    app.state.config = config

    # Initialize database
    from local_portal.db import PortalDatabase
    app.state.db = PortalDatabase(temp_db)

    return TestClient(app)


class TestHealthCheck:
    """Tests for health endpoint."""

    def test_health_returns_ok(self, test_client):
        """Health check should return healthy status."""
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestDashboard:
    """Tests for dashboard endpoints."""

    def test_get_dashboard(self, test_client):
        """Dashboard should return summary data."""
        response = test_client.get("/api/dashboard")
        assert response.status_code == 200

        data = response.json()
        assert "devices" in data
        assert "compliance" in data
        assert data["devices"]["total"] == 3
        assert data["devices"]["medical"] == 1

    def test_get_kpis(self, test_client):
        """KPIs endpoint should return key metrics."""
        response = test_client.get("/api/kpis")
        assert response.status_code == 200

        data = response.json()
        assert data["total_devices"] == 3
        assert data["medical_devices_excluded"] == 1


class TestDevices:
    """Tests for device endpoints."""

    def test_list_devices(self, test_client):
        """Should list all devices."""
        response = test_client.get("/api/devices")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 3
        assert len(data["devices"]) == 3

    def test_list_devices_filter_by_type(self, test_client):
        """Should filter devices by type."""
        response = test_client.get("/api/devices?device_type=workstation")
        assert response.status_code == 200

        data = response.json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["hostname"] == "workstation-01"

    def test_get_device(self, test_client):
        """Should get device details."""
        response = test_client.get("/api/devices/dev-001")
        assert response.status_code == 200

        data = response.json()
        assert data["device"]["hostname"] == "workstation-01"
        assert "ports" in data
        assert "compliance_checks" in data

    def test_get_device_not_found(self, test_client):
        """Should return 404 for unknown device."""
        response = test_client.get("/api/devices/unknown-device")
        assert response.status_code == 404

    def test_update_device_policy(self, test_client):
        """Should update device scan policy."""
        response = test_client.put(
            "/api/devices/dev-001/policy",
            json={"scan_policy": "limited"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["scan_policy"] == "limited"

    def test_medical_device_requires_opt_in(self, test_client):
        """Medical device policy change should require opt-in."""
        # Try to change medical device to limited without opt-in
        response = test_client.put(
            "/api/devices/dev-003/policy",
            json={"scan_policy": "limited"},
        )
        assert response.status_code == 400
        assert "opt-in" in response.json()["detail"].lower()

    def test_medical_device_cannot_be_standard(self, test_client):
        """Medical device cannot use standard policy."""
        response = test_client.put(
            "/api/devices/dev-003/policy",
            json={"scan_policy": "standard", "manually_opted_in": True},
        )
        assert response.status_code == 400
        assert "limited" in response.json()["detail"].lower()

    def test_medical_device_opt_in(self, test_client):
        """Should allow medical device opt-in with proper flag."""
        response = test_client.put(
            "/api/devices/dev-003/policy",
            json={
                "scan_policy": "limited",
                "manually_opted_in": True,
                "reason": "Required for audit",
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["scan_policy"] == "limited"
        assert data["manually_opted_in"] == 1


class TestScans:
    """Tests for scan endpoints."""

    def test_list_scans(self, test_client):
        """Should list scan history."""
        response = test_client.get("/api/scans")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 1

    def test_get_latest_scan(self, test_client):
        """Should get latest scan."""
        response = test_client.get("/api/scans/latest")
        assert response.status_code == 200

        data = response.json()
        assert data["scan"]["id"] == "scan-001"

    def test_trigger_scan_scanner_unavailable(self, test_client):
        """Should handle scanner service unavailable."""
        response = test_client.post(
            "/api/scans/trigger",
            json={"scan_type": "full"},
        )
        # Should return 503 when scanner is not running
        assert response.status_code == 503


class TestCompliance:
    """Tests for compliance endpoints."""

    def test_compliance_summary(self, test_client):
        """Should return compliance summary."""
        response = test_client.get("/api/compliance/summary")
        assert response.status_code == 200

        data = response.json()
        assert "total_devices" in data
        assert "compliance" in data

    def test_drifted_devices(self, test_client):
        """Should list drifted devices."""
        response = test_client.get("/api/compliance/drifted")
        assert response.status_code == 200

        data = response.json()
        assert data["drifted_count"] == 1

    def test_device_compliance(self, test_client):
        """Should get device compliance details."""
        response = test_client.get("/api/compliance/device/dev-001")
        assert response.status_code == 200

        data = response.json()
        assert data["device_id"] == "dev-001"
        assert len(data["all_checks"]) >= 1


class TestExports:
    """Tests for export endpoints."""

    def test_export_devices_csv(self, test_client):
        """Should export devices as CSV."""
        response = test_client.get("/api/exports/csv/devices")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]

        # Check CSV content
        content = response.text
        assert "hostname" in content
        assert "workstation-01" in content

    def test_export_compliance_csv(self, test_client):
        """Should export compliance as CSV."""
        response = test_client.get("/api/exports/csv/compliance")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]

    def test_export_compliance_pdf(self, test_client):
        """Should export compliance report as PDF."""
        response = test_client.get("/api/exports/pdf/compliance")
        assert response.status_code == 200
        assert "application/pdf" in response.headers["content-type"]

        # Check PDF magic bytes
        assert response.content[:4] == b"%PDF"

    def test_export_inventory_pdf(self, test_client):
        """Should export inventory as PDF."""
        response = test_client.get("/api/exports/pdf/inventory")
        assert response.status_code == 200
        assert "application/pdf" in response.headers["content-type"]
        assert response.content[:4] == b"%PDF"


class TestConfig:
    """Tests for configuration."""

    def test_config_defaults(self):
        """Config should have sensible defaults."""
        config = PortalConfig()

        assert config.port == 8083
        assert config.scanner_api_url == "http://127.0.0.1:8082"
        assert "devices.db" in str(config.scanner_db_path)

    def test_config_from_env(self):
        """Config should load from environment."""
        import os

        os.environ["LOCAL_PORTAL_PORT"] = "9000"
        os.environ["SITE_NAME"] = "Test Clinic"

        try:
            config = PortalConfig.from_env()
            assert config.port == 9000
            assert config.site_name == "Test Clinic"
        finally:
            del os.environ["LOCAL_PORTAL_PORT"]
            del os.environ["SITE_NAME"]
