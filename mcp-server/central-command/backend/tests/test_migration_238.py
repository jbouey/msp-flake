import pytest
from sqlalchemy import text
from shared import async_session

@pytest.mark.asyncio
async def test_substrate_action_invocations_schema():
    async with async_session() as db:
        cols = await db.execute(text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'substrate_action_invocations' "
            "ORDER BY ordinal_position"
        ))
        cols = list(cols)
    names = {r[0] for r in cols}
    assert {"id", "idempotency_key", "actor_email", "action_key",
            "target_ref", "reason", "result_status", "result_body",
            "admin_audit_id", "created_at"} <= names

@pytest.mark.asyncio
async def test_substrate_action_invocations_unique_index():
    async with async_session() as db:
        idx = await db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'substrate_action_invocations' "
            "AND indexname = 'substrate_action_invocations_idem'"
        ))
        assert idx.scalar() == "substrate_action_invocations_idem"
