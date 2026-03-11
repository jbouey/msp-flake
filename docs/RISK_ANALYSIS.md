# OsirisCare Risk Analysis

**Regulation:** HIPAA Security Rule, 45 CFR 164.308(a)(1)(ii)(A)
**Document Type:** Risk Analysis (Required Implementation Specification)
**Prepared by:** OsirisCare Engineering
**Date:** 2026-03-11
**Next Review:** 2027-03-11
**Revision:** 1.0

---

## Scope

This risk analysis covers the OsirisCare compliance attestation platform -- a managed service that performs drift detection, evidence capture, and operator-authorized remediation for healthcare SMB clients. The platform is **ePHI-adjacent**: it does not store, process, or transmit electronic Protected Health Information (ePHI) directly. It manages the compliance posture of systems that do handle ePHI. A compromise of this platform could degrade the security controls protecting ePHI at client sites.

The production stack runs on a Hetzner VPS (178.156.162.116) behind Caddy with automatic TLS. Services are containerized via Docker Compose: FastAPI backend (mcp-server), PostgreSQL 16 via PgBouncer, Redis 7, MinIO WORM storage, Caddy reverse proxy, Nginx frontend, and a Go checkin-receiver. NixOS appliances deployed at client sites communicate via authenticated HTTPS checkin.

---

## Methodology

Threats are identified per-asset using NIST SP 800-30 guidance. Likelihood and impact are rated qualitatively. Controls are verified against source code, session logs, and production configuration. Each control is marked:

- **IMPLEMENTED** -- code deployed and verified in production
- **PARTIAL** -- code exists but coverage is incomplete
- **PLANNED** -- design exists, implementation not yet deployed

---

## Asset 1: Central Command API (FastAPI Backend)

**Description:** The FastAPI application (`mcp-server` container, port 8000) serves the admin dashboard, partner portal, client portal, companion portal, appliance checkin, fleet order delivery, incident pipeline (L1/L2/L3/L4), and evidence chain endpoints. It is the sole write path to PostgreSQL and the coordination point for all healing orders.

**ePHI Adjacent:** Yes. Compromise grants ability to disable compliance monitoring, issue unauthorized healing orders to client infrastructure, or exfiltrate site credential material.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 1.1 | Authentication bypass -- attacker gains admin session | Medium | Critical |
| 1.2 | Privilege escalation -- operator/readonly user accesses admin functions | Low | High |
| 1.3 | IDOR -- cross-tenant data access via manipulated site_id | Medium | Critical |
| 1.4 | Injection -- SQL injection via unsanitized query parameters | Low | Critical |
| 1.5 | Denial of service -- resource exhaustion via unbounded queries | Medium | Medium |
| 1.6 | Unauthorized healing order issuance | Medium | Critical |
| 1.7 | Session fixation or replay | Low | High |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| bcrypt 12-round password hashing, no plaintext storage | IMPLEMENTED | `auth.py` lines 106-108, mandatory bcrypt import |
| Password complexity: 12+ chars, upper/lower/digit/special, breached password check, sequential/repeat char rejection | IMPLEMENTED | `auth.py` lines 42-103 |
| HMAC-SHA256 session token hashing with server-side secret (not raw token in DB) | IMPLEMENTED | `auth.py` lines 136-149, Session 157 |
| Session idle timeout 15 minutes (HIPAA 164.312(a)(2)(iii)) | IMPLEMENTED | `auth.py` lines 28, 448-479 |
| Absolute session expiry 24 hours | IMPLEMENTED | `auth.py` line 27 |
| Account lockout: 5 failed attempts triggers 15-minute lock | IMPLEMENTED | `auth.py` lines 29-30, 246-261 |
| TOTP 2FA (pyotp) for admin, partner, and client portals with backup codes | IMPLEMENTED | `auth.py` lines 267-299, `totp.py`, Session 157 |
| RBAC with 4 roles: admin, operator, readonly, companion | IMPLEMENTED | `auth.py` lines 738-806, `require_auth`/`require_admin`/`require_operator`/`require_companion` dependencies |
| Org-scoped admin access (admin_org_assignments limits visibility) | IMPLEMENTED | `auth.py` lines 488-501, `apply_org_filter` |
| IDOR prevention via `require_site_access()` -- returns 404 not 403 for out-of-scope sites | IMPLEMENTED | `auth.py` lines 524-557, Session 169 |
| CSRF protection with token validation, narrowed exemptions | IMPLEMENTED | `csrf.py`, Session 157 |
| Rate limiting (Redis-backed, configurable requests/window) | IMPLEMENTED | `redis_rate_limiter.py`, `rate_limiter.py` |
| Query parameter bounds: `ge=1` on all `limit` params | IMPLEMENTED | `routes.py`, Session 160 |
| Open redirect prevention in OAuth callback | IMPLEMENTED | `OAuthCallback.tsx`, Session 160 |
| Uniform error messages on auth failure (no username enumeration) | IMPLEMENTED | `auth.py` line 229 returns same message for invalid user and invalid password |
| Audit logging for all auth events (login, logout, MFA, lockout) | IMPLEMENTED | `auth.py` `_log_audit()` on every auth path |
| Ed25519 signing of fleet orders and healing orders | IMPLEMENTED | `fleet_cli.py`, `fleet_updates.py`, Session 142 |
| RLS tenant isolation on partner portal endpoints (10 endpoints wired) | PARTIAL | `tenant_middleware.py`, Session 168. Client portal and dashboard endpoints not yet wired. `app.is_admin` default still `'true'` |
| Prompt injection sanitization in L2 planner | IMPLEMENTED | `l2_planner.py` `_sanitize_field()` regex + untrusted data notice, Session 168 |

