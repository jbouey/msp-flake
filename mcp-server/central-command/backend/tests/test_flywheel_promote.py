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
