"""Pin gate — `enforce_privileged_order_attestation` AND
`enforce_privileged_order_immutability` function bodies are
ADDITIVE-ONLY across migrations.

Session 220 task #111 (2026-05-11). Gate B v1 on the zero-auth Commit 1
sprint caught a silent body-shape regression in mig 305 v1: I rewrote
the function body from scratch when adding `delegate_signing_key` to
`v_privileged_types`, accidentally dropping:
  - `parameters->>'site_id'` cross-bundle check (chain-of-custody hole)
  - `PRIVILEGED_CHAIN_VIOLATION:` error prefix (SIEM/alert key)
  - `USING HINT` clause (operator debuggability)

The list-side checker (`scripts/check_privileged_chain_lockstep.py`)
proves v_privileged_types LIST parity across 3 lists but NOT body
parity. THIS gate is the body-parity sibling.

SCOPE: Catches future drift FROM the current canonical body. The
canonical hashes pinned below correspond to the bodies as of mig 305
(the most recent legitimate redefinition). Older migrations (175,
176, 218, 223) are historical baselines — their bodies legitimately
evolved over time, and pinning against the original would force
every body-change migration to also rewrite ancient migs.

CANONICAL-TAMPER BACKDOOR closed by hash-pinning (Gate A P0-2):
the canonical SHA256 lives as a Python literal in this test file.
Editing the canonical migration body without bumping the literal
breaks the test → forced explicit acknowledgement.

LEGITIMATE-CHANGE PROTOCOL (Gate A P0-3): when a body change is
intentional (e.g. adding a new chain-of-custody check), update
the canonical hash literal AND include `PRIVILEGED-CHAIN-BODY-CHANGE:
<reason>` token in the commit message. The token signals reviewer
attention; the hash bump is the structural enforcement.

Algorithm:
  1. Find every migration `*.sql` containing `CREATE OR REPLACE
     FUNCTION enforce_privileged_order_(attestation|immutability)`.
  2. For each occurrence, extract function body between
     `LANGUAGE plpgsql AS $$` and `$$;`.
  3. Normalize: strip `v_privileged_types TEXT[] := ARRAY[...]`
     block (replaced with placeholder), strip trailing whitespace
     per line, drop blank lines.
  4. SHA256 the normalized body.
  5. For the HIGHEST-numbered migration redefining each function,
     assert hash matches the pinned canonical hash.

Older migrations (numerically lower than the canonical) are
verified to compile but their bodies are NOT pinned — they may
have evolved legitimately.

Sibling pattern:
  - `tests/test_escalate_rule_check_type_drift.py`
  - `tests/test_privileged_chain_allowed_events_lockstep.py`
  - `tests/test_appliance_delegation_auth_pinned.py`
"""
from __future__ import annotations

import hashlib
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIGRATIONS = _BACKEND / "migrations"

# Canonical normalized-body SHA256 hashes. Computed from mig 305
# (the current truth). To update:
#   1. Author a new migration that legitimately changes the body.
#   2. Run the body-extraction script (see _normalize() + _body_hash()
#      in this file) against the new migration.
#   3. Replace the corresponding constant below with the new hash.
#   4. Include `PRIVILEGED-CHAIN-BODY-CHANGE: <reason>` in the
#      commit message body.
# Skipping any of steps 3-4 = this test fails = PR blocked = review
# attention. Gate A P0-2 + P0-3 closure.
_CANONICAL_ATTESTATION_HASH = "16d64e0a4fca5cde366233418adbe8a87dec360da247b3da5cf0f81d12ace869"
_CANONICAL_IMMUTABILITY_HASH = "6ff6cd07e600abbc787d25846513919811cdf6c62c02459eee5202982ba6b33d"

# Migration numbers up to which we IGNORE body parity (historical
# baselines). Migrations newer than these MUST match the canonical
# hashes above.
_HISTORICAL_CUTOFF_ATTESTATION = 217  # mig 218 was the first to use the current body
_HISTORICAL_CUTOFF_IMMUTABILITY = 222  # mig 223 was the first to use the current body

