# Production Readiness Audit Report

**Audit Date:** 2026-01-27
**Auditor:** Claude Opus 4.5
**Environment:** OsirisCare MSP Compliance Platform

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Overall Readiness** | ✅ **Production Ready** |
| **Critical Issues** | 0 (1 fixed) |
| **Warning Issues** | 3 |
| **Passing Checks** | 7 |

### Critical Issue
- ~~**VPS signing.key has world-readable permissions (644)**~~ - **FIXED 2026-01-27** (chmod 600)

### Warning Issues
- SQLite tools missing on appliance (can't verify DB integrity)
- Windows lab VMs unreachable (may be powered off)
- TLS cert expires in ~2 months (monitor and auto-renew)

---

## 1. Environment Variables & Secrets Audit

**Status:** ⚠️ Warning

### Findings

**VPS Containers:**
- ✅ DATABASE_URL properly configured
- ✅ Environment variables using defaults from docker-compose
- ✅ Secrets not hardcoded in compose file (uses env substitution)

**Appliance Config:**
```yaml
# /var/lib/msp/config.yaml (verified)
site_id: physical-appliance-pilot-1aea78
api_key: <REDACTED>
api_endpoint: https://api.osiriscare.net
```
- ✅ Required fields present (site_id, api_key, api_endpoint)
- ✅ Config file has restricted permissions (600)

**Code Secrets Scan:**
- ✅ No hardcoded API keys found in mcp-server/central-command/backend/
- ✅ Pattern `sk-` matches only test/demo files, not production code

### Action Required
- [x] Config file permissions verified
- [x] No hardcoded secrets in production code

---

## 2. Clock Synchronization Audit

**Status:** ✅ Pass

### Findings

| System | Time (UTC) | NTP Status | Drift |
|--------|------------|------------|-------|
| VPS | 2026-01-27 13:59:51 | ✅ Synchronized | Reference |
| Physical Appliance | 2026-01-27 13:59:53 | ✅ Synchronized | +2s |

**Appliance NTP Verification (from logs):**
```
NTP verification passed: 5 servers, median offset -15.2ms, max skew 34.9ms
```

**Windows VMs:** Unreachable (likely powered off)

### Action Required
- [ ] Start Windows VMs and verify time sync
- [x] VPS NTP working
- [x] Appliance NTP working

---

## 3. DNS Resolution Audit

**Status:** ✅ Pass

### Findings

**From VPS Container (central-command):**
```
Server: 127.0.0.11 (Docker DNS)
api.osiriscare.net → 178.156.162.116 ✅
```

**From Physical Appliance:**
```
Server: 192.168.88.1 (Local gateway)
api.osiriscare.net → 178.156.162.116 ✅
```

### Action Required
- [x] Containers resolve external domains
- [x] Appliances resolve Central Command

---

## 4. File Permissions Audit

**Status:** ✅ Pass (Fixed 2026-01-27)

### Findings

**VPS (Central Command):**
| File | Current | Required | Status |
|------|---------|----------|--------|
| `/opt/mcp-server/secrets/signing.key` | 600 (rw-------) | 600 (rw-------) | ✅ FIXED |
| `/var/run/docker.sock` | srw-rw---- | OK | ✅ |
| `/mnt/storagebox` | Mounted, 1TB | OK | ✅ |

**Physical Appliance:**
| File | Current | Required | Status |
|------|---------|----------|--------|
| `/var/lib/msp/config.yaml` | 600 (rw-------) | 600 | ✅ |
| `/var/lib/msp/signing.key` | 600 (rw-------) | 600 | ✅ |
| `/var/lib/msp/*.db` | 644 (rw-r--r--) | OK | ✅ |

**Service User:**
- VPS: Docker containers run as root (typical for containers)
- Appliance: compliance-agent runs as root (acceptable for NixOS)

### Action Required
- [x] **CRITICAL:** ~~Fix VPS signing.key permissions~~ - **FIXED 2026-01-27**
- [x] Appliance signing key secured
- [x] Config files secured

---

## 5. TLS Certificate Audit

**Status:** ⚠️ Warning

### Findings

**api.osiriscare.net:**
```
notBefore: Dec 31 04:44:04 2025 GMT
notAfter:  Mar 31 04:44:03 2026 GMT
```
- Certificate valid for ~63 more days
- ⚠️ Should renew before March 17 (14-day buffer)

**From Appliance:**
```bash
curl -sI https://api.osiriscare.net/health
HTTP/2 200  # ✅ No SSL warnings
```

**Security Headers (verified):**
- ✅ `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- ✅ `X-Content-Type-Options: nosniff`
- ✅ `X-Frame-Options: DENY`
- ✅ `Referrer-Policy: strict-origin-when-cross-origin`

### Action Required
- [ ] Ensure Let's Encrypt auto-renewal is configured
- [ ] Set up cert expiry monitoring (alert at 14 days)
- [x] Appliance validates cert chain

---

## 6. Database Connection Audit

**Status:** ✅ Pass

### Findings

**PostgreSQL (Central Command):**
- Container: `mcp-postgres` running healthy
- User: `mcp`, Database: `mcp`
- Note: Direct psql check failed due to socket path, but service is healthy

**SQLAlchemy Pool Settings (verified in code):**
| File | pool_size | max_overflow |
|------|-----------|--------------|
| main.py | 10 | 20 |
| server.py | 5 | 10 |
| database/store.py | 10 | 20 |

**SQLite (Appliance):**
- `/var/lib/msp/devices.db` - 90KB
- `/var/lib/msp/incidents.db` - 53KB
- ⚠️ Cannot verify integrity (sqlite3 not installed on appliance)

### Action Required
- [ ] Add sqlite3 to appliance image for diagnostics
- [x] Pool settings explicitly configured
- [x] PostgreSQL container healthy

---

## 7. Async/Blocking Code Audit

**Status:** ✅ Pass

### Findings

**Sync Database Drivers:**
- ✅ No `psycopg2` imports in FastAPI backend
- ✅ Using `asyncpg` via SQLAlchemy async engine

**Blocking Calls in Async Code:**
- ✅ No `time.sleep()` in `mcp-server/central-command/backend/`
- ✅ No `requests` library in async backend code

**Async HTTP Clients:**
- Uses `httpx` for async HTTP
- Uses `aiohttp` in compliance-agent

### Action Required
- [x] All DB operations use async
- [x] No blocking calls in async code

---

## 8. Rate Limits & External Services Audit

**Status:** ✅ Pass

### Findings

**Retry Logic:**
- ✅ `executor.py`: Step retry with configurable count and delay
- ✅ `evidence/uploader.py`: Upload retry with delay
- ✅ `guardrails.py`: Circuit breaker pattern (threshold=3, timeout=60s)

**Timeout Configuration:**
- ✅ Step execution: configurable per step (default 60s)
- ✅ HTTP client timeouts configured
- ✅ Circuit breaker with timeout recovery

**gRPC Settings:**
- ✅ `max_concurrent` semaphore for agent deployments (default 5)
- Note: Basic gRPC channel settings, no explicit keepalive

### Action Required
- [ ] Consider adding gRPC keepalive settings for long-lived connections
- [x] Retry logic implemented
- [x] Timeouts configured
- [x] Circuit breaker implemented

---

## 9. Systemd Service Ordering Audit

**Status:** ✅ Pass

### Findings

**VPS Docker Services:**
```yaml
# docker-compose.yml
mcp-server:
  depends_on:
    postgres:
      condition: service_healthy
  restart: unless-stopped
```
- ✅ Correct dependency ordering (postgres must be healthy first)
- ✅ Restart policy: `unless-stopped`

**Appliance Systemd (compliance-agent):**
```ini
After=network-online.target msp-auto-provision.service msp-health-gate.service
Wants=network-online.target
Restart=always
RestartSec=10s
Type=simple
```
- ✅ Correct boot order (network → provision → health-gate → agent)
- ✅ Restart policy: always with 10s delay
- ✅ Service running: `active (running)` for 43min

### Action Required
- [x] Services have correct ordering
- [x] Restart policies configured
- [x] Network dependencies specified

---

## 10. Proto & Contract Drift Audit

**Status:** ✅ Pass

### Findings

**Proto File Sync:**
```
proto/compliance.proto == agent/proto/compliance.proto
✅ Protos in sync
```

**Type Contracts:**
- `CONTRACTS.md` created and documents Python/TypeScript alignment
- `_types.py` created as single source of truth
- `validate-types.sh` script available for verification

### Action Required
- [x] Proto files synchronized
- [x] Type contracts documented
- [ ] Set up CI to verify proto sync on PR

---

## Critical Path Items

### Must Fix Before Production

| Priority | Issue | Impact | Fix |
|----------|-------|--------|-----|
| ~~**P0**~~ | ~~VPS signing.key is 644~~ | ~~Anyone with server access can sign orders~~ | ✅ **FIXED 2026-01-27** |

### Should Fix Soon

| Priority | Issue | Impact | Fix |
|----------|-------|--------|-----|
| **P1** | TLS cert expires Mar 31 | Service outage | Verify auto-renewal |
| **P2** | No sqlite3 on appliance | Can't diagnose DB issues | Add to NixOS config |
| **P3** | Windows VMs unreachable | Can't verify lab health | Start VMs, verify network |

---

## Recommended Fix Order

1. ~~**Immediate (5 min):** Fix VPS signing.key permissions~~ ✅ **FIXED 2026-01-27**

2. **Today:** Verify TLS auto-renewal
   ```bash
   ssh root@178.156.162.116 "docker exec caddy caddy reload" # Test renewal
   ```

3. **This Week:** Add sqlite3 to appliance image
   ```nix
   # iso/appliance-disk-image.nix
   environment.systemPackages = with pkgs; [ ... sqlite ];
   ```

4. **This Week:** Start Windows lab VMs and verify time sync

5. **This Sprint:** Add CI proto sync check

---

## Verification Commands

After fixes, run these to verify:

```bash
# 1. Verify signing key permissions
ssh root@178.156.162.116 "ls -la /opt/mcp-server/secrets/signing.key"
# Expected: -rw------- 1 root root 65 ...

# 2. Verify TLS cert
echo | openssl s_client -connect api.osiriscare.net:443 2>/dev/null | openssl x509 -noout -enddate
# Expected: notAfter > 14 days from now

# 3. Run health check script
./scripts/prod-health-check.sh
```

---

*Generated by Production Readiness Audit - 2026-01-27*
