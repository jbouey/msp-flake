# OsirisCare — Strategy Briefing Pack
**Date:** 2026-02-09 | **Session:** 104 | **Agent:** v1.0.57 | **Tests:** 963 passing

---

## What OsirisCare Is

HIPAA compliance attestation substrate for healthcare SMBs. NixOS + MCP + LLM.
Drift detection, evidence-grade observability, operator-authorized remediation. **75% lower cost than traditional MSPs.**

**Positioning:** Evidence-grade compliance attestation substrate. Provides observability, drift detection, evidence capture, and human-authorized remediation workflows. Not a coercive enforcement platform — remediation occurs only via operator-configured rules or human-escalated decisions.

**Target:** 1-50 provider practices in NEPA region
**Pricing:** $200-3000/mo per practice

---

## Architecture at a Glance

```
Client Site                    Cloud (Hetzner VPS)
┌─────────────────┐           ┌──────────────────────────────┐
│ NixOS Appliance │──HTTPS──▶│  Central Command             │
│ (HP T640 thin)  │  60s poll │  api.osiriscare.net          │
│                 │           │  dashboard.osiriscare.net     │
│ Python Agent    │           │                              │
│ - 6 HIPAA checks│           │  FastAPI + PostgreSQL        │
│ - L1/L2/L3 heal│           │  Redis + MinIO (WORM)        │
│ - Ed25519 sign  │           │  Caddy (auto TLS)            │
│ - WinRM → DCs   │           └──────────────────────────────┘
│                 │
│ Go Agents ◀─────── Windows Workstations (gRPC push)
│ (6 WMI checks)  │
└─────────────────┘
```

### Key Differentiators
1. **Evidence-by-architecture** — MCP audit trail inseparable from operations
2. **Deterministic builds** — NixOS flakes = cryptographic proof of configuration
3. **Metadata-only monitoring** — zero PHI processed, stored, or transmitted
4. **Ed25519 signed evidence** — tamper-evident bundles with hash chains
5. **Bitcoin timestamping** — OpenTimestamps proofs for blockchain-anchored evidence
6. **Three-tier auto-healing** — 70-80% resolved instantly at $0 cost

---

## Three-Tier Auto-Healing

```
Incident → L1 Deterministic (70-80%, <100ms, $0)
         → L2 LLM Planner   (15-20%, 2-5s, ~$0.001)
         → L3 Human Escalation (5-10%)
         → Data Flywheel (promotes L2→L1 automatically)
```

- **L1:** 21 YAML rules covering firewall, defender, services, patches, BitLocker, etc.
- **L2:** GPT-4o generates Python code in RestrictedPython sandbox (98% token reduction vs tool-calling)
- **L3:** Rich escalation emails with HIPAA context, SLA tracking, partner notification routing
- **Flywheel:** Patterns promoted after 5+ occurrences with 90%+ success rate

---

## What's Built and Running (Production)

### Infrastructure
| Component | Status | Details |
|-----------|--------|---------|
| VPS | Live | 178.156.162.116 (Hetzner), Caddy auto-TLS |
| Dashboard | Live | dashboard.osiriscare.net (React + Vite) |
| API | Live | api.osiriscare.net (FastAPI) |
| PostgreSQL | Live | 26 migrations, all data models |
| Redis | Live | Cache + session store |
| MinIO WORM | Live | evidence-worm-v2 bucket, 90-day object lock |
| CI/CD | Live | GitHub Actions auto-deploys on push to main |

### Appliances
| Site | Type | IP | Status |
|------|------|-----|--------|
| North Valley Dental (pilot) | HP T640 Physical | 192.168.88.246 | Online |
| Main Street Medical (test) | VirtualBox VM | 192.168.88.247 | Online |

