# Gate A — zero-auth endpoint hardening sprint (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

Direction is sound and matches Session 202 pattern. Six P0/P1 fixes required before
execution; original audit scope is incomplete (≥3 additional zero-auth endpoints
verified). One of the 4 endpoints (`delegate_signing_key`) is privileged-chain class
and missing its three-list registration — that is a chain-of-custody gap, not just
an auth fix.

---

## P0 findings (must close before any commit)

**P0-1 — `delegate_signing_key` is privileged-chain class but not registered.**
`appliance_delegation.py:325` INSERTs into `delegated_keys`; the resulting Ed25519
key signs evidence + audit-trail entries later relied on by the customer-facing
attestation chain. This is functionally equivalent to a signing-key rotation
(currently in `PRIVILEGED_ORDER_TYPES`: `signing_key_rotation` —
`fleet_cli.py:157`). Hardening to require `require_appliance_bearer` is necessary
but NOT sufficient. The CLAUDE.md Privileged-Access Chain of Custody §inviolable:
"Three lists MUST stay in lockstep". `delegate_signing_key` is in zero lists. Add
`delegate_signing_key` to `PRIVILEGED_ORDER_TYPES` + `ALLOWED_EVENTS`
(`privileged_access_attestation.py:52`) + mig-175 `v_privileged_types` (new
migration), OR explicitly justify in-doc why the appliance-self-issued path is
class-distinct and route attestation a different way. Recommendation: write an
attestation bundle on each successful `delegate-key` issuance, chained to the
site's prior evidence head — same shape as `signing_key_rotation`.

**P0-2 — Audit of historical `delegated_keys` rows mandatory.** Before flipping
auth on, Maya needs the answer: `SELECT key_id, appliance_id, site_id,
delegated_at, scope FROM delegated_keys WHERE revoked = false ORDER BY
delegated_at` on prod VPS. Any row issued by a caller other than the bound
appliance (telling: no matching `api_keys` row at the time of issuance) is a
historical leak that must be revoked + disclosed. Cannot be done after the
endpoint is auth-gated because pre-fix legitimate calls also looked
identical to attacker calls.

**P0-3 — Original audit scope is incomplete: ≥3 additional zero-auth state-changing
endpoints confirmed.** Focused grep + per-handler signature check found:
- `discovery.py:192` `POST /report` — writes `discovered_assets` for any scan_id
  it can name. Defense is "scan must exist + site_id must match"; both values
  attacker-controlled.
- `sensors.py:269` `POST /commands/{command_id}/complete` — flips
  `sensor_commands.status='completed'`. Anyone can mark any command done.
- `sensors.py:292` `POST /heartbeat` and `sensors.py:539` `POST /linux/heartbeat`
  — INSERT-OR-UPDATE into `sensor_registry`. Spoof-by-hostname class. Sibling of
  the heartbeat-4 issue.
- `provisioning.py:423` `POST /status` — flips
  `site_appliances.status` + transitions `sites.onboarding_stage='connectivity'`
  for any `appliance_id`. Not in original 4. Same class as heartbeat — pre-active
  identity, but mutates `sites` table. **Worse than heartbeat** because it sets
  `status='active'` which the dashboard treats as the cross-over to live.

Add these 4 to the sprint (or open immediate follow-up tasks). Original scope
of 4 missed at least 4 more. Audit method (manual code-read of 1 file)
under-sampled; the regex-based sweep above (FN around in-body auth) found 8
true positives across the backend.

**P0-4 — `delegate_signing_key` request-body `site_id` must NOT be trusted even
post-auth.** The proposed fix says "replace `request.site_id` with
`auth_site_id`". Confirm + extend: also pass `auth_site_id` to
`verify_appliance_ownership(conn, appliance_id, auth_site_id)` at line 282
(currently uses `request.site_id`) AND to the INSERT at line 332. Both
references must change in lockstep or the bound check is meaningless.
`request.site_id` should then be removed from `DelegatedKeyRequest` (model
breaking change — Python-agent caller in `local_resilience.py:807` still sends
it; harmless if ignored).

