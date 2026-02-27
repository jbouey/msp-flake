# Skills Docs

The compressed knowledge index lives in **CLAUDE.md** (always in context).
Full docs below are retrieved on-demand when working in a specific area.

## Doc Map

```
docs/security/security.md     — Auth, bcrypt, PKCE, rate limiting, secrets rotation
docs/testing/testing.md        — pytest, async tests, fixtures, mocking
docs/hipaa/compliance.md       — Drift checks, evidence bundles, L1 rules, PHI scrub
docs/backend/backend.md        — FastAPI, 3-tier healing, gRPC, order signing
docs/database/database.md      — asyncpg, SQLite WAL, migrations, multi-tenant
docs/nixos/infrastructure.md   — Flake structure, partitions, systemd, nftables, ISO
docs/nixos/advanced.md         — Module system, sops-nix, impermanence, deploy-rs, disko
docs/golang/golang.md          — Concurrency, pgx, slog, testing, security, production
docs/frontend/frontend.md      — React Query, hooks, Tailwind, routing
docs/api/api.md                — REST endpoints, gRPC proto, OAuth flow
docs/performance/performance.md — Pool sizing, React.memo, virtual scroll, batch upload
```

## Type Imports

```python
from compliance_agent._types import (
    Incident, EvidenceBundle, ComplianceCheck,
    CheckStatus, Severity, CheckType,
    now_utc  # NOT datetime.utcnow()
)
```