### Evidence Pipeline
- **182,685 bundles** in DB (111K physical, 71K test)
- All Feb bundles **Ed25519 signed** (2,408 signed)
- Hash chain **111K+ positions deep**
- **82,861 Bitcoin-anchored** via OpenTimestamps (2,214 pending, 0 expired)
- WORM uploads active (verified latest bundle in MinIO)
- Old evidence-worm bucket removed; only evidence-worm-v2 active
- Compliance packets: Jan 28.3%, Feb 52.1% (improving as checks accumulate)

### Agent Capabilities
- **Python agent v1.0.57** — 963 tests passing
- **Go agent** — 6 WMI checks, SQLite offline queue, RMM detection, 24 tests
- **43 runbooks** (27 Windows + 16 Linux)
- **12 PowerShell sensor checks** (push-based, <30s detection)
- **Fleet update system** — overlay hotpatching + NixOS rebuild with A/B rollback

### Frontend (React + TypeScript + Vite)
- **31 page components**, 51+ custom hooks
- **4 portals:** Admin, Partner, Client, Public Site Portal
- **iOS glassmorphism** design system with OsirisCare teal branding
- **Code-split** with React.lazy (67% bundle reduction)
- OAuth (Google + Microsoft), magic link auth, bcrypt-12, PKCE

---

## Portal Structure

### Admin Portal (dashboard.osiriscare.net)
- Fleet dashboard with real-time WebSocket events
- Per-site detail with compliance checks, evidence, Go agents
- Runbook config, framework config, integration setup
- User management with invite system
- Learning loop dashboard (L2→L1 promotion tracking)
- Notification settings, audit logs, fleet updates
- Command bar (Cmd+K)

### Partner Portal (/partner/*)
- White-label dashboard for MSP resellers
- QR code provisioning for new sites
- Billing, compliance framework management
- Exception management with IDOR-protected CRUD
- L3 escalation routing (Slack, PagerDuty, Teams, Email, Webhook)

### Client Portal (/client/*)
- Magic-link passwordless auth (cookie-based)
- Compliance score dashboard with KPIs
- Evidence archive with download
- Monthly/annual report PDFs
- Help documentation with visual walkthroughs
- Account settings + provider transfer

