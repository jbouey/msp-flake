# Session 75 - Production Readiness Audit & Critical Security Fixes

**Date:** 2026-01-27
**Duration:** Extended session (continuation from Session 74)
**Agent Version:** v1.0.49

---

## Session Goals

1. Complete production readiness audit
2. Fix critical security issues
3. Verify learning system sync working
4. Fix infrastructure issues on physical appliance

---

## Key Accomplishments

### 1. Production Readiness Audit - COMPLETE

Created comprehensive 10-section audit document covering:
- Environment Variables & Secrets (no hardcoded secrets)
- Clock Synchronization (VPS and appliance NTP verified)
- DNS Resolution (both systems resolve correctly)
- File Permissions (signing key now 600)
- TLS Certificate (expires Mar 31, ~63 days)
- Database Connection Pooling (pool settings configured)
- Async/Blocking Code (no blocking calls in async)
- Rate Limits & External Services (retry logic, circuit breakers)
- Systemd Service Ordering (correct dependencies)
- Proto & Contract Drift (protos in sync)

**Result:** System rated **Production Ready**
- 0 Critical Issues (1 fixed during audit)
- 3 Warning Issues (non-blocking)

### 2. CRITICAL Security Fix - VPS Signing Key

**Issue:** `/opt/mcp-server/secrets/signing.key` had 644 permissions
- World-readable on the VPS
- Anyone with server access could sign orders

**Fix Applied:**
```bash
chmod 600 /opt/mcp-server/secrets/signing.key
chown 1000:1000 /opt/mcp-server/secrets/signing.key
```

### 3. STATE_DIR Path Mismatch Fix

**Issue:** "Read-only file system" error on appliance

**Root Cause:** Python code defaults to `/var/lib/msp-compliance-agent`, but NixOS appliance uses `/var/lib/msp`

**Fixes:**
1. Created symlink on appliance (immediate)
2. Added `STATE_DIR=/var/lib/msp` to NixOS configs (permanent)
3. Added environment override support to `appliance_config.py`

### 4. Healing DRY-RUN Mode Fix

**Issue:** Healing stuck in DRY-RUN despite `HEALING_DRY_RUN=false` env var

**Root Cause:** Config loader only read YAML, ignored env vars

**Fix:** Added environment variable override support:
```python
env_overrides = {
    'healing_dry_run': os.environ.get('HEALING_DRY_RUN'),
    'state_dir': os.environ.get('STATE_DIR'),
    'log_level': os.environ.get('LOG_LEVEL'),
}
```

### 5. Execution Telemetry Datetime Fix

**Issue:** 500 errors on `/api/agent/executions` endpoint

**Root Cause:** PostgreSQL asyncpg requires datetime objects, received ISO timestamp strings

**Fix:** Added `parse_iso_timestamp()` helper function to handle both datetime objects and ISO strings

### 6. Learning Sync Verification

Verified full learning system data flywheel:
- Pattern sync: Working (8 patterns in aggregated_pattern_stats)
- Execution telemetry: Working (200 OK responses)
- Promoted rules sync: Working (returns YAML to agents)

---

## Files Created

| File | Lines | Description |
|------|-------|-------------|
| `docs/PRODUCTION_READINESS_AUDIT.md` | ~373 | 10-section production audit |
| `scripts/prod-health-check.sh` | ~315 | Automated health check script |

## Files Modified

| File | Change |
|------|--------|
| `iso/appliance-disk-image.nix` | Added STATE_DIR env var |
| `iso/appliance-image.nix` | Added STATE_DIR env var |
| `packages/compliance-agent/src/compliance_agent/appliance_config.py` | Env var override support |
| `mcp-server/main.py` | parse_iso_timestamp() helper |

## VPS Changes Applied

| Change | Location | Method |
|--------|----------|--------|
| signing.key permissions | `/opt/mcp-server/secrets/` | SSH + chmod/chown |
| main.py datetime fix | `/opt/mcp-server/app/` | Docker rebuild |

---

## Git Commits

| Hash | Message |
|------|---------|
| `8b712ea` | feat: Production readiness audit and health check script |
| `328549e` | fix: Mark critical signing.key permission issue as resolved |
| `3c97d01` | fix: Add STATE_DIR env var and environment override support |
| `8f029ef` | fix: Parse ISO timestamp strings in execution telemetry endpoint |

---

## Remaining Warning Issues

1. **SQLite tools missing on appliance** - Can't verify DB integrity (add sqlite3 to NixOS config)
2. **Windows lab VMs unreachable** - May be powered off
3. **TLS cert expires ~63 days** - Verify Let's Encrypt auto-renewal configured

---

## Next Session Priorities

1. Deploy ISO v49 to physical appliance (includes all fixes)
2. Add sqlite3 to appliance image
3. Start Windows VMs and verify time sync
4. Test full A/B partition update cycle
5. Evidence bundles uploading to MinIO

---

## Key Learnings

1. **Docker volume mounts override built images** - Deploy to `/opt/mcp-server/app/` not `/root/msp-iso-build/`
2. **Container user is UID 1000** - File ownership must match for container access
3. **asyncpg requires datetime objects** - ISO strings cause database errors
4. **Environment variables need explicit override support** in Pydantic config loaders
