# MSP Compliance Platform

HIPAA compliance attestation substrate for healthcare SMBs. NixOS + MCP + LLM.
Drift detection, evidence-grade observability, operator-authorized remediation. 75% lower cost than traditional MSPs.

**Target:** 1-50 provider practices in NEPA region | **Pricing:** $200-3000/mo

> **Positioning:** This is an evidence-grade compliance attestation substrate. It provides observability, drift detection, evidence capture, and human-authorized remediation workflows. It is not a coercive enforcement platform. Remediation occurs only via operator-configured rules or human-escalated decisions.

## Appliance Deployment

**DO NOT use `dd` disk images.** Use the Golden Flake architecture:
1. Build installer ISO: `nix build .#appliance-iso`
2. Write to USB, boot target hardware - installation is automatic
3. Appliance calls home to Central Command, MAC lookup provisions identity

Key files: `iso/appliance-image.nix`, `iso/configuration.nix`, `flake.nix`

## Directory Structure

```
packages/compliance-agent/   # Python agent (DEPRECATED — Go daemon is active)
  tests/                     # pytest tests (1161+ passing)
  venv/                      # Python 3.13 virtualenv
appliance/                   # Go appliance daemon (ACTIVE agent)
  internal/daemon/           # Core daemon, StateManager, interfaces, threat_detector
  internal/phiscrub/         # PHI scrubbing package (14 patterns, 21 tests)
  internal/orders/           # Fleet order processor (22 handlers)
  internal/evidence/         # Evidence bundle signing + submission
  internal/grpcserver/       # Agent registry, TLS enrollment
  Makefile                   # Build with VERSION injection via ldflags
agent/                       # Go workstation agent (Windows/macOS/Linux)
modules/                     # NixOS modules
mcp-server/central-command/  # Dashboard backend + frontend
  backend/                   # FastAPI backend (routes.py, sites.py, main.py)
    constants/               # Design system (copy.ts, status.ts)
    components/composed/     # MetricCard, StatusBadge, PageShell, etc.
    migrations/              # 098 migrations (latest: security_events)
  frontend/                  # React + TypeScript + Tailwind
docs/                        # Reference docs + compliance attestations
.agent/reference/            # Credentials, network, decisions
```

## Key Commands

```bash
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short        # Run tests
python -m pytest tests/test_agent.py -v      # Single file
nix flake check --no-build                   # Validate NixOS configs
```

## Three-Tier Auto-Healing

```
Incident → L1 Deterministic (70-80%, <100ms, $0)
        → L2 LLM Planner (15-20%, 2-5s, ~$0.001)
        → L3 Human Escalation (5-10%)
        → Data Flywheel (promotes L2→L1)
```

## Type System

```python
from compliance_agent._types import (
    Incident, EvidenceBundle, ComplianceCheck,
    CheckStatus, Severity, CheckType,
    now_utc  # Use instead of datetime.utcnow()
)
```

## Reference Docs (read on demand, not eagerly)

| Area | Doc |
|------|-----|
| Credentials | `.agent/reference/LAB_CREDENTIALS.md` |
| Network | `.agent/reference/NETWORK.md` |
| Decisions | `.agent/reference/DECISIONS.md` |
| Architecture | `docs/ARCHITECTURE.md` |
| HIPAA | `docs/HIPAA_FRAMEWORK.md` |
| Runbooks | `docs/RUNBOOKS.md` |
| Provenance | `docs/PROVENANCE.md` |

## Knowledge Index

**Prefer retrieval-led reasoning:** When working in any area below, READ the linked doc before relying on training data. These docs contain project-specific patterns that override general knowledge.