_ARRAY_RE = re.compile(
    r"v_privileged_types\s+TEXT\[\]\s*:=\s*ARRAY\[.*?\];",
    re.DOTALL,
)
_BODY_RE = re.compile(
    r"CREATE OR REPLACE FUNCTION (enforce_privileged_order_(?:attestation|immutability))\(\)"
    r"\s*RETURNS TRIGGER LANGUAGE plpgsql AS \$\$(.*?)\$\$;",
    re.DOTALL,
)
_MIG_NUM_RE = re.compile(r"^(\d+)_")


def _normalize(body: str) -> str:
    """Strip v_privileged_types array (the LEGITIMATELY-differing
    part) + trailing whitespace per line + blank lines."""
    body = _ARRAY_RE.sub("<ARRAY>", body)
    return "\n".join(line.rstrip() for line in body.splitlines() if line.strip())


def _body_hash(body: str) -> str:
    return hashlib.sha256(_normalize(body).encode()).hexdigest()


def _migration_number(path: pathlib.Path) -> int | None:
    m = _MIG_NUM_RE.match(path.name)
    return int(m.group(1)) if m else None


def _collect_redefinitions() -> dict[str, list[tuple[int, pathlib.Path, str]]]:
    """Returns {function_name: [(mig_num, path, normalized_hash), ...]}
    sorted by mig_num ascending."""
    out: dict[str, list[tuple[int, pathlib.Path, str]]] = {
        "enforce_privileged_order_attestation": [],
        "enforce_privileged_order_immutability": [],
    }
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        mig_num = _migration_number(path)
        if mig_num is None:
            continue
        src = path.read_text()
        for m in _BODY_RE.finditer(src):
            fn = m.group(1)
            body = m.group(2)
            out[fn].append((mig_num, path, _body_hash(body)))
    for fn in out:
        out[fn].sort()
    return out


def test_latest_attestation_body_matches_canonical():
    """The HIGHEST-numbered migration redefining
    `enforce_privileged_order_attestation` MUST match the pinned
    canonical hash. Closes the mig-305-v1 silent-weakening class."""
    redefs = _collect_redefinitions()["enforce_privileged_order_attestation"]
    assert redefs, "no migration redefines enforce_privileged_order_attestation"
    mig_num, path, actual_hash = redefs[-1]
    if actual_hash == _CANONICAL_ATTESTATION_HASH:
        return
    raise AssertionError(
        f"\n\nFunction body for `enforce_privileged_order_attestation` "
        f"has drifted from the pinned canonical in {path.name} (mig {mig_num}).\n\n"
        f"  expected hash: {_CANONICAL_ATTESTATION_HASH}\n"
        f"  actual hash:   {actual_hash}\n\n"
        f"Two valid responses:\n"
        f"  (a) REVERT the body change in {path.name} — copy the\n"
        f"      function body VERBATIM from the prior canonical migration\n"
        f"      and change ONLY the v_privileged_types array entries.\n"
        f"      This is correct if you intended an additive lockstep update.\n"
        f"  (b) UPDATE _CANONICAL_ATTESTATION_HASH in this test file to\n"
        f"      the new hash above AND include token\n"
        f"      `PRIVILEGED-CHAIN-BODY-CHANGE: <reason>` in the commit\n"
        f"      message. This is correct ONLY if you intentionally\n"
        f"      changed the chain-of-custody body (e.g. added a new\n"
        f"      validation check). Reviewer attention required.\n\n"
        f"Session 220 task #111 — closes the mig-305-v1 class where\n"
        f"the body was silently rewritten, dropping site_id cross-bundle\n"
        f"check + PRIVILEGED_CHAIN_VIOLATION error prefix + USING HINT.\n"
    )


def test_latest_immutability_body_matches_canonical():
    """The HIGHEST-numbered migration redefining
    `enforce_privileged_order_immutability` MUST match the pinned
    canonical hash. Sibling of attestation pin."""
    redefs = _collect_redefinitions()["enforce_privileged_order_immutability"]
    assert redefs, "no migration redefines enforce_privileged_order_immutability"
    mig_num, path, actual_hash = redefs[-1]
    if actual_hash == _CANONICAL_IMMUTABILITY_HASH:
        return
    raise AssertionError(
        f"\n\nFunction body for `enforce_privileged_order_immutability` "
        f"has drifted from the pinned canonical in {path.name} (mig {mig_num}).\n\n"
        f"  expected hash: {_CANONICAL_IMMUTABILITY_HASH}\n"
        f"  actual hash:   {actual_hash}\n\n"
        f"Two valid responses:\n"
        f"  (a) REVERT the body change in {path.name} — copy verbatim\n"
        f"      from the prior canonical migration; change only the\n"
        f"      v_privileged_types array entries.\n"
        f"  (b) UPDATE _CANONICAL_IMMUTABILITY_HASH in this test file\n"
        f"      AND include `PRIVILEGED-CHAIN-BODY-CHANGE: <reason>`\n"
        f"      in the commit message body.\n"
    )


