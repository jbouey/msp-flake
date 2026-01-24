"""Tests for device database operations."""

import pytest
import tempfile
from pathlib import Path

from network_scanner._types import (
    Device,
    DeviceType,
    DevicePort,
    ScanPolicy,
    DeviceStatus,
    ComplianceStatus,
    DiscoverySource,
    DeviceComplianceCheck,
)
from network_scanner.device_db import DeviceDatabase


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    database = DeviceDatabase(db_path)
    yield database

    # Cleanup
    db_path.unlink(missing_ok=True)
    wal_path = db_path.with_suffix(".db-wal")
    wal_path.unlink(missing_ok=True)
    shm_path = db_path.with_suffix(".db-shm")
    shm_path.unlink(missing_ok=True)


class TestDeviceCRUD:
    """Tests for device CRUD operations."""

    def test_insert_device(self, db: DeviceDatabase):
        """Should insert a new device."""
        device = Device(
            ip_address="192.168.1.100",
            hostname="test-pc",
            device_type=DeviceType.WORKSTATION,
        )

        is_new, is_changed = db.upsert_device(device)

        assert is_new is True
        assert is_changed is False

        # Retrieve and verify
        retrieved = db.get_device_by_ip("192.168.1.100")
        assert retrieved is not None
        assert retrieved.hostname == "test-pc"
        assert retrieved.device_type == DeviceType.WORKSTATION

    def test_update_device(self, db: DeviceDatabase):
        """Should update an existing device."""
        # Insert first
        device = Device(
            ip_address="192.168.1.100",
            hostname="test-pc",
            device_type=DeviceType.WORKSTATION,
        )
        db.upsert_device(device)

        # Update
        device.hostname = "renamed-pc"
        device.device_type = DeviceType.SERVER
        is_new, is_changed = db.upsert_device(device)

        assert is_new is False
        assert is_changed is True

        # Verify update
        retrieved = db.get_device_by_ip("192.168.1.100")
        assert retrieved.hostname == "renamed-pc"
        assert retrieved.device_type == DeviceType.SERVER

    def test_get_devices_by_type(self, db: DeviceDatabase):
        """Should filter devices by type."""
        # Insert devices of different types
        db.upsert_device(Device(ip_address="192.168.1.1", device_type=DeviceType.WORKSTATION))
        db.upsert_device(Device(ip_address="192.168.1.2", device_type=DeviceType.WORKSTATION))
        db.upsert_device(Device(ip_address="192.168.1.3", device_type=DeviceType.SERVER))

        workstations = db.get_devices(device_type=DeviceType.WORKSTATION)
        servers = db.get_devices(device_type=DeviceType.SERVER)

        assert len(workstations) == 2
        assert len(servers) == 1

    def test_get_devices_for_scanning_excludes_medical(self, db: DeviceDatabase):
        """Medical devices should not be returned for scanning."""
        # Insert regular device
        regular = Device(
            ip_address="192.168.1.1",
            device_type=DeviceType.WORKSTATION,
            status=DeviceStatus.MONITORED,
        )
        db.upsert_device(regular)

        # Insert medical device (excluded by default)
        medical = Device(ip_address="192.168.1.2")
        medical.mark_as_medical()
        db.upsert_device(medical)

        scannable = db.get_devices_for_scanning()

        assert len(scannable) == 1
        assert scannable[0].ip_address == "192.168.1.1"

    def test_get_devices_for_scanning_includes_opted_in_medical(self, db: DeviceDatabase):
        """Opted-in medical devices should be returned for scanning."""
        # Insert opted-in medical device
        medical = Device(ip_address="192.168.1.2")
        medical.mark_as_medical()
        medical.opt_in_medical_device()
        medical.status = DeviceStatus.MONITORED
        db.upsert_device(medical)

        scannable = db.get_devices_for_scanning()

        assert len(scannable) == 1
        assert scannable[0].ip_address == "192.168.1.2"


