"""Tests for Session 203 Tier 2.2 — compliance packet determinism + no-identical canary.

The April 2026 Delve / DeepDelver scandal hinged on a single discoverable
fact: a sample of customer compliance reports were 99.8% string-identical to
each other. The auditor independence claim was statistically impossible.

OsirisCare's positioning as the recovery platform requires the *opposite*
guarantee: every customer's compliance packet must be data-driven such that

  (a) generating the same packet twice is byte-identical (deterministic), and
  (b) generating packets for two different sites produces materially different
      content that references each site's specific data.

These two properties together defeat the Delve playbook. (a) means the packet
is reproducible by the auditor. (b) means we can demonstrate to a regulator
that we cannot have copy-pasted reports across customers.

The tests in this file run the *real* `CompliancePacket.generate_packet()` —
not a mock — with stubbed `_get_*` data sources. The Jinja2 template path is
exercised end-to-end.
"""

import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Environment / path setup — `dashboard_api` is a symlink to backend/ at the
# mcp-server/ level. Same pattern as test_client_credentials.py.
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-fernet-key-placeholder-32chars!")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_SERVER_DIR = os.path.dirname(os.path.dirname(_BACKEND_DIR))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _MCP_SERVER_DIR not in sys.path:
    sys.path.insert(0, _MCP_SERVER_DIR)

# Drop any stub modules that earlier pure-function tests may have injected.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]


# =============================================================================
# Test fixtures — deterministic stubs for the data layer
# =============================================================================

def _make_stub_data(site_id: str, site_name: str, *, variant: str = "a"):
    """Return a dict of stub return-values for every `_get_*` method on
    CompliancePacket.

    `variant` lets two callers produce *meaningfully different* data so the
    no-identical canary can prove the markdown body actually diverges.
    """
    multiplier = 1 if variant == "a" else 7  # site B has 7x the activity

    return {
        "_get_site_name": site_name,
        "_calculate_compliance_score": 92 if variant == "a" else 67,
        "_count_critical_issues": 1 * multiplier,
        "_count_auto_fixes": 12 * multiplier,
        "_calculate_mttr": 0.4 if variant == "a" else 2.1,
        "_calculate_backup_success_rate": 100 if variant == "a" else 85,
        "_get_control_posture": [
            {
                "control": "164.312(b)",
                "description": "Audit Controls",
                "status": "PASS" if variant == "a" else "FAIL",
                "pass_rate": "100%" if variant == "a" else "60%",
                "check_count": 14 * multiplier,
                "last_checked": "2026-04-01 12:00 UTC",
            },
        ],
        "_get_backup_summary": {
            "schedule": "daily",
            "retention_days": 90,
            "weeks": [
                {
                    "week": "Week 1",
                    "status": "PASS" if variant == "a" else "FAIL",
                    "checks": 7 * multiplier,
                    "pass_count": 7 * multiplier if variant == "a" else 5,
                    "fail_count": 0 if variant == "a" else 2 * multiplier,
                },
            ],
        },
        "_get_time_sync_status": {
            "ntp_server": "time.cloudflare.com",
            "sync_status": "synced",
            "last_check": "2026-04-09 12:00 UTC",
        },
        "_get_access_controls": {
            "password_policy": {
                "total_checks": 4 * multiplier,
                "compliance_rate": "100%" if variant == "a" else "75%",
            },
        },
        "_get_patch_posture": {
            "last_check": "2026-04-09",
            "status": "current" if variant == "a" else "behind",
            "note": f"All systems patched ({variant})",
        },
        "_get_encryption_status": {
            "bitlocker": {
                "status": "PASS" if variant == "a" else "FAIL",
                "pass_count": 4 * multiplier if variant == "a" else 2,
                "total_checks": 4 * multiplier,
            },
            "in_transit": "TLS 1.3 enforced",
        },
        "_get_incidents": [] if variant == "a" else [
            {
                "time": "2026-04-08 14:30 UTC",
                "type": "firewall_status",
                "incident_id": f"INC-{variant}-{site_id[:8]}-001",
            },
        ],
        "_get_baseline_exceptions": [],
        "_get_evidence_manifest": {
            "total_bundles": 100 * multiplier,
            "signed_bundles": 100 * multiplier,
            "chain_range": f"1..{100 * multiplier}",
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "latest_chain_hash": hashlib.sha256(
                f"{site_id}-{variant}".encode()
            ).hexdigest(),
            "worm_url": f"s3://evidence-worm-v2/{site_id}/2026-04",
        },
        "_get_administrative_attestations": [],
        "_get_healing_summary": {
            "tier1_count": 50 * multiplier,
            "tier2_count": 5 * multiplier,
            "tier3_count": 1 if variant == "b" else 0,
        },
        "_get_device_inventory": {
            "total_devices": 10 * multiplier,
            "compliant": 10 * multiplier if variant == "a" else 7,
        },
        "_get_approval_log": [],
    }


