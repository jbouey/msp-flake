# E2E Attestation Adversarial Audit — 2026-05-08

**Auditor:** consistency-coach (Principal SWE + Compliance Officer hat)
**Scope:** full attestation chain — code + VPS runtime (`178.156.162.116`)
**Worktree HEAD:** `e2eb1c6c` (deployed runtime sha matches)
**Verdict TL;DR:** Substrate is *architecturally sound* but ships with **two
production chain-integrity ruptures** (1 Merkle worker stalled for 18 days
on the only customer site, 1 attestation-less privileged-fleet-order
backlog) plus **system-wide silent-write-failure debt** that violates the
project's own inviolable invariants. Not enterprise-shippable as is.

---

## §1. Chain map

```
                                                                  ┌────────────────────────────┐
                                                                  │  AUDITOR / CUSTOMER PORTAL │
                                                                  │  /verify/{hash}            │
                                                                  │  /api/.../auditor-kit ZIP  │
                                                                  │  ClientReports / Partner   │
                                                                  └─────────────▲──────────────┘
                                                                                │
                                                                                │  read paths
                                                                                │  (RLS-filtered;
                                                                                │   require_evidence_view_access)
                                                                                │
APPLIANCE (Go daemon, internal/evidence/signer.go)                              │
   ┌──────────────────────────────────────┐                                     │
   │ StateManager → check evidence built  │                                     │
   │ phiscrub @ egress → 14 patterns      │                                     │
   │ Ed25519 sign with /var/lib/msp/      │                                     │
   │   agent-signing-key                  │                                     │
   └─────────────┬────────────────────────┘                                     │
                 │  POST /api/evidence/sites/{site_id}/submit                   │
                 │  (require_appliance_bearer; site_id site_id-mismatch 403)    │
                 ▼                                                              │
BACKEND (FastAPI, evidence_chain.py)                                            │
   ┌──────────────────────────────────────┐    ┌───────────────────────────┐    │
   │ verify Ed25519 against per-appl key  │───▶│ compliance_bundles        │────┘
   │   (site_appliances.agent_public_key, │    │ partitioned by month      │
   │    fall-back: sites.agent_public_key)│    │ chain_position, prev_hash │
   │ INSERT bundle, set                   │    │ chain_hash, agent_signature│
   │   ots_status='batching' or 'pending' │    │ signature (server)         │
   │ background: merkle_batch task        │    │ ots_status, ots_proof      │
   └──────────────────────────────────────┘    │ merkle_batch_id            │
                 │                              └───────────────────────────┘
                 │  hourly per-site
                 ▼
   ┌──────────────────────────────────────┐
   │ process_merkle_batch (evidence_chain)│
   │ collects ots_status='batching'       │
   │ → build Merkle tree → submit_hash_to_│
   │   ots → ots_merkle_batches (pending) │
   │ upgrade loop: pending→anchored when  │
   │   Bitcoin block confirms             │
   └──────────────────────────────────────┘

PARALLEL CHAINS (all anchor in compliance_bundles, check_type ≠ 'drift'):
  • check_type='privileged_access' (privileged_access_attestation.py +
       chain_attestation.emit_privileged_attestation)
       — fleet_orders trigger trg_enforce_privileged_chain (mig 175)
         REJECTS INSERT when parameters->>'attestation_bundle_id' is
         missing for a privileged order_type
       — three-list lockstep: PRIVILEGED_ORDER_TYPES (Python) /
         ALLOWED_EVENTS (Python) / v_privileged_types (SQL trigger)
         enforced by tests/test_privileged_chain_allowed_events_lockstep,
         test_privileged_order_four_list_lockstep
  • check_type='owner_transfer' / 'partner_admin_transfer' /
       'cross_org_relocate' — same shape, anchored at
       client_org's primary site_id (sites WHERE client_org_id=$1
       ORDER BY created_at LIMIT 1) or partner_org:<id> synthetic
  • Operator-alert chain-gap escalation: every privileged hook flags
       attestation_failed → severity escalates to P0-CHAIN-GAP +
       subject suffixed [ATTESTATION-MISSING] (chain_attestation.py
       send_chain_aware_operator_alert)
  • Cross-org relocate: dual-admin propose+approve flag flip
       (feature_flags table, mig 281+282; lower(approver) <>
       lower(proposer) DB CHECK; flag-flip event INTENTIONALLY
       not in ALLOWED_EVENTS — audit lives in feature_flags +
       admin_audit_log)
  • Substrate Integrity Engine: substrate_violations / 11
       invariants every 60s (incl. cross_org_relocate_chain_orphan
       sev1 — bypass-path detector for sites with prior_client_org_id
       set but no completed relocate row)

DETERMINISM (auditor-kit ZIP):
  • _kit_zwrite pins date_time + ZIP_DEFLATED + 0o644<<16
    (auditor_kit_zip_primitives.py)
  • _KIT_COMPRESSLEVEL=6 fixed (zlib level)
  • generated_at derives from chain-head latest.created_at (NOT now())
  • sort_keys=True on every JSON dump
  • kit_version='2.1' pinned across 4 surfaces (header, chain, pubkeys,
    identity, iso_ca)
```

