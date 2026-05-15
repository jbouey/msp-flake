# Gate A v2 — Task #93: `baa_signatures.client_org_id` FK — RE-GATE on signup-flow temporal ordering

> **Class-B 7-lens adversarial review (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)**
> **Author:** fork (general-purpose subagent, fresh context per Session 219 lock-in)
> **Date:** 2026-05-15
> **Predecessor:** `audit/coach-93-baa-signatures-client-org-id-fk-gate-a-2026-05-15.md`
> **Trigger:** implementation surfaced an INSERT-time atomicity blindspot at `client_signup.py:299` — the BAA INSERT fires in `POST /signup/sign-baa` (~line 299), but `client_orgs` is not materialized until the Stripe webhook `complete_signup` → `_materialize_self_serve_tenant` (~line 717), MINUTES TO HOURS LATER. The v1 prescribed fix ("look up `client_orgs` by primary_email before the BAA INSERT") cannot work — the row doesn't exist yet.

<!-- mig-claim:321 task:#93 -->

---

## 350-word summary

**Verdict: APPROVE Option (E) — Python-side UUID pre-generation, materialize client_orgs FIRST at BAA-sign time (not at webhook time), pass the same UUID through both INSERTs.** This is materially different from the v1 design's "look up client_orgs by email immediately before the BAA INSERT" — at the BAA endpoint, the row genuinely does not exist yet, and the prescribed lookup would 100% fail-closed every cold-onboarding self-serve customer.

Probe verified the actual flow: `/signup/start` writes only `signup_sessions`; `/signup/sign-baa` writes `baa_signatures` (line 299); Stripe Checkout runs; webhook `complete_signup` calls `_materialize_self_serve_tenant` which finally INSERTs `client_orgs` (line 717) via `ON CONFLICT (primary_email) DO UPDATE`. There are 3 client_orgs writers: `_materialize_self_serve_tenant` (self-serve cold-onboarding), `org_management.py:145` (partner-led explicit create), `routes.py:4734` (admin create). The BAA endpoint is hit BEFORE any of these on the self-serve path.

The legal model (verified §164.504(e) reading + the existing `org_status = "active" if baa_signature_id else "pending"` ternary at line 716) is: **a customer signing the BAA establishes the CE↔BA relationship; the client_orgs row is the persistence of that establishment**, not its precondition. The order is "the CE signs the BAA → the BA materializes the org record reflecting that signed BAA." Materializing `client_orgs` at BAA-sign time (status=`pending_provisioning` until checkout completes) is a faithful realization of this — NOT a contract violation. The webhook's `ON CONFLICT (primary_email) DO UPDATE` already handles idempotent re-entry.

Option (E) is cleanest because: (1) NO trigger-disable needed (the BAA row INSERTs with `client_org_id` populated from the start); (2) NO migration backfill complexity beyond what v1 already designed; (3) atomic same-transaction INSERT of `client_orgs` + `baa_signatures` preserves §164.504(e) "BAA in place before BA services" because the two rows materialize at the same instant; (4) the webhook becomes idempotent on `client_org_id` lookup from `signup_sessions` (which now stores it); (5) failed-checkout rollback is trivial — `client_orgs.status='pending_provisioning'` rows have no PHI, no provisioning code, no users yet, sweepable.

**Effort revision: 1 day → 1.5 days.** Mig 321 SQL unchanged; client_signup.py changes expand from "1-line lookup" to "~30-line restructure of sign_baa endpoint + signup_sessions column + 24-line restructure of _materialize_self_serve_tenant for idempotency".

---

## v1 Gate A BLINDSPOT — class lesson

**v1 prescribed**: at `client_signup.py:299`, INSERT into baa_signatures with `client_org_id` resolved via `SELECT id FROM client_orgs WHERE LOWER(primary_email) = LOWER($email)`, fail-closed if no row.

**What v1 missed**: v1 grep'd `INSERT INTO baa_signatures` (single writer) and `INSERT INTO client_orgs` (multiple writers) but did NOT verify these run in the **same request flow** in the **same temporal window**. v1 treated "single writer of baa_signatures" as "row exists nearby in code" — but the BAA writer is in `POST /signup/sign-baa` (step 2 of a 4-step checkout funnel), and the self-serve `client_orgs` writer is in the Stripe webhook handler invoked SECONDS-TO-HOURS later by Stripe's async retry-capable channel.