---

## P1 findings

**P1-1 — Heartbeat option choice — recommend B3 + MAC-bind enrichment.** Walk-
through below. The lowest-friction fix that closes the spoof window.

**P1-2 — `sensors.py` Linux + Windows heartbeats use the SAME pattern as the
provisioning heartbeat — apply the same B3 fix to all three.** They're
"appliance-forwarded" sensor heartbeats per the docstring; the forwarding
appliance already has a bearer, so they SHOULD use
`require_appliance_bearer + _enforce_site_id(auth_site_id, heartbeat.site_id)`,
NOT the pre-enrollment B3 carve-out. Different class than the provisioning
heartbeat — these run AFTER provisioning.

**P1-3 — `delegate_signing_key` 409 leak.** Line 294 returns
`f"Active key {existing['key_id']} exists until {existing['expires_at']}"` —
post-auth this is fine, but the `key_id` should still be redacted (returns 8-
char prefix only). Defense in depth.

**P1-4 — Audit-log row at the helper layer, not the handler.** Session 202
established the pattern that `_enforce_site_id()` writes the audit trail on
mismatch. Confirm the helper writes `admin_audit_log` with
`username=<auth_site_id>` + `action='cross_site_spoof_attempt'` rows on 403.
If not (it currently only logger.warns), upgrade as part of this sprint —
otherwise all 4 endpoints will silently lose the forensic trail.

---

## P2 findings

**P2-1 — `provisioning.py:849 /admin/restore` uses `require_admin` per the
grep — good. No change needed.** Listed only to confirm sibling endpoints
match.

**P2-2 — `framework_sync.py:206,213 /sync` and `/sync/{framework}` are
unauthenticated** but only kick off background OSCAL refresh tasks (DoS
class, not security class). Add `require_admin`; one-line fix.

**P2-3 — Coach pattern check: `_enforce_site_id` helper lives in
`agent_api.py:80` and is not re-exported.** Sprint touches `appliance_delegation.py`
+ `provisioning.py` + `sensors.py` — each file currently imports neither the
helper nor `require_appliance_bearer`. Decide ONCE whether to (a) lift
`_enforce_site_id` to `auth.py` / `shared.py` (real fix, used by Session 202
plus new sprint) or (b) duplicate. (a) preferred.

---

## Per-lens analysis

### Steve (Principal SWE)
- `verify_appliance_ownership(conn, appliance_id, request.site_id)` at line 282
  takes `site_id` as the 3rd arg — replacing with `auth_site_id` works
  mechanically. INSERT at line 327 also references `request.site_id` → must
  become `auth_site_id`. Both LOAD-BEARING; miss either and the auth gate
  is a no-op.