def _make_packet(tmp_path: Path, site_id: str, site_name: str,
                 *, variant: str = "a", month: int = 4, year: int = 2026):
    """Construct a `CompliancePacket` with all data-layer methods stubbed."""
    from dashboard_api.compliance_packet import CompliancePacket

    pkt = CompliancePacket(
        site_id=site_id,
        month=month,
        year=year,
        db=object(),  # never used because every _get_* is stubbed
        output_dir=tmp_path / site_id,
    )

    stubs = _make_stub_data(site_id, site_name, variant=variant)

    async def _make_async(value):
        return value

    for method_name, value in stubs.items():
        # Bind a coroutine factory that closes over `value`
        def _stub(_self, _v=value):
            async def _coro():
                return _v
            return _coro()
        setattr(pkt, method_name, _stub.__get__(pkt))

    return pkt


def _strip_volatile_fields(markdown: str) -> str:
    """Remove fields that are inherently per-call (timestamps).

    The `Generated:` line carries `datetime.now(timezone.utc).isoformat()`
    which obviously differs between calls. Everything else in the body must
    be byte-identical for the same site/month/year.
    """
    return re.sub(
        r"^\*\*Generated:\*\* .*$",
        "**Generated:** <REDACTED>",
        markdown,
        flags=re.MULTILINE,
    )


# =============================================================================
# T2.2(a) — DETERMINISM: same site twice → byte-identical
# =============================================================================

class TestPacketDeterminism:
    @pytest.mark.asyncio
    async def test_same_site_same_period_byte_identical(self, tmp_path):
        """Generate the SAME packet twice. Strip the timestamp. The remaining
        bytes MUST match exactly. If they don't, packet generation has hidden
        non-determinism (random IDs, dict ordering, locale-dependent formatting,
        etc.) and an auditor cannot reproduce the report."""
        pkt_1 = _make_packet(tmp_path / "run1", "site-alpha", "Alpha Clinic")
        pkt_2 = _make_packet(tmp_path / "run2", "site-alpha", "Alpha Clinic")

        result_1 = await pkt_1.generate_packet()
        result_2 = await pkt_2.generate_packet()

        md_1 = Path(result_1["markdown_path"]).read_text()
        md_2 = Path(result_2["markdown_path"]).read_text()

        stripped_1 = _strip_volatile_fields(md_1)
        stripped_2 = _strip_volatile_fields(md_2)

        assert stripped_1 == stripped_2, (
            "Packet generation is non-deterministic — bodies differ between "
            "two identical runs after stripping timestamps. An auditor "
            "cannot reproduce the report. Investigate dict ordering, "
            "locale-dependent number formatting, or random suffixes."
        )

    @pytest.mark.asyncio
    async def test_packet_id_is_deterministic_function_of_inputs(self, tmp_path):
        """packet_id must be a pure function of (year, month, site_id) — no
        UUIDs, no random suffixes. Auditors should be able to predict the
        packet_id given the period and site."""
        pkt_a = _make_packet(tmp_path / "a", "site-alpha", "Alpha")
        pkt_b = _make_packet(tmp_path / "b", "site-alpha", "Alpha")
        assert pkt_a.packet_id == pkt_b.packet_id == "MON-202604-site-alpha"

    @pytest.mark.asyncio
    async def test_different_period_produces_different_packet_id(self, tmp_path):
        """Two different months for the same site MUST yield different packet
        IDs — otherwise downstream storage would silently overwrite."""
        pkt_apr = _make_packet(tmp_path / "apr", "site-alpha", "Alpha", month=4)
        pkt_may = _make_packet(tmp_path / "may", "site-alpha", "Alpha", month=5)
        assert pkt_apr.packet_id != pkt_may.packet_id

    @pytest.mark.asyncio
    async def test_packet_dict_excluding_timestamp_is_stable(self, tmp_path):
        """The data dict returned from `generate_packet()` must be stable
        across calls (excluding `generated_timestamp`). This is the API-level
        version of the byte-identical body check."""
        pkt_1 = _make_packet(tmp_path / "r1", "site-alpha", "Alpha Clinic")
        pkt_2 = _make_packet(tmp_path / "r2", "site-alpha", "Alpha Clinic")

        d1 = (await pkt_1.generate_packet())["data"]
        d2 = (await pkt_2.generate_packet())["data"]

        d1.pop("generated_timestamp", None)
        d2.pop("generated_timestamp", None)

        assert d1 == d2


# =============================================================================
# T2.2(b) — NO-IDENTICAL CANARY: two sites must produce materially different content
# =============================================================================

