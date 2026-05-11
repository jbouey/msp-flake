# Gate A — L1-orphan substrate close + chaos-lab nightly-restore fix (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

Investigation grounded in: `assertions.py:1100-1154` (L2 sibling), `migrations/300_backfill_orphan_l2_decisions.sql`, `migrations/106_incidents_monitoring_tier.sql:9` (CHECK enum), `migrations/142_incident_pipeline_hardening.sql:7-9` (partial-unique dedup index), `migrations/031_learning_sync.sql:88-167` (`execution_telemetry.incident_id VARCHAR(255)`), `agent_api.py:1595-1684` (L1 writers), `sites.py:2947` + `health_monitor.py:660-758`, `appliance/internal/daemon/daemon.go:1810` + `healing_executor.go:644`.

## P0 findings (must fix before implementation)

**P0-1 (Coach + Steve): `auto_clean` is a NET-NEW enum value but `'monitoring'` already exists for exactly this case.** `migrations/106_incidents_monitoring_tier.sql:9` defines `CHECK (resolution_tier IN ('L1','L2','L3','monitoring'))`. `health_monitor.py:660-758` already uses `resolution_tier='monitoring'` for three distinct auto-clean classes (>7d stale, stuck-resolving >3d, zombie superseded). Adding `auto_clean` AS WELL fragments the semantics — dashboards in `db_queries.py:65-66`, `partners.py:1392-1394`, `compliance_packet.py:1132-1134`, `routes.py:2441-2525`, `ops_health.py:390` all switch on the literal tier strings and would have to add the new branch in lockstep. Coach pattern rule: **repurpose existing shape, don't duplicate**. Re-label the no-runbook clean-state path as `'monitoring'`. Update the substrate invariant to accept `('L1','L2')` for the runbook/decision-record gates and EXCLUDE `'monitoring'` + `'L3'` from the check. Closes A3 + half of A4 with zero new enum surface. (If Maya rejects "monitoring" as customer-facing — see P0-2 — propose a SEPARATE renaming sweep; do not introduce a third value.)

**P0-2 (Maya): `auto_clean` (or `monitoring`) label appears in customer-facing surfaces — verify before settling on a name.** `partners.py:1428` + `compliance_packet.py:1132` + `routes.py:2476-2525` aggregate by `resolution_tier` for PDFs and dashboards. The L2-orphan pattern was internal-only (backfill rows hidden behind `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'`), but a NEW `resolution_tier` value gets COUNT-FILTERED everywhere. Required before implementation: grep every `FILTER (WHERE resolution_tier = 'L1')` callsite and confirm whether reclassified incidents drop out of the "auto-healed" customer number. If yes, the customer-facing healed-count will DROP retroactively after the labeling-path fix lands — that's a public-facing metric regression. Maya recommendation: `'monitoring'` reads correctly in `compliance_packet` ("passive monitoring observed clean state"); do NOT introduce a new label.

**P0-3 (Steve): `execution_telemetry.incident_id` is `VARCHAR(255)` (mig 031:91), `incidents.id` is `uuid`.** The proposed join `incidents LEFT JOIN execution_telemetry ON et.incident_id = i.id::text` is correct — `assertions.py:1132` already does this for L2. BUT: the daemon side (`incident_reporter.go:160`, `daemon.go:1810`, `healing_executor.go:644-645`) writes the `incident_id` field as a string of whatever the daemon believes the incident ID is — which on the appliance is often the daemon's LOCAL incident ID, NOT the backend UUID. Audit required: confirm that the L1 path actually writes `execution_telemetry.incident_id = <backend_uuid_as_string>`, not the daemon-local id. If it writes the daemon-local id, the proposed invariant will fire on EVERY successful L1 (massive false positive). Sample 10 rows from `execution_telemetry` on prod and verify the format matches `incidents.id::text`.

**P0-4 (Steve): partial unique index `idx_incidents_dedup_key_unique` (mig 142:7-9) excludes resolved/closed.** A flap-row site has 50+ resolved rows with the SAME dedup_key. The invariant `LIMIT 50` (sibling at `assertions.py:1134`) will return ONLY flap-rows from the noisiest site and starve every other violation type from being seen for the tick. Required fix: invariant query must DISTINCT-on `dedup_key` (or `site_id, incident_type, hostname`) and surface the LATEST orphan per dedup_key — same SQL shape as `v_l2_outcomes_canonical` (mig 285). Otherwise observability hides itself behind a single chatty site.

**P0-5 (Carol): chaos-lab VM nightly restore wipes daemon enrollment state — re-enrollment under a stale or new agent_id creates phantom `site_appliances` rows.** Same class as Session 210 reflash bug (CLAUDE.md rule: "Site rename is a multi-table migration"). Required before B1 ships: (a) confirm `north-valley-branch-2` VM snapshot captures `/var/lib/msp/enrollment/*` + `agent_public_key` + signing key OR (b) the daemon re-uses prior identity by MAC. If neither, the unconditional nightly restore will leak a new `site_appliances` row per day. Mitigation: B1 sequence must include a post-restore assertion `SELECT count(*) FROM site_appliances WHERE site_id='north-valley-branch-2' AND deleted_at IS NULL = 1`.

## P1 findings (must fix before close-out OR carry as TaskCreate)

**P1-1 (Steve): Go daemon also writes `resolution_tier='L2'` at `daemon.go:1810`.** The proposal's A3 only audits backend Python writers, but the daemon's local L2 path is the one mig 302 backfilled. The "labeling-path fix" needs a daemon-side mirror: the Go code at `healing_executor.go:644` should refuse to set `resolution_tier='L1'` in completion payload if no `execution_telemetry` was emitted in the same RPC. Carry as TaskCreate if not closed in this commit.

