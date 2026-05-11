# Gate A — L1-orphan revised 3-phase plan (2026-05-11)

**Verdict:** BLOCK

Ground-truth signal in Phase 1 is wrong. Shipping it as drafted will fire ~all L1 rows in the past 7d as sev1, including any that were actually healed via the relational ledger — the dashboard goes red on a false signal and the user loses confidence in the substrate engine the same week we're trying to use it to find a real outage.

---

## P0 findings

### P0-1 (Steve + Coach) — `incidents.remediation_history` is the WRONG ground-truth column

The plan: "Use `remediation_history` as ground truth, NOT execution_telemetry join."

Reality check:
- Migration 099 added `incidents.remediation_history JSONB DEFAULT '[]'::jsonb`.
- **Migration 137** (`137_remediation_steps_table.sql`) moved that data to a relational table `incident_remediation_steps` and explicitly comments on line 63 that the JSONB column is NOT yet dropped: `-- ALTER TABLE incidents DROP COLUMN remediation_history;`. So the column still exists, defaults to `'[]'::jsonb`, and is no longer being written to.
- The write callsite is `agent_api.py:1248-1262`: INSERTs into `incident_remediation_steps`, never touches the JSONB column.
- The read callsite is `routes.py:561-585`: queries `incident_remediation_steps` first, only falls back to the JSONB column if the table query throws.
- CLAUDE.md `agent_api.py:1260` comment confirms: "incident_remediation_steps is the audit-classified table that replaced incidents.remediation_history JSONB (Migration 137)".

**Consequence if Phase 1 ships as drafted:** `jsonb_array_length(remediation_history) = 0` is true for EVERY incidents row created after Migration 137 ran (everything in the past 7d, plus most of the table). The invariant fires sev1 on ~all 539 L1+L2 rows in 24h regardless of whether they got real remediation. Steve P0-3 from prior Gate A (incident_id type mismatch) is the same class of mistake: querying a ground-truth signal that doesn't exist.

**Fix:** Ground truth MUST be `LEFT JOIN incident_remediation_steps irs ON irs.incident_id = i.id WHERE irs.id IS NULL` (or `EXISTS` negation). This is also fact-checkable against the prod sample BEFORE the invariant ships — if the 539 L1 rows actually have rows in `incident_remediation_steps`, the pipeline isn't dark, the labeling is just confused. If they don't, the invariant correctly fires.

### P0-2 (Steve) — Prod sample claim is unverified; run it against the right table FIRST

The prompt cites `COUNT(*) FILTER (WHERE jsonb_array_length(remediation_history) > 0)` returning 0 across 540 resolved rows. Per P0-1, this signal is meaningless. The TRUE diagnostic query is:

```sql
SELECT i.resolution_tier, COUNT(*) AS total,
       COUNT(*) FILTER (WHERE irs.id IS NOT NULL) AS has_relational_step
FROM incidents i
LEFT JOIN incident_remediation_steps irs ON irs.incident_id = i.id
WHERE i.reported_at > NOW() - INTERVAL '7 days' AND i.status = 'resolved'
GROUP BY i.resolution_tier;
```

If this returns the same shape (532 L1 / 0 with steps), the "pipeline is dark" framing is real — proceed with the 3-phase plan AFTER fixing P0-1. If it returns ~532 L1 / ~530 with steps, the pipeline is NOT dark; only `execution_telemetry` is dark, and the whole framing shifts to "telemetry-write outage", not "healing-pipeline outage." Phase 2 collapses to one bisect, no Phase 1 substrate invariant needed at all (or sev3, not sev1).

**This MUST be re-run before Phase 1 is even drafted.** The prior Gate A's "verify P0-3 with the right table" stayed open.

### P0-3 (Maya + Steve) — Sev1 severity is wrong UNTIL P0-1 + P0-2 are resolved

Sev1 pages operators. The substrate-meta SLA in `assertions.py:614` reads `"sev1": 240` minutes (4h response). Firing sev1 on a query whose ground-truth column is empty by design (P0-1) creates a paging event on a falsehood. Even after P0-1 is fixed, if the prod sample says 532 are real orphans, that's ~3/hr arrival — `compliance_bundles_no_delete` (sev1, line 444) and similar use sev1 for active-bleed events. This qualifies if the orphan signal is genuine. But the user is the only operator, the pipeline has been dark for 3 months unalarmed, and a single named-followup TaskCreate is sufficient triage. Recommend **sev2 with a `details->>'trending_up'` discriminator** for the first 7 days, escalate to sev1 if the count grows after Phase 3 ships. Sibling: `assertions.py:870` uses sev2 for the analogous "> 5 unresolved rows in 24h" threshold.

---

## P1 findings

### P1-1 (Steve) — `DISTINCT ON (site_id, incident_type)` hides legitimate distinct incidents

A site can have the same incident_type re-fire after a successful resolve (separate dedup_key, separate audit-chain entry). Squashing them collapses N legit orphans to 1 surfaced violation. Recommendation: dedup on `(site_id, dedup_key)` not `(site_id, incident_type)` — dedup_key is the natural key the daemon already uses. If `dedup_key` is nullable for older rows, COALESCE to `id::text`.

### P1-2 (Carol) — `'monitoring'` enum is now overloaded with 4 use cases