**The class**: any Gate A that adds an FK between two append-only or attestation-bearing tables MUST verify INSERT-time atomicity by:
1. `grep -n 'INSERT INTO {parent_table}' --include='*.py'` — enumerate parents.
2. `grep -n 'INSERT INTO {child_table}' --include='*.py'` — enumerate children.
3. **For EACH (parent_writer, child_writer) pair**: trace the call graph. Are they in the same endpoint? Same db transaction? Same HTTP request? Different requests separated by webhooks/queues?
4. If they're in different requests, the FK design MUST address: (a) which writer fires first temporally? (b) is that ordering legally/semantically required? (c) can we re-order? (d) if not, must we introduce a deferred/nullable phase?

**Concrete gate addition** (P1 followup to land with mig 321 commit): `tests/test_fk_insert_atomicity_documented.py` — scan migration files for `ADD COLUMN ... REFERENCES` patterns and require a sibling design-doc citation that names the temporal ordering of the two writers. Stretch goal; not blocking for mig 321 itself.

---

## DESIGN-QUESTION RESOLUTION

### Q1: Is the BAA INSERT a HARD prerequisite for client_orgs creation, or just historical?

**Verified semantic** (read of `_materialize_self_serve_tenant` line 716 + the `org_status = "active" if baa_signature_id else "pending"` ternary): the existing code **already encodes "BAA-on-file → org status advances"** as a state-machine transition, NOT as a hard ordering precondition. The `client_orgs` row exists in `status='pending'` if no BAA signature yet, and gets promoted to `'active'` once BAA confirmed.

This proves the legal model is the second framing: **"CE signs BAA → BA materializes the persistent record of the CE↔BA relationship in its system."** The `client_orgs` row IS that persistent record. Pre-materializing it as `status='pending_provisioning'` at BAA-sign time is faithful to §164.504(e) because:

- The signed BAA IS the legal instrument establishing the CE↔BA relationship.
- The client_orgs row is the BA's internal record of that relationship — it persists the legal fact, doesn't create the legal fact.
- §164.504(e) requires "satisfactory assurances in the form of a written contract" — that's the `baa_signatures` row, not the `client_orgs` row.

**Counsel position (Maya §)**: re-ordering is NOT a contract violation. The two rows are different concerns: `baa_signatures` is the legal instrument; `client_orgs` is the operational record. They MUST be atomic in the same transaction (for accountability), but their relative INSERT order within that transaction is a database mechanics question, not a §164.504(e) question.

### Q2: Resolution options

| # | Option | Effort | Closes orphan class? | Counsel risk | Verdict |
|---|--------|--------|------------------------|---------------|---------|
| A | Re-order to client_orgs-first in webhook | 4-6hr refactor | YES — but doesn't help mig 321 because BAA INSERT is upstream of webhook | LOW | REJECT — doesn't solve the right problem |
| B | NULL forever, no NOT NULL | 0hr | NO — leaves the class structurally open | LOW | REJECT (counsel Rule 6 fails) |
| C | Move client_orgs materialization INTO `/signup/sign-baa` endpoint (no Python-uuid) | 4hr refactor | YES | LOW | **VIABLE — fallback** |
| D | Pre-materialize client_orgs at `/signup/start` | 6hr refactor | YES — but creates ghost orgs for abandoned signups (dead-row class) | MEDIUM (BAA-precedes-org reversed) | REJECT — counsel signal |
| E | Python-side UUID pre-gen, atomic same-txn INSERT (client_orgs → baa_signatures) inside `/signup/sign-baa` | 3-4hr refactor | YES | LOW | **RECOMMEND** |

**Why E over C**: E is just C with the implementation detail that we generate `client_org_id` in Python (uuid.uuid4()) and pass it explicitly to both INSERTs. This is marginally cleaner than C (which would rely on `INSERT INTO client_orgs ... RETURNING id` then bind that id to the BAA INSERT) because: (i) it lets the BAA INSERT and the client_orgs INSERT happen in arbitrary order in the same transaction with no dependency between them; (ii) the same UUID is later inserted into `signup_sessions.client_org_id` for the webhook to find idempotently; (iii) failure modes are simpler — if either INSERT fails, the whole txn rolls back, no orphan rows.

C is the fallback if Carol or Steve later raise an objection to Python-generated UUIDs (none expected — see Carol §).