### Residual Risk: **Medium**

RLS is enforced on 27 tables but `app.is_admin` default has not been flipped to `'false'`, meaning the RLS safety net is not yet active for all code paths. Client portal and dashboard endpoints still lack `tenant_connection()` wiring.

**Owner:** OsirisCare Engineering

**Notes:** The `require_site_access()` IDOR guard was added in Session 169 to protection profiles. The same pattern should be extended to all remaining site-scoped admin routers (routes.py, frameworks.py).

---

## Asset 2: PostgreSQL Database

**Description:** PostgreSQL 16 (Alpine) running in `mcp-postgres` container. Stores site configurations, appliance metadata, incidents, healing orders, evidence bundle metadata, L1 rules, L2 decisions, escalation tickets, partner/client user accounts, audit logs, and credential material. Accessed via PgBouncer (port 6432) in transaction pooling mode.

**ePHI Adjacent:** Yes. Contains site credentials (encrypted), appliance identities, and compliance posture data. Credential compromise combined with appliance access could reach ePHI-handling systems.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 2.1 | Unauthorized direct database access | Low | Critical |
| 2.2 | Cross-tenant data leakage via shared queries | Medium | Critical |
| 2.3 | Credential material exposure from `site_credentials` table | Medium | Critical |
| 2.4 | Audit log tampering to hide breach evidence | Medium | High |
| 2.5 | Connection exhaustion DoS | Medium | Medium |
| 2.6 | Backup loss or corruption | Medium | High |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| PostgreSQL bound to 127.0.0.1 only (no external exposure) | IMPLEMENTED | `docker-compose.yml` ports: `127.0.0.1:5432:5432` |
| PgBouncer transaction pooling (v1.25.1, SCRAM-SHA-256 auth) | IMPLEMENTED | `pgbouncer.ini`, Session 168 |
| Separate `mcp_app` role (NOSUPERUSER, NOBYPASSRLS) for application queries | IMPLEMENTED | Migration 079, Session 167 |
| `mcp` superuser reserved for migrations only | IMPLEMENTED | Session 167 |
| Row-Level Security on 27 tables with FORCE enabled | IMPLEMENTED | Migrations 078-080, Sessions 167-168 |
| GUC-based tenant isolation: `app.current_tenant`, `app.is_admin`, `app.current_org` | IMPLEMENTED | `tenant_middleware.py`, `SET LOCAL` per transaction |
| Append-only triggers on 4 audit tables (prevent UPDATE/DELETE) | IMPLEMENTED | Migration 084, Session 169 |
| Auto-populate triggers for `site_id` on incidents, l2_decisions, orders, evidence_bundles, discovered_devices, device_compliance_details | IMPLEMENTED | Migrations 078-080 |
| Credential encryption: admin portal uses JSON in `encrypted_data`, partner portal uses Fernet in `password_encrypted` | IMPLEMENTED | `sites.py`, Session 160 |
| `statement_cache_size=0` on all asyncpg/SQLAlchemy connections (PgBouncer compatibility) | IMPLEMENTED | `fleet.py`, `main.py`, `server.py`, Session 168 |
| Parameterized queries throughout (SQLAlchemy `text()` with `:param` binding) | IMPLEMENTED | Codebase-wide pattern |
| Backup monitoring (status file mounted read-only) | PARTIAL | `/opt/backups/status` volume mount. Automated restore testing not documented |
| Encryption at rest for database volume | PLANNED | Docker volume on unencrypted Hetzner disk |

