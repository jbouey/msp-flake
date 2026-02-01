# Skills Knowledge Index

Compressed pattern reference. For full details, read the linked doc.

## Quick Lookup (pipe-delimited)

```
AREA | KEY PATTERN | FILE | CRITICAL SNIPPET
-----|-------------|------|------------------
auth | bcrypt 12-round | docs/security/security.md | secrets.token_urlsafe(32)
auth | PKCE flow | docs/security/security.md | hashlib.sha256(verifier).digest()
auth | session cookie | docs/api/api.md | httponly=True,secure=True,samesite="lax"
auth | rate limit | docs/security/security.md | 5 fail → 15min lockout
secrets | SOPS/age | docs/nixos/infrastructure.md | sopsFile + ageKeyFile
secrets | rotation | docs/security/security.md | 60-day age warning
-----|-------------|------|------------------
test | async decorator | docs/testing/testing.md | @pytest.mark.asyncio
test | fixture | docs/testing/testing.md | @pytest.fixture + tmp_path
test | mock cmd | docs/testing/testing.md | AsyncMock(stdout="...")
test | 839 tests | docs/testing/testing.md | python -m pytest tests/ -v
-----|-------------|------|------------------
hipaa | 6 drift checks | docs/hipaa/compliance.md | patching,backup,firewall,logging,av,encryption
hipaa | evidence bundle | docs/hipaa/compliance.md | EvidenceBundle dataclass
hipaa | L1 rule | docs/hipaa/compliance.md | conditions→action→runbook_id
hipaa | PHI scrub | docs/hipaa/compliance.md | 12 patterns, hash_redacted=True
hipaa | control map | docs/hipaa/compliance.md | 164.308/310/312 sections
-----|-------------|------|------------------
backend | 3-tier healing | docs/backend/backend.md | L1(70%)→L2(20%)→L3(10%)
backend | FastAPI router | docs/backend/backend.md | APIRouter(prefix="/api/x")
backend | Depends auth | docs/backend/backend.md | user: Dict = Depends(require_auth)
backend | asyncpg pool | docs/backend/backend.md | create_pool(min=2,max=10)
backend | gRPC stream | docs/backend/backend.md | ReportDrift(stream DriftEvent)
-----|-------------|------|------------------
db | postgres pool | docs/database/database.md | asyncpg.create_pool()
db | sqlite WAL | docs/database/database.md | PRAGMA journal_mode=WAL
db | 26 migrations | docs/database/database.md | migrations/001_*.sql
db | multi-tenant | docs/database/database.md | WHERE site_id = $1
db | upsert | docs/database/database.md | ON CONFLICT DO UPDATE
-----|-------------|------|------------------
nixos | module pattern | docs/nixos/infrastructure.md | options + config = mkIf
nixos | A/B partition | docs/nixos/infrastructure.md | /dev/sda2(A) + /dev/sda3(B)
nixos | health gate | docs/nixos/infrastructure.md | max 3 boot attempts
nixos | systemd harden | docs/nixos/infrastructure.md | ProtectSystem=strict
nixos | nftables | docs/nixos/infrastructure.md | pull-only firewall
nixos | ISO build | docs/nixos/infrastructure.md | nix build .#appliance-iso
-----|-------------|------|------------------
frontend | React Query | docs/frontend/frontend.md | useQuery({queryKey,queryFn})
frontend | mutation | docs/frontend/frontend.md | useMutation + invalidateQueries
frontend | 51 hooks | docs/frontend/frontend.md | src/hooks/
frontend | api.ts | docs/frontend/frontend.md | fetchApi<T> with Bearer
frontend | glass card | docs/frontend/frontend.md | bg-white/5 backdrop-blur
frontend | routing | docs/frontend/frontend.md | /client/* /partner/* /portal/*
-----|-------------|------|------------------
api | REST base | docs/api/api.md | /api prefix, Bearer auth
api | gRPC proto | docs/api/api.md | ComplianceAgent service
api | error codes | docs/api/api.md | 400/401/403/404/429/500
api | OAuth flow | docs/api/api.md | auth_url→callback→tokens
api | TS client | docs/api/api.md | sitesApi.{list,get,create}
-----|-------------|------|------------------
perf | pool sizing | docs/performance/performance.md | min=2,max=10
perf | filtered idx | docs/performance/performance.md | WHERE status='pending'
perf | asyncio.gather | docs/performance/performance.md | 6x faster checks
perf | React.memo | docs/performance/performance.md | 3-5x fewer renders
perf | useMemo | docs/performance/performance.md | expensive computations
perf | virtual scroll | docs/performance/performance.md | @tanstack/react-virtual
perf | batch upload | docs/performance/performance.md | gzip + batch endpoint
```

## Type Imports

```python
# compliance-agent type system
from compliance_agent._types import (
    Incident, EvidenceBundle, ComplianceCheck,
    CheckStatus, Severity, CheckType,
    now_utc  # NOT datetime.utcnow()
)
```

## Common Commands

```bash
# Agent work
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# Build ISO
nix build .#appliance-iso

# VPS deploy
scp file.py root@178.156.162.116:/opt/mcp-server/dashboard_api_mount/
ssh root@178.156.162.116 "docker restart mcp-server"

# Health check
curl https://api.osiriscare.net/health
```

## Doc Retrieval

When working on a specific area, READ the full doc:
- Security/Auth → `.claude/skills/docs/security/security.md`
- Tests → `.claude/skills/docs/testing/testing.md`
- HIPAA/Evidence → `.claude/skills/docs/hipaa/compliance.md`
- Backend/Healing → `.claude/skills/docs/backend/backend.md`
- Database → `.claude/skills/docs/database/database.md`
- NixOS/Docker → `.claude/skills/docs/nixos/infrastructure.md`
- React/Hooks → `.claude/skills/docs/frontend/frontend.md`
- REST/gRPC → `.claude/skills/docs/api/api.md`
- Performance → `.claude/skills/docs/performance/performance.md`
