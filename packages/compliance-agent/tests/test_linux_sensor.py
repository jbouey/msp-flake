"""Tests for Linux Sensor API."""

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from compliance_agent.sensor_linux import (
    router,
    LinuxSensorHeartbeat,
    LinuxSensorEvent,
    LinuxSensorStatus,
    linux_sensor_registry,
    has_active_linux_sensor,
    get_linux_sensor_hosts,
    get_linux_polling_hosts,
    update_linux_sensor_registry,
    generate_sensor_credentials,
    clear_stale_linux_sensors,
    LINUX_SENSOR_TIMEOUT,
)


@pytest.fixture
def app():
    """Create test FastAPI app."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear sensor registry before each test."""
    linux_sensor_registry.clear()
    yield
    linux_sensor_registry.clear()


class TestLinuxSensorModels:
    """Test Linux sensor data models."""

    def test_heartbeat_model(self):
        """Test LinuxSensorHeartbeat model."""
        heartbeat = LinuxSensorHeartbeat(
            sensor_id="lsens-abc123",
            hostname="webserver01",
            version="1.0.0",
            uptime=3600,
            timestamp="2026-01-10T12:00:00Z"
        )
        assert heartbeat.sensor_id == "lsens-abc123"
        assert heartbeat.hostname == "webserver01"
        assert heartbeat.version == "1.0.0"
        assert heartbeat.uptime == 3600

    def test_event_model(self):
        """Test LinuxSensorEvent model."""
        event = LinuxSensorEvent(
            sensor_id="lsens-abc123",
            hostname="webserver01",
            check_type="ssh_config",
            severity="high",
            title="SSH Password Auth Enabled",
            details="Password authentication is enabled",
            current_value="yes",
            expected_value="no",
            timestamp="2026-01-10T12:00:00Z"
        )
        assert event.check_type == "ssh_config"
        assert event.severity == "high"
        assert event.current_value == "yes"
        assert event.expected_value == "no"


class TestLinuxSensorRegistry:
    """Test sensor registry functions."""

    def test_generate_sensor_credentials(self):
        """Test sensor credential generation."""
        sensor_id, api_key = generate_sensor_credentials()

        assert sensor_id.startswith("lsens-")
        assert len(sensor_id) == 22  # lsens- + 16 hex chars
        assert len(api_key) > 20  # URL-safe base64

    def test_update_registry_from_heartbeat(self):
        """Test registry update from heartbeat."""
        heartbeat = LinuxSensorHeartbeat(
            sensor_id="lsens-test123",
            hostname="server01",
            version="1.0.0",
            uptime=3600,
            timestamp="2026-01-10T12:00:00Z"
        )

        update_linux_sensor_registry("lsens-test123", heartbeat)

        assert "lsens-test123" in linux_sensor_registry
        status = linux_sensor_registry["lsens-test123"]
        assert status.hostname == "server01"
        assert status.version == "1.0.0"
        assert status.uptime == 3600

    def test_has_active_sensor_true(self):
        """Test has_active_linux_sensor returns True for active sensor."""
        heartbeat = LinuxSensorHeartbeat(
            sensor_id="lsens-active",
            hostname="active-host",
            version="1.0.0",
            uptime=100,
            timestamp="2026-01-10T12:00:00Z"
        )
        update_linux_sensor_registry("lsens-active", heartbeat)

        assert has_active_linux_sensor("active-host") is True
        assert has_active_linux_sensor("ACTIVE-HOST") is True  # Case insensitive

    def test_has_active_sensor_false(self):
        """Test has_active_linux_sensor returns False for unknown host."""
        assert has_active_linux_sensor("unknown-host") is False

    def test_get_sensor_hosts(self):
        """Test get_linux_sensor_hosts returns active hostnames."""
        for i in range(3):
            heartbeat = LinuxSensorHeartbeat(
                sensor_id=f"lsens-{i}",
                hostname=f"server{i}",
                version="1.0.0",
                uptime=100,
                timestamp="2026-01-10T12:00:00Z"
            )
            update_linux_sensor_registry(f"lsens-{i}", heartbeat)

        hosts = get_linux_sensor_hosts()
        assert len(hosts) == 3
        assert "server0" in hosts
        assert "server1" in hosts
        assert "server2" in hosts

    def test_get_polling_hosts(self):
        """Test get_linux_polling_hosts excludes sensor hosts."""
        # Register one sensor
        heartbeat = LinuxSensorHeartbeat(
            sensor_id="lsens-sensor1",
            hostname="server1",
            version="1.0.0",
            uptime=100,
            timestamp="2026-01-10T12:00:00Z"
        )
        update_linux_sensor_registry("lsens-sensor1", heartbeat)

        all_targets = ["server1", "server2", "server3"]
        polling = get_linux_polling_hosts(all_targets)

        assert "server1" not in polling  # Has sensor
        assert "server2" in polling
        assert "server3" in polling

    def test_clear_stale_sensors(self):
        """Test stale sensor cleanup."""
        # Create a stale sensor entry
        linux_sensor_registry["lsens-stale"] = LinuxSensorStatus(
            sensor_id="lsens-stale",
            hostname="stale-host",
            last_heartbeat=datetime(2020, 1, 1, tzinfo=timezone.utc),
            version="1.0.0"
        )

        # Create a fresh sensor entry
        linux_sensor_registry["lsens-fresh"] = LinuxSensorStatus(
            sensor_id="lsens-fresh",
            hostname="fresh-host",
            last_heartbeat=datetime.now(timezone.utc),
            version="1.0.0"
        )

        removed = clear_stale_linux_sensors(max_age_seconds=3600)

        assert removed == 1
        assert "lsens-stale" not in linux_sensor_registry
        assert "lsens-fresh" in linux_sensor_registry


