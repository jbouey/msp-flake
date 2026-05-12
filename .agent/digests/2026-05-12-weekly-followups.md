# Weekly `scheduled_followups` Digest — 2026-05-12 (Tue)

Today = **2026-05-12**. Total entries: **11** (9 open, 2 closed).
Buckets relative to today; due-windows: OVERDUE / DUE_THIS_WEEK (≤05-19) / UPCOMING (≤06-02) / LATER (>06-02).

---

## OVERDUE (3)

| Due | Task | Owner | Recommendation |
|---|---|---|---|
| **2026-05-05** | sigauth post-fix-window watch (#169). After 2026-05-05 if both invariants silent → **REMOVE `sigauth_post_fix_window_canary`** from ALL_ASSERTIONS. | any pickup | **READY TO REMOVE.** Prod confirms `sigauth_post_fix_window_canary` is absent from the live invariant table (38 distinct rows; no canary). `sigauth_enforce_mode_rejections` last fired 2026-04-28, 0 open. Close this entry; only residue is documentation cleanup if the canary still appears in `assertions.py` source. |
| **2026-05-08** | P1: persistence-drift recurrence not escalating to L2 (406 L1 / 0 L2 over 7d for windows_update/defender_exclusions/rogue_scheduled_tasks). | engineering | **HIGH PRIORITY** — auditor-visible flywheel gap; spawn research fork next session to trace `_recurrence_velocity_loop` → `l2_decisions` write path. |
| **2026-05-08** | P3: extend `incidents.resolution_tier` VARCHAR(10) → VARCHAR(32) via mig 268 (3 BEGIN/COMMIT blocks; recreate `v_canonical_incidents` + `partner_site_weekly_rollup` MV). | engineering | Defer until calm session; not blocking. Materialized-view REFRESH timing must be measured first. |

## DUE_THIS_WEEK (2)

| Due | Task | Owner | Recommendation |
|---|---|---|---|
| **2026-05-15** | **EXPECTED_INTERVAL_S calibration sweep** (Session 214 follow-up) — audit `bg_heartbeat.EXPECTED_INTERVAL_S` vs `asyncio.sleep(N)` in each loop; recommend AST-grep CI gate. | any pickup | Bounded research fork; cheap to execute; closes a known regression class. |
| **2026-05-15** | D5 (closed 2026-05-01, commit `a5bb68ad`). | — | **PRUNE** — already closed early; safe to drop from array. |

## UPCOMING (2)

| Due | Task | Owner | Recommendation |
|---|---|---|---|
| **2026-05-22** | **D1: per-control granularity in `calculate_compliance_score`** — implementation gate; design doc round-table-approved. | engineering | Multi-hour schema design (mig 269 + writer + 117K backfill); plan a dedicated session, not a fly-by. |
| **2026-05-22** | D7 (closed 2026-05-01, 11 new fixture tests). | — | **PRUNE** — already closed; safe to drop. |

## LATER (3)

| Due | Task | Owner | Recommendation |
|---|---|---|---|
| 2026-05-29 | D8: `scheduled_followups` **dependency_warnings** schema field + context-manager surface. | engineering | Quick; do this BEFORE 5/15 calibration vs 5/29 BUG-2 sweep collisions reappear. |
| 2026-05-29 | BUG 2 sweep: 63 frontend `credentials: 'same-origin'` → `'include'` (per-site eyes-on). | engineering | Per-site verified — slow burn, not urgent. |
| 2026-05-29 | BUG 3 cleanup: 10 legacy `compliance_status` readers → `get_per_device_compliance()`. | engineering | Slow burn. CI ratchet baseline=14 holds line. |

## CLOSED — recommend prune (2)
- **D5** closed 2026-05-01 (`a5bb68ad`) — ratchet baseline=0 in place. **Prune.**
- **D7** closed 2026-05-01 — 11 new fixture tests landed. **Prune.**

## Recurring (1)
- **2026-05-11** Weekly `scheduled_followups` digest — THIS ENTRY. Parent session bumps due → 2026-05-18.

---

## Substrate cross-check (prod `substrate_violations`)

| Invariant | Open | Total | Last fired |
|---|---|---|---|
| `bg_loop_silent` | 0 | 9 | 2026-05-01 23:57Z |
| `compliance_packets_stalled` | 0 | 1 | 2026-05-02 00:00Z |
| `pre_mig175_privileged_unattested` | **1 (sev3)** | 1 | 2026-05-12 11:57Z (last_seen) |
| `sigauth_enforce_mode_rejections` | 0 | 1 | 2026-04-28 14:01Z |
| `sigauth_post_fix_window_canary` | **NOT IN TABLE** | — | — confirmed removed per Session 217 commit `5328b272` |
| `substrate_assertions_meta_silent` | — | 0 | never fired |
| `partition_maintainer_dry` | — | 0 | never fired |
| `merkle_batch_stalled` | — | 0 | never fired |
| `email_dlq_growing` | — | 0 | never fired |
| `client_portal_zero_evidence_with_data` | — | 0 | never fired |
| `substrate_sla_breach` | — | 0 | never fired |

**Notes:**
- `pre_mig175_privileged_unattested` open since 2026-05-09 — informational, sev3, 3 pre-mig-175 historical fleet_orders (1 expired + 2 cancelled `*_emergency_access`) carrying no `attestation_bundle_id`. Covered by public advisory `OSIRIS-2026-04-13-PRIVILEGED-PRE-TRIGGER`. Not actionable; document-only.
- `sigauth_post_fix_window_canary` confirmed retired in `DISTINCT invariant_name` scan — removal step from #169 already shipped.

## Conflicts / dependency warnings (per D8)
- **5/15 calibration sweep (bg_heartbeat) ↔ 5/29 BUG 2 frontend sweep**: thematically distinct; no conflict.
- **5/15 calibration sweep ↔ 5/22 D1 per-control**: independent.
- **5/22 D1 (mig 269 + writer rewrite) ↔ 5/08 resolution_tier mig 268**: both touch migration sequence near 268–269. **Stagger:** ship `resolution_tier` widening FIRST (lower risk) before D1 lands its mig 269. Confirm migration numbering not collided.
- **5/29 BUG 2 sweep ↔ 5/29 BUG 3 sweep**: distinct codepaths but same calm-session bucket; do not bundle into one PR — both touch ratchet baselines.

## Single recommended next-session action

**Action the OVERDUE 2026-05-08 P1 (persistence-drift not escalating to L2).** It is the only OVERDUE item with auditor-visible substrate impact (406 L1 resolutions, 0 L2 over 7d directly contradicts the documented flywheel SLA) and is bounded to a research fork — no production write. The 5/05 sigauth canary removal is a 2-minute housekeeping cleanup that can ride alongside.
