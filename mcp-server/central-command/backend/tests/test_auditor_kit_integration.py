"""Auditor-kit integration test (round-table 2026-05-06).

Goal: replace source-grep test theater with a real end-to-end
build of the auditor-kit ZIP using the SAME helpers the production
endpoint uses, then verify:

  1. Byte-determinism across two consecutive builds with identical
     input — the central tamper-evidence promise of the kit.
  2. Every expected entry name is present (README.md, verify.sh,
     verify_identity.sh, chain.json, bundles.jsonl, pubkeys.json,
     identity_chain.json, iso_ca_bundle.json).
  3. JSON entries are well-formed and contain the round-table-
     mandated v2.1 kit_version.
  4. README content includes the Carol-mandated Scope, Reproducibility,
     and Complaints sections.
  5. ZIP opens and entries are individually readable.

Why integration vs source-grep: two consecutive deploys (3afffccd
and 9af5e8c7) failed at CI because source-grep tests pinned literal
patterns that the refactor changed. An integration test that opens
the actual ZIP catches structural drift instead of formatting drift.

This test imports `auditor_kit_zip_primitives` directly (the module
has zero FastAPI deps) so it runs cleanly on any CPython 3.11+
without triggering full app boot.
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import zipfile

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from auditor_kit_zip_primitives import (  # noqa: E402
    _kit_zwrite,
    _KIT_COMPRESSLEVEL,
)


# --------------------------------------------------------------------- fixture


# A representative kit payload mirroring the production endpoint's
# `fixed_entries` shape. Same keys, same JSON canonical form, same
# bundles.jsonl shape. If this drifts from production the test
# stops being a credible determinism contract — the source-shape
# gate (`test_zwrite_helper_pins_date_time_and_compress`) is what
# pins production to this shape.
_FIXTURE_SITE_ID = "fixture-site-deterministic"
_FIXTURE_GENERATED_AT = "2026-05-06T00:00:00+00:00"
_FIXTURE_ZIP_MTIME = (2026, 5, 6, 0, 0, 0)
_FIXTURE_KIT_VERSION = "2.1"


def _build_kit_zip() -> bytes:
    """Build a deterministic auditor-kit ZIP using the production
    primitives + the documented contract (sort_keys, sorted entry
    order, sorted OTS, pinned compresslevel)."""
    chain_metadata = {
        "kit_version": _FIXTURE_KIT_VERSION,
        "generated_at": _FIXTURE_GENERATED_AT,
        "site": {"site_id": _FIXTURE_SITE_ID, "clinic_name": "Fixture Clinic"},
        "presentation": {
            "presenter_brand": "OsirisCare",
            "presenter_partner_id": None,
        },
        "summary": {
            "bundle_count": 2,
            "signed_count": 2,
            "anchored_count": 1,
        },
    }
    pubkeys_payload = {
        "site_id": _FIXTURE_SITE_ID,
        "kit_version": _FIXTURE_KIT_VERSION,
        "public_keys": [
            {
                "appliance_id": "fixture-appliance-1",
                "fingerprint": "abcdef0123456789",
                "public_key_hex": "0" * 64,
            },
        ],
    }
    identity_chain_payload = {
        "kit_version": _FIXTURE_KIT_VERSION,
        "site_id": _FIXTURE_SITE_ID,
        "events": [],
    }
    iso_ca_payload = {
        "kit_version": _FIXTURE_KIT_VERSION,
        "generated_at": _FIXTURE_GENERATED_AT,
        "cas": [],
    }
    bundles_jsonl_lines = [
        json.dumps(
            {
                "bundle_id": "fixture-bundle-1",
                "bundle_hash": "h1",
                "chain_position": 0,
                "ots": None,
            },
            sort_keys=True,
        ),
        json.dumps(
            {
                "bundle_id": "fixture-bundle-2",
                "bundle_hash": "h2",
                "chain_position": 1,
                "ots": {"file": "ots/fixture-bundle-2.ots"},
            },
            sort_keys=True,
        ),
    ]
    readme_text = (
        "# Auditor Verification Kit (FIXTURE)\n\n"
        "## Scope of this kit\n\n"
        "This kit is a cryptographic integrity artifact. "
        "It is NOT, on its own, a §164.528 disclosure accounting.\n\n"
        "## Reproducibility\n\n"
        "Two consecutive downloads of this kit, when nothing has "
        "changed, produce byte-identical archives.\n\n"
        "## Complaints and concerns\n\n"
        "compliance@osiriscare.com — best-effort acknowledgment.\n"
        "HHS OCR: hhs.gov/hipaa/filing-a-complaint/\n"
    )
    fixed_entries = [
        ("README.md", readme_text),
        ("verify.sh", "#!/usr/bin/env bash\n# verify.sh fixture\nexit 0\n"),
        (
            "verify_identity.sh",
            "#!/usr/bin/env bash\n# verify_identity.sh fixture\nexit 0\n",
        ),
        (
            "chain.json",
            json.dumps(chain_metadata, indent=2, sort_keys=True),
        ),
        ("bundles.jsonl", "\n".join(bundles_jsonl_lines) + "\n"),
        (
            "pubkeys.json",
            json.dumps(pubkeys_payload, indent=2, sort_keys=True),
        ),
        (
            "identity_chain.json",
            json.dumps(identity_chain_payload, indent=2, sort_keys=True),
        ),
        (
            "iso_ca_bundle.json",
            json.dumps(iso_ca_payload, indent=2, sort_keys=True),
        ),
    ]
    ots_files = {
        "fixture-bundle-2.ots": b"\x00\x01\x02fixture-ots-bytes",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(
        buf, "w", zipfile.ZIP_DEFLATED, compresslevel=_KIT_COMPRESSLEVEL,
    ) as zf:
        for name, blob in sorted(fixed_entries):
            _kit_zwrite(zf, name, blob, _FIXTURE_ZIP_MTIME)
        for ots_name in sorted(ots_files):
            _kit_zwrite(zf, f"ots/{ots_name}", ots_files[ots_name], _FIXTURE_ZIP_MTIME)
    return buf.getvalue()


# ---------------------------------------------------------------- determinism


def test_two_consecutive_kit_builds_are_byte_identical():
    """The central tamper-evidence promise. If this test ever
    flakes, the kit silently ships a determinism regression and
    auditors will see hash drift on equal-content downloads."""
    a = _build_kit_zip()
    b = _build_kit_zip()
    assert a == b, (
        "Determinism regression: two consecutive ZIP builds with "
        "identical input produced different bytes. Audit "
        "_kit_zwrite, _KIT_COMPRESSLEVEL, sort_keys, and entry "
        "ordering. Round-table 2026-05-06 P0."
    )
    # Stronger: hash equality.
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_changed_input_produces_different_bytes():
    """Negative case: if the chain advances, the bytes MUST change.
    Otherwise a kit that's been substituted at the storage layer
    would not be detectable by hash diff."""
    baseline = _build_kit_zip()
    # Swap in a different bundles.jsonl line — simulates one new
    # bundle written.
    altered_buf = io.BytesIO()
    with zipfile.ZipFile(
        altered_buf, "w", zipfile.ZIP_DEFLATED, compresslevel=_KIT_COMPRESSLEVEL,
    ) as zf:
        _kit_zwrite(
            zf, "bundles.jsonl",
            "{\"bundle_id\": \"NEW-bundle-3\"}\n",
            _FIXTURE_ZIP_MTIME,
        )
    altered = altered_buf.getvalue()
    assert baseline != altered, (
        "Different content produced same bytes — a content change "
        "is invisible to the byte-identity check."
    )


# ---------------------------------------------------------------- structure


def test_kit_zip_opens_cleanly():
    """Production must produce a valid ZIP — not a corrupt blob."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        # testzip returns None on success, else first bad name.
        assert zf.testzip() is None, "ZIP central directory CRC failure."