```
AREA | PATTERN | DOC | CRITICAL SNIPPET
-----|---------|-----|------------------
auth | bcrypt 12-round | docs/security/security.md | secrets.token_urlsafe(32)
auth | PKCE flow | docs/security/security.md | hashlib.sha256(verifier).digest()
auth | session cookie | docs/api/api.md | httponly=True,secure=True,samesite="lax"
auth | rate limit | docs/security/security.md | 5 fail → 15min lockout
secrets | SOPS/age | docs/nixos/infrastructure.md | sopsFile + ageKeyFile
-----|---------|-----|------------------
test | async decorator | docs/testing/testing.md | @pytest.mark.asyncio
test | fixture | docs/testing/testing.md | @pytest.fixture + tmp_path
test | mock cmd | docs/testing/testing.md | AsyncMock(stdout="...")
-----|---------|-----|------------------
hipaa | 6 drift checks | docs/hipaa/compliance.md | patching,backup,firewall,logging,av,encryption
hipaa | evidence bundle | docs/hipaa/compliance.md | EvidenceBundle dataclass
hipaa | L1 rule | docs/hipaa/compliance.md | conditions→action→runbook_id
hipaa | PHI scrub | docs/hipaa/compliance.md | 12 patterns, hash_redacted=True
-----|---------|-----|------------------
backend | 3-tier healing | docs/backend/backend.md | L1(70%)→L2(20%)→L3(10%)
backend | FastAPI router | docs/backend/backend.md | APIRouter(prefix="/api/x")
backend | Depends auth | docs/backend/backend.md | user: Dict = Depends(require_auth)
backend | asyncpg pool | docs/backend/backend.md | create_pool(min=2,max=10)
backend | gRPC stream | docs/backend/backend.md | ReportDrift(stream DriftEvent)
-----|---------|-----|------------------
db | postgres pool | docs/database/database.md | asyncpg.create_pool()
db | sqlite WAL | docs/database/database.md | PRAGMA journal_mode=WAL
db | multi-tenant | docs/database/database.md | WHERE site_id = $1
db | upsert | docs/database/database.md | ON CONFLICT DO UPDATE
-----|---------|-----|------------------
nixos | module pattern | docs/nixos/infrastructure.md | options + config = mkIf
nixos | systemd harden | docs/nixos/infrastructure.md | ProtectSystem=strict
nixos | nftables | docs/nixos/infrastructure.md | pull-only firewall
nixos | ISO build | docs/nixos/infrastructure.md | nix build .#appliance-iso
nixos | module priority | docs/nixos/advanced.md | mkForce=50, mkDefault=1000
nixos | sops-nix | docs/nixos/advanced.md | sops.secrets + templates
nixos | impermanence | docs/nixos/advanced.md | tmpfs root + persist
nixos | deploy-rs | docs/nixos/advanced.md | magic rollback on disconnect
nixos | specialisation | docs/nixos/advanced.md | A/B boot configs
-----|---------|-----|------------------
golang | errgroup | docs/golang/golang.md | g.SetLimit(10) bounded
golang | sync.Pool | docs/golang/golang.md | bufPool.Get/Put
golang | slog | docs/golang/golang.md | slog.New(JSONHandler)
golang | pgxpool | docs/golang/golang.md | MaxConns,MinConns,HealthCheck
golang | table tests | docs/golang/golang.md | t.Run(tt.name, func...)
golang | fuzzing | docs/golang/golang.md | FuzzXxx + go test -fuzz
golang | sd_notify | docs/golang/golang.md | READY=1, WATCHDOG=1
golang | func options | docs/golang/golang.md | WithPort(8080)
golang | circuit breaker | docs/golang/golang.md | gobreaker.Settings
-----|---------|-----|------------------
windows | 25 drift checks | ~/.claude/skills/windows-server-compliance/SKILL.md | driftscan.go checkTarget
windows | WinRM execution | ~/.claude/skills/windows-server-compliance/SKILL.md | 5985 HTTP, 5986 HTTPS, NTLM/Kerberos
windows | GPO WinRM enable | ~/.claude/skills/windows-server-compliance/SKILL.md | ensureWinRMViaGPO + startup script
windows | 22 event IDs | ~/.claude/skills/windows-server-compliance/SKILL.md | 4625 brute force, 1102 audit clear
windows | threat detect | ~/.claude/skills/windows-server-compliance/SKILL.md | cross-host brute force, VSS deletion
windows | 50+ runbooks | ~/.claude/skills/windows-server-compliance/SKILL.md | RB-WIN-*, MAC-*, LIN-*
windows | agent deploy | ~/.claude/skills/windows-server-compliance/SKILL.md | 5-tier fallback, NETLOGON, base64 chunk
-----|---------|-----|------------------
frontend | React Query | docs/frontend/frontend.md | useQuery({queryKey,queryFn})
frontend | mutation | docs/frontend/frontend.md | useMutation + invalidateQueries
frontend | api.ts | docs/frontend/frontend.md | fetchApi<T> with Bearer
frontend | glass card | docs/frontend/frontend.md | bg-white/5 backdrop-blur
frontend | routing | docs/frontend/frontend.md | /client/* /partner/* /portal/*
-----|---------|-----|------------------
api | REST base | docs/api/api.md | /api prefix, Bearer auth
api | gRPC proto | docs/api/api.md | ComplianceAgent service
api | OAuth flow | docs/api/api.md | auth_url→callback→tokens
-----|---------|-----|------------------
perf | asyncio.gather | docs/performance/performance.md | 6x faster checks
perf | React.memo | docs/performance/performance.md | 3-5x fewer renders
perf | virtual scroll | docs/performance/performance.md | @tanstack/react-virtual
-----|---------|-----|------------------
workflow | 4-phase debug | docs/workflow/workflow.md | root cause → pattern → hypothesis → fix
workflow | verify gate | docs/workflow/workflow.md | IDENTIFY→RUN→READ→VERIFY→CLAIM
```

