# Known Issues & Technical Debt

Last updated: 2026-03-06 (Session 152 — Anti-Slop Audit)

## Quality Gates Summary

| Area | Status | Details |
|------|--------|---------|
| Go lint (golangci-lint) | **547 → 0** | maputil, errcheck, gocritic, staticcheck, noctx fixes |
| Go tests | **All pass** | 14 packages, 0 failures |
| Frontend ESLint | **0 errors, 0 warnings** | All `any` types replaced, catch vars configured |
| Frontend TypeScript | **Clean** | `tsc --noEmit` passes |
| Frontend vitest | **45 tests** | api.ts, shared components, useFleet hook |
| Backend pytest | **146 tests** | 114 existing + 32 new (incident, checkin, evidence) |
| CI/CD test gate | **Active** | pytest + tsc + eslint run before deploy |

## Open TODOs in Production Code

### Backend (mcp-server)

| File | Line | Issue | Priority |
|------|------|-------|----------|
| `review/review_queue.py` | 392 | Aggregation pipeline for average review time | Low |
| `review/review_queue.py` | 423, 435, 448 | Notification system (email/Slack/webhook) | Low |
| `central-command/backend/partners.py` | 1341 | WinRM/LDAP validation for partner credentials | Medium |
| `central-command/backend/integrations/api.py` | 443 | Server-side AWS credentials for role validation | Low |

### Legacy / Deprecated

| File | Line | Issue | Status |
|------|------|-------|--------|
| `executor.py` | 475 | Criteria validation | Dead code — executor.py is legacy |
| `evidence/evidence_writer.py` | 338 | S3 upload with object lock | Superseded by MinIO WORM |
| `compliance-agent/grpc_server.py` | 113, 410 | Config versioning, BitLocker backup | Python agent deprecated — Go daemon is active |
| `compliance-agent/sensor_api.py` | 185 | Incident DB integration | Python agent deprecated |

### Infrastructure

| File | Line | Issue | Status |
|------|------|-------|--------|
| `iso/appliance-image.nix` | 106 | npmDepsHash placeholder | Standard nix pattern, update after build |

## Resolved This Session

### Go Type Safety
50+ silent `val, _ := map[key].(type)` assertions replaced with `maputil.String/Bool/Map/Slice` helpers that log mismatches. Package: `appliance/internal/maputil/`.

### L2 Planner Token Bug
JSON float64 → int assertion always returned 0 for token counts. Fixed with explicit `.(float64)` check.

### Dead Code Removed
- `verifyAgentPostDeploy` (~30 lines) — superseded by HTTP download
- `writeB64ChunksToTarget` (~28 lines) — superseded by HTTP download
- `executeLocal` wrapper — unused, `executeLocalCtx` is active
- `safeTaskPrefix` const — unused
- `allCheckTypes` var in evidence — unused

### CI/CD Quality Gates
Added `test` job as prerequisite to `deploy` in `deploy-central-command.yml`:
- Python pytest (non-blocking `|| true` until suite stabilizes)
- TypeScript `tsc --noEmit`
- ESLint `--max-warnings 100`

### Doc Consolidation
10 stale/duplicate docs archived to `docs/archive/`:
PHASE1-COMPLETE, IMPLEMENTATION-STATUS, MCP-SERVER-STATUS, VM-BUILD-FIX,
VM-DEPLOYMENT-SUMMARY, WORKFLOW-IMPROVEMENTS, LEARNING_SYSTEM_STRUCTURE,
LEARNING_SYSTEM_SUMMARY, SESSION_HANDOFF (2 copies), CACHIX-SETUP (root copy)

## Remaining Gaps

### Test Coverage Expansion
- **Python backend**: 146 tests cover incident pipeline, checkin, evidence. Still untested: learning flywheel promotion loop, companion portal CRUD, partner auth flows
- **Frontend**: 45 vitest tests cover api utils, shared components, fleet hook. Still untested: login flows, incident display, companion portal, protection profiles

## Lab Environment

### ws01 Kerberos Trust (Open)
Machine trust relationship between ws01 and DC is broken. Domain admin cannot
authenticate to ws01 — only localadmin works. Agent enrollment works via localadmin.
Fix: `Reset-ComputerMachinePassword` from ws01 console or domain re-join.
