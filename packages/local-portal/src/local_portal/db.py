"""
Database access for Local Portal.

Reads from the network-scanner's SQLite database.
"""

import sqlite3
from pathlib import Path
from typing import Optional


class PortalDatabase:
    """Read access to the network scanner database."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection with WAL mode."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            # WAL mode for better concurrency
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def get_devices(
        self,
        device_type: Optional[str] = None,
        status: Optional[str] = None,
        compliance_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Get devices with optional filtering."""
        query = "SELECT * FROM devices WHERE 1=1"
        params = []

        if device_type:
            query += " AND device_type = ?"
            params.append(device_type)

        if status:
            query += " AND status = ?"
            params.append(status)

        if compliance_status:
            query += " AND compliance_status = ?"
            params.append(compliance_status)

        query += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_device(self, device_id: str) -> Optional[dict]:
        """Get a single device by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM devices WHERE id = ?",
            (device_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_device_ports(self, device_id: str) -> list[dict]:
        """Get ports for a device."""
        cursor = self.conn.execute(
            "SELECT * FROM device_ports WHERE device_id = ?",
            (device_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_device_compliance_checks(self, device_id: str) -> list[dict]:
        """Get compliance checks for a device."""
        cursor = self.conn.execute(
            """SELECT * FROM device_compliance
               WHERE device_id = ?
               ORDER BY checked_at DESC""",
            (device_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_device_counts(self) -> dict:
        """Get device count statistics."""
        cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'monitored' THEN 1 ELSE 0 END) as monitored,
                SUM(CASE WHEN status = 'discovered' THEN 1 ELSE 0 END) as discovered,
                SUM(CASE WHEN status = 'excluded' THEN 1 ELSE 0 END) as excluded,
                SUM(CASE WHEN status = 'offline' THEN 1 ELSE 0 END) as offline,
                SUM(CASE WHEN medical_device = 1 THEN 1 ELSE 0 END) as medical
            FROM devices
        """)
        row = cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "monitored": row["monitored"] or 0,
            "discovered": row["discovered"] or 0,
            "excluded": row["excluded"] or 0,
            "offline": row["offline"] or 0,
            "medical": row["medical"] or 0,
        }

    def get_compliance_summary(self) -> dict:
        """Get compliance status summary."""
        cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN compliance_status = 'compliant' THEN 1 ELSE 0 END) as compliant,
                SUM(CASE WHEN compliance_status = 'drifted' THEN 1 ELSE 0 END) as drifted,
                SUM(CASE WHEN compliance_status = 'unknown' THEN 1 ELSE 0 END) as unknown,
                SUM(CASE WHEN compliance_status = 'excluded' THEN 1 ELSE 0 END) as excluded
            FROM devices
            WHERE scan_policy != 'excluded'
        """)
        row = cursor.fetchone()
        total = row["total"] or 0
        compliant = row["compliant"] or 0

        return {
            "total": total,
            "compliant": compliant,
            "drifted": row["drifted"] or 0,
            "unknown": row["unknown"] or 0,
            "excluded": row["excluded"] or 0,
            "compliance_rate": round(compliant / total * 100, 1) if total > 0 else 0.0,
        }

    def get_device_types_summary(self) -> list[dict]:
        """Get count of devices by type."""
        cursor = self.conn.execute("""
            SELECT device_type, COUNT(*) as count
            FROM devices
            GROUP BY device_type
            ORDER BY count DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_scan_history(self, limit: int = 10) -> list[dict]:
        """Get recent scan history."""
        cursor = self.conn.execute(
            """SELECT * FROM scan_history
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_latest_scan(self) -> Optional[dict]:
        """Get the most recent scan."""
        cursor = self.conn.execute(
            """SELECT * FROM scan_history
               ORDER BY started_at DESC
               LIMIT 1"""
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_device_notes(self, device_id: str) -> list[dict]:
        """Get notes for a device."""
        cursor = self.conn.execute(
            """SELECT * FROM device_notes
               WHERE device_id = ?
               ORDER BY created_at DESC""",
            (device_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


# Global database instance
_db: Optional[PortalDatabase] = None


def get_db(db_path: Optional[Path] = None) -> PortalDatabase:
    """Get or create database instance."""
    global _db
    if _db is None:
        path = db_path or Path("/var/lib/msp/devices.db")
        _db = PortalDatabase(path)
    return _db
