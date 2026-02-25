"""
Device database for network scanner.

SQLite database at /var/lib/msp/devices.db storing:
- Discovered devices and their classification
- Open ports per device
- Scan history
- Compliance check results

Uses WAL mode for crash safety and concurrent reads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ._types import (
    ComplianceStatus,
    Device,
    DeviceComplianceCheck,
    DevicePort,
    DeviceStatus,
    DeviceType,
    DiscoverySource,
    ScanHistory,
    ScanPolicy,
    ScanResult,
    now_utc,
)

logger = logging.getLogger(__name__)


# Database schema
SCHEMA = """
-- Core device inventory
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    hostname TEXT,
    ip_address TEXT NOT NULL UNIQUE,
    mac_address TEXT,
    device_type TEXT NOT NULL DEFAULT 'unknown',
    os_name TEXT,
    os_version TEXT,
    manufacturer TEXT,
    model TEXT,

    -- Medical device handling (EXCLUDED BY DEFAULT)
    medical_device BOOLEAN DEFAULT FALSE,
    scan_policy TEXT DEFAULT 'standard',
    manually_opted_in BOOLEAN DEFAULT FALSE,

    -- PHI access tracking
    phi_access_flag BOOLEAN DEFAULT FALSE,

    -- Discovery metadata
    discovery_source TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_scan_at TEXT,

    -- Status
    status TEXT DEFAULT 'discovered',
    online BOOLEAN DEFAULT FALSE,

    -- Compliance
    compliance_status TEXT DEFAULT 'unknown',
    last_compliance_check TEXT,

    -- Sync tracking
    synced_to_central BOOLEAN DEFAULT FALSE,
    sync_version INTEGER DEFAULT 0
);

-- Device open ports (from nmap scans)
CREATE TABLE IF NOT EXISTS device_ports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT DEFAULT 'tcp',
    service_name TEXT,
    service_version TEXT,
    state TEXT DEFAULT 'open',
    last_seen_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    UNIQUE(device_id, port, protocol)
);

-- Scan history
CREATE TABLE IF NOT EXISTS scan_history (
    id TEXT PRIMARY KEY,
    scan_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT DEFAULT 'running',

    -- Results
    devices_found INTEGER DEFAULT 0,
    new_devices INTEGER DEFAULT 0,
    changed_devices INTEGER DEFAULT 0,
    medical_devices_excluded INTEGER DEFAULT 0,

    -- Methods used
    methods_used TEXT,  -- JSON array
    network_ranges TEXT,  -- JSON array

    error_message TEXT,
    triggered_by TEXT DEFAULT 'schedule'
);

-- Compliance check results per device
CREATE TABLE IF NOT EXISTS device_compliance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    check_type TEXT NOT NULL,
    hipaa_control TEXT,
    status TEXT NOT NULL,
    details TEXT,  -- JSON
    checked_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- Manual exclusions/notes
CREATE TABLE IF NOT EXISTS device_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    note_type TEXT,
    note TEXT NOT NULL,
    created_by TEXT DEFAULT 'local',
    created_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_ip ON devices(ip_address);