After Phase 3, `resolution_tier='monitoring'` will mean ALL of: (a) stale-incident auto-resolve (h_m.py:672), (b) stuck-resolving cleanup (694), (c) zombie cleanup (725), (d) checkin-handler auto-clean (new). Sibling-endpoint header-parity rule applies: customer-facing surfaces that count `'monitoring'` (compliance_packet.py and similar) need a discriminator. Recommend `details->>'monitoring_reason'` populated at every write site with one of: `stale_7d`, `stuck_3d`, `zombie_48h`, `auto_clean`. Cheap; closes the semantic-overload class Coach raised.

### P1-3 (Carol) — North-valley-branch-1 exposure during the dark window

Memory pointer `operator_north_valley_environmental_actions.md` says branch-2 is chaos-lab; branch-1 is the paying customer. Before Phase 3 cutoff date is finalized, run the diagnostic for branch-1 specifically — which of its 532 L1 "auto-healed" rows are real Windows-registry-persistence / Defender-exclusion / scheduled-task detections that got marked healed without a runbook run? Each one is a potential malware-exposure window the customer should be notified about under Maya's "active false claim on the audit chain" framing.

### P1-4 (Maya) — Retroactive PDF correction obligation needs counsel input, not engineering

Maya prompt question: is the 2026-02-18 → 2026-05-10 stretch of misleading "auto-healed N" PDFs a §164.528 disclosure-accounting correction event? Engineering answer: I don't know. Outside-counsel question. Open a TaskCreate followup in the same shipping commit, do NOT block Phase 1 on it.

---

## P2 findings

### P2-1 (Coach) — Phase 2 needs Gate B even though "no code change"

Plan says: "Phase 2 is diagnostic only, no code change; coach pre-completion check before user reads." That's a Gate B. Call it one. The doc is the deliverable; it has the same misread-as-truth risk as a shipping commit. `audit/coach-l1-orphan-phase2-investigation-gate-b-2026-05-11.md`.

### P2-2 (Steve) — `remediation_history` JSONB column should be dropped in same migration

Migration 137 left the column for rollback safety. 3 years later (sic), it's a footgun — exactly the one this plan stepped on. After P0-1 is fixed, open a P2 TaskCreate to drop the column in a future migration (NOT this sprint — drop migrations on hot tables need their own Gate A).

---

## Per-lens analysis

### Steve
Pipeline-is-dark framing is plausible but unverified against the correct ground-truth column. P0-1 + P0-2 must close before any Phase 1 work. `DISTINCT ON` choice is wrong (P1-1). Severity is wrong until ground truth is real (P0-3).

### Maya
If the pipeline IS dark (post-P0-2 verification): yes, this is an active false claim on the audit chain since Feb 2026, and yes, counsel needs to weigh in on retroactive disclosure (P1-4). `substrate_violations` does NOT surface to customer portals (verified — no grep hits in client_portal.py or partners.py), so the operator-dashboard noise concern dissolves. Phase 3 cutoff at `2026-05-11` is defensible IF a counsel-question doc is opened simultaneously; otherwise it looks like silent backdating.

### Carol
P1-3 is the load-bearing one — north-valley-branch-1 (real customer) exposure during the dark window needs a site-scoped diagnostic BEFORE Phase 1 ships, not after. Authn/integrity of `incident_remediation_steps` writes: the table is INSERTed via `db.execute()` from agent_api.py:1249 inside the order-creation transaction. No appliance-side write path. Forgeable only if appliance compromise + main.py POST endpoint accepts forged remediation_step claims. Spot-check: no such endpoint in agent_api.py grep. Safe.

### Coach
3-phase split is canonically right (visibility → diagnostic → fix, same shape as Session 218 RT21 cross_org_relocate). Phase 2 needs an explicit Gate B (P2-1). Sibling-endpoint parity for `'monitoring'` discriminator (P1-2). Each phase gets its own commit + its own Gate B verdict file. Acceptable as redesigned.

---

## Phase-by-phase verdict

- **Phase 1: REDESIGN.** Fix P0-1 (relational join not JSONB), then re-verify P0-2 (true orphan count), then re-evaluate P0-3 (severity). Add P1-1 (dedup_key dedup) and P1-2 (`monitoring_reason` discriminator).
- **Phase 2: HOLD until Phase 1 redesign lands.** The investigation doc's recommendations depend on knowing whether the pipeline is actually dark or just mis-telemetered. Then ship with explicit Gate B (P2-1).
- **Phase 3: HOLD until Phase 2 produces a root cause.** If root cause is "telemetry-write outage, not pipeline outage," Phase 3's labeling fix is unnecessary — the L1 labels were correct, only the audit signal was missing.

---

## Recommendation

**BLOCK.** Mandatory before re-review:

1. Re-run the prod sample using `LEFT JOIN incident_remediation_steps` (P0-2 query above) and report the real orphan count.
2. Redraft Phase 1 invariant SQL against `incident_remediation_steps` (P0-1).
3. Redraft severity decision based on step-1 numbers (P0-3).
4. Switch dedup to `(site_id, COALESCE(dedup_key, id::text))` (P1-1).
5. Add `details->>'monitoring_reason'` discriminator to Phase 3 + each existing health_monitor.py:672/694/725 callsite in the same commit (P1-2).
6. Open a counsel-question TaskCreate for retroactive PDF correction obligation (P1-4) before Phase 3 ships.
7. Run a north-valley-branch-1-scoped variant of the diagnostic in Phase 2 first (P1-3).

Re-submit as Gate A v2 after items 1-3 are evidence-cited in the design doc. Items 4-7 can ride as named follow-ups in the v2 verdict.
