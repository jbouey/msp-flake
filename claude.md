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
-----|---------|-----|------------------
lessons | Sessions 200-209 | docs/lessons/sessions-200-209.md | evidence partitioning, fleet-order auth, auditor kit, RLS P0, flywheel spine, substrate engine, v38 ISO
lessons | Sessions 210-212 | docs/lessons/sessions-210-212.md | appliance relocation, daemon v0.4.11/.12/.13, sigauth identity-vs-evidence, admin_transaction PgBouncer routing, buildAuthedHeaders, flywheel audit DLQ
lessons | Sessions 213-215 | docs/lessons/sessions-213-215.md | canonical_site_id + rename_site centralized fn, F6 federation tier MVP, F7 diagnostic, kill-switch + admin destructive billing, score=0 closure, deploy-verification rule
lessons | Sessions 216-217 | docs/lessons/sessions-216-217.md | owner-transfer state machines (mig 273+274), MFA admin overrides (mig 276), email rename (mig 277), client-portal RLS org-scope (mig 278), unified compute_compliance_score, Auditor Kit reframing, restore-endpoint auth deadlock
lessons | Session 218 | docs/lessons/sessions-218.md | RT33 portal ghost-data + appliance visibility (client + partner) + auditor-kit StreamingResponse, RT21 cross-org site relocate (mig 279+280+281, 3-actor state machine, attested feature_flags table behind outside-counsel BAA review), python3.11 pre-push syntax gate (deploy outage class)
lessons | Sessions 219-220 | docs/lessons/sessions-219-220.md | Session 219: L2 audit-gap closure (mig 300), delegate_signing_key privileged-chain (mig 305), L1 escalate false-heal (1137 prod L1-orphans, daemon+backend two-layer fix, mig 306 backfill pending Maya Gate A), substrate per-assertion admin_transaction cascade-fail closure, TWO-GATE protocol locked-in. Session 220: Master BAA v1.0-INTERIM shipped, BAA enforcement triad (5 active workflows, sev1 substrate invariant), synthetic-site MTTR soak (mig 315+323), BUG 1 site_appliances 81→0, Counsel Rule 1 canonical-source close (Phase 3 26→0 via implementation-discovery override of Gate A MIGRATE verdicts), registry-integrity drift gate, compute_category_weighted_overall canonical primitive (Fork B leak audit close), 6 ratchet baselines hard-locked at 0
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
python3 .agent/scripts/context-manager.py validate     # Memory hygiene check
```

Primary state: `.agent/claude-progress.json` (schema v2 — see `.agent/archive/claude-progress.v1.json` for legacy v1 dump).

## Memory Hygiene (Session 205 cleanup, ENFORCED)

- `~/.claude/projects/.../memory/MEMORY.md` is **truncated at ~200 lines** at session start. Keep it as a pure index of pointer-rows; put detail in topic files.
- Every topic file under `memory/` must start with YAML frontmatter:
  ```yaml
  ---
  name: short title
  description: one-line — used when deciding to load
  type: feedback | project | reference | user
  decay_after_days: 30  # feedback default; project=60; reference=365
  last_verified: YYYY-MM-DD
  ---
  ```
- `decay_after_days` is a soft signal: a memory older than that should be re-verified or archived. Defaults: feedback=30, project=60, reference=365.
- `active_tasks` field in `claude-progress.json` is **always empty** in v2 — Claude Code's TaskCreate is the authoritative task store. Do not regress to in-line tasks.
- `python3 .agent/scripts/context-manager.py validate` runs all hygiene checks and is wired into `.github/workflows/memory-hygiene.yml` on every push that touches `.agent/`. Pytest cases at `.agent/scripts/test_context_manager.py`.

## Privileged-Access Chain of Custody (Session 205, INVIOLABLE)

**`client identity → policy approval → execution → attestation` is an unbroken cryptographically verifiable chain.** Any privileged action on a customer appliance (`enable_emergency_access`, `disable_emergency_access`, `bulk_remediation`, `signing_key_rotation`) MUST carry the chain end-to-end. Enforced at three layers — breaking any one is a **security incident**, not a cleanup task:

- **CLI** (`backend/fleet_cli.py`): refuses privileged orders without `--actor-email` + `--reason ≥20ch` + successful `create_privileged_access_attestation()`. Rate-limited 3/site/week via `count_recent_privileged_events`.
- **API** (`backend/privileged_access_api.py`): partner-initiated + client-approved request flow. Each state transition writes a chained attestation bundle. Per-site `privileged_access_consent_config` controls whether client approval is required.
- **DB** (migration 175 `trg_enforce_privileged_chain`): REJECTS any `fleet_orders` INSERT of a privileged type unless `parameters->>'attestation_bundle_id'` matches a real `compliance_bundles WHERE check_type='privileged_access'` row for the same site.

The attestation itself is Ed25519-signed by server, hash-chained to the site's prior evidence bundle (unified chain across drift + remediation + privileged events), OTS-anchored via the existing Merkle-batch worker, and published into `/api/evidence/sites/{id}/auditor-kit` ZIP + the client portal evidence view. Customers + auditors verify independently.

**Three lists MUST stay in lockstep** (any gap = chain violation):
- `fleet_cli.PRIVILEGED_ORDER_TYPES`
- `privileged_access_attestation.ALLOWED_EVENTS`
- `migration 175 v_privileged_types` in `enforce_privileged_order_attestation()`

**Never** log actor as `system`/`fleet-cli`/`admin` — actor MUST be a named human email. **Never** flip `client_approval_required=false` without a consent-config attestation bundle. **Never** `ALTER TABLE fleet_orders DISABLE TRIGGER` for bulk ops. **Never** skip the OTS enqueue for `privileged_access` bundles.

See `docs/security/emergency-access-policy.md` + `.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_critical_architectural_principles.md` §8.

## Counsel's 7 Hard Rules (2026-05-13, GOLD AUTHORITY, INVIOLABLE)

Outside HIPAA counsel laid these down 2026-05-13 for multi-device enterprise-scale close. Treated as gold-grade architectural authority — first-pass filter on every design, every Gate A review, every Class-B 7-lens round-table, every commit. Where these conflict with prior internal heuristics, **the 7 rules win.** Full enumeration + worked examples at `.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_enterprise_counsel_seven_rules.md`.

1. **No non-canonical metric leaves the building.** Every customer-facing metric declares a canonical source. Anything non-canonical is hidden or marked non-authoritative. No deck / dashboard / postcard / auditor artifact computes against convenience tables. (Counsel cited broken truth paths around `runbook_id`, L2 resolution recording, `orders.status` completion state — these are not harmless internal noise at enterprise scale.)

2. **No raw PHI crosses the appliance boundary.** PHI-free Central Command is a compiler rule, not a posture preference. Every new data-emitting feature (endpoint, log sink, LLM prompt, export, email template) MUST answer at merge time: *"Could raw PHI cross this boundary?"* If the answer is not a hard no, it does not ship.

3. **No privileged action without attested chain of custody.** No privileged order type without lockstep registration. No admin bypass "just for now." No feature flag for legally sensitive behavior without schema-enforced approval chain. No human operator improvisation outside attested pathways. (Detailed in §"Privileged-Access Chain of Custody" above.)

4. **No segmentation design that creates silent orphan coverage.** One authoritative site anchor as default. Add collectors only where segmentation forces it. Prefer host agents where possible. **Orphan detection is sev1, not tolerable warning.** At enterprise scale, coverage holes are worse than inefficiency.

5. **No stale document may outrank the current posture overlay.** Any operational or legal workflow must cite either the current posture overlay (`docs/POSTURE_OVERLAY.md` once shipped — task #51) OR a refreshed doc that supersedes the old one. Stale onboarding / runbook / dashboard docs are not authority just because they exist.

6. **No legal/BAA state may live only in human memory.** BAA state gates functionality, not just paperwork. No receiving-org behavior without explicit BAA / eligibility satisfaction. **Expired BAA must block new ingest or sensitive workflow advancement.** Machine-enforced where possible.

7. **No unauthenticated channel gets meaningful context by default.** Opaque by default for every email / webhook preview / SMS / notification subject / unauthenticated response. Context belongs behind auth. Small leaks become enterprise embarrassments.

Two additional rules in the 10-rule enumeration: **(8) Subprocessors classified by actual data flow, not hopeful labeling** + **(9) Determinism and provenance are not decoration** + **(10) Never let the platform imply clinical authority** — all detailed in the memory file linked above.

**Application:** when reviewing any new design / commit / Class-B packet / customer-facing artifact, run the 7-rule filter FIRST. If any rule is unaddressed or weakly addressed, BLOCK pending revision. Counsel-grade gold authority overrides internal heuristics.

## Rules

- **Migration numbers are claimed via the RESERVED_MIGRATIONS ledger (Task #59 Session 220-2026-05-13).** Every new `migrations/NNN_*.sql` file MUST be pre-claimed: add a row to `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` AND drop a line-anchored `<!-- mig-claim:NNN task:#TT -->` HTML-comment marker in the design doc (outside code fences). CI gate `tests/test_migration_number_collision.py` enforces no double-claims, no claims-on-shipped, marker↔ledger parity, ≤30 active rows, stale rows need per-row justification. When the migration ships, REMOVE the ledger row in the same commit. Pattern emerged after 3-of-6 designs collided on mig numbers in a single Gate A sweep.
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
- **`check_type_registry` is the single source of truth for check names (Session 205, Migration 157).** Go daemon check names are canonical. Scoring categories, HIPAA controls, display labels, monitoring-only flags all from this table. Hardcoded `CATEGORY_CHECKS` in `db_queries.py` is fallback — registry overrides at startup via `load_check_registry()`. When adding new checks to daemon, add to registry migration. 15 guardrail tests verify completeness.
- **MONITORING_ONLY_CHECKS loaded from registry at startup.** Hardcoded set in `agent_api.py` is fallback. `load_monitoring_only_from_registry()` overrides.
- **`nix.gc` + `nix.optimise` weekly automatic** in `/etc/nixos/configuration.nix` on VPS. `--delete-older-than 14d`, `persistent = true`.
- **asyncpg savepoint invariant (Session 205).** Every `conn.execute/fetch*` inside `tenant_connection`/`admin_connection` whose failure is caught non-fatally MUST be inside `async with conn.transaction():` (or SQLAlchemy `db.begin_nested()`). Python's try/except does NOT reset Postgres transaction state.
- **No silent write failures.** `except Exception: pass` on DB writes BANNED. `logger.warning` on DB failures BANNED → `logger.error(exc_info=True)`. Reads may eat exceptions; writes log-and-raise.
- **CI/CD deploys code AND restarts automatically.** The deploy workflow rsyncs to `dashboard_api_mount`, restarts mcp-server+frontend, runs health check, and auto-rollbacks on failure. No manual restart needed.
- **iMac SSH on port 2222** (Session 191). Port 22 blocked by MikroTik HW offload (Ethernet↔WiFi). Use `ssh -p 2222 jrelly@192.168.88.50`. LaunchDaemon plist at `/Library/LaunchDaemons/com.local.sshd2222.plist`.
- **Credential encryption key required.** Fernet key at `/app/secrets/credential_encryption.key`. Missing key = RuntimeError on startup. Key also in `.env` as `CREDENTIAL_ENCRYPTION_KEY`.
- **`dashboard_api_mount` is the ONLY backend mount (Session 202).** The conflicting `./app/dashboard_api` build-context mount was removed. Deploy backend to `/opt/mcp-server/dashboard_api_mount/` only. Never rsync to `app/dashboard_api`.
- **Go build ldflags**: Version is `internal/daemon.Version`, NOT `main.Version`. Use `make build-linux VERSION=x` or `-X github.com/osiriscare/appliance/internal/daemon.Version=x`.
- **Terminology: "drift" is dead (Session 199).** User-facing text says "failing"/"compliance issue"/"configuration change". Variable names may still say "drift" — that's fine, only display text matters. `constants/status.ts` has `drifted.label: 'Failing'`. `cleanAttentionTitle()` in `constants/status.ts` maps backend titles.
- **Legal language (Session 199).** Never use "ensures", "prevents", "protects", "guarantees", "audit-ready", "PHI never leaves", "100%". Use "monitors", "helps detect", "reduces exposure", "audit-supportive", "PHI scrubbed at appliance". Disclaimers that LIMIT liability are safe — don't change them.
- **auth.py: always use execute_with_retry() (Session 199).** Raw `db.execute()` causes DuplicatePreparedStatementError through PgBouncer. `validate_session()` runs on EVERY authenticated request — must use `execute_with_retry()`.
- **Site rename is a multi-table migration.** When a `site_id` is renamed or recreated, ALL of these tables must be migrated in lockstep or drift bugs appear later: `site_credentials` (Session 199 — blank targets), `appliance_provisioning` (Session 210 — reflashed appliance re-provisioned under the old site_id, created a parallel `site_appliances` row), `aggregated_pattern_stats` (Session 210 — phantom `promotion_eligible=true` rows pointing at the renamed site), `platform_pattern_stats`, and any other table keyed by `site_id`. Prefer `UPDATE … SET site_id = $new WHERE site_id = $old` inside a single transaction across all of them; never rename the `sites` row alone.
- **Per-appliance signing keys (Session 196).** `site_appliances.agent_public_key` — NOT `sites.agent_public_key`. Evidence verification checks per-appliance keys. Multi-appliance sites MUST NOT use the single site-level key.
- **`compliance_bundles` is the evidence table, NOT `evidence_bundles`.** evidence_bundles is a legacy table (1 row). compliance_bundles has 232K+ entries, Ed25519 signed, hash-chained, OTS-anchored. **PARTITIONED by month** (Migration 138, Session 200). Default partition catches overflow. **ON CONFLICT (bundle_id) is INCOMPATIBLE with partitioned tables** — use DELETE+INSERT upsert pattern (Session 201).
- **Dual connection pools are intentional.** SQLAlchemy (shared.py, pool_size=20) for admin CRUD via `Depends(get_db)`. asyncpg (fleet.py, min=2/max=25) for RLS-enforced queries via `tenant_connection()`/`admin_connection()`. Both go through PgBouncer (25 server conns). Cannot consolidate — RLS requires asyncpg's `SET LOCAL` transaction control.
- **Checkin savepoints: every step must have one.** Bare queries in `sites.py` checkin handler poison the tenant_connection transaction on failure. All steps (3.5, 3.6, 4, 4.5, 6b-2) wrapped in `async with conn.transaction():` for isolation (Session 200).
- **`normalize_mac_for_ring()` vs `normalize_mac()`.** `hash_ring.py` has `normalize_mac_for_ring()` (stripped, no separators: `843A5B91B661`). `sites.py` has `normalize_mac()` (colon-separated: `84:3A:5B:91:B6:61`). Don't confuse them.
- **Device sync has 3 competing sources.** Netscan (6 devices, IP IDs), replay (12 devices, UUID IDs), home net (23 devices, MAC IDs). The CASE expression in device_sync.py ensures IP format always wins. GREATEST() prevents timestamp reversion.
- **CSRF exempt paths for machine-to-machine endpoints.** `/api/witness/submit`, `/api/provision/`, `/api/appliances/checkin`, `/api/devices/sync` must be in `csrf.py EXEMPT_PATHS`. Missing = 403/500 from daemon.
- **requirements.txt MUST use exact pins (`==`).** Loose pins (`>=`) caused pydantic v2 breakage in CI. Include `pydantic-core` and `pydantic-settings` explicitly — they're transitive deps that pip resolves differently across Python versions.
- **agent_api.py `_enforce_site_id()` required on ALL appliance endpoints (Session 202).** Every endpoint with `Depends(require_appliance_bearer)` must call `_enforce_site_id(auth_site_id, request_site_id, endpoint_name)`. Prevents cross-site spoofing. 13 endpoints hardened.
- **`execute_with_retry()` required for ALL SQLAlchemy queries through PgBouncer (Session 202).** auth.py (7 queries), routes.py (104 queries), oauth_login.py (25 queries), users.py (33 queries) all migrated. New code must use `execute_with_retry(db, text(...), params)` not `db.execute()`.
- **Go daemon uses `slog` structured logging (Session 202).** 15 files migrated from `log.Printf` to `slog.Info/Warn/Error` with `"component"` key. New Go code must use slog, not log.Printf.
- **`go_agents.site_id` has FK constraint to `sites(site_id)` ON DELETE CASCADE (Migration 144).** Prevents orphaned agents under wrong sites.
- **Go agent summary computed live on read (Session 202).** `get_site_go_agents()` in sites.py computes summary from raw `go_agents` rows, not from stale `site_go_agent_summaries` table.
- **`COMPLIANCE_CATEGORIES` in `client_portal.py` is the single source of truth** for check-type → category mapping. Never inline category dicts in functions.
- **`_send_smtp_with_retry()` in `email_alerts.py`** is the single SMTP send function. All email functions delegate to it. Never write inline SMTP retry loops.
- **asyncpg `$1` parameters need `::text` cast** when the same connection runs multiple queries with `$1` against different column types. PgBouncer statement caching causes `AmbiguousParameterError` otherwise. See `health_monitor.py` mesh isolation queries.
- **Fleet CLI must include `--param site_id=<site>` for v0.3.82 compatibility.** The April 6 order that worked on the installer included `site_id` in parameters. Orders without it are silently dropped by v0.3.82 daemons (unconfirmed but correlates with observed behavior).
- **Stripe products use lookup_keys, not price IDs.** Code references `osiris-pilot-onetime`, `osiris-essentials-monthly`, `osiris-professional-monthly`, `osiris-enterprise-monthly`. `stripe.Price.list(lookup_keys=[...])` resolves at runtime. Price-id rotation in Stripe doesn't require a code deploy.
- **`stripe` Python lib in image requires manual rebuild.** Deploy workflow does NOT rsync Dockerfile or requirements.lock — only rsyncs backend/frontend code. New/bumped Python deps require `scp` of Dockerfile + requirements.lock to `/opt/mcp-server/app/` on VPS and `docker compose build mcp-server && docker compose up -d mcp-server`. Container `appuser` is pinned to **UID 1000** to match host bind-mount ownership (secrets/, runbooks/, evidence/ on VPS are uid=1000).
- **Nix flake build needs `git add -A` on dirty worktrees.** `buildGoModule` + `src = ../appliance` filters sources via git index — if you rsync new files into a non-git-tracked copy of the repo (e.g. onto the VPS at `/root/Msp_Flakes`), Nix will exclude them. Symptom: `undefined: <symbol>` compile errors for code that exists on disk but not in `git ls-files`. Fix: `cd /root/Msp_Flakes && git add -A` before rebuilding. Do NOT try to work around it by shoe-horning `src = ./.` with a broader path filter; the git filter exists for a reason.
- **`fleet_orders` is FLEET-WIDE — no site_id/appliance_id columns.** Per-appliance scoping is via `target_appliance_id` embedded in the SIGNED payload (`processor.go::verifyHostScope` rejects mismatched). Use `order_signing.sign_admin_order(target_appliance_id=...)` for per-appliance orders (e.g. reprovision); `order_signing.sign_fleet_order` for true fleet-wide (e.g. update_daemon). The relocate endpoint's first version had a broken INSERT referencing nonexistent columns — fixed in `331b7d29`.
- **`admin_audit_log` column is `username`, not `actor`.** Schema: (id, user_id, username, action, target, details, ip_address, created_at). Two new endpoints (sites.py:relocate, provisioning.py:admin_restore) had `actor` and would have failed on first call — fixed in `24613c15`.
- **`promoted_rules` natural unique key is (site_id, rule_id), NOT rule_id alone (Session 210-B Migration 247).** Same rule rolls out to many sites; each gets its own row. `INSERT ... ON CONFLICT (rule_id)` raises `InvalidColumnReferenceError` because rule_id has no unique constraint. Use `ON CONFLICT (site_id, rule_id) DO UPDATE`. The bug was in 3 places (flywheel_promote.py, client_portal.py, learning_api.py) — all fixed. Migration 247 added the UNIQUE INDEX. Tests: `test_sql_columns_match_schema.py` is the linter; static check for ON-CONFLICT-vs-unique-constraint is task #167.
- **Frontend mutation CSRF rule (Session 210-B audit P0).** Every state-changing fetch (POST/PUT/PATCH/DELETE) must EITHER use `fetchApi` (utils/api.ts auto-injects CSRF + credentials) OR include both `credentials: 'include'` AND `headers: { 'X-CSRF-Token': getCsrfTokenOrEmpty() }`. Raw fetches missing CSRF return 403 from the CSRF middleware. Test: `test_frontend_mutation_csrf.py` (ratchet baseline 58, drive to 0). Caught OperatorAckPanel + SensorStatus on first run.
- **`admin_transaction()` for multi-statement admin paths (Session 212).** `tenant_middleware.admin_transaction(pool)` is the canonical helper for any admin-context work that issues 2+ queries. Pins SET LOCAL app.is_admin + the queries to a single PgBouncer backend via an explicit transaction. Use for read-multi or read+write paths. **Single-statement reads stay on `admin_connection`** (the SET and the read share one pgbouncer transaction in that case). Routing-pathology fix shipped inline at sites.py:3565 (sigauth verify, commit 303421cc); centralized helper added in b62c91d2; symbolic adopters at prometheus_metrics.py:108 (f89802be) + device_sync.py:145 (92f2f73b). 230 more sites identified by audit — migrate as routing evidence surfaces.
- **Site-id rename: use `rename_site()` SQL function ONLY (mig 257).** Direct `UPDATE … SET site_id =` outside the per-line `# noqa: rename-site-gate` allowlist fails CI (`tests/test_no_direct_site_id_update.py`, ratchet 6). The function alias-renames via `site_canonical_mapping`; cryptographic + audit tables are in the IMMUTABLE list and are NOT touched. Detail: `docs/lessons/sessions-213-215.md`.
- **`canonical_site_id()` for telemetry/operational aggregations (mig 256).** Resolves any site_id to canonical via `site_canonical_mapping`. Use everywhere that aggregates by site_id (telemetry, incidents, l2_decisions, aggregated_pattern_stats). **NEVER for `compliance_bundles`** — Ed25519+OTS bind to original site_id forever; CI gate `tests/test_canonical_not_used_for_compliance_bundles.py` enforces. Detail: `docs/lessons/sessions-213-215.md`.
- **Deploy-verification process rule (Session 215 #77).** Every commit MUST: pre-push gates → push → wait CI green → `curl /api/version` → assert `runtime_sha == disk_sha == deployed commit` BEFORE claiming shipped. `tests/test_pre_push_allowlist_only_references_git_tracked_files` enforces the local-vs-git-tree class. Detail: `docs/lessons/sessions-213-215.md`.
- **Owner-transfer state machines (Session 216, mig 273 + 274).** Two cohabiting state machines for org-level role transfers: `client_org_owner_transfer_requests` (6 events, 24h cooling-off, magic-link target accept, target-creation flow) and `partner_admin_transfer_requests` (4 events, immediate-completion, OAuth-session re-auth, target-must-pre-exist). Both backed by 1-owner-min / 1-admin-min DB triggers — schema-level last-line-defense. Per-org `transfer_cooling_off_hours` + `transfer_expiry_days` configurable via `PUT /api/{client/users,partners/me}/owner-transfer/transfer-prefs` (mig 275). Detail: `docs/lessons/sessions-216-*.md` (when written) + `client_owner_transfer.py` / `partner_admin_transfer.py`.
- **Operator-alert chain-gap escalation pattern (Session 216).** Every operator-visibility hook that follows an Ed25519 attestation MUST escalate severity to `P0-CHAIN-GAP` + append `[ATTESTATION-MISSING]` to the subject if the attestation step failed. Implemented uniformly across 16 hooks via `_send_operator_alert(...)` + per-callsite `<event>_attestation_failed: bool` flag. Test pinned in `test_operator_alert_hook_callsites` AST gate + `test_user_mutation_ed25519_parity`.
- **Anchor-namespace convention for cryptographic chains (Session 216).** Client-org events anchor at the org's primary `site_id` via `SELECT … FROM sites WHERE client_org_id=$1 ORDER BY created_at ASC LIMIT 1`, with `client_org:<id>` synthetic fallback when no sites yet. Partner-org events anchor at `partner_org:<partner_id>` synthetic. Auditor kit walks partner-event chains by namespace prefix. NEVER use `canonical_site_id()` for these anchors — chain is immutable, mapping is read-only.
- **org_connection RLS coverage (Session 217 mig 278).** Any new site-RLS table read by client_portal under `org_connection` MUST also have a parallel `tenant_org_isolation` policy via `rls_site_belongs_to_current_org(site_id::text)`. Without it, every client-portal read returns zero rows (silent for ~months pre-fix on `compliance_bundles` — customer saw 0 bundles for 155K-row org). Pinned by `test_org_scoped_rls_policies.py` (manual SITE_RLS_TABLES list + auto-discover meta-gate over migration `CREATE POLICY` statements). Detail: `docs/lessons/sessions-216-217.md`.
- **Canonical compliance-score helper (Session 217 RT25 + RT30).** `compliance_score.compute_compliance_score(conn, site_ids, *, include_incidents=False, window_days=DEFAULT_WINDOW_DAYS)` is THE source of truth for the customer-facing compliance number. All three surfaces (`/api/client/dashboard`, `/api/client/reports/current`, `/api/client/sites/{id}/compliance-health`) delegate. NEVER inline `passed/total*100` — pinned by `test_no_ad_hoc_score_formula_in_endpoints`. Default window is 30 days (profiled 4.7s → 2.4s on 155K bundles); auditor-export contexts override to `window_days=None` for all-time chain reads.
- **Partner-side mutation role-gating (Session 217 RT31).** Every `/me/*` POST/PUT/PATCH/DELETE on `partners.py` MUST use `require_partner_role("admin")` (partner-org-state) or `require_partner_role("admin", "tech")` (site-state). Bare `Depends(require_partner)` is forbidden — billing-role partner_user could rotate site credentials pre-fix. Per-user mutations (own-notification-read) may stay relaxed but must be allowlisted in `test_no_partner_mutation_uses_bare_require_partner`. Auditor-kit + similar rate-limited downloads must use JS fetch→blob (not `<a href>`) so customers see actionable error copy on 401/429.
- **Portal site_appliances + sites filters (Session 218 RT33 P1).** Every `client_portal.py` and `partners.py` query that JOINs or selects `site_appliances` MUST filter `sa.deleted_at IS NULL` ON the JOIN line itself (not in WHERE on a continuation line — the gate's window heuristic anchors on JOIN line). Every site-list query (`WHERE s.client_org_id = $1` shape) MUST filter `s.status != 'inactive'`. Pinned by `test_client_portal_filters_soft_deletes.py` over both files. Soft-delete carve-outs (e.g. historical-IP exclusion list) require an inline `intentionally NOT filtered` comment that the gate checks for. Pre-fix the user saw phantom orgs/sites/appliances when North Valley was the only real customer.
- **Portal appliance endpoints query site_appliances directly, NOT the rollup MV (Session 218 RT33 P2 Steve veto).** PG materialized views don't inherit base-table RLS policies. The `appliance_status_rollup` MV (mig 193) is faster but reading it bypasses `tenant_org_isolation` — a refactor that drops the explicit JOIN clause silently leaks every appliance to every customer. The new `/api/client/appliances` + `/api/partners/me/appliances` endpoints query `site_appliances` directly with an inline LATERAL heartbeat join (same `live_status` semantics as the MV) — proper RLS defense in depth. Pinned by `test_client_appliances_field_allowlist.py`. Field allowlist on the customer endpoint excludes mac/ip/daemon_health (Carol veto on Layer-2 leak).
- **Pre-push python3.11 syntax check (Session 218 RT33 deploy outage).** CI runs Python 3.11; local devs may be on 3.13/3.14 which accepts more syntax (e.g. backslashes in f-string expressions). Three RT33 deploys failed at test-collection time because the pre-push allowlist used 3.13 syntax. `.githooks/pre-push` now compiles every backend `.py` through `python3.11` if available — fast (~1s) and catches the exact class deterministically before push. NEVER write `re.sub(r"\s", " ", x)` inside an f-string expression — extract to a variable first.
- **Cross-org site relocate (Session 218 RT21 + counsel-revision v2).** `cross_org_site_relocate.py` ships behind an attestation-gated feature flag (mig 281 + 282 dual-admin) that returns 503 until outside HIPAA counsel signs off on the v2 packet's four §-questions: (a) §164.504(e) permitted-use scope under both source-org and target-org BAAs (regardless of vendor identity — counsel's adversarial review 2026-05-06 retired the v1 "same-BA inapplicability" framing as attackable); (b) §164.528 substantive completeness + retrievability of the disclosure accounting (counsel correction: legal test is content + producibility, not chain tamper-resistance); (c) receiving-org BAA scope (likely commercial choke point — addendum may be required for received clinics); (d) opaque-mode email defaults (no clinic/org names in subjects/bodies; portal auth serves identifying context). Three-actor state machine with pinned `expected_*_email` columns (multi-owner attribution rule), race-guarded `UPDATE sites WHERE client_org_id = source_org_id` on execute, 24h cooling-off CHECK constraint, dual-admin governance (mig 282: `lower(approver) <> lower(proposer)` enforced at DB CHECK; endpoints `/propose-enable` + `/approve-enable`). Flag-flip event INTENTIONALLY ABSENT from `ALLOWED_EVENTS` (FK incompatibility — flag has no site anchor); audit lives in append-only `feature_flags` table + `admin_audit_log`. Substrate invariant `cross_org_relocate_chain_orphan` (sev1) catches sites with `prior_client_org_id` set but no completed relocate row (bypass-path detector). Detail: `docs/lessons/sessions-218.md` + `.agent/plans/21-counsel-briefing-packet-2026-05-06.md` (v2).
- **Opaque-mode email parity (Session 218 task #42, 2026-05-06).** Once one customer-facing email class shipped opaque per counsel (RT21 v2.3), any verbose-mode helper became attackable: it leaked context the opaque class deliberately withheld. Three modules now opaque: `cross_org_site_relocate.py`, `client_owner_transfer.py` (`_send_initiator_confirmation_email`, `_send_target_accept_email`), `client_user_email_rename.py` (`_send_dual_notification`). Subjects are static or reference-id-only (`transfer_id[:8]`); bodies redirect to authenticated portal — no org/clinic/actor names in the SMTP channel. Audit chain UNCHANGED — `actor_kind`, `org_name` still captured in `admin_audit_log` + attestation rows. Opaque-to-email ≠ opaque-to-audit. Pinned by `tests/test_email_opacity_harmonized.py` (8 gates, includes Maya hardenings: forbid `{old_email}` in NEW-recipient body; ban f-string subjects). NEVER add a new customer-facing email helper that interpolates org/clinic/actor names; subjects must be plain string literals.
- **Auditor-kit determinism contract (Session 218 round-table 2026-05-06).** Two consecutive downloads of the kit with no chain progression and no OTS pending→anchored transitions and no presenter-brand edits and no advisory-set changes MUST produce byte-identical ZIPs. The contract is the load-bearing tamper-evidence promise — auditors hash the kit and compare across downloads to detect substitution. Implementation: `auditor_kit_zip_primitives.py::_kit_zwrite` (pinned `date_time` + `compress_type=ZIP_DEFLATED` + `external_attr=0o644<<16`) + `_KIT_COMPRESSLEVEL=6` (zlib level — pinned for cross-CPython byte-identity) + `sort_keys=True` on every JSON dump (chain.json, pubkeys.json, identity_chain.json, iso_ca_bundle.json, bundles.jsonl per-line) + sorted entry order + sorted OTS files + `ORDER BY iso_release_sha` on the iso_ca SQL. `generated_at` derives from chain-head `latest.created_at` (NOT wall-clock); wall-clock `download_at` is ONLY for audit-log + (deterministic) Content-Disposition filename. `kit_version` pinned to `2.1` across all 4 surfaces (X-Kit-Version header, chain_metadata, pubkeys_payload, identity_chain_payload, iso_ca_payload). NEVER use `datetime.now()` to generate kit-internal timestamps; NEVER skip `sort_keys=True` on a kit JSON dump; NEVER bypass `_kit_zwrite` for a ZIP entry; NEVER advance kit_version on one surface without all four. Pinned by `tests/test_auditor_kit_integration.py` (10 tests open the actual ZIP) + `tests/test_auditor_kit_deterministic.py` (source-shape gates).
- **Auditor-kit auth: 5 branches, partner-portal MUST role-gate (Session 218 round-table 2026-05-06).** `require_evidence_view_access` accepts: (1) admin session, (2) `osiris_client_session` cookie + org owns site, (3) `osiris_partner_session` cookie + `sites.partner_id` matches + role IN {admin, tech}, (4) legacy `portal_session` cookie, (5) legacy `?token=` query param (deprecation telemetry warns on each use). Billing-role partner_users MUST NOT pull evidence (RT31 site-state class). Per-(site, caller) rate limit isolates buckets per identity so an admin's investigation, a partner's pull, and an auditor's download don't compete for the same 10/hr cap. Every download writes a structured `auditor_kit_download` row to `admin_audit_log` (best-effort — failure logs at ERROR but does NOT block the §164.524 access right). Auditor kit framing is `audit-supportive technical evidence`, NOT a §164.528 disclosure accounting; README + ClientReports + PracticeHomeCard ship IDENTICAL §164.528 disclaimer copy.
- **In-source `.format()` templates are a banned shape for customer-facing artifacts (Session 218 round-table 2026-05-06).** Today's recurring kit 500s traced to literal `{bundle_id}` and JSON example `{...}` blocks inside `_AUDITOR_KIT_README` being interpreted by Python's `.format(**kwargs)` as placeholders → `KeyError` on every download. NEVER add a new customer-facing `.format()` template inside a `.py` triple-quoted string. ANY `{` and `}` in prose/JSON examples MUST be escaped as `{{` / `}}` until the artifact migrates to Jinja2. Pinned by `tests/test_auditor_kit_integration.py::test_real_readme_template_formats_without_keyerror` (smoke against production kwargs) + `test_readme_template_has_only_allowed_placeholders` (static allowlist scan; whitespace + positional + format-spec hardened). Migration target (NEXT SESSION): move `_AUDITOR_KIT_README` + `_AUDITOR_KIT_VERIFY_SH` to `backend/templates/auditor_kit/*.j2` with Jinja2 `StrictUndefined` + boot-smoke that aborts container start on render error (`{% raw %}` for fenced examples). Closes the entire `.format()`-drift class structurally.
- **Pre-push full-CI-parity sweep (Session 218 round-table 2026-05-06).** `.githooks/full-test-sweep.sh` runs every `tests/test_*.py` (excluding `*_pg.py`) in its own subprocess matching CI's stub-isolation, parallel `-P 6`, ~92s on a fully-equipped dev box. Dep-import failures (asyncpg/pynacl/pydantic_core/nacl/sqlalchemy.ext.asyncio/aiohttp/cryptography/google.auth) are SKIPPED — those run server-side. Closes the `18af959c` failure class where a test pinned a literal that the SOURCE_LEVEL_TESTS curated list didn't cover. Tunables: `PRE_PUSH_PARALLEL=N` / `PRE_PUSH_SKIP_FULL=1`. Curated SOURCE_LEVEL_TESTS array stays as the fast lane (~45s) for narrow-target iterations; full sweep is the comprehensive belt-and-suspenders.
- **`resolution_tier='L2'` requires `l2_decision_recorded` gate (Session 219 mig 300, task #104).** Substrate invariant `l2_resolution_without_decision_record` (sev2) caught 26 north-valley-branch-2 incidents with `resolution_tier='L2'` but no matching `l2_decisions` row — ghost-L2 audit gap that violates the data-flywheel + attestation chain. Root cause: agent_api.py:1338 + main.py:4530 both swallowed `record_l2_decision()` exceptions and continued setting `resolution_tier='L2'` anyway. Fix: introduce `l2_decision_recorded: bool` set inside the try-block immediately after the record call. The `if l2_decision_recorded and decision.runbook_id ...` gate refuses to set L2 without the audit row — escalates to L3 instead. NEVER set `resolution_tier='L2'` (Python literal OR SQL UPDATE) without an `l2_decision_recorded` reference within 80 lines above. Pinned by `tests/test_l2_resolution_requires_decision_record.py` (3 tests: source-walk + positive control + negative control). Backfill mig 300 inserts synthetic `l2_decisions` rows with `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'` for the 26 historical orphans; `llm_model='backfill_synthetic'` makes them auditor-distinguishable.
- **`jsonb_build_object($N, ...)` params need explicit casts (Session 219).** asyncpg's prepare phase can't infer the JSONB-component type for unannotated params — and PgBouncer statement caching makes the inference even worse. Symptom: `IndeterminateDatatypeError: could not determine data type of parameter $N`, silently failing audit-row INSERTs (`journal_api.py:178` JOURNAL_UPLOAD_UNSCRUBBED was firing 1×/3-5min). NEVER write `jsonb_build_object('field', $N)` without `$N::text` / `$N::int` / `$N::uuid` etc. Same class as the `auth.py + execute_with_retry` ::text rule. Verified backend callsites: appliance_relocation_api.py + fleet_cli.py already cast — single-callsite fix (journal_api) closed the prod leak.
- **`COUNT(*)` on partitioned tables is a hidden timeout class (Session 219).** `prometheus_metrics.py:521` was firing `QueryCanceledError: canceling statement due to statement timeout` 57×/hr on `SELECT COUNT(*) FROM log_entries` (4.2M-row monthly-partitioned table). The exception was caught + logged but masked every other gauge in /api/metrics + burned PgBouncer slots. Fix: use `SUM(reltuples)` from `pg_class WHERE relname LIKE 'log_entries%' AND relkind IN ('r','p')` — Postgres planner-stat approximation, instant. Document the metric as approximate. Class rule: if a Prometheus metric exposes a row-count, use planner-stat (reltuples) UNLESS exactness is required AND the table is small. Sibling pattern: `aggregated_pattern_stats`, `compliance_bundles`, `incidents` are similarly large and any future `SELECT COUNT(*)` on them risks the same timeout.
- **Adversarial 2nd-eye review is MANDATORY via fork — author-written counter-arguments DO NOT count (Session 219, locked in 2026-05-11).** Any new system / deliverable / design doc / endpoint / CLI tool that mutates production state / 24h+ soak / load test / chaos run MUST receive a fork-based 4-lens adversarial review (Steve / Maya / Carol / Coach) BEFORE execution OR completion. The fork runs via `Agent(subagent_type="general-purpose")` with a fresh context window — the author CANNOT play the lenses themselves. Writing a "Steve says X / Maya says Y" section IN the design doc YOURSELF is the antipattern this rule exists to prevent: same author, same context, same blind spots. Caught on Phase 4 substrate-MTTR soak 2026-05-11 where the in-doc counter-arguments were author-written; user replied "qa round table adversarial 2nd eye ran?" — answer was no. The fork's verdict is the gate: APPROVE / APPROVE-WITH-FIXES (address P0 before run) / BLOCK (redesign). APPROVE-as-is is rare; if it returns clean, suspect the fork was under-scoped. Full rule + lock-in scope: `feedback_round_table_at_gates_enterprise.md` §"Lock-in for NEW SYSTEMS / NEW DELIVERABLES (2026-05-11)".
- **TWO-GATE adversarial review + mandatory-implementation of recommendations (Session 219, locked in 2026-05-11 extension).** The fork-based adversarial review runs at TWO gates per deliverable, not one: **Gate A (pre-execution)** before any new system runs / migration applies / soak fires AND **Gate B (pre-completion)** before any commit body says "shipped" / "complete" / task is marked done. Both gates are fork-based, both demand a written verdict at `audit/coach-<topic>-<gate>-YYYY-MM-DD.md`, both name the 4 lenses (Steve/Maya/Carol/Coach). **Findings are NOT advisory:** P0 from EITHER gate MUST be closed before advancing; P1 from EITHER gate MUST be closed OR carried as named TaskCreate followup items in the same commit; "acknowledged / noted / deferred to v2" does NOT satisfy a P0 unless the deferral itself passes a Gate B fork review. Commit body must cite BOTH gate verdicts. Skipping Gate B because "Gate A approved the design and the implementation matches the design" is the most insidious antipattern — design may have been right but the AS-IMPLEMENTED artifact can drift in SQL shape, edge cases, banned words, ungated imports. The Phase 4 v1 BLOCK (2026-05-11) is the canonical worked example: Gate A would have caught all 6 P0s before mig 303 landed in prod, instead the author treated in-doc counter-arguments as Gate A pass and prod was contaminated for ~30min before quarantine. Full mechanics: `feedback_consistency_coach_pre_completion_gate.md` §"TWO-GATE LOCK-IN — BOTH gates mandatory + recommendations are NOT advisory (2026-05-11)".
- **Gate B MUST run the full pre-push test sweep, not just review the diff (Session 220 lock-in 2026-05-11).** Three separate deploy outages this session shipped under diff-scoped Gate B review because the fork only audited what the diff TOUCHED, not what was MISSING that should have been added. L1 Phase 1 (`39c31ade`) shipped without `substrate_runbooks/l1_resolution_without_remediation_step.md` — `test_substrate_docs_present` failed at CI. Zero-auth Commit 1 (`94339410`) missed the Go daemon's `dangerousOrderTypes` 4th list + `test_site_id_enforcement.test_enforce_function_exists` regression. Zero-auth Commit 2 (`eea92d6c`) introduced a soft-delete ratchet regression (83→84) caught only by running the broader sweep. Class rule: every Gate B fork MUST execute the curated source-level sweep (`tests/test_pre_push_ci_parity.py` SOURCE_LEVEL_TESTS array or `bash .githooks/full-test-sweep.sh`) and cite the pass/fail count in the verdict. Diff-only review = automatic BLOCK pending sweep verification.
- **Privileged-chain trigger functions are ADDITIVE-ONLY (Session 220 lock-in 2026-05-11).** Gate B v1 caught me silently weakening `enforce_privileged_order_attestation` when adding `delegate_signing_key` to v_privileged_types: my mig 305 first draft rewrote the function body from scratch, dropping the `parameters->>'site_id'` cross-bundle check + `PRIVILEGED_CHAIN_VIOLATION` error prefix + `USING HINT` clause. The lockstep checker (`scripts/check_privileged_chain_lockstep.py`) proves LIST parity but NOT body parity. NEVER rewrite `enforce_privileged_order_attestation` or `enforce_privileged_order_immutability` body from scratch when extending v_privileged_types — copy the prior migration's function body VERBATIM and append only the new array entry. Both functions need the new entry in lockstep (the immutability function's `v_was_privileged <> v_is_privileged` check protects against UPDATE-spoof into the privileged set). Pinned by task #111 (planned CI gate to diff function bodies across migrations).
- **L1 escalate-action false-heal class — daemon hardcoded "L1" + missing success key (Session 220 commits `3f0e5104` daemon + `3b2b8480` backend).** 9 builtin Go rules in `appliance/internal/healing/builtin_rules.go` use `Action: "escalate"`. Pre-fix the daemon's escalate handler (`healing_executor.go:92`) returned `{"escalated": true, "reason": ...}` with NO `"success"` key. `l1_engine.go:328` defaulted `result.Success = true`. `daemon.go:1706` hardcoded `"L1"` in `ReportHealed`. Backend `main.py:4870` persisted daemon-supplied tier without server-side check. Net effect: **1,137 prod L1-orphans** across 3 chaos-lab check_types (rogue_scheduled_tasks 510, net_unexpected_ports 404, net_host_reachability 223) over 90 days. **Two-layer fix shipped**: Layer 1 daemon (`healing_executor.go:106-110` explicit `success: false` on escalate + `l1_engine.go:335-350` fail-closed defaults on BOTH `missing-key` AND `output==nil` paths); Layer 2 backend (`main.py:4870` downgrades `resolution_tier='L1' → 'monitoring'` when `check_type in MONITORING_ONLY_CHECKS`). Substrate invariant `l1_resolution_without_remediation_step` (sev2) detects regressions. Go AST ratchet `appliance/internal/daemon/action_executor_success_key_test.go` enumerates every case in `makeActionExecutor` switch + requires explicit `"success":` key OR trusted helper. Commit-order rule: backend Layer 2 ships FIRST (live in ~5min) as safety net for the asynchronous daemon fleet-update window (hours/days). Mig 306 backfill (1,137 rows L1→L3/monitoring per class) requires its OWN Gate A — Maya §164.528 retroactive PDF impact deep-dive (task #117).
- **Substrate per-assertion `admin_transaction` cascade-fail closure (Session 220 commit `57960d4b`).** Pre-fix the Substrate Integrity Engine held ONE `admin_connection` for all 60+ assertions per 60s tick. One `asyncpg.InterfaceError` poisoned the conn — every subsequent assertion in the tick blinded. Defensive `conn_dead` flag from commit `b55846cb` was a band-aid masking the cascade-fail class. Fix: per-assertion `admin_transaction(pool)` blocks at `assertions.py::run_assertions_once`. One InterfaceError costs 1 assertion (1.6% tick fidelity), not all 60+ (100%). `_ttl_sweep` moves to its OWN independent `admin_transaction` block — removed the `if errors == 0` short-circuit that silently dropped sigauth reclaim on any tick with even one transient error. `conn_dead` band-aid deleted. CI gate `tests/test_assertions_loop_uses_admin_transaction.py` (5 tests) pins the design. Runtime verified post-deploy: 5 consecutive ticks logged `errors=0 sigauth_swept=3`.
- **`delegate_signing_key` privileged-chain registration (Session 220 mig 305 + commit `4b9b6d35`).** Weekly audit cadence found `appliance_delegation.py:258 POST /delegate-key` was zero-auth — anyone could mint an Ed25519 signing key bound to any caller-supplied appliance_id, then sign evidence/audit-trail entries against the customer-facing attestation chain. Functionally equivalent to `signing_key_rotation` which was already privileged. Added to all 3 lockstep lists: `fleet_cli.PRIVILEGED_ORDER_TYPES`, `privileged_access_attestation.ALLOWED_EVENTS`, mig 305 `v_privileged_types`. Plus Python-only allowlist entry in `tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY` (backend-only — daemon never receives it as a fleet_order). Prod audit at fix time: 1 historical row in `delegated_keys`, synthetic test data, already expired — zero customer exposure.
- **BAA enforcement 3-list lockstep — every new CE-mutating endpoint MUST be gated or registered as deferred (Session 220 #52 + #91 + #92, 2026-05-15).** Counsel Rule 6 machine-enforcement. The triad: List 1 = `baa_enforcement.BAA_GATED_WORKFLOWS` (active: `owner_transfer`, `cross_org_relocate`, `evidence_export`); List 2 = enforcing callsites — `require_active_baa(workflow)` factory for the client-owner context, `enforce_or_log_admin_bypass(...)` for the admin carve-out path (logs `baa_enforcement_bypass` to `admin_audit_log`, never blocks), `check_baa_for_evidence_export(_auth, site_id)` for the method-aware auditor-kit branches; List 3 = `sensitive_workflow_advanced_without_baa` sev1 substrate invariant (`assertions.py`), scans state-machine tables + `admin_audit_log auditor_kit_download` rows in last 30d, excludes admin + legacy `?token=` carve-outs via `details->>'auth_method' IN ('client_portal','partner_portal')`. CI gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔ List 2. `auditor_kit_download` audit rows denormalize `site_id` + `client_org_id` at write time (evidence_chain.py) so the invariant SQL skips the JOIN. Predicate `baa_status.baa_enforcement_ok()` is DELIBERATELY SEPARATE from `is_baa_on_file_verified()` — does NOT require `client_orgs.baa_on_file=TRUE` (demo posture is FALSE everywhere; reusing it would block every org on deploy). `baa_version` comparison is numeric (`_parse_baa_version` tuple) NOT lexical — v10.0 > v2.0 holds. `_DEFERRED_WORKFLOWS` (intentionally NOT gated): `partner_admin_transfer` — partner-internal role swap, doesn't touch CE state (Task #90 Gate A 2026-05-15 confirmed via grep + Counsel §164.504(e) test: zero PHI flow; gating it would add zero safety + §164.524 access-right downside; the new admin's subsequent BAA-gated actions are already gated at the 5 active gates). `ingest` — Exhibit C "pending inside-counsel verdict" (Task #37 counsel queue). Cliff 2026-06-12; all 5 active workflows now have both build-time (lockstep CI gate) + runtime (substrate invariant scan) coverage post-#98.
- **Master BAA v2.0 drafting has engineering-evidence preconditions (Session 220 #70).** Before drafting/editing ANY `MASTER_BAA_v2.0` article or exhibit, consult `docs/legal/v2.0-hardening-prerequisites.md`. PRE-1: no per-event/per-heartbeat/"continuously verified" verification language unless D1 heartbeat-signature backend verification has a ≥7-day clean soak (≥99% `signature_valid IS TRUE` per pubkeyed appliance, zero open `daemon_heartbeat_signature_{unverified,invalid,unsigned}` violations). v1.0-INTERIM does NOT over-claim (Task #70 Gate A) — every signed-claim scopes to evidence bundles. CI backstop `tests/test_baa_artifacts_no_heartbeat_verification_overclaim.py` (baseline 0) pins that scoping. Extend the prerequisites doc with a new `PRE-N` whenever a v2.0 article would assert a not-yet-proven capability.
- **Schema fixtures regenerate together — never hand-edit one (Session 220 #77 commit `ad2f3281`).** `tests/fixtures/schema/` holds prod-extracted schema sidecars: `prod_columns.json` (`{table:[col]}`), `prod_column_types.json` (`{table:{col:data_type}}`, NEW), `prod_column_widths.json` (`{table:{col:width}}`), `prod_unique_indexes.json`. `prod_columns.json` is DERIVED from the typed pull — both regenerate via ONE psql command (documented in `test_sql_columns_match_schema.py` docstring) so they cannot drift; pinned by `test_schema_fixture_parity.py` (identical table + per-table column sets). New per-column schema facets follow the SIDECAR convention — do NOT augment `prod_columns.json`'s shape. The cast-vs-column-type CI gate `test_no_param_cast_against_mismatched_column.py` reads the typed sidecar and bans `col = $N::TYPE` casts whose type family ≠ the column's prod type (the 2026-05-13 `appliance_id = $1::uuid` outage class); it skips multi-class-ambiguous columns, so `test_no_uuid_cast_on_text_column.py` (Phase A, hardcoded 6-column `::uuid` pin) STAYS as the floor for `appliance_id`/`site_id` which are `{uuid,text}` in the full schema. Refreshing the fixtures can surface latent column-drift bugs — that is the gate working, not a regression.