### Residual Risk: **Medium**

`app.is_admin` default remains `'true'` at the DB level, so any code path that fails to call `tenant_connection()` will bypass RLS. Credentials stored in the database are encrypted but the encryption keys reside on the same VPS. No automated backup restore testing is documented. Database volume is not encrypted at rest.

**Owner:** OsirisCare Engineering

**Notes:** Phase 4 P2 remaining work (from Session 168): flip `app.is_admin` default to `'false'`, wire `tenant_connection()` into all remaining endpoints, add Redis key scoping.

---

## Asset 3: MinIO WORM Evidence Storage

**Description:** MinIO S3-compatible object storage (`mcp-minio` container, ports 9000/9001 on localhost). Backs to Hetzner Storage Box at `/mnt/storagebox`. Stores evidence bundles with Object Lock in COMPLIANCE retention mode (default 90 days). Used for tamper-evident compliance evidence that auditors consume.

**ePHI Adjacent:** Yes. Evidence bundles contain compliance check results, remediation logs, and system state snapshots from client infrastructure. No raw ePHI but contains metadata about ePHI-handling system configurations.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 3.1 | Evidence tampering to falsify compliance posture | Low | Critical |
| 3.2 | Unauthorized deletion of evidence during retention period | Low | Critical |
| 3.3 | Console access to modify or delete objects | Medium | High |
| 3.4 | Storage Box failure or data loss | Low | High |
| 3.5 | Credential exposure of MinIO root credentials | Medium | High |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| COMPLIANCE-mode Object Lock with configurable retention (default 90 days) | IMPLEMENTED | `evidence_chain.py` lines 1095-1149, `Retention(COMPLIANCE, retention_until)` |
| Background WORM upload on evidence bundle creation | IMPLEMENTED | `evidence_chain.py` lines 983-987, `upload_to_worm_background` |
| Ed25519 digital signatures on evidence bundles | IMPLEMENTED | `evidence_chain.py`, signing key at `/app/secrets/signing.key` |
| HMAC-SHA256 timing-safe hash comparison for evidence chain verification | IMPLEMENTED | `evidence_chain.py`, `hmac.compare_digest()`, Session 160 |
| OpenTimestamps Bitcoin anchoring for evidence timestamps | IMPLEMENTED | `evidence_chain.py` lines 47-50, 4 calendar servers |
| MinIO bound to 127.0.0.1 only | IMPLEMENTED | `docker-compose.yml` ports: `127.0.0.1:9000:9000` |
| MinIO credentials via environment variables | IMPLEMENTED | `docker-compose.yml` env vars with defaults |
| Separate storage backend (Hetzner Storage Box at /mnt/storagebox) | IMPLEMENTED | `docker-compose.yml` MinIO volume mount |
| WORM toggle via `WORM_ENABLED` environment variable | IMPLEMENTED | `evidence_chain.py` line 984 |

### Residual Risk: **Low**

The COMPLIANCE-mode Object Lock prevents deletion during retention. OpenTimestamps anchoring provides independent timestamp verification. The main residual risk is that MinIO root credentials and signing keys are stored on the same VPS, so a full VPS compromise could allow creation of falsified evidence (though not modification of already-anchored evidence).