class TestLinuxSensorAPI:
    """Test Linux sensor API endpoints."""

    def test_heartbeat_endpoint(self, client):
        """Test sensor heartbeat endpoint."""
        response = client.post("/sensor/heartbeat", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "version": "1.0.0",
            "uptime": 1000,
            "timestamp": "2026-01-10T12:00:00Z"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "sensor"
        assert "lsens-test" in linux_sensor_registry

    def test_event_endpoint(self, client):
        """Test sensor event endpoint."""
        # First register the sensor via heartbeat
        client.post("/sensor/heartbeat", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "version": "1.0.0",
            "uptime": 1000,
            "timestamp": "2026-01-10T12:00:00Z"
        })

        # Send event
        response = client.post("/sensor/event", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "check_type": "disk_space",
            "severity": "high",
            "title": "Disk Space Critical",
            "details": "Root filesystem at 95%",
            "current_value": "95%",
            "expected_value": "<90%",
            "timestamp": "2026-01-10T12:00:00Z"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["healing_queued"] is True

        # Verify event count updated
        assert linux_sensor_registry["lsens-test"].event_count == 1
        assert linux_sensor_registry["lsens-test"].last_event_type == "disk_space"

    def test_status_endpoint(self, client):
        """Test sensor status endpoint."""
        # Register a sensor
        client.post("/sensor/heartbeat", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "version": "1.0.0",
            "uptime": 1000,
            "timestamp": "2026-01-10T12:00:00Z"
        })

        response = client.get("/sensor/status")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["active"] == 1
        assert len(data["sensors"]) == 1
        assert data["sensors"][0]["hostname"] == "testhost"

    def test_register_endpoint(self, client):
        """Test sensor registration endpoint."""
        response = client.post("/sensor/register", json={
            "hostname": "newserver",
            "os_version": "Ubuntu 22.04",
            "kernel": "5.15.0-generic"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["sensor_id"].startswith("lsens-")
        assert len(data["api_key"]) > 20
        assert data["check_interval"] == 10
        assert data["heartbeat_interval"] == 60


class TestLinuxSensorEventTypes:
    """Test all supported Linux sensor check types."""

    @pytest.fixture
    def registered_client(self, client):
        """Client with a registered sensor."""
        client.post("/sensor/heartbeat", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "version": "1.0.0",
            "uptime": 1000,
            "timestamp": "2026-01-10T12:00:00Z"
        })
        return client

    @pytest.mark.parametrize("check_type,severity", [
        ("ssh_config", "high"),
        ("firewall", "medium"),
        ("failed_logins", "high"),
        ("disk_space", "critical"),
        ("memory", "medium"),
        ("users", "high"),
        ("services", "high"),
        ("file_integrity", "critical"),
        ("open_ports", "high"),
        ("updates", "medium"),
        ("audit_logs", "low"),
        ("cron_jobs", "high"),
    ])
    def test_all_check_types(self, registered_client, check_type, severity):
        """Test all Linux sensor check types are accepted."""
        response = registered_client.post("/sensor/event", json={
            "sensor_id": "lsens-test",
            "hostname": "testhost",
            "check_type": check_type,
            "severity": severity,
            "title": f"Test {check_type} event",
            "details": "Test details",
            "timestamp": "2026-01-10T12:00:00Z"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