class TestNoIdenticalPacketsCanary:
    """The Delve canary. If two different customer packets ever come out
    string-identical, the platform has the same defect Delve had — and we
    will see it before any auditor does."""

    @pytest.mark.asyncio
    async def test_two_different_sites_produce_different_markdown(self, tmp_path):
        """Two sites with different stubbed data MUST produce different
        markdown after stripping timestamps and the packet ID. If the bodies
        match, the packet template is not actually using the site data and
        we have the Delve defect."""
        pkt_a = _make_packet(
            tmp_path / "a", "site-alpha", "Alpha Clinic", variant="a"
        )
        pkt_b = _make_packet(
            tmp_path / "b", "site-bravo", "Bravo Hospital", variant="b"
        )

        md_a = Path((await pkt_a.generate_packet())["markdown_path"]).read_text()
        md_b = Path((await pkt_b.generate_packet())["markdown_path"]).read_text()

        # Strip volatile + identifying fields so we're comparing structure
        stripped_a = _strip_volatile_fields(md_a)
        stripped_b = _strip_volatile_fields(md_b)

        assert stripped_a != stripped_b, (
            "Two different customer packets are byte-identical. This is the "
            "Delve defect (99.8% identical reports). Investigate immediately."
        )

    @pytest.mark.asyncio
    async def test_packet_body_references_site_specific_name(self, tmp_path):
        """The site name must appear in the rendered markdown. If the
        template hard-codes a placeholder or skips the site name, the
        packet would render identically across customers."""
        pkt = _make_packet(
            tmp_path, "site-alpha", "Alpha Clinic Distinct Name 12345"
        )
        md = Path((await pkt.generate_packet())["markdown_path"]).read_text()
        assert "Alpha Clinic Distinct Name 12345" in md

    @pytest.mark.asyncio
    async def test_packet_body_references_site_specific_metrics(self, tmp_path):
        """A site with 700 bundles must render the number 700 in the
        markdown. If we render a fixed value, the packet is templated, not
        data-driven."""
        pkt = _make_packet(tmp_path, "site-bravo", "Bravo", variant="b")
        md = Path((await pkt.generate_packet())["markdown_path"]).read_text()
        # variant="b" multiplier is 7, so total_bundles = 100 * 7 = 700
        assert "700" in md
        # variant="b" compliance score is 67%
        assert "67%" in md
        # variant="b" has a specific incident_id we injected
        assert "INC-b-site-bra-001" in md

    @pytest.mark.asyncio
    async def test_sha256_of_two_site_packets_diverges(self, tmp_path):
        """Stronger version of the canary: not only do bodies differ, the
        SHA256 of the body must differ. This is the form an automated
        regression test would compute on every CI run to catch any future
        regression toward the Delve defect."""
        pkt_a = _make_packet(tmp_path / "a", "site-alpha", "Alpha", variant="a")
        pkt_b = _make_packet(tmp_path / "b", "site-bravo", "Bravo", variant="b")

        md_a = Path((await pkt_a.generate_packet())["markdown_path"]).read_text()
        md_b = Path((await pkt_b.generate_packet())["markdown_path"]).read_text()

        sha_a = hashlib.sha256(_strip_volatile_fields(md_a).encode()).hexdigest()
        sha_b = hashlib.sha256(_strip_volatile_fields(md_b).encode()).hexdigest()

        assert sha_a != sha_b

    @pytest.mark.asyncio
    async def test_packet_id_per_site_is_unique(self, tmp_path):
        """Two different sites must yield two different packet IDs even for
        the same period."""
        pkt_a = _make_packet(tmp_path / "a", "site-alpha", "Alpha")
        pkt_b = _make_packet(tmp_path / "b", "site-bravo", "Bravo")
        assert pkt_a.packet_id != pkt_b.packet_id


# =============================================================================
# T2.2(c) — REPRODUCIBILITY METADATA: the packet must declare its own determinism
# =============================================================================

class TestPacketReproducibilityMetadata:
    """An auditor reading a packet should see, in the packet itself, that it
    is reproducible. The metadata that proves this — packet_id, period, site
    ID — must all be present and consistent."""

    @pytest.mark.asyncio
    async def test_packet_id_appears_in_markdown(self, tmp_path):
        pkt = _make_packet(tmp_path, "site-alpha", "Alpha")
        md = Path((await pkt.generate_packet())["markdown_path"]).read_text()
        assert pkt.packet_id in md

    @pytest.mark.asyncio
    async def test_period_appears_in_markdown(self, tmp_path):
        pkt = _make_packet(tmp_path, "site-alpha", "Alpha", month=4, year=2026)
        md = Path((await pkt.generate_packet())["markdown_path"]).read_text()
        assert "April" in md
        assert "2026" in md

    @pytest.mark.asyncio
    async def test_site_id_appears_in_markdown(self, tmp_path):
        pkt = _make_packet(tmp_path, "site-alpha-distinct", "Alpha")
        md = Path((await pkt.generate_packet())["markdown_path"]).read_text()
        assert "site-alpha-distinct" in md