### Q3: Each viable option assessment

**Option (C) — RETURNING id**:
- Effort: 4hr (refactor sign_baa to do `INSERT INTO client_orgs ... ON CONFLICT (primary_email) DO UPDATE ... RETURNING id` first, then pass returned id to baa_signatures INSERT).
- Rollback: txn rollback on either INSERT failure.
- Counsel: equivalent to (E) on §164.504(e). FK feasible NOT NULL.

**Option (D) — pre-materialize at /signup/start**:
- Effort: 6hr.
- Risk: creates `client_orgs` rows for never-completed signups. Today the system filters these out by `status='pending'` but the row count would grow unboundedly. Need a `pending_signup_orgs_pruner` daily task — extra surface area.
- Counsel: WEAK — materializing the BA's record BEFORE the BAA is signed inverts the §164.504(e) "satisfactory assurances before performance" order; not a hard violation but reads worse than (C)/(E).
- **REJECT**.

**Option (E) — Python-side UUID + atomic same-txn dual INSERT** ← RECOMMENDED:
- Effort: 3-4hr.
- Rollback: txn rollback if either INSERT fails. No dead rows.
- Counsel: equivalent to (C) on §164.504(e). FK feasible NOT NULL.
- Differentiator: client_org_id is generated in Python BEFORE both INSERTs, so it can be passed as an explicit param to both. The BAA INSERT references `client_org_id` from the start (no post-hoc UPDATE, no trigger-disable on the runtime path).

---

## BUILD-READY SPEC (Option E)

### Migration 321 — UNCHANGED from v1

The mig 321 SQL block in the v1 Gate A doc remains correct as-is. The trigger-disable + synthetic-row quarantine + ADD COLUMN UUID + ALTER NOT NULL + FK creation + index creation + audit-log row → all unchanged.

**Reason**: the migration's job is to (a) cleanse the existing synthetic orphan row, (b) add the column NOT NULL with FK. The new INSERT-time semantics (atomic same-txn client_orgs+baa_signatures) operate on rows created AFTER mig 321 lands; the migration itself doesn't know or care.

### `client_signup.py` changes — REPLACE v1's prescribed 1-line lookup

**v1 prescribed** (at line 299, immediately before the BAA INSERT):
```python
org_row = await conn.fetchrow(
    "SELECT id FROM client_orgs WHERE LOWER(primary_email) = LOWER($1)",
    row["email"],
)
if not org_row:
    raise HTTPException(status_code=400, detail={"error_code": "baa_signup_no_org", ...})
```
**This fails 100% of self-serve cold-onboarding because client_orgs doesn't exist yet.**

**v2 prescribed** (REPLACE lines 281-317 in sign_baa endpoint):

```python
pool = await get_pool()
async with admin_connection(pool) as conn, conn.transaction():
    row = await conn.fetchrow(
        "SELECT email, stripe_customer_id, plan, expires_at, completed_at, "
        "       practice_name, billing_contact_name, state, client_org_id "
        "  FROM signup_sessions WHERE signup_id = $1 FOR UPDATE",
        req.signup_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="signup_id not found")
    if row["completed_at"]:
        raise HTTPException(status_code=409, detail="signup already completed")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="signup session expired")

    signature_id = str(uuid.uuid4())
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]

    # ─── Atomic client_org materialization ──────────────────────
    # Pre-materialize client_orgs in status='pending_provisioning'
    # so baa_signatures can reference it via FK from the start.
    # The Stripe webhook's _materialize_self_serve_tenant later
    # promotes status='pending_provisioning' → 'active' and
    # populates fields not yet known (stripe_customer_id may be
    # null here if checkout hasn't run; promotion handles it).
    #
    # Idempotent on (primary_email). If a client_orgs row exists
    # (e.g. partner-led path created it first), reuse its id.
    # If reused row already has client_org_id stored in
    # signup_sessions.client_org_id, that takes precedence.
    if row["client_org_id"]:
        client_org_id = str(row["client_org_id"])
    else:
        practice_name = row["practice_name"] or row["email"] or "Practice"
        client_org_id = str(uuid.uuid4())
        org_row = await conn.fetchrow(
            """
            INSERT INTO client_orgs (
                id, name, primary_email, billing_email, state,
                stripe_customer_id, status
            ) VALUES (
                $1::uuid, $2, $3, $3, $4, $5, 'pending_provisioning'
            )
            ON CONFLICT (primary_email) DO UPDATE SET
                updated_at = NOW()
            RETURNING id
            """,
            client_org_id, practice_name, row["email"],
            row["state"], row["stripe_customer_id"],
        )
        # ON CONFLICT may have returned the EXISTING id, not the
        # one we generated. Use whatever the DB says.
        client_org_id = str(org_row["id"])

    # ─── baa_signatures INSERT (now with client_org_id) ─────────
    await conn.execute(
        """
        INSERT INTO baa_signatures
            (signature_id, email, stripe_customer_id, client_org_id,
             signer_name, signer_ip, signer_user_agent,
             baa_version, baa_text_sha256, metadata)
        VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10::jsonb)
        """,
        signature_id, row["email"], row["stripe_customer_id"],
        client_org_id,
        req.signer_name, client_ip, user_agent,
        BAA_VERSION, req.baa_text_sha256,
        json.dumps({"signup_id": req.signup_id, "plan": row["plan"]}),
    )

    # ─── Update signup_sessions with both pointers ──────────────
    await conn.execute(
        "UPDATE signup_sessions "
        "   SET baa_signature_id = $1, baa_signed_at = NOW(), "
        "       client_org_id = $3::uuid "
        " WHERE signup_id = $2",
        signature_id, req.signup_id, client_org_id,
    )
```

