"""Auditor-kit determinism contract test (round-table 2026-05-06).

Steve P0 + Coach P0: a tamper-evidence promise is empty without
byte-determinism. Two consecutive downloads of an unchanged chain
must produce byte-identical ZIPs so an auditor can re-download
later and prove non-substitution by hash comparison.

This is a CONTRACT test — it does not run live HTTP. It exercises
the deterministic primitives directly: helper signatures, JSON
canonicalization rules, ZipInfo mtime pinning. The endpoint test
that hits FastAPI is in test_auditor_kit_endpoint.py (live HTTP +
DB fixture) and is the integration version of this contract.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import zipfile

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_EVIDENCE_CHAIN = _BACKEND / "evidence_chain.py"


def _load_endpoint_source() -> str:
    return _EVIDENCE_CHAIN.read_text()


def test_zwrite_helper_pins_date_time_and_compress():
    """The deterministic helper must pin date_time, compress_type,
    and external_attr on every ZipInfo. Source-shape audit of the
    module-level _kit_zwrite helper (Coach P1-2 extraction)."""
    src = _load_endpoint_source()
    assert "def _kit_zwrite(" in src, (
        "Module-level _kit_zwrite helper must be defined "
        "(Coach P1-2 extraction so test imports SAME impl)."
    )
    helper_block_start = src.find("def _kit_zwrite(")
    helper_block = src[helper_block_start : helper_block_start + 800]
    assert "date_time=mtime" in helper_block, (
        "_kit_zwrite must pin date_time from caller-supplied mtime."
    )
    assert "ZIP_DEFLATED" in helper_block, (
        "_kit_zwrite must pin compress_type to ZIP_DEFLATED."
    )
    assert "external_attr" in helper_block, (
        "_kit_zwrite must pin external_attr (file mode bits) so "
        "two downloads agree on permissions."
    )
    # Inner closure in download_auditor_kit must delegate to the
    # module-level helper, not duplicate the logic.
    assert "_kit_zwrite(zf, name, data, zip_mtime)" in src, (
        "download_auditor_kit's local _zwrite must delegate to "
        "_kit_zwrite (no implementation drift)."
    )
    # Coach P1-3: explicit compresslevel pin.
    assert "_KIT_COMPRESSLEVEL = 6" in src, (
        "_KIT_COMPRESSLEVEL must be pinned to 6 (zlib level) so "
        "byte-identity holds across CPython builds."
    )
    assert "compresslevel=_KIT_COMPRESSLEVEL" in src, (
        "ZipFile must be opened with compresslevel=_KIT_COMPRESSLEVEL."
    )


def test_zip_mtime_derived_from_chain_head_not_wall_clock():
    """generated_at + zip_mtime must derive from the chain-head
    bundle's created_at. Wall-clock derivation is the regression
    vector this fix exists to close."""
    src = _load_endpoint_source()
    # Locate the download_auditor_kit body
    idx = src.find("async def download_auditor_kit(")
    assert idx > 0
    body = src[idx : idx + 80000]  # entire endpoint
    # Find the generated_at assignment in the body
    ga_idx = body.find("generated_at = content_ts.isoformat()")
    assert ga_idx > 0, (
        "generated_at must be derived from content_ts (chain-head "
        "bundle's created_at), not datetime.now()."
    )
    # zip_mtime must use content_ts components
    zm_idx = body.find("zip_mtime = (")
    assert zm_idx > 0
    zm_block = body[zm_idx : zm_idx + 200]
    assert "content_ts.year" in zm_block, (
        "zip_mtime must be derived from content_ts components."
    )
    # download_at (wall-clock) is allowed but only for audit log,
    # not embedded in artifacts.
    da_idx = body.find("download_at = datetime.now")
    assert da_idx > 0, (
        "download_at must exist as the wall-clock timestamp for "
        "audit log + Content-Disposition filename — separate from "
        "the deterministic generated_at used in artifacts."
    )


def test_json_dumps_uses_sort_keys_in_kit_artifacts():
    """Every JSON artifact in the kit must use sort_keys=True so
    key ordering is canonical across downloads."""
    src = _load_endpoint_source()
    idx = src.find("async def download_auditor_kit(")
    body = src[idx : idx + 80000]
    # The fixed-entry block writes chain.json, pubkeys.json,
    # identity_chain.json, iso_ca_bundle.json. Each must use
    # sort_keys=True.
    expected_artifacts = [
        ("chain.json", "_json.dumps(chain_metadata"),
        ("pubkeys.json", "_json.dumps(pubkeys_payload"),
        ("identity_chain.json", "_json.dumps(identity_chain_payload"),
        ("iso_ca_bundle.json", "_json.dumps(iso_ca_payload"),
    ]
    for label, marker in expected_artifacts:
        m_idx = body.find(marker)
        assert m_idx > 0, f"{label} dump call not found"
        # Walk forward to the closing paren and inspect arguments
        snippet = body[m_idx : m_idx + 200]
        assert "sort_keys=True" in snippet, (
            f"{label}: _json.dumps call must include sort_keys=True "
            f"for canonical key ordering."
        )
    # bundles.jsonl per-line must also sort_keys
    bjsonl_idx = body.find("_json.dumps(bundle_obj")
    assert bjsonl_idx > 0
    jsnippet = body[bjsonl_idx : bjsonl_idx + 100]
    assert "sort_keys=True" in jsnippet, (
        "bundles.jsonl per-line bundle_obj must sort_keys=True."
    )


def test_entries_written_in_sorted_order():
    """The fixed-entries list must be iterated in sorted() order so
    the ZIP TOC is deterministic. OTS files + advisories ordered
    by sorted filename."""
    src = _load_endpoint_source()
    idx = src.find("async def download_auditor_kit(")
    body = src[idx : idx + 80000]
    assert "for name, blob in sorted(fixed_entries):" in body, (
        "Fixed entries must be written in sorted(filename) order."
    )
    assert "for ots_name in sorted(ots_files):" in body, (
        "OTS files must be written in sorted filename order."
    )


def test_advisories_collected_once_not_twice():
    """Steve P3: _collect_security_advisories was called twice.
    One call, cached, used at both render sites."""
    src = _load_endpoint_source()
    idx = src.find("async def download_auditor_kit(")
    body = src[idx : idx + 80000]
    # Inside the endpoint we should see exactly one call
    call_count = body.count("_collect_security_advisories()")
    assert call_count == 1, (
        f"_collect_security_advisories() called {call_count}x "
        f"inside download_auditor_kit; should be 1. Cache the "
        f"result in `advisories` and reuse."
    )


def test_kit_version_consistent_across_surfaces():
    """Steve P3 + Carol P3: kit_version was 1.0 / 2.0 / 2.1 across
    different surfaces. Pin to 2.1 (chain_metadata.kit_version is
    canonical per round-table 2026-05-06)."""
    src = _load_endpoint_source()
    idx = src.find("async def download_auditor_kit(")
    body = src[idx : idx + 80000]
    # X-Kit-Version header
    hdr_idx = body.find('"X-Kit-Version":')
    assert hdr_idx > 0
    hdr_snippet = body[hdr_idx : hdr_idx + 80]
    assert '"2.1"' in hdr_snippet, (
        "X-Kit-Version header must be 2.1 to match "
        "chain_metadata.kit_version."
    )
    # pubkeys_payload kit_version
    pk_idx = body.find('"kit_version": "2.1"')
    assert pk_idx > 0, (
        "pubkeys_payload must declare kit_version 2.1 (was 2.0 — "
        "drifted across surfaces, fixed round-table 2026-05-06)."
    )


def test_max_parent_walk_bound_present():
    """Maya P2 follow-up: parent walk in _collect_security_advisories
    must have a hard depth bound (cycle/symlink defense)."""
    src = _load_endpoint_source()
    idx = src.find("def _collect_security_advisories")
    body = src[idx : idx + 3000]
    assert "MAX_PARENT_WALK" in body, (
        "_collect_security_advisories must enforce MAX_PARENT_WALK "
        "bound on the parent-walk loop (Maya P2 follow-up)."
    )
    assert "i >= MAX_PARENT_WALK" in body or "if i >=" in body, (
        "MAX_PARENT_WALK bound must be checked inside the loop."
    )


def test_readme_includes_scope_and_complaint_sections():
    """Carol P0-3 + P1-1: README must include Scope-of-this-kit
    section and Complaints-and-concerns section."""
    src = _load_endpoint_source()
    readme_idx = src.find("_AUDITOR_KIT_README = ")
    readme_end = src.find('"""', readme_idx + len("_AUDITOR_KIT_README = "))
    readme_end = src.find('"""', readme_end + 3) + 3
    readme = src[readme_idx:readme_end]
    assert "## Scope of this kit" in readme, (
        "README must include 'Scope of this kit' section "
        "(Carol P0-3 narrowing — kit is integrity evidence, "
        "not §164.528 disclosure accounting)."
    )
    assert "§164.528" in readme, (
        "README scope section must explicitly cite §164.528 to "
        "avoid over-claim."
    )
    assert "## Complaints and concerns" in readme, (
        "README must include 'Complaints and concerns' section "
        "with HHS OCR pointer (Carol P1-1)."
    )
    assert "hhs.gov/hipaa/filing-a-complaint" in readme, (
        "Complaint section must include HHS OCR URL."
    )
    assert "compliance@osiriscare.com" in readme, (
        "Complaint section must include OsirisCare's compliance "
        "channel email."
    )
    assert "## Reproducibility" in readme, (
        "README must document the determinism contract so "
        "auditors know they can compare ZIP hashes."
    )


