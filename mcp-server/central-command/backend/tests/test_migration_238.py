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
async def test_substrate_action_invocations_indexes():
    async with async_session() as db:
        rows = await db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'substrate_action_invocations'"
        ))
        names = {r[0] for r in rows}
    assert "substrate_action_invocations_idem" in names
    assert "substrate_action_invocations_actor_time" in names
    assert "substrate_action_invocations_action_time" in names