### `signup_sessions` schema extension — NEW column

Mig 321 must ALSO add (same transaction as the baa_signatures FK):

```sql
-- Cache client_org_id on the signup session so the Stripe
-- webhook can find it idempotently without re-querying by
-- primary_email (which is racy if a partner-led path also
-- created the row).
ALTER TABLE signup_sessions
    ADD COLUMN IF NOT EXISTS client_org_id UUID NULL
        REFERENCES client_orgs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_signup_sessions_client_org_id
    ON signup_sessions (client_org_id)
    WHERE client_org_id IS NOT NULL;
```

`signup_sessions` is NOT append-only (no trg_baa_no_update equivalent), so this ADD COLUMN + UPDATE pattern is trivial.

### `_materialize_self_serve_tenant` changes — idempotency hardening

Replace lines 715-748 (the client_orgs upsert) with:

```python
# 1. client_orgs — idempotent. If sign_baa already materialized
# the org (status='pending_provisioning'), promote it; else
# create. The new flow ALWAYS materializes at BAA-sign time,
# but historical signups (pre-mig-321) may arrive here
# without a prior client_orgs row — handle both paths.
existing_org_id = signup_row.get("client_org_id")  # from signup_sessions

org_status = "active" if baa_signature_id else "pending"
if existing_org_id:
    # Promote existing pending_provisioning row.
    org_row = await conn.fetchrow(
        """
        UPDATE client_orgs SET
            stripe_customer_id = COALESCE(stripe_customer_id, $2),
            status = CASE
                WHEN status = 'pending_provisioning' AND $3 = 'active'
                    THEN 'active'
                ELSE status
            END,
            onboarded_at = COALESCE(onboarded_at,
                CASE WHEN $3 = 'active' THEN NOW() ELSE NULL END),
            updated_at = NOW()
         WHERE id = $1::uuid
        RETURNING id, status
        """,
        existing_org_id, customer_id, org_status,
    )
else:
    # Legacy path — sign_baa didn't run for this session
    # (pre-mig-321) or session was created via a path we don't
    # control. Fall back to the original upsert by email.
    org_row = await conn.fetchrow(
        """  -- original INSERT/ON CONFLICT (primary_email) block
        """,
        practice_name, email, billing_state, customer_id, org_status,
    )
```

### NEW status value: `pending_provisioning`

Add to whatever CHECK constraint governs `client_orgs.status`. If the column has no CHECK, add one in mig 321:

```sql
ALTER TABLE client_orgs
    ADD CONSTRAINT IF NOT EXISTS client_orgs_status_ck
    CHECK (status IN ('pending', 'pending_provisioning', 'active',
                      'suspended', 'churned', 'inactive'));
```

(verify the current allowed values via `\d+ client_orgs` against prod before pinning the list — Maya P0 below.)

### Idempotency invariant

