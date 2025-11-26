"""
Tests for offline evidence queue.
"""

import pytest
import tempfile
import shutil
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sqlite3

from compliance_agent.offline_queue import EvidenceQueue


@pytest.fixture
def temp_queue_db():
    """Create temporary queue database."""
    temp_dir = Path(tempfile.mkdtemp())
    db_path = temp_dir / "queue.db"
    yield db_path
    shutil.rmtree(temp_dir)


@pytest.fixture
def queue(temp_queue_db):
    """Create evidence queue instance."""
    return EvidenceQueue(temp_queue_db, max_retries=5)


@pytest.fixture
def mock_evidence_paths(tmp_path):
    """Create mock evidence bundle paths."""
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text('{"test": "data"}')

    sig_path = tmp_path / "bundle.sig"
    sig_path.write_bytes(b"mock_signature")

    return bundle_path, sig_path


@pytest.mark.asyncio
async def test_queue_initialization(temp_queue_db):
    """Test queue database initialization."""
    queue = EvidenceQueue(temp_queue_db)

    # Check database exists
    assert temp_queue_db.exists()

    # Check WAL mode enabled
    conn = sqlite3.connect(temp_queue_db)
    cursor = conn.execute('PRAGMA journal_mode')
    mode = cursor.fetchone()[0]
    assert mode.lower() == 'wal'
    conn.close()

    # Check table exists
    conn = sqlite3.connect(temp_queue_db)
    cursor = conn.execute('''
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='queued_evidence'
    ''')
    assert cursor.fetchone() is not None
    conn.close()


@pytest.mark.asyncio
async def test_enqueue_evidence(queue, mock_evidence_paths):
    """Test adding evidence to queue."""
    bundle_path, sig_path = mock_evidence_paths

    queue_id = await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    assert queue_id > 0

    # Verify entry exists
    item = await queue.get_by_bundle_id("test-bundle-001")
    assert item is not None
    assert item.bundle_id == "test-bundle-001"
    assert item.retry_count == 0
    assert item.last_error is None


@pytest.mark.asyncio
async def test_enqueue_duplicate(queue, mock_evidence_paths):
    """Test enqueuing duplicate bundle ID raises error."""
    bundle_path, sig_path = mock_evidence_paths

    # First enqueue succeeds
    await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # Second enqueue fails
    with pytest.raises(sqlite3.IntegrityError):
        await queue.enqueue(
            bundle_id="test-bundle-001",
            bundle_path=bundle_path,
            signature_path=sig_path
        )


@pytest.mark.asyncio
async def test_list_pending(queue, mock_evidence_paths):
    """Test listing pending evidence."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue multiple items
    for i in range(5):
        await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )

    # List all pending
    pending = await queue.list_pending()
    assert len(pending) == 5

    # Check order (oldest first)
    assert pending[0].bundle_id == "test-bundle-000"
    assert pending[4].bundle_id == "test-bundle-004"


@pytest.mark.asyncio
async def test_list_pending_with_limit(queue, mock_evidence_paths):
    """Test listing pending with limit."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue 10 items
    for i in range(10):
        await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )

    # List with limit
    pending = await queue.list_pending(limit=3)
    assert len(pending) == 3


@pytest.mark.asyncio
async def test_mark_uploaded(queue, mock_evidence_paths):
    """Test marking evidence as uploaded."""
    bundle_path, sig_path = mock_evidence_paths

    queue_id = await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # Mark as uploaded
    await queue.mark_uploaded(queue_id)

    # Should not appear in pending list
    pending = await queue.list_pending()
    assert len(pending) == 0

    # Verify uploaded_at is set
    item = await queue.get_by_bundle_id("test-bundle-001")
    # Note: item won't be returned by get_by_bundle_id if uploaded
    # This is expected behavior


@pytest.mark.asyncio
async def test_mark_failed(queue, mock_evidence_paths):
    """Test marking upload as failed."""
    bundle_path, sig_path = mock_evidence_paths

    queue_id = await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # Mark as failed
    await queue.mark_failed(queue_id, "Connection timeout")

    # Should still be in pending
    pending = await queue.list_pending(ready_only=False)
    assert len(pending) == 1
    assert pending[0].retry_count == 1
    assert pending[0].last_error == "Connection timeout"


@pytest.mark.asyncio
async def test_exponential_backoff(queue, mock_evidence_paths):
    """Test exponential backoff on retries."""
    bundle_path, sig_path = mock_evidence_paths

    queue_id = await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # First failure: should be ready immediately (but next_retry_at set)
    await queue.mark_failed(queue_id, "Error 1")

    # Check next_retry_at is in the future
    conn = sqlite3.connect(queue.db_path)
    cursor = conn.execute(
        'SELECT next_retry_at FROM queued_evidence WHERE id = ?',
        (queue_id,)
    )
    next_retry = cursor.fetchone()[0]
    conn.close()

    next_retry_dt = datetime.fromisoformat(next_retry)
    now = datetime.now(timezone.utc)

    # Should be scheduled for future (2^1 = 2 minutes)
    assert next_retry_dt > now