---

## §2. Findings (severity-ranked)

### P0 — legal/compliance blocker

#### F-P0-1 [CODE+RUNTIME] Merkle batch worker stalled — 2,659 bundles unanchored on the only paying site

- **Where:** `compliance_bundles` table + `evidence_chain.process_merkle_batch` (line 2145) + `main.py merkle_batch` background task
- **Code finding:** `evidence_chain.py:2197` `if not ots_result: logger.warning(...) return`. The batching pipeline silently aborts on any OTS-calendar hiccup (network, calendar 5xx). The bundles stay pinned at `ots_status='batching'` indefinitely; there is **no DLQ, no retry-bounded escalation, no Prometheus gauge that would alert** on growing batching-age.
- **Runtime evidence:**
  ```
  SELECT site_id, COUNT(*), MIN(created_at), MAX(created_at)
   FROM compliance_bundles WHERE ots_status='batching' GROUP BY 1;
  --
   site_id               | count | oldest                    | newest
   ----------------------+-------+---------------------------+------
   north-valley-branch-2 |  2659 | 2026-04-20 01:59:19+00    | 2026-05-08 22:25:40+00
  ```
  All 2,659 stuck rows have `merkle_batch_id IS NULL` — they were marked `batching` at INSERT but **never picked up by the batcher**. The most recent log entry for the merkle background task is `bg_task_started` at the current process boot — no `process_merkle_batch` complete-line in the last 5,000 log lines. Worker is functionally stalled.
- **Why P0:** §164.312(c)(1) integrity controls + the customer-facing tamper-evidence promise rely on Bitcoin OTS anchoring. 18 days of unanchored evidence = 18 days the platform has been unable to produce auditor-grade evidence for the only customer site. **The customer would not pass an audit today** if asked to produce timestamped evidence for the period 2026-04-20 → present.
- **Suggested fix:** (a) immediate manual `process_merkle_batch('north-valley-branch-2')` via admin shell; (b) add Prometheus gauge `osiriscare_bundles_unanchored_age_hours{site=...}` with an alert at >6h; (c) escalate the `if not ots_result` path to `logger.error(exc_info=True)` and surface as a substrate invariant `merkle_batch_stalled` (sev1).

#### F-P0-2 [CODE+RUNTIME] Three orphan privileged fleet_orders without attestation; chain-of-custody rule was never retroactively healed

- **Where:** `fleet_orders` rows; mig 175 trigger `trg_enforce_privileged_chain`
- **Runtime evidence:**
  ```
   order_type               | total | with_attestation
   -------------------------+-------+-----------------
   disable_emergency_access |   1   |        0
   enable_emergency_access  |   2   |        0
  ```
  All 3 rows are dated 2026-04-11 and 2026-04-13 — **inserted ~2 hours BEFORE migration 175 was applied** (`schema_migrations` shows 175 applied 2026-04-13 09:01). All 3 orders are for `north-valley-branch-2`. All 3 have empty `actor` (the named-human-actor invariant violated historically).
  Cross-check: `SELECT COUNT(*) FROM compliance_bundles WHERE check_type='privileged_access'` returns **0** — there is **not a single privileged_access bundle in production**.
