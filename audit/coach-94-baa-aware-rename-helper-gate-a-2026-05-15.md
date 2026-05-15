# Gate A — Task #94 — BAA-aware `primary_email` rename helper

**Date:** 2026-05-15
**Gate:** A (pre-execution, fork-isolated)
**Author of design:** primary session
**Reviewer:** Gate A fork — 7 lenses (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
**Predecessors:**
- `audit/coach-91-baa-email-rename-orphan-gate-a-2026-05-15.md` — Task #91 Gate A (orphan-prevention test design)
- `audit/coach-91-primary-email-orphan-gate-b-2026-05-15.md` — Task #91 Gate B (close-out)
**Companions:**
- Task #93 (FU-A) — add `baa_signatures.client_org_id` FK column (structural fix)
- Task #95 (FU-C) — frontend Organizations.tsx modal silently no-ops

---

## Summary (≤300 words)

Task #94 is the proposed BAA-aware helper `rename_primary_email(conn, org_id,
new_email)` that re-anchors `baa_signatures` whenever a customer renames their
`client_orgs.primary_email`. The need exists because `baa_signatures` is
append-only (`trg_baa_no_update`, mig 224) and `baa_status.baa_enforcement_ok()`
joins by `LOWER(bs.email) = LOWER(co.primary_email)`. Renaming the email
without re-anchoring orphans every prior formal-BAA signature and blocks the
org from every BAA-gated workflow.

**LOAD-BEARING FINDING (Coach + PM unanimous): Task #94 is largely OBSOLETED by
Task #93.** Once `baa_signatures.client_org_id` ships as a real FK (#93), the
enforcement join in `baa_status.py:287-295` re-keys to `bs.client_org_id =
co.id`. Renaming `primary_email` then has **zero effect** on the join. The
orphan-class disappears structurally — not procedurally — and #94 collapses
from a 3-4h cryptographically-fraught re-anchor migration to a ~30-min audit
+ notification helper.

**Counsel verdict (load-bearing on `signed_at`):** if #94 ships pre-#93, the
new `baa_signatures` rows MUST preserve the ORIGINAL `signed_at` from the
source row (honest provenance — the customer signed on 2026-04-15, not today).
Setting `signed_at = NOW()` would misrepresent the signing date and undermine
§164.316(b)(2)(i)'s evidentiary purpose. The new row's audit-distinctness comes
from a `metadata.rename_event_id` JSONB key + a non-NULL
`metadata.re_anchored_from_signature_id` pointer, NOT from a forged timestamp.

**Final verdict: BLOCK-on-#93.** Recommend sequencing #93 BEFORE #94 and
re-scoping #94 post-#93 to a thin audit-log + notification helper. If
operational pressure forces #94 to ship first, the pre-#93 spec below is
APPROVE-WITH-FIXES (6 P0s, 4 P1s named below — most about `signed_at`
provenance, IP/UA semantics, RBAC, and the partition-incompatible UPSERT path).

**Prod-state probe:** zero current customers need a `primary_email` rename
this sprint (every existing client_orgs row was created via signup with the
same email that's on file). The orphan-class is latent, not live. This
strengthens the BLOCK-on-#93 recommendation: no operational pressure forces
the cryptographic re-anchor path.

---

## Per-lens verdict

### Steve (Principal SWE) — **BLOCK-on-#93, conditional-APPROVE pre-#93**

**Helper shape (pre-#93):**

```python
async def rename_primary_email(
    conn: asyncpg.Connection,   # admin_transaction-scoped
    org_id: str,
    new_email: str,
    *,
    actor_email: str,           # named human, NEVER 'system'/'admin'
    actor_kind: str,            # 'admin' | 'owner_self' | 'support'
    reason: str,                # >= 20 chars
    request: Request,           # for IP + UA capture
) -> dict:
    """Rename client_orgs.primary_email + re-anchor every baa_signatures
    row for the source email. ALL OR NOTHING — single admin_transaction.
    Returns {rename_event_id, signatures_re_anchored: int,
             attestation_bundle_id, old_email, new_email}.
    """
