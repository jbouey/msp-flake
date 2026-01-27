# Session 74: Learning System Partner Promotion Workflow

**Date:** 2026-01-27
**Status:** COMPLETE
**Agent Version:** 1.0.48
**ISO Version:** v48

## Summary

Implemented complete partner-facing learning system promotion workflow with dashboard UI, API endpoints, database migration, and end-to-end testing.

## Accomplishments

### 1. Partner Learning API (learning_api.py)
Created 8 API endpoints for partner learning management:
- Stats dashboard (pending candidates, active rules, resolution rates)
- Candidate list and details
- Approve/reject workflow
- Promoted rules management
- Execution history

### 2. Database Migration (032_learning_promotion.sql)
- `promoted_rules` table for generated L1 rules
- `v_partner_promotion_candidates` view
- `v_partner_learning_stats` view
- Unique constraint for upsert operations
- Nullable columns for dashboard approvals

### 3. Frontend Component (PartnerLearning.tsx)
- Stats cards with metrics
- Candidates table with approve/reject
- Approval modal with custom name and notes
- Promoted rules list with enable/disable toggle
- Empty states for new partners

### 4. VPS Deployment Architecture Discovery
Critical finding: Docker compose volume mounts override built images.
- Backend: `/opt/mcp-server/dashboard_api_mount/` → `/app/dashboard_api`
- Frontend: `/opt/mcp-server/frontend_dist/` → `/usr/share/nginx/html`

### 5. End-to-End Testing
- Created test pattern data
- Approved pattern with custom name
- Rule generated: `L1-PROMOTED-PRINT-SP`
- Stats API verified working

## Files Created
- `mcp-server/central-command/backend/learning_api.py` (~350 lines)
- `mcp-server/central-command/backend/migrations/032_learning_promotion.sql` (~93 lines)
- `mcp-server/central-command/frontend/src/partner/PartnerLearning.tsx` (~500 lines)

## Files Modified
- `mcp-server/central-command/backend/main.py` - Added learning_router
- `mcp-server/central-command/frontend/src/partner/PartnerDashboard.tsx` - Added Learning tab
- `mcp-server/central-command/frontend/src/partner/index.ts` - Export

## Bug Fixes
1. **InvalidColumnReferenceError** - Added unique constraint for ON CONFLICT
2. **NotNullViolationError** - Made 6 columns nullable for dashboard approvals
3. **ModuleNotFoundError** - Deploy to host mount paths, not image paths

## Key Learnings
1. VPS Docker compose mounts override built images - must deploy to host paths
2. Dashboard operations need different constraints than agent operations
3. Columns should be nullable when data sources vary

## Next Session Priorities
1. Test promoted rules sync to agents
2. Verify agent applies promoted rules in L1 engine
3. Monitor learning flywheel in production

## Git Commits
- `feat: Learning promotion workflow database fixes` (migration update)
