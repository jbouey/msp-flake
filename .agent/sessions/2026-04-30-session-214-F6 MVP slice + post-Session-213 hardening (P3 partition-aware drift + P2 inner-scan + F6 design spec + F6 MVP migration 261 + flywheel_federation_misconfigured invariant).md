# Session 214 — F6 MVP slice + post-Session-213 hardening

**Date:** 2026-04-30 (overnight continuation from Session 213)
**Started:** 03:15 UTC
**Previous Session:** 213

---

## Goals

- [x] Item 3 — partition-aware drift detection (P3, deferred from Session 213 F4-followup)
- [x] Item 4 — defense-in-depth inner scan within auto-EXEMPT files (P2, round-table deferred)
- [x] Item 1 — F6 design spec (multi-day, design-first)
- [x] Item 2 — confirmed 2026-05-05 sigauth watch is passive
- [x] F6 MVP slice — schema + feature flag scaffolding
- [x] F6 fast-follow — flywheel_federation_misconfigured sev3 substrate invariant

---

## Progress

### Items 3 + 4 — partition-aware drift + inner-scan (commit `86df7625`)

Two-pass UNION query in `_check_rename_site_immutable_list_drift`:
- Pass 1: trigger directly on the table (regular + partitioned parents — relkind expansion from `'r'` to `IN ('r', 'p')` is the actual fix; round-table corrected my narrative)
- Pass 2: trigger on a partition child → surface the PARENT (legacy/manual case)

Defense-in-depth inner scan in `_inner_scan_misuse()` runs on auto-EXEMPT migration files; 3 regex patterns catch actual misuse (UPDATE compliance_bundles + canonical_site_id within 500 chars; INSERT same; reverse-direction). `_strip_sql_comments()` strips `-- ` and `/* */` first so docs co-mention doesn't false-positive.

5 inner-scan self-tests + ratchet wired correctly. Round-table verdict: READY (first pass), 2 polish items (mig 191 narrative correction + runbook Verification SQL update) folded in.

### F6 design spec (commit `86df7625`)

`docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` — full scoping doc for Federation tier (local/org/platform). Three-week calibration window referenced; 4 round-table risks pre-anticipated; explicit "do NOT bundle with hotfix sessions".

### F6 MVP slice (commit `7dee6d6c`)

Migration 261 — `flywheel_eligibility_tiers` table. 3 seed rows (local/org/platform), ALL `enabled=FALSE`, `calibrated_at=NULL`. Schema-level CHECK constraints enforce calibration discipline (org_isolation_required=TRUE for tier_level >=1; distinct_orgs/sites required when calibrated_at is set).

main.py Step 2 reads `FLYWHEEL_FEDERATION_ENABLED` env var (lenient parser matches `assertions.py::L2_ENABLED` sibling). Triple-gate activation: env=true AND tier.enabled=TRUE AND calibrated_at IS NOT NULL. Default OFF — production behavior unchanged.

Round-table verdict: NEEDS-IMPROVEMENT. 2 P1 + 3 P2 ship-now items integrated:
- P1-1: lenient parser instead of strict `== "true"` (sibling-subsystem mismatch was operator footgun)
- P1-2: NULL out distinct_orgs/sites in higher-tier seeds + CHECK constraints forcing calibration to set them
- P2-3: org_isolation_required column with CHECK (HIPAA boundary at schema level)
- P2-4: logger.info → logger.warning + TODO for Prom counter
- P2-5: reconciled min_distinct_sites=1 on local seed comment

Reviewer said "the structural slice is sound. You did not ship a foot-gun. The OFF-state preservation property holds."

### F6 fast-follow — flywheel_federation_misconfigured invariant (commit `21bdda0a`)

Sev3 substrate invariant fires when env flag is truthy but no tier is enabled+calibrated. Three-list lockstep: Assertion + _DISPLAY_METADATA + runbook. Round-table verdict: READY first pass (1 P3 + 1 P2 sharpening folded in — explicit "no calibration migration shipped yet" callout in runbook Path B).

Total invariant count: **47 → 48**.

---

## Files Changed

| File | Change | Commit |
|------|--------|--------|
| `assertions.py` | partition-aware drift query (Pass 1 + Pass 2 UNION) | `86df7625` |
| `tests/test_canonical_not_used_for_compliance_bundles.py` | inner-scan + comment stripper + 5 self-tests | `86df7625` |
| `substrate_runbooks/rename_site_immutable_list_drift.md` | Verification SQL updated to two-pass form | `86df7625` |
| `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` | NEW — F6 design spec | `86df7625` |
| `migrations/261_flywheel_eligibility_tiers.sql` | NEW — F6 MVP table + 3 disabled tiers + CHECK constraints | `7dee6d6c` |
| `mcp-server/main.py` | Step 2 federation read path (lenient parser, triple-gate) | `7dee6d6c` |
| `tests/test_flywheel_federation_scaffold.py` | NEW — 11 source-level + 2 DB-gated tests | `7dee6d6c` |
| `assertions.py` | flywheel_federation_misconfigured Assertion + _DISPLAY_METADATA + check fn + `import os` | `21bdda0a` |
| `substrate_runbooks/flywheel_federation_misconfigured.md` | NEW — runbook for sev3 invariant | `21bdda0a` |

---

## Verification

All deploys verified post-CI:
- mig 260 (telemetry index) applied 2026-04-30 02:51:26Z; planner uses `Index Only Scan using idx_execution_telemetry_site_created` (sub-ms COUNT(*))
- mig 261 (federation scaffold) applied 2026-04-30 07:09:41Z; 3 tiers all disabled+uncalibrated; CHECK constraints reject invalid INSERT correctly; FLYWHEEL_FEDERATION_ENABLED unset in prod (OFF state preserved)
- Drift query returns 0 rows post-fix (substrate is self-consistent on the immutable-list class)
- Empirical: appliance_heartbeats has trigger on the parent (`relkind='p'`), not children — confirms the round-table reviewer's narrative correction. Pass 1's `relkind IN ('r', 'p')` expansion is what catches it; Pass 2 is dormant for this case (exists for legacy/manual cases).

---

## Round-table scoreboard (Session 214)

| # | Round-table | Verdict | Outcome |
|---|---|---|---|
| 1 | P3+P2 hardening (items 3+4) | READY (1st pass) | 2 polish items folded in |
| 2 | F6 MVP slice | NEEDS-IMPROVEMENT (2P1+3P2) | All integrated same-commit |
| 3 | flywheel_federation_misconfigured | READY (1st pass) | 1 P2 sharpening folded in |

**Cumulative across Session 213 + 214: 10 round-tables, 10 approvals.**

---

## Next Session

1. **F6 phase 2** — Tier 1 (org-aggregated) + Tier 2 (platform) read paths. Multi-day, **needs cross-org HIPAA round-table**. Spec at `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md`.
2. **F6 phase 3** — Threshold calibration after 2-3 week observation window. Migration that flips at least one tier to `enabled=TRUE` with calibrated values + `calibrated_at=NOW()`. Round-table required (data + HIPAA).
3. **2026-05-05** — Sigauth invariant watch (passive — auto-reopens task #169 if `sigauth_enforce_mode_rejections` or `sigauth_post_fix_window_canary` fires). After 2026-05-05, REMOVE `sigauth_post_fix_window_canary` from `ALL_ASSERTIONS` if both stayed silent.
4. **(Optional)** F7 endpoint extension — add `federation` section to `/api/admin/sites/{id}/flywheel-diagnostic` showing current tier state per-site. Phase 2 follow-up; not urgent.