**Owner:** OsirisCare Engineering

**Notes:** The WORM retention period (90 days) should be reviewed against HIPAA's 6-year retention requirement for security documentation. Consider extending retention or implementing archival tier storage for long-term evidence.

---

## Asset 4: Redis Cache

**Description:** Redis 7 (Alpine) with AOF persistence (`mcp-redis` container). Used for rate limiting, caching, and session-adjacent data. Password-protected via `REDIS_PASSWORD` environment variable.

**ePHI Adjacent:** No direct ePHI. Contains rate limit counters, cached query results, and transient operational data.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 4.1 | Rate limit bypass via Redis manipulation | Low | Medium |
| 4.2 | Cache poisoning leading to stale security decisions | Low | Medium |
| 4.3 | Unauthorized access to cached data | Low | Low |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| Password authentication required (`requirepass`) | IMPLEMENTED | `docker-compose.yml` Redis command |
| AOF persistence enabled (`appendonly yes`) | IMPLEMENTED | `docker-compose.yml` Redis command |
| No external port exposure (Docker network only) | IMPLEMENTED | No ports section in `docker-compose.yml` for Redis |
| Health check monitoring | IMPLEMENTED | `docker-compose.yml` Redis healthcheck |
| Rate limiting implementation (Redis-backed) | IMPLEMENTED | `redis_rate_limiter.py` |
| Redis key tenant scoping | PLANNED | Session 168 identified as remaining Phase 4 P2 work |

### Residual Risk: **Low**

Redis is not externally accessible and contains no ePHI. The main gap is that cache keys are not yet tenant-scoped, which could theoretically allow cross-tenant cache reads within the application layer (not externally exploitable).

**Owner:** OsirisCare Engineering

**Notes:** Redis key scoping (tenant-prefix keys) is planned as part of Phase 4 P2 (Session 168).

---

## Asset 5: Appliance Fleet (NixOS Endpoints)

**Description:** NixOS appliances deployed at client sites (physical hardware or VMs). Each appliance runs a Go daemon that performs compliance checks against Windows infrastructure via WinRM, reports drift to Central Command via HTTPS checkin every 60 seconds, executes healing orders, and manages credential material locally. Appliances are provisioned via MAC lookup or QR code claiming.

**ePHI Adjacent:** Yes. Appliances have direct network access to client infrastructure that handles ePHI. They hold Windows domain credentials and can execute commands on workstations and servers.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 5.1 | Physical theft of appliance exposing stored credentials | Medium | Critical |
| 5.2 | Appliance compromise via unauthorized SSH access | Low | Critical |
| 5.3 | Malicious healing order execution on client infrastructure | Low | Critical |
| 5.4 | NixOS configuration drift from known-good state | Low | Medium |
| 5.5 | Appliance impersonation (rogue device registering) | Low | High |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| NixOS deterministic configuration (flake-based, cryptographic store hashes) | IMPLEMENTED | `flake.nix`, `iso/appliance-disk-image.nix` |
| SSH key-only authentication (no password auth) | IMPLEMENTED | ISO configuration |
| nftables pull-only firewall (outbound HTTPS, inbound SSH only) | IMPLEMENTED | `iso/appliance-disk-image.nix` |
| systemd hardening: `ProtectSystem=strict`, sandboxed daemon | IMPLEMENTED | NixOS module, Session 150 |
| Ed25519 signature verification on fleet orders and healing orders | IMPLEMENTED | `orders/processor.go`, Session 142 |
| Unsigned order rejection when server public key is present | IMPLEMENTED | `orders/processor.go` lines 301-306, Session 141 |
| Nonce replay protection with 24h eviction | IMPLEMENTED | `orders/processor.go`, Session 142 |
| Hostname validation for healing targets (`isKnownTarget()`) | IMPLEMENTED | `healing_executor.go`, Session 142 |
| WinRM SSL (port 5986, UseSSL:true) | IMPLEMENTED | `daemon.go`, `driftscan.go`, `autodeploy.go`, Session 142 |
| SSH TOFU (Trust On First Use) host key verification | IMPLEMENTED | `sshexec/executor.go`, Session 141 |
| Panic recovery wrapper (`safeGo()`) for all goroutines | IMPLEMENTED | `daemon.go`, Session 160 |
| SSH connection LRU cache (max 50, prevents resource exhaustion) | IMPLEMENTED | `sshexec/executor.go`, Session 141 |
| WaitGroup drain with 30s timeout on shutdown | IMPLEMENTED | `daemon.go`, Session 141 |
| Agent registry persistence (survives daemon restart) | IMPLEMENTED | `registry.go`, Session 160 |
| SOPS/age encrypted secrets in NixOS configuration | IMPLEMENTED | `flake.nix`, sopsFile + ageKeyFile pattern |
| MAC-based provisioning with Central Command lookup | IMPLEMENTED | `provisioning.py` |
| Site ID entropy: 48-bit token_hex(6) | IMPLEMENTED | `provisioning.py`, Session 160 |
| Credential freshness check (re-deliver if updated since last checkin) | IMPLEMENTED | `sites.py`, Session 160 |
| Per-workstation credential lookup (least-privilege WinRM access) | IMPLEMENTED | `daemon.go` `LookupWinTarget()`, Session 152 |
| API key authentication on checkin endpoint | IMPLEMENTED | `require_appliance_auth()`, Session 142 |
| Disk encryption at rest | PLANNED | NixOS supports LUKS but not yet enabled in appliance image |

