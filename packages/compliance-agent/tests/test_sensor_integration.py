"""
Integration tests for Windows Sensor dual-mode architecture.

Tests the sensor API, registry tracking, and dual-mode loop behavior.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Import the sensor API module
from compliance_agent.sensor_api import (
    SensorHeartbeat,
    SensorDriftEvent,
    SensorResolution,
    SensorStatus,
    sensor_heartbeat_endpoint,
    sensor_drift_endpoint,
    sensor_resolved_endpoint,
    get_sensor_status,
    has_active_sensor,
    get_sensor_hosts,
    get_polling_hosts,
    sensor_registry,
    update_sensor_registry,
    touch_sensor,
    configure_healing,
    get_dual_mode_stats,
    clear_stale_sensors,
    SENSOR_TIMEOUT,
)


class TestSensorRegistry:
    """Test sensor tracking and dual-mode detection."""

    def setup_method(self):
        """Clear registry before each test."""
        sensor_registry.clear()

    def test_has_active_sensor_empty_registry(self):
        """Empty registry should return False for any host."""
        assert has_active_sensor("DC01") is False
        assert has_active_sensor("unknown") is False

    def test_has_active_sensor_after_heartbeat(self):
        """Sensor should be active after heartbeat."""
        heartbeat = SensorHeartbeat(
            hostname="DC01",
            domain="CONTOSO.LOCAL",
            sensor_version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            drift_count=0,
            has_critical=False,
            compliant=True
        )
        update_sensor_registry("DC01", heartbeat)

        assert has_active_sensor("DC01") is True

    def test_has_active_sensor_timeout(self):
        """Sensor should be inactive after timeout."""
        # Register sensor with old timestamp
        old_time = datetime.now(timezone.utc) - timedelta(seconds=SENSOR_TIMEOUT + 60)
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        assert has_active_sensor("DC01") is False

    def test_get_sensor_hosts_empty(self):
        """Empty registry should return empty list."""
        assert get_sensor_hosts() == []

    def test_get_sensor_hosts_with_active(self):
        """Should return only active sensor hosts."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(seconds=SENSOR_TIMEOUT + 60)

        # Active sensor
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )

        # Inactive sensor
        sensor_registry["DC02"] = SensorStatus(
            hostname="DC02",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        hosts = get_sensor_hosts()
        assert "DC01" in hosts
        assert "DC02" not in hosts

    def test_get_polling_hosts(self):
        """Should return only hosts without active sensors."""
        now = datetime.now(timezone.utc)

        # DC01 has active sensor
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )

        all_hosts = ["DC01", "DC02", "FileServer"]
        polling = get_polling_hosts(all_hosts)

        assert "DC01" not in polling
        assert "DC02" in polling
        assert "FileServer" in polling
        assert len(polling) == 2

    def test_touch_sensor_updates_heartbeat(self):
        """touch_sensor should update last_heartbeat time."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        touch_sensor("DC01")

        # Should be updated to recent time
        age = (datetime.now(timezone.utc) - sensor_registry["DC01"].last_heartbeat).total_seconds()
        assert age < 5  # Should be less than 5 seconds old

    def test_touch_sensor_nonexistent(self):
        """touch_sensor should be safe for non-existent hosts."""
        # Should not raise
        touch_sensor("nonexistent")

    def test_get_dual_mode_stats(self):
        """Should return correct dual-mode statistics."""
        now = datetime.now(timezone.utc)

        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )
        sensor_registry["DC02"] = SensorStatus(
            hostname="DC02",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )

        stats = get_dual_mode_stats()

        assert stats["total_sensors"] == 2
        assert stats["active_sensors"] == 2
        assert "DC01" in stats["sensor_hostnames"]
        assert "DC02" in stats["sensor_hostnames"]

    def test_clear_stale_sensors(self):
        """Should remove sensors older than max_age_seconds."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(seconds=7200)  # 2 hours old

        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )
        sensor_registry["DC02"] = SensorStatus(
            hostname="DC02",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        cleared = clear_stale_sensors(max_age_seconds=3600)

        assert cleared == 1
        assert "DC01" in sensor_registry
        assert "DC02" not in sensor_registry


class TestSensorHeartbeat:
    """Test sensor heartbeat endpoint."""

    def setup_method(self):
        sensor_registry.clear()

    @pytest.mark.asyncio
    async def test_heartbeat_registers_sensor(self):
        """Heartbeat should register sensor in registry."""
        heartbeat = SensorHeartbeat(
            hostname="DC01",
            domain="CONTOSO.LOCAL",
            sensor_version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            drift_count=2,
            has_critical=True,
            compliant=False
        )

        response = await sensor_heartbeat_endpoint(heartbeat)

        assert response["status"] == "ok"
        assert response["mode"] == "sensor"
        assert "DC01" in sensor_registry
        assert sensor_registry["DC01"].drift_count == 2
        assert sensor_registry["DC01"].compliant is False

    @pytest.mark.asyncio
    async def test_heartbeat_updates_existing(self):
        """Heartbeat should update existing sensor entry."""
        # Initial heartbeat
        heartbeat1 = SensorHeartbeat(
            hostname="DC01",
            sensor_version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            drift_count=0,
            has_critical=False,
            compliant=True
        )
        await sensor_heartbeat_endpoint(heartbeat1)

        # Updated heartbeat
        heartbeat2 = SensorHeartbeat(
            hostname="DC01",
            sensor_version="1.0.1",
            timestamp=datetime.now(timezone.utc).isoformat(),
            drift_count=3,
            has_critical=True,
            compliant=False
        )
        await sensor_heartbeat_endpoint(heartbeat2)

        assert sensor_registry["DC01"].sensor_version == "1.0.1"
        assert sensor_registry["DC01"].drift_count == 3


class TestSensorDrift:
    """Test sensor drift event handling."""

    def setup_method(self):
        sensor_registry.clear()

    @pytest.mark.asyncio
    async def test_drift_event_queues_healing(self):
        """Drift event should trigger healing queue."""
        event = SensorDriftEvent(
            hostname="DC01",
            drift_type="firewall_disabled",
            severity="critical",
            details={"profile": "Domain"},
            check_id="RB-WIN-FIREWALL-001",
            detected_at=datetime.now(timezone.utc).isoformat()
        )

        background_tasks = AsyncMock()

        response = await sensor_drift_endpoint(event, background_tasks)

        assert response["status"] == "received"
        assert response["healing_queued"] is True
        background_tasks.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_drift_updates_sensor_heartbeat(self):
        """Drift events should update sensor last-seen time."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        event = SensorDriftEvent(
            hostname="DC01",
            drift_type="defender_stopped",
            severity="critical",
            details={},
            detected_at=datetime.now(timezone.utc).isoformat()
        )

        await sensor_drift_endpoint(event, AsyncMock())

        # Should have updated
        age = (datetime.now(timezone.utc) - sensor_registry["DC01"].last_heartbeat).total_seconds()
        assert age < 5


class TestSensorResolution:
    """Test sensor resolution event handling."""

    def setup_method(self):
        sensor_registry.clear()

    @pytest.mark.asyncio
    async def test_resolution_acknowledged(self):
        """Resolution event should be acknowledged."""
        event = SensorResolution(
            hostname="DC01",
            drift_type="firewall_disabled",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            resolved_by="external"
        )

        response = await sensor_resolved_endpoint(event)

        assert response["status"] == "acknowledged"


class TestSensorStatus:
    """Test sensor status endpoint."""

    def setup_method(self):
        sensor_registry.clear()

    @pytest.mark.asyncio
    async def test_status_empty_registry(self):
        """Status should return empty list for empty registry."""
        response = await get_sensor_status()

        assert response["sensors"] == []
        assert response["total"] == 0
        assert response["active"] == 0

    @pytest.mark.asyncio
    async def test_status_with_sensors(self):
        """Status should return all registered sensors."""
        now = datetime.now(timezone.utc)

        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0",
            drift_count=2,
            compliant=False
        )

        response = await get_sensor_status()

        assert response["total"] == 1
        assert response["active"] == 1
        assert len(response["sensors"]) == 1
        assert response["sensors"][0]["hostname"] == "DC01"
        assert response["sensors"][0]["drift_count"] == 2


class TestSensorEventSchema:
    """Test event JSON schema validation."""

    def test_heartbeat_required_fields(self):
        """Heartbeat should require all required fields."""
        data = {
            "hostname": "DC01",
            "sensor_version": "1.0.0",
            "timestamp": "2026-01-08T14:30:00Z",
            "drift_count": 2,
            "has_critical": True,
            "compliant": False
        }

        heartbeat = SensorHeartbeat(**data)
        assert heartbeat.hostname == "DC01"
        assert heartbeat.drift_count == 2

    def test_heartbeat_optional_fields(self):
        """Heartbeat optional fields should have defaults."""
        data = {
            "hostname": "DC01",
            "sensor_version": "1.0.0",
            "timestamp": "2026-01-08T14:30:00Z",
            "drift_count": 0,
            "has_critical": False,
            "compliant": True
        }

        heartbeat = SensorHeartbeat(**data)
        assert heartbeat.domain is None
        assert heartbeat.uptime_seconds is None
        assert heartbeat.mode == "sensor"

    def test_drift_event_required_fields(self):
        """Drift event should require all required fields."""
        data = {
            "hostname": "DC01",
            "drift_type": "firewall_disabled",
            "severity": "critical",
            "detected_at": "2026-01-08T14:30:00Z"
        }

        event = SensorDriftEvent(**data)
        assert event.drift_type == "firewall_disabled"
        assert event.severity == "critical"

    def test_drift_event_optional_fields(self):
        """Drift event optional fields should have defaults."""
        data = {
            "hostname": "DC01",
            "drift_type": "firewall_disabled",
            "severity": "critical",
            "detected_at": "2026-01-08T14:30:00Z"
        }

        event = SensorDriftEvent(**data)
        assert event.domain is None
        assert event.details == {}
        assert event.check_id is None


class TestConfigureHealing:
    """Test healing configuration for sensor API."""

    def test_configure_healing_sets_globals(self):
        """configure_healing should set global healing dependencies."""
        mock_healer = MagicMock()
        mock_targets = [MagicMock()]
        mock_db = MagicMock()
        mock_config = MagicMock()

        configure_healing(
            auto_healer=mock_healer,
            windows_targets=mock_targets,
            incident_db=mock_db,
            config=mock_config
        )

        # Verify by importing the module-level variables
        from compliance_agent import sensor_api
        assert sensor_api._auto_healer == mock_healer
        assert sensor_api._windows_targets == mock_targets


class TestDualModeIntegration:
    """Integration tests for dual-mode behavior."""

    def setup_method(self):
        sensor_registry.clear()

    def test_polling_fallback_when_sensor_offline(self):
        """Should include host in polling list when sensor goes offline."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=SENSOR_TIMEOUT + 60)

        # Sensor was active but is now offline
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        all_hosts = ["DC01", "DC02"]
        polling = get_polling_hosts(all_hosts)

        # Both should be in polling list
        assert "DC01" in polling
        assert "DC02" in polling

    def test_skip_polling_for_active_sensor(self):
        """Should skip polling for host with active sensor."""
        now = datetime.now(timezone.utc)

        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )

        all_hosts = ["DC01", "DC02"]
        polling = get_polling_hosts(all_hosts)

        assert "DC01" not in polling
        assert "DC02" in polling

    def test_mixed_mode_operation(self):
        """Test mixed sensor/polling mode operation."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(seconds=SENSOR_TIMEOUT + 60)

        # Active sensor
        sensor_registry["DC01"] = SensorStatus(
            hostname="DC01",
            last_heartbeat=now,
            sensor_version="1.0.0"
        )

        # Offline sensor
        sensor_registry["DC02"] = SensorStatus(
            hostname="DC02",
            last_heartbeat=old_time,
            sensor_version="1.0.0"
        )

        all_hosts = ["DC01", "DC02", "FileServer"]

        sensor_hosts = get_sensor_hosts()
        polling_hosts = get_polling_hosts(all_hosts)

        # DC01 has active sensor
        assert "DC01" in sensor_hosts
        assert "DC01" not in polling_hosts

        # DC02 sensor is offline - should poll
        assert "DC02" not in sensor_hosts
        assert "DC02" in polling_hosts

        # FileServer never had sensor - should poll
        assert "FileServer" not in sensor_hosts
        assert "FileServer" in polling_hosts