CREATE INDEX IF NOT EXISTS idx_devices_medical ON devices(medical_device);
CREATE INDEX IF NOT EXISTS idx_devices_sync ON devices(synced_to_central);
CREATE INDEX IF NOT EXISTS idx_device_ports_device ON device_ports(device_id);
CREATE INDEX IF NOT EXISTS idx_scan_history_status ON scan_history(status);
CREATE INDEX IF NOT EXISTS idx_scan_history_started ON scan_history(started_at);
CREATE INDEX IF NOT EXISTS idx_device_compliance_device ON device_compliance(device_id);
CREATE INDEX IF NOT EXISTS idx_device_compliance_check ON device_compliance(check_type);
"""


def _iso_format(dt: datetime) -> str:
    """Format datetime as ISO string."""
    return dt.isoformat()


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class DeviceDatabase:
    """
    SQLite database for device inventory and scan history.

    Thread-safe with WAL mode enabled for concurrent reads.
    """

    def __init__(self, db_path: Path | str = "/var/lib/msp/devices.db"):
        self.db_path = Path(db_path)
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self) -> None:
        """Ensure database directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """Initialize database with schema."""
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)
            # Enable WAL mode for crash safety
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Device CRUD
    # -------------------------------------------------------------------------

    def upsert_device(self, device: Device) -> tuple[bool, bool]:
        """
        Insert or update a device.

        Returns: (is_new, is_changed)
        """
        with self._get_connection() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT id, device_type, status, scan_policy FROM devices WHERE ip_address = ?",
                (device.ip_address,)
            ).fetchone()

            if existing:
                # Update existing device
                old_type = existing["device_type"]
                is_changed = old_type != device.device_type.value

                conn.execute("""
                    UPDATE devices SET
                        hostname = ?,
                        mac_address = ?,
                        device_type = ?,
                        os_name = ?,
                        os_version = ?,
                        manufacturer = ?,
                        model = ?,
                        medical_device = ?,
                        scan_policy = ?,
                        discovery_source = ?,
                        last_seen_at = ?,
                        online = ?,
                        sync_version = sync_version + 1,
                        synced_to_central = FALSE
                    WHERE ip_address = ?
                """, (
                    device.hostname,
                    device.mac_address,
                    device.device_type.value,
                    device.os_name,
                    device.os_version,
                    device.manufacturer,
                    device.model,
                    device.medical_device,
                    device.scan_policy.value,
                    device.discovery_source.value,
                    _iso_format(device.last_seen_at),
                    device.online,
                    device.ip_address,
                ))
                conn.commit()
                return (False, is_changed)
            else:
                # Insert new device
                conn.execute("""
                    INSERT INTO devices (
                        id, hostname, ip_address, mac_address, device_type,
                        os_name, os_version, manufacturer, model,
                        medical_device, scan_policy, manually_opted_in,
                        phi_access_flag, discovery_source, first_seen_at,
                        last_seen_at, status, online, compliance_status,
                        synced_to_central, sync_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    device.id,
                    device.hostname,
                    device.ip_address,
                    device.mac_address,
                    device.device_type.value,
                    device.os_name,
                    device.os_version,
                    device.manufacturer,
                    device.model,
                    device.medical_device,
                    device.scan_policy.value,
                    device.manually_opted_in,
                    device.phi_access_flag,
                    device.discovery_source.value,
                    _iso_format(device.first_seen_at),
                    _iso_format(device.last_seen_at),
                    device.status.value,
                    device.online,
                    device.compliance_status.value,
                    device.synced_to_central,
                    device.sync_version,
                ))
                conn.commit()
                return (True, False)

    def get_device(self, device_id: str) -> Optional[Device]:
        """Get device by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if row:
                return self._row_to_device(row)
            return None

    def get_device_by_ip(self, ip_address: str) -> Optional[Device]:
        """Get device by IP address."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE ip_address = ?", (ip_address,)
            ).fetchone()
            if row:
                return self._row_to_device(row)
            return None

    def get_devices(
        self,
        device_type: Optional[DeviceType] = None,
        status: Optional[DeviceStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Device]:
        """Get devices with optional filters."""
        query = "SELECT * FROM devices WHERE 1=1"
        params: list = []

        if device_type:
            query += " AND device_type = ?"
            params.append(device_type.value)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_device(row) for row in rows]

    def get_devices_for_scanning(self) -> list[Device]:
        """Get devices eligible for compliance scanning."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM devices
                WHERE scan_policy != 'excluded'
                  AND status = 'monitored'
                  AND (medical_device = FALSE OR manually_opted_in = TRUE)
                ORDER BY last_scan_at ASC NULLS FIRST
            """).fetchall()
            return [self._row_to_device(row) for row in rows]

    def get_unsynced_devices(self) -> list[Device]:
        """Get devices not yet synced to Central Command."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE synced_to_central = FALSE"
            ).fetchall()
            return [self._row_to_device(row) for row in rows]

    def mark_device_synced(self, device_id: str) -> None:
        """Mark device as synced to Central Command."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE devices SET synced_to_central = TRUE WHERE id = ?",
                (device_id,)
            )
            conn.commit()

    def update_device_status(self, device_id: str, status: DeviceStatus) -> None:
        """Update device lifecycle status."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE devices SET status = ?, synced_to_central = FALSE WHERE id = ?",
                (status.value, device_id),
            )
            conn.commit()

    def update_device_policy(
        self,
        device_id: str,
        scan_policy: Optional[ScanPolicy] = None,
        manually_opted_in: Optional[bool] = None,
        phi_access_flag: Optional[bool] = None,
    ) -> bool:
        """Update device scan policy."""
        updates = []
        params = []

        if scan_policy is not None:
            updates.append("scan_policy = ?")
            params.append(scan_policy.value)
        if manually_opted_in is not None:
            updates.append("manually_opted_in = ?")
            params.append(manually_opted_in)
        if phi_access_flag is not None:
            updates.append("phi_access_flag = ?")
            params.append(phi_access_flag)

        if not updates:
            return False

        updates.append("sync_version = sync_version + 1")
        updates.append("synced_to_central = FALSE")

        query = f"UPDATE devices SET {', '.join(updates)} WHERE id = ?"
        params.append(device_id)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        """Convert database row to Device object."""
        return Device(
            id=row["id"],
            hostname=row["hostname"],
            ip_address=row["ip_address"],
            mac_address=row["mac_address"],
            device_type=DeviceType(row["device_type"]),
            os_name=row["os_name"],
            os_version=row["os_version"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            medical_device=bool(row["medical_device"]),
            scan_policy=ScanPolicy(row["scan_policy"]),
            manually_opted_in=bool(row["manually_opted_in"]),
            phi_access_flag=bool(row["phi_access_flag"]),
            discovery_source=DiscoverySource(row["discovery_source"]) if row["discovery_source"] else DiscoverySource.NMAP,
            first_seen_at=_parse_datetime(row["first_seen_at"]) or now_utc(),
            last_seen_at=_parse_datetime(row["last_seen_at"]) or now_utc(),
            last_scan_at=_parse_datetime(row["last_scan_at"]),
            status=DeviceStatus(row["status"]),
            online=bool(row["online"]),
            compliance_status=ComplianceStatus(row["compliance_status"]),
            last_compliance_check=_parse_datetime(row["last_compliance_check"]),
            synced_to_central=bool(row["synced_to_central"]),
            sync_version=row["sync_version"],
        )

    # -------------------------------------------------------------------------
    # Device Ports
    # -------------------------------------------------------------------------

    def upsert_device_ports(self, device_id: str, ports: list[DevicePort]) -> None:
        """Update ports for a device."""
        with self._get_connection() as conn:
            for port in ports:
                conn.execute("""
                    INSERT INTO device_ports (device_id, port, protocol, service_name, service_version, state, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, port, protocol) DO UPDATE SET
                        service_name = excluded.service_name,
                        service_version = excluded.service_version,
                        state = excluded.state,
                        last_seen_at = excluded.last_seen_at
                """, (
                    device_id,
                    port.port,
                    port.protocol,
                    port.service_name,
                    port.service_version,
                    port.state,
                    _iso_format(port.last_seen_at),
                ))
            conn.commit()

    def get_device_ports(self, device_id: str) -> list[DevicePort]:
        """Get open ports for a device."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM device_ports WHERE device_id = ? ORDER BY port",
                (device_id,)
            ).fetchall()
            return [
                DevicePort(
                    device_id=row["device_id"],
                    port=row["port"],
                    protocol=row["protocol"],
                    service_name=row["service_name"],
                    service_version=row["service_version"],
                    state=row["state"],
                    last_seen_at=_parse_datetime(row["last_seen_at"]) or now_utc(),
                )
                for row in rows
            ]

    # -------------------------------------------------------------------------
    # Scan History
    # -------------------------------------------------------------------------

    def create_scan_record(
        self,
        scan_id: str,
        scan_type: str,
        started_at: datetime,
        triggered_by: str,
    ) -> None:
        """Create a new scan history record."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO scan_history (id, scan_type, started_at, status, triggered_by)
                VALUES (?, ?, ?, 'running', ?)
            """, (scan_id, scan_type, _iso_format(started_at), triggered_by))
            conn.commit()

    def complete_scan(
        self,
        scan_id: str,
        devices_found: int,
        new_devices: int,
        changed_devices: int,
        medical_devices_excluded: int,
        methods_used: list[str],
        network_ranges: list[str],
        error_message: Optional[str] = None,
    ) -> None:
        """Mark scan as completed with results."""
        status = "completed" if error_message is None else "failed"
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE scan_history SET
                    completed_at = ?,
                    status = ?,
                    devices_found = ?,
                    new_devices = ?,
                    changed_devices = ?,
                    medical_devices_excluded = ?,
                    methods_used = ?,
                    network_ranges = ?,
                    error_message = ?
                WHERE id = ?
            """, (
                _iso_format(now_utc()),
                status,
                devices_found,
                new_devices,
                changed_devices,
                medical_devices_excluded,
                json.dumps(methods_used),
                json.dumps(network_ranges),
                error_message,
                scan_id,
            ))
            conn.commit()

    def get_scan_history(self, limit: int = 50) -> list[ScanHistory]:
        """Get recent scan history."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM scan_history ORDER BY started_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [
                ScanHistory(
                    id=row["id"],
                    scan_type=row["scan_type"],
                    started_at=_parse_datetime(row["started_at"]) or now_utc(),
                    completed_at=_parse_datetime(row["completed_at"]),
                    status=row["status"],
                    devices_found=row["devices_found"],
                    new_devices=row["new_devices"],
                    changed_devices=row["changed_devices"],
                    medical_devices_excluded=row["medical_devices_excluded"] or 0,
                    methods_used=json.loads(row["methods_used"] or "[]"),
                    network_ranges=json.loads(row["network_ranges"] or "[]"),
                    error_message=row["error_message"],
                    triggered_by=row["triggered_by"],
                )
                for row in rows
            ]

    def get_latest_scan(self) -> Optional[ScanHistory]:
        """Get the most recent scan."""
        history = self.get_scan_history(limit=1)
        return history[0] if history else None

    # -------------------------------------------------------------------------
    # Compliance Checks
    # -------------------------------------------------------------------------

    def store_compliance_results(
        self,
        device_id: str,
        checks: list[DeviceComplianceCheck],
    ) -> None:
        """Store compliance check results for a device."""
        with self._get_connection() as conn:
            for check in checks:
                conn.execute("""
                    INSERT INTO device_compliance (device_id, check_type, hipaa_control, status, details, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    device_id,
                    check.check_type,
                    check.hipaa_control,
                    check.status,
                    json.dumps(check.details) if check.details else None,
                    _iso_format(check.checked_at),
                ))

            # Update device compliance status
            overall_status = "compliant"
            for check in checks:
                if check.status == "fail":
                    overall_status = "drifted"
                    break
                elif check.status == "warn" and overall_status != "drifted":
                    overall_status = "drifted"

            conn.execute("""
                UPDATE devices SET
                    compliance_status = ?,
                    last_compliance_check = ?,
                    last_scan_at = ?,
                    synced_to_central = FALSE
                WHERE id = ?
            """, (overall_status, _iso_format(now_utc()), _iso_format(now_utc()), device_id))
            conn.commit()

    def get_device_compliance_history(
        self,
        device_id: str,
        limit: int = 100,
    ) -> list[DeviceComplianceCheck]:
        """Get compliance check history for a device."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM device_compliance
                WHERE device_id = ?
                ORDER BY checked_at DESC
                LIMIT ?
            """, (device_id, limit)).fetchall()
            return [
                DeviceComplianceCheck(
                    id=row["id"],
                    device_id=row["device_id"],
                    check_type=row["check_type"],
                    hipaa_control=row["hipaa_control"],
                    status=row["status"],
                    details=json.loads(row["details"]) if row["details"] else None,
                    checked_at=_parse_datetime(row["checked_at"]) or now_utc(),
                )
                for row in rows
            ]

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_device_counts(self) -> dict[str, int]:
        """Get device counts by type and status."""
        with self._get_connection() as conn:
            result = {
                "total": 0,
                "by_type": {},
                "by_status": {},
                "medical_excluded": 0,
                "compliant": 0,
                "drifted": 0,
            }

            # Total count
            row = conn.execute("SELECT COUNT(*) as cnt FROM devices").fetchone()
            result["total"] = row["cnt"]

            # By type
            rows = conn.execute(
                "SELECT device_type, COUNT(*) as cnt FROM devices GROUP BY device_type"
            ).fetchall()
            result["by_type"] = {row["device_type"]: row["cnt"] for row in rows}

            # By status
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM devices GROUP BY status"
            ).fetchall()
            result["by_status"] = {row["status"]: row["cnt"] for row in rows}

            # Medical excluded
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM devices WHERE medical_device = TRUE AND manually_opted_in = FALSE"
            ).fetchone()
            result["medical_excluded"] = row["cnt"]

            # Compliance status
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM devices WHERE compliance_status = 'compliant'"
            ).fetchone()
            result["compliant"] = row["cnt"]

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM devices WHERE compliance_status = 'drifted'"
            ).fetchone()
            result["drifted"] = row["cnt"]

            return result

    # -------------------------------------------------------------------------
    # Notes
    # -------------------------------------------------------------------------

    def add_device_note(
        self,
        device_id: str,
        note: str,
        note_type: str = "other",
        created_by: str = "local",
    ) -> None:
        """Add a note to a device."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO device_notes (device_id, note_type, note, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (device_id, note_type, note, created_by, _iso_format(now_utc())))
            conn.commit()

    def get_device_notes(self, device_id: str) -> list[dict]:
        """Get notes for a device."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM device_notes WHERE device_id = ? ORDER BY created_at DESC",
                (device_id,)
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "note_type": row["note_type"],
                    "note": row["note"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
