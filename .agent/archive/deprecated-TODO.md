# Current Tasks

**Last Updated:** 2026-02-03 | **Session:** 85

---

## Active Blockers

| Priority | Task | Status | Notes |
|----------|------|--------|-------|
| P0 | Manual VM appliance update | BLOCKED | Requires iMac gateway access |

---

## Current Sprint Tasks

### In Progress
- [ ] Break chicken-egg update cycle (manual SSH intervention)

### Ready
- [ ] Verify fleet update system post-fix
- [ ] Evidence bundles → MinIO verification
- [ ] First compliance packet generation

### Backlog
- [ ] Go Agent CGO dependency fix (switch to modernc.org/sqlite)
- [ ] Windows SCM integration for Go agent
- [ ] 30-day monitoring period

---

## Recently Completed (Session 85)

- [x] Remove dry_run mode from all configs
- [x] Add circuit breaker for healing loops
- [x] Fix mcp_api_key_file backward compatibility
- [x] CSRF exemptions for Fleet/Orders APIs
- [x] MAC address format normalization

---

## Session History

For full session history, see `.agent/sessions/` directory.

Recent sessions:
- Session 85 (2026-02-03): Dry-run removal, circuit breaker
- Session 84 (2026-02-01): Fleet Update v52, compatibility fixes
- Session 83 (2026-02-01): Runbook security audit, project analysis
- Session 82 (2026-02-01): Production security audit, frontend optimization

---

## Quick Commands

```bash
# Run tests
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# Check appliance status
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c 'SELECT site_id, agent_version, last_checkin FROM appliances ORDER BY last_checkin DESC'"
```