All doc paths relative to `.claude/skills/`. Read the full doc when working in that area.

## Quick Lab Reference

| System | IP | User |
|--------|-----|------|
| iMac Host | 192.168.88.50 | jrelly (SSH key) |
| Physical Appliance | 192.168.88.241 | root (SSH key) |
| VM Appliance | DEPRECATED — decommissioned Session 183 |  |
| VPS | 178.156.162.116 | root (SSH key) |
| VPS WireGuard | 10.100.0.1 | Hub, UDP 51820 |
| Appliance WireGuard | 10.100.0.2 | `ssh root@10.100.0.2` from VPS |

Full credentials in `.agent/reference/LAB_CREDENTIALS.md` when needed.

## Session Tracking

```bash
python3 .agent/scripts/context-manager.py status       # View state
python3 .agent/scripts/context-manager.py new-session N description
python3 .agent/scripts/context-manager.py end-session
python3 .agent/scripts/context-manager.py compact      # Archive old sessions
```

Primary state: `.agent/claude-progress.json`

## Rules

- **Debugging: root cause first.** No fixes without investigation. Trace data flow backward. One hypothesis at a time. 3+ failed fixes = question architecture. (Full process: `.claude/skills/docs/workflow/workflow.md`)
- **Verify before claiming done.** Run the actual command, read the output, show evidence. No "should pass" or "probably works."
- Use `now_utc()` not `datetime.utcnow()`
- Run tests before AND after changes
- Log session work to `.agent/sessions/YYYY-MM-DD-description.md`
- **DEPLOY VIA GIT PUSH, NOT SCP.** CI/CD (`.github/workflows/deploy-central-command.yml`) auto-deploys backend + frontend to VPS on push to main. Manual scp causes stale versions.
- **SQLAlchemy AsyncSession:** Never use `asyncio.gather()` on the same session - causes `InvalidStateError`. Run queries sequentially.
- **execution_telemetry.runbook_id:** Agent uses internal IDs (L1-SVC-DNS-001) that differ from backend IDs (RB-AUTO-SERVICE_). Match by `incident_type` + `hostname/site_id`, not `runbook_id`.
- **Synced L1 rules override built-in rules.** Rules at `/var/lib/msp/rules/l1_rules.json` (synced hourly from Central Command) take precedence over built-in rules in `level1_deterministic.py`. Changes to built-in rules must also be applied to server-side rules in `mcp-server/main.py` and the `l1_rules` DB table.
- **server.py DELETED (Session 185).** Container runs `uvicorn main:app`. server.py was dead code.
- **All main.py endpoints require auth (Session 185).** `require_appliance_bearer` for daemon endpoints, `require_auth` for admin endpoints. Only `/` and `/health` are public.
- **PHI scrubbing at appliance egress (Session 185).** `phiscrub` package scrubs all outbound data before it leaves the appliance. Central Command is PHI-free.
- **Design system: constants/copy.ts is THE source of truth** for all user-facing text. Never hardcode status labels, tooltips, disclaimers, or branding in component files.
- **Score thresholds: 90/70/50.** Defined in `constants/status.ts` via `getScoreStatus()`. Never create local score-to-color functions.
- **MONITORING_ONLY_CHECKS must be synced** between `main.py` AND `agent_api.py`. Only genuinely un-automatable checks belong there. Both files have their own copy.
- **agent_api.py router is NOT registered in main.py** (too many overlapping endpoints). Individual endpoints like `/api/agent/l2/plan` are wired manually via `app.post()(handler)`.
- **L1 rule `incident_pattern` must have `incident_type` key.** The L1 query matches on `incident_pattern->>'incident_type'`. Synced/promoted rules with only `check_type` are dead weight.
- **CI/CD deploys code but does NOT restart the container.** After git push, manually restart: `cd /opt/mcp-server && docker compose up -d --build mcp-server`.
- **iMac SSH on port 2222** (Session 191). Port 22 blocked by MikroTik HW offload (Ethernet↔WiFi). Use `ssh -p 2222 jrelly@192.168.88.50`. LaunchDaemon plist at `/Library/LaunchDaemons/com.local.sshd2222.plist`.
- **Credential encryption key required.** Fernet key at `/app/secrets/credential_encryption.key`. Missing key = RuntimeError on startup. Key also in `.env` as `CREDENTIAL_ENCRYPTION_KEY`.
- **`dashboard_api_mount` overrides `./app/dashboard_api`** in docker-compose. Copy backend files to BOTH paths when deploying manually.
- **Go build ldflags**: Version is `internal/daemon.Version`, NOT `main.Version`. Use `make build-linux VERSION=x` or `-X github.com/osiriscare/appliance/internal/daemon.Version=x`.
- **Fleet orders: complete old before creating new.** Active orders block delivery of newer orders. `UPDATE fleet_orders SET status='completed' WHERE status='active'` before creating.
- **Backend-authoritative mesh (Session 196, hardened 199).** Target assignment computed SERVER-SIDE in checkin (STEP 3.8c in sites.py). `hash_ring.py` uses round-robin when targets < 2x nodes (guarantees distribution), hash ring otherwise. Go daemon uses server assignments via `ApplyTargetAssignment()`, local ring is 15-min fallback only. 20 tests in `test_target_assignment.py`.
- **Terminology: "drift" is dead (Session 199).** User-facing text says "failing"/"compliance issue"/"configuration change". Variable names may still say "drift" — that's fine, only display text matters. `constants/status.ts` has `drifted.label: 'Failing'`. `cleanAttentionTitle()` in `constants/status.ts` maps backend titles.
- **Legal language (Session 199).** Never use "ensures", "prevents", "protects", "guarantees", "audit-ready", "PHI never leaves", "100%". Use "monitors", "helps detect", "reduces exposure", "audit-supportive", "PHI scrubbed at appliance". Disclaimers that LIMIT liability are safe — don't change them.
- **auth.py: always use execute_with_retry() (Session 199).** Raw `db.execute()` causes DuplicatePreparedStatementError through PgBouncer. `validate_session()` runs on EVERY authenticated request — must use `execute_with_retry()`.
- **Site credentials may need migration.** When sites are renamed/recreated, `site_credentials` rows stay on the old `site_id`. Check `site_credentials` if a site shows 0 targets despite having appliances.
- **Per-appliance signing keys (Session 196).** `site_appliances.agent_public_key` — NOT `sites.agent_public_key`. Evidence verification checks per-appliance keys. Multi-appliance sites MUST NOT use the single site-level key.
- **Appliance display_name (Session 196).** `site_appliances.display_name` auto-generated on checkin: first appliance = hostname, subsequent = `{hostname}-{N}`. Frontend shows `display_name || hostname || appliance_id`.
- **`compliance_bundles` is the evidence table, NOT `evidence_bundles`.** evidence_bundles is a legacy table (1 row). compliance_bundles has 232K+ entries, Ed25519 signed, hash-chained, OTS-anchored. **PARTITIONED by month** (Migration 138, Session 200). Default partition catches overflow. **ON CONFLICT (bundle_id) is INCOMPATIBLE with partitioned tables** — use DELETE+INSERT upsert pattern (Session 201).
- **`compliance_packets` table (Migration 141, Session 201).** Monthly compliance attestation packets persisted for HIPAA 6-year retention. Auto-generated on 1st of month at 02:00 UTC.
- **`mark_proof_anchored()` is the ONLY way to anchor OTS proofs (Session 201).** Single helper in evidence_chain.py handles ots_proofs, ots_merkle_batches, and admin_audit_log. Never write raw UPDATE ots_proofs SET status='anchored'.
- **Fleet orders: GET pending orders requires auth (Session 201).** `require_appliance_bearer` on `/{site_id}/appliances/{appliance_id}/orders/pending`. Was unauthenticated before Session 201.
- **Go daemon: dangerous orders blocked before server key received (Session 201).** `update_daemon`, `nixos_rebuild`, `healing`, `diagnostic`, `sync_promoted_rule` require Ed25519 verification. Only `force_checkin`, `run_drift`, `restart_agent` allowed pre-checkin.
- **Go daemon v0.3.84: ReloadRules() after sync_promoted_rule (Session 201).** Rules were being written to disk but never loaded into memory. `SetRuleReloader(d.l1Engine.ReloadRules)` wired in daemon.go.
- **Nonce replay TTL is 2 hours (Session 201).** Was 24h. `nonceMaxAge = 2 * time.Hour` in processor.go.
- **Download domain allowlist: api.osiriscare.net + release.osiriscare.net ONLY (Session 201).** github.com removed — too broad.
- **`portal_access_log` is PARTITIONED by month** (Migration 138, Session 200). 
- **`incident_remediation_steps` replaces `incidents.remediation_history` JSONB** (Migration 137, Session 200). Relational table: incident_id FK, tier, runbook_id, result, confidence, created_at. `remediation_history` JSONB column still exists on incidents but is no longer written to — code uses INSERT/SELECT on new table. routes.py falls back to JSONB if table doesn't exist.
- **Dual connection pools are intentional.** SQLAlchemy (shared.py, pool_size=20) for admin CRUD via `Depends(get_db)`. asyncpg (fleet.py, min=2/max=25) for RLS-enforced queries via `tenant_connection()`/`admin_connection()`. Both go through PgBouncer (25 server conns). Cannot consolidate — RLS requires asyncpg's `SET LOCAL` transaction control.
- **Checkin savepoints: every step must have one.** Bare queries in `sites.py` checkin handler poison the tenant_connection transaction on failure. All steps (3.5, 3.6, 4, 4.5, 6b-2) wrapped in `async with conn.transaction():` for isolation (Session 200).
- **`normalize_mac_for_ring()` vs `normalize_mac()`.** `hash_ring.py` has `normalize_mac_for_ring()` (stripped, no separators: `843A5B91B661`). `sites.py` has `normalize_mac()` (colon-separated: `84:3A:5B:91:B6:61`). Don't confuse them.
- **Device sync has 3 competing sources.** Netscan (6 devices, IP IDs), replay (12 devices, UUID IDs), home net (23 devices, MAC IDs). The CASE expression in device_sync.py ensures IP format always wins. GREATEST() prevents timestamp reversion.
- **CSRF exempt paths for machine-to-machine endpoints.** `/api/witness/submit`, `/api/provision/`, `/api/appliances/checkin`, `/api/devices/sync` must be in `csrf.py EXEMPT_PATHS`. Missing = 403/500 from daemon.
- **Ops center endpoints**: `/api/ops/health` (admin), `/api/ops/health/{org_id}` (partner), `/api/ops/audit-readiness/{org_id}`, `PUT /api/ops/audit-config/{org_id}`. Registered in main.py via ops_health_router + audit_report_router.
- **requirements.txt MUST use exact pins (`==`).** Loose pins (`>=`) caused pydantic v2 breakage in CI. Include `pydantic-core` and `pydantic-settings` explicitly — they're transitive deps that pip resolves differently across Python versions.
