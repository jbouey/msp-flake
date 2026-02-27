# Session 142 - Security Hardening — Fleet Orders Auth, Signatures, Nonce Replay, WinRM SSL

**Date:** 2026-02-27
**Started:** 06:48
**Previous Session:** 141
**Commit:** a68fe2d

---

## Goals

- [x] Fix 4 CRITICAL + 3 HIGH vulnerabilities from fleet orders audit

---

## Progress

### Completed

1. **Appliance checkin auth (CRITICAL)** — `require_appliance_auth()` validates Bearer token via `verify_site_api_key()`. Graceful fallback for sites without API keys. Applied to checkin, acknowledge, complete.
2. **Signature delivery (CRITICAL)** — admin_orders, healing orders, fleet_orders now include nonce/signature/signed_payload in SELECT and response.
3. **server_public_key (CRITICAL)** — Checkin response returns `get_public_key_hex()` for Go daemon signature verification.
4. **Nonce replay protection (HIGH)** — Go Processor tracks used nonces in-memory + JSON persistence, 24h eviction.
5. **Hostname validation (HIGH)** — `isKnownTarget()` validates healing hostnames against DC, deployed workstations, linux targets.
6. **WinRM SSL (HIGH)** — All 10 WinRM sites switched to port 5986 + UseSSL:true + VerifySSL:false.
7. **Fleet order signatures (CRITICAL)** — `get_fleet_orders_for_appliance()` includes signature columns.

### Blocked

- WinRM HTTPS listener must be configured on Windows targets before SSL connections work

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/sites.py` | Auth, signatures, server_public_key |
| `mcp-server/central-command/backend/fleet_updates.py` | Fleet order signatures |
| `appliance/internal/orders/processor.go` | Nonce replay tracking |
| `appliance/internal/daemon/healing_executor.go` | Hostname validation, WinRM SSL |
| `appliance/internal/daemon/daemon.go` | WinRM SSL (3 locations) |
| `appliance/internal/daemon/driftscan.go` | WinRM SSL (3 locations) |
| `appliance/internal/daemon/autodeploy.go` | WinRM SSL (3 locations) |

---

## Next Session

1. Push to main, verify CI/CD deploys backend
2. Configure WinRM HTTPS on Windows DC + workstations
3. Deploy Go daemon v0.3.7 via fleet order
4. Verify appliance checkin works with auth enabled
5. Monitor for signature verification failures in daemon logs