### Residual Risk: **Medium**

Physical appliance theft is the primary residual risk. Credential material is stored on disk without full-disk encryption. While WinRM uses SSL, certificate verification is disabled (`VerifySSL:false`). Per-workstation certificate revocation (CRL/OCSP) for mTLS is not yet implemented.

**Owner:** OsirisCare Engineering

**Notes:** LUKS full-disk encryption for appliances would significantly reduce the physical theft risk. WinRM certificate verification requires deploying a PKI to client sites, which is a future enhancement.

---

## Asset 6: Go Checkin Daemon

**Description:** Standalone Go binary (`checkin-receiver` container, port 8001) that handles the high-frequency appliance checkin endpoint. Directly connects to PostgreSQL via PgBouncer. Authenticates appliances via API key. Runs as an Alpine container with the binary mounted read-only.

**ePHI Adjacent:** Yes. Processes appliance telemetry including drift scan results, incident reports, and credential delivery responses.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 6.1 | Checkin spoofing (unauthorized appliance sending fake telemetry) | Medium | High |
| 6.2 | Resource exhaustion via high-frequency checkin flood | Medium | Medium |
| 6.3 | Information disclosure in checkin response | Low | Medium |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| API key bearer token authentication (`--auth-token` flag) | IMPLEMENTED | `docker-compose.yml` checkin-receiver entrypoint |
| Binary mounted read-only | IMPLEMENTED | `docker-compose.yml` volume mount `:ro` |
| Health check endpoint | IMPLEMENTED | `docker-compose.yml` healthcheck |
| Database access via PgBouncer (connection pooling, not direct) | IMPLEMENTED | `docker-compose.yml` DB connection string |
| Savepoints for optional checkin steps (prevents transaction poisoning) | IMPLEMENTED | `sites.py`, Session 161 |

### Residual Risk: **Low**

The checkin receiver has a narrow attack surface (single authenticated endpoint). The main risk is that a compromised appliance API key would allow telemetry injection, which could corrupt compliance scoring.

**Owner:** OsirisCare Engineering

---

## Asset 7: Anthropic Claude API (L2 Planner)

**Description:** The L2 tier of the three-tier healing pipeline uses the Anthropic Claude API (via `ANTHROPIC_API_KEY`) to analyze incidents that L1 deterministic rules cannot resolve. The planner selects runbooks, evaluates confidence, and recommends escalation or auto-remediation. Calls are made from the `mcp-server` container.