- `provisioning_heartbeat` claim flow: `provisioning.py:162 /claim` is the
  pre-enrollment entry that mints api_key. `/heartbeat` is called BEFORE
  `/claim` (appliance powered on but hasn't scanned QR yet — daemon emits
  heartbeat with MAC to let dashboard show "Appliance discovered, not yet
  claimed"). So the daemon has ZERO credential material at heartbeat time.
  This confirms B1/B2 are the only auth-true options; B3 is constrain-but-
  don't-authenticate.
- Additional zero-auth state-changing endpoints found by audit grep: 8 total
  across backend (4 original + 4 new: `discovery.py /report`,
  `sensors.py /commands/.../complete`, `sensors.py /heartbeat`,
  `sensors.py /linux/heartbeat`, plus `provisioning.py /status` which the
  prompt didn't flag but is in the same file and the same class).

### Maya (Legal / Compliance)
- `delegate_signing_key`'s output (Ed25519 keypair, hex-encoded private key)
  signs evidence later attached to `compliance_bundles` chain. If any
  attacker-issued key was minted historically, evidence signed by that key
  is repudiable. **The bundle.json sig column does NOT carry the
  delegated-key id by default** — `sync_audit_trail` does (line 463) but
  the main evidence pipeline (evidence_chain.py) signs with the per-
  appliance key from `site_appliances.agent_public_key`, not delegated
  keys. So the blast radius is contained to `appliance_audit_trail` rows
  + offline-mode L1 actions. Still a chain-gap for ANY appliance that ran
  offline-mode and synced after; needs prod query + audit.
- `appliance_audit_trail` rows accepted without auth would let an attacker
  inject false attestation events showing "we ran remediation X at time Y"
  for any site. §164.528 disclosure accounting class. P0 even if zero
  historical evidence of exploit.

### Carol (Security / HIPAA)
- Heartbeat **B1 (HMAC + shared fleet secret)** = key-distribution problem
  is real: if one ISO leaks, fleet-wide spoof window opens. Mitigation:
  per-ISO unique HMAC key built into the appliance disk image at flake build
  time, written into `appliance_provisioning.hmac_pre_enrollment_key` at
  the same time the provision code is generated. Workable but adds 1
  table column + 1 build-time secret + appliance-side code path.
- Heartbeat **B2 (one-time provisioning token)** = the appliance ALREADY
  has the provision_code (printed on label, scanned by tech, entered at
  setup). Reuse it as the heartbeat auth: heartbeat body includes
  `provision_code` + MAC; backend validates the code is `pending`. Same
  trust model as `/claim`. **Lowest implementation cost. Recommend B2.**
- Heartbeat **B3 (status-gate + rate-limit)** = doesn't authenticate, just
  shrinks the attack window. Adversarial scenario the prompt names:
  attacker steals MAC after legitimate first heartbeat — defense is the
  status transition `provisioning → active` is one-way; once active, the
  appliance has a real bearer + uses `require_appliance_bearer` for
  everything. Window is the 5-30 min from first heartbeat to claim. Not
  zero, but small.
- The current code at `provisioning.py:478` only matches on MAC for the
  UPDATE path; doesn't bind to hardware_id even though that's available
  during `/claim`. Pre-claim heartbeats COULD enrich `site_appliances`
  with `hardware_id` and have `/claim` reject if mismatched. Defense-in-
  depth; not required for the auth fix itself.
- `delegated_keys` privileged-chain integration: see P0-1 above.

### Coach (Consistency)
- Session 202 pattern is well-established (15 callsites in agent_api.py);
  copy exactly. `auth_site_id: str = Depends(require_appliance_bearer)`
  in handler signature + `_enforce_site_id(auth_site_id, request.site_id,
  "<endpoint_name>")` as the FIRST statement of the handler body. Don't
  diverge.
- Single-commit vs four-commit: **recommend two commits**. Commit 1 =
  Part A (3 delegation endpoints + privileged-chain registration of
  `delegate_signing_key`, single transactional class). Commit 2 = Part B
  (heartbeat auth-model choice + the 4 additional sensors/provisioning
  zero-auth endpoints found by the broader audit). Two commits keeps
  Maya's audit-trail review (Commit 1) separable from the heartbeat
  design-debate (Commit 2). Atomic per concern.
- Audit completeness: confirm 8 zero-auth state-changing endpoints total
  across the backend (this audit's grep result). Original 4 = 50% coverage.
  Either re-scope sprint to all 8 (recommended) OR open tasks for the
  other 4 immediately so they don't drift.
- Heartbeat option: Gate A should RECOMMEND (B2) — don't defer the
  decision to the user when the analysis is fork-tractable. User remains
  the final approver at Gate B.

---

## Heartbeat auth-model decision required

**Recommendation: B2 (one-time provisioning code as heartbeat auth).**

| Option | Implementation cost | Closes MAC-spoof? | Key-distribution problem? | Recommend |
|---|---|---|---|---|
| B1 | High (per-ISO HMAC, new column, build-flow change, appliance code) | Yes | Yes (per-ISO unique key required) | No |
| **B2** | Low (reuse `provision_code` already on the ISO label) | Yes | No (code is per-appliance + one-time) | **Yes** |
| B3 | Lowest (rate-limit + status-gate) | No (only constrains) | N/A | No — leaves auth gap |
| B4 (drop) | Lowest (delete handler) | N/A | N/A | No — daemon depends on it for the "discovered, not yet claimed" dashboard state |

B2 rationale: heartbeat body becomes `{mac, hostname, ip, provision_code}`.
Backend validates the code is `pending` + the MAC matches `claimed_by_mac` if
already claimed (idempotent). Code transitions to `claimed` only on `/claim`
(unchanged). Cost: one extra field in `HeartbeatRequest`, one extra column
read, ~15 lines.

---

## Audit completeness

Focused per-file regex sweep across all 100+ router files (see Gate A
session transcript). **8 zero-auth state-changing endpoints total:**

1. `appliance_delegation.py:258 /delegate-key` (original)
2. `appliance_delegation.py:414 /audit-trail` (original)
3. `appliance_delegation.py:561 /urgent-escalations` (original)
4. `provisioning.py:465 /heartbeat` (original)
5. `provisioning.py:423 /status` (**NEW** — flips site_appliances.status + sites.onboarding_stage)
6. `discovery.py:192 /report` (**NEW** — writes discovered_assets)
7. `sensors.py:269 /commands/{id}/complete` (**NEW** — flips sensor_commands state)
8. `sensors.py:292 /heartbeat` + `sensors.py:539 /linux/heartbeat` (**NEW** — INSERT/UPDATE sensor_registry)

False positives (have in-body auth via `require_appliance_auth`, webhook
signature, install token, signup-session, magic-link): `log_ingest.py:57`,
`alertmanager_webhook.py:45`, `billing.py:571`, `client_signup.py:28917/29019/29089`,
`portal.py:842/891/977/1722/989/1479/1603/1848/2057/2070/2607`,
`evidence_chain.py:3884` etc.

Notable lower-severity: `framework_sync.py:206 /sync` + `:213 /sync/{framework}`
(unauthenticated background-task triggers — DoS class only; one-line `require_admin` fix).

---

## Phase plan

- **Commit 1 (Part A — privileged-chain):** Lift `_enforce_site_id` to
  `auth.py`. Add `Depends(require_appliance_bearer)` + `_enforce_site_id` to
  the 3 `appliance_delegation.py` endpoints. Replace `request.site_id` with
  `auth_site_id` in `verify_appliance_ownership` + INSERT (P0-4). Register
  `delegate_signing_key` in `PRIVILEGED_ORDER_TYPES` + `ALLOWED_EVENTS` +
  new mig 304 for `v_privileged_types` (P0-1). Maya prod-query +
  remediation for any orphan `delegated_keys` rows (P0-2). Tests:
  401-no-auth + 403-cross-site + 404-unknown-appliance + admin_audit_log
  row written. Gate B fork-review required before push.

- **Commit 2 (Part B — heartbeat-class + extended scope):** Option B2 for
  `provisioning.py:465 /heartbeat`. Reuse same `require_appliance_bearer +
  _enforce_site_id` pattern for `provisioning.py:423 /status` (post-claim,
  has bearer), `sensors.py:292/539/269` (post-claim, has bearer),
  `discovery.py:192 /report` (post-claim, has bearer). Tests as Commit 1.
  Gate B fork-review required before push.

---

## Recommendation

**APPROVE-WITH-FIXES.** Proceed once: (1) P0-1 privileged-chain three-list
addition is designed + mig stubbed; (2) P0-2 prod `delegated_keys` audit
query is run + results reviewed by Maya; (3) Sprint scope re-baselined to
8 endpoints (or 4 follow-up tasks created); (4) Heartbeat decision = B2.

Two commits per phase plan. Gate B (fork-based) mandatory on each before
push per TWO-GATE rule (CLAUDE.md 2026-05-11 lock-in).