A signup session that has reached `/signup/sign-baa` once but failed before checkout, then re-attempted: the new BAA endpoint re-runs the same logic. The `ON CONFLICT (primary_email) DO UPDATE SET updated_at = NOW() RETURNING id` shape returns the existing `client_orgs.id`, so the second BAA signature row (different signature_id) references the SAME client_org_id. Multiple BAA signatures per client_org are legal (re-sign on BAA version bump) — the FK is many-to-one.

### Failed-checkout cleanup

`client_orgs` rows in `status='pending_provisioning'` for sessions that abandoned before checkout: these accumulate but contain no PHI, no users, no provisioning code (those are still gated by webhook completion). A daily pruner task can sweep `status='pending_provisioning' AND created_at < NOW() - INTERVAL '14 days' AND id NOT IN (SELECT client_org_id FROM signup_sessions WHERE completed_at IS NOT NULL)`. Filed as task #93-FU-D (P2 — not blocking).

---

## 7-LENS BREAKDOWN

### Steve (Principal Engineer — schema + flow mechanics)

**APPROVE Option E with FIX-S1**:

- Walked the flow end-to-end. The v1 design's "lookup by primary_email at line 299" is unimplementable on the self-serve path. Confirmed.
- Option E (Python-side UUID) is the simplest correct shape. The `INSERT ... ON CONFLICT (primary_email) DO UPDATE ... RETURNING id` correctly handles the partner-led path where `client_orgs` may already exist.
- **FIX-S1 (P0)**: the `ON CONFLICT (primary_email)` clause MUST return the EXISTING id, not the Python-generated one we tried to insert. Postgres RETURNING on ON CONFLICT DO UPDATE returns the post-update row's id (which is the existing one). The spec uses `client_org_id = str(org_row["id"])` after the INSERT — correct. Pin in test: `tests/test_signup_baa_idempotent_on_email_collision.py` (pg integration test, post-mig-321).
- No FK/trigger breakage — the new INSERT on baa_signatures populates client_org_id from the start, never NULL.

### Maya (HIPAA Counsel proxy + schema impact)

**APPROVE with FIX-M1**:

- Option E preserves the §164.504(e) "BAA before BA services" ordering perfectly: both rows materialize in the SAME transaction at the SAME instant. The BAA is "in place" at the moment the client_orgs row exists.
- **FIX-M1 (P0)**: verify the prod-current `client_orgs.status` CHECK constraint allows the new `pending_provisioning` value. If the constraint exists, mig 321 must `DROP CONSTRAINT … ADD CONSTRAINT …` with the expanded list. If no constraint exists, add one (defensive). Probe: `ssh root@VPS docker exec mcp-postgres psql -U mcp -d mcp -c "\d+ client_orgs"` BEFORE pushing mig 321. Cite the actual current allowed values in the migration comment.
- baa_signatures FK NOT NULL feasibility: CONFIRMED. Every new BAA signature row will have a populated client_org_id from the start. Historical rows are quarantined by mig 321's synthetic-row DELETE; if non-synthetic historicals exist in non-prod environments, the mig's backfill UPDATE catches them. The post-backfill `RAISE EXCEPTION` guard at mig step 4 fails-loud if any orphan remains.

### Carol (Production safety / runtime risk)

**APPROVE**:

- The 3-row atomic transaction (client_orgs INSERT + baa_signatures INSERT + signup_sessions UPDATE) is acceptable transaction size — well under PgBouncer's transaction limits, and an `ON CONFLICT DO UPDATE` on a non-hot index (`primary_email`) is microseconds.
- No new lock contention: `client_orgs (primary_email)` is unique-indexed; `signup_sessions (signup_id)` is the PK with FOR UPDATE already held; baa_signatures is INSERT-only.
- **No Python-uuid concern**. UUIDs generated via `uuid.uuid4()` are 122 bits of entropy from `os.urandom()`. Collision probability with `gen_random_uuid()` in Postgres is identical. The DB's PK uniqueness constraint provides the same belt-and-suspenders guarantee either way.
- Webhook idempotency: the new `signup_sessions.client_org_id` column means the Stripe webhook's `_materialize_self_serve_tenant` no longer needs to upsert into client_orgs by primary_email — it can do a direct UPDATE by id. Reduces ambiguity in the partner-led-collides-with-self-serve case (theoretical today; would have been a long-tail bug).
- Failed-checkout dead rows: pruner deferred as P2 task #93-FU-D. Acceptable — 14-day TTL keeps the table bounded.