- **Code finding:** No backfill script in `scripts/` to retroactively chain-link these orphan orders. `trg_enforce_privileged_chain` is a **pre-INSERT trigger**, so it cannot heal historical rows. The CLAUDE.md rule says "breaking the chain is a security incident, not a cleanup task" — but the cleanup path for historical rows that pre-date the trigger is undocumented.
- **Why P0:** The Privileged-Access Chain-of-Custody rule is labeled **INVIOLABLE** in CLAUDE.md and references `docs/security/emergency-access-policy.md`. Three rows of attestation-less privileged action against a customer's appliance is exactly what that rule exists to prevent. An auditor opening the kit today will see 3 fleet_orders that match the privileged catalog with no corresponding attestation bundle — that is forensically indistinguishable from "operator backdoored the customer and forged the audit trail."
- **Suggested fix:** ship a one-shot `scripts/backfill_privileged_attestation.py` that walks the orphan rows, prompts for human actor + reason, writes `check_type='privileged_access'` bundles with `chain_position` correctly grafted (or uses a synthetic `event_type='backfill_pre_mig175'` outside ALLOWED_EVENTS but recorded in admin_audit_log), and updates `parameters->>'attestation_bundle_id'`. Documented disclosure in the auditor kit advisories/ folder.

#### F-P0-3 [CODE+RUNTIME] Silent-write-failure debt in the attestation hot path

- **Where:**
  - `privileged_access_attestation.py:471` — `logger.warning(f"admin_audit_log mirror failed for {bundle_id}: {e}")` after a DB INSERT failure
  - `evidence_chain.py:1156` — `logger.warning(f"Evidence rejection tracking failed for site {site_id}: {e}")` after a UPDATE failure
  - `evidence_chain.py:1190-1191` — bare `except Exception: pass` after `UPDATE site_appliances SET agent_public_key = :key`
  - `evidence_chain.py:1217` — same shape
  - 30+ `logger.warning` instances across this file alone
- **Code finding:** CLAUDE.md inviolable rule: *"`logger.warning` on DB failures BANNED → `logger.error(exc_info=True)`. `except Exception: pass` on DB writes BANNED."* All four sites above directly violate this. The attestation hot path is exactly where this rule matters most — a swallowed exception here is the difference between `signature_valid=true` being reliable and being a lie.
- **Runtime evidence:** `docker logs --tail 5000 mcp-server | grep -c "InFailedSQLTransactionError"` → **171 occurrences** in 5,000 lines, all in `prometheus_metrics.py` (lines 519, 549, 577, ... 1270 — 18 distinct line numbers). The `admin_transaction()` block holds 48 sequential reads, each in its own `try/except` but **without per-query `conn.transaction()` savepoints** — so when one fetch errors, every subsequent fetch in the same transaction returns `InFailedSQLTransactionError` and the metric reports 0 silently. This is the same class of failure CLAUDE.md "Checkin savepoints" rule ships defenses for in `sites.py` — but `prometheus_metrics.py` was missed.
- **Why P0:** Silent zero-row metrics on the prom dashboard mean operators *cannot trust the dashboard* during incidents — exactly when the chain-gap escalation rule needs the dashboard to be honest. CLAUDE.md flags this as a security boundary issue: *"No silent write failures."*
- **Suggested fix:** wrap each `await conn.fetch/fetchval/fetchrow` in `prometheus_metrics.py` and the four hot-path sites in `evidence_chain.py` / `privileged_access_attestation.py` with `async with conn.transaction():` savepoints; promote `logger.warning` on DB failures to `logger.error(exc_info=True)`; add `tests/test_no_logger_warning_on_db_writes.py` AST gate to make this a CI ratchet.

### P1 — chain integrity / correctness

#### F-P1-1 [CODE+RUNTIME] Site-level `agent_public_key` fallback contradicts the per-appliance signing-key rule

- **Where:** `evidence_chain.py:1078,1082,1083-1090` + `sites.agent_public_key` column still populated in production
- **Code finding:** Fallback sequence in `submit_evidence` is: per-appliance key → submitted key → **site-level key** → auto-register at site level. The CLAUDE.md "Per-appliance signing keys (Session 196)" rule says: *"`site_appliances.agent_public_key` — NOT `sites.agent_public_key`. Multi-appliance sites MUST NOT use the single site-level key."* The auto-register branch at lines 1083-1090 actively writes `UPDATE sites SET agent_public_key = :key` — i.e. it perpetuates the legacy shape on every fresh site.
- **Runtime evidence:**
  ```
   site_id                         | key_prefix
   --------------------------------+-----------------
   north-valley-branch-2           | e39d1ac5f65ba71a
   physical-appliance-pilot-1aea78 | 9f7b1132e47b113d
  ```
  Both production sites still carry a site-level key. Ed25519 verification on multi-appliance sites can therefore validate against the wrong key without raising — a layer-2 attack surface.