```

**Step sequence inside `admin_transaction(pool)`:**

1. `SELECT … FROM client_orgs WHERE id = $1 FOR UPDATE` — pessimistic row lock.
   Read current `primary_email`. If already == `new_email`: 409 no-op.
2. Collision-check: `SELECT id FROM client_orgs WHERE LOWER(primary_email) =
   LOWER($1) AND id != $2` — no two orgs may anchor to the same email
   (FK-like contract for the enforcement join).
3. `SELECT * FROM baa_signatures WHERE LOWER(email) = LOWER($1) ORDER BY
   signed_at ASC` — every row including `is_acknowledgment_only=TRUE` AND
   `=FALSE`. Both classes get re-anchored: acknowledgments retain audit
   continuity even though they're not enforcement-bearing.
4. `UPDATE client_orgs SET primary_email = $1, updated_at = NOW() WHERE id
   = $2 # noqa: primary-email-baa-gate — Task #94 BAA-aware helper`. The
   noqa marker is the LONE production-code exemption to Task #91's CI gate;
   NOQA_BASELINE_MAX must bump from 0 → 1 in the same commit + the line
   must be inside `rename_primary_email()` (file-level allowlist
   `client_org_email_rename.py`).
5. For each source signature row: `INSERT INTO baa_signatures (…) VALUES
   (…)` with NEW `signature_id` (uuid4) + ORIGINAL `signed_at` preserved
   (Counsel ruling §-Q below) + NEW email + NEW metadata JSONB carrying
   `{"rename_event_id": <uuid>, "re_anchored_from_signature_id":
   <source_id>, "rename_actor": <actor_email>, "rename_reason": <reason>}`.
   Carry forward: `signer_name`, `baa_version`, `baa_text_sha256`,
   `stripe_customer_id`, `is_acknowledgment_only`.
   DO NOT carry forward: `signer_ip` / `signer_user_agent` — those describe
   the ORIGINAL signing event (the human at the desk on
   2026-04-15), not the rename. Replace with NULL + record the rename
   actor's IP/UA in `metadata.rename_actor_ip` / `rename_actor_user_agent`.
6. `INSERT INTO admin_audit_log (user_id, username, action, target,
   details, ip_address)` with `action='primary_email_rename'`, target =
   `client_orgs.id::text`, details JSONB carrying
   `{old_email, new_email, signatures_re_anchored, rename_event_id}`.
7. Ed25519 attestation via `_emit_attestation` (anchor-namespace pattern:
   first-site-by-created_at via `_resolve_client_anchor_site_id`, fallback
   `client_org:<id>` synthetic). Event type:
   `client_org_primary_email_renamed`. **NEW addition to
   `privileged_access_attestation.ALLOWED_EVENTS`** — Steve M0 P0.
8. Operator-alert hook (`_send_operator_alert` opaque-mode, no clinic name
   in subject — Counsel Rule 7 opaque-by-default).