**ePHI Adjacent:** No direct ePHI. Incident data sent to the API contains system-level telemetry (service names, error codes, hostnames) but no patient data. PHI scrubbing (12 regex patterns) is applied before evidence capture.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 7.1 | Prompt injection via crafted incident data | Medium | High |
| 7.2 | API key exposure | Low | Medium |
| 7.3 | LLM recommending destructive remediation | Low | High |
| 7.4 | API unavailability degrading incident response | Medium | Medium |
| 7.5 | Data leakage of client system details to third-party API | Low | Medium |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| Input sanitization: `_sanitize_field()` regex scrubbing on all incident fields before prompt construction | IMPLEMENTED | `l2_planner.py` lines 48-67, Session 168 |
| Untrusted data notice in system prompt | IMPLEMENTED | `l2_planner.py`, Session 168 |
| Confidence threshold: remediation only if confidence >= 0.6 | IMPLEMENTED | `l2_planner.py`, `main.py` ~line 1449 |
| Graceful L3 fallback when L2 is unavailable or uncertain | IMPLEMENTED | Incident pipeline: L2 failure escalates to L3 human review |
| L2 fallback on failed L1 order execution (tries L2 before L3 escalation) | IMPLEMENTED | `sites.py`, Session 163 |
| Runbook-constrained execution: L2 selects from known runbooks, does not generate arbitrary commands | IMPLEMENTED | `l2_planner.py` loads runbooks from DB, `main.py` `runbook_action_map`, Session 167 |
| API key via environment variable (not hardcoded) | IMPLEMENTED | `docker-compose.yml` `ANTHROPIC_API_KEY` |
| PHI scrubbing (12 regex patterns) on evidence data | IMPLEMENTED | Evidence chain module |
| Dynamic runbook loading with 5-minute TTL cache | IMPLEMENTED | `l2_planner.py` |
| Notification dedup: 4h for L1/L2, 24h for L3 | IMPLEMENTED | Incident pipeline |

### Residual Risk: **Low**

The L2 planner is constrained to selecting from known runbooks and cannot generate arbitrary commands. Prompt injection mitigations are in place. The primary residual risk is that sophisticated prompt injection could influence runbook selection toward an inappropriate but valid runbook.

**Owner:** OsirisCare Engineering

**Notes:** Consider implementing output validation that cross-references the LLM's selected runbook against the incident type to detect anomalous selections.

---

## Asset 8: Hetzner VPS Infrastructure

**Description:** Single Hetzner VPS (178.156.162.116) running all Central Command services via Docker Compose. The server hosts the Caddy reverse proxy, all backend services, and the database. Root SSH access via key only.

**ePHI Adjacent:** Yes. The VPS is the single point of hosting for the entire platform including encrypted credentials, compliance data, and fleet management capabilities.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 8.1 | VPS compromise via SSH brute force or vulnerability | Low | Critical |
| 8.2 | Hetzner infrastructure failure (single point of failure) | Low | Critical |
| 8.3 | Insider threat at hosting provider | Low | High |
| 8.4 | DDoS against VPS IP | Medium | Medium |
| 8.5 | Unauthorized access to Hetzner account | Low | Critical |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| SSH key-only authentication to VPS | IMPLEMENTED | Server configuration |
| All internal services bound to 127.0.0.1 or Docker network only | IMPLEMENTED | `docker-compose.yml` -- PostgreSQL, Redis, MinIO all on localhost |
| Caddy as sole external-facing service (ports 80/443) | IMPLEMENTED | `docker-compose.yml` |
| Automated CI/CD deployment via GitHub Actions (no manual SCP) | IMPLEMENTED | `.github/workflows/deploy-central-command.yml` |
| Container restart policies (`unless-stopped`) | IMPLEMENTED | All services in `docker-compose.yml` |
| Health checks on all critical services | IMPLEMENTED | PostgreSQL, Redis, MinIO, mcp-server, checkin-receiver all have healthchecks |
| Docker network isolation (`mcp-network` bridge) | IMPLEMENTED | `docker-compose.yml` networks section |
| Hetzner Storage Box for MinIO data (external to VPS compute) | IMPLEMENTED | `/mnt/storagebox` mount |
| Secrets mounted read-only | IMPLEMENTED | `docker-compose.yml` `/app/secrets:ro` |
| Backup status monitoring | PARTIAL | `/opt/backups/status` mounted. Full backup/restore SOP not documented |
| Multi-region redundancy | PLANNED | Single VPS, no failover |
| Disk encryption | PLANNED | Hetzner VPS disk not encrypted |