- **Why P1:** Today only 5 site_appliances exist (4 with per-appliance keys, 1 without — which falls through to the site-level key). Once the customer base scales the per-site assumption silently breaks.
- **Suggested fix:** drop the site-level fallback (raise 401 if no per-appliance key matches); migrate `sites.agent_public_key` to a deprecated marker; update mig to NULL the column on reads; add `tests/test_no_site_level_signing_key_fallback.py`.

#### F-P1-2 [RUNTIME] Substrate-integrity engine flags `rename_site_immutable_list_drift` (sev2) — `cross_org_site_relocate_requests` table NOT in immutable list

- **Where:** substrate invariant `rename_site_immutable_list_drift`; `_rename_site_immutable_tables()` SQL function
- **Runtime evidence:**
  ```
  SELECT * FROM v_substrate_violations_active WHERE invariant_name='rename_site_immutable_list_drift';
   severity | minutes_open  | details (drift_tables)
   sev2     | 3,975 (66h)   | ["cross_org_site_relocate_requests"]
  ```
  The substrate's own integrity engine has been screaming for 66 hours that `cross_org_site_relocate_requests` has a DELETE-blocking trigger AND a `site_id` column AND is NOT in the rename-immutable list. **`rename_site()` will rewrite its `site_id` columns** — for a chain-of-custody table — and nobody has acted on the alert.
- **Why P1:** This is precisely the bypass-path detector working as designed — and being ignored. A `rename_site()` call against a site mid-relocate would silently corrupt the cross-org chain.
- **Suggested fix:** ship the immediate one-line migration adding `cross_org_site_relocate_requests` to `_rename_site_immutable_tables()`. Add an MTTR SLA on substrate sev2 invariants (e.g. 24h to-acknowledge) — **6 violations open for >19,000 minutes total** indicates the engine is observed-but-not-actioned.

#### F-P1-3 [CODE] `prometheus_metrics.py` admin_transaction lacks per-query savepoints

- **Where:** `prometheus_metrics.py:117-1299`
- **Code finding:** see F-P0-3 detail. The fix is structurally simple but invasive (~48 wraps).
- **Why P1:** Metrics surface is not the chain itself, but the operator-visibility surface; chain-gap detection depends on it.

### P2 — operational verifiability

#### F-P2-1 [CODE+RUNTIME] `admin_audit_log` empty for cross-org relocate flag-flip and propose/approve

