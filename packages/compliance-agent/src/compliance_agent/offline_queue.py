"""
Offline evidence queue with SQLite persistence.

Queues evidence bundles for upload to MCP server when offline.
Uses SQLite with WAL mode for crash-safe persistence.
"""

import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging
import json

from .models import QueuedEvidence

logger = logging.getLogger(__name__)


class EvidenceQueue:
    """
    Offline queue for evidence bundles awaiting upload.

    Features:
    - SQLite database with WAL mode for durability
    - Retry logic with exponential backoff
    - Max retry limit to prevent infinite loops
    - Query queued items by status
    - Mark items as uploaded
    - Prune successfully uploaded items
    """

    def __init__(self, db_path: Path, max_retries: int = 10):
        """
        Initialize evidence queue.

        Args:
            db_path: Path to SQLite database file
            max_retries: Maximum retry attempts per bundle (default: 10)
        """
        self.db_path = Path(db_path)
        self.max_retries = max_retries
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with schema."""
        # Create parent directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)

        # Enable WAL mode for better concurrency and crash safety
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')

        # Create queued_evidence table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS queued_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle_id TEXT NOT NULL UNIQUE,
                bundle_path TEXT NOT NULL,
                signature_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                uploaded_at TEXT,
                next_retry_at TEXT
            )
        ''')

        # Create index for efficient queries
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_uploaded_at
            ON queued_evidence(uploaded_at)
        ''')

        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_next_retry_at
            ON queued_evidence(next_retry_at)
        ''')

        conn.commit()
        conn.close()

        logger.info(f"Initialized evidence queue at {self.db_path}")

    async def enqueue(
        self,
        bundle_id: str,
        bundle_path: Path,
        signature_path: Path
    ) -> int:
        """
        Add evidence bundle to upload queue.

        Args:
            bundle_id: Unique bundle identifier
            bundle_path: Path to bundle.json
            signature_path: Path to bundle.sig

        Returns:
            Queue entry ID

        Raises:
            sqlite3.IntegrityError: If bundle_id already queued
        """
        conn = sqlite3.connect(self.db_path)

        try:
            cursor = conn.execute('''
                INSERT INTO queued_evidence
                (bundle_id, bundle_path, signature_path, created_at, next_retry_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                bundle_id,
                str(bundle_path),
                str(signature_path),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()  # Ready immediately
            ))

            queue_id = cursor.lastrowid
            conn.commit()

            logger.info(f"Enqueued evidence bundle {bundle_id} (queue_id={queue_id})")

            return queue_id

        finally:
            conn.close()

    async def list_pending(
        self,
        limit: Optional[int] = None,
        ready_only: bool = True
    ) -> List[QueuedEvidence]:
        """
        List evidence bundles pending upload.

        Args:
            limit: Maximum number of items to return
            ready_only: Only return items ready for retry (default: True)

        Returns:
            List of queued evidence bundles
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            query = '''
                SELECT id, bundle_id, bundle_path, signature_path,
                       created_at, retry_count, last_error
                FROM queued_evidence
                WHERE uploaded_at IS NULL
            '''

            params = []

            if ready_only:
                query += ' AND (next_retry_at IS NULL OR next_retry_at <= ?)'
                params.append(datetime.now(timezone.utc).isoformat())

            query += ' ORDER BY created_at ASC'

            if limit:
                query += ' LIMIT ?'
                params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            items = []
            for row in rows:
                items.append(QueuedEvidence(
                    id=row['id'],
                    bundle_id=row['bundle_id'],
                    bundle_path=row['bundle_path'],
                    signature_path=row['signature_path'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    retry_count=row['retry_count'],
                    last_error=row['last_error']
                ))

            return items

        finally:
            conn.close()

    async def mark_uploaded(self, queue_id: int):
        """
        Mark evidence bundle as successfully uploaded.

        Args:
            queue_id: Queue entry ID
        """
        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute('''
                UPDATE queued_evidence
                SET uploaded_at = ?
                WHERE id = ?
            ''', (datetime.now(timezone.utc).isoformat(), queue_id))

            conn.commit()

            logger.info(f"Marked queue entry {queue_id} as uploaded")

        finally:
            conn.close()

    async def mark_failed(
        self,
        queue_id: int,
        error: str,
        retry_after_sec: Optional[int] = None
    ):
        """
        Mark upload attempt as failed and schedule retry.

        Args:
            queue_id: Queue entry ID
            error: Error message
            retry_after_sec: Seconds until next retry (None = exponential backoff)
        """
        conn = sqlite3.connect(self.db_path)

        try:
            # Get current retry count
            cursor = conn.execute(
                'SELECT retry_count FROM queued_evidence WHERE id = ?',
                (queue_id,)
            )
            row = cursor.fetchone()

            if not row:
                logger.warning(f"Queue entry {queue_id} not found")
                return

            retry_count = row[0] + 1

            # Calculate next retry time (exponential backoff)
            if retry_after_sec is None:
                # Exponential backoff: 2^retry_count minutes, max 60 minutes
                backoff_minutes = min(2 ** retry_count, 60)
                retry_after_sec = backoff_minutes * 60

            next_retry = datetime.now(timezone.utc) + timedelta(seconds=retry_after_sec)

            # Update record
            conn.execute('''
                UPDATE queued_evidence
                SET retry_count = ?,
                    last_error = ?,
                    next_retry_at = ?
                WHERE id = ?
            ''', (retry_count, error, next_retry.isoformat(), queue_id))

            conn.commit()

            logger.warning(
                f"Queue entry {queue_id} failed (attempt {retry_count}): {error}. "
                f"Next retry at {next_retry.isoformat()}"
            )

            # Check if max retries exceeded
            if retry_count >= self.max_retries:
                logger.error(
                    f"Queue entry {queue_id} exceeded max retries ({self.max_retries}). "
                    "Manual intervention required."
                )

        finally:
            conn.close()

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue stats
        """
        conn = sqlite3.connect(self.db_path)

        try:
            # Total pending
            cursor = conn.execute('''
                SELECT COUNT(*) FROM queued_evidence
                WHERE uploaded_at IS NULL
            ''')
            pending_count = cursor.fetchone()[0]

            # Total uploaded
            cursor = conn.execute('''
                SELECT COUNT(*) FROM queued_evidence
                WHERE uploaded_at IS NOT NULL
            ''')
            uploaded_count = cursor.fetchone()[0]

            # Exceeded max retries
            cursor = conn.execute('''
                SELECT COUNT(*) FROM queued_evidence
                WHERE uploaded_at IS NULL
                AND retry_count >= ?
            ''', (self.max_retries,))
            failed_count = cursor.fetchone()[0]

            # Oldest pending
            cursor = conn.execute('''
                SELECT created_at FROM queued_evidence
                WHERE uploaded_at IS NULL
                ORDER BY created_at ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            oldest_pending = row[0] if row else None

            # Ready for retry
            cursor = conn.execute('''
                SELECT COUNT(*) FROM queued_evidence
                WHERE uploaded_at IS NULL
                AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ''', (datetime.now(timezone.utc).isoformat(),))
            ready_count = cursor.fetchone()[0]

            return {
                'total_pending': pending_count,
                'total_uploaded': uploaded_count,
                'failed_max_retries': failed_count,
                'ready_for_retry': ready_count,
                'oldest_pending': oldest_pending
            }

        finally:
            conn.close()

    async def prune_uploaded(self, older_than_days: int = 7) -> int:
        """
        Delete successfully uploaded evidence from queue.

        Args:
            older_than_days: Only delete uploads older than this many days

        Returns:
            Number of entries deleted
        """
        conn = sqlite3.connect(self.db_path)

        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=older_than_days)

            cursor = conn.execute('''
                DELETE FROM queued_evidence
                WHERE uploaded_at IS NOT NULL
                AND uploaded_at < ?
            ''', (cutoff_date.isoformat(),))

            deleted_count = cursor.rowcount
            conn.commit()

            logger.info(
                f"Pruned {deleted_count} uploaded entries older than {older_than_days} days"
            )

            return deleted_count

        finally:
            conn.close()

    async def get_by_bundle_id(self, bundle_id: str) -> Optional[QueuedEvidence]:
        """
        Get queue entry by bundle ID.

        Args:
            bundle_id: Evidence bundle ID

        Returns:
            QueuedEvidence if found, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute('''
                SELECT id, bundle_id, bundle_path, signature_path,
                       created_at, retry_count, last_error
                FROM queued_evidence
                WHERE bundle_id = ?
            ''', (bundle_id,))

            row = cursor.fetchone()

            if not row:
                return None

            return QueuedEvidence(
                id=row['id'],
                bundle_id=row['bundle_id'],
                bundle_path=row['bundle_path'],
                signature_path=row['signature_path'],
                created_at=datetime.fromisoformat(row['created_at']),
                retry_count=row['retry_count'],
                last_error=row['last_error']
            )

        finally:
            conn.close()

    async def clear_all(self):
        """
        Clear all entries from queue (for testing).

        WARNING: This is destructive and should only be used in tests.
        """
        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute('DELETE FROM queued_evidence')
            conn.commit()

            logger.warning("Cleared all entries from evidence queue")

        finally:
            conn.close()

    def close(self):
        """
        Close database connection pool.

        Note: SQLite connections are created per-operation and closed immediately,
        so this is a no-op for now. Included for API completeness.
        """
        pass


# Alias for backward compatibility
OfflineQueue = EvidenceQueue