### Coach (Cross-cutting integrity)

**APPROVE Option E — load-bearing question answered**:

Did I miss a simpler option? Reviewed all 5 (A-E) and a 6th I considered ("(F) leave baa_signatures.client_org_id nullable, add a daily backfill job"). (F) was rejected immediately — leaves the orphan class structurally open during the backfill window + creates a new failure mode (backfill job crash → silent NULL accumulation). (B) is structurally (F).

Is the live signup flow really BAA-first or is that just code drift? Probed. The flow IS BAA-first by deliberate design: see comment at `_materialize_self_serve_tenant:692-694` which explicitly states "When `signup_sessions.baa_signature_id IS NOT NULL`, the BAA is already on file → status flips straight to 'active'." The deliberate design treats BAA-sign as a state-transition trigger for client_orgs, NOT as a hard temporal precondition. Reading the comment alongside the code, the engineer who wrote this was already conceptualizing client_orgs as "the persistent record of the relationship" — moving its materialization earlier (to BAA-sign time) is a clean refinement of the same design, not a contradiction.

Option E is the simplest correct shape. Confirmed.

**FIX-C1 (P1)**: the v1 Gate A's commit-1 sequencing ("mig + writer in same commit, readers in commit 2") is now MORE important. With Option E, the writer change is substantive (~30 lines, not 1 line). Gate B fork MUST verify the full block runs end-to-end on a staging signup before claim-shipped. Add to Gate B checklist.

### Auditor (OCR §164.524 / §164.528)

**APPROVE**:

- Both surfaces unchanged from v1: no PHI moves, no new disclosure accounting events created, no customer-facing surface changes. The reorg is purely internal data model.
- The new `pending_provisioning` status surfaces in customer dashboards as "Setting up your account..." — opaque-safe, no new info leak.

### PM (Effort + sequencing)

**APPROVE with revision**:

