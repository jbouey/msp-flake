#!/usr/bin/env python3
"""Database migration runner with rollback support.

Usage:
    python migrate.py up                 # Apply all pending migrations
    python migrate.py up 003             # Apply up to migration 003
    python migrate.py down               # Rollback last migration
    python migrate.py down 005           # Rollback to migration 005
    python migrate.py status             # Show migration status
    python migrate.py check              # Verify migration checksums

Migrations are SQL files in the migrations/ directory. They can contain
a -- DOWN section for rollback SQL.

Example migration file:
    -- UP
    CREATE TABLE users (...);

    -- DOWN
    DROP TABLE IF EXISTS users;
"""

import os
import sys
import re
import time
import hashlib
import argparse
from pathlib import Path
from typing import Optional, List, Tuple, Dict

import asyncpg

# Configuration
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mcp:mcp@localhost/mcp")


def parse_migration(content: str) -> Tuple[str, Optional[str]]:
    """Parse migration file into up and down sections.

    Returns:
        (up_sql, down_sql) - down_sql may be None if not provided
    """
    # Check for -- DOWN marker
    down_marker = re.search(r'^--\s*DOWN\s*$', content, re.MULTILINE | re.IGNORECASE)

    if down_marker:
        up_sql = content[:down_marker.start()].strip()
        down_sql = content[down_marker.end():].strip()
        # Remove comment markers from down SQL
        down_lines = []
        for line in down_sql.split('\n'):
            # Remove leading -- from each line if present
            if line.strip().startswith('-- '):
                down_lines.append(line.strip()[3:])
            elif line.strip().startswith('--'):
                down_lines.append(line.strip()[2:])
            else:
                down_lines.append(line)
        down_sql = '\n'.join(down_lines).strip()
        return up_sql, down_sql if down_sql else None

    return content.strip(), None


