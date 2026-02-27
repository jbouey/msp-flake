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
packages/compliance-agent/   # Python agent (main work area)
  src/compliance_agent/      # Core modules
  tests/                     # pytest tests (1037+ passing)
  venv/                      # Python 3.13 virtualenv
modules/                     # NixOS modules
mcp-server/central-command/  # Dashboard backend + frontend
docs/                        # Detailed reference docs
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
| VM Appliance | 192.168.88.254 | root (SSH key, DHCP) |
| VPS | 178.156.162.116 | root (SSH key) |

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
