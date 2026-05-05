# Round-table: Client-user email rename + admin recovery path

**Date:** 2026-05-05
**Scope:** Allow partner-side admin AND central-command admin to change a `client_users.email` (the login identity), without breaking operator posture. Also: close the "no-login-after-signup" onboarding gap so this isn't needed in the first place for new customers.
**Trigger:** North Valley test org has `contact_email='rellytherell@gmail'` on the site row but no matching `client_users` row + no admin path to set/rename one. Operator can't log in to test enterprise features.
**Format:** 5-seat principal round-table + Maya 2nd-eye consistency.
**Status:** DESIGN — implementation NOT started. Reviewed before any code.

---

## Verified state (2026-05-05, repo)

```
table         | column         | only-mutator                          | who can call
--------------|----------------|---------------------------------------|--------------------------
client_users  | email          | NONE — there is no rename endpoint    | nobody
client_users  | role           | client_portal.py:2113 PUT             | client owner-self
client_users  | is_active      | client_portal.py:2077 (deprovision)   | client owner-self
client_users  | password_hash  | self-service password reset           | the user themselves
client_users  | mfa_*          | mfa_admin.py (mig 276 #19)            | client owner / partner admin
client_orgs   | primary_email  | routes.py:4707 PUT /api/organizations | central-command admin only
sites         | contact_email  | routes.py:6801, sites.py PUT          | partner + admin
```

**Email sources at signup (none auto-mint a login):**
- `signup_sessions.email` (Stripe self-serve funnel) — discarded after completion
- `baa_signatures.email` — legal record only
- `client_orgs.primary_email` — display + alert routing
- `sites.contact_email` — display + alert routing

**Net:** a customer who completes Stripe signup + signs the BAA + has a site provisioned has `client_users` row count = **0**. They cannot log in. There is no UI or API to fix this. North Valley is exactly this state.

---

## Two distinct problems

### P1 — Onboarding gap (root cause for new customers)
The Stripe self-serve signup path doesn't auto-provision a `client_users` owner row. The customer's signup email is in 3 different tables but never used to mint a login. Manual partner-invite is the only path; if the partner forgets, the customer is stranded.

### P2 — Email rename gap (root cause for North Valley + future ops mistakes)
Even if signup did auto-provision (P1 fixed), there's no recovery if the email was wrong, the user lost access to that mailbox, or it was a placeholder during testing. `client_users.email` is effectively immutable today.

**This memo addresses both** — they're the same root cause (missing identity-management surface) and should ship as one feature so the contract is consistent end-to-end.

---

## Operator-posture constraint (NON-NEGOTIABLE)

Per `feedback_non_operator_partner_posture.md` + 8 architectural principles:
- OsirisCare is **substrate**, MSP is **operator**
- Clinic (CE) → MSP (BA) → Osiris (Subcontractor) is the HIPAA chain
- **Substrate (us) must NOT make clinical or operational decisions on behalf of the operator (MSP).**
- Substrate **MAY** perform administrative actions at the substrate layer with full audit + attestation chain.

**Email rename is administrative, not operational.** It does not access PHI. It does not change clinical workflow. It is identity-management on a substrate-managed table. **Therefore central-command admin is permitted to perform it** as long as:
1. Every action is Ed25519-attested + chained
2. The MSP partner is notified within minutes (operator visibility, not approval)
3. The end-user (whose login email is being changed) is notified
4. There's an undo / reversal path
5. Audit trail is auditor-kit-grade