**P1-2 (Coach): CI pin gate must match L2 sibling shape exactly.** `test_l2_resolution_requires_decision_record.py` is 182 lines, three tests (source-walk + positive + negative). The L1 test MUST mirror: AST walker for every Python write to `resolution_tier='L1'` requiring an `execution_telemetry` INSERT call within 80 lines OR an explicit `# noqa: l1-orphan-allowed` allowlist comment (the daemon completion-callback handler at `sites.py:2947` is a legit exception — the daemon already wrote the telemetry, the backend is just receiving the callback). Without the allowlist mechanism, the gate produces false-positive blockers.

**P1-3 (Maya): backfill mig idempotency.** Mig 300 is idempotent via `WHERE NOT EXISTS (SELECT 1 FROM l2_decisions ...)` — safe to re-run. L1 backfill MUST use the same shape: `WHERE NOT EXISTS (SELECT 1 FROM execution_telemetry et WHERE et.incident_id = i.id::text AND et.success = false)` — using `success=false` distinguishes synthetic-backfill rows so a future real telemetry insert doesn't get confused.

**P1-4 (Carol): chaos-lab WinRM 5985 exposure.** Confirm `192.168.88.251:5985` is firewall-restricted to the iMac orchestrator only (no inbound from VPS/WG). The persistent "SecurityUpdate" registry Run key is a real backdoor if the box is ever bridged.

**P1-5 (Coach): commit MUST be split.** Platform (A1-A4) and chaos-lab (B1-B4) share zero code paths, zero test files, zero deployment surface. A Gate B review of a combined commit would have to evaluate two unrelated risk profiles in one verdict; if one half breaks rollback is entangled. Recommended: PR-1 (platform L1-orphan), PR-2 (chaos-lab orchestrator). Each gets its own Gate B.

## P2 findings (nice to have)

**P2-1 (Steve): B2 cron UTC move.** Switching from local to UTC also breaks the cadence-results.csv historical compare (timestamps shift 7-8h). Add a one-time pivot row in the CSV header noting the cutover date.

**P2-2 (Coach): mig number conflict risk.** 303 + 304 already exist (substrate-MTTR soak + quarantine). The L1 backfill is mig 305 — fine, but document the renumbering rule if the chaos-lab fix produces a SQL mig (it should not — chaos-lab is iMac-side bash).

## Per-lens analysis

### Steve
P0-3 + P0-4 are SQL-correctness blockers; P1-1 is the missing daemon-side mirror. The proposal under-specifies the type-boundary and partial-index pathology that bit the L2 work. Reading `mig 302` shows the authors already learned the daemon-callsite lesson once — apply it preemptively here.

### Maya
P0-2 + P1-3 are audit-chain hygiene. The L1 path is more customer-visible than L2 (compliance PDFs count L1 as "auto-healed"), so the label choice ripples further. Backfill mig MUST be reasoned-trace-honest (it isn't fabricating a runbook execution, it's recording "no runbook ran but state went clean") — `failure_type='auto_recovered'` (mig 031:141) is the existing semantic match.

### Carol
P0-5 + P1-4 are lab-isolation. Chaos-lab is on `192.168.88.0/24` per CLAUDE.md but daemon re-enrollment after restore is the realistic operational footgun. Bigger risk than the WinRM surface.

### Coach
The two halves are unrelated; combining them invites a Gate B verdict where one half passes and one fails, and the author rolls back both or neither. Split. Also: P0-1 is the canonical "duplicates an existing shape" antipattern — the proposal would have shipped a third tier label for a case where the existing `'monitoring'` label is already the right home.

## Should the commit be split?

YES — split. Part A is a backend Python + SQL mig + CI test change deploying via the standard CI/CD pipeline to the VPS, blast radius = all sites. Part B is an iMac-local bash script edit on the chaos-lab orchestrator, blast radius = lab only, zero customer impact. They share no files, no tests, no deployment, no rollback. Combining them creates a Gate B where evaluating "the commit" means evaluating two independent risk profiles. Each gets its own Gate A/B pair. P1-5.

## Recommendation

**APPROVE-WITH-FIXES.** Mandatory before implementation:
1. **P0-1:** Use existing `'monitoring'` enum value, NOT new `'auto_clean'`. Update substrate invariant to exclude `'monitoring'` and `'L3'`, require runbook/decision-record only for `'L1'` and `'L2'`.
2. **P0-2:** Verify customer-facing aggregations (`partners.py`, `compliance_packet.py`, `routes.py`) handle reclassification without metric regression. If `'monitoring'` excludes from auto-healed COUNT, document the customer-visible drop.
3. **P0-3:** Sample 10 prod `execution_telemetry.incident_id` rows; confirm format matches `incidents.id::text`. If mismatch, fix daemon write side BEFORE invariant lands.
4. **P0-4:** Add `DISTINCT ON (dedup_key)` to invariant SQL so flap-row sites don't starve the violation budget.
5. **P0-5:** Add post-restore `site_appliances` row-count assertion to chaos-lab nightly script.
6. **P1-5:** Split into two PRs / two Gate B reviews.

P1-1 + P1-2 + P1-3 + P1-4 + P1-2 may be carried as named TaskCreate followups in the same commit if not closed inline.

Gate B must verify: (a) AS-IMPLEMENTED SQL uses `DISTINCT ON`, (b) no new `resolution_tier` enum value introduced, (c) AST-walk pin gate has allowlist mechanism, (d) backfill mig is idempotent + uses `failure_type='auto_recovered'`, (e) chaos-lab change is in a SEPARATE commit, (f) prod sample confirms `incident_id` format match.