class TestDevicePorts:
    """Tests for device port operations."""

    def test_upsert_ports(self, db: DeviceDatabase):
        """Should insert and update ports."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        ports = [
            DevicePort(device_id=device.id, port=22, service_name="ssh"),
            DevicePort(device_id=device.id, port=443, service_name="https"),
        ]
        db.upsert_device_ports(device.id, ports)

        retrieved = db.get_device_ports(device.id)
        assert len(retrieved) == 2
        assert any(p.port == 22 and p.service_name == "ssh" for p in retrieved)
        assert any(p.port == 443 and p.service_name == "https" for p in retrieved)


class TestScanHistory:
    """Tests for scan history operations."""

    def test_create_and_complete_scan(self, db: DeviceDatabase):
        """Should create and complete a scan record."""
        from datetime import datetime, timezone

        scan_id = "test-scan-123"
        started_at = datetime.now(timezone.utc)

        db.create_scan_record(scan_id, "full", started_at, "manual")

        # Complete the scan
        db.complete_scan(
            scan_id=scan_id,
            devices_found=10,
            new_devices=5,
            changed_devices=2,
            medical_devices_excluded=1,
            methods_used=["nmap", "arp"],
            network_ranges=["192.168.1.0/24"],
        )

        # Verify
        latest = db.get_latest_scan()
        assert latest is not None
        assert latest.id == scan_id
        assert latest.status == "completed"
        assert latest.devices_found == 10
        assert latest.new_devices == 5
        assert latest.medical_devices_excluded == 1
        assert "nmap" in latest.methods_used


class TestComplianceResults:
    """Tests for compliance result storage."""

    def test_store_compliance_results(self, db: DeviceDatabase):
        """Should store compliance results and update device status."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        checks = [
            DeviceComplianceCheck(
                device_id=device.id,
                check_type="firewall",
                hipaa_control="164.312(e)(1)",
                status="pass",
            ),
            DeviceComplianceCheck(
                device_id=device.id,
                check_type="antivirus",
                hipaa_control="164.308(a)(5)(ii)(B)",
                status="pass",
            ),
        ]
        db.store_compliance_results(device.id, checks)

        # Verify device compliance status updated
        retrieved = db.get_device(device.id)
        assert retrieved.compliance_status == ComplianceStatus.COMPLIANT

    def test_failing_check_updates_status(self, db: DeviceDatabase):
        """A failing check should mark device as drifted."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        checks = [
            DeviceComplianceCheck(
                device_id=device.id,
                check_type="firewall",
                status="fail",
            ),
        ]
        db.store_compliance_results(device.id, checks)

        retrieved = db.get_device(device.id)
        assert retrieved.compliance_status == ComplianceStatus.DRIFTED


class TestDevicePolicy:
    """Tests for device policy updates."""

    def test_update_scan_policy(self, db: DeviceDatabase):
        """Should update device scan policy."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        result = db.update_device_policy(
            device.id,
            scan_policy=ScanPolicy.EXCLUDED,
        )

        assert result is True

        retrieved = db.get_device(device.id)
        assert retrieved.scan_policy == ScanPolicy.EXCLUDED

    def test_opt_in_medical_device(self, db: DeviceDatabase):
        """Should opt-in medical device."""
        device = Device(ip_address="192.168.1.100")
        device.mark_as_medical()
        db.upsert_device(device)

        db.update_device_policy(
            device.id,
            scan_policy=ScanPolicy.LIMITED,
            manually_opted_in=True,
        )

        retrieved = db.get_device(device.id)
        assert retrieved.manually_opted_in is True
        assert retrieved.scan_policy == ScanPolicy.LIMITED


class TestDeviceStatistics:
    """Tests for device statistics."""

    def test_get_device_counts(self, db: DeviceDatabase):
        """Should return device counts."""
        # Insert various devices
        db.upsert_device(Device(ip_address="192.168.1.1", device_type=DeviceType.WORKSTATION))
        db.upsert_device(Device(ip_address="192.168.1.2", device_type=DeviceType.WORKSTATION))
        db.upsert_device(Device(ip_address="192.168.1.3", device_type=DeviceType.SERVER))

        # Insert medical device (excluded)
        medical = Device(ip_address="192.168.1.4")
        medical.mark_as_medical()
        db.upsert_device(medical)

        counts = db.get_device_counts()

        assert counts["total"] == 4
        assert counts["by_type"]["workstation"] == 2
        assert counts["by_type"]["server"] == 1
        assert counts["by_type"]["medical"] == 1
        assert counts["medical_excluded"] == 1


class TestSync:
    """Tests for sync operations."""

    def test_get_unsynced_devices(self, db: DeviceDatabase):
        """Should return devices not synced to Central Command."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        unsynced = db.get_unsynced_devices()
        assert len(unsynced) == 1
        assert unsynced[0].ip_address == "192.168.1.100"

    def test_mark_device_synced(self, db: DeviceDatabase):
        """Should mark device as synced."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        db.mark_device_synced(device.id)

        unsynced = db.get_unsynced_devices()
        assert len(unsynced) == 0


class TestDeviceNotes:
    """Tests for device notes."""

    def test_add_and_get_notes(self, db: DeviceDatabase):
        """Should add and retrieve device notes."""
        device = Device(ip_address="192.168.1.100")
        db.upsert_device(device)

        db.add_device_note(
            device.id,
            note="This is an X-ray machine",
            note_type="identification",
        )

        notes = db.get_device_notes(device.id)
        assert len(notes) == 1
        assert notes[0]["note"] == "This is an X-ray machine"
        assert notes[0]["note_type"] == "identification"
