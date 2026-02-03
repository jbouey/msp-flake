# Current State - Single Source of Truth

**Last Updated:** 2026-02-03
**Session:** 85
**Agent Version:** v1.0.52 (code complete)
**ISO Version:** v52 (built, deployment blocked)

---

## System Health

| Component | Status | Version | Notes |
|-----------|--------|---------|-------|
| VPS API | HEALTHY | - | https://api.osiriscare.net |
| Dashboard | HEALTHY | - | https://dashboard.osiriscare.net |
| Physical Appliance | OFFLINE | v1.0.49 | 192.168.88.246 - needs manual update |
| VM Appliance | BLOCKED | v1.0.49 | 192.168.88.247 - chicken-egg problem |
| Tests | PASSING | - | 858 + 24 tests |

---

## Current Blocker

**Chicken-and-Egg Update Problem:**
- Appliances on v1.0.49 crash processing update orders (missing `mcp_api_key_file`)
- Fix is in v1.0.52, but they need to process update to get it
- **Solution:** SSH manual intervention required

```bash
ssh jrelly@192.168.88.50      # iMac gateway
ssh root@192.168.88.247       # Then to VM appliance
# Manually patch appliance_agent.py and evidence.py
systemctl restart msp-compliance-agent
```

---

## Recent Changes (Session 85)

- Removed `dry_run` mode entirely (production mode default)
- Added circuit breaker for healing loops (max 5 attempts, 30min cooldown)
- All tests passing

---

## Immediate Next Actions

1. [ ] Manual VM appliance update (requires iMac access)
2. [ ] Verify fleet update system works post-fix
3. [ ] Evidence bundles to MinIO verification
4. [ ] First compliance packet generation

---

## Quick Reference

```bash
# Tests
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# SSH
ssh root@178.156.162.116      # VPS
ssh jrelly@192.168.88.50      # iMac Gateway
ssh root@192.168.88.246       # Physical Appliance
ssh root@192.168.88.247       # VM Appliance

# Health check
curl https://api.osiriscare.net/health
```

---

## File Map

| Need | File |
|------|------|
| Current state | `.agent/CURRENT_STATE.md` (this file) |
| Task list | `.agent/TODO.md` |
| Session logs | `.agent/sessions/YYYY-MM-DD-*.md` |
| Architecture | `docs/ARCHITECTURE.md` |
| Credentials | `.agent/LAB_CREDENTIALS.md` |
| Full history | `IMPLEMENTATION-STATUS.md` |
