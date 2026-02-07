"""
Learning System Sync Service.

Handles bidirectional synchronization between agent and Central Command:
1. Push pattern_stats to server (every 4 hours)
2. Pull approved promoted rules from server
3. Report execution telemetry
4. Handle offline scenarios with queue
"""

import asyncio
import logging
import os
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .appliance_client import CentralCommandClient
    from .incident_db import IncidentDatabase

logger = logging.getLogger(__name__)


class LearningSyncQueue:
    """
    SQLite-based queue for learning sync operations when offline.

    Stores operations like pattern syncs and execution reports that
    failed due to connectivity issues, for replay when back online.
    """

    # Maximum retry attempts before marking as dead
    MAX_RETRIES = 10

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS learning_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                completed_at TEXT,
                next_retry_at TEXT
            )
        ''')

        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_learning_queue_pending
            ON learning_queue(completed_at) WHERE completed_at IS NULL
        ''')

        conn.commit()
        conn.close()

        logger.debug(f"Initialized learning sync queue at {self.db_path}")

    def enqueue(self, operation: str, data: Dict[str, Any]) -> int:
        """Add operation to queue for later replay."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('''
                INSERT INTO learning_queue (operation, data, created_at, next_retry_at)
                VALUES (?, ?, ?, ?)
            ''', (
                operation,
                json.dumps(data),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            ))
            queue_id = cursor.lastrowid
            conn.commit()
            logger.info(f"Queued learning operation: {operation} (id={queue_id})")
            return queue_id
        finally:
            conn.close()

    def dequeue_batch(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get batch of pending operations ready for replay."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute('''
                SELECT id, operation, data, created_at, retry_count
                FROM learning_queue
                WHERE completed_at IS NULL
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC
                LIMIT ?
            ''', (datetime.now(timezone.utc).isoformat(), limit))

            items = []
            for row in cursor.fetchall():
                items.append({
                    "id": row["id"],
                    "operation": row["operation"],
                    "data": json.loads(row["data"]),
                    "created_at": row["created_at"],
                    "retry_count": row["retry_count"],
                })
            return items
        finally:
            conn.close()

    def mark_completed(self, queue_id: int):
        """Mark operation as successfully completed."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                UPDATE learning_queue
                SET completed_at = ?
                WHERE id = ?
            ''', (datetime.now(timezone.utc).isoformat(), queue_id))
            conn.commit()
            logger.debug(f"Marked learning queue item {queue_id} as completed")
        finally:
            conn.close()

    def mark_failed(self, queue_id: int, error: str):
        """Mark operation as failed, schedule retry with exponential backoff.

        After MAX_RETRIES attempts, marks the item as permanently failed
        to prevent infinite retries.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                'SELECT retry_count FROM learning_queue WHERE id = ?',
                (queue_id,)
            )
            row = cursor.fetchone()
            if not row:
                return

            retry_count = row[0] + 1

            # Check if max retries exceeded
            if retry_count >= self.MAX_RETRIES:
                # Mark as permanently failed
                conn.execute('''
                    UPDATE learning_queue
                    SET retry_count = ?, last_error = ?, completed_at = ?,
                        next_retry_at = NULL
                    WHERE id = ?
                ''', (retry_count, f"PERMANENTLY_FAILED: {error}",
                      datetime.now(timezone.utc).isoformat(), queue_id))
                conn.commit()
                logger.error(f"Learning queue item {queue_id} permanently failed after {retry_count} attempts")
                return

            # Exponential backoff: 2^retry_count minutes, max 60 minutes
            backoff_minutes = min(2 ** retry_count, 60)
            next_retry = datetime.now(timezone.utc) + timedelta(minutes=backoff_minutes)

            conn.execute('''
                UPDATE learning_queue
                SET retry_count = ?, last_error = ?, next_retry_at = ?
                WHERE id = ?
            ''', (retry_count, error, next_retry.isoformat(), queue_id))
            conn.commit()

            logger.warning(f"Learning queue item {queue_id} failed (attempt {retry_count}/{self.MAX_RETRIES}): {error}")
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('''
                SELECT
                    COUNT(*) FILTER (WHERE completed_at IS NULL) as pending,
                    COUNT(*) FILTER (WHERE completed_at IS NOT NULL) as completed
                FROM learning_queue
            ''')
            row = cursor.fetchone()
            return {
                "pending": row[0] if row else 0,
                "completed": row[1] if row else 0,
            }
        finally:
            conn.close()


class LearningSyncService:
    """
    Periodic sync service for learning system data.

    Responsibilities:
    - Sync pattern_stats to server (every 4 hours)
    - Fetch and deploy promoted rules from server
    - Report execution telemetry to server
    - Queue operations when offline, replay when back online
    """

    SYNC_INTERVAL_HOURS = 4
    DEFAULT_QUEUE_PATH = Path(os.environ.get("STATE_DIR", "/var/lib/msp")) / "learning_sync_queue.db"
    DEFAULT_PROMOTED_RULES_DIR = Path("/etc/msp/rules/promoted")

    def __init__(
        self,
        client: "CentralCommandClient",
        incident_db: "IncidentDatabase",
        site_id: str,
        appliance_id: str,
        queue_path: Optional[Path] = None,
        promoted_rules_dir: Optional[Path] = None,
    ):
        """
        Initialize learning sync service.

        Args:
            client: Central Command API client
            incident_db: Local incident database
            site_id: Site identifier
            appliance_id: Appliance identifier
            queue_path: Path to offline queue database (optional)
            promoted_rules_dir: Directory to write promoted rules (optional)
        """
        self.client = client
        self.incident_db = incident_db
        self.site_id = site_id
        self.appliance_id = appliance_id

        self._last_pattern_sync: Optional[datetime] = None
        self._last_rule_fetch: Optional[datetime] = None

        # Initialize queue and rules directory
        self._queue_path = queue_path or self.DEFAULT_QUEUE_PATH
        self._offline_queue = LearningSyncQueue(self._queue_path)

        self._promoted_rules_dir = promoted_rules_dir or self.DEFAULT_PROMOTED_RULES_DIR
        self._promoted_rules_dir.mkdir(parents=True, exist_ok=True)

    async def sync(self) -> Dict[str, Any]:
        """
        Main sync entry point. Called periodically from appliance agent.

        Returns sync status report.
        """
        report = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "patterns_synced": False,
            "patterns_count": 0,
            "rules_fetched": False,
            "rules_count": 0,
            "offline_queue_processed": False,
            "offline_queue_items": 0,
            "errors": []
        }

        # 1. Process offline queue first (replay failed operations)
        try:
            items_processed = await self._process_offline_queue()
            report["offline_queue_processed"] = True
            report["offline_queue_items"] = items_processed
        except Exception as e:
            logger.warning(f"Offline queue processing failed: {e}")
            report["errors"].append(f"offline_queue: {str(e)}")

        # 2. Sync pattern stats (if interval elapsed)
        if self._should_sync_patterns():
            try:
                count = await self._sync_pattern_stats()
                report["patterns_synced"] = True
                report["patterns_count"] = count
                self._last_pattern_sync = datetime.now(timezone.utc)
            except Exception as e:
                logger.warning(f"Pattern sync failed (will queue): {e}")
                await self._queue_pattern_sync()
                report["errors"].append(f"pattern_sync: {str(e)}")

        # 3. Fetch and deploy promoted rules
        try:
            rules = await self._fetch_promoted_rules()
            report["rules_fetched"] = True
            report["rules_count"] = len(rules)
            self._last_rule_fetch = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Rule fetch failed: {e}")
            report["errors"].append(f"rule_fetch: {str(e)}")

        return report

    def _should_sync_patterns(self) -> bool:
        """Check if pattern sync is due."""
        if self._last_pattern_sync is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_pattern_sync
        return elapsed.total_seconds() >= (self.SYNC_INTERVAL_HOURS * 3600)

    async def _sync_pattern_stats(self) -> int:
        """Push pattern_stats to server."""
        # Get all pattern stats from local DB
        conn = sqlite3.connect(self.incident_db.db_path)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute("SELECT * FROM pattern_stats")
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            logger.debug("No pattern stats to sync")
            return 0

        # Convert rows to list of dicts
        pattern_stats = []
        for row in rows:
            pattern_stats.append({
                "pattern_signature": row["pattern_signature"],
                "total_occurrences": row["total_occurrences"],
                "l1_resolutions": row["l1_resolutions"],
                "l2_resolutions": row["l2_resolutions"],
                "l3_resolutions": row["l3_resolutions"],
                "success_count": row["success_count"],
                "total_resolution_time_ms": row["total_resolution_time_ms"] or 0.0,
                "last_seen": row["last_seen"],
                "recommended_action": row["recommended_action"],
                "promotion_eligible": bool(row["promotion_eligible"]),
            })

        # Send to server
        payload = {
            "site_id": self.site_id,
            "appliance_id": self.appliance_id,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "pattern_stats": pattern_stats,
        }

        status, response = await self.client._request(
            'POST',
            '/api/agent/sync/pattern-stats',
            json_data=payload
        )

        if status in (200, 201):
            accepted = response.get("accepted", 0)
            merged = response.get("merged", 0)
            logger.info(f"Pattern sync complete: {accepted} new, {merged} merged")
            return accepted + merged
        else:
            raise Exception(f"Server returned {status}: {response}")

    async def _fetch_promoted_rules(self) -> List[Dict[str, Any]]:
        """Fetch and deploy server-approved promoted rules."""
        since = "1970-01-01T00:00:00Z"
        if self._last_rule_fetch:
            since = self._last_rule_fetch.isoformat()

        status, response = await self.client._request(
            'GET',
            f'/api/agent/sync/promoted-rules?site_id={self.site_id}&since={since}'
        )

        if status != 200:
            logger.warning(f"Failed to fetch promoted rules: {status}")
            return []

        rules = response.get("rules", [])
        deployed = []

        for rule in rules:
            try:
                await self._deploy_promoted_rule(rule)
                deployed.append(rule)
            except Exception as e:
                logger.error(f"Failed to deploy rule {rule.get('rule_id')}: {e}")

        if deployed:
            logger.info(f"Deployed {len(deployed)} promoted rules from server")

        return deployed

    async def _deploy_promoted_rule(self, rule: Dict[str, Any]):
        """Deploy a promoted rule to local rules directory."""
        rule_id = rule.get("rule_id")
        rule_yaml = rule.get("rule_yaml")

        if not rule_id or not rule_yaml:
            raise ValueError("Invalid rule data: missing rule_id or rule_yaml")

        rule_file = self._promoted_rules_dir / f"{rule_id}.yaml"

        # Check if rule already exists
        if rule_file.exists():
            logger.debug(f"Rule {rule_id} already deployed, skipping")
            return

        rule_file.write_text(rule_yaml)
        logger.info(f"Deployed promoted rule: {rule_id} to {rule_file}")

    async def _queue_pattern_sync(self):
        """Queue pattern sync for later when offline."""
        conn = sqlite3.connect(self.incident_db.db_path)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute("SELECT * FROM pattern_stats")
            rows = cursor.fetchall()
        finally:
            conn.close()

        pattern_stats = []
        for row in rows:
            pattern_stats.append({
                "pattern_signature": row["pattern_signature"],
                "total_occurrences": row["total_occurrences"],
                "l1_resolutions": row["l1_resolutions"],
                "l2_resolutions": row["l2_resolutions"],
                "l3_resolutions": row["l3_resolutions"],
                "success_count": row["success_count"],
                "total_resolution_time_ms": row["total_resolution_time_ms"] or 0.0,
                "last_seen": row["last_seen"],
                "recommended_action": row["recommended_action"],
                "promotion_eligible": bool(row["promotion_eligible"]),
            })

        self._offline_queue.enqueue(
            operation="pattern_sync",
            data={
                "site_id": self.site_id,
                "appliance_id": self.appliance_id,
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "pattern_stats": pattern_stats,
            }
        )

    async def _process_offline_queue(self) -> int:
        """Process queued operations from offline periods."""
        items = self._offline_queue.dequeue_batch(limit=10)
        processed = 0

        for item in items:
            try:
                if item["operation"] == "pattern_sync":
                    status, _ = await self.client._request(
                        'POST',
                        '/api/agent/sync/pattern-stats',
                        json_data=item["data"]
                    )
                    if status in (200, 201):
                        self._offline_queue.mark_completed(item["id"])
                        processed += 1
                    else:
                        self._offline_queue.mark_failed(item["id"], f"Server returned {status}")

                elif item["operation"] == "execution_report":
                    status, _ = await self.client._request(
                        'POST',
                        '/api/agent/executions',
                        json_data=item["data"]
                    )
                    if status in (200, 201):
                        self._offline_queue.mark_completed(item["id"])
                        processed += 1
                    else:
                        self._offline_queue.mark_failed(item["id"], f"Server returned {status}")

                else:
                    logger.warning(f"Unknown queue operation: {item['operation']}")
                    self._offline_queue.mark_completed(item["id"])

            except Exception as e:
                logger.warning(f"Failed to process queue item {item['id']}: {e}")
                self._offline_queue.mark_failed(item["id"], str(e))

        if processed:
            logger.info(f"Processed {processed} items from offline queue")

        return processed

    async def report_execution(self, execution_result: Dict[str, Any]) -> bool:
        """
        Report execution telemetry to server, queuing if offline.

        Called after each healing action to send state capture data
        to the central learning engine.

        Args:
            execution_result: Dictionary with execution telemetry (state_before,
                            state_after, duration, success, etc.)

        Returns:
            True if reported successfully, False if queued for later
        """
        payload = {
            "site_id": self.site_id,
            "execution": execution_result,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            status, response = await self.client._request(
                'POST',
                '/api/agent/executions',
                json_data=payload
            )

            if status in (200, 201):
                logger.debug(f"Reported execution: {execution_result.get('execution_id')}")
                return True
            else:
                raise Exception(f"Server returned {status}")

        except Exception as e:
            logger.warning(f"Execution report failed (queuing): {e}")
            self._offline_queue.enqueue(
                operation="execution_report",
                data=payload
            )
            return False

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get offline queue statistics."""
        return self._offline_queue.get_stats()

    def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status."""
        return {
            "last_pattern_sync": self._last_pattern_sync.isoformat() if self._last_pattern_sync else None,
            "last_rule_fetch": self._last_rule_fetch.isoformat() if self._last_rule_fetch else None,
            "sync_interval_hours": self.SYNC_INTERVAL_HOURS,
            "patterns_sync_due": self._should_sync_patterns(),
            "queue_stats": self.get_queue_stats(),
        }