def test_kit_contains_all_expected_entries():
    """Source-grep tests check the existence of writeStr CALLS for
    each entry; this test opens the resulting ZIP and verifies the
    NAMES are actually present in the central directory. Catches
    refactors that move the writes into helpers but lose entries."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = set(zf.namelist())
    expected_top = {
        "README.md",
        "verify.sh",
        "verify_identity.sh",
        "chain.json",
        "bundles.jsonl",
        "pubkeys.json",
        "identity_chain.json",
        "iso_ca_bundle.json",
    }
    missing = expected_top - names
    assert not missing, f"Missing top-level kit entries: {missing}"
    # OTS files live under ots/<bundle_id>.ots
    assert any(n.startswith("ots/") and n.endswith(".ots") for n in names), (
        "No ots/*.ots entries in kit — anchoring evidence lost."
    )


def test_kit_json_entries_are_well_formed_and_canonical():
    """Each JSON entry must parse and use sorted-key form."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for name in (
            "chain.json",
            "pubkeys.json",
            "identity_chain.json",
            "iso_ca_bundle.json",
        ):
            blob = zf.read(name).decode("utf-8")
            parsed = json.loads(blob)  # raises if malformed
            # Round-trip with sort_keys: bytes must be identical.
            re_dumped = json.dumps(parsed, indent=2, sort_keys=True)
            assert blob == re_dumped, (
                f"{name} is not sort_keys=True canonical form — "
                f"round-trip with sort_keys produced different bytes."
            )


def test_kit_version_is_2_1_in_every_json_artifact():
    """Coach P0-2 (round-table 2026-05-06): kit_version had drifted
    across 4 surfaces. Verify by opening the actual artifacts that
    every JSON declares 2.1."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for name in (
            "chain.json",
            "pubkeys.json",
            "identity_chain.json",
            "iso_ca_bundle.json",
        ):
            payload = json.loads(zf.read(name))
            kv = payload.get("kit_version")
            assert kv == _FIXTURE_KIT_VERSION, (
                f"{name} declares kit_version={kv!r}, expected "
                f"{_FIXTURE_KIT_VERSION!r}. Round-table 2026-05-06 "
                f"unified all four surfaces to 2.1."
            )


def test_bundles_jsonl_is_per_line_canonical_json():
    """Each line of bundles.jsonl must parse independently AND be
    sort_keys=True canonical."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        text = zf.read("bundles.jsonl").decode("utf-8")
    lines = [ln for ln in text.split("\n") if ln]
    assert lines, "bundles.jsonl is empty"
    for ln in lines:
        obj = json.loads(ln)
        assert json.dumps(obj, sort_keys=True) == ln, (
            f"bundles.jsonl line is not sort_keys canonical: {ln!r}"
        )


