# Gate B re-review verdict — L1-orphan Phase 2A diagnostic v2 (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES (one P1 + two P2 nits)

## Source verification (binary)

- `healing_executor.go:92-98` returns `{"escalated": true, "reason": reason}` with NO `"success"` key: VERIFIED. Line 92 `case "escalate":`, line 98 `return map[string]interface{}{"escalated": true, "reason": reason}, nil`.
- `l1_engine.go:327-334` defaults `result.Success = true` when `output["success"]` missing: VERIFIED. Line 328 `if s, ok := output["success"]; ok`, line 332-334 `} else { result.Success = true }`.
- `daemon.go:1706` hardcoded `"L1"` in `ReportHealed`: VERIFIED. `d.incidents.ReportHealed(req.Hostname, req.CheckType, "L1", match.Rule.ID)`.
- `builtin_rules.go` 9 escalate-action lines: VERIFIED via grep — 161, 215, 712, 732, 823, 988, 1008, 1028, 1048. Sampled with surrounding context:
  - L161 → L1-ENCRYPT-001, check_type=`encryption`
  - L215 → L1-SERVICE-001, incident_type=`service_crash`
  - L823 → L1-WIN-ROGUE-TASKS-001, check_type=`rogue_scheduled_tasks` (matches doc)
  - L988 → L1-NET-PORTS-001, check_type=`net_unexpected_ports` (matches doc)
  - L1008/1028/1048 → L1-NET-SVC-001, L1-NET-REACH-001, L1-NET-DNS-001
- No other code consumes `output["escalated"]` (grep across appliance + backend, sole producer at healing_executor.go:98). Layer 1 fix is safe.
- No immutability TRIGGER on `incidents` (only RLS site_id auto-set at mig 078:375). `resolution_tier` is freely UPDATEable. Mig 151 only protects `incident_remediation_steps`. Mig 306 UPDATEs on `incidents` are valid.
- `main.py:4865-4892` UPDATE shape verified — `resolution_tier = :resolution_tier` is the daemon-supplied value with no server-side check; Layer 2 gate slots in cleanly before the UPDATE binding.

## Adversarial findings

### P1 — Sibling-rule blast radius is asserted, not measured
Doc §"Sibling builtin rules at risk" lists 9 rules but only 2 are confirmed firing in prod (rogue_scheduled_tasks + net_unexpected_ports). The other 7 (`encryption`, `service_crash`, lines 712/732, `net_expected_service`, `net_host_reachability`, `net_dns_resolution`) are claimed at-risk but the doc has no SQL evidence (orphan count by `incident_type` for those 7 over the same 90d window). Soften header from "will produce the SAME orphan class" to "are at risk of the same class; prod orphan-count by incident_type pending." Trivial SQL:
```sql
SELECT incident_type, COUNT(*) FROM incidents
 WHERE status='resolved' AND resolution_tier='L1'
   AND incident_type IN ('encryption','service_crash','net_expected_service',
                          'net_host_reachability','net_dns_resolution')
   AND reported_at > NOW() - INTERVAL '90 days'
 GROUP BY 1;
```
Run before Phase 3 lands so mig 306 backfill `incident_type IN (...)` list is empirically grounded, not author-asserted.

### P2 — Layer-2 gate scope explained but mig 306 list is hand-curated
mig 306 UPDATE 2 hardcodes `incident_type IN ('rogue_scheduled_tasks')` with comment "expand per Layer-1 fix audit." Better: derive programmatically from the `builtin_rules.go` escalate-rule set so future rule additions don't drift the backfill. Acceptable for one-shot backfill, but flag as followup: substrate invariant `escalate_rule_check_type_drift` to detect daemon rules whose check_type doesn't appear in mig-306-style backfills.

### P2 — Phase 3 commit sequencing
Doc says "two changes; one commit" for Layer 1, then separately "Layer 1 + Layer 2 (both same commit OR sequenced)." Pin the order: Layer 1 (daemon) MUST be in an appliance build that rolls out before Layer 2 lands in backend (defense-in-depth assumes Layer 1 is the primary; if Layer 2 deploys first against existing daemons, the false-L1 path keeps firing for non-monitoring-only check_types like rogue_scheduled_tasks). Add: "Phase 3 commit order: appliance v0.X.Y with Layer 1 → fleet update_daemon order → backend Layer 2 deploy → 24h soak → mig 306."

## Per-lens

- **Steve:** Source-verification PASS. Theory matches the file:line citations. No collateral damage from Layer 1 fix.
- **Maya:** Audit-chain implications PASS. `resolution_tier` is updatable, mig 306 is valid. The Layer-2-only-downgrades-monitoring-only / rogue_scheduled_tasks-not-monitoring-only logic chain holds — rogue_scheduled_tasks relies on Layer 1 fix exclusively, doc's mig 306 case-2 (escalate → L3) catches the historical rows. Layer-1+Layer-2 are NOT redundant for that class; sequencing matters (P2 above).
- **Carol:** Security boundary PASS. Layer 2 reads `check_type_registry.monitoring_only` server-side; a compromised daemon cannot bypass. Chaos-lab `/Users/jrelly/chaos-lab/v2-orchestrator.sh` not reachable from this host — manual verify deferred to fleet rollout window.
- **Coach:** Consistency PASS with P1 above. v2 doc accurately reflects trace fork findings; "ONE bug" framing is correct. The 9-rule audit table is the only place the doc overreaches.

## Phase 3 design soundness

Layer 1 (`success: false` on escalate + fail-closed default in l1_engine.go:332-334) — sound, minimal, primary fix. Layer 2 (backend monitoring-only downgrade at main.py:4870) — sound defensive belt for the monitoring-only subset (matches mig 300 L2-gate sibling pattern). mig 306 backfill — three-case coverage (monitoring / escalate→L3 / true-L1→synthetic-step) is correct on logic; UPDATE-2's hardcoded `IN ('rogue_scheduled_tasks')` needs P1 SQL evidence before going to prod.

## Recommendation

**APPROVE-WITH-FIXES.** Address P1 (SQL count of the 7 unverified escalate classes; expand mig 306 UPDATE 2 IN-list accordingly + soften v2 doc §"Sibling builtin rules" language) before Phase 3 implementation. P2s are non-blocking sequencing/followup items — pin commit order in Phase 3 PR description; ship the substrate-invariant followup as a named TaskCreate.
