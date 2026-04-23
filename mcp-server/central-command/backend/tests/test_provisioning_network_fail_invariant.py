"""FIX-14 + FIX-15 (v40, 2026-04-23): provisioning_network_fail invariant
+ install_sessions.first_outbound_success_at column + /api/install/report/
net-ready endpoint.

These tests guard against three silent-drift regressions that would turn
the v40 24h soak gate back into an after-the-fact signal:

  1. Removing the invariant from assertions.ALL_ASSERTIONS or
     _DISPLAY_METADATA (three-list lockstep rule).
  2. Removing or renaming the Pydantic model / endpoint that populates
     the first_outbound_success_at timestamp.
  3. Dropping or renaming the migration 239 column.

Pure source-level + import checks — no DB, no network. Runs in CI on
every PR.
"""
from __future__ import annotations

import pathlib
import sys

# tests/ → backend/ → central-command/ → mcp-server/ → <repo-root>
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from assertions import ALL_ASSERTIONS, _DISPLAY_METADATA  # noqa: E402


INVARIANT_NAME = "provisioning_network_fail"


def test_invariant_is_registered():
    """FIX-14 invariant must appear in assertions.ALL_ASSERTIONS. Missing
    here means the Substrate Integrity Engine never runs the check, and
    the v40 install-gate failure mode becomes silent again."""
    names = {a.name for a in ALL_ASSERTIONS}
    assert INVARIANT_NAME in names, (
        f"FIX-14 regression: `{INVARIANT_NAME}` is not in "
        "assertions.ALL_ASSERTIONS. This is the outcome-layer signal "
        "for the v40 FIX-9/10/11 ISO hardening — without it, the 24h "
        "soak has no live visibility and an install-gate failure at a "
        "customer site is silent until an on-call grep. Re-add per "
        ".agent/plans/v40-complete-iso.md §FIX-14."
    )


def test_invariant_severity_is_sev2():
    """Sev2 is the right band: customer-visible issue that is NOT an
    immediate platform-wide outage. Sev1 would page the on-call for
    every individual slow DNS-filter whitelist approval."""
    target = next(
        (a for a in ALL_ASSERTIONS if a.name == INVARIANT_NAME), None
    )
    assert target is not None, "invariant not registered (see prior test)"
    assert target.severity == "sev2", (
        f"FIX-14 regression: `{INVARIANT_NAME}` severity is "
        f"{target.severity!r}; plan specified sev2. sev1 over-pages "
        "for every customer-network whitelist delay; sev3 under-pages "
        "and the signal gets buried on the dashboard."
    )


def test_invariant_has_display_metadata():
    """Three-list lockstep rule: every invariant must also have an entry
    in _DISPLAY_METADATA so the /admin/substrate-health panel renders
    the operator-facing human text. Missing metadata logs a warning at
    module load — tolerable but defeats the whole point of the v36
    operator-facing redesign."""
    assert INVARIANT_NAME in _DISPLAY_METADATA, (
        f"FIX-14 regression: `{INVARIANT_NAME}` is missing from "
        "assertions._DISPLAY_METADATA. The dashboard will render the "
        "raw engineering name + description, which is what the v36 "
        "round-table explicitly rejected. Add display_name + "
        "recommended_action per .agent/plans/v40-complete-iso.md §FIX-14."
    )
    meta = _DISPLAY_METADATA[INVARIANT_NAME]
    assert meta.get("display_name"), "display_name must be non-empty"
    assert meta.get("recommended_action"), "recommended_action must be non-empty"
    # Operator-facing text MUST name the beacon URL — it's the action
    # that closes the loop without needing shell access to the appliance.
    assert ":8443" in meta["recommended_action"], (
        "recommended_action must reference the LAN beacon at :8443 — "
        "that's the actionable surface the v40 FIX-11 gate populates. "
        "Without it, the operator has no way to tell which of DNS / "
        "TCP / TLS / HEALTH is the broken stage."
    )


def test_runbook_stub_exists_and_cites_v40():
    """Substrate docs gate (test_substrate_docs_present.py) already
    enforces the required sections. This test adds a tighter check:
    the runbook body MUST reference v40 FIX-9/10/11 so an operator
    reading it understands which release fixes this class of failure
    at the ISO layer."""
    runbook = _BACKEND / "substrate_runbooks" / f"{INVARIANT_NAME}.md"
    assert runbook.exists(), f"FIX-14 regression: {runbook} missing"
    body = runbook.read_text()
    assert "FIX-9" in body or "FIX-11" in body, (
        f"FIX-14 regression: {runbook} does not cite the v40 ISO-side "
        "fixes (FIX-9 firewall determinism / FIX-11 4-stage gate). "
        "Without that cross-reference the operator reading the runbook "
        "has no idea the underlying fix already shipped — they'd think "
        "they're looking at an unsolved problem."
    )


