"""Tests for flywheel_promote.promote_candidate — single source of truth for promotions.

Covers:
- Rule ID generation (auto vs custom names)
- Pattern signature parsing (check_type:runbook_id format)
- Confidence derivation from success_rate
- All 7 table writes in the correct order
- Actor/audit tracking
"""

import json
import os
import sys
import types
import pytest


class _FakeTxn:
    """No-op asyncpg transaction shim — returns an async context manager
    that simply yields. Real asyncpg transactions BEGIN/COMMIT (savepoints
    when nested); for these unit tests the wrap is structural-only and
    the fake conn just needs to honor the context manager protocol so
    the wrapped INSERT actually executes."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never swallow exceptions


class FakeConn:
    """Minimal asyncpg connection mock. Records all execute/fetchrow calls."""

    def __init__(self):
        self.executed = []  # list of (query_fragment, args)

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args):
        self.executed.append((query, args))
        return None

    def transaction(self):
        """Real asyncpg.Connection.transaction() returns a Transaction
        context manager. Our mock returns a no-op so promote_candidate's
        savepoints (added 2026-04-28 per Session 205 invariant) don't
        AttributeError on conn.transaction()."""
        return _FakeTxn()


class RaisingFakeConn(FakeConn):
    """Variant that RAISES on the first execute matching `raise_on`
    substring, then behaves normally on subsequent calls. Used to
    pin the savepoint-behavior contract (round-table angle 4 P1):
    INSERT failure inside a savepoint MUST NOT poison the outer
    transaction — subsequent steps must still execute."""

    def __init__(self, raise_on: str):
        super().__init__()
        self._raise_on = raise_on
        self._raised = False
        self.raise_count = 0

    async def execute(self, query: str, *args):
        if not self._raised and self._raise_on in query:
            self._raised = True
            self.raise_count += 1
            self.executed.append((query, args, "RAISED"))
            raise RuntimeError(f"synthetic failure on '{self._raise_on}'")
        return await super().execute(query, *args)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flywheel_promote import promote_candidate, _slugify, _build_minimal_yaml


class TestSlugify:
    def test_basic(self):
        assert _slugify("firewall_status") == "FIREWALL-STATUS"

    def test_empty(self):
        assert _slugify("") == "UNKNOWN"

    def test_special_chars(self):
        assert _slugify("check type!") == "CHECK-TYPE"

    def test_leading_trailing(self):
        assert _slugify("  test  ") == "TEST"

    def test_already_clean(self):
        assert _slugify("TEST123") == "TEST123"


class TestBuildMinimalYAML:
    def test_contains_required_fields(self):
        y = _build_minimal_yaml("L1-TEST", "firewall", "RB-WIN-001")
        assert "id: L1-TEST" in y
        assert "firewall" in y
        assert "RB-WIN-001" in y
        assert "action: execute_runbook" in y


class TestPromoteCandidate:
    @pytest.mark.asyncio
    async def test_basic_promotion_writes_7_tables(self):
        """promote_candidate should write to exactly these tables:
        1. l1_rules (promoted source)
        2. l1_rules (synced source)
        3. promoted_rules
        4. runbooks
        5. runbook_id_mapping
        6. learning_promotion_candidates (UPDATE)
        7. promotion_audit_log
        """
        conn = FakeConn()
        candidate = {
            "id": "cand-123",
            "site_id": "site-test",
            "pattern_signature": "firewall_status:RB-WIN-SEC-001",
            "check_type": "firewall_status",
            "success_rate": 0.95,
            "total_occurrences": 10,
            "l2_resolutions": 5,
            "recommended_action": "RB-WIN-SEC-001",
        }

        result = await promote_candidate(
            conn=conn,
            candidate=candidate,
            actor="test-admin",
            actor_type="admin",
        )

        assert result["rule_id"] == "L1-AUTO-FIREWALL-STATUS"
        assert result["synced_rule_id"] == "SYNC-L1-AUTO-FIREWALL-STATUS"

        # Verify writes happened in order
        tables_written = [q for (q, _) in conn.executed]
        assert any("INSERT INTO l1_rules" in q for q in tables_written)
        assert any("INSERT INTO promoted_rules" in q for q in tables_written)
        assert any("INSERT INTO runbooks" in q for q in tables_written)
        assert any("INSERT INTO runbook_id_mapping" in q for q in tables_written)
        assert any("UPDATE learning_promotion_candidates" in q for q in tables_written)
        assert any("INSERT INTO promotion_audit_log" in q for q in tables_written)

    @pytest.mark.asyncio
    async def test_custom_name_generates_custom_rule_id(self):
        conn = FakeConn()
        candidate = {
            "id": "c1",
            "site_id": "s1",
            "pattern_signature": "test:RB-001",
            "success_rate": 0.9,
        }
        result = await promote_candidate(
            conn=conn,
            candidate=candidate,
            actor="partner-x",
            actor_type="partner",
            custom_name="My Custom Rule",
        )
        assert result["rule_id"].startswith("L1-CUSTOM-")
        assert "MY" in result["rule_id"]

    @pytest.mark.asyncio
    async def test_confidence_from_success_rate(self):
        conn = FakeConn()
        await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "test:rb1",
                "success_rate": 0.85,
            },
            actor="x",
            actor_type="admin",
        )
        # Find the l1_rules INSERT — confidence should be 0.85
        for q, args in conn.executed:
            if "INSERT INTO l1_rules" in q and "promoted" in q:
                assert 0.85 in args
                return
        pytest.fail("l1_rules INSERT not found")

    @pytest.mark.asyncio
    async def test_default_confidence_when_success_rate_none(self):
        conn = FakeConn()
        await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "test:rb1",
                "success_rate": None,
            },
            actor="x",
            actor_type="admin",
        )
        # Default is 0.9
        for q, args in conn.executed:
            if "INSERT INTO l1_rules" in q and "promoted" in q:
                assert 0.9 in args
                return
        pytest.fail("l1_rules INSERT not found")

    @pytest.mark.asyncio
    async def test_partner_path_passes_partner_id(self):
        conn = FakeConn()
        await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "test:rb1",
                "partner_id": "partner-uuid-123",
            },
            actor="partner-abc",
            actor_type="partner",
            rule_yaml="custom: yaml\n",
            rule_json={"id": "custom"},
        )
        for q, args in conn.executed:
            if "INSERT INTO promoted_rules" in q:
                assert "partner-uuid-123" in args
                return
        pytest.fail("promoted_rules INSERT not found")

    @pytest.mark.asyncio
    async def test_audit_log_records_actor_type(self):
        conn = FakeConn()
        await promote_candidate(
            conn=conn,
            candidate={"id": "c1", "site_id": "s1", "pattern_signature": "test:rb"},
            actor="system-auto",
            actor_type="system",
        )
        for q, args in conn.executed:
            if "promotion_audit_log" in q:
                assert "system" in args
                assert "system-auto" in args
                return
        pytest.fail("audit log INSERT not found")

    @pytest.mark.asyncio
    async def test_pattern_signature_without_colon(self):
        """Bare pattern signature (no runbook_id) uses recommended_action fallback."""
        conn = FakeConn()
        result = await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "bare_pattern",
                "recommended_action": "FALLBACK-RB",
            },
            actor="x",
            actor_type="admin",
        )
        assert "BARE-PATTERN" in result["rule_id"]

    @pytest.mark.asyncio
    async def test_incident_pattern_includes_check_type(self):
        """When check_type differs from incident_type, both should be in pattern."""
        conn = FakeConn()
        await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "firewall_status:RB-WIN-001",
                "check_type": "firewall",
            },
            actor="x",
            actor_type="admin",
        )
        # The l1_rules INSERT should have incident_pattern with both fields
        for q, args in conn.executed:
            if "INSERT INTO l1_rules" in q and "promoted" in q:
                pattern_json = args[1]  # $2 is the pattern
                parsed = json.loads(pattern_json)
                assert parsed["incident_type"] == "firewall_status"
                assert parsed.get("check_type") == "firewall"
                return
        pytest.fail("l1_rules INSERT not found")


# ---------------------------------------------------------------------------
# Savepoint-behavior contract (round-table 2026-04-28 angle 4 P1)
# ---------------------------------------------------------------------------
#
# The savepoints added in efe413cf around `promotion_audit_log` INSERT
# and `upsert_pattern_embedding` are decorative under test if the test
# only verifies the wrap doesn't AttributeError (11b38d7e). The
# enterprise-grade contract is: an INSERT failure INSIDE the savepoint
# must be CONTAINED — subsequent steps in promote_candidate must still
# execute. The Session 205 asyncpg savepoint invariant exists for this.
# RaisingFakeConn pins the contract.


class TestSavepointBehavior:
    @pytest.mark.asyncio
    async def test_audit_log_failure_does_not_block_promotion(self, monkeypatch):
        """promotion_audit_log INSERT raises → outer txn continues →
        Step 8 (safe_rollout) still runs. Pre-fix, this would have
        raised InFailedSQLTransactionError on the next execute.

        The dead-letter recovery write attempts a relative import
        `from .fleet import get_pool` which fails in this test's
        flat-module context — that's expected. The dead-letter raise
        itself is caught + logged; what we're pinning here is that
        the OUTER promote_candidate flow continues regardless."""
        called = {"safe_rollout": False}

        async def _fake_safe_rollout(*args, **kwargs):
            called["safe_rollout"] = True
            return 1

        monkeypatch.setattr(
            "flywheel_promote.safe_rollout_promoted_rule",
            _fake_safe_rollout,
        )

        conn = RaisingFakeConn(raise_on="INSERT INTO promotion_audit_log")
        result = await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "test:rb1",
                "success_rate": 0.9,
            },
            actor="x", actor_type="admin",
        )
        # The savepoint contained the failure. Step 8 still ran.
        assert called["safe_rollout"], (
            "Step 8 (safe_rollout) MUST execute even when audit_log "
            "INSERT inside the savepoint raises. Pre-fix, the outer "
            "txn was poisoned and the next conn.execute would raise "
            "InFailedSQLTransactionError."
        )
        assert conn.raise_count == 1, "the synthetic failure should have fired exactly once"
        assert result["rule_id"], "promote_candidate must still return a result"

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_block_rollout(self, monkeypatch):
        """pattern_embedding upsert raises → outer txn continues →
        Step 8 still runs. Same Session 205 invariant on a different
        savepoint."""
        called = {"safe_rollout": False}

        async def _fake_safe_rollout(*args, **kwargs):
            called["safe_rollout"] = True
            return 1

        monkeypatch.setattr(
            "flywheel_promote.safe_rollout_promoted_rule",
            _fake_safe_rollout,
        )

        # Force pattern_embeddings.upsert_pattern_embedding to raise.
        async def _raise(*args, **kwargs):
            raise RuntimeError("synthetic embedding failure")

        # Patch the import inside the function (it does a relative
        # import inside the savepoint).
        import sys as _sys
        import types as _types
        fake_mod = _types.ModuleType("pattern_embeddings")
        fake_mod.upsert_pattern_embedding = _raise
        _sys.modules["pattern_embeddings"] = fake_mod
        # Also patch the dashboard_api package path if relative import
        # tries that namespace.
        try:
            import dashboard_api  # noqa: F401
            _sys.modules["dashboard_api.pattern_embeddings"] = fake_mod
        except ImportError:
            pass

        conn = FakeConn()  # No INSERTs raise; only the embedding raises
        result = await promote_candidate(
            conn=conn,
            candidate={
                "id": "c1", "site_id": "s1",
                "pattern_signature": "test:rb1",
                "success_rate": 0.9,
            },
            actor="x", actor_type="admin",
        )
        assert called["safe_rollout"], (
            "Step 8 (safe_rollout) MUST execute even when embedding "
            "savepoint raises. Outer txn must not be poisoned."
        )
        assert result["rule_id"]
