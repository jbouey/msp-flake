# Session 218 (2026-05-05 / 2026-05-06)

Two themes: (1) RT33 portal observability + ghost-data cleanup; (2) RT21
cross-org site relocate shipped behind an outside-counsel-gated feature flag.

---

## RT33 — Portal appliance visibility + ghost-data + auditor-kit perf

User observation: "On central command we see all appliances; client portal
has no representation of those appliances and it has to be scaleable for
large enterprises on client portal/partner portal in relevant ways" + "north
valley branch is the only real site on central command so theres ghost data
on client portal and likely partner portal — auditor kit page is still
taking long to load as well".

**Round-table 33** (PM Linda + Sam Principal SWE + Carol CCIE + Dana DBA +
Steve Sec + Adam Perf + Maya Consistency) decomposed the cluster into 5
phases. Each phase shipped as its own commit with adversarial 2nd-eye
review.

### P1 — ghost-data filters (commit `f8e10f4c`)

`client_portal.py:839` LEFT-JOINed `site_appliances` without `deleted_at IS
NULL`, polluting `MAX(sa.last_checkin)` with dead-appliance timestamps.
`client_portal.py:826` site list missed `s.status != 'inactive'`. Same
defect repeated in 6 client + 8 partner queries. Maya 2nd-eye caught
`partners.py:1079` in the initial sweep — gate extended to scan both
files.

CI gate `test_client_portal_filters_soft_deletes.py` pins the filter on
the JOIN line itself (not a continuation line — anchor robustness rule).
Carve-out for the historical-IP exclusion list documented inline.

### P2 — `/api/client/appliances` endpoint (commit `d4a1e61d`)

Read-only org-scoped appliance list for the client portal. Field allowlist
excludes mac/ip/daemon_health (Carol veto: customer session must not
become a Layer-2 recon map).

**Steve VETO** at 2nd-eye: the initial implementation read from
`appliance_status_rollup` MV which bypasses RLS (PG MVs don't inherit
base-table policies). Rewrote to query `site_appliances` directly with
an inline LATERAL heartbeat join — same `live_status` semantics as the
MV but with proper `tenant_org_isolation` enforcement. Trade ~1ms per
query for defense-in-depth.

**Maya REQUEST CHANGES**: the component had a local `formatRelative`
helper duplicating canonical `formatTimeAgo` from `constants/status.ts`.
Replaced with the canonical helper. (DRY ratchet — `formatTimeAgo` is the
"Replaces 14+ duplicate implementations" canonical.)

### P3 — `/api/partners/me/appliances` fleet view (commit `005f9865`)

Cross-site fleet roll-up answering "which appliances are offline across my
book" — the question Linda flagged as the #1 partner-admin 3am incident
question. Cursor pagination cap 100, server-side filters (status, site_id).
Operator-class fields included (mac, l2_mode) since partners are operator-
class unlike substrate-class clients.

**Dana REQUEST CHANGES** at 2nd-eye: the initial draft had two separate
queries (paginated rows + aggregate counts) each running the LATERAL
heartbeat join. For a 200-site partner × 3 appliances = 1200 LATERAL
evaluations per page load. Refactored to a single CTE shape where the
base computes `live_status` once and the summary aggregates over the
unfiltered base via subqueries.

### P4 — auditor-kit `StreamingResponse` (commit `b3d344e2`)

`evidence_chain.py::download_auditor_kit` materialized the entire ZIP
(`buf.getvalue()`) before returning — time-to-first-byte was zip-build
time. Switched to `tempfile.SpooledTemporaryFile` (≤20MB in RAM, spills
to disk above) + `StreamingResponse` with an async generator that
offloads the synchronous `buf.read` to a thread.

**Adam VETO** at 2nd-eye: a sync generator on an async-def endpoint
blocks the event loop on every read, starving concurrent requests on
slow client networks. Fixed by making the generator `async def` + using
`asyncio.to_thread(buf.read, 64 * 1024)`.

### P5 — poll-interval constants (commit `cd539ba1`)

Hoisted `POLL_INTERVAL_CLIENT_MS=30_000` + `POLL_INTERVAL_PARTNER_MS=15_000`
from two component files into `constants/polling.ts`. Maya parity rule
("DRY: one config, many consumers").