### Residual Risk: **High**

Single point of failure with no geographic redundancy. Full VPS compromise exposes all services, secrets, and database. No disk encryption at the VPS level. Backup restore procedures are not documented or tested.

**Owner:** OsirisCare Engineering

**Notes:** This is the highest residual risk in the platform. Mitigations to prioritize: (1) documented and tested backup/restore procedures, (2) Hetzner disk encryption or volume encryption, (3) geographic failover planning for business continuity.

---

## Asset 9: TLS/Certificate Management (Caddy)

**Description:** Caddy 2 (Alpine) serves as the reverse proxy for all external traffic. Handles automatic TLS certificate provisioning via Let's Encrypt, HTTPS termination, HTTP/3 (QUIC), security headers, and CSP enforcement. Domains: `api.osiriscare.net`, `dashboard.osiriscare.net`, `msp.osiriscare.net`.

**ePHI Adjacent:** Yes. All data in transit between appliances, browsers, and Central Command passes through Caddy.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 9.1 | TLS certificate expiry causing service outage | Low | High |
| 9.2 | TLS downgrade attack | Low | High |
| 9.3 | Missing or weak security headers allowing XSS/clickjacking | Low | Medium |
| 9.4 | Certificate transparency log exposure of infrastructure | Low | Low |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| Automatic TLS via Let's Encrypt (Caddy built-in ACME) | IMPLEMENTED | `Caddyfile`, Caddy auto-TLS |
| HTTP/3 (QUIC) support | IMPLEMENTED | `docker-compose.yml` port `443:443/udp` |
| HSTS with preload (`max-age=31536000; includeSubDomains; preload`) | IMPLEMENTED | `Caddyfile` `(security_headers)` snippet |
| `X-Content-Type-Options: nosniff` | IMPLEMENTED | `Caddyfile` |
| `X-Frame-Options: DENY` | IMPLEMENTED | `Caddyfile` |
| `Referrer-Policy: strict-origin-when-cross-origin` | IMPLEMENTED | `Caddyfile` |
| `Permissions-Policy` restricting geolocation, microphone, camera | IMPLEMENTED | `Caddyfile` |
| Content-Security-Policy for dashboard (restricts script/style/connect sources) | IMPLEMENTED | `Caddyfile` `(dashboard_csp)` snippet |
| Server header suppression (`-Server`) | IMPLEMENTED | `Caddyfile` `header_down -Server` on all proxied responses |
| Certificate data persistence across restarts | IMPLEMENTED | `caddy_data` and `caddy_config` Docker volumes |

### Residual Risk: **Low**

Caddy's automatic certificate management eliminates the most common TLS risk (expiry). Security headers are comprehensive. The CSP is reasonably strict but includes `'unsafe-inline'` for scripts and styles, which weakens XSS protection.

**Owner:** OsirisCare Engineering

**Notes:** Consider removing `'unsafe-inline'` from CSP by migrating to nonce-based script loading. Monitor HSTS preload list submission status.

---

## Asset 10: OpenTimestamps Bitcoin Anchoring

**Description:** Evidence bundles are timestamped via the OpenTimestamps protocol, which creates cryptographic commitments anchored to the Bitcoin blockchain. The system submits hashes to 4 OTS calendar servers and stores proof files. Proofs can be independently verified without trusting OsirisCare infrastructure.

**ePHI Adjacent:** No. Only cryptographic hashes (SHA-256) of evidence bundles are sent to the Bitcoin network. No ePHI or metadata is transmitted.

### Threats

| # | Threat | Likelihood | Impact |
|---|--------|-----------|--------|
| 10.1 | OTS calendar server unavailability | Medium | Low |
| 10.2 | Proof upgrade failure (pending attestations never confirmed) | Medium | Medium |
| 10.3 | Bitcoin blockchain reorganization invalidating proofs | Very Low | Low |
| 10.4 | Timestamp proof storage loss | Low | Medium |

### Current Controls

