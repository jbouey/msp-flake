# Session 146 - Companion Compliance Alerts + ws01 WinRM Auth

**Date:** 2026-03-01
**Started:** 15:29
**Previous Session:** 145

---

## Goals

- [x] Build companion portal alert system for HIPAA module deadline tracking
- [x] Deploy alerts feature (commit, push, apply migration)
- [x] Fix alert button not working in production
- [x] Fix ws01 WinRM auth (NTLM not offered, only Negotiate/Kerberos)
- [x] Add companion document upload/download endpoints
- [x] Deploy updated daemon with Basic auth WinRM to physical appliance

---

## Progress

### Completed

**Companion Compliance Alerts Feature (SHIPPED)**

Full-stack feature: companions set deadline alerts on HIPAA modules per client.

- Migration `066_companion_alerts.sql` — status lifecycle (active → triggered → resolved/dismissed)
- 5 CRUD endpoints in companion.py + `_evaluate_module_status()` + background check loop (6h)
- Email notifications via `send_companion_alert_email()` (teal-branded, 24h dedup)
- Background task registered in main.py lifespan()
- 5 React Query hooks in useCompanionApi.ts
- Alert indicators on module cards (CompanionClientDetail.tsx)
- Set Alert form in CompanionModuleWork.tsx
- Overdue badge on client list (CompanionClientList.tsx)
- Committed `06db58e`, pushed, CI/CD deployed
- Migration manually applied on VPS (was the root cause of alert button not working)

**Companion Document Upload/Download (SHIPPED)**

Tasha reported IR Plan section attachments not loading in companion portal. Root cause: companion backend had no document endpoints — only the client portal did.

- 4 new endpoints in `companion.py` (list, upload, download, delete) with `require_companion` auth
- MinIO storage integration with presigned URLs (15-min expiry)
- Soft-delete for regulatory compliance
- Fixed `detail` vs `details` kwarg bug in `_log_activity`
- Fixed `user` vs `user["id"]` parameter bug
- Committed `49b10c0`, pushed, CI/CD deployed

**ws01 WinRM Auth (FIXED)**

Root cause was two-fold:
1. ws01 only advertised Negotiate+Kerberos (no NTLM/Basic) due to GPO policy overrides
2. Go `masterzen/winrm` library's `ClientNTLM{}` sends raw NTLM, not accepted by ws01

Fix approach:
- VBoxManage console login to ws01 (individual scan codes with 1s pauses for reliable input)
- Bumped DC + ws01 VMs to 6GB RAM each for reliable console interaction
- Ran `Enable-PSRemoting -Force -SkipNetworkProfileCheck`
- Set GPO policy-level registry keys (local `winrm set` commands were being overridden by GPO):
  - `HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service\Auth\AllowBasic=1`
  - `HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service\AllowUnencryptedTraffic=1`
- Restarted WinRM — Basic auth now advertised in `WWW-Authenticate` header
- Removed `TransportDecorator: ClientNTLM{}` from 3 Go files (executor.go, winrm-exec, ad-unlock)
- Library defaults to Basic auth which both DC and workstations now accept
- Built `appliance-daemon-basic` (21MB Linux binary), deployed to physical appliance via iMac
- Verified: `winrm-exec` from appliance → ws01 returns hostname, whoami, WinRM service status
- Committed `5571b36`, pushed

---

## Files Changed

| File | Change |
|------|--------|
| `backend/migrations/066_companion_alerts.sql` | NEW — table + indexes |
| `backend/companion.py` | +374 lines (5 alert endpoints, evaluator, bg loop) + 4 document endpoints (~130 lines) |
| `backend/email_alerts.py` | +139 lines (companion alert email template) |
| `main.py` | +9 lines (background task registration) |
| `frontend/src/companion/useCompanionApi.ts` | +57 lines (5 hooks) |
| `frontend/src/companion/CompanionClientDetail.tsx` | +88 lines (alert indicators, alerts section) |
| `frontend/src/companion/CompanionModuleWork.tsx` | +161 lines (alert form) |
| `frontend/src/companion/CompanionClientList.tsx` | +25 lines (overdue badge) |
| `appliance/internal/winrm/executor.go` | Removed `ClientNTLM{}` TransportDecorator (use Basic auth) |
| `appliance/cmd/winrm-exec/main.go` | Removed `ClientNTLM{}` TransportDecorator |
| `appliance/cmd/ad-unlock/main.go` | Removed `ClientNTLM{}` TransportDecorator |

## Commits

| Hash | Description |
|------|-------------|
| `06db58e` | feat: companion compliance alerts (full-stack) |
| `49b10c0` | feat: companion document upload/download endpoints |
| `5571b36` | fix: WinRM Basic auth — remove NTLM transport decorator |

---

## Next Session

1. Verify WinRM Basic auth persists across ws01 reboots (GPO policy registry should stick)
2. Verify companion alerts work end-to-end in production
3. Verify Tasha can upload IR Plan DOCX templates through companion portal