- v1 estimate: 1 day. v2 revised: **1.5 days**.
  - mig 321 SQL (2hr) — UNCHANGED from v1, plus +1hr for signup_sessions.client_org_id column + client_orgs status CHECK constraint expansion.
  - `client_signup.py` refactor (~30 lines in sign_baa + ~24 lines idempotency hardening in _materialize_self_serve_tenant): 4hr (up from v1's 1hr 1-line).
  - 3 new pinning tests (test_baa_signatures_has_client_org_id, test_no_baa_signatures_trigger_disable_outside_migrations, test_signup_baa_idempotent_on_email_collision): 3hr.
  - Gate B review: 2hr.
  - **Total**: ~12hr coding + ~2hr Gate B = 1.5 days.
- Commit-1 bundle is now: mig 321 (with new ALTER for signup_sessions + status CHECK) + client_signup.py refactor + 3 new tests. Single commit, single push, single CI run.
- 24h soak before commit 2 (baa_status.py helper migration to FK join) unchanged.

### Counsel (Outside HIPAA — Rule 6 machine-enforcement)

**APPROVE Option E**:

- §164.504(e) "satisfactory assurances before BA performance": the BAA signature is the assurance instrument; performance is the BA acting on PHI. Materializing the BA's *internal record* of the org in the same db transaction as the signature is NOT BA performance — no PHI is in scope yet (status='pending_provisioning', no appliance provisioning code issued, no client_users magic-link). Performance begins at webhook-completion when the provisioning code is issued and the customer is invited to flash an appliance. The 'pending_provisioning' window is administrative, not operational.
- Rule 6 (machine-enforce BAA state): the NOT NULL FK on baa_signatures.client_org_id structurally enforces "every signed BAA points to an org in our system." The renamed-email orphan class is closed FROM THE FIRST INSERT — never enters the system as an orphan, never needs cleanup.
- Stronger than v1: under v1, a BAA signed by an email that hasn't yet had a client_orgs row would be rejected (HTTP 400). That's a hard-fail UX regression for the cold-onboarding path. Option E removes the regression by creating the row at sign-time.
- **APPROVE — proceed**.

---

## CONSOLIDATED VERDICT

**APPROVE Option E** subject to:

- **P0-S1 (Steve)**: pin `test_signup_baa_idempotent_on_email_collision.py` to verify ON CONFLICT returns the existing id.
- **P0-M1 (Maya)**: verify prod `client_orgs.status` CHECK against `pending_provisioning` BEFORE pushing mig 321. Probe command: `ssh root@178.156.162.116 docker exec mcp-postgres psql -U mcp -d mcp -c "\d+ client_orgs"`.
- **P0-C1 (Coach)**: Gate B fork MUST run the curated source-level test sweep (per Session 220 lock-in) AND verify the new sign_baa block runs end-to-end on a staging signup before claim-shipped.
- **P0-Counsel**: cite this v2 Gate A in the mig 321 admin_audit_log row (replace the v1 citation): `'gate_a_artifact', 'audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md'`.
- **P1-PM**: revise task #93 estimate to 1.5 days.
- **P1-Coach (followup)**: add `tests/test_fk_insert_atomicity_documented.py` as a class-prevention gate for future FK-add migrations. Task spinoff.
- **P2-Carol (followup)**: implement `pending_provisioning_orgs_pruner` daily task with 14-day TTL. Task #93-FU-D.

**Claimed migration: 321** (unchanged from v1; SQL block expands to include the new signup_sessions.client_org_id column + client_orgs.status CHECK constraint).

**Sibling task coordination unchanged**: #94 BAA-aware rename helper still trivial post-mig-321 (rename txn issues a new baa_signatures row with new email + existing client_org_id). #95 frontend silent-no-op orthogonal.

---

## V1-GATE-A BLINDSPOT — what the next Gate A should grep for

**Class rule (proposed)**: any Gate A reviewing an FK addition between two append-only OR attestation-bearing tables MUST include a §"INSERT-time atomicity" with the following 4 verifications:

1. **Single-writer parity**: `grep -n 'INSERT INTO {child_table}' --include='*.py'` enumerates every writer of the child. v1 did this. ✓
2. **Parent-writer enumeration**: `grep -n 'INSERT INTO {parent_table}' --include='*.py'` enumerates every writer of the parent. v1 SKIPPED this.
3. **Co-location check**: for EACH (parent_writer, child_writer) pair, verify they execute in the same HTTP request, same db transaction, OR same callable. v1 SKIPPED this.
4. **Temporal ordering rationalization**: if (3) shows different requests/txns, the Gate A MUST address: which fires first? Is the order legally required? Can it be re-ordered? If not, must we introduce a deferred/nullable phase OR a stored-id-in-intermediate-table pattern (Option E)?

Filed as task #93-FU-E (proposed CI gate / Gate A checklist update). Not blocking mig 321 itself.

---

## EVIDENCE TRAIL

- `client_signup.py` flow read end-to-end: `/signup/start` line 232 (signup_sessions only) → `/signup/sign-baa` line 261-328 (baa_signatures INSERT at 299, NO client_orgs reference) → `/signup/checkout` line 331 (Stripe Checkout) → webhook `complete_signup` → `_materialize_self_serve_tenant` line 681-828 (client_orgs INSERT at 717 with ON CONFLICT (primary_email)).
- 3 client_orgs writers enumerated: `client_signup.py:719` (self-serve cold path), `org_management.py:145` (partner-led explicit), `routes.py:4734` (admin create). No others.
- baa_signatures trigger definition verified at `migrations/224_client_signup_and_billing.sql:73-84`: `BEFORE UPDATE OR DELETE FOR EACH ROW` — same finding as v1, unchanged.
- `_materialize_self_serve_tenant:716` line `org_status = "active" if baa_signature_id else "pending"` is the load-bearing evidence that the existing design treats client_orgs as the persistent record of the BAA-bearing relationship, not its precondition. Quoted verbatim above.
- Mig 321 SQL block in v1 Gate A doc: unchanged in v2 except (a) ADD COLUMN for signup_sessions.client_org_id, (b) ADD/MODIFY CHECK on client_orgs.status to include 'pending_provisioning', (c) updated admin_audit_log citation to point at this v2 doc.
- CLAUDE.md "Privileged-Access Chain of Custody" + "Counsel's 7 Hard Rules" (Rule 6) reread: Option E preserves both — no privileged action introduced, BAA state is machine-enforced via FK.
- Session 219 fork-isolation rule + Session 220 Gate-B-must-run-full-sweep rule observed: this Gate A doc is fork-authored (fresh-context, NOT inline counter-args by the author); Gate B will run full sweep per checklist P0-C1.
