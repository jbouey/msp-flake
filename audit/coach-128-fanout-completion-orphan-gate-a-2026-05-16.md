# Gate A — #128 fleet_order_fanout_partial_completion invariant
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: APPROVE-WITH-FIXES

## Schema verification

- **fleet_order_ids persistence — VERIFIED.** `fleet_cli.py:543-559` writes `details = details || jsonb_build_object('fleet_order_ids', $1::jsonb)` against `admin_audit_log` rows matched by `(details->>'bundle_id') = $2`. This is the ONLY producer in repo (grep confirms). The cross-link is best-effort (wrapped in try/except → stderr WARNING) — invariant must tolerate its absence gracefully (treat as 0-fan-out, i.e. SKIP, NOT alert).
- **`admin_audit_log` shape (prod_columns.json):** `[action, created_at, details, id, ip_address, target, user_id, username]`. No `site_id` column — the invariant must extract site context from `details->>'bundle_id'` → JOIN `compliance_bundles` (which has site_id).
- **`fleet_order_completions` shape (prod_columns.json + mig 049 + mig 172):** PK `(fleet_order_id, appliance_id)` — **per-order-per-appliance**, NOT per-order. CRITICAL: the proposed `LEFT JOIN ... WHERE foc.id IS NULL` from the sketch is wrong — there is NO `foc.id` column. Correct shape: `LEFT JOIN ... WHERE foc.fleet_order_id IS NULL`. Status enum: `acknowledged | completed | failed | skipped`. `skipped` rows mean the appliance was at `skip_version` already — that IS a successful ack, must count as completion. `failed` rows mean the appliance tried + reported failure — different bug class (`enable_emergency_access_failed_unack` sev2 at assertions.py:6272 already covers this).
- **`fleet_orders` shape:** id, order_type, parameters JSONB, status (`active|completed|cancelled`), created_at, expires_at, signed_payload, signature, nonce, signing_method. `parameters->>'target_appliance_id'` carries the per-iteration appliance target. `parameters->>'attestation_bundle_id'` carries the cross-link to the bundle. Indexes: `idx_fleet_orders_active(status, expires_at) WHERE status='active'`, `idx_fleet_completions_appliance(appliance_id)`. **NO index on `fleet_order_completions(fleet_order_id)` alone** — PK covers it (leading column).
- **6h threshold:** daemon ack cadence is 60s heartbeat + pull. Mig 161 explicitly notes `failed` rows auto-DELETE after 1h to allow retry. 6h is conservative: it means the appliance has missed ~360 pull cycles. Reasonable for "appliance is offline OR daemon is wedged." Could tighten to 2h but legitimate planned-maintenance offline windows can exceed 1h; 6h leaves the operator inbox quiet for those.

## Query design

Recommended shape (corrects parent Gate B sketch on `foc.id` → uses correct PK semantics):

```sql
WITH fan_out_bundles AS (
    SELECT al.details->>'bundle_id'                        AS bundle_id,
           jsonb_array_elements_text(al.details->'fleet_order_ids') AS fleet_order_id,
           jsonb_array_length(al.details->'fleet_order_ids')        AS fan_out_size,
           al.created_at
      FROM admin_audit_log al
     WHERE al.created_at > NOW() - INTERVAL '24 hours'
       AND al.action = 'privileged_access_attestation_chain'  -- narrow scan
       AND al.details ? 'fleet_order_ids'
       AND jsonb_array_length(al.details->'fleet_order_ids') > 1
)
SELECT fob.bundle_id,
       fob.fan_out_size,
       COUNT(*)                  AS unacked_orders,
       array_agg(fob.fleet_order_id ORDER BY fob.fleet_order_id) AS unacked_order_ids,
       cb.site_id
  FROM fan_out_bundles fob
  LEFT JOIN fleet_orders fo
         ON fo.id::text = fob.fleet_order_id
        AND fo.status = 'active'           -- skip cancelled
  LEFT JOIN fleet_order_completions foc
         ON foc.fleet_order_id = fo.id
        AND foc.status IN ('completed','acknowledged','skipped')
  LEFT JOIN compliance_bundles cb
         ON cb.bundle_id = fob.bundle_id
 WHERE fob.created_at < NOW() - INTERVAL '6 hours'
   AND foc.fleet_order_id IS NULL
   AND fo.id IS NOT NULL                   -- order exists (skip pruned rows)
 GROUP BY fob.bundle_id, fob.fan_out_size, cb.site_id
 LIMIT 100
```