def test_net_ready_endpoint_is_declared():
    """FIX-15 endpoint must be declared in install_reports.py. Without
    it, first_outbound_success_at is never populated and the invariant
    fires forever on any box that would otherwise be green."""
    install_reports_src = (_BACKEND / "install_reports.py").read_text()
    assert '"/report/net-ready"' in install_reports_src, (
        "FIX-15 regression: /report/net-ready endpoint missing from "
        "install_reports.py. The installed-system msp-auto-provision "
        "service posts here after the 4-stage gate passes — without "
        "it, install_sessions.first_outbound_success_at stays NULL "
        "forever and provisioning_network_fail fires for every "
        "otherwise-healthy appliance."
    )
    assert "InstallReportNetReady" in install_reports_src, (
        "FIX-15 regression: InstallReportNetReady Pydantic model "
        "missing. The endpoint's request schema defines the wire "
        "contract with the ISO-side shell script — removing the model "
        "silently breaks the contract without breaking imports."
    )
    # Endpoint MUST reuse the X-Install-Token dependency — if the
    # dependency line disappears, the endpoint becomes unauthenticated.
    # Grep for the combination "report/net-ready" followed closely by
    # require_install_token.
    idx = install_reports_src.find('"/report/net-ready"')
    next_200 = install_reports_src[idx : idx + 200]
    assert "require_install_token" in next_200, (
        "FIX-15 regression: /report/net-ready endpoint is missing the "
        "require_install_token dependency. Without it the endpoint "
        "accepts anonymous POSTs, which would let any internet scanner "
        "mark arbitrary MACs as 'network ready' and suppress the "
        "provisioning_network_fail invariant."
    )


def test_migration_239_exists_and_adds_column():
    """FIX-15 migration is the source of truth for the column. If it's
    deleted (or replaced by a non-additive migration), the invariant
    short-circuits on the column-presence probe and becomes a no-op."""
    migrations_dir = _BACKEND / "migrations"
    # Find any 239_*.sql file — the name is permitted to drift but the
    # number is stable (enforced by migrate.py ordering).
    candidates = list(migrations_dir.glob("239_*.sql"))
    assert candidates, (
        "FIX-15 regression: no migration 239_*.sql found. The "
        "first_outbound_success_at column is defined by migration 239 "
        "and used by /api/install/report/net-ready. Without it the "
        "endpoint's UPDATE fails, the invariant short-circuits to "
        "empty, and the whole FIX-14+15 chain is silently dead."
    )
    body = candidates[0].read_text()
    assert "first_outbound_success_at" in body, (
        f"FIX-15 regression: migration {candidates[0].name} does not "
        "add the first_outbound_success_at column. If it was moved, "
        "re-add the ALTER TABLE ADD COLUMN here or in a later "
        "additive migration and update the grep."
    )
    assert "TIMESTAMPTZ" in body, (
        "FIX-15 regression: first_outbound_success_at must be "
        "TIMESTAMPTZ — the application code and /substrate-installation-sla "
        "endpoint assume timezone-aware timestamps."
    )


def test_invariant_check_respects_column_absence():
    """Defense-in-depth: the check function itself must short-circuit
    if the column doesn't exist (e.g., migration auto-apply failed).
    Hard requirement because a NameError or UndefinedColumn would take
    down the entire substrate-assertions loop — one bad invariant
    would mask every other signal."""
    src = (_BACKEND / "assertions.py").read_text()
    fn_start = src.find("async def _check_provisioning_network_fail(")
    assert fn_start > 0, "check function moved or deleted"
    # Probe a wide window — the function has a substantial docstring
    # and commentary before the guard. 5000 chars is more than the
    # full function body as of this writing.
    fn_body = src[fn_start : fn_start + 5000]
    assert "information_schema.columns" in fn_body, (
        "FIX-14 regression: _check_provisioning_network_fail lost its "
        "information_schema column-presence guard. Without it, a "
        "partial deploy (code shipped before migration 239 applied) "
        "crashes the whole substrate-assertions loop with "
        "UndefinedColumn, masking every OTHER invariant until the "
        "mismatch is fixed. Keep the col_exists probe."
    )


def test_installation_sla_reads_first_outbound_success_at():
    """FIX-15 follow-up (2026-04-23 ultrathink audit): the new column
    is useless unless SOMETHING reads it. /admin/substrate-installation-sla
    is the canonical read path — it powers the Provisioning Latency
    SLA card on /admin/substrate-health. Without this, we would be
    shipping a write-only column: the invariant covers the failure
    mode but operators cannot see the healthy distribution move
    earlier (net_up << auth_up), which is half the signal."""
    routes_src = (_BACKEND / "routes.py").read_text()
    sla_idx = routes_src.find('"/admin/substrate-installation-sla"')
    assert sla_idx > 0, "SLA endpoint missing from routes.py"
    # The endpoint body is ~6KB. Probe a generous window from the
    # decorator onward.
    sla_body = routes_src[sla_idx : sla_idx + 8000]
    assert "first_outbound_success_at" in sla_body, (
        "FIX-15 system-wide gap: /admin/substrate-installation-sla "
        "does not reference first_outbound_success_at. The column is "
        "write-only (POST /api/install/report/net-ready writes it, no "
        "read path surfaces it). Patch get_installation_sla() to join "
        "in the column and return both net_up and auth_up distributions."
    )
    assert '"net_up"' in sla_body, (
        "FIX-15 system-wide gap: endpoint must return a net_up stats "
        "block so the dashboard can render the earlier (network-up) "
        "latency alongside the existing (auth-up) latency. See "
        ".agent/plans/v40-complete-iso.md §FIX-15 SLA follow-up."
    )
    assert '"auth_up"' in sla_body, (
        "FIX-15 system-wide gap: endpoint lost the auth_up stats block. "
        "The legacy flat fields alone are ambiguous — the dashboard "
        "distinguishes net_up vs auth_up tiles in the v40 follow-up."
    )
    # Column-absence guard also required on the READ path — same
    # defense-in-depth reasoning as the invariant itself.
    assert "information_schema.columns" in sla_body, (
        "FIX-15 regression: SLA endpoint lost its information_schema "
        "column-presence guard. A 500 on this endpoint hides every "
        "other SLA stat on /admin/substrate-health for 60s at a time."
    )