| Control | Status | Reference |
|---------|--------|-----------|
| 4 redundant OTS calendar servers (a.pool, b.pool, alice.btc, bob.btc) | IMPLEMENTED | `evidence_chain.py` lines 47-50 |
| OTS proof file validation with magic byte verification | IMPLEMENTED | `evidence_chain.py` `OTS_MAGIC` constant |
| Proof storage alongside evidence bundles in database | IMPLEMENTED | `evidence_chain.py` |
| Graceful degradation: evidence bundles are valid without OTS (OTS adds independent verification) | IMPLEMENTED | OTS is additive, not required for evidence validity |
| Calendar retention check for proof upgrades | IMPLEMENTED | `evidence_chain.py` line 566 |

### Residual Risk: **Low**

OTS is a defense-in-depth measure. Its unavailability does not compromise evidence validity (Ed25519 signatures remain the primary integrity mechanism). The main risk is that pending attestations may never upgrade to Bitcoin block header attestations if not periodically checked.

**Owner:** OsirisCare Engineering

**Notes:** Implement a periodic job to attempt upgrade of pending OTS attestations. Consider storing proof files in MinIO WORM alongside the evidence bundles they attest.

---

## Summary Risk Matrix

| Asset | Residual Risk | Primary Gap |
|-------|--------------|-------------|
| Central Command API | Medium | RLS `app.is_admin` default not flipped; `require_site_access` not applied to all routers |
| PostgreSQL Database | Medium | Encryption at rest; `app.is_admin` default; backup restore testing |
| MinIO WORM Storage | Low | Retention period vs HIPAA 6-year requirement |
| Redis Cache | Low | Tenant key scoping |
| Appliance Fleet | Medium | No full-disk encryption; WinRM cert verification disabled |
| Go Checkin Daemon | Low | Narrow attack surface, well-contained |
| Anthropic Claude API | Low | Prompt injection is mitigated; runbook-constrained output |
| Hetzner VPS | **High** | Single point of failure, no disk encryption, no tested DR |
| TLS/Caddy | Low | CSP `unsafe-inline` |
| OpenTimestamps | Low | Pending attestation upgrade automation |

---

## Remediation Priorities

### P0 -- Address Within 30 Days

1. **VPS disaster recovery**: Document and test backup/restore procedure. Verify ability to restore full service from backups within defined RTO.
2. **Flip `app.is_admin` default to `'false'`**: Complete Phase 4 P2 (Sessions 167-168) to enforce RLS by default across all code paths.
3. **Wire `tenant_connection()` into all remaining endpoints**: Client portal, dashboard, companion, and compliance_frameworks endpoints.

### P1 -- Address Within 90 Days

4. **VPS disk encryption**: Enable encryption at rest for the Hetzner VPS root volume and data volumes.
5. **Appliance LUKS encryption**: Enable full-disk encryption on NixOS appliances to protect credentials at rest.
6. **Extend `require_site_access()` pattern**: Apply IDOR guards to all site-scoped admin routes beyond protection profiles.
7. **WORM retention review**: Evaluate 90-day default against HIPAA 6-year documentation retention requirement; implement archival tier.
8. **Redis tenant key scoping**: Prefix all cache keys with tenant identifier.

### P2 -- Address Within 180 Days

9. **Geographic redundancy**: Evaluate multi-region or warm-standby deployment for business continuity.
10. **CSP hardening**: Remove `'unsafe-inline'` from Content-Security-Policy via nonce-based script loading.
11. **WinRM certificate verification**: Deploy PKI to client sites to enable `VerifySSL:true`.
12. **OTS attestation upgrade automation**: Periodic job to upgrade pending attestations.
13. **Per-workstation mTLS certificate revocation**: Implement CRL or OCSP for gRPC agent certificates.

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-03-11 | OsirisCare Engineering | Initial risk analysis |

**Review Schedule:** Annual (next: 2027-03-11), or upon significant architecture change, security incident, or new service deployment.

**Related Documents:**
- `docs/HIPAA_FRAMEWORK.md` -- HIPAA control mapping
- `docs/PROVENANCE.md` -- Evidence chain and signing architecture
- `docs/ARCHITECTURE.md` -- System architecture overview
- `docs/security/security.md` -- Security patterns and implementation details