**Perf concerns:**

- `admin_audit_log` 24h scan + `?` operator + `jsonb_array_elements_text` is the hot path. Existing indexes: `idx_admin_audit_user`, `idx_admin_audit_action`, `idx_admin_audit_created` — `idx_admin_audit_created` IS a btree on `created_at`, so the time-window predicate prunes. **NEEDED narrowing:** add `AND al.action = 'privileged_access_attestation_chain'` (or whatever the exact action string is — verify from `privileged_access_attestation.py`); the action index then collapses the scan to N=privileged-events-per-24h (today: <20 fleet-wide).
- `jsonb_array_length > 1` filter eliminates N=1 cases (single-target orders) which is the majority by row count.
- `LEFT JOIN fleet_order_completions` uses the PK leading column → index scan, not seq scan. No new index required.
- 24h `admin_audit_log` row count: rough estimate ~5K-50K on enterprise scale (sessions, magic-link emits, attestations, transfer events). `details ?` on JSONB has a built-in optimization; with the `action` predicate the scan is bounded to <100 rows in practice. Acceptable.
- **Caveat:** without the `action` narrowing, a future audit-log volume spike could push this to seconds. Lens recommendation: pin the action enum in the WHERE clause.

## Counsel + sibling pattern

- **Rule 4 (orphan coverage):** directly addresses fan-out tail orphans. ✓ Closes a real gap left explicit in #118 Gate B (the "P2 follow-up" — this IS that work).
- **Sibling parity (`fleet_order_url_resolvable`):** registered at sev1. URL-resolvable is sev1 because a dead URL means the canary loops forever, burning DNS + CPU + alerting; the fan-out orphan is a different shape (silent partial success, no canary loop, operator-discoverable via `fleet_cli list`).
- **Sev choice — Gate A recommends sev2, NOT sev3:** the parent Gate B sketch said sev3 but the better fit is **sev2**. Reasoning: (a) the orphan-coverage class (Counsel Rule 4) is "sev1 not tolerable warning" per CLAUDE.md gold authority — sev3 understates; (b) the sibling `enable_emergency_access_failed_unack` covers the EXPLICIT-failure case at sev2, this is its silent twin and should match; (c) a 30%-silent fan-out of `enable_emergency_access` means privileged orders the operator BELIEVES are issued aren't running — that's an attested-chain visibility gap, not pure ops noise; (d) sev3 sits below the panel-attention threshold in current SIE surfacing, defeating the visibility purpose. Sev1 is also defensible per Rule 4 but would page-overshoot; sev2 is the right middle.

## Test sweep + fixture impact