def compute_checksum(content: str) -> str:
    """Compute SHA-256 checksum of migration content."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_migration_files() -> List[Tuple[str, str, Path]]:
    """Get list of migration files sorted by version.

    Returns:
        List of (version, name, path) tuples
    """
    migrations = []

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        # Parse version from filename: 001_name.sql -> (001, name)
        match = re.match(r'^(\d{3})_(.+)\.sql$', sql_file.name)
        if match:
            version, name = match.groups()
            migrations.append((version, name, sql_file))

    return migrations


async def ensure_migrations_table(conn: asyncpg.Connection) -> None:
    """Ensure schema_migrations table exists."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(20) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            applied_at TIMESTAMPTZ DEFAULT NOW(),
            checksum VARCHAR(64) NOT NULL,
            execution_time_ms INTEGER
        )
    """)


async def get_applied_migrations(conn: asyncpg.Connection) -> Dict[str, dict]:
    """Get all applied migrations."""
    rows = await conn.fetch("""
        SELECT version, name, checksum, applied_at, execution_time_ms
        FROM schema_migrations
        ORDER BY version
    """)
    return {row['version']: dict(row) for row in rows}


async def apply_migration(
    conn: asyncpg.Connection,
    version: str,
    name: str,
    path: Path
) -> bool:
    """Apply a single migration.

    Returns:
        True if applied, False if already applied
    """
    content = path.read_text()
    up_sql, _ = parse_migration(content)
    checksum = compute_checksum(content)

    # Check if already applied
    existing = await conn.fetchval(
        "SELECT checksum FROM schema_migrations WHERE version = $1",
        version
    )

    if existing:
        if existing != checksum:
            print(f"  WARNING: Checksum mismatch for {version}_{name}")
            print(f"    Expected: {existing}")
            print(f"    Current:  {checksum}")
        return False

    # Apply migration
    start = time.time()
    try:
        await conn.execute(up_sql)
        execution_time = int((time.time() - start) * 1000)

        # Record migration
        await conn.execute("""
            INSERT INTO schema_migrations (version, name, checksum, execution_time_ms)
            VALUES ($1, $2, $3, $4)
        """, version, name, checksum, execution_time)

        return True
    except Exception as e:
        print(f"  ERROR: Failed to apply {version}_{name}: {e}")
        raise


async def rollback_migration(
    conn: asyncpg.Connection,
    version: str,
    name: str,
    path: Path
) -> bool:
    """Rollback a single migration.

    Returns:
        True if rolled back, False if not applied or no down SQL
    """
    # Check if applied
    existing = await conn.fetchval(
        "SELECT version FROM schema_migrations WHERE version = $1",
        version
    )

    if not existing:
        print(f"  SKIP: {version}_{name} not applied")
        return False

    content = path.read_text()
    _, down_sql = parse_migration(content)

    if not down_sql:
        print(f"  ERROR: No DOWN section in {version}_{name}")
        return False

    # Apply rollback
    try:
        await conn.execute(down_sql)

        # Remove migration record
        await conn.execute(
            "DELETE FROM schema_migrations WHERE version = $1",
            version
        )

        return True
    except Exception as e:
        print(f"  ERROR: Failed to rollback {version}_{name}: {e}")
        raise


async def cmd_up(target: Optional[str] = None) -> int:
    """Apply pending migrations."""
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await ensure_migrations_table(conn)
        applied = await get_applied_migrations(conn)
        migrations = get_migration_files()

        count = 0
        for version, name, path in migrations:
            if target and version > target:
                break

            if version in applied:
                continue

            print(f"Applying: {version}_{name}")
            if await apply_migration(conn, version, name, path):
                count += 1
                print(f"  Applied: {version}_{name}")

        if count == 0:
            print("No pending migrations")
        else:
            print(f"\nApplied {count} migration(s)")

        return 0
    finally:
        await conn.close()


async def cmd_down(target: Optional[str] = None) -> int:
    """Rollback migrations."""
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await ensure_migrations_table(conn)
        applied = await get_applied_migrations(conn)
        migrations = get_migration_files()

        # Get migrations to rollback (in reverse order)
        to_rollback = []
        for version, name, path in reversed(migrations):
            if version in applied:
                to_rollback.append((version, name, path))
                if target is None:
                    # Only rollback one
                    break
                if version == target:
                    break

        if not to_rollback:
            print("No migrations to rollback")
            return 0

        count = 0
        for version, name, path in to_rollback:
            print(f"Rolling back: {version}_{name}")
            if await rollback_migration(conn, version, name, path):
                count += 1
                print(f"  Rolled back: {version}_{name}")

        print(f"\nRolled back {count} migration(s)")
        return 0
    finally:
        await conn.close()


async def cmd_status() -> int:
    """Show migration status."""
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await ensure_migrations_table(conn)
        applied = await get_applied_migrations(conn)
        migrations = get_migration_files()

        print(f"{'Version':<8} {'Status':<10} {'Name':<40} {'Applied At':<24}")
        print("-" * 82)

        for version, name, path in migrations:
            if version in applied:
                info = applied[version]
                status = "applied"
                applied_at = info['applied_at'].strftime('%Y-%m-%d %H:%M:%S')
            else:
                status = "pending"
                applied_at = ""

            print(f"{version:<8} {status:<10} {name:<40} {applied_at:<24}")

        pending = len([m for m in migrations if m[0] not in applied])
        print(f"\n{len(applied)} applied, {pending} pending")

        return 0
    finally:
        await conn.close()


async def cmd_check() -> int:
    """Verify migration checksums."""
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await ensure_migrations_table(conn)
        applied = await get_applied_migrations(conn)
        migrations = get_migration_files()

        errors = 0
        for version, name, path in migrations:
            if version not in applied:
                continue

            content = path.read_text()
            current_checksum = compute_checksum(content)
            stored_checksum = applied[version]['checksum']

            if current_checksum != stored_checksum:
                print(f"MISMATCH: {version}_{name}")
                print(f"  Stored:  {stored_checksum}")
                print(f"  Current: {current_checksum}")
                errors += 1

        if errors:
            print(f"\n{errors} checksum error(s)")
            return 1
        else:
            print("All checksums match")
            return 0
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Database migration runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    up_parser = subparsers.add_parser("up", help="Apply pending migrations")
    up_parser.add_argument("target", nargs="?", help="Apply up to this version")

    down_parser = subparsers.add_parser("down", help="Rollback migrations")
    down_parser.add_argument("target", nargs="?", help="Rollback to this version")

    subparsers.add_parser("status", help="Show migration status")
    subparsers.add_parser("check", help="Verify migration checksums")

    args = parser.parse_args()

    import asyncio

    if args.command == "up":
        return asyncio.run(cmd_up(args.target))
    elif args.command == "down":
        return asyncio.run(cmd_down(args.target))
    elif args.command == "status":
        return asyncio.run(cmd_status())
    elif args.command == "check":
        return asyncio.run(cmd_check())


if __name__ == "__main__":
    sys.exit(main())