This is the same posture as the MFA admin overrides (#19) — substrate provides the recovery primitive, partner remains the operator.

---

## Brian (Principal SWE) — design surface

### Endpoint shape

**Partner-side** (operator-class friction, mirrors existing partner mutations):
```
POST /api/partners/me/clients/{client_org_id}/users/{user_id}/change-email
  body: { new_email, reason ≥ 20ch, confirm_phrase }
  auth: require_partner_role("admin")
  emits: client_user_email_changed_by_partner (Ed25519, chain-anchored to org's primary site)
  side effects: invalidate all sessions for that user; send "your login email was changed by your provider" email to BOTH old + new addresses
```

**Central-command-side** (substrate operator-class, gated higher):
```
POST /api/admin/client-users/{user_id}/change-email
  body: { new_email, reason ≥ 40ch, confirm_phrase }
  auth: require_auth (admin_users) + require role >= 'admin'
  emits: client_user_email_changed_by_substrate (Ed25519)
  side effects: same invalidation + dual-email + ADDITIONAL operator-alert to the partner who owns the org (operator visibility, not consent — substrate just notifies)
```

**Why TWO endpoints, not one:** chain-of-custody must record WHO acted. A partner-initiated change vs a substrate-initiated change have different blast radii and different auditor-kit semantics. Same shape as session-216's owner-transfer (client + partner have separate state machines that converge to same role).

### State machine
None. Email rename is a **single-step** transaction (UPDATE + session invalidation + dual-email + attestation). No cooling-off, no magic-link confirmation. Why: cooling-off makes the recovery primitive itself useless when the original mailbox is what's broken. Same logic as MFA restore (#19 P0-2).

**Brian's veto:** if anyone proposes a "target-confirms-via-magic-link-to-new-address" gate, the entire feature is dead-on-arrival for North Valley class of bugs because the user does not yet control the new mailbox in the worst case (typo, transfer between people, etc.). Single-step + dual-notification is the right shape.

### Onboarding gap fix (P1)
Add to `client_signup.py::_complete_signup`:
```python
INSERT INTO client_users (client_org_id, email, role, is_active, email_verified)
VALUES ($org_id, $signup_email, 'owner', true, false)
ON CONFLICT (client_org_id, email) DO NOTHING
```
Then send the existing invite-style "set your password" email. Reuse the invite-token machinery from `client_portal.py:1955`. Behind a per-partner toggle `partners.auto_provision_owner_on_signup` BOOLEAN DEFAULT true. Operators who want manual gating can opt out.

**Brian's flag:** the auto-provision must be inside the same txn that writes the `subscriptions` row at signup completion, OR be re-driven by a sweep loop if it fails — otherwise partial signup state can strand the customer same as today. Prefer in-txn.

---

## Camila (DBA) — schema + integrity

### Migration 277 sketch
```sql
-- Partner toggle (default true = enterprise-ready posture)
ALTER TABLE partners
    ADD COLUMN auto_provision_owner_on_signup BOOLEAN NOT NULL DEFAULT true;

-- Email-change audit ledger (separate from admin_audit_log for clarity)
CREATE TABLE client_user_email_change_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_user_id UUID NOT NULL,
    client_org_id UUID NOT NULL,
    old_email TEXT NOT NULL,
    new_email TEXT NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    changed_by_kind TEXT NOT NULL
        CHECK (changed_by_kind IN ('self', 'partner', 'substrate')),
    changed_by_email TEXT NOT NULL,
    reason TEXT NOT NULL,
    attestation_bundle_id TEXT,
    -- Reversal window: 7 days. After that, ledger row is final and the
    -- change cannot be one-click-undone (still possible via fresh
    -- email-change call by the right actor).
    reversed_at TIMESTAMP WITH TIME ZONE,
    reversed_by_email TEXT,
    CONSTRAINT chk_email_change_reason_length
        CHECK (LENGTH(reason) >= 20)
);
CREATE INDEX idx_email_change_user ON client_user_email_change_log(client_user_id, changed_at DESC);
-- Append-only — same trigger pattern as mig 273/274/276
```

### Camila's flags
1. **Uniqueness collision on rename:** `client_users` has `(client_org_id, email)` unique. The endpoint MUST check for collision pre-UPDATE and return 409 — the OLD email row + the user-currently-holding-the-NEW-email row would both exist briefly. Pre-flight `SELECT 1 FROM client_users WHERE client_org_id=$1 AND email=$2 AND id != $3` and 409 if hit.
2. **Cross-org rename is BANNED.** This endpoint does NOT change `client_org_id`. If you want to move a user to a different org, that's a separate flow (and probably shouldn't exist at all — it leaks org boundaries). CHECK constraint via the SQL: `WHERE id=$1 AND client_org_id=$2` — if the user_id resolves to a different org, return 404.
3. **Lower-case normalization** on insert — same as login query (`email = body.email.lower()`). Without normalization a `Foo@bar.com` rename followed by a `foo@bar.com` rename creates a phantom dup.
4. **No FK from `mfa_revocation_pending.target_email` to `client_users.email`** — already noted in mig 276; rename safely doesn't break the audit chain because target_user_id is the FK-equivalent.