- **CI gate (source-shape sentinel) — REQUIRED.** Per the recurring pattern (cf. `l2_resolution_without_decision_record` shipped Session 219): the new assertion needs a test that pins (i) the assertion is REGISTERED in `ALL_ASSERTIONS`, (ii) display_name + recommended_action exist in the dict at line 2990, (iii) the runbook file exists (covered by `test_substrate_docs_present.py` automatically once registered), (iv) the SQL has `LIMIT` + `> NOW() - INTERVAL '24 hours'` window + `jsonb_array_length(...) > 1` guard.
- **`test_startup_invariants_pg.py` PREREQ_SCHEMA — needs additions.** Current fixture (lines 36-140) DROPs + CREATEs: `sites, fleet_orders, compliance_bundles, admin_audit_log, client_audit_log, portal_access_log, vault_signing_key_versions`. **MISSING:** `fleet_order_completions`. Per the Session 220 #77 fixture-parity lesson, DROP + CREATE must be added as a paired stanza (BOTH in PREREQ_SCHEMA's DROP block AND the finally-block teardown). Add: `CREATE TABLE fleet_order_completions (fleet_order_id UUID NOT NULL, appliance_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'completed', completed_at TIMESTAMPTZ DEFAULT NOW(), output JSONB, error_message TEXT, duration_ms INTEGER, updated_at TIMESTAMPTZ, PRIMARY KEY (fleet_order_id, appliance_id))`. The minimal shape (just PK columns + status) is sufficient for the assertion query; the mig 172 diagnostic columns are not needed for the invariant but include them for forward-compat parity with prod.
- **`test_no_param_cast_against_mismatched_column.py` (Session 220 #77):** the proposed query uses `fo.id::text = fob.fleet_order_id` — `fleet_orders.id` is UUID, `fob.fleet_order_id` is the text-extracted jsonb value. Casting UUID → text is allowed (the gate bans the inverse `col = $N::TYPE` mismatch). Safe.
- **PG-fixture integration test (optional but recommended):** mirror the existing `test_l2_resolution_requires_decision_record.py` shape — seed 1 bundle + 3 fan-out orders + 2 completions → assert 1 violation row with `unacked_orders=1`. Closes the "sometimes the SQL drifts off the fixture" risk.

## Runbook outline

File: `mcp-server/central-command/backend/substrate_runbooks/fleet_order_fanout_partial_completion.md`

**Sections required by `_TEMPLATE.md` (test_substrate_docs_present enforces):**

- **What this means (plain English):** "A privileged fleet operation was issued to N appliances; one or more never acknowledged. The operator's CLI showed 'orders issued' but some target appliances are silently offline or not pulling orders."
- **Root cause categories:** (1) Target appliance offline >6h (most common — power, network, wedged daemon); (2) Daemon not pulling orders despite heartbeat (auth_failure_count >= 3 lockout → `auth_failure_lockout` sev1 sibling); (3) Order created mid-deploy in window where appliance hadn't received its first fetch since INSERT (false-positive if appliance came online <6h after issuance — the 6h window prevents this); (4) `fleet_order_completion` writer broken (cf. mig 161 history — DELETE trigger blocked writes; would surface as 100% fan-out silent, not partial); (5) `target_appliance_id` mismatch in `processor.go::verifyHostScope` (appliance pulls but rejects — would surface in daemon logs, not just silence).
- **Immediate action SQL:** `SELECT fo.id, fo.created_at, fo.parameters->>'target_appliance_id' AS target, sa.hostname, sa.last_checkin, sa.status FROM fleet_orders fo LEFT JOIN site_appliances sa ON sa.appliance_id = fo.parameters->>'target_appliance_id' WHERE fo.id IN ('<unacked_order_ids>');` Cross-reference with `SELECT appliance_id, status, completed_at FROM fleet_order_completions WHERE fleet_order_id IN (...)`.
- **Verification:** Panel row clears on next 60s tick once either (a) appliance comes online + acks, or (b) operator cancels via `fleet_cli cancel <order_id>`.
- **Escalation:** If ALL N of a fan-out are silent → suspect `fleet_order_completion` writer regression (read mig 161 history first). If a specific appliance is silent across multiple fan-outs → escalate to L3, suspect daemon-side wedge.
- **Related runbooks:** `enable_emergency_access_failed_unack`, `auth_failure_lockout`, `appliance_offline_extended`, `agent_version_lag`.

## Sev choice + threshold

- **Sev: sev2** (Gate A overrides parent Gate B's sev3 sketch — see Counsel + sibling pattern above).
- **Threshold: 6h** — accept the proposed value. Tighter (1h-2h) false-positives on planned-maintenance offline windows; looser (12h-24h) defeats the visibility purpose since the operator typically discovers via `fleet_cli list` within a few hours anyway. 6h = "appliance has missed ~360 pull cycles, this is not transient."
- **Window: 24h** — accept. Beyond 24h the order has likely expired (`fleet_orders.expires_at` default 1d per mig 049); aging out into a "no longer actionable" set is the right boundary.

## Findings

### P0 (BLOCK)

- **SCHEMA-P0-1 (sketch wrong on `foc.id`).** Parent Gate B sketch uses `WHERE foc.id IS NULL` — `fleet_order_completions` has no `id` column (PK is composite `(fleet_order_id, appliance_id)`). Implementation MUST use `WHERE foc.fleet_order_id IS NULL`. Without this fix the query fails at runtime with `UndefinedColumnError` and the assertion 500s on every tick.
- **FIXTURE-P0-1 (PREREQ_SCHEMA missing fleet_order_completions).** `test_startup_invariants_pg.py` line 36-140 has no DROP or CREATE for `fleet_order_completions`. Any test that exercises this assertion in the PG fixture will fail with `UndefinedTableError`. MUST add DROP + CREATE in lockstep (Session 220 #77 fixture-parity rule).
- **STATUS-P0-1 (skipped rows count as completion).** Query MUST include `'skipped'` in the success-status set. `skipped` = appliance at `skip_version` already, semantically a successful ack. Without this, the invariant fires false-positives every time an `update_daemon` fan-out reaches an already-updated appliance. The recommended shape above includes this; pin it explicitly in implementation.

### P1 (MUST-fix-or-task)

- **PERF-P1-1 narrow scan to `action = 'privileged_access_attestation_chain'`.** Pin the exact action enum (verify from `privileged_access_attestation.py::create_privileged_access_attestation`). Without the narrowing the assertion seq-scans `admin_audit_log` 24h, which on enterprise scale (50K rows/24h) regresses to multi-second per-tick. Sibling `prometheus_metrics.py:521` is the cautionary tale (Session 219 COUNT(*) timeout class).
- **SEV-P1-1 sev2 not sev3.** Update parent Gate B sketch's sev3 → sev2. Counsel Rule 4 + sibling parity (`enable_emergency_access_failed_unack` sev2) + sev3 falls below operator-attention threshold.
- **BEST-EFFORT-P1-1 graceful absence of cross-link.** If `admin_audit_log.details ? 'fleet_order_ids'` is FALSE because the best-effort UPDATE at `fleet_cli.py:543` failed at write time (caught + logged to stderr), the invariant is BLIND to that fan-out entirely. Add a sibling invariant `privileged_fanout_cross_link_missing` (sev3) that counts privileged attestation bundles with parameters indicating fan-out (`details->>'count' > 1` or similar) but no `fleet_order_ids` array. File as TaskCreate followup — out-of-scope for #128 but the gap should be tracked.

### P2 (consider)

- **RUNBOOK-P2-1 cross-link from `enable_emergency_access_failed_unack` runbook.** The two assertions are siblings (explicit-fail vs silent-fail). Add bidirectional "Related runbooks" pointers.
- **TEST-P2-1 PG-fixture integration test.** Mirror `test_l2_resolution_requires_decision_record.py`: seed 1 bundle + 3 fan-out orders + 2 completions → assert 1 violation. Catches SQL-shape drift the AST gate misses.
- **METRIC-P2-1 expose `fleet_order_fanout_partial_completion` count in `/api/metrics`.** Operator-visible Prometheus gauge enables trending; once published, an alertmanager rule can fire on sustained > 0.

## Binding requirements

1. Use `WHERE foc.fleet_order_id IS NULL` (not `foc.id IS NULL`) — `fleet_order_completions` PK is composite, no `id` column exists.
2. Add `fleet_order_completions` DROP + CREATE (minimal shape: `fleet_order_id UUID, appliance_id TEXT, status TEXT, completed_at TIMESTAMPTZ, PRIMARY KEY (fleet_order_id, appliance_id)`) to `test_startup_invariants_pg.py::PREREQ_SCHEMA` AND the finally-block teardown — Session 220 #77 fixture-parity rule.
3. Treat `foc.status IN ('completed','acknowledged','skipped')` as success — `skipped` = already-at-version is a valid ack.
4. Narrow scan with `AND al.action = '<exact action string from privileged_access_attestation.py>'` to pin index usage and bound 24h scan cost.
5. Register at **sev2** (not sev3). Sibling parity + Counsel Rule 4 + operator-attention threshold.
6. Write the runbook `mcp-server/central-command/backend/substrate_runbooks/fleet_order_fanout_partial_completion.md` per `_TEMPLATE.md` sections — `test_substrate_docs_present.py` fails the build otherwise (the `39c31ade` outage class).
7. Add the display_name + recommended_action entry at `assertions.py:~3000` dict (sibling to `fleet_order_url_resolvable`).
8. File TaskCreate followup for BEST-EFFORT-P1-1 (cross-link missing detector) in the same commit.
9. Gate B MUST run `bash .githooks/full-test-sweep.sh` and cite pass count — Session 220 lock-in.

## Final

APPROVE-WITH-FIXES. The design closes a real Counsel Rule 4 gap left explicit in #118 Gate B and the sibling pattern is sound. Three P0s (schema column wrong, fixture missing, status set incomplete) MUST be fixed before implementation lands. Three P1s (action narrowing, sev2 not sev3, blind-to-missing-cross-link followup) MUST be addressed or carried as named TaskCreate items in the same commit per TWO-GATE recommendations-are-not-advisory rule.

Path: `audit/coach-128-fanout-completion-orphan-gate-a-2026-05-16.md`
