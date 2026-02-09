"""
Incident Database for Auto-Healing Architecture.

Provides historical context for all three levels:
- Level 1: Pattern matching for rule promotion
- Level 2: Context for LLM decisions
- Level 3: Rich ticket generation with history

Implements the "data flywheel" for continuous improvement.
"""

import sqlite3
import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum


class ResolutionLevel(str, Enum):
    """Which level resolved the incident."""
    LEVEL1_DETERMINISTIC = "L1"
    LEVEL2_LLM = "L2"
    LEVEL3_HUMAN = "L3"
    UNRESOLVED = "UNRESOLVED"


class IncidentOutcome(str, Enum):
    """Outcome of incident resolution."""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    ESCALATED = "escalated"
    TIMEOUT = "timeout"


@dataclass
class Incident:
    """Represents an incident in the system."""
    id: str
    site_id: str
    host_id: str
    incident_type: str
    severity: str
    raw_data: Dict[str, Any]
    pattern_signature: str
    created_at: str
    resolved_at: Optional[str] = None
    resolution_level: Optional[str] = None
    resolution_action: Optional[str] = None
    outcome: Optional[str] = None
    resolution_time_ms: Optional[int] = None
    human_feedback: Optional[str] = None
    promoted_to_l1: bool = False


@dataclass
class PatternStats:
    """Statistics for a pattern signature."""
    pattern_signature: str
    total_occurrences: int
    l1_resolutions: int
    l2_resolutions: int
    l3_resolutions: int
    success_rate: float
    avg_resolution_time_ms: float
    last_seen: str
    recommended_action: Optional[str] = None
    promotion_eligible: bool = False


