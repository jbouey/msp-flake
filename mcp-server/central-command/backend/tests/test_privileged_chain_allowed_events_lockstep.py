"""TIER-1 lockstep guard: ALLOWED_EVENTS must match the expected set.

#79 closure 2026-05-02. The same assertion lived in
test_privileged_access_attestation_pg.py — but the `_pg` suffix put it
in TIER-3 (DB-gated, CI-only) by the pre-push parity rule. CI 25255876254
caught the drift on commit 12b3f1b4 (#72) but local pre-push didn't,
forcing a second round-trip to fix the lockstep test.

This assertion is pure set comparison — no DB, no asyncpg, no pynacl.
Promoted out of _pg.py so pre-push catches the same class of drift
before push.

Three lists must stay in lockstep:
  1. fleet_cli.PRIVILEGED_ORDER_TYPES   (privileged fleet orders)
  2. attestation.ALLOWED_EVENTS         (Ed25519 attestation events)
  3. migration v_privileged_types       (DB-enforced trigger gate)

Admin-only events (no fleet_order counterpart) are in #2 only — the
lockstep script (scripts/check_privileged_chain_lockstep.py) only
enforces #1 ⊆ #2 and #3 ⊆ #2, so admin-only events in #2 don't break
either side.
"""
from __future__ import annotations

import pathlib
import sys


_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def test_allowed_events_matches_expected_set():
    """Regression guard against silent drift in ALLOWED_EVENTS.

    Adding a privileged event REQUIRES updating, in lockstep:
      - fleet_cli.PRIVILEGED_ORDER_TYPES (if a fleet_order)
      - privileged_access_attestation.ALLOWED_EVENTS (always)
      - migration v_privileged_types (if a fleet_order)
      - this test's expected set (always)
      - lockstep CI script's docs (admin-only asymmetry note)

    Each addition is documented inline so future readers know whether
    a new event is fleet_order-class or admin-only-class.
    """
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    import privileged_access_attestation as paa  # noqa: E402

    expected = {
        # Session 205 — emergency-access toggles (CLI-issued fleet_orders)
        "enable_emergency_access",
        "disable_emergency_access",
        "signing_key_rotation",
        "bulk_remediation",
        # Session 207 Phase W0 — watchdog catalog (CLI-issued fleet_orders)
        "watchdog_restart_daemon",
        "watchdog_refetch_config",
        "watchdog_reset_pin_store",
        "watchdog_reset_api_key",
        "watchdog_redeploy_daemon",
        "watchdog_collect_diagnostics",
        # Session 207 Phase S — SSH-free escape hatch (CLI-issued fleet_order)
        "enable_recovery_shell_24h",
        # Session 207 R+S — break-glass passphrase retrieval
        # (admin API call, NOT a fleet_order — asymmetry permitted)
        "break_glass_passphrase_retrieval",
        # v36 round-table — appliance physical-move acknowledgment
        # (admin API call, NOT a fleet_order — asymmetry permitted)
        "appliance_relocation_acknowledged",
        # #74 closure 2026-05-02 — fleet-wide healing kill-switch
        # (admin API call, NOT a fleet_order — asymmetry permitted)
        "fleet_healing_global_pause",
        "fleet_healing_global_resume",
        # #72 closure 2026-05-02 — admin destructive billing actions
        # (admin API call, NOT a fleet_order — asymmetry permitted)
        "customer_subscription_cancel",
        "customer_subscription_refund",
        # Punch-list #8 closure 2026-05-04 — owner-transfer state machine
        # (client_owner_transfer.py endpoints, NOT fleet_orders —
        # admin-API class, asymmetry permitted). Six events: one per
        # state transition. Round-table 2026-05-04 5/5 APPROVE_DESIGN.
        "client_org_owner_transfer_initiated",
        "client_org_owner_transfer_acked",
        "client_org_owner_transfer_accepted",
        "client_org_owner_transfer_completed",
        "client_org_owner_transfer_canceled",
        "client_org_owner_transfer_expired",
        # Maya cross-cutting parity finding 2026-05-04 — partner-portal
        # consistency audit. User role changes (client + partner) are
        # privileged actions; both portals attest via Ed25519 chain.
        # NOT fleet_orders — admin-API class.
        "client_user_role_changed",
        "partner_user_created",
        # Round-table 2026-05-04 item B — partner-admin transfer state
        # machine (mig 274). Maya's simpler shape: 4 events vs the
        # client-side owner-transfer's 6. NOT a fleet_order.
        "partner_admin_transfer_initiated",
        "partner_admin_transfer_completed",
        "partner_admin_transfer_canceled",
        "partner_admin_transfer_expired",
        # Maya P1-1 closure 2026-05-04 — final-pass adversarial review.
        # Operator-alert hooks for these 4 admin events were claiming
        # "cryptographically attested" without writing an Ed25519
        # bundle. Promoted to full chain.
        "org_deprovisioned",
        "org_reprovisioned",
        "partner_api_key_regenerated",
        "partner_org_deleted",
        # Followup task #20 closure 2026-05-04 — per-org configurable
        # cooling-off / expiry on transfers (mig 275). Changes to
        # friction-level config are themselves privileged.
        "client_org_transfer_prefs_changed",
        "partner_transfer_prefs_changed",
        # Task #19 closure 2026-05-05 — MFA admin overrides (mig 276).
        # 3 sub-features × 2 portals = 6 events + 2 reversal events
        # for Steve mit B (24h reversible-link revoke). Total
        # ALLOWED_EVENTS: 43.
        "client_org_mfa_policy_changed",
        "partner_mfa_policy_changed",
        "client_user_mfa_reset",
        "partner_user_mfa_reset",
        "client_user_mfa_revoked",
        "partner_user_mfa_revoked",
        "client_user_mfa_revocation_reversed",
        "partner_user_mfa_revocation_reversed",
    }
    assert paa.ALLOWED_EVENTS == expected, (
        f"ALLOWED_EVENTS drifted.\n"
        f"  Got:      {sorted(paa.ALLOWED_EVENTS)}\n"
        f"  Expected: {sorted(expected)}\n"
        f"Missing from ALLOWED_EVENTS: {expected - paa.ALLOWED_EVENTS}\n"
        f"Extra in ALLOWED_EVENTS:     {paa.ALLOWED_EVENTS - expected}\n"
        f"Update fleet_cli + attestation + migration + this test + the "
        f"lockstep script together or the chain has a gap."
    )