### Hotfix — Python 3.11 deploy outage (commit `097863a5`)

Three RT33 deploys (P1, P2, P3) failed in CI at the test-collection
step:

```
File ".../test_client_portal_filters_soft_deletes.py", line 80
  bad.append(f"{portal_file.name}: {re.sub(r'\\s+', ' ', block)[:180]}")
                                                                          ^
SyntaxError: f-string expression part cannot include a backslash
```

**Local Python is 3.13/3.14; CI is 3.11.** 3.11 doesn't accept backslashes
inside f-string expressions; 3.12+ does. Pre-push gates ran on local Python
and missed it. The user saw "deploys not going through" — three failures.

Fix: pre-push now compiles every backend `.py` through `python3.11` (if
available) before push. Adds ~1s. Catches the same class deterministically.
The deploys unblocked on the next push.

**Lesson:** language-version drift between local and CI is a real failure
mode. Either run CI's exact Python locally OR run an equivalent compile
pass.

---

## RT21 — Cross-org site relocate (commit `be980fc7`)

Pre-ship: `sites.py:1938` returned 403 on cross-org relocate with a
"coming soon" comment. Real demand: clinic acquired by hospital network
(client_org change), clinic switches MSP (partner_id swap is a different
endpoint), clinic merger.

**Round-table** had been documented in
`.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md` since
2026-05-05 with all five personas APPROVE_DESIGN. Blocker was outside HIPAA
counsel review on three §-questions, NOT engineering. PM call to ship the
engineering behind a feature flag that itself gates on counsel sign-off.

Re-convened the round-table with two new personas for adversarial
implementation review:
- Patricia — HIPAA counsel surrogate
- Marcus — healthcare regulatory engineer (HIPAA Security Rule)
- Linda — PM

### Three-actor state machine (mig 279)

```
pending_source_release   →release→  pending_target_accept
pending_target_accept    →accept→   pending_admin_execute (24h cooling-off)
pending_admin_execute    →execute→  completed
                                    sites.client_org_id flipped,
                                    sites.prior_client_org_id set
any pending              →cancel→   canceled
any pending              →expires_at passed→ expired
```

Each transition writes an Ed25519 attestation bundle. 6 lifecycle events
added to `ALLOWED_EVENTS` (49 → 55).

### Migrations

- **279** — `cross_org_site_relocate_requests` table. 6-state CHECK,
  partial unique index on `(site_id) WHERE status IN pending`,
  append-only DELETE trigger, magic-link token hash columns,
  `expected_*_email` pinning columns (Patricia rule), cooling-off
  CHECK constraint (Marcus rule).
- **280** — `sites.prior_client_org_id` UUID column (nullable, FK
  `client_orgs(id)`). The cryptographic chain stays anchored at the
  ORIGINAL site_id forever (Brian Option A); auditors walk across the
  org boundary via this column.
- **281** — `feature_flags` table. Patricia's attestation-gated toggle
  pattern. CHECK enforces enable_reason ≥40ch (legal-opinion identifier
  goes here). Append-only via DELETE trigger. Seed row inserted with
  `enabled=false`.

### Adversarial verdicts addressed in-line

**Gate 1 (schema):**
- Marcus VETO — nullable `cooling_off_until` is a footgun. Added CHECK
  constraint: `status NOT IN ('pending_admin_execute','completed') OR
  cooling_off_until IS NOT NULL`.
- Patricia REQUEST CHANGES — UUID-format CHECK on `enable_attestation_
  bundle_id`. Added regex CHECK; later DROPPED at Gate 2 (see below).

**Gate 2 (endpoint):**
- Patricia P0 — initiate response leaked plaintext magic-link tokens
  (`_v1_*_link_token`). Removed entirely; email delivery only. Until
  Phase 3 emails wire, the magic links are unreachable — which IS the
  feature flag's safety property.
- Patricia P1 — multi-owner attribution gap (`LIMIT 1` over owners is
  arbitrary). Added `expected_source_release_email` + `expected_target_
  accept_email` columns persisted at initiate; redeemer endpoints
  verify the pinned email is still an active owner (defense in depth
  across email rename).
