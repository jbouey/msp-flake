# Round-Table: MFA Admin Overrides (Task #19)

**Date:** 2026-05-05
**Format:** PM-led adversarial (Camila + Brian + Linda + Steve + Adam) + Maya 2nd-eye
**Status:** DESIGN APPROVED — ready for implementation
**Scope:** ~4-6h post-design; 3 sub-features × 2 portals = 6 endpoints + lockstep + frontend (separate task)

---

## Problem statement

Three MFA-administrative gaps surfaced by Fork A in the partner-portal audit:

1. **Toggle org-level mfa_required** — currently set at provision-time only via `org_management.provision`. No endpoint to change post-provision. If a practice decides to enforce MFA mid-engagement, they have no path.
2. **Force re-enroll a specific user's MFA** — lost device, suspected token compromise. Today: clear `mfa_secret` directly in DB.
3. **Revoke a specific user's MFA** (different from re-enroll) — when MFA itself is the attack vector (sim swap class). Today: no path.

All three needed for both portals (client_users + partner_users).

## Camila — PM lead

**Customer + ops framing.** MFA admin overrides are the operator's incident-response toolkit when MFA goes wrong. Today they don't exist as endpoints — every recovery requires DB surgery, which is operator-class work that bypasses the privileged-access chain.

**Priority order from impact perspective:**
1. **Toggle mfa_required** — most common; practices grow into MFA on a deliberate schedule.
2. **Force re-enroll** — lost-device class is bounded but real; happens monthly on a 50-person practice.
3. **Revoke** — rare but high-stakes; sim-swap incident response.

Ship all three together since they share infrastructure (auth + Ed25519 + audit + alert), but design defaults differ per Maya.

## Brian — Principal SWE

**Schema-side (no migration needed):**
- `client_orgs.mfa_required` (mig 029) + `partners.mfa_required` (mig 227) already exist. Toggle = `UPDATE … SET mfa_required = $1`.
- `client_users.mfa_enabled` + `client_users.mfa_secret` (mig 071) + same on `partner_users` (partner_auth.py setup path) already exist. Force re-enroll = `UPDATE … SET mfa_secret = NULL, mfa_enabled = false` (user must re-enroll on next login). Revoke = same UPDATE + a follow-up reset-confirmation email to the user with a 24h reversible link.

**Endpoint surface (6 total):**

```
PUT  /api/client/org/mfa-policy            (owner only, body: required: bool)
POST /api/client/users/{uid}/mfa-reset     (owner+admin, body: reason)
POST /api/client/users/{uid}/mfa-revoke    (owner only, body: reason — higher friction)

PUT  /api/partners/me/mfa-policy           (admin only)
POST /api/partners/{pid}/users/{uid}/mfa-reset    (admin)
POST /api/partners/{pid}/users/{uid}/mfa-revoke   (admin only — same friction as client owner)
```

**Each endpoint:** reason ≥20ch + Ed25519 attestation + audit row + operator alert. Same friction model as the rest of the privileged chain.

**Anchor namespace:**
- Client events anchor at org's primary site_id (with `client_org:<id>` fallback) — matches `client_user_role_changed` precedent.
- Partner events anchor at `partner_org:<id>` synthetic — matches existing partner-namespace.

**Implementation effort:** ~4-6h once design is approved (6 endpoints × ~30 min each + tests + lockstep + 2 new event_types per action × 3 actions = 6 new ALLOWED_EVENTS).

## Linda — DBA

**Audit semantics:**
- `mfa_policy_changed` — log prior + new value, target=org_id (or partner_id).
- `mfa_reset` — log target_user_id + reason, NO secrets in details.
- `mfa_revoke` — same as reset BUT additionally writes a 24h-reversible row to a NEW small table `mfa_revocation_pending` (target_user_id, revoked_at, reversal_token_hash, expires_at) so the user can self-restore via emailed link if the revocation was malicious.

