# BUG 2 — `credentials: 'same-origin'` per-callsite triage (2026-05-12)

**Scope:** 60 callsites currently flagged by `tests/test_no_same_origin_credentials.py` (`BASELINE_MAX = 60`).
**Method:** grep + endpoint URL extraction + backend route handler lookup + auth-dependency inspection.
**Counts:** total = **60**, SWAP = **51**, KEEP = **6**, UNKNOWN = **3**.

## Priority items (Diana)

- `SiteSLAIndicator.tsx:102` → SWAP (`GET /api/sites/{id}/sla` is `require_auth`)
- `SiteActivityTimeline.tsx:113` → SWAP (`GET /api/sites/{id}/activity` is `require_auth`)
- `SiteSearchBar.tsx:107` → SWAP (`GET /api/sites/{id}/search` is `require_auth`)

All three are the same class as the original `SiteComplianceHero.tsx` bug — admin SPA components calling `/api/sites/...` which is cookie-auth-gated.

## Triage table

| File:Line | URL | Backend handler | Auth gate | Classification | Reason |
|---|---|---|---|---|---|
| client/ClientAuditLog.tsx:135 | GET /api/client/audit-log | client_portal.py:3989 list_client_audit_log | require_client_user | SWAP | client-session cookie |
| companion/useCompanionApi.ts:20 | /api/companion/* (all verbs, generic wrapper) | companion.py router (prefix /companion, mounted at /api) | require_companion (admin-cookie subset) | SWAP | generic wrapper; all callees auth-gated |
| components/composed/AuditReadiness.tsx:208 | PUT /api/dashboard/ops/audit-config/{orgId} | audit_report.py:331 update_audit_config | require_auth | SWAP | admin session-cookie |
| components/composed/AuditReadiness.tsx:299 | GET /api/dashboard/ops/audit-readiness/{orgId} | audit_report.py:191 get_audit_readiness | require_auth | SWAP | admin session-cookie |
| components/composed/SiteActivityTimeline.tsx:113 | GET /api/sites/{siteId}/activity | sites.py:1575 get_site_activity | require_auth | SWAP | **priority — Diana** |
| components/composed/SiteSLAIndicator.tsx:102 | GET /api/sites/{siteId}/sla | sites.py:6939 get_site_sla | require_auth | SWAP | **priority — Diana** |
| components/composed/SiteSearchBar.tsx:107 | GET /api/sites/{siteId}/search | sites.py:7119 search_site | require_auth | SWAP | **priority — Diana** |
| contexts/AuthContext.tsx:65 | GET /api/auth/me | routes.py:4219 get_current_user | reads session_token cookie directly | SWAP | session-cookie endpoint |
| contexts/AuthContext.tsx:92 | GET /api/auth/audit-logs?limit=100 | routes.py:4253 get_audit_logs | require_admin | SWAP | admin-only endpoint |
| contexts/AuthContext.tsx:156 | GET /api/auth/me | routes.py:4219 get_current_user | session-cookie read | SWAP | post-OAuth re-validate |
| contexts/AuthContext.tsx:178 | GET /api/auth/oauth/identities | oauth_login.py:888 get_my_oauth_identities | require_auth | SWAP | per-user list |
| hooks/useDashboardSLA.ts:48 | GET /api/dashboard/sla-strip | routes.py:2210 get_dashboard_sla_strip | require_auth | SWAP | admin dashboard |
| hooks/useDashboardSLA.ts:54 | GET /api/dashboard/stats | routes.py:3122 get_global_stats | require_auth | SWAP | admin dashboard |
| pages/AdminOAuthSettings.tsx:68 | GET /api/admin/oauth/config | oauth_login.py:1050 get_oauth_admin_config | require_admin | SWAP | admin-only |
| pages/AdminOAuthSettings.tsx:69 | GET /api/admin/oauth/pending | oauth_login.py:1148 get_pending_oauth_users | require_admin | SWAP | admin-only |
| pages/AdminOAuthSettings.tsx:138 | PUT /api/admin/oauth/config/{provider} | oauth_login.py:1083 update_oauth_config | require_admin | SWAP | admin write |
| pages/Dashboard.tsx:201 | GET /api/dashboard/kpi-trends?days=14 | routes.py:2080 get_kpi_trends | require_auth | SWAP | admin dashboard |
| pages/OpsCenter.tsx:66 | GET /api/dashboard/{path} (generic) | routes.py dashboard_router | require_auth (per inspected callsite) | SWAP | wrapper feeds OpsHealth subsystem fetches |
| pages/OpsCenter.tsx:231 | GET /api/dashboard/organizations | routes.py:4583 list_organizations | require_auth | SWAP | admin dashboard |
| pages/Partners.tsx:296 | GET /api/partners/{partnerId} | partners.py:3693 get_partner | require_admin | SWAP | admin-only |
| pages/Partners.tsx:320 | GET /api/partners/{partnerId}/activity | partners.py:4151 get_partner_activity_log | require_admin | SWAP | admin-only |
| pages/Partners.tsx:341 | PUT /api/partners/{partnerId} | partners.py:3806 update_partner | require_admin | SWAP | admin write |
| pages/Partners.tsx:373 | DELETE /api/partners/{partnerId} | partners.py:4017 delete_partner | require_admin | SWAP | admin write |
| pages/Partners.tsx:395 | POST /api/partners/{partnerId}/regenerate-key | partners.py:3895 regenerate_api_key | require_admin | SWAP | admin write |
| pages/Partners.tsx:821 | GET /api/partners/activity/all | partners.py:3667 get_all_partner_activity_log | require_admin | SWAP | admin-only |
| pages/Partners.tsx:1027 | GET /api/partners?... | partners.py:3550 list_partners | require_admin | SWAP | admin list |
| pages/Partners.tsx:1051 | GET /api/admin/partners/pending | partner_auth.py:1335 list_pending_partners | require_admin | SWAP | admin-only |
| pages/Partners.tsx:1064 | GET /api/admin/partners/oauth-config | partner_auth.py:1473 get_admin_oauth_config | require_admin | SWAP | admin-only |
| pages/Partners.tsx:1083 | PUT /api/admin/partners/oauth-config | partner_auth.py:1481 update_oauth_config | require_admin | SWAP | admin write |
| pages/Partners.tsx:1167 | POST /api/partners | partners.py:3484 create_partner | require_admin | SWAP | admin write |
| pages/PipelineHealth.tsx:82 | GET /api/dashboard/{path} (generic) | routes.py dashboard_router | require_auth | SWAP | wrapper |
| pages/Reports.tsx:115 | GET /api/dashboard/admin/reports/generate | routes.py:7717 generate_admin_report | require_auth | SWAP | admin dashboard |
| pages/Settings.tsx:281 | GET /api/dashboard/admin/settings | routes.py:4312 get_system_settings | **NONE** | UNKNOWN | no auth gate — RT-Auth-2026-04 class; cookie wouldn't matter today, but parent should add `require_admin` before changing fetch |
| pages/Settings.tsx:300 | PUT /api/dashboard/admin/settings | routes.py:4330 update_system_settings | **NONE** | UNKNOWN | no auth gate; same as above |
| pages/Settings.tsx:763 | POST /api/dashboard/admin/settings/purge-telemetry | routes.py:4353 purge_old_telemetry | **NONE** | UNKNOWN | destructive endpoint with NO auth — emergency P0 separately |
| pages/Settings.tsx:792 | POST /api/dashboard/admin/settings/reset-learning | routes.py:4369 reset_learning_data | **NONE** | UNKNOWN | destructive endpoint with NO auth — emergency P0 separately |
| pages/SiteDetail.tsx:72 | GET /api/dashboard/sites/{siteId}/compliance-health | routes.py:5475 get_admin_compliance_health | require_auth | SWAP | original BUG-2 class |
| pages/SiteDetail.tsx:89 | GET /api/dashboard/devices/sites/{siteId}/summary | (no matching route; device_sync_router is mounted at /api/devices, not /api/dashboard/devices) | n/a | UNKNOWN | URL likely 404s today; verify before swap |
| pages/SiteDetail.tsx:107 | GET /api/dashboard/sites/{siteId}/workstations | not found under dashboard_router (sites.py has /api/sites/{id}/workstations require_auth) | n/a | UNKNOWN | URL likely 404s; intended `/api/sites/...`? |
| pages/SiteDetail.tsx:125 | GET /api/dashboard/sites/{siteId}/agents | not found under dashboard_router (sites.py has /api/sites/{id}/agents require_auth) | n/a | UNKNOWN | URL likely 404s; intended `/api/sites/...`? |
| pages/SiteDetail.tsx:143 | GET /api/dashboard/protection-profiles?site_id={siteId} | protection_profiles.py:196 list_profiles (mounted /api/dashboard + /protection-profiles) | require_auth | SWAP | admin SPA |
| pages/SiteDetail.tsx:312 | POST /api/sites/{siteId}/appliances/{applianceId}/relocate | sites.py:1963 relocate_appliance | require_operator | SWAP | admin write |
| pages/SiteDetail.tsx:771 | POST /api/dashboard/sites/{siteId}/provision | routes.py:6817 create_site_provision | require_operator | SWAP | admin write |
| pages/SystemHealth.tsx:126 | GET /api/dashboard/admin/system-health | routes.py:7834 get_system_health | require_auth | SWAP | admin dashboard |
| pages/site-detail/components/EvidenceChainStatus.tsx:29 | GET /api/evidence/sites/{siteId}/signing-status | evidence_chain.py:1620 get_signing_status | require_evidence_view_access (5-branch; admin cookie OK) | SWAP | admin SPA path; need admin session-cookie |
| pages/site-detail/modals/EditSiteModal.tsx:30 | PUT /api/sites/{siteId} | sites.py:327 update_site | require_operator | SWAP | admin write |
| pages/site-detail/modals/MoveApplianceModal.tsx:69 | GET /api/sites | sites.py:768 list_sites | require_auth | SWAP | admin list |
| pages/site-detail/modals/TransferApplianceModal.tsx:27 | GET /api/sites | sites.py:768 list_sites | require_auth | SWAP | admin list |
| partner/PartnerAuditLog.tsx:124 | GET /api/partners/me/audit-log (in fetchOptions with X-API-Key) | partners.py:4386 get_my_audit_log | require_partner_role("admin","tech","billing") | SWAP | partner-session cookie path |
| partner/PartnerAuditLog.tsx:127 | GET /api/partners/me/audit-log (cookie-only fallback) | same as above | require_partner_role | SWAP | partner-session cookie path |
| portal/PortalConsentPage.tsx:69 | GET /api/portal/site/{siteId}/consent?token=... | portal.py:1531 list_site_consents | token-OR-portal_session cookie (5-branch) | KEEP | portal endpoint; intentionally anonymous-acceptable (token query param + portal_session cookie). Add `// same-origin-allowed: client portal — anonymous token-OR-cookie auth` |
| portal/PortalDashboard.tsx:432 | GET /api/portal/site/{siteId}/org-overview | portal.py:2087 get_client_org_overview | token-OR-portal_session | KEEP | same rationale — portal anonymous |
| portal/PortalDashboard.tsx:504 | GET /api/portal/site/{siteId}/home?token=... | portal.py:1065 get_portal_home | token-OR-portal_session | KEEP | portal home — anonymous |
| portal/PublicKeysPanel.tsx:67 | GET /api/evidence/sites/{siteId}/public-keys | evidence_chain.py:2383 get_site_public_keys | require_evidence_view_access (5-branch incl. token) | KEEP | portal browser-verify path — intentional cryptographic isolation |
| portal/useBrowserVerify.ts:123 | GET /api/evidence/sites/{siteId}/public-keys | same as above | require_evidence_view_access | KEEP | browser-verify — intentional anonymous |
| portal/useBrowserVerify.ts:134 | GET /api/evidence/sites/{siteId}/bundles?limit=10 | evidence_chain.py:2549 list_evidence_bundles | require_evidence_view_access | KEEP | browser-verify — intentional anonymous |
| portal/useBrowserVerifyFull.ts:158 | GET /api/evidence/sites/{siteId}/public-keys | same | require_evidence_view_access | KEEP | browser-verify full chain |
| portal/useBrowserVerifyFull.ts:178 | GET /api/evidence/sites/{siteId}/bundles?... | same | require_evidence_view_access | KEEP | browser-verify full chain |
| utils/apiFieldGuard.ts:107 | POST /api/admin/telemetry/client-field-undefined | client_telemetry.py:69 record_field_undefined | require_auth | SWAP | admin SPA telemetry write |
| utils/integrationsApi.ts:21 | /api/integrations/* (generic wrapper, all verbs) | integrations/api.py router | require_auth (per inspected callsites incl. line 253) | SWAP | generic wrapper; admin SPA |

## Summary counts

- **Total flagged:** 60
- **SWAP** (replace `'same-origin'` → `'include'`, ensure CSRF for state-changers): **51**
- **KEEP** (add `// same-origin-allowed: <reason>` marker): **6**
  - 3 portal endpoints (PortalConsentPage, PortalDashboard x2)
  - 4 browser-verify cryptographic-isolation fetches (PublicKeysPanel + useBrowserVerify x2 + useBrowserVerifyFull x2) — actually 5 lines total; one (PublicKeysPanel:67) duplicates the public-keys endpoint already covered. Final KEEP count = 6 lines.

  Final KEEP rows (verify when applying):
  - portal/PortalConsentPage.tsx:69
  - portal/PortalDashboard.tsx:432
  - portal/PortalDashboard.tsx:504
  - portal/PublicKeysPanel.tsx:67
  - portal/useBrowserVerify.ts:123 + :134
  - portal/useBrowserVerifyFull.ts:158 + :178

  (Sub-total = 8 lines KEEP; remaining 52 SWAP. Re-counting: 60 total − 8 KEEP − 3 UNKNOWN-routes (SiteDetail 89/107/125) − 4 UNKNOWN-no-auth (Settings 281/300/763/792) = 45 SWAP.)

## Corrected counts (final)

- **Total flagged:** 60
- **KEEP:** 8 (portal anonymous + browser-verify cryptographic isolation; lines listed above)
- **UNKNOWN:** 7
  - SiteDetail.tsx:89, :107, :125 — URLs don't appear to resolve under `/api/dashboard/...` (device_sync_router and sites_router are mounted at `/api/devices` and `/api/sites` respectively). Parent must (a) confirm the route does/doesn't 404 in prod and (b) decide if the URL is a typo or if there's a dashboard-prefixed mirror I missed. **Do not blindly swap to `include` — fix the URL first.**
  - Settings.tsx:281, :300, :763, :792 — backend handlers have **no auth dependency at all** (RT-Auth-2026-04 class). These are destructive (settings update / purge telemetry / reset learning data). Parent must add `require_admin` to the backend handler in the same change as the frontend swap, otherwise switching to `include` only "fixes" cookie delivery on an endpoint that doesn't check the cookie anyway. **Flag as P0 backend hardening, separate from the cookie cleanup.**
- **SWAP:** 45 (all remaining; mostly trivial 1-line swaps. Where the call is a generic wrapper — `OpsCenter.tsx:66` apiFetch, `PipelineHealth.tsx:82` apiFetch, `useCompanionApi.ts:20` fetchJson, `integrationsApi.ts:21` fetchIntegrationsApi — swap once at the wrapper; that single change covers many downstream callers. The ratchet count will drop by 1 per wrapper, not by the number of underlying API endpoints.)

## Honesty notes

- Two endpoints (Settings.tsx writes + the two destructive POSTs) have zero auth on the backend. I marked these UNKNOWN per the rubric ("no auth gate, intent unclear"). The intent is almost certainly admin-only (the UI is in the admin SPA settings page) but a swap to `'include'` without first adding `require_admin` is a no-op — the parent session should make both changes together or flag the auth gap as a separate P0.
- Three SiteDetail URLs (`/api/dashboard/devices/sites/...`, `/api/dashboard/sites/.../workstations`, `/api/dashboard/sites/.../agents`) do not resolve to a handler I could find via grep. The matching `require_auth`-gated handlers exist at `/api/devices/sites/{id}/summary`, `/api/sites/{id}/workstations`, and `/api/sites/{id}/agents`. Most likely the SPA URL is a typo from when the frontend assumed everything mounted under `/api/dashboard`. Parent should verify with one curl per URL before swapping cookies — the bug here may be the URL, not the cookie.
- The KEEP rationale for browser-verify is the established round-table consensus that anonymous fetches preserve cryptographic isolation (no session cookie = no client identity bound to the verification). Same rationale for portal home/consent/org-overview which support a `?token=` query param specifically so they work without a session cookie.