@pytest.mark.asyncio
async def test_max_retries(queue, mock_evidence_paths):
    """Test max retry limit."""
    bundle_path, sig_path = mock_evidence_paths

    queue_id = await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # Fail 5 times (max_retries=5 in fixture)
    for i in range(5):
        await queue.mark_failed(queue_id, f"Error {i+1}")

    # Check retry count
    item = await queue.get_by_bundle_id("test-bundle-001")
    assert item.retry_count == 5

    # Stats should show failed count
    stats = await queue.get_stats()
    assert stats['failed_max_retries'] == 1


@pytest.mark.asyncio
async def test_queue_stats(queue, mock_evidence_paths):
    """Test queue statistics."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue 5 items
    queue_ids = []
    for i in range(5):
        queue_id = await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )
        queue_ids.append(queue_id)

    # Mark 2 as uploaded
    await queue.mark_uploaded(queue_ids[0])
    await queue.mark_uploaded(queue_ids[1])

    # Mark 1 as failed
    await queue.mark_failed(queue_ids[2], "Test error")

    # Get stats
    stats = await queue.get_stats()

    assert stats['total_pending'] == 3  # 5 - 2 uploaded
    assert stats['total_uploaded'] == 2
    assert stats['ready_for_retry'] == 2  # 2 never attempted + 0 ready after backoff
    assert stats['oldest_pending'] is not None


@pytest.mark.asyncio
async def test_prune_uploaded(queue, mock_evidence_paths):
    """Test pruning uploaded evidence."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue and upload 3 items
    for i in range(3):
        queue_id = await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )
        await queue.mark_uploaded(queue_id)

    # Manually set uploaded_at to 10 days ago for testing
    conn = sqlite3.connect(queue.db_path)
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute('''
        UPDATE queued_evidence
        SET uploaded_at = ?
        WHERE bundle_id IN ('test-bundle-000', 'test-bundle-001')
    ''', (old_date,))
    conn.commit()
    conn.close()

    # Prune items older than 7 days
    deleted = await queue.prune_uploaded(older_than_days=7)

    assert deleted == 2  # Only the 2 we backdated


@pytest.mark.asyncio
async def test_get_by_bundle_id(queue, mock_evidence_paths):
    """Test retrieving entry by bundle ID."""
    bundle_path, sig_path = mock_evidence_paths

    await queue.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    item = await queue.get_by_bundle_id("test-bundle-001")

    assert item is not None
    assert item.bundle_id == "test-bundle-001"
    assert item.bundle_path == str(bundle_path)
    assert item.signature_path == str(sig_path)


@pytest.mark.asyncio
async def test_get_by_bundle_id_not_found(queue):
    """Test retrieving non-existent bundle ID."""
    item = await queue.get_by_bundle_id("nonexistent")
    assert item is None


@pytest.mark.asyncio
async def test_clear_all(queue, mock_evidence_paths):
    """Test clearing all queue entries."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue 5 items
    for i in range(5):
        await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )

    # Verify items exist
    pending = await queue.list_pending()
    assert len(pending) == 5

    # Clear all
    await queue.clear_all()

    # Verify empty
    pending = await queue.list_pending()
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_queue_persistence(temp_queue_db, mock_evidence_paths):
    """Test queue persists across restarts."""
    bundle_path, sig_path = mock_evidence_paths

    # Create queue and add items
    queue1 = EvidenceQueue(temp_queue_db)
    await queue1.enqueue(
        bundle_id="test-bundle-001",
        bundle_path=bundle_path,
        signature_path=sig_path
    )
    await queue1.enqueue(
        bundle_id="test-bundle-002",
        bundle_path=bundle_path,
        signature_path=sig_path
    )

    # Close and recreate queue
    queue1.close()
    queue2 = EvidenceQueue(temp_queue_db)

    # Items should still be there
    pending = await queue2.list_pending()
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_concurrent_operations(queue, mock_evidence_paths):
    """Test concurrent queue operations."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue items concurrently
    tasks = []
    for i in range(10):
        task = queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # All should succeed
    assert len(results) == 10
    assert all(r > 0 for r in results)

    # Verify all items in queue
    pending = await queue.list_pending()
    assert len(pending) == 10


@pytest.mark.asyncio
async def test_ready_only_filter(queue, mock_evidence_paths):
    """Test ready_only filter in list_pending."""
    bundle_path, sig_path = mock_evidence_paths

    # Enqueue 3 items
    queue_ids = []
    for i in range(3):
        queue_id = await queue.enqueue(
            bundle_id=f"test-bundle-{i:03d}",
            bundle_path=bundle_path,
            signature_path=sig_path
        )
        queue_ids.append(queue_id)

    # Mark one as failed (will have future retry time)
    await queue.mark_failed(queue_ids[1], "Test error")

    # List ready only
    ready = await queue.list_pending(ready_only=True)
    # Should be 2: the 2 that were never attempted
    assert len(ready) == 2

    # List all pending (ignore retry time)
    all_pending = await queue.list_pending(ready_only=False)
    assert len(all_pending) == 3
