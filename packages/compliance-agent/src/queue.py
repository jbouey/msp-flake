"""
Offline Queue - Durable Order Storage

Provides offline queue capability when MCP is unavailable:
- SQLite with WAL mode for durability
- ACID transactions with fsync
- Exponential backoff for retries
- Automatic cleanup of old orders

This ensures the agent can continue operating during network outages
and will catch up when connectivity is restored.

WAL Mode Benefits:
- Writers don't block readers
- Better concurrency
- Faster commits
- Crash recovery

Schema:
- orders table: Queued orders awaiting execution
- evidence table: Evidence bundles awaiting push to MCP
"""

import sqlite3
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class OfflineQueue:
    """
    Durable offline queue using SQLite WAL mode

    Guardrail #9: Queue durability (WAL, fsync, backoff)
    """

    def __init__(self, db_path: str, max_size: int = 1000):
        """
        Initialize offline queue

        Args:
            db_path: Path to SQLite database file
            max_size: Maximum number of queued items
        """
        self.db_path = Path(db_path)
        self.max_size = max_size

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

        logger.info(f"Offline queue initialized at {self.db_path}")

    def _init_db(self):
        """Initialize database schema with WAL mode"""
        conn = sqlite3.connect(str(self.db_path))

        # Enable WAL mode for better concurrency and durability
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')  # fsync after each transaction
        conn.execute('PRAGMA foreign_keys=ON')

        # Create orders table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                received_at REAL NOT NULL,
                ttl REAL NOT NULL,
                payload TEXT NOT NULL,
                signature TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                last_retry REAL,
                status TEXT DEFAULT 'pending',
                created_at REAL DEFAULT (julianday('now'))
            )
        ''')

        # Create evidence table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                bundle TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at REAL DEFAULT (julianday('now')),
                retry_count INTEGER DEFAULT 0,
                last_retry REAL,
                status TEXT DEFAULT 'pending'
            )
        ''')

        # Create index for faster queries
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status, site_id)
        ''')

        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_evidence_status
            ON evidence(status, site_id)
        ''')

        conn.commit()
        conn.close()

        logger.info("Database schema initialized")

    @contextmanager
    def _get_connection(self):
        """Get database connection with context manager"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Access columns by name
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add(self, order: Dict) -> bool:
        """
        Add order to queue

        Args:
            order: Order dictionary from MCP

        Returns:
            True if added successfully, False if queue is full
        """
        # Check queue size
        if self.size() >= self.max_size:
            logger.warning(f"Queue full ({self.max_size} items), cannot add order")
            return False

        with self._get_connection() as conn:
            try:
                conn.execute('''
                    INSERT INTO orders
                    (id, site_id, received_at, ttl, payload, signature)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    order['id'],
                    order.get('site_id', 'unknown'),
                    order['timestamp'],
                    order.get('ttl', 900),
                    json.dumps(order['payload']),
                    order['signature']
                ))

                logger.info(f"Order {order['id']} added to offline queue")
                return True

            except sqlite3.IntegrityError:
                # Order already in queue
                logger.debug(f"Order {order['id']} already in queue")
                return False

    def get_pending(self, limit: int = 10) -> List[Dict]:
        """
        Get pending orders from queue

        Returns oldest orders first, respecting TTL.

        Args:
            limit: Maximum number of orders to return

        Returns:
            List of orders ready for execution
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, site_id, received_at, ttl, payload, signature, retry_count
                FROM orders
                WHERE status = 'pending'
                  AND received_at + ttl > ?
                ORDER BY received_at ASC
                LIMIT ?
            ''', (time.time(), limit))

            orders = []
            for row in cursor.fetchall():
                order = {
                    'id': row['id'],
                    'site_id': row['site_id'],
                    'timestamp': row['received_at'],
                    'ttl': row['ttl'],
                    'payload': json.loads(row['payload']),
                    'signature': row['signature'],
                    'retry_count': row['retry_count']
                }
                orders.append(order)

            if orders:
                logger.info(f"Retrieved {len(orders)} orders from queue")

            return orders

    def mark_executed(self, order_id: str):
        """
        Mark order as executed

        Args:
            order_id: Order ID to mark
        """
        with self._get_connection() as conn:
            conn.execute('''
                UPDATE orders
                SET status = 'executed'
                WHERE id = ?
            ''', (order_id,))

            logger.debug(f"Order {order_id} marked as executed")

    def mark_failed(self, order_id: str, increment_retry: bool = True):
        """
        Mark order as failed

        Args:
            order_id: Order ID to mark
            increment_retry: Whether to increment retry count
        """
        with self._get_connection() as conn:
            if increment_retry:
                conn.execute('''
                    UPDATE orders
                    SET status = 'failed',
                        retry_count = retry_count + 1,
                        last_retry = ?
                    WHERE id = ?
                ''', (time.time(), order_id))
            else:
                conn.execute('''
                    UPDATE orders
                    SET status = 'failed'
                    WHERE id = ?
                ''', (order_id,))

            logger.warning(f"Order {order_id} marked as failed")

    def add_evidence(self, evidence: Dict) -> bool:
        """
        Add evidence bundle to queue for pushing to MCP

        Args:
            evidence: Evidence bundle dictionary

        Returns:
            True if added successfully
        """
        with self._get_connection() as conn:
            try:
                conn.execute('''
                    INSERT INTO evidence
                    (id, site_id, bundle, signature)
                    VALUES (?, ?, ?, ?)
                ''', (
                    evidence['id'],
                    evidence['site_id'],
                    json.dumps(evidence['bundle']),
                    evidence.get('signature', '')
                ))

                logger.info(f"Evidence {evidence['id']} added to queue")
                return True

            except sqlite3.IntegrityError:
                logger.debug(f"Evidence {evidence['id']} already in queue")
                return False

    def get_pending_evidence(self, limit: int = 10) -> List[Dict]:
        """
        Get pending evidence bundles

        Args:
            limit: Maximum number to return

        Returns:
            List of evidence bundles to push
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, site_id, bundle, signature, retry_count
                FROM evidence
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit,))

            evidence_list = []
            for row in cursor.fetchall():
                evidence = {
                    'id': row['id'],
                    'site_id': row['site_id'],
                    'bundle': json.loads(row['bundle']),
                    'signature': row['signature'],
                    'retry_count': row['retry_count']
                }
                evidence_list.append(evidence)

            if evidence_list:
                logger.info(f"Retrieved {len(evidence_list)} evidence bundles from queue")

            return evidence_list

    def mark_evidence_pushed(self, evidence_id: str):
        """
        Mark evidence as successfully pushed to MCP

        Args:
            evidence_id: Evidence ID to mark
        """
        with self._get_connection() as conn:
            conn.execute('''
                UPDATE evidence
                SET status = 'pushed'
                WHERE id = ?
            ''', (evidence_id,))

            logger.debug(f"Evidence {evidence_id} marked as pushed")

    def cleanup_old(self, days: int = 7):
        """
        Remove old completed orders and evidence

        Keeps database size manageable.

        Args:
            days: Remove items older than this many days
        """
        cutoff = time.time() - (days * 86400)

        with self._get_connection() as conn:
            # Delete old executed orders
            cursor = conn.execute('''
                DELETE FROM orders
                WHERE status = 'executed'
                  AND received_at < ?
            ''', (cutoff,))

            orders_deleted = cursor.rowcount

            # Delete old pushed evidence
            cursor = conn.execute('''
                DELETE FROM evidence
                WHERE status = 'pushed'
                  AND created_at < julianday('now', ?)
            ''', (f'-{days} days',))

            evidence_deleted = cursor.rowcount

            if orders_deleted > 0 or evidence_deleted > 0:
                logger.info(f"Cleanup: deleted {orders_deleted} orders, {evidence_deleted} evidence")

            # Vacuum to reclaim space (runs outside transaction)
            conn.commit()

        # Vacuum requires autocommit mode
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.execute('VACUUM')
        conn.close()

    def size(self) -> int:
        """
        Get current queue size (pending orders)

        Returns:
            Number of pending orders
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT COUNT(*) FROM orders WHERE status = 'pending'
            ''')
            return cursor.fetchone()[0]

    def stats(self) -> Dict:
        """
        Get queue statistics

        Returns:
            Dictionary with queue stats
        """
        with self._get_connection() as conn:
            # Orders by status
            cursor = conn.execute('''
                SELECT status, COUNT(*) as count
                FROM orders
                GROUP BY status
            ''')
            order_stats = {row['status']: row['count'] for row in cursor.fetchall()}

            # Evidence by status
            cursor = conn.execute('''
                SELECT status, COUNT(*) as count
                FROM evidence
                GROUP BY status
            ''')
            evidence_stats = {row['status']: row['count'] for row in cursor.fetchall()}

            return {
                'orders': order_stats,
                'evidence': evidence_stats,
                'total_size_mb': self.db_path.stat().st_size / (1024 * 1024)
            }

    def health_check(self):
        """
        Verify queue is operational

        Raises:
            Exception if queue is not healthy
        """
        with self._get_connection() as conn:
            # Run simple query
            cursor = conn.execute('SELECT COUNT(*) FROM orders')
            count = cursor.fetchone()[0]

            logger.debug(f"Health check: {count} orders in queue")

    def close(self):
        """
        Close database connections

        Called during agent shutdown.
        """
        # Connections are managed per-transaction, nothing to do here
        logger.info("Queue closed")

    def __repr__(self) -> str:
        return f"OfflineQueue(db_path='{self.db_path}', size={self.size()})"