- Marcus P0 (FK) — `enable_cross_org_site_relocate` event tried to write
  a privileged_access bundle but `compliance_bundles.site_id` FKs to
  `sites(site_id)`. The flag-flip has no natural site anchor.
  Synthetic anchors fail FK; per-site fan-out (the
  `fleet_healing_global_pause` pattern) is heavy for a rare event.
  **Dropped the event from ALLOWED_EVENTS entirely.** Audit lives in:
  (1) `feature_flags` table itself — append-only, records actor +
  reason ≥40ch + timestamps + the parallel disable triplet; (2)
  `admin_audit_log` row written on every toggle. The asymmetry vs
  other privileged events is documented inline.
- Marcus P1 (race) — execute UPDATE on `sites` didn't filter by
  current `client_org_id`. Two simultaneous admins reading
  `pending_admin_execute` could both flip + record `executor_email +
  executed_at`, muddying the audit trail. Added `WHERE
  client_org_id = $source` guard + UPDATE row-count check (409 if
  no-op transition).

**Maya final 2nd-eye APPROVE** — verified PARITY with mig 273/277,
three-list lockstep clean (ALLOWED_EVENTS ⊇ PRIVILEGED_ORDER_TYPES +
v_privileged_types per asymmetry rule), `chain_attestation.emit_
privileged_attestation` DRY delegation, no banned language,
`datetime.now(timezone.utc)` not `utcnow`, invariant triplet (check +
display_name + recommended_action) all in lockstep, migration ordering
idempotent.

### Substrate invariant `cross_org_relocate_chain_orphan` (sev1)

Bypass-path detector: any site with `prior_client_org_id` set but no
completed `cross_org_site_relocate_requests` row attesting the move is
a chain-of-custody gap. Catches direct UPDATE shortcuts, accidental
backfills, regressions in other endpoints. Sev1 because §164.528
disclosure-accounting integrity is on the line. Runbook at
`substrate_runbooks/cross_org_relocate_chain_orphan.md`.

### Deferred to v1.1 (Marcus Gate 2 P1)

- Source/target owner cancel via magic-link (v1 admin-only cancel).
- Email infra wiring (Phase 3 follow-up; without it the magic links are
  unreachable, which is the design's safety property until counsel
  approves).

### Counsel preconditions (legal review, async, multi-day)

The flag stays disabled until outside HIPAA counsel signs off on:
1. §164.504(e) permitted-use scope under both source-org and target-org
   BAAs (regardless of vendor identity). This was originally framed as
   "BA-to-BA inapplicability under same-substrate-BA" in the v1 packet
   but counsel's adversarial review (2026-05-06) flagged that framing
   as attackable; v2 reframes as permitted-use scope under each BAA.
2. §164.528 substantive completeness + retrievability of the disclosure
   accounting record (originally framed as "chain immutability is
   stronger than the standard"; counsel's correction: the legal test is
   content + producibility, not log tamper-resistance).
3. Receiving-org BAA scope (does the standard substrate-BAA cover
   received clinics from prior orgs, or is successor / continuity /
   addendum language required — counsel's likely commercial choke point).
4. (NEW v2) Opaque-mode email defaults — confirm the cheap safer
   alternative (no clinic/org names in email plaintext) is acceptable,
   or direct us to ship verbose-mode templates instead.

When counsel returns, the legal-opinion identifier goes in the ≥40-char
`enable_reason` field at flag-flip time. After mig 282 + the
counsel-revision split (commit f2bba323), this is the APPROVER's reason
field; the proposer's separate ≥20-char reason captures the operational
trigger. Two distinct admin attestations are required and the schema
CHECK enforces approver != proposer at the DB layer.

---

## What durable rules came out of Session 218

Added to CLAUDE.md Rules:

1. **Portal site_appliances + sites filters** — RT33 P1 anti-regression.
2. **Portal appliance endpoints query site_appliances directly** — RT33 P2
   Steve veto. PG MVs bypass RLS.
3. **Pre-push python3.11 syntax check** — RT33 deploy-outage class. Local
   Python diverges from CI Python; explicit syntax check prevents.
4. **Cross-org site relocate** — RT21 ship-behind-counsel-gate pattern.

5 commits on RT33 + 1 hotfix + 1 commit on RT21 = 7 commits. All shipped
green at runtime-SHA-verified enterprise-grade bar.