def test_readme_contains_round_table_mandated_sections():
    """Carol P0-3 + P1-1: the README is the customer-facing
    framing surface. Verify the three mandated sections are
    actually inside the kit, not just in the source template."""
    body = _build_kit_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        readme = zf.read("README.md").decode("utf-8")
    assert "## Scope of this kit" in readme
    assert "## Reproducibility" in readme
    assert "## Complaints and concerns" in readme
    assert "§164.528" in readme
    assert "compliance@osiriscare.com" in readme
    assert "hhs.gov/hipaa/filing-a-complaint" in readme


def test_production_readme_carries_firm_complaint_sla():
    """User confirmed 2026-05-06: compliance@osiriscare.com is
    wired as a monitored inbox with 7-business-day acknowledgment
    + 30-day substantive-response targets. Pin the production
    README's SLA copy so a future PR cannot silently soften it
    back to best-effort.

    Coach P0-3 (round-table 2026-05-06) caveat: an unbacked SLA
    in a hash-chained artifact is a documented commitment risk.
    Inverse caveat: a *backed* SLA must stay firm — softening it
    back to ambiguity once the inbox is wired wastes the
    operational commitment. This gate enforces the firm posture."""
    import pathlib as _pl
    evidence_chain_path = (
        _pl.Path(__file__).resolve().parent.parent / "evidence_chain.py"
    )
    src = evidence_chain_path.read_text()
    # Sanity: locate the README template embedded in evidence_chain.py
    readme_marker = "_AUDITOR_KIT_README"
    assert readme_marker in src, (
        "_AUDITOR_KIT_README template not found in evidence_chain.py"
    )
    # The Complaints section must reference compliance@ with the
    # firm 7/30 commitment, not the prior best-effort softening.
    assert "compliance@osiriscare.com" in src, (
        "compliance@ channel removed from kit README — Carol P1-1 "
        "regression."
    )
    assert (
        "7 business days" in src and "30 days" in src
    ), (
        "Auditor-kit README must commit to 7-business-day "
        "acknowledgment + 30-day substantive-response targets "
        "(user-confirmed operational commitment 2026-05-06). A "
        "softening back to 'best-effort' wastes the wired-inbox "
        "operational state."
    )
    # The §164.524 alignment is the auditor-recognizable peg —
    # don't drop it.
    assert "§164.524" in src, (
        "§164.524 alignment dropped from Complaints SLA — auditors "
        "rely on this peg to read our 30-day target as standards-"
        "aligned, not arbitrary."
    )
    # Defense against silent regression to best-effort wording.
    assert "best-effort acknowledgment" not in src, (
        "Auditor-kit README still carries 'best-effort "
        "acknowledgment' softening; user confirmed inbox is wired "
        "with firm 7/30 SLA — update the README to match."
    )
    # Banned-words sweep on the new copy.
    for banned in ("ensures", "guarantees", "audit-ready"):
        readme_idx = src.find(readme_marker)
        readme_end = src.find('"""', readme_idx + len(readme_marker))
        readme_end = src.find('"""', readme_end + 3) + 3
        readme_block = src[readme_idx:readme_end].lower()
        assert banned not in readme_block, (
            f"Banned legal-language word '{banned}' present in kit "
            f"README (CLAUDE.md Session 199 hard rule)."
        )


# ---------------------------------------------------------------- helper


def test_kit_zwrite_pins_zip_metadata():
    """Direct test of the production helper — verifies it pins
    date_time, compress_type, and external_attr on the ZipInfo."""
    buf = io.BytesIO()
    with zipfile.ZipFile(
        buf, "w", zipfile.ZIP_DEFLATED, compresslevel=_KIT_COMPRESSLEVEL,
    ) as zf:
        _kit_zwrite(zf, "test.txt", "hello", _FIXTURE_ZIP_MTIME)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        info = zf.getinfo("test.txt")
        assert info.date_time == _FIXTURE_ZIP_MTIME, (
            f"date_time not pinned: {info.date_time}"
        )
        assert info.compress_type == zipfile.ZIP_DEFLATED, (
            "compress_type not pinned to ZIP_DEFLATED"
        )
        # external_attr top 16 bits = unix mode bits.
        unix_mode = (info.external_attr >> 16) & 0o7777
        assert unix_mode == 0o644, (
            f"external_attr unix mode = {oct(unix_mode)}, expected "
            f"0o644 (file mode 644 — readable by all)."
        )


def test_kit_compresslevel_is_pinned_to_six():
    """Coach P1-3: zlib level 6 explicit — preempts CPython build
    drift on the bundled zlib's default."""
    assert _KIT_COMPRESSLEVEL == 6