---

## Linda (PM) — UX + product semantics

### Where this lives in UI
**Partner side:**
- Partner portal → Clients → click client → Users tab → row → "Change email" button → modal
- Modal shape: confirms the user, asks for new email, asks for reason ≥20ch, confirm-phrase = "CHANGE-CLIENT-EMAIL"
- Toast post-success: "Login email changed. {old} → {new}. Both have been notified."

**Central-command side:**
- AdminOrgs.tsx → org row → Users tab → row → "Change email (substrate)" button — visually distinct (red-accent) to signal substrate-class action
- Modal: same shape but reason ≥40ch (higher friction) + warning banner: "This is a substrate-class action. The partner will be notified. You are NOT the operator — only use this to recover account access when the partner has lost the path."

### Linda's flag (consistency)
The 20ch vs 40ch threshold is the SAME asymmetry as the privileged chain (#19 used ≥20 for reset, ≥40 for revoke). We're applying it correctly here: partner action is operational class, substrate action is incident-recovery class.

**Add a self-service path too:** Settings → Account → "Change my email" — emits `client_user_email_changed_by_self` event. Most enterprise products allow this. It removes 80% of the admin demand. Maya may push back on this — see her section.

---

## Steve (Security) — threat model

### Mitigations

**M1 — Session invalidation must be transactional.** When email changes, every session token for that `user_id` is invalidated in the same txn. Otherwise a session minted under the OLD email continues to work after rename, which would let an attacker keep access if they were the one who triggered the rename:
```sql
DELETE FROM client_sessions WHERE user_id = $1
```
**Steve veto:** if this isn't in the same txn, the entire feature is a privilege-retention vector. Non-negotiable.

**M2 — Dual-notification (old + new addresses).**
- OLD address: "Your OsirisCare login email was changed to {new}. If this wasn't you, contact your provider AND OsirisCare support immediately." Include a 7-day reversal link (token-only, same as MFA restore primitive — Maya rule #2 from #19).
- NEW address: "Your OsirisCare login email is now {new}. You can sign in immediately." Include a "this wasn't me / I didn't expect this" link to file an abuse report.

**M3 — Rate limit + cooldown.** No more than 3 email-changes per `client_user_id` per 30 days, regardless of who initiates. Prevents rename-thrash as an attack pattern (rotating email past detection windows).

**M4 — Owner-transfer interlock.** If a `client_org_owner_transfer_request` is in `pending_target_accept` for this user, refuse the rename — same anti-race posture as #19 (Steve mit D). Otherwise the rename could redirect the magic-link to attacker-controlled mailbox.

**M5 — MFA-revocation interlock.** If a `mfa_revocation_pending` row is open for this user, refuse the rename. Same race vector as M4.

**M6 — Owner-role downgrade rule.** When the `owner` role's email is changed, the partner gets a HIGH-priority operator alert (P0-CHAIN-NOTIFICATION). Owner is the highest-blast-radius account in the org; visibility on owner-rename is mandatory.

**Steve's flags on Linda's self-service idea:** OK to ship, but with extra friction:
- Self-service email change MUST require fresh password re-entry (re-auth at the moment of action — proves session isn't compromised)
- Self-service MUST require email-confirmation on the NEW address (24h confirmation token → email is "pending" until confirmed; old email keeps working until then)
- Self-service does NOT require partner approval (per "self-service is fine") but DOES emit a notification

This is a **different shape** from partner/substrate paths (which are immediate). Self-service has the magic-link-to-new-address gate because the user controls both mailboxes; for partner/substrate that gate is a deadlock (Brian's veto above).

---

## Adam (CCIE) — operational posture

### Adam's flags
1. **Partner-notification on substrate action must be best-effort, not blocking.** If the partner SMTP is down, the substrate change still completes — but a `pending_partner_notification` row is queued for retry. Same shape as `email_dlq` from session 216.
2. **No active-partner check pre-rename.** If the partner is `status='deprovisioned'`, substrate can STILL rename the client user — that's exactly the recovery scenario this is built for. Partner-notification just no-ops in that case (logged at WARN).
3. **Cross-org sanity gate.** Adam wants to confirm that even with substrate access, the substrate admin cannot rename a `client_users` row across `client_org` boundaries (Camila already covered this — confirming enforcement at the SQL layer).
4. **Audit kit visibility.** The `client_user_email_changed_*` events MUST appear in the existing `/api/evidence/sites/{id}/auditor-kit` ZIP under a new section "Identity changes" so customers + auditors can see the full history when they download.

---

## Maya (consistency 2nd-eye) — adversarial review

### PARITY checks
- ✅ Partner + substrate endpoints have parallel shapes (matches #19 / owner-transfer dual-machine pattern)
- ✅ Reason length asymmetry (20 partner vs 40 substrate) matches existing privileged-action friction ladder
- ✅ Dual-email notification matches MFA revoke pattern (Steve mit B)
- ✅ Operator-alert hooks on every state transition (chain-gap escalation pattern from session 216)
- ✅ Anchor-namespace convention: client events anchor at org's primary site_id (or client_org:<id> synthetic if no sites yet)

### DELIBERATE_ASYMMETRY (these should differ)
- ✅ NO state machine (single-step rename) vs owner-transfer's 6-event machine — correct, because email is identity-recovery primitive, not role transfer
- ✅ NO target-confirm magic-link on partner/substrate paths (would deadlock for the very recovery cases this exists to fix). Self-service path DOES have it because user controls both mailboxes.
- ✅ Substrate admin gets HIGHER reason threshold (≥40) AND distinct event type (`_by_substrate` suffix) — auditor kit can filter on this when running the "did substrate touch operator data" query

### DIFFERENT_SHAPE_NEEDED — Maya pushback
**Maya P0:** the proposed self-service email change at Settings → Account is missing the partner-notification hook. If a clinic owner self-changes their email, the MSP partner needs to know — they're the operator and might be the only one with a record of the old email for support flows. **Fix:** self-service path also fires `client_user_email_changed_by_self` operator-alert to the partner. Same chain-gap escalation pattern. Severity P2 (lower than partner-initiated which is P1).

**Maya P0:** the auto-provision-on-signup toggle defaults `true` — Maya wants to confirm this is right. Reasoning: if it defaults `false` and an operator forgets to enable it, every customer they onboard is stranded. Default-true is enterprise-grade-default posture per `feedback_enterprise_grade_default.md`. ✅ APPROVED.

**Maya P1:** the 7-day reversal window on the email-change ledger (Camila's design) is shorter than I'd want. The MFA-revoke window is 24h because that's recovery-class. Email change is less urgent (the user can self-service-change again with the new email). **Either:** drop the reversal window entirely (and rely on a fresh change-email call as the undo), OR extend to 30 days. Don't pick 7. Brian + Camila to decide; Maya's recommendation is **drop the window** — it adds complexity for a path that's already covered by re-running the rename.

**Maya P1:** owner-role rename should fire a P0-OWNER-RENAME severity to the partner, NOT P1. Owner is the highest-privilege account; the visibility tier should match owner-transfer's. Steve's M6 noted P0-CHAIN-NOTIFICATION; Maya confirms.

### VETOED items
**Maya VETO:** any proposal to allow partner-side rename of `admin_users` (central command operators). Partners do not manage substrate operators. That's a directionally backwards posture violation. (Nobody proposed this; pre-emptive guard.)

**Maya VETO:** any proposal to change `client_users.email` AND `client_users.client_org_id` in the same call. Cross-org user move is its own can of worms — out of scope, separate round-table if ever needed.

### Three-list lockstep update
Adding 4 new ALLOWED_EVENTS:
- `client_user_email_changed_by_self`
- `client_user_email_changed_by_partner`
- `client_user_email_changed_by_substrate`
- `client_user_email_change_reversed`

Total ALLOWED_EVENTS: 45 → 49. Update `privileged_access_attestation.py` + lockstep test + verify NOT added to `fleet_cli.PRIVILEGED_ORDER_TYPES` (admin-API events, not fleet orders).

---

## Implementation checklist

### Migration 277
- [ ] `partners.auto_provision_owner_on_signup` BOOLEAN DEFAULT true
- [ ] `client_user_email_change_log` table + append-only trigger
- [ ] Indexes per Camila

### Backend
- [ ] `POST /api/partners/me/clients/{client_org_id}/users/{user_id}/change-email` — partner-class
- [ ] `POST /api/admin/client-users/{user_id}/change-email` — substrate-class
- [ ] `POST /api/client/users/me/change-email` — self-service (with magic-link confirm to NEW address)
- [ ] `POST /api/client/users/me/change-email/confirm` — token-only confirmation endpoint
- [ ] Auto-provision owner row in `client_signup.py::_complete_signup` (gated by partner toggle)
- [ ] Session invalidation in same txn (Steve M1)
- [ ] Dual-email notifications (old + new) per Steve M2
- [ ] Owner-transfer + MFA-revocation interlocks per Steve M4 + M5
- [ ] Operator-alert hooks on all 4 events (chain-gap escalation pattern)
- [ ] Auditor-kit "Identity changes" section per Adam #4

### Privileged-access lockstep
- [ ] 4 events added to `ALLOWED_EVENTS` (45 → 49)
- [ ] 4 events added to `test_privileged_chain_allowed_events_lockstep.py` expected set
- [ ] Confirm 4 events NOT in `fleet_cli.PRIVILEGED_ORDER_TYPES`

### Tests
- [ ] `tests/test_client_user_email_rename.py` — source-level + lockstep
- [ ] `tests/test_signup_auto_provisions_owner.py` — onboarding-gap fix
- [ ] `tests/test_email_rename_session_invalidation.py` — Steve M1 enforcement
- [ ] `tests/test_email_rename_interlocks.py` — Steve M4 + M5
- [ ] Partial unique index collision tests
- [ ] Pre-push allowlist updates

### Frontend (folds into task #18)
- [ ] Partner: PartnerClientUsersScreen → row → ChangeEmailModal
- [ ] Substrate: AdminOrgs.tsx → org → users → ChangeEmailModal (substrate variant, red accent)
- [ ] Self-service: ClientSettings → Account tab → Change Email form + confirmation flow

### Rollout
- [ ] Ship migration + endpoints + tests in one commit
- [ ] Round-table re-verify: Brian + Camila + Linda + Steve + Adam + Maya
- [ ] Deploy via git push, verify CI green, verify `/api/version` runtime_sha == disk_sha
- [ ] Use the substrate-class endpoint to fix North Valley as the smoke test

### Outstanding pre-work
- [ ] Round-table review of THIS doc with user before any code lands
- [ ] Decide Maya P1 reversal-window question (drop / 30 days)
- [ ] Confirm Maya P1 owner-rename severity (P0 vs P1)
- [ ] Confirm self-service flow is in scope for v1 or deferred (Linda+Maya recommend ship; Steve approves with M2 friction)

---

## Verdict matrix

| Reviewer | P0s | P1s | Verdict |
|---|---|---|---|
| Brian | 0 | 0 | APPROVE_DESIGN |
| Camila | 0 | 0 (4 enforcement notes) | APPROVE_DESIGN |
| Linda | 0 | 0 | APPROVE_DESIGN |
| Steve | 0 (6 mitigations baked in) | 0 | APPROVE_DESIGN |
| Adam | 0 | 0 (4 ops-posture confirmations) | APPROVE_DESIGN |
| Maya | 1 (self-service partner-notify) | 2 (reversal-window / owner-severity) | NEEDS_DECISION_THEN_APPROVE |

**Status:** Maya P0 (self-service operator-alert) is a one-line fix at code time, not a redesign. P1s are configurable choices the user should answer.

---

## User decisions needed before code

1. **Self-service email change in scope for v1?** (recommended: yes, with Steve M2 friction)
2. **Reversal window: drop or 30 days?** (Maya recommends drop)
3. **Owner-rename alert severity: P0 or P1?** (Maya recommends P0)
4. **Onboarding auto-provision default: confirm `true`?** (recommended: yes)
5. **Should we ship North Valley fix as a one-shot SQL via fork BEFORE the feature ships?** (so you can test enterprise features today vs waiting on this whole feature) — recommended: YES, gated behind a dated migration with explicit comment "manual recovery for testing org pre-feature-ship"