**Migration 276 needs:**
- `mfa_revocation_pending` table (target_user_id UUID, revoked_at, expires_at, reversal_token_hash, restored_at, restored_by). Audit-class — DELETE blocked, UPDATE only via the restore endpoint.
- A 24h sweep loop marks expires_at-passed rows as expired (can't be restored anymore).

## Steve — Adversary

**Threat model — MFA admin overrides are themselves attack vectors:**

A. **Compromised owner toggles mfa_required to false.** Currently MFA is never re-required on existing sessions; new logins skip MFA. Mitigation: changing mfa_required to FALSE triggers a P1 operator alert + 24h grace window during which existing MFA-enrolled users still get challenged on next login (the change applies forward-only to NEW enrollments).

B. **Compromised admin revokes target's MFA, takes over their account.** This is the explicit attack the revoke endpoint creates. Mitigations:
   1. Revoked user gets an immediate email with a "Restore my MFA" link valid for 24h. They click, MFA is restored, the revoking actor is recorded in admin_audit_log.
   2. Operator alert P0-CHAIN-GAP-equivalent severity (but call it `P0-MFA-REVOKE` for distinction). On every revoke, ALERT_EMAIL gets pinged immediately.
   3. Revoke requires reason ≥40ch (vs ≥20ch elsewhere) — higher friction for higher-risk action.

C. **Force-reset abuse** — same attack but lower stakes (target re-enrolls, gets to choose new authenticator app). Mitigation: P1 operator alert + revoked user gets email "your MFA was reset by [admin email]; if this wasn't expected, contact [operator-fallback-email]". Reason ≥20ch.

D. **Race condition: A revokes B's MFA, then A initiates owner-transfer to attacker before B can restore.** Mitigation: Maya's prior owner-transfer state machine refuses initiate while ANY pending mfa_revocation exists for any in-org user. New CHECK at the initiate handler.

**Adversarial verdict:** SHIP with these defenses applied. Items A-D each get a test pin.

## Adam — Tech writer

**Email body language for the 3 customer-facing emails (post-revoke, post-reset, post-org-policy-change):**

- "Your multi-factor authentication has been revoked by [admin@org]. If this was not expected, click this link within 24 hours to restore. After restoration the substrate records a `mfa_revocation_reversed` attestation visible in your auditor kit."
- Same shape for reset, but no reversible link (resetting is recoverable on next login by re-enrolling).
- For org-policy-change: notify ALL users in the org "Your organization now requires MFA. You will be prompted to enroll on your next login."

Per CLAUDE.md Session 199: NO banned words. "monitors" / "supports audit-readiness through cryptographic chain" / OK to say "the substrate records this" / NOT OK to say "ensures recovery" or "guarantees the link works". The 24h reversible link is a recovery PATH, not a guarantee.

## Maya — Consistency coach (2nd-eye)

| # | Item | Maya verdict |
|---|---|---|
| 1 | Toggle mfa_required: PUT /api/client/org/mfa-policy + partner equivalent | **PARITY** — both portals get the toggle, same friction. |
| 2 | Force-reset: 2 endpoints, owner+admin auth client-side, admin auth partner-side | **PARITY** — operator-class flows for both. |
| 3 | Revoke: 2 endpoints, owner-only client-side, admin-only partner-side | **PARITY-with-asymmetry** — client demands owner role (highest privilege); partner demands admin (highest available role). Same friction-elevation logic. |
| 4 | 24h reversible link on revoke | **PARITY** — Steve's mitigation B applies symmetrically to both portals. |
| 5 | Reason ≥40ch on revoke (vs ≥20ch elsewhere) | **PARITY** — applies to BOTH revoke endpoints. |
| 6 | Mig 276 `mfa_revocation_pending` table | **PARITY** — schema cross-cuts. |
| 7 | Auto-add to PartnerUsersScreen / ClientUsersScreen frontend | **DEFER** — frontends are task #18 (multi-day calm-session work). |

**6 new ALLOWED_EVENTS:**
- `client_org_mfa_policy_changed`, `partner_mfa_policy_changed`
- `client_user_mfa_reset`, `partner_user_mfa_reset`
- `client_user_mfa_revoked`, `partner_user_mfa_revoked`

**Plus 2 follow-on events** (Steve's mitigation B):
- `client_user_mfa_revocation_reversed`, `partner_user_mfa_revocation_reversed`

Total ALLOWED_EVENTS bump: 35 → 43.

## Implementation checklist

1. **Migration 276:** `mfa_revocation_pending` table + DELETE/UPDATE-block trigger (audit-class).
2. **Eight new ALLOWED_EVENTS** + lockstep test update.
3. **Six new endpoints** (3 sub-features × 2 portals), each with full chain.
4. **Sweep loop** `mfa_revocation_expiry_sweep` (60s cadence, EXPECTED_INTERVAL_S calibrated, heartbeat-instrumented per Session 214 rule).
5. **Owner-transfer interlock:** `client_owner_transfer.initiate` + `partner_admin_transfer.initiate` refuse while ANY pending mfa_revocation exists for ANY in-org user.
6. **Customer emails** (3 templates): post-revoke (with restore link), post-reset (informational), post-policy-change (informational).
7. **Tests:** ~25 source-level + behavior tests covering the four Steve-flagged threat-model scenarios.
8. **Frontend:** OUT OF SCOPE — task #18.

## Disposition

**SHIP_THIS_WEEK post-implementation.** Round-table 5/5 APPROVE_DESIGN with Maya 2nd-eye green on all 6 dispositions. Implementation needs to come before any frontend work in #18 — the frontends will need these endpoints to call.

Total ALLOWED_EVENTS post-ship: 43.

## Outstanding pre-implementation

- [ ] Confirm `client_users.mfa_secret` + `partner_users.mfa_secret` columns exist + nullable (verify via prod_columns.json fixture).
- [ ] Identify any existing MFA-related sweep loops to coordinate cadence with.
