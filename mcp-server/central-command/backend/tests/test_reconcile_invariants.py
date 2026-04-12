"""Time-travel reconciliation — invariant guardrails (Phase 1).

These tests verify structural + security properties of the reconcile module
without needing a running backend. They catch regressions that would
re-open replay-attack windows or break Ed25519 signature validation.
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
RECONCILE_SRC = (BACKEND / "reconcile.py").read_text()
MIGRATION = BACKEND / "migrations" / "160_time_travel_reconciliation.sql"


class TestMigration160:
    """Migration 160 must contain the full foundation schema."""

    def test_migration_exists(self):
        assert MIGRATION.exists(), "Migration 160 not present"

    def test_site_appliances_gets_state_columns(self):
        sql = MIGRATION.read_text()
        for col in ("boot_counter", "generation_uuid", "nonce_epoch",
                    "last_reconcile_at", "reconcile_count"):
            assert col in sql, f"Missing column: {col}"

    def test_reconcile_events_table_created(self):
        sql = MIGRATION.read_text()
        assert "CREATE TABLE IF NOT EXISTS reconcile_events" in sql
        # Required audit fields
        for col in ("appliance_id", "site_id", "detection_signals",
                    "plan_runbook_ids", "plan_signature_hex",
                    "plan_nonce_epoch_hex", "plan_status"):
            assert col in sql, f"reconcile_events missing column: {col}"

    def test_reconcile_events_append_only(self):
        """DELETE must be blocked — otherwise audit is tamperable."""
        sql = MIGRATION.read_text()
        assert "prevent_reconcile_events_delete" in sql
        assert "BEFORE DELETE ON reconcile_events" in sql

    def test_reconcile_events_has_rls(self):
        sql = MIGRATION.read_text()
        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "FORCE ROW LEVEL SECURITY" in sql


class TestDetectionValidation:
    """Detection signal handling must be strict — prevents replay attacks."""

    def test_min_signals_required(self):
        """≥2 detection signals required — single-signal reconciles are risky."""
        m = re.search(r"MIN_SIGNALS_REQUIRED\s*=\s*(\d+)", RECONCILE_SRC)
        assert m, "MIN_SIGNALS_REQUIRED not defined"
        assert int(m.group(1)) >= 2, "Must require ≥2 signals to trigger reconcile"

    def test_clock_skew_limit_reasonable(self):
        """Max clock skew must be 1-10 min — too loose breaks TLS, too tight never reconciles."""
        m = re.search(r"MAX_CLOCK_SKEW_SECONDS\s*=\s*(\d+)", RECONCILE_SRC)
        assert m
        skew = int(m.group(1))
        assert 60 <= skew <= 600, f"Clock skew limit {skew}s out of safe range [60, 600]"

    def test_validate_detection_called_before_plan(self):
        """Detection must be validated BEFORE plan generation."""
        # Find request_reconcile function body
        m = re.search(
            r"async def request_reconcile\([^)]+\).*?(?=\n(?:async def |@router|class ))",
            RECONCILE_SRC, re.DOTALL,
        )
        assert m, "request_reconcile function not found"
        body = m.group(0)
        validate_pos = body.find("_validate_detection")
        sign_pos = body.find("sign_data")
        assert validate_pos > 0 and sign_pos > 0
        assert validate_pos < sign_pos, (
            "Detection validation must happen BEFORE signing. Otherwise a rogue "
            "appliance could spam reconciles to extract signatures."
        )


class TestSigningSafety:
    """Signing + audit must be airtight."""

    def test_site_id_enforced(self):
        """request_reconcile must check auth_site_id matches req.site_id."""
        m = re.search(
            r"async def request_reconcile\([^)]+\).*?(?=\n(?:async def |@router|class ))",
            RECONCILE_SRC, re.DOTALL,
        )
        body = m.group(0)
        assert "req.site_id != auth_site_id" in body, (
            "request_reconcile must enforce auth_site_id matches req.site_id"
        )

    def test_nonce_epoch_is_random_32_bytes(self):
        """Epoch must be freshly-generated 32 bytes per reconcile."""
        assert "secrets.token_bytes(32)" in RECONCILE_SRC, (
            "Nonce epoch must be 32 random bytes (via secrets.token_bytes)"
        )

    def test_signature_length_validated(self):
        """Signed payload length check: 128-char hex for Ed25519."""
        assert "len(signature_hex) != 128" in RECONCILE_SRC, (
            "Must validate signature is 128 hex chars (Ed25519)"
        )

    def test_signed_payload_is_sorted_json(self):
        """Canonical JSON is required so agent can reconstruct for verification."""
        m = re.search(r"_build_plan_payload.*?(?=\n(?:async def |def |@router))",
                      RECONCILE_SRC, re.DOTALL)
        assert m, "_build_plan_payload not found"
        assert "sort_keys=True" in m.group(0), (
            "Canonical JSON (sort_keys=True) required — otherwise agent's "
            "reconstructed signature check fails from field ordering"
        )

    def test_every_reconcile_logged(self):
        """Both accepted and rejected reconciles must insert into reconcile_events."""
        # Count INSERT INTO reconcile_events occurrences in the file
        insert_count = len(re.findall(r"INSERT INTO reconcile_events", RECONCILE_SRC))
        assert insert_count >= 2, (
            f"Only {insert_count} INSERTs into reconcile_events — expected ≥2 "
            "(one for accepted plan generation, one for rejections)"
        )


class TestAckEndpoint:
    """Agent ACK endpoint exists + updates correct state."""

    def test_ack_endpoint_registered(self):
        assert '@router.post("/reconcile/ack")' in RECONCILE_SRC

    def test_ack_updates_status(self):
        """ACK must update plan_status + plan_applied_at on the event row."""
        assert "plan_applied_at = NOW()" in RECONCILE_SRC
        assert "plan_status = :status" in RECONCILE_SRC