class IncidentDatabase:
    """
    SQLite-based incident database for the data flywheel.

    Features:
    - Append-only incident log (WORM-style)
    - Pattern signature tracking for L2→L1 promotion
    - Historical context for LLM decisions
    - Rich data for ticket generation
    """

    def __init__(self, db_path: str = "/var/lib/msp-compliance-agent/incidents.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")

        # Main incidents table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                raw_data TEXT NOT NULL,
                pattern_signature TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution_level TEXT,
                resolution_action TEXT,
                outcome TEXT,
                resolution_time_ms INTEGER,
                human_feedback TEXT,
                promoted_to_l1 BOOLEAN DEFAULT 0,

                -- Indexes for common queries
                UNIQUE(id)
            )
        """)

        # Pattern statistics table (materialized view, updated on resolution)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_stats (
                pattern_signature TEXT PRIMARY KEY,
                total_occurrences INTEGER DEFAULT 0,
                l1_resolutions INTEGER DEFAULT 0,
                l2_resolutions INTEGER DEFAULT 0,
                l3_resolutions INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                total_resolution_time_ms INTEGER DEFAULT 0,
                last_seen TEXT,
                recommended_action TEXT,
                promotion_eligible BOOLEAN DEFAULT 0
            )
        """)

        # L1 rules that were promoted from L2
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promoted_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_signature TEXT NOT NULL UNIQUE,
                rule_yaml TEXT NOT NULL,
                promoted_at TEXT NOT NULL,
                promoted_from_incidents TEXT NOT NULL,
                success_rate_at_promotion REAL NOT NULL,
                occurrences_at_promotion INTEGER NOT NULL
            )
        """)

        # Learning feedback table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                feedback_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents(id)
            )
        """)

        # Persistent flap suppressions: once a check flaps and escalates to L3,
        # healing is suppressed until a human explicitly clears it.
        # Survives agent restarts, prevents infinite L3 escalation loops
        # (e.g., Windows firewall drift where GPO constantly reverts changes).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flap_suppressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                suppressed_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                cleared_at TEXT,
                cleared_by TEXT,
                UNIQUE(site_id, host_id, incident_type)
            )
        """)

        # Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_pattern ON incidents(pattern_signature)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(incident_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_site ON incidents(site_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_outcome ON incidents(outcome)")

        conn.commit()
        conn.close()

    def generate_pattern_signature(self, incident_type: str, raw_data: Dict[str, Any]) -> str:
        """
        Generate a pattern signature for deduplication and learning.

        Normalizes variable data (timestamps, IPs, etc.) to create
        a stable signature for similar incidents.
        """
        # Extract key fields that define the pattern
        pattern_fields = {
            "type": incident_type,
            "check_type": raw_data.get("check_type"),
            "drift_type": raw_data.get("drift_type"),
            "service_name": raw_data.get("service_name"),
            "error_pattern": self._normalize_error(raw_data.get("error_message", "")),
        }

        # Remove None values
        pattern_fields = {k: v for k, v in pattern_fields.items() if v is not None}

        # Create stable hash
        pattern_str = json.dumps(pattern_fields, sort_keys=True)
        return hashlib.sha256(pattern_str.encode()).hexdigest()[:16]

    def _normalize_error(self, error: str) -> str:
        """Normalize error message by removing variable parts."""
        import re

        # Remove timestamps
        error = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '<TIMESTAMP>', error)

        # Remove IPs
        error = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<IP>', error)

        # Remove UUIDs
        error = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '<UUID>', error)

        # Remove paths with variable components
        error = re.sub(r'/[a-z0-9]{32}/', '/<HASH>/', error)

        return error[:200]  # Truncate for signature

    def create_incident(
        self,
        site_id: str,
        host_id: str,
        incident_type: str,
        severity: str,
        raw_data: Dict[str, Any]
    ) -> Incident:
        """Create and store a new incident."""
        import uuid
        now = datetime.now(timezone.utc)
        incident_id = f"INC-{now.strftime('%Y%m%d%H%M%S')}-{now.microsecond:06d}-{uuid.uuid4().hex[:4]}"
        pattern_signature = self.generate_pattern_signature(incident_type, raw_data)

        incident = Incident(
            id=incident_id,
            site_id=site_id,
            host_id=host_id,
            incident_type=incident_type,
            severity=severity,
            raw_data=raw_data,
            pattern_signature=pattern_signature,
            created_at=datetime.now(timezone.utc).isoformat()
        )

        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("""
            INSERT INTO incidents (
                id, site_id, host_id, incident_type, severity,
                raw_data, pattern_signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            incident.id, incident.site_id, incident.host_id,
            incident.incident_type, incident.severity,
            json.dumps(incident.raw_data), incident.pattern_signature,
            incident.created_at
        ))

        # Update pattern stats
        conn.execute("""
            INSERT INTO pattern_stats (pattern_signature, total_occurrences, last_seen)
            VALUES (?, 1, ?)
            ON CONFLICT(pattern_signature) DO UPDATE SET
                total_occurrences = total_occurrences + 1,
                last_seen = excluded.last_seen
        """, (pattern_signature, incident.created_at))

        conn.commit()
        conn.close()

        return incident

    def resolve_incident(
        self,
        incident_id: str,
        resolution_level: ResolutionLevel,
        resolution_action: str,
        outcome: IncidentOutcome,
        resolution_time_ms: int
    ):
        """Mark an incident as resolved and update stats."""
        resolved_at = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self.db_path, timeout=30)

        # Get pattern signature
        cursor = conn.execute(
            "SELECT pattern_signature FROM incidents WHERE id = ?",
            (incident_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise ValueError(f"Incident {incident_id} not found")

        pattern_signature = row[0]

        # Update incident
        conn.execute("""
            UPDATE incidents SET
                resolved_at = ?,
                resolution_level = ?,
                resolution_action = ?,
                outcome = ?,
                resolution_time_ms = ?
            WHERE id = ?
        """, (
            resolved_at, resolution_level.value, resolution_action,
            outcome.value, resolution_time_ms, incident_id
        ))

        # Update pattern stats - using CASE to avoid SQL injection
        # Map resolution level to integer for safe parameterized query
        level_code = {
            ResolutionLevel.LEVEL1_DETERMINISTIC: 1,
            ResolutionLevel.LEVEL2_LLM: 2,
            ResolutionLevel.LEVEL3_HUMAN: 3,
        }.get(resolution_level, 3)

        success_increment = 1 if outcome == IncidentOutcome.SUCCESS else 0

        conn.execute("""
            UPDATE pattern_stats SET
                l1_resolutions = l1_resolutions + CASE WHEN ? = 1 THEN 1 ELSE 0 END,
                l2_resolutions = l2_resolutions + CASE WHEN ? = 2 THEN 1 ELSE 0 END,
                l3_resolutions = l3_resolutions + CASE WHEN ? = 3 THEN 1 ELSE 0 END,
                success_count = success_count + ?,
                total_resolution_time_ms = total_resolution_time_ms + ?,
                recommended_action = CASE
                    WHEN ? = 'success' THEN ?
                    ELSE recommended_action
                END
            WHERE pattern_signature = ?
        """, (
            level_code, level_code, level_code,
            success_increment, resolution_time_ms,
            outcome.value, resolution_action, pattern_signature
        ))

        # Check for L1 promotion eligibility
        self._check_promotion_eligibility(conn, pattern_signature)

        conn.commit()
        conn.close()

    def _check_promotion_eligibility(self, conn: sqlite3.Connection, pattern_signature: str):
        """Check if a pattern should be promoted to L1."""
        cursor = conn.execute("""
            SELECT
                total_occurrences,
                l2_resolutions,
                success_count,
                recommended_action
            FROM pattern_stats
            WHERE pattern_signature = ?
        """, (pattern_signature,))

        row = cursor.fetchone()
        if not row:
            return

        total, l2_resolutions, success_count, recommended_action = row

        # Promotion criteria:
        # - At least 5 occurrences
        # - At least 3 L2 resolutions with same action
        # - 90%+ success rate
        if total >= 5 and l2_resolutions >= 3 and recommended_action:
            success_rate = success_count / total if total > 0 else 0

            if success_rate >= 0.9:
                conn.execute("""
                    UPDATE pattern_stats
                    SET promotion_eligible = 1
                    WHERE pattern_signature = ?
                """, (pattern_signature,))

    def get_pattern_context(self, pattern_signature: str, limit: int = 10) -> Dict[str, Any]:
        """
        Get historical context for a pattern.
        Used by Level 2 LLM for informed decisions.
        """
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row

        # Get pattern stats
        cursor = conn.execute("""
            SELECT * FROM pattern_stats WHERE pattern_signature = ?
        """, (pattern_signature,))
        stats_row = cursor.fetchone()

        # Get recent incidents with this pattern
        cursor = conn.execute("""
            SELECT * FROM incidents
            WHERE pattern_signature = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (pattern_signature, limit))
        recent_incidents = [dict(row) for row in cursor.fetchall()]

        # Get successful resolutions
        cursor = conn.execute("""
            SELECT resolution_action, COUNT(*) as count
            FROM incidents
            WHERE pattern_signature = ? AND outcome = 'success'
            GROUP BY resolution_action
            ORDER BY count DESC
            LIMIT 5
        """, (pattern_signature,))
        successful_actions = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "pattern_signature": pattern_signature,
            "stats": dict(stats_row) if stats_row else None,
            "recent_incidents": recent_incidents,
            "successful_actions": successful_actions,
            "has_recommended_action": stats_row and stats_row["recommended_action"] is not None,
            "promotion_eligible": stats_row and stats_row["promotion_eligible"]
        }

    def get_similar_incidents(
        self,
        incident_type: str,
        site_id: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get similar incidents for context building."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row

        query = """
            SELECT * FROM incidents
            WHERE incident_type = ?
            AND outcome = 'success'
        """
        params = [incident_type]

        if site_id:
            query += " AND site_id = ?"
            params.append(site_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    def get_promotion_candidates(self) -> List[PatternStats]:
        """Get patterns eligible for L1 promotion."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("""
            SELECT
                ps.*,
                CAST(ps.success_count AS FLOAT) / ps.total_occurrences as success_rate,
                CAST(ps.total_resolution_time_ms AS FLOAT) / ps.total_occurrences as avg_resolution_time_ms
            FROM pattern_stats ps
            WHERE promotion_eligible = 1
            ORDER BY total_occurrences DESC
        """)

        results = []
        for row in cursor.fetchall():
            results.append(PatternStats(
                pattern_signature=row["pattern_signature"],
                total_occurrences=row["total_occurrences"],
                l1_resolutions=row["l1_resolutions"],
                l2_resolutions=row["l2_resolutions"],
                l3_resolutions=row["l3_resolutions"],
                success_rate=row["success_rate"],
                avg_resolution_time_ms=row["avg_resolution_time_ms"],
                last_seen=row["last_seen"],
                recommended_action=row["recommended_action"],
                promotion_eligible=True
            ))

        conn.close()
        return results

    def mark_promoted(self, pattern_signature: str, rule_yaml: str, incident_ids: List[str]):
        """Mark a pattern as promoted to L1."""
        conn = sqlite3.connect(self.db_path, timeout=30)

        # Get current stats for record
        cursor = conn.execute("""
            SELECT
                CAST(success_count AS FLOAT) / total_occurrences as success_rate,
                total_occurrences
            FROM pattern_stats
            WHERE pattern_signature = ?
        """, (pattern_signature,))
        row = cursor.fetchone()

        if row:
            success_rate, occurrences = row

            # Record promotion
            conn.execute("""
                INSERT INTO promoted_rules (
                    pattern_signature, rule_yaml, promoted_at,
                    promoted_from_incidents, success_rate_at_promotion,
                    occurrences_at_promotion
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                pattern_signature, rule_yaml, datetime.now(timezone.utc).isoformat(),
                json.dumps(incident_ids), success_rate, occurrences
            ))

            # Update incidents
            conn.execute("""
                UPDATE incidents
                SET promoted_to_l1 = 1
                WHERE pattern_signature = ?
            """, (pattern_signature,))

            # Update pattern stats
            conn.execute("""
                UPDATE pattern_stats
                SET promotion_eligible = 0
                WHERE pattern_signature = ?
            """, (pattern_signature,))

        conn.commit()
        conn.close()

    def add_human_feedback(
        self,
        incident_id: str,
        feedback_type: str,
        feedback_data: Dict[str, Any]
    ):
        """Record human feedback for learning."""
        conn = sqlite3.connect(self.db_path, timeout=30)

        conn.execute("""
            INSERT INTO learning_feedback (
                incident_id, feedback_type, feedback_data, created_at
            ) VALUES (?, ?, ?, ?)
        """, (
            incident_id, feedback_type,
            json.dumps(feedback_data), datetime.now(timezone.utc).isoformat()
        ))

        # Also update incident human_feedback field for quick reference
        conn.execute("""
            UPDATE incidents
            SET human_feedback = ?
            WHERE id = ?
        """, (json.dumps(feedback_data), incident_id))

        conn.commit()
        conn.close()

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get a single incident by ID."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Incident(
                id=row["id"],
                site_id=row["site_id"],
                host_id=row["host_id"],
                incident_type=row["incident_type"],
                severity=row["severity"],
                raw_data=json.loads(row["raw_data"]),
                pattern_signature=row["pattern_signature"],
                created_at=row["created_at"],
                resolved_at=row["resolved_at"],
                resolution_level=row["resolution_level"],
                resolution_action=row["resolution_action"],
                outcome=row["outcome"],
                resolution_time_ms=row["resolution_time_ms"],
                human_feedback=row["human_feedback"],
                promoted_to_l1=bool(row["promoted_to_l1"])
            )
        return None

    def get_stats_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get summary statistics for dashboard."""
        conn = sqlite3.connect(self.db_path, timeout=30)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cursor = conn.execute("""
            SELECT
                COUNT(*) as total_incidents,
                SUM(CASE WHEN resolution_level = 'L1' THEN 1 ELSE 0 END) as l1_count,
                SUM(CASE WHEN resolution_level = 'L2' THEN 1 ELSE 0 END) as l2_count,
                SUM(CASE WHEN resolution_level = 'L3' THEN 1 ELSE 0 END) as l3_count,
                SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success_count,
                AVG(resolution_time_ms) as avg_resolution_time_ms
            FROM incidents
            WHERE created_at >= ?
        """, (cutoff,))

        row = cursor.fetchone()
        conn.close()

        total = row[0] or 1  # Avoid division by zero

        return {
            "period_days": days,
            "total_incidents": row[0] or 0,
            "l1_percentage": (row[1] or 0) / total * 100,
            "l2_percentage": (row[2] or 0) / total * 100,
            "l3_percentage": (row[3] or 0) / total * 100,
            "success_rate": (row[4] or 0) / total * 100,
            "avg_resolution_time_ms": row[5] or 0
        }

    def get_recent_incidents(self, limit: int = 10, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get most recent incidents for audit/evidence."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row

        query = "SELECT * FROM incidents"
        params = []

        if site_id:
            query += " WHERE site_id = ?"
            params.append(site_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    def prune_old_incidents(
        self,
        retention_days: int = 30,
        keep_unresolved: bool = True
    ) -> Dict[str, int]:
        """
        Prune old resolved incidents to prevent unbounded database growth.

        Args:
            retention_days: Delete resolved incidents older than this many days
            keep_unresolved: If True, never delete unresolved incidents

        Returns:
            Dict with counts of deleted records
        """
        import logging
        logger = logging.getLogger(__name__)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

        conn = sqlite3.connect(self.db_path, timeout=30)

        # Get counts before deletion for reporting
        cursor = conn.execute("SELECT COUNT(*) FROM incidents")
        total_before = cursor.fetchone()[0]

        # Delete old learning feedback first (foreign key constraint)
        cursor = conn.execute("""
            DELETE FROM learning_feedback
            WHERE incident_id IN (
                SELECT id FROM incidents
                WHERE created_at < ?
                AND (resolved_at IS NOT NULL OR ? = 0)
            )
        """, (cutoff, 1 if keep_unresolved else 0))
        feedback_deleted = cursor.rowcount

        # Delete old incidents
        if keep_unresolved:
            # Only delete resolved incidents older than cutoff
            cursor = conn.execute("""
                DELETE FROM incidents
                WHERE created_at < ?
                AND resolved_at IS NOT NULL
            """, (cutoff,))
        else:
            # Delete all incidents older than cutoff
            cursor = conn.execute("""
                DELETE FROM incidents
                WHERE created_at < ?
            """, (cutoff,))

        incidents_deleted = cursor.rowcount

        # Prune pattern_stats that have no recent incidents
        # Keep stats if they have incidents in the last retention_days or are promotion eligible
        cursor = conn.execute("""
            DELETE FROM pattern_stats
            WHERE last_seen < ?
            AND promotion_eligible = 0
            AND pattern_signature NOT IN (
                SELECT DISTINCT pattern_signature FROM incidents
            )
        """, (cutoff,))
        stats_deleted = cursor.rowcount

        conn.commit()

        # VACUUM to reclaim disk space
        conn.execute("VACUUM")

        # Get size after
        cursor = conn.execute("SELECT COUNT(*) FROM incidents")
        total_after = cursor.fetchone()[0]

        conn.close()

        result = {
            "incidents_deleted": incidents_deleted,
            "feedback_deleted": feedback_deleted,
            "pattern_stats_deleted": stats_deleted,
            "incidents_before": total_before,
            "incidents_after": total_after,
            "retention_days": retention_days
        }

        logger.info(
            f"Pruned incident database: {incidents_deleted} incidents, "
            f"{feedback_deleted} feedback entries, {stats_deleted} pattern stats "
            f"(retention: {retention_days} days)"
        )

        return result

    def get_database_stats(self) -> Dict[str, Any]:
        """Get database size and record counts for monitoring."""
        import os

        conn = sqlite3.connect(self.db_path, timeout=30)

        stats = {}

        # File size
        if self.db_path.exists():
            stats["file_size_bytes"] = os.path.getsize(self.db_path)
            stats["file_size_mb"] = round(stats["file_size_bytes"] / (1024 * 1024), 2)

        # WAL file size (if exists)
        wal_path = Path(str(self.db_path) + "-wal")
        if wal_path.exists():
            stats["wal_size_bytes"] = os.path.getsize(wal_path)
            stats["wal_size_mb"] = round(stats["wal_size_bytes"] / (1024 * 1024), 2)

        # Record counts
        for table in ["incidents", "pattern_stats", "promoted_rules", "learning_feedback"]:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            stats[f"{table}_count"] = cursor.fetchone()[0]

        # Age of oldest and newest incidents
        cursor = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM incidents")
        row = cursor.fetchone()
        stats["oldest_incident"] = row[0]
        stats["newest_incident"] = row[1]

        # Unresolved count
        cursor = conn.execute("SELECT COUNT(*) FROM incidents WHERE resolved_at IS NULL")
        stats["unresolved_count"] = cursor.fetchone()[0]

        conn.close()

        return stats

    # --- Persistent Flap Suppression ---

    def record_flap_suppression(
        self,
        site_id: str,
        host_id: str,
        incident_type: str,
        reason: str
    ) -> None:
        """Record a flap suppression. Healing stays suppressed until cleared by a human."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("""
            INSERT INTO flap_suppressions (site_id, host_id, incident_type, suppressed_at, reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_id, host_id, incident_type) DO UPDATE SET
                suppressed_at = excluded.suppressed_at,
                reason = excluded.reason,
                cleared_at = NULL,
                cleared_by = NULL
        """, (site_id, host_id, incident_type, datetime.now(timezone.utc).isoformat(), reason))
        conn.commit()
        conn.close()

    def is_flap_suppressed(self, site_id: str, host_id: str, incident_type: str) -> bool:
        """Check if healing is suppressed for this circuit key."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.execute("""
            SELECT 1 FROM flap_suppressions
            WHERE site_id = ? AND host_id = ? AND incident_type = ?
            AND cleared_at IS NULL
        """, (site_id, host_id, incident_type))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def clear_flap_suppression(
        self, site_id: str, host_id: str, incident_type: str, cleared_by: str = "operator"
    ) -> bool:
        """Clear a flap suppression so healing can resume. Returns True if a suppression was cleared."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.execute("""
            UPDATE flap_suppressions
            SET cleared_at = ?, cleared_by = ?
            WHERE site_id = ? AND host_id = ? AND incident_type = ?
            AND cleared_at IS NULL
        """, (datetime.now(timezone.utc).isoformat(), cleared_by, site_id, host_id, incident_type))
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def get_active_suppressions(self) -> List[Dict[str, Any]]:
        """Get all active flap suppressions (for dashboard display)."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT site_id, host_id, incident_type, suppressed_at, reason
            FROM flap_suppressions
            WHERE cleared_at IS NULL
            ORDER BY suppressed_at DESC
        """)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
