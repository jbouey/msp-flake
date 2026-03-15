# Session 166: Compliance Scoring Total Basket + Devices at Risk + Chrome GPU Fix

**Date:** 2026-03-10
**Started:** 16:14
**Previous Session:** 165

---

## Goals
- [x] Add per-device drift visibility (Devices at Risk panel)
- [x] Fix Chrome GPU white-screen crash from backdrop-filter
- [x] Fix compliance score to include ALL platforms (total basket)
- [x] Score distinct compliance issues, not raw alert count
- [x] Respect disabled drift checks in incident scoring

---

## Progress

### Completed

1. **Per-Device Drift Visibility** — Backend endpoints (admin + client), DevicesAtRisk.tsx component, hostname-filtered Incidents click-through
2. **Chrome GPU Fix** — Opaque dark mode backgrounds, disabled backdrop-filter in dark mode
3. **Total Basket Scoring** — Active incidents from Linux/NixOS/Windows now penalize score. 60+ check types mapped to 8 categories
4. **Distinct Issue Counting** — 370 alerts → 20 distinct (check_type × device) pairs. Alert volume ≠ compliance posture
5. **Disabled Check Exclusion** — Site drift config respected for both bundles and incidents

### Blocked
- macOS agent results not yet flowing into compliance scoring basket

---

## Files Changed

| File | Change |
|------|--------|
| `backend/routes.py` | devices-at-risk endpoint + total basket scoring |
| `backend/client_portal.py` | devices-at-risk endpoint + total basket scoring |
| `frontend/src/client/DevicesAtRisk.tsx` | NEW — expandable device risk cards |
| `frontend/src/client/ClientDashboard.tsx` | Added DevicesAtRisk component |
| `frontend/src/pages/SiteDetail.tsx` | Added DevicesAtRisk + click-through |
| `frontend/src/pages/Incidents.tsx` | Hostname filter + expanded category map |
| `frontend/src/index.css` | Opaque dark mode glass, no backdrop-filter |

## Commits
- `87c1382` feat: per-device drift visibility
- `cf1771e` fix: eliminate backdrop-filter in dark mode
- `d13d311` fix: compliance score includes Linux/NixOS incidents
- `893e086` fix: respect disabled drift checks in scoring
- `83e999a` fix: score distinct issues per device, not raw alerts

---

## Next Session

1. macOS agent integration into compliance scoring + evidence pipeline
2. Verify infographic renders correct scores after Chrome GPU fix
3. Add macOS check types to category map once agent data flows
