# Gate A — #121 Partner dashboard render performance at 250 rows (multi-device P2-2)

**Date:** 2026-05-17
**Reviewer:** fresh-context fork (general-purpose subagent, opus-4.7[1m])
**Verdict: APPROVE-WITH-FIXES** (profile-first, narrow scope)

## 2-line summary

`PartnerFleetAppliances` already paginates server-side (cursor, limit=50, hard cap 100) — that surface is fine. The remaining risk is `PartnerDashboard` Sites tab rendering ALL 250 sites in 3 non-virtualized `<tbody>` maps; recommend profile-first to confirm before shipping virtualization.

## Recommended approach

**Profile FIRST, ship a measured fix in #121-B.** Do NOT speculatively add `@tanstack/react-virtual` without a Chrome-DevTools-Performance profile screenshot showing >100ms scripting/render at 250 rows. Reasoning:
- `PartnerFleetAppliances` is already paginated 50/page — at 250 appliances the user sees 50, scrolls (server-side load-more), no client perf issue at all.
- `PartnerDashboard` Sites tab renders all 250 in one go (3 maps × 250 = 750 row-renders + 1 portfolioAvg `useMemo` reduce that's already memoized). At ~12 fields per row this is well within React's comfort zone (~1-5ms total render); virtualization may be premature.
- `/api/partners/me/sites` payload at 250 rows ≈ 75KB JSON over the wire — small.
- The SQL is the SECOND-most-likely bottleneck: GROUP BY across `sites` × `site_appliances` × `site_go_agent_summaries` at 250 sites with `gas` row-per-site is ~250-1000 row scan, sub-100ms with proper indexes.

**Profile harness #121-A** (1-2 commits, no prod risk):
1. Add `tests/perf/test_partner_sites_endpoint_p95.py` — seed 250 sites under a fake partner, hit `/me/sites` 50×, assert p95 < 500ms.
2. Add a manual-run `docs/perf/partner-dashboard-profile.md` checklist for DevTools-Performance (record 5s scroll, capture scripting/rendering/painting numbers, attach screenshot).
3. Land the harness. If p95 backend > 500ms OR scripting > 100ms — open #121-B with the specific fix (server pagination on /me/sites OR react-virtual on Sites tab OR React.memo per row).

## Per-lens verdict (7 lines)

- **Steve (eng quality):** APPROVE-WITH-FIXES — speculative virtualization adds dep + complexity without numbers; profile first.
- **Maya (legal/HIPAA):** APPROVE — no PHI in any of these fields; page-size cap stays at 100; no new email/log surface.
- **Carol (security):** APPROVE-WITH-FIXES — **P0 if /me/sites gets cursor-paginated:** preserve `require_partner_role` + `partner_id = $1` WHERE clause + RLS isolation gate `tests/test_cross_partner_isolation.py` and `test_client_appliances_field_allowlist.py` parity for the new field allowlist.
- **Coach (consistency):** APPROVE-WITH-FIXES — **P1:** mirror the EXISTING pattern from `PartnerFleetAppliances` (cursor + status_filter, NOT React Query, NOT react-virtual — codebase has zero `@tanstack/react-virtual` adopters; introducing it is greenfield, not "mirror existing").
- **DBA:** APPROVE-WITH-FIXES — **P1:** if profile shows backend slow, drop the per-row GROUP BY in favor of LATERAL subqueries (matches `/me/appliances` pattern; `COUNT(DISTINCT sa.id)` over GROUP BY is the typical hot path).
- **PM/UX:** APPROVE — pagination + filter + summary banner is a known-good shape at fleet scale; copy from `PartnerFleetAppliances`.
- **Counsel 7-rule filter:** Rules 1/2/3/4/5/6/7 all clean — no canonical-metric leak, no PHI, no privileged action, no orphan collector, no stale doc claim, no BAA bypass, no unauth context (endpoint is `require_partner_role`).

## P0 / P1 / P2

- **P0 (BLOCK ship until satisfied):** Profile evidence (DevTools screenshot OR p95 test output) cited in any subsequent #121-B PR body — no speculative virtualization.
- **P0 if /me/sites pagination is chosen:** Preserve `partner_id = $1` + `status != 'inactive'` filter + soft-delete `sa.deleted_at IS NULL` (caught by `test_client_portal_filters_soft_deletes.py`); add cross-partner isolation test for the cursor path; field allowlist parity (RT33 Carol Layer-2 leak veto).
- **P1:** Mirror `PartnerFleetAppliances` shape exactly — same `getJson` helper, same `next_cursor` + `limit` + status filter contract, same KPI banner pattern.
- **P1:** If react-virtual is introduced, add it as a new project convention: cite it in CLAUDE.md `perf | virtual scroll` row + add ≥1 other adopter in same commit (avoid one-off pattern).
- **P2:** Add an integration test pinning `/me/sites` returns ≤500ms at 250 sites in CI fixture.

## Anti-scope

- Do NOT add `@tanstack/react-virtual` without a profile screenshot.
- Do NOT replace `useEffect+fetch` with React Query in this PR — that's a fleetwide refactor, not a perf fix.
- Do NOT touch `PartnerFleetAppliances` — it's already pagination-correct.
- Do NOT add server-side pagination to `/me/sites` without preserving the portfolioAvg aggregate (frontend currently reduces ALL sites; pagination would require a separate `/me/sites/summary` endpoint or moving avg server-side — that's design work, not a one-commit fix).

## File layout (Phase A — profile harness)

- `tests/perf/test_partner_sites_endpoint_p95.py` (NEW) — 250-site seed, 50× hit, p95 < 500ms
- `docs/perf/partner-dashboard-profile.md` (NEW) — manual DevTools checklist
- THIS verdict file

## Existing react-virtual reference

**Zero existing adopters.** `grep -r "@tanstack/react-virtual\|useVirtualizer" frontend/src` returns no matches. CLAUDE.md mentions it as a perf pattern but no code has adopted it. Introducing it now would be greenfield; coach P1 verdict above requires ≥1 sibling adopter in the same PR if chosen.