def test_readme_does_not_contain_banned_legal_words():
    """CLAUDE.md hard rule (Session 199 legal-language). The
    auditor-kit is the most adversarially-read artifact in the
    project — verify zero banned words."""
    src = _load_endpoint_source()
    readme_idx = src.find("_AUDITOR_KIT_README = ")
    readme_end = src.find('"""', readme_idx + len("_AUDITOR_KIT_README = "))
    readme_end = src.find('"""', readme_end + 3) + 3
    readme = src[readme_idx:readme_end].lower()
    banned = (
        " ensures",
        " prevents",
        " protects",
        " guarantees",
        " audit-ready",
        " phi never leaves",
        " 100%",
    )
    for word in banned:
        assert word not in readme, (
            f"README contains banned legal-language phrase: "
            f"`{word.strip()}` (CLAUDE.md Session 199)."
        )


def test_zip_helper_produces_deterministic_archive_on_synthetic_input():
    """Direct unit test exercising the determinism primitives.
    Importing evidence_chain at test-collection time triggers
    FastAPI route inspection which can fail on Python 3.14 with
    `inspect.signature` regressions, so this test inlines the
    primitives — drift detection is enforced separately by
    `test_zwrite_helper_pins_date_time_and_compress` which
    source-audits the production helper's shape (Coach P1-2)."""
    _KIT_COMPRESSLEVEL = 6
    zip_mtime = (2026, 5, 6, 0, 0, 0)

    def _kit_zwrite_inline(zf, name, data, mtime):
        zi = zipfile.ZipInfo(filename=name, date_time=mtime)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        zf.writestr(zi, data)

    def _build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(
            buf, "w", zipfile.ZIP_DEFLATED, compresslevel=_KIT_COMPRESSLEVEL,
        ) as zf:
            entries = [
                ("README.md", "static readme content"),
                (
                    "chain.json",
                    json.dumps(
                        {"b": 2, "a": 1, "z": [3, 1, 2]},
                        indent=2,
                        sort_keys=True,
                    ),
                ),
                (
                    "bundles.jsonl",
                    "\n".join(
                        json.dumps(
                            {"b": 2, "a": 1, "id": "x"}, sort_keys=True
                        )
                        for _ in range(3)
                    )
                    + "\n",
                ),
            ]
            for name, blob in sorted(entries):
                _kit_zwrite_inline(zf, name, blob, zip_mtime)
        return buf.getvalue()

    bytes_one = _build()
    bytes_two = _build()
    assert bytes_one == bytes_two, (
        "Determinism primitives produced different bytes on "
        "identical input — regression in _kit_zwrite, sort_keys, "
        "compresslevel, or entry ordering."
    )


def test_partner_session_branch_in_evidence_view_access():
    """Round-table 2026-05-06 P0 (Maya/Steve/Coach): partner-portal
    parity. require_evidence_view_access must accept the partner
    session cookie + role-gate to admin/tech."""
    src = _load_endpoint_source()
    fn_idx = src.find("async def require_evidence_view_access(")
    fn_end = src.find("# Ed25519 signature verification", fn_idx)
    fn_body = src[fn_idx:fn_end]
    assert "osiris_partner_session" in fn_body, (
        "Partner session cookie must be accepted by the evidence "
        "view access gate (round-table 2026-05-06 P0)."
    )
    assert 'role in {"admin", "tech"}' in fn_body, (
        "Partner branch must role-gate to admin/tech per CLAUDE.md "
        "RT31 (billing-role partner_users must NOT pull evidence)."
    )
    assert "partner_id = $2" in fn_body or "AND partner_id" in fn_body, (
        "Partner branch must verify sites.partner_id matches "
        "session's partner_id (cross-tenant defense)."
    )
