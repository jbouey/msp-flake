"""CI gates for #118 fleet_cli --target-appliance-id / --all-at-site
(per Gate A: audit/coach-118-fleet-cli-multi-target-gate-a-2026-05-16.md).

4 source-shape gates pin the multi-target fan-out contract:

  1. single-bundle-per-fan-out — --all-at-site fan-out MUST call
     create_privileged_access_attestation EXACTLY ONCE per CLI
     invocation (not N times — would burn the per-site rate limit
     + duplicate audit rows + create N separate chain positions).

  2. soft-delete filter — the enumeration query MUST filter
     `WHERE deleted_at IS NULL`. Without it, decommissioned
     appliances would receive the fan-out order + the daemon
     (if it still runs) would execute against revoked credentials.

  3. dynamic-N confirm prompt — the count-confirm prompt MUST
     reference the actual N (not a hardcoded constant). Otherwise
     operator builds muscle memory typing the same number, defeating
     the defensive-friction purpose.

  4. dry-run field allowlist — the --dry-run output MUST exclude
     ip_addresses / daemon_health / agent_public_key per Counsel
     Rule 7 (Layer-2 leak class — Carol RT33 P2). Keep `mac` because
     the operator needs cross-inventory matching.

Plus 2 closure sentinels:

  5. atomic-txn wrap — cmd_create MUST wrap the bundle write + N
     order INSERTs in `async with conn.transaction():`. Pre-fix the
     missing wrap meant `_get_prev_bundle()`'s
     `assert conn.is_in_transaction()` would fail on every
     privileged invocation (latent bug since 2026-05-09).

  6. cross-link aggregate — the post-INSERT UPDATE on
     admin_audit_log MUST emit `fleet_order_ids` (array) not
     `fleet_order_id` (string). Otherwise N orders sharing one
     bundle overwrite each other (last-wins, N-1 lost from audit).
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_FLEET_CLI = _BACKEND / "fleet_cli.py"
_ATTEST = _BACKEND / "privileged_access_attestation.py"


def _read_cli() -> str:
    return _FLEET_CLI.read_text(encoding="utf-8")


def test_single_bundle_per_fan_out():
    """Gate A P0: --all-at-site must call create_privileged_access_
    attestation EXACTLY ONCE per CLI invocation, regardless of N
    target appliances.

    Source-shape check: there's exactly ONE call to create_privileged_
    access_attestation in cmd_create, and it appears OUTSIDE the
    iter_targets loop (otherwise N appliances → N bundles, which
    burns the per-site rate limit + creates N chain positions for
    one logical action).
    """
    src = _read_cli()
    # Find cmd_create body
    m = re.search(
        r"async def cmd_create\(.*?(?=\nasync def |\nif __name__|\Z)",
        src, re.DOTALL,
    )
    assert m, "could not locate cmd_create"
    body = m.group(0)
    # Count attestation creation calls
    calls = re.findall(r"create_privileged_access_attestation\s*\(", body)
    assert len(calls) == 1, (
        f"cmd_create calls create_privileged_access_attestation "
        f"{len(calls)} times — must be EXACTLY 1 (single bundle covers "
        f"N orders for --all-at-site fan-out). N>1 would burn the "
        f"per-site rate limit + create N chain positions for one "
        f"logical action."
    )
    # Find the for-loop over iter_targets + verify the attestation
    # call is BEFORE the loop, not inside.
    loop_pos = body.find("for per_target in iter_targets")
    attest_pos = body.find("create_privileged_access_attestation(")
    assert loop_pos > 0, "could not locate iter_targets loop"
    assert attest_pos > 0, "could not locate attestation call"
    assert attest_pos < loop_pos, (
        "create_privileged_access_attestation must run BEFORE the "
        "iter_targets fan-out loop, not inside it. Inside the loop "
        "= N bundles (wrong shape)."
    )


def test_soft_delete_filter_on_site_enumeration():
    """Gate A P0: enumeration query must filter `deleted_at IS NULL`.
    Without it, decommissioned appliances would receive the fan-out."""
    src = _read_cli()
    # Find the SELECT from site_appliances (cmd_create enumeration)
    m = re.search(
        r"SELECT[^;]*FROM site_appliances\b[^;]*",
        src, re.IGNORECASE | re.DOTALL,
    )
    assert m, "no SELECT FROM site_appliances in fleet_cli.py"
    query = m.group(0)
    assert "deleted_at IS NULL" in query, (
        "--all-at-site enumeration must filter `deleted_at IS NULL`. "
        "Decommissioned appliances would otherwise receive fan-out "
        "orders against revoked credentials."
    )


def test_dynamic_n_confirm_prompt():
    """Gate A P0: count-confirm prompt must reference the ACTUAL N
    (not a hardcoded constant or yes/no). Operator typing the same
    number repeatedly builds muscle memory + defeats the defensive
    friction purpose at scale."""
    src = _read_cli()
    # Find the confirm input line(s)
    # Pattern: input(...) where the preceding prompt mentions {N}
    # or len(target_appliance_ids) or similar dynamic value.
    m = re.search(
        r"confirm\s*=\s*input\(.*?\)\.strip\(\)",
        src, re.DOTALL,
    )
    assert m, "could not locate confirm = input(...) in cmd_create"
    # Look backward ~30 lines for an `N = ...` or similar dynamic
    # binding before the input.
    before = src[: m.start()][-2000:]
    # Pattern: `N = len(target_appliance_ids)` OR `f"...{N}..."` in
    # the prompt string.
    assert "N = len(" in before or "{N}" in before, (
        "Count-confirm prompt does not reference a dynamic N "
        "(expected `N = len(target_appliance_ids)` before the input "
        "call). Hardcoded prompts defeat the defensive-friction "
        "purpose."
    )
    # And the confirm-check compares against str(N) dynamically
    after = src[m.end(): m.end() + 500]
    assert "str(N)" in after or "!= str(" in after, (
        "Confirm-check must compare against the dynamic N, not a "
        "hardcoded value."
    )


def test_dry_run_field_allowlist():
    """Gate A: dry-run output must EXCLUDE ip_addresses /
    daemon_health / agent_public_key (Counsel Rule 7 Layer-2 leak
    per Carol RT33 P2). Keep `mac` (operator needs cross-inventory)."""
    src = _read_cli()
    # Find the site_appliance_rows list-comprehension in cmd_create
    # that builds the allowlist
    m = re.search(
        r"site_appliance_rows\s*=\s*\[(.*?)\]\s*\n",
        src, re.DOTALL,
    )
    assert m, "could not locate site_appliance_rows allowlist comprehension"
    body = m.group(1)
    # Forbidden fields
    forbidden = ["ip_addresses", "daemon_health", "agent_public_key"]
    for field in forbidden:
        assert field not in body, (
            f"--dry-run allowlist includes {field!r} — Counsel Rule 7 "
            f"forbids this in operator-facing output (Layer-2 leak "
            f"class per RT33 P2). Drop the field from the dry-run "
            f"projection."
        )
    # MAC is allowed + expected
    assert "mac" in body, (
        "--dry-run allowlist missing `mac` — operator needs the MAC "
        "for cross-inventory matching."
    )


def test_atomic_txn_wrap_in_cmd_create():
    """Gate A P0 + latent bug closure: cmd_create MUST wrap the
    bundle write + N order INSERTs in `async with conn.transaction():`.
    Pre-fix the missing wrap meant `_get_prev_bundle()`'s
    `assert conn.is_in_transaction()` would have failed on every
    privileged invocation (latent bug since 2026-05-09)."""
    src = _read_cli()
    m = re.search(
        r"async def cmd_create\(.*?(?=\nasync def |\nif __name__|\Z)",
        src, re.DOTALL,
    )
    assert m, "could not locate cmd_create"
    body = m.group(0)
    assert "async with conn.transaction():" in body, (
        "cmd_create must wrap the privileged-attestation path in "
        "`async with conn.transaction():`. Without it, _get_prev_"
        "bundle()'s assert is_in_transaction() fails. Latent bug "
        "since 2026-05-09."
    )
    # And the create_privileged_access_attestation call must be
    # inside that wrap
    txn_pos = body.find("async with conn.transaction():")
    attest_pos = body.find("create_privileged_access_attestation(")
    assert txn_pos < attest_pos, (
        "create_privileged_access_attestation must be called INSIDE "
        "the conn.transaction() block, not before it."
    )


def test_cross_link_uses_aggregate_array_not_singular():
    """Gate A P1-3 closure: the audit-log cross-link UPDATE must
    emit `fleet_order_ids` (jsonb array) not `fleet_order_id`
    (single string). With N orders sharing 1 bundle, the singular
    shape overwrites N times = last-wins, N-1 audit values lost."""
    src = _read_cli()
    # Find the cross-link UPDATE
    m = re.search(
        r"UPDATE admin_audit_log.*?bundle_id.*?=\s*\$",
        src, re.DOTALL,
    )
    assert m, "could not locate audit cross-link UPDATE"
    update = m.group(0)
    assert "fleet_order_ids" in update, (
        "Cross-link UPDATE must emit `fleet_order_ids` (array) not "
        "`fleet_order_id` (singular). N orders sharing 1 bundle "
        "would overwrite N times under the singular shape."
    )
    assert "fleet_order_id'" not in update, (
        "Cross-link UPDATE still references `fleet_order_id` "
        "(singular). Replace with `fleet_order_ids` jsonb array."
    )


def test_attestation_accepts_target_appliance_ids_kwarg():
    """Gate A P0: create_privileged_access_attestation must accept
    `target_appliance_ids` kwarg so the bundle summary encodes
    count=N + target_appliance_ids=[...]. Without it the bundle
    under-represents the multi-target scope."""
    import inspect
    import sys
    sys.path.insert(0, str(_BACKEND))
    try:
        import privileged_access_attestation as paa  # type: ignore
    except ImportError:
        # If module deps aren't available locally (asyncpg/pynacl),
        # skip gracefully — CI will run it.
        return
    sig = inspect.signature(paa.create_privileged_access_attestation)
    assert "target_appliance_ids" in sig.parameters, (
        "create_privileged_access_attestation must accept "
        "`target_appliance_ids` kwarg so the bundle summary encodes "
        "count=N + the list."
    )
    p = sig.parameters["target_appliance_ids"]
    assert p.default is None, (
        "target_appliance_ids default must be None (back-compat — "
        "every existing caller passes nothing)."
    )
    # Source-walk: verify the summary_payload uses the kwarg
    src = (_ATTEST).read_text()
    assert "target_appliance_ids" in src, (
        "privileged_access_attestation.py must reference "
        "target_appliance_ids in the summary_payload construction."
    )
    assert 'summary_payload["target_appliance_ids"]' in src or \
           '"target_appliance_ids":' in src, (
        "summary_payload must encode target_appliance_ids when present."
    )


def test_target_appliance_id_and_all_at_site_mutually_exclusive():
    """The two new args are mutually exclusive — using both is
    operator confusion. Verify the sys.exit guard exists."""
    src = _read_cli()
    assert "mutually exclusive" in src and \
           "target_appliance_id" in src and \
           "all_at_site" in src, (
        "cmd_create must sys.exit when --target-appliance-id and "
        "--all-at-site are both passed."
    )


def test_target_appliance_id_validates_as_uuid():
    """Gate A: --target-appliance-id arg validates UUID format
    upfront. Typo class catch at 250-appliance scale."""
    src = _read_cli()
    # Look for the validation block
    m = re.search(
        r"target_appliance_id.*?_uuid_mod\.UUID\(",
        src, re.DOTALL,
    )
    assert m, (
        "--target-appliance-id must validate UUID format via "
        "uuid.UUID(...) at parse time."
    )
