# Partner dashboard profile checklist

**Owner:** #121 Phase A profile harness
**Use when:** A partner reports the dashboard feels slow at fleet
scale (>100 sites) and you need evidence BEFORE choosing a fix.

Per Gate A (`audit/coach-121-partner-dashboard-gate-a-2026-05-17.md`):
do NOT speculatively add `@tanstack/react-virtual` or server-side
pagination without numbers. Profile first, ship the measured fix
in #121-B with screenshot + p95 evidence.

## Manual DevTools-Performance capture

### 1. Seed a 250-site fleet locally

```bash
# Requires PG_TEST_URL pointing at a dev/staging Postgres (NOT prod).
export PG_TEST_URL=postgresql://mcp:mcp@localhost:5432/mcp_dev
python3.11 -c "
import asyncio, asyncpg, uuid, os
async def go():
    c = await asyncpg.connect(os.environ['PG_TEST_URL'])
    pid = str(uuid.uuid4())
    await c.execute(
        \"INSERT INTO partners (id, name, status, contact_email) \"
        \"VALUES (\$1::uuid, 'PROFILE-#121', 'active', \"
        \"'profile@example.invalid')\",
        pid,
    )
    for i in range(250):
        await c.execute(
            \"INSERT INTO sites (site_id, clinic_name, partner_id, \"
            \"tier, industry, status, client_org_id, created_at, updated_at) \"
            \"VALUES (\$1, \$2, \$3::uuid, 'small', 'healthcare', \"
            \"'online', '00000000-0000-4000-8000-00000000ff05'::uuid, \"
            \"NOW(), NOW())\",
            f'profile-121-site-{i:03d}', f'Profile Clinic {i:03d}', pid,
        )
    print(f'Seeded partner {pid} with 250 sites.')
    await c.close()
asyncio.run(go())
"
```

### 2. Mint a partner session token for that partner_id

```bash
# Via admin API or psql — depends on local auth setup. The token
# must be a `osiris_partner_session` cookie value valid for the
# partner_id seeded above.
```

### 3. Browser capture

1. Open Chrome / Chromium → DevTools (Cmd+Option+I) → Performance tab
2. Navigate to the partner dashboard (`/partner/dashboard` or equivalent)
3. Click the record button (•)
4. Scroll the Sites tab from top to bottom over 5 seconds
5. Click stop
6. Screenshot the **bottom panel** showing:
   - Scripting time (yellow)
   - Rendering time (purple)
   - Painting time (green)
   - System time (gray)

### 4. Backend p95 capture

```bash
# Run the perf harness against the SAME dev instance + same partner:
export PERF_RUN=1
export PARTNER_SESSION_COOKIE=<token from step 2>
export PERF_TEST_BASE_URL=http://localhost:8000
cd mcp-server/central-command/backend
python3.11 -m pytest tests/perf/test_partner_sites_endpoint_p95.py -v -s
```

The harness will print: `#121 perf: n=50 p50=... p95=... p99=... mean=...`

### 5. Decide the fix

**If frontend Scripting < 100ms AND backend p95 < 500ms:**
- No fix needed. Close #121 with the profile evidence + numbers.

**If frontend Scripting > 100ms (DOM bottleneck at ~750 row-renders):**
- Open #121-B for react-virtual on the Sites tab.
- Per Gate A Coach P1: react-virtual is greenfield in this codebase
  (zero existing adopters). Add ≥1 sibling adopter in the same PR
  + cite in CLAUDE.md `perf | virtual scroll` row.

**If backend p95 > 500ms (SQL bottleneck on the GROUP BY):**
- Open #121-B for server-side cursor pagination on `/me/sites`.
- Per Gate A Carol P0: preserve `partner_id = $1` + soft-delete
  filter + cross-partner isolation tests.

**If both — fix backend first (smaller surface), re-profile, then
decide on frontend.**

### 6. Cleanup

```bash
python3.11 -c "
import asyncio, asyncpg, os
async def go():
    c = await asyncpg.connect(os.environ['PG_TEST_URL'])
    await c.execute(\"DELETE FROM site_appliances WHERE site_id LIKE 'profile-121-site-%'\")
    await c.execute(\"DELETE FROM sites WHERE site_id LIKE 'profile-121-site-%'\")
    await c.execute(\"DELETE FROM partners WHERE name = 'PROFILE-#121'\")
    await c.close()
asyncio.run(go())
"
```

## Reference

- `mcp-server/central-command/frontend/src/partner/PartnerDashboard.tsx`
  — the Sites tab maps at L666/L699/L1080, no pagination/virtualization.
- `mcp-server/central-command/frontend/src/partner/PartnerFleetAppliances.tsx`
  — already cursor-paginated (50/page). Mirror this shape if #121-B
  needs server pagination.
- `mcp-server/central-command/backend/partners.py:1217` — `/me/sites`
  endpoint. Currently GROUP BY with sub-aggregates. LATERAL pattern
  at `/me/appliances` is the recommended target if SQL fix needed.
- `tests/perf/test_partner_sites_endpoint_p95.py` — the harness this
  doc operates.