- **Where:** `cross_org_site_relocate.py:1448, 1547` + `admin_audit_log` table
- **Code finding:** `propose_enable` and `approve_enable` write to admin_audit_log with action `propose_enable_cross_org_site_relocate` / `approve_enable_cross_org_site_relocate`. The flag is currently `enabled=false` so these are unexercised — but the runtime audit log shows **0 rows** with action `LIKE 'PROPOSE%'` or `LIKE '%RELOCATE%'`. A pre-flight call from staging or a one-off canary test would confirm the audit-write path actually executes (the code currently lives behind a 503 which the test wouldn't reach).
- **Why P2:** Untested-in-prod write paths are exactly how `actor`-vs-`username` column drift bugs ship (CLAUDE.md cites two such regressions in commits `331b7d29`, `24613c15`).
- **Suggested fix:** add a "dry-run" or smoke-test endpoint that exercises the audit write inside a rolled-back transaction — proves the column shape is right without flipping the flag.

#### F-P2-2 [CODE] `signing_backend` import has dual-path try/except — fragile

- **Where:** `privileged_access_attestation.py:348-351`
  ```python
  try:
      from .signing_backend import get_signing_backend, SigningBackendError
  except ImportError:
      from signing_backend import get_signing_backend, SigningBackendError
  ```
- **Why P2:** Hides import errors. If a refactor renames the module, the fallback masks the failure until production. The same pattern appears in 3+ other files (test-vs-prod path divergence).
- **Suggested fix:** standardize on the package-relative import; let the fallback path die. Install pytest config that puts `backend/` on `sys.path` so the prod path works in tests.

#### F-P2-3 [CODE] `_get_prev_bundle` uses `ORDER BY checked_at DESC LIMIT 1` — race-prone for concurrent attestations

- **Where:** `privileged_access_attestation.py:289-300`
- **Code finding:** Two concurrent privileged attestations on the same site read the same `prev_hash` and produce two bundles with identical `chain_position` + `prev_hash`. There is no SERIALIZABLE isolation, no `SELECT ... FOR UPDATE`, no advisory lock. Privileged events are rare today, but the same chain anchor is shared with drift bundles (every checkin), so the race window is real.
- **Why P2:** Latent. `compliance_bundles_pkey` is `(id)` not `(site_id, chain_position)`, so the duplicate slips in. Detected only on chain-walk verify. The user-facing symptom is "verify.sh diverges" — a credibility hit.
- **Suggested fix:** add a UNIQUE INDEX `(site_id, chain_position)` (with the partition caveat: indexed at the partition level), and use `INSERT ... ON CONFLICT (site_id, chain_position) DO NOTHING RETURNING bundle_id` — the loser retries with a fresh prev. Or: pg_advisory_xact_lock(hashtext('attest:'||site_id)) inside the helper.

### P3 — drift / hygiene

- **F-P3-1** [RUNTIME] `schema_fixture_drift` substrate sev3 open 59h on `orders.error_message` — fixture stale.
- **F-P3-2** [RUNTIME] `journal_upload_never_received` sev3 open 78h on north-valley-branch-2 — appliance lacks `msp-journal-upload.timer`. ISO drift class.
- **F-P3-3** [RUNTIME] `install_session_ttl` sev3 open 321h — 3 expired install sessions not pruned.
- **F-P3-4** [CODE] `evidence_chain.py:1190` and `:1217` bare `except Exception: pass` — see F-P0-3 family.
- **F-P3-5** [CODE] `chain_attestation.py:113-134` distinguishes `PrivilegedAccessAttestationError` from generic `Exception` only by log message — both return `(True, None)` and downstream callers can't tell whether the failure was deterministic (key missing) or transient (DB hiccup) → no policy difference in retry vs abort. Smell, not bug.
- **F-P3-6** [RUNTIME] `compliance_bundles` Jan 2026: 100,972 rows in `ots_status='legacy'` — historical Merkle-batch bug per Session 203 C1. Now backfilled correctly but auditor-kit consumers will see `legacy_count` non-zero forever; ensure the README explains it.

---

## §3. What's strong (don't lose it)

1. **Three-list lockstep tests** (`test_privileged_chain_allowed_events_lockstep` + `test_privileged_order_four_list_lockstep` + `test_three_list_lockstep_pg`) — comprehensive AST + DB-shape gates. ALLOWED_EVENTS has 60 entries, all enumerated with comments.
2. **Auditor-kit determinism contract** — `_kit_zwrite` + `sort_keys=True` + `_KIT_COMPRESSLEVEL=6` + `generated_at` from chain-head bundle is rigorous; pinned by `tests/test_auditor_kit_deterministic.py` AND `tests/test_auditor_kit_integration.py` (10 tests open the actual ZIP).
3. **`require_evidence_view_access` 5-branch auth chain** — admin / client-cookie / partner-cookie+role / portal-session / legacy-token; partner-billing-role excluded; per-(site, caller) rate-limit isolated.
4. **`mig 175` privileged-chain trigger** — pre-INSERT REJECT with `RAISE EXCEPTION` on missing attestation_bundle_id is the right shape; v_privileged_types (11 entries) is a strict subset of ALLOWED_EVENTS (60) per the asymmetry-allowed lockstep checker.
5. **Cross-org relocate dual-admin flag flip** — `feature_flags_dual_admin_check` CHECK constraint at the DB level (`lower(approver) <> lower(proposer)`, `length(reason)>=40`) is defense-in-depth done right.
6. **`appliance_id`-mismatch evidence rejection counter** — refusing to pollute siblings (line 1144-1153) when the offending appliance is unidentifiable is a thoughtful invariant.
7. **Substrate Integrity Engine** is firing — F-P1-2 was caught by the engine, not by an external auditor. The system is observing itself.

---

## §4. What's missing evidence (couldn't verify)

- **`/api/admin/substrate-health`** — couldn't auth from this audit context. Verified via direct `v_substrate_violations_active` SELECT instead.
- **Auditor-kit ZIP byte-identity test in production** — couldn't pull two consecutive kits without a session cookie. The determinism unit test `test_auditor_kit_integration.py` is a strong proxy but doesn't exercise the full DB path.
- **Bitcoin txid/block confirmation chain** — sampled `ots_status='anchored'` rows but didn't independently verify against a Bitcoin node. Trust the OTS upgrade-loop logic for now.
- **Go appliance `internal/evidence/signer.go` test coverage** — read but didn't run the Go test suite from this audit context.
- **`/api/version` direct hit** — outbound TLS from this dev box returned `tlsv1 unrecognized name`; verified via `docker exec mcp-server curl localhost:8000/api/version` instead → `runtime_sha=disk_sha=e2eb1c6c, matches=true`.

---

## §5. Round-table queue — 5–8 priority items for Auditor + PM

> **Hand this verbatim to the parent for the round-table.**

1. **F-P0-1 — Manual unstall the Merkle batcher on north-valley-branch-2 (2,659 stuck bundles, 18 days unanchored) + ship a `bundles_unanchored_age_hours` Prom alert.** This is a *today* action, not a sprint item. The customer would not pass §164.312(c)(1) integrity audit for the period.
2. **F-P0-2 — Backfill the 3 attestation-less privileged fleet_orders or write a public disclosure into auditor-kit advisories/.** Legal-compliance must decide: heal the chain retroactively (with a synthetic backfill event_type) OR disclose openly that the trigger went live mid-stream and 3 historical rows are forensically untraceable. Either is defensible; silence is not.
3. **F-P0-3 — Ratchet `logger.warning`-on-DB-write to `logger.error(exc_info=True)` and ban `except Exception: pass` on write paths via CI gate.** 30+ existing violations in `evidence_chain.py` alone. The fix is mechanical; the cost of *not* fixing is exactly the silent-zero-row class CLAUDE.md was written to prevent.
4. **F-P0-3 (sub) — Wrap every `prometheus_metrics.py` admin_transaction read in a per-query savepoint.** 171 `InFailedSQLTransactionError` in 5,000 log lines = the metrics dashboard is lying to operators. Highest leverage / lowest risk fix in this audit.
5. **F-P1-1 — Drop `sites.agent_public_key` fallback from `submit_evidence`.** The fallback contradicts the Session-196 per-appliance-key rule and provides no real value (auto-register can target `site_appliances` directly). Add CI gate `tests/test_no_site_level_signing_key_fallback.py`.
6. **F-P1-2 — Add `cross_org_site_relocate_requests` to `_rename_site_immutable_tables()` (one-line migration).** The substrate integrity engine has been alerting on this for 66 hours. Resolving this also closes the meta-question: *"What's our SLA on substrate sev2 invariants?"* — which itself deserves a round-table item.
7. **F-P2-3 — Race-harden `_get_prev_bundle` with UNIQUE(site_id, chain_position) + ON CONFLICT loop OR pg_advisory_xact_lock.** Rare today, certain at scale. Cheap to fix now.
8. **Process — Ratchet substrate-violation MTTR.** 4 violations open. Total cumulative open time **>32,500 minutes (>22 days)**. The engine is healthy; the response loop is not.

---

## §6. Final readiness verdict

**CONDITIONAL — NOT enterprise-shippable today.**

Architecture is sound and several invariants (lockstep, determinism, dual-admin flag flip, anchor namespace) are class-leading. But the **production runtime is in chain-rupture state on the only paying customer** (Merkle worker 18d stalled) AND ships pre-existing chain-of-custody holes (3 attestation-less privileged orders) AND violates its own inviolable silent-write rules in 30+ places.

A HIPAA auditor pulling the kit today for north-valley-branch-2 would see:
- ~2,659 bundles in `ots_status='batching'` with no Merkle proof, no Bitcoin anchor, no calendar URL — i.e. **18 days of evidence with no tamper-evidence layer at all**;
- 3 privileged fleet orders with no attestation_bundle_id;
- 0 rows of `check_type='privileged_access'` for a site that demonstrably had emergency access enabled twice and disabled once.

That's a finding. It's recoverable (manual batch run + backfill + disclosure) but it MUST be recovered before claiming "audit-supportive technical evidence" to a paying customer.

The §6 verdict ratchets to **READY** when:
1. F-P0-1 closed (live batching=0, alert in place);
2. F-P0-2 closed (backfill OR public disclosure);
3. F-P0-3 closed (logger.warning DB ratchet + savepoints);
4. Substrate sev2 MTTR <24h proven for two consecutive weeks.

— consistency-coach, 2026-05-08