**Steve P0 list (pre-#93):**

- **P0-1.** Step-4 noqa exemption must be file-level allowlisted in
  `test_no_primary_email_update_orphans_baa.py::EXEMPT_PATHS` AND tied to
  function name (path-only allow is too coarse — any other function in the
  file should not borrow the exemption). Pattern: add an AST gate that
  asserts the marker lives inside `def rename_primary_email`.
- **P0-2.** Step-5 INSERT-row count vs source-row count MUST be asserted
  equal after the loop; if 0 signatures existed for the source email,
  Step-5 is no-op (acceptable — org never signed; let the rename proceed
  but log `signatures_re_anchored=0`).
- **P0-3.** Step-7 attestation event MUST be added to:
  - `fleet_cli.PRIVILEGED_ORDER_TYPES` (no — this is not a fleet order; skip)
  - `privileged_access_attestation.ALLOWED_EVENTS` (yes — chain anchor)
  - NOT in `migration NNN v_privileged_types` (also not a fleet_order
    insertion path)
  Per the §"Privileged-Access Chain of Custody" rule, this is a 2-of-3
  lockstep registration: ALLOWED_EVENTS + the test that pins it. Verify
  via `tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY`.
- **P0-4.** `signer_ip` / `signer_user_agent` policy ABOVE must be tested
  in source-shape gate: new INSERT in helper MUST carry literal `None` for
  those columns + record rename actor IP in metadata. Pattern matches the
  decision Counsel ruled on `signed_at` provenance (don't fake the original
  signing context; record the rename-event context separately).
- **P0-5.** Helper must REFUSE if `new_email` collision-check (step 2)
  returns a hit. Two orgs anchored at the same email would let either org
  steal the other's signatures on the next rename. 409 Conflict, no
  partial state.
- **P0-6.** `is_acknowledgment_only` flag on the new row must be COPIED
  from source (`is_acknowledgment_only=TRUE` rows stay TRUE on the new
  email). Setting them all to FALSE would silently promote click-throughs
  into formal signatures — mig 312's whole point was to distinguish those.

**Steve P1 list:**

- **P1-1.** Idempotency token on `metadata.rename_event_id` prevents double
  re-anchor if caller retries mid-txn-failure. Generate uuid4 at helper
  entry, return it; caller passes it back on retry.
- **P1-2.** The helper must be the ONLY production path that touches
  `primary_email`. Step 4's noqa is the canonical singleton. Test:
  `test_no_primary_email_update_orphans_baa.py` ratchet `NOQA_BASELINE_MAX`
  = 1 (one allowlisted callsite).
- **P1-3.** Wrap step 1 (FOR UPDATE) in its own savepoint inside the
  outer admin_transaction so a "not found / collision" raises a clean
  HTTPException(404/409) without poisoning the conn — asyncpg savepoint
  invariant per CLAUDE.md.
- **P1-4.** Email send must be opaque per Counsel Rule 7 + the
  `test_email_opacity_harmonized.py` pinning. Subject = plain string
  literal `"OsirisCare — Organization Primary Email Changed"`. Body
  redirects to authenticated portal — no clinic/org/actor names. The
  rename actor's IP/UA stay in audit + attestation, NOT in the SMTP
  channel.

**Post-#93 thin-helper spec (NOT a re-anchor — Steve recommended path):**

```python
async def rename_primary_email_thin(
    conn, org_id, new_email, *, actor_email, actor_kind, reason, request
) -> dict:
    """Post-#93 version. baa_signatures join is by client_org_id FK, not
    email — the rename has zero effect on enforcement. Helper exists only
    to record the audit-log + Ed25519 attestation + opaque notification.

    NO baa_signatures re-anchor. NO loop. ~30 lines.
    """
```

Same step 1, 2, 4, 6, 7, 8 from pre-#93 spec. **Drop step 3 + step 5
entirely.** The append-only re-anchor migration becomes dead code at #93
land time.

---

### Maya (CCIE / data integrity) — **BLOCK-on-#93**

**Schema column-by-column policy:**

| Column | Pre-#93 carry-forward policy | Why |
|--------|------------------------------|-----|
| `signature_id` | **NEW (uuid4)** | PK uniqueness; rows are distinct events |
| `email` | **NEW (= `new_email`)** | The whole point of the re-anchor |
| `stripe_customer_id` | **CARRY FORWARD** | Same customer; same billing context |
| `signer_name` | **CARRY FORWARD** | Same human signed both rows |
| `signer_ip` | **NULL** | Original IP describes the original signing event; we don't have it for the rename event because the rename actor is a DIFFERENT human |
| `signer_user_agent` | **NULL** | Same reason |
| `baa_version` | **CARRY FORWARD** | Same legal instrument |
| `baa_text_sha256` | **CARRY FORWARD** | Same document hash — the customer literally signed THIS BAA text |
| `metadata` | **NEW JSONB** with merge: source `metadata` ∪ `{rename_event_id, re_anchored_from_signature_id, rename_actor, rename_actor_ip, rename_actor_user_agent, rename_reason, original_signed_at_iso}` | Preserves original metadata + appends rename-event context |
| `signed_at` | **CARRY FORWARD ORIGINAL** | **COUNSEL §-Q below** |
| `is_acknowledgment_only` | **CARRY FORWARD** | Mig 312 distinction must survive the rename — a click-through-era acknowledgment stays an acknowledgment |

**Maya P0 list:**

- **P0-1.** `baa_text_sha256` carry-forward is REQUIRED, not optional. If
  it were re-derived from current BAA text, the rename would silently
  upgrade a v1.0-INTERIM click-through (pre-mig-312) into the appearance
  of a v2.0 signature. The audit-log must show the customer signed the
  v1.0 BAA, not "they renamed and now magically signed v2.0."
- **P0-2.** `signed_at` provenance per Counsel ruling below — preserve
  original.
- **P0-3.** Re-anchor loop must use `INSERT ... RETURNING signature_id`
  + count vs source-row count assertion. Partial loop completion under
  txn abort is technically impossible (single admin_transaction) but the
  assertion catches future refactors that try to parallelize.
- **P0-4.** Mig 312 backfill compatibility: rows that today have
  `is_acknowledgment_only=TRUE` carry forward as TRUE. The default-FALSE
  flip on the column happened AFTER existing rows were backfilled — the
  helper must NOT rely on column default and MUST set the flag explicitly
  on every new INSERT row.

**Post-#93 cleanup:**

Once #93 ships, the orphan-class disappears. The pre-#93 re-anchor rows
WILL EXIST in prod (Maya FU-A) and should be left in place — they're
auditor-distinguishable via `metadata.rename_event_id` and represent
honest event history. Mig 312-style backfill of `client_org_id` (#93
companion) must walk both the original AND the re-anchored rows; the FK
lookup is by-row, not by-email.

---

### Carol (Security / Counsel adjacent) — **BLOCK-on-#93**

**RBAC policy:**

| Caller | Pre-#93 allow? | Post-#93 allow? |
|--------|----------------|-----------------|
| Admin session (Depends(require_auth)) | **YES** | YES — with `admin_audit_log` row |
| Client owner self-service (osiris_client_session + role='owner') | **NO** (Counsel review needed; sensitive workflow) | maybe — same review |
| Partner admin (osiris_partner_session + admin role) | **NO** (cross-org elevation; explicitly disallowed by RT31) | NO |
| Substrate / automation | **NO** (no system-actor for legally-bearing rename) | NO |
| Magic-link recovery / break-glass | **NO** | NO |

**Carol P0 list:**

- **P0-1.** Admin-only for v1. Owner-self-service requires a separate
  Counsel review — primary_email is the BAA enforcement anchor; an
  owner who renames it is implicitly modifying the legal address of
  record. That's a §164.504(e)(2)(ii)(A)-adjacent question — defer.
- **P0-2.** Helper MUST emit attestation BEFORE returning success. Failure
  to attest escalates to `P0-CHAIN-GAP` operator alert per the
  "Operator-alert chain-gap escalation pattern" rule (Session 216, 16
  hooks). Implement the `<event>_attestation_failed: bool` flag exactly
  like client_user_email_rename.py:457 (see Read above).
- **P0-3.** Rate limit: 3 renames per org per 30 days. A rapid-fire
  rename sequence is a strong signal of compromise (attacker moving
  the anchor to grab the signatures). Use the rate-limit table pattern
  from `client_user_email_rename.py::_check_rate_limit`.
- **P0-4.** CSRF + audit-log capture of caller IP/UA. The helper writes
  `admin_audit_log.ip_address` from `request.client.host` — same pattern
  as the partner-portal mutation rules.

---

### Coach (consistency / 7 hard rules) — **BLOCK-on-#93 (load-bearing)**

This is the load-bearing finding. The 7-rules + obsolescence analysis:

**Rule 6 (BAA state machine-enforced):** the orphan-class IS the rule-6
class — BAA state silently broke when primary_email moved. Task #91 closed
the live path. Task #93 closes the class STRUCTURALLY. Task #94 closes
the class PROCEDURALLY. **Structural always wins.**

**Coach analysis: does #93 make #94 obsolete?**

Pre-#93 join (today, `baa_status.py:287-295`):
```sql
SELECT bs.baa_version
  FROM baa_signatures bs
 WHERE LOWER(bs.email) = LOWER($1)   -- $1 = client_orgs.primary_email
   AND bs.is_acknowledgment_only = FALSE
```

Post-#93 join (proposed):
```sql
SELECT bs.baa_version
  FROM baa_signatures bs
 WHERE bs.client_org_id = $1::uuid   -- $1 = client_orgs.id
   AND bs.is_acknowledgment_only = FALSE
```

In the post-#93 world, `UPDATE client_orgs SET primary_email = …` has
**ZERO effect** on the enforcement join. The orphan-class evaporates.
`#94` reduces to:
- audit-log row capturing who renamed + when
- attestation bundle for cryptographic chain continuity
- opaque notification to operator + (post-Counsel-review) owner

The append-only re-anchor migration (#94's hard part) is **dead code
post-#93**. Shipping it would create permanent operational complexity
that protects against an extinct bug class.

**Coach recommendation: BLOCK #94 pending #93's ship.** Re-scope post-#93
to the thin-helper spec (~30 lines). Effort drops from 3-4h to ~30min.

**Counterargument considered: ship #94 first as belt-and-suspenders.**
Rejected because:
1. Belt-and-suspenders for an extinct class is pure carrying cost.
2. The re-anchor path itself introduces new failure modes (partial loop
   abort, signed_at provenance bugs, RBAC drift) — each one is its OWN
   class of bug that wouldn't exist if we just shipped #93.
3. Zero customers need a primary_email rename this sprint. No
   operational pressure.
4. Counsel time on §-Q (`signed_at`, owner-self-service) is best spent
   on Master BAA v2.0 (#56), not on a soon-to-be-extinct helper.

**Coach 7-rules first-pass filter:**

| Rule | #94 pre-#93 | #94 post-#93 (thin) |
|------|-------------|---------------------|
| 1 — canonical metric | N/A | N/A |
| 2 — PHI boundary | clean (no PHI in baa_signatures) | clean |
| 3 — privileged chain | MUST register in ALLOWED_EVENTS | MUST register |
| 4 — orphan coverage | re-anchor risks new-orphan class if loop drops a row | thin helper has no loop, no risk |
| 5 — stale doc | n/a | n/a |
| 6 — BAA state machine-enforced | procedural fix; brittle | structural fix; cannot regress |
| 7 — opaque channel | opaque email per RT21 | same |

**Pre-#93 path passes rule 6 but introduces rule-4 risk. Post-#93 passes
all 7 cleanly.**

---

### Auditor (HHS OCR §164.316(b)(2)(i)) — **BLOCK-on-#93**

**§164.316(b)(2)(i) 7-year retention requirement:**

> "Retain the documentation required by paragraph (b)(1) of this section
> for 6 years from the date of its creation or the date when it last
> was in effect, whichever is later."

The append-only `baa_signatures` table + `trg_baa_no_update` already
satisfy this for the ORIGINAL signature. The rename-event re-anchor row
is a NEW record — it has its own 6-year clock starting at the rename
date. So the rename creates TWO retainable records:

1. Original signature (created at original signing; preserved by
   append-only) — clock starts at original signing date.
2. Re-anchored signature (created at rename event; preserved by
   append-only) — clock starts at rename date.

**Auditor P0 list:**

- **P0-1.** Audit-log row MUST capture: actor_email, actor_kind,
  timestamp, old_email, new_email, count of signatures re-anchored,
  rename_event_id, attestation_bundle_id. Pattern: same as
  `client_user_email_change_log` (lines 436-445).
- **P0-2.** Attestation bundle in `compliance_bundles` MUST anchor at
  the org's first site (or synthetic `client_org:<id>` if no sites yet)
  per the Session-216 anchor-namespace convention.
- **P0-3.** Re-anchored rows' `metadata.original_signed_at_iso` MUST be
  populated. Even though `signed_at` carries forward (Counsel), having
  the ISO timestamp explicitly in metadata makes the audit story
  obvious in JSONB scans.

**Auditor verdict on post-#93 thin-helper:** the audit-log row + attestation
are STILL required because the rename event itself is a discoverable
change to the legal address of record. Just the re-anchor `baa_signatures`
INSERT goes away. Audit story stays intact via the audit-log row + the
unchanged signature row (now linked by `client_org_id` FK).

---

### PM (sequencing / cost) — **BLOCK-on-#93**

**Effort matrix:**

| Path | Effort | Counsel time | Risk surface |
|------|--------|--------------|--------------|
| Pre-#93 #94 (re-anchor migration) | 3-4h impl + 1-2h tests + Counsel §-Q (signed_at, owner-self) | HIGH | re-anchor loop, signed_at provenance, RBAC, partition compatibility |
| Wait for #93, then thin #94 | ~30min impl + audit-log + attestation reuse | LOW (no §-Q) | minimal |
| #93 itself | 1-2h impl + Maya backfill walk + Carol RLS parity | LOW (structural fix, mechanical) | bounded — touches one column, one FK |

**Sequencing recommendation:**

1. **Now**: keep Task #91's CI gate in place (already shipped). Zero
   new customer pressure. Status quo holds.
2. **This week**: ship #93. ~2h work + mig + backfill + RLS parity
   (Task #91 Gate B already named the test
   `test_no_primary_email_update_orphans_baa.py` as the orphan-class
   pinner; #93 closes the class by making the test redundant).
3. **After #93 lands**: write thin-#94 (~30min). Strip out the
   re-anchor loop entirely. Helper is just audit-log + attestation +
   opaque notification.
4. **In parallel**: Task #95 (frontend Organizations.tsx silent-no-op)
   gets fixed alongside the thin-helper since the modal needs to call
   into the helper instead of the raw PUT endpoint.

**Cost of NOT doing this (ship #94 pre-#93):** ~5h of work, Counsel
§-Q load, permanent append-only re-anchor rows in prod that have no
post-#93 purpose, a one-line `noqa` exemption that needs a permanent
home in a CI gate. All to protect against an extinct bug class.

**PM verdict: BLOCK-on-#93.** Re-scope #94 to thin-helper post-#93.

---

### Counsel (load-bearing on `signed_at`) — **PRESERVE ORIGINAL `signed_at`**

The §-Q: when re-anchoring a `baa_signatures` row at a new email, does the
new row's `signed_at` carry forward the original timestamp or use NOW()?

**Counsel ruling: PRESERVE ORIGINAL.**

Rationale:
- `signed_at` is an evidentiary timestamp. The customer signed the BAA
  on 2026-04-15. They did NOT sign it today. Setting `signed_at = NOW()`
  forges the signing date.
- The rename event is a SEPARATE event with its own timestamp — that
  belongs in `admin_audit_log.created_at` + the attestation bundle's
  timestamp + `metadata.rename_event_id`. NOT in `signed_at`.
- §164.316(b)(2)(i) requires retention "from the date of its creation
  or the date when it last was in effect" — preserving original
  `signed_at` keeps the original "date of creation" intact for the
  underlying signing event, while the rename row has its own
  metadata-side timestamp for the re-anchor event.
- An auditor asking "when did this org sign the BAA?" should see the
  original date. Asking "when did this org rename its primary email?"
  should see the rename date in audit-log. Two questions, two
  timestamps, no conflation.

**Counsel ruling on owner-self-service:** DEFER. The primary_email IS the
BAA address-of-record. An owner unilaterally changing it without admin
oversight is the legal equivalent of moving the place of contract
performance — that touches §164.504(e)(2)(ii)(A) (terms of the BAA) and
deserves its own §-Q in the Counsel-queue bundle (Task #37). For v1:
admin-only.

**Counsel ruling on `signer_ip` / `signer_user_agent`:** the original IP+UA
describe the original signing event. Carrying them forward into the
re-anchored row would misrepresent the rename actor as the original
signer's device. Set to NULL on the re-anchored row + capture the
rename actor's IP+UA in `metadata.rename_actor_ip` /
`rename_actor_user_agent`. Two separate provenance trails, neither one
forged.

---

## P0 / P1 consolidated rollup

### P0 (must close before #94 ships in EITHER path)

- **P0-1 (Coach load-bearing):** Block #94 pending #93 land. Re-scope to
  thin-helper post-#93. If operational pressure overrides this, escalate
  to user with this verdict cited.
- **P0-2 (Steve M0 + Carol P0-2):** Register `client_org_primary_email_renamed`
  in `privileged_access_attestation.ALLOWED_EVENTS`. Update lockstep
  pinner `tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY`.
- **P0-3 (Steve P0-3 + Counsel):** `signed_at` preservation policy MUST
  be source-shape-pinned. New test
  `test_baa_aware_rename_helper_preserves_signed_at.py` asserts the helper
  uses `signed_at = $N` (carried) not `signed_at = NOW()` / `DEFAULT`.
- **P0-4 (Maya P0-2 + P0-4):** `baa_text_sha256` carry-forward +
  `is_acknowledgment_only` carry-forward must be pinned by source-shape
  gate. A future refactor that tries to "modernize" the new row's
  baa_version → v2.0 silently must fail CI.
- **P0-5 (Steve P0-1):** noqa exemption for step-4 UPDATE must be
  AST-gated to live inside `def rename_primary_email`, not just file-level.
  NOQA_BASELINE_MAX bump 0 → 1.
- **P0-6 (Auditor P0-3 + P0-1):** audit-log row schema fully specified
  + attestation bundle anchor pattern matches Session-216 convention.
  Operator-alert chain-gap escalation pattern wired in for attestation
  failure.

### P1 (named follow-ups, may carry as TaskCreate)

- **P1-1 (Steve P1-3):** asyncpg savepoint invariant — wrap each
  no-fatal step in `async with conn.transaction():`.
- **P1-2 (Steve P1-4 + Counsel Rule 7):** opaque-mode notification —
  no clinic/org/actor names in SMTP channel; pinned by
  `test_email_opacity_harmonized.py`.
- **P1-3 (Carol P0-3):** rate-limit 3 renames per org per 30 days.
- **P1-4 (Steve P0-5):** collision-check 409 prevents two orgs sharing
  an email anchor.

### P2 (post-launch / nice-to-have)

- **P2-1 (PM):** UI affordance — frontend Edit modal in Organizations.tsx
  should call helper endpoint, not raw PUT. Tracked as Task #95.
- **P2-2 (Steve P1-1):** Idempotency token on `metadata.rename_event_id`
  for retry-safe behavior.

---

## Verdict

**BLOCK-on-#93.** Recommend:

1. Mark Task #94 dependency: blocked-by #93.
2. Ship Task #93 first (FK column, ~2h work, mechanical).
3. Re-scope Task #94 to the thin-helper spec (~30min work) AFTER #93 lands.
4. If user/operator pressure forces #94 to ship pre-#93, the
   pre-#93 spec is APPROVE-WITH-FIXES on the 6 P0s above.

**Reviewer:** Gate A fork — 7 lenses.
**Next gate:** Gate B (pre-completion) once the helper actually ships, per
Session 219 TWO-GATE lock-in. Gate B MUST run the curated source-level
test sweep per Session 220 lock-in.
