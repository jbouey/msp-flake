# Layer 4: Multi-Appliance Dashboard UX

**Date:** 2026-04-06
**Scope:** Frontend-only (one minor backend filter addition)
**Effort:** ~1 session

## Problem

Site Detail page shows appliances as a static, non-interactive list. No way to drill into per-appliance health, compliance, or incidents. Partners managing multi-appliance sites can't diagnose which appliance has issues.

## Changes

### 1. Expandable Appliance Cards (Site Detail)

Replace plain list rows with clickable mini-cards. Click expands an inline detail panel.

**Collapsed card shows:**
- Status dot (online/stale/offline with color)
- Display name + hostname
- Agent version + last checkin (relative time, e.g. "2 min ago")
- Mini compliance bar (% from `appliance.health.compliance`)
- Assigned target count badge

**Expanded panel shows:**
- Compliance breakdown grid: patching, firewall, encryption, backup, logging, antivirus — each with score + status color
- Connectivity metrics: checkin freshness, healing success rate, order execution rate
- IP addresses
- Assigned targets list

### 2. Per-Appliance Incident Filter

Add filter chips above the incidents table on Site Detail: "All" / per-appliance display_name. Selecting an appliance filters incidents by `details->>'hostname'` matching that appliance.

**Backend addition:** Add optional `hostname` query param to the incidents endpoint in `routes.py`:
```sql
AND ($N IS NULL OR details->>'hostname' = $N)
```

### 3. Org Dashboard Appliance Dots

In the org dashboard sites table, replace the plain `3/3 online` text with color-coded dots — one per appliance (green = online, red = offline, yellow = stale). Hovering a dot shows the display_name.

## Files

| File | Change |
|------|--------|
| `frontend/src/pages/ClientDetail.tsx` | Expandable appliance cards, incident filter chips |
| `frontend/src/components/fleet/ApplianceCard.tsx` | NEW: reusable collapsed/expanded appliance card |
| `frontend/src/pages/OrgDashboard.tsx` | Appliance status dots per site row |
| `backend/routes.py` | Optional hostname filter on incidents query |

## Out of Scope

- Separate appliance detail page (keep it inline)
- Mesh topology visualization
- Appliance status history/timeline
- Appliance comparison view
