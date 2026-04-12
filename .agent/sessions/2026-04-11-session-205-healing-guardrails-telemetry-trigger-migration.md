# Session 205 — Flywheel Intelligence + Scoring Architecture + Persistence Runbooks

**Date:** 2026-04-11 → 2026-04-12
**Previous Session:** 204

---

## Goals

- [x] Write automated guardrail tests for healing pipeline
- [x] Formalize telemetry→remediation trigger as migration 155
- [x] Fix zombie/stuck incident cleanup
- [x] Fix telemetry trigger UUID safety (500 errors)
- [x] Fix site activity `title` column error
- [x] Build recurrence-aware L2 escalation
- [x] Ship flywheel intelligence: velocity, auto-promotion, cross-correlation
- [x] Build canonical check_type_registry (single source of truth)
- [x] Fix scoring engine — was ignoring 12/19 daemon check names
- [x] Fix scoring oscillation — latest-per-check replaces last-50-bundles
- [x] Add 24h staleness cutoff to scoring
- [x] Build persistence-aware runbooks (RB-WIN-PERSIST-001, RB-WIN-PERSIST-002)
- [x] Add configure_dns fleet order handler
- [x] Mark BitLocker as not_applicable for VMs without TPM
- [x] Clean 11 zombie incidents from production
- [x] Fix provision modal dark mode text visibility

---

## Files Changed

| File | Change |
|------|--------|
| `backend/tests/test_healing_pipeline_integrity.py` | 15 guardrail tests (runbooks, monitoring sync, L1 steps, circuit breaker, registry completeness) |
| `backend/migrations/155_telemetry_remediation_sync_trigger.sql` | Trigger with UUID safety |
| `backend/migrations/156_flywheel_recurrence_intelligence.sql` | escalation_reason, recurrence_velocity, correlation_pairs tables |
| `backend/migrations/157_check_type_registry.sql` | 69 canonical check names, categories, HIPAA controls |
| `backend/health_monitor.py` | Zombie cleanup, stuck-resolving cleanup (>3d) |
| `backend/agent_api.py` | Recurrence-aware L2 escalation (3+ in 4h bypasses L1), monitoring-only from registry |
| `backend/l2_planner.py` | Recurrence prompt, escalation_reason recording, persistence runbooks in catalog |
| `backend/background_tasks.py` | recurrence_velocity_loop, recurrence_auto_promotion_loop, cross_incident_correlation_loop |
| `backend/db_queries.py` | Latest-per-check scoring, 24h staleness, registry loader, all daemon check names mapped |
| `backend/routes.py` | /flywheel-intelligence endpoint |
| `backend/sites.py` | Remove non-existent `title` column from activity query |
| `main.py` | Register 3 new background tasks, load check registry + monitoring-only at startup |
| `frontend/src/pages/Dashboard.tsx` | Flywheel Intelligence card |
| `frontend/src/pages/SiteDetail.tsx` | Provision modal dark mode text fix |
| `frontend/src/hooks/useFleet.ts` | useFlywheelIntelligence hook |
| `frontend/src/hooks/index.ts` | Export useFlywheelIntelligence |
| `frontend/src/utils/api.ts` | flywheelApi.getIntelligence |
| `appliance/internal/orders/processor.go` | configure_dns handler |
| `appliance/internal/daemon/runbooks.json` | RB-WIN-PERSIST-001, RB-WIN-PERSIST-002 |

---

## 14 Commits Shipped to Production

1. `432de97` — Healing pipeline guardrail tests + migration 155
2. `5c6db0c` — Zombie cleanup + provision modal dark mode
3. `784d6a2` — Telemetry trigger UUID safety
4. `95c567b` — Site activity title column fix
5. `ba8b767` — Recurrence-aware L2 escalation
6. `30ca12d` — Flywheel intelligence: velocity, auto-promotion, cross-correlation
7. `79be24e` — Zombie cleanup CHECK constraint fix
8. `b0d7c70` — Stuck-resolving cleanup (>3d)
9. `d49e175` — Scoring recognizes all daemon check names (was hiding 12/19)
10. `cfa0765` — categories_with_data NameError fix
11. `32af56d` — Latest-per-check scoring (stops 50↔90 oscillation)
12. `7cdb498` — 24h staleness cutoff + LIMIT 200
13. `da2654e` — Recurrence L2 priority over chronic L3
14. `e6d5cec` — Persistence runbooks + configure_dns + L2 catalog

---

## Next Session

1. Verify recurrence L2 fired and produced persistence runbook recommendations
2. Build + deploy daemon binary with configure_dns handler + persistence runbooks
3. Send configure_dns fleet order (NVDC01 → 192.168.88.250)
4. Monitor auto-promotion: did RB-WIN-PERSIST-001 get promoted to L1?
5. Validate enterprise installer with 2 new ordered devices
6. Checkin handler decomposition (1,373 lines)