### Public Site Portal (/portal/site/:siteId/*)
- Token-based access (no account needed)
- KPI cards, control grid, incident history
- Evidence downloads with chain verification
- HIPAA control explanations

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Infrastructure | NixOS Flakes (deterministic, auditable) |
| Agent | Python 3.13 (compliance checks, healing, evidence) |
| Go Agent | Go (workstation WMI checks, gRPC push) |
| Backend | FastAPI (REST API, phone-home, OAuth) |
| Frontend | React 18 + TypeScript + Vite 5 + TanStack Query v5 |
| Database | PostgreSQL 16 (26 migrations) |
| Cache | Redis (sessions, rate limiting) |
| Evidence Storage | MinIO S3 (WORM, 90-day object lock) |
| Signing | Ed25519 (evidence bundles) |
| Timestamping | OpenTimestamps (Bitcoin blockchain) |
| Reverse Proxy | Caddy (auto TLS via Let's Encrypt) |
| Hosting | Hetzner VPS + Storage Box |
| CI/CD | GitHub Actions (auto-deploy on push) |

---

## Recent Work (Sessions 99-103)

### Session 99-102: Production Hardening
- Flap detector to prevent firewall drift circular loops
- Domain discovery deduplication
- L3 escalation vs healing failure distinction
- PHI scrubber skip for infrastructure queries
- Zero-friction AD workstation auto-enrollment

### Session 103: OsirisCare Brand Overhaul
- Replaced all "Malachor" references with "OsirisCare"
- Replaced "Central Command" with "OsirisCare / Compliance Dashboard"
- Purged all grey hover states → brand-tinted alternatives (blue/indigo/teal per portal)
- Changed fill design tokens from grey to blue-tinted globally
- Created OsirisCareLeaf SVG component (two overlapping teal leaves)
- Replaced generic shield icon with leaf logo across 8 files
- Changed admin brand gradient from blue-purple to teal (#14A89E → #3CBCB4)
- Added leaf favicon
- 5 commits pushed and deployed

### Session 104: Polish, Security Hardening, Ops Cleanup (Today)
- **Grey purge complete** — replaced 1,030 `gray-*` → `slate-*` (blue-tinted) across 34 files, all portals
- **SetPassword page** — fixed stale purple gradient → teal to match brand
- **Portal visual verification** — client (teal), partner (indigo), admin (teal) all confirmed correct
- **Mobile responsive check** — login pages render correctly; admin sidebar needs hamburger menu (future)
- **Credential rotation (VPS-side)** — rotated all site API keys, provisioning keys, and admin dashboard password
- **OTS proof resubmission** — all 1,289 expired proofs resubmitted (0 failures, 0 remaining)
- **WORM bucket cleanup** — old evidence-worm bucket removed (was already empty); only evidence-worm-v2 active
- 2 commits pushed and deployed

---

## Security Posture

- **Ed25519 evidence signing** with cryptographic verification
- **bcrypt-12** password hashing (mandatory)
- **PKCE** OAuth flow (Google + Microsoft)
- **HTTP-only secure cookies** with Redis session store
- **CSRF protection** on all mutation endpoints
- **Fernet encryption** for OAuth tokens at rest
- **IDOR protection** on all partner/client endpoints
- **PHI scrubbing** at collection point (10 regex patterns)
- **Pull-only architecture** — no listening sockets on appliance
- **WORM storage** — 90-day compliance retention on evidence
- **SQL injection fix** applied (parameterized queries)
- **3 critical auth fixes** from security audit (Session 65)
- **Credential rotation** — all VPS-side keys rotated (site API keys, provisioning keys, admin password)

---

## Business Model

### Pricing Tiers (per site/month)
- **Essential ($200-500):** Compliance monitoring + evidence capture
- **Professional ($500-1500):** + Auto-healing + evidence chain + reports
- **Enterprise ($1500-3000):** + Full coverage + partner white-label + SLA

### Revenue Streams
1. Direct client subscriptions (practice pays OsirisCare directly)
2. Partner/reseller channel (MSPs white-label and resell)
3. Evidence packets for auditors (premium report generation)

### Cost Structure
- Hetzner VPS: ~$20/mo
- Hetzner Storage Box: ~$4/mo (1TB WORM)
- LLM API (L2): ~$0.001/incident (most handled at L1 for $0)
- Email (SMTP): ~$5/mo
- Domain: ~$12/yr

### Go-to-Market
- Target: NEPA region healthcare practices (1-50 providers)
- Deploy HP T640 thin client (~$50 used) as appliance at each site
- Appliance auto-provisions via QR code scan
- Partner MSPs can white-label and provision clients

---

## What's Working End-to-End

1. Appliance boots → auto-provisions via MAC lookup
2. Agent polls Central Command every 60s
3. Credential-pull architecture (no creds cached on disk)
4. 6 HIPAA drift checks run continuously
5. L1 rules match 70-80% of incidents → auto-heal in <100ms
6. L2 LLM handles complex cases → generates Python fix code
7. L3 escalates with rich context → email + partner routing
8. Evidence bundles signed Ed25519 → uploaded to WORM MinIO
9. Hash chain links bundles → tamper-evident audit trail
10. OpenTimestamps anchors to Bitcoin → third-party timestamp proof
11. Compliance packets generated → auditor-ready reports
12. Learning flywheel promotes L2 patterns → L1 rules (22 promoted)
13. Fleet updates push agent code → overlay hotpatch or NixOS rebuild
14. Dashboard shows real-time fleet health → WebSocket push

---

## Open Tasks / Known Issues

| Priority | Task | Status |
|----------|------|--------|
| ~~High~~ | ~~Rotate leaked credentials (VPS-side)~~ | **Done** (Session 104) |
| ~~Medium~~ | ~~Re-submit expired OTS proofs~~ | **Done** — 1,289 resubmitted, 0 failures |
| ~~Medium~~ | ~~Old evidence-worm bucket cleanup~~ | **Done** — bucket removed |
| High | Rotate Windows domain passwords (lab network access required) | Pending |
| High | Purge git history of leaked credentials (`git-filter-repo`) | Pending |
| Medium | Admin sidebar mobile hamburger menu | Pending |
| Low | Remove argparse bootstrap shim after nix rebuild | Pending |
| Low | Server-side credential version tracking (migration 036) | Pending |

---

## Pending Decisions

### PDR-001: Approval Workflow for Disruptive Actions
How should disruptive actions (patching, BitLocker) be approved?
- Option 1: Require explicit human approval via Web UI
- Option 2: Auto-approve during maintenance window
- Option 3: Require approval only for first occurrence
- Option 4: Tiered by subscription level

### PDR-003: Local LLM Fallback
Should L2 fall back to local LLM when API unavailable?
- Currently escalates to L3 if API down
- Local Llama option scaffolded but not deployed

---

## Immediate Priorities (Next Sessions)

1. ~~**Client portal interior verification**~~ — **Done** (teal gradients correct)
2. ~~**Partner portal interior verification**~~ — **Done** (indigo theme consistent)
3. ~~**Remaining semantic greys**~~ — **Done** (1,030 gray→slate across 34 files)
4. ~~**Mobile responsive pass**~~ — **Done** (login pages good; admin sidebar hamburger menu noted as future work)
5. **Rotate Windows domain passwords** — needs lab network access (NorthValley2024! on NVDC01, NVWS01, service accounts)
6. **Purge git history** — remove leaked credentials from all commits (`git-filter-repo`)
7. **Admin sidebar mobile collapse** — add hamburger menu for mobile viewports
8. **Partner onboarding polish** — first-run experience for new MSP partners
9. **First paying client** — complete pilot with North Valley Dental
10. **30-day monitoring period** — continuous evidence capture before first invoice

---

## Codebase Scale

| Area | Metric |
|------|--------|
| Python agent | ~15,000 LOC |
| Python tests | 963 tests passing |
| Go agent | ~1,030 LOC + 24 tests |
| Frontend (React/TS) | ~63,000 LOC across 31 pages |
| Backend (FastAPI) | ~5,000 LOC (main.py + modules) |
| NixOS modules | ~2,000 LOC |
| Database | 26 SQL migrations |
| Total commits | 100+ on main branch |
| Session logs | 104 development sessions |

---

## Lab Environment

```
192.168.88.0/24 — North Valley Lab Network
├── .1    Gateway/Router (MikroTik)
├── .50   iMac (VirtualBox host, SSH gateway)
├── .244  NVSRV01 (Windows Server 2022, domain member)
├── .246  OsirisCare Appliance (HP T640 Physical, pilot)
├── .247  OsirisCare Appliance VM (VirtualBox, test)
├── .250  NVDC01 (Windows Server 2019 DC)
└── .251  NVWS01 (Windows 10 Workstation)

AD Domain: northvalley.local
Service Account: NORTHVALLEY\svc.monitoring
```

---

## Key Architecture Decisions

1. **Pull-only agent** — no inbound connections, works behind NAT
2. **Three-tier healing** — L1 deterministic ($0) → L2 LLM ($0.001) → L3 human
3. **L2 code mode** — LLM writes Python code, not tool-calling (98% token reduction)
4. **Evidence by reference** — raw data stored locally, only summaries flow through LLM
5. **PHI scrubbing at collection** — 10 regex patterns, never reaches central systems
6. **Golden Flake deployment** — installer ISO, not dd disk images
7. **Credential-pull** — fresh creds fetched every 60s, never cached on disk

---

*Updated 2026-02-09 (Session 104) for Claude.ai project knowledge upload*
