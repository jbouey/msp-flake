# Session 193 — Auto-Rekey, 12-Point Hardening, Agent Error Classification

**Date:** 2026-04-03
**Commits:** 5
**Daemon Version:** 0.3.76
**Lines Changed:** ~450 added across 12 files

## Incident & Diagnosis

Appliance reported offline. Root cause chain:
1. Switch reset → appliance rebooted → DHCP assigned .235 (was .241)
2. ARP scan found it via MAC `7C:D3:0A:7C:55:18`
3. Real issue: API key mismatch → 401 on every checkin for 2.5h
4. Manual rekey restored checkins immediately

## Commit 1: Auto-Rekey Feature
- `POST /api/provision/rekey` — unauthenticated, MAC+site_id+hardware_id identity
- Daemon: `ErrAuthFailed` → `attemptRekey()` after 3 consecutive 401s
- `UpdateAPIKey()` atomically writes new key to config.yaml
- Dashboard: `auth_failed` orange badge (vs generic "Offline")
- Migration 118: auth_failure_since/count/last on site_appliances

## Commit 2: CSRF + Audit Fix
- `/api/provision/` prefix CSRF exempt
- audit_log column names (event_type not action)

## Commit 3: 12-Point Hardening
**P0:** SudoPassword propagation (unblocks ALL Linux healing), root cause in offline alerts
**P1:** Sudo failure logging, AD DNS fallback resolver, subnet-dark detection (≥80% unreachable → single incident), healing netscan IP fallback
**P2:** SSH credential auto-update in device_sync, NixOS msp-dns-hosts service, credential IP update endpoint, mass-unreachable circuit breaker

## Commit 4: Workstation Agent Error Classification
- `ClassifyConnectionError()` — 10 categories (dns_not_found, appliance_down, network_down, timeout, tls_error, auth_rejected, etc.)
- Consecutive failure counter + cert auto-re-enrollment after 5 auth rejections
- `ForceReEnrollment()` deletes stale certs for TOFU re-enrollment

## Commit 5: CI/CD Deploy Key Fix
- New ed25519 keypair for GitHub Actions → VPS SSH
- Deploys green after 2 failures with stale key

## Network Fixes (Manual)
- Linux VM .236→.233: promiscuous mode Allow All, credential IP updated
- NVDC01 DNS: /etc/hosts entry + ad_dns_server config
- NV appliance API key restored, v0.3.76 fleet order deployed
- iMac back on correct WiFi, VMs running

## Open Items
- iMac SSH port 2222: LaunchDaemon "Operation not permitted" — needs `sudo launchctl load -w` from iMac Terminal
- MikroTik DHCP reservations: needs admin credentials
- NVDC01 agent config still points to .241 — will auto-fix on next autodeploy cycle
- Linux healing: SudoPassword fix deployed, awaiting cooldown expiry for verification
- Chaos lab scripts: `chaos_workstation_cadence.py` is stale local copy — real scripts run ON iMac, need IP update (.246→.235) once iMac SSH is back
- Chaos lab cron on iMac likely targeting wrong appliance IP
