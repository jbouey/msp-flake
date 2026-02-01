"""System Settings API for Central Command Dashboard.

Provides endpoints for managing global system settings.
Settings are stored in a single-row table for easy retrieval.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel

try:
    from .auth import require_admin
except ImportError:
    from auth import require_admin


router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


class SystemSettings(BaseModel):
    """System-wide settings."""
    # Display
    timezone: str = "America/New_York"
    date_format: str = "MM/DD/YYYY"

    # Session
    session_timeout_minutes: int = 60
    require_2fa: bool = False

    # Fleet
    auto_update_enabled: bool = True
    update_window_start: str = "02:00"
    update_window_end: str = "06:00"
    rollout_percentage: int = 5

    # Data Retention
    telemetry_retention_days: int = 90
    incident_retention_days: int = 365
    audit_log_retention_days: int = 730

    # Notifications
    email_notifications_enabled: bool = True
    slack_notifications_enabled: bool = False
    escalation_timeout_minutes: int = 60

    # API
    api_rate_limit: int = 100
    webhook_timeout_seconds: int = 30


async def get_db():
    """Get database session."""
    try:
        from main import async_session
    except ImportError:
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
            raise RuntimeError("Database session not configured")

    async with async_session() as session:
        yield session


async def ensure_settings_table(db: AsyncSession):
    """Ensure the system_settings table exists."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            settings JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by VARCHAR(255)
        )
    """))
    # Insert default row if not exists
    await db.execute(text("""
        INSERT INTO system_settings (id, settings)
        VALUES (1, '{}')
        ON CONFLICT (id) DO NOTHING
    """))
    await db.commit()


@router.get("", response_model=SystemSettings)
async def get_settings(db: AsyncSession = Depends(get_db), admin: Dict[str, Any] = Depends(require_admin)):
    """Get current system settings (admin only)."""
    await ensure_settings_table(db)

    result = await db.execute(text(
        "SELECT settings FROM system_settings WHERE id = 1"
    ))
    row = result.fetchone()

    if row and row.settings:
        # Merge stored settings with defaults
        defaults = SystemSettings()
        stored = row.settings
        return SystemSettings(**{**defaults.model_dump(), **stored})

    return SystemSettings()


@router.put("", response_model=SystemSettings)
async def update_settings(
    settings: SystemSettings,
    db: AsyncSession = Depends(get_db),
    admin: Dict[str, Any] = Depends(require_admin)
):
    """Update system settings (admin only)."""
    await ensure_settings_table(db)

    await db.execute(
        text("""
            UPDATE system_settings
            SET settings = :settings,
                updated_at = NOW()
            WHERE id = 1
        """),
        {"settings": settings.model_dump_json()}
    )
    await db.commit()

    return settings


@router.post("/purge-telemetry")
async def purge_old_telemetry(db: AsyncSession = Depends(get_db), admin: Dict[str, Any] = Depends(require_admin)):
    """Purge telemetry data older than retention period (admin only)."""
    # Get current retention setting
    settings = await get_settings(db)
    retention_days = settings.telemetry_retention_days

    # SECURITY: Use parameterized query to prevent SQL injection
    result = await db.execute(
        text("DELETE FROM execution_telemetry WHERE created_at < NOW() - INTERVAL '1 day' * :days RETURNING id"),
        {"days": retention_days}
    )
    deleted = len(result.fetchall())
    await db.commit()

    return {"deleted": deleted, "retention_days": retention_days}


@router.post("/reset-learning")
async def reset_learning_data(db: AsyncSession = Depends(get_db), admin: Dict[str, Any] = Depends(require_admin)):
    """Reset all learning data (patterns and L1 rules) (admin only)."""
    # Delete patterns
    patterns_result = await db.execute(text("DELETE FROM patterns RETURNING id"))
    patterns_deleted = len(patterns_result.fetchall())

    # Delete L1 rules (only auto-promoted ones)
    rules_result = await db.execute(text(
        "DELETE FROM l1_rules WHERE promoted_from_l2 = true RETURNING id"
    ))
    rules_deleted = len(rules_result.fetchall())

    await db.commit()

    return {
        "patterns_deleted": patterns_deleted,
        "rules_deleted": rules_deleted
    }