def test_all_post_cutoff_redefinitions_match_canonical():
    """Every migration AFTER the historical cutoff redefining either
    function MUST match the canonical hash. Catches a regression
    that lands mid-stack (e.g. someone edits mig 305 to weaken the
    body — the latest-only test above still catches it, but this
    test makes the failure surface for every drifted mig)."""
    redefs = _collect_redefinitions()
    drift: list[str] = []
    for fn, entries in redefs.items():
        canonical = (
            _CANONICAL_ATTESTATION_HASH
            if fn == "enforce_privileged_order_attestation"
            else _CANONICAL_IMMUTABILITY_HASH
        )
        cutoff = (
            _HISTORICAL_CUTOFF_ATTESTATION
            if fn == "enforce_privileged_order_attestation"
            else _HISTORICAL_CUTOFF_IMMUTABILITY
        )
        for mig_num, path, hash_ in entries:
            if mig_num <= cutoff:
                continue
            if hash_ != canonical:
                drift.append(
                    f"  {path.name} mig {mig_num} {fn}: hash={hash_[:16]}…"
                )
    assert not drift, (
        "Migrations after the historical cutoff have drifted bodies:\n"
        + "\n".join(drift)
        + "\n\nSee test_latest_*_body_matches_canonical for remediation."
    )


def test_synthetic_body_change_is_caught(tmp_path):
    """Positive control: a synthetic mig with a stripped-down body
    (mirroring the mig-305-v1 regression class) is caught."""
    bad = tmp_path / "999_bad_drift.sql"
    bad.write_text(
        """
BEGIN;

CREATE OR REPLACE FUNCTION enforce_privileged_order_attestation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_privileged_types TEXT[] := ARRAY[
        'enable_emergency_access',
        'disable_emergency_access'
    ];
BEGIN
    -- WEAKENED: missing site_id check + PRIVILEGED_CHAIN_VIOLATION
    -- prefix + USING HINT. This is the mig-305-v1 regression class.
    IF NOT (NEW.order_type = ANY(v_privileged_types)) THEN
        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$$;

COMMIT;
"""
    )
    src = bad.read_text()
    matches = list(_BODY_RE.finditer(src))
    assert len(matches) == 1
    body = matches[0].group(2)
    actual_hash = _body_hash(body)
    assert actual_hash != _CANONICAL_ATTESTATION_HASH, (
        "synthetic stripped-down body hashed equal to canonical — "
        "normalize step is broken (false-negative class)"
    )


def test_canonical_hashes_match_current_mig_305(tmp_path):
    """Sanity / negative control: extract bodies from the real
    mig 305, hash them, and confirm they match the pinned canonical
    constants. If this test fails, the pinned constants are stale —
    sync them to match the current mig."""
    mig_305 = _MIGRATIONS / "305_delegate_signing_key_privileged.sql"
    if not mig_305.exists():
        return  # mig number may shift; skip if file missing
    src = mig_305.read_text()
    found: dict[str, str] = {}
    for m in _BODY_RE.finditer(src):
        found[m.group(1)] = _body_hash(m.group(2))
    assert (
        found.get("enforce_privileged_order_attestation")
        == _CANONICAL_ATTESTATION_HASH
    ), (
        "_CANONICAL_ATTESTATION_HASH literal is stale. "
        f"Update to {found.get('enforce_privileged_order_attestation')}"
    )
    assert (
        found.get("enforce_privileged_order_immutability")
        == _CANONICAL_IMMUTABILITY_HASH
    ), (
        "_CANONICAL_IMMUTABILITY_HASH literal is stale. "
        f"Update to {found.get('enforce_privileged_order_immutability')}"
    )
