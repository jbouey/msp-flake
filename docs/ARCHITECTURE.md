# MSP Platform Architecture

**Last Updated:** 2026-05-16 (Session 220 doc refresh)
**Go Daemon:** v0.4.13+ (appliance) with signed-heartbeat verification (mig 313)
**Go Agents on:** NVDC01, NVWS01, iMac
**ISO Version:** v40.x (rescue-CLI + circuit-breaker remediation)
**Python Agent:** Deprecated — Go daemon is production agent

<!-- updated 2026-05-16 — Session-220 doc refresh -->

## Overview

**Stack:** NixOS + MCP + LLM
**Target:** Small to mid-sized clinics (NEPA region)
**Service Model:** HIPAA compliance **attestation substrate** — observability, drift detection, evidence capture, operator-authorized remediation. **Not** a coercive enforcement platform.

**Governance authority:** Counsel's 7 Hard Rules (2026-05-13, gold authority) are the first-pass filter on every design, Gate A review, and commit. See CLAUDE.md "Counsel's 7 Hard Rules" section. They override prior internal heuristics where they conflict.

## Production Infrastructure

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   Clients     │       │  Central        │       │   Operators     │
│  (Appliances) │       │  Command        │       │  (Dashboard)    │
└───────┬───────┘       │  178.156.162.116│       └────────┬────────┘
        │               └────────┬────────┘                │
        │                        │                         │
        │    ┌───────────────────┼───────────────────┐     │
        │    │                   │                   │     │
        ▼    ▼                   ▼                   ▼     ▼
┌─────────────────────────────────────────────────────────────┐
│                    Caddy Reverse Proxy                      │
│                    (Auto TLS via Let's Encrypt)             │
└─────────────────────────────────────────────────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│ api.osiris    │       │ dashboard.    │       │ msp.osiris    │
│ care.net      │       │ osiriscare.net│       │ care.net      │
│ :8000         │       │ :3000         │       │ :3000         │
└───────────────┘       └───────────────┘       └───────────────┘
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│  PostgreSQL   │       │    Redis      │       │    MinIO      │
│  :5432        │       │    :6379      │       │  :9000-9001   │
│  + PgBouncer  │       │    (Cache)    │       │  (Evidence)   │
└───────────────┘       └───────────────┘       └───────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  Vault Transit (shadow mode)                                │
│  Hetzner 89.167.76.203 / WG 10.100.0.3                      │
│  Ed25519 non-exportable; 1Password owns unseal shares       │
│  Dual-write byte-identical signatures; hot-cutover pending  │
└─────────────────────────────────────────────────────────────┘
```

### Production Endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| API | https://api.osiriscare.net | MCP API, phone-home, sites |
| Dashboard | https://dashboard.osiriscare.net | Central Command UI |
| Alternate | https://msp.osiriscare.net | Dashboard alias |
| Public verify | https://api.osiriscare.net/verify | Public attestation-letter verify (F4) |

### Appliance Phone-Home Flow

```
┌─────────────────┐     Every 60s      ┌─────────────────┐
│  Client Site    │ ──────────────────▶│  Central        │
│  NixOS Appliance│ POST /checkin      │  Command        │
│  (Ed25519 sig)  │ (D1 sig-verified)  └────────┬────────┘
└─────────────────┘                             │
                                       ┌────────▼────────┐
                                       │  Verify sig vs  │
                                       │  per-appliance  │
                                       │  pubkey;        │
                                       │  bump heartbeat,│
                                       │  signal live.   │
                                       └─────────────────┘
```

**D1 heartbeat signature verification (mig 313, Session 220).** Heartbeats are Ed25519-signed at the daemon and verified server-side. The substrate invariants `daemon_heartbeat_unsigned`, `daemon_heartbeat_signature_invalid`, and `daemon_heartbeat_signature_unverified` track per-appliance signature health. `MASTER_BAA_v2.0` per-heartbeat verification language is gated on a ≥7-day clean soak (see `docs/legal/v2.0-hardening-prerequisites.md`).

**Status Calculation:**
- `online`: Last checkin < 5 minutes
- `stale`: Last checkin 5-15 minutes
- `offline`: Last checkin > 15 minutes
- `pending`: Never checked in

`appliance_status_dual_source_drift` invariant catches MV-vs-base divergence (see RT33 P2 fix — portal endpoints query `site_appliances` directly, not `appliance_status_rollup`, because PG materialized views don't inherit base-table RLS).

## Lab Network (North Valley Clinic Test)

```
192.168.88.0/24 - North Valley Lab Network (iMac VirtualBox host)
├── 192.168.88.1   - Gateway/Router (MikroTik)
├── 192.168.88.50  - iMac (VirtualBox host, Go Agent) — jrelly@ (SSH port 2222)
├── 192.168.88.241 - osiriscare-appliance (HP T640 Physical, Go Daemon)
├── 192.168.88.250 - NVDC01 (Windows Server 2019 DC, Go Agent) — 6GB+ RAM required
└── 192.168.88.251 - NVWS01 (Windows 10 Workstation, Go Agent) — 6GB+ RAM required
```

**Deprecated:** VM appliance (.254) decommissioned Session 183. NVSRV01 (.244) inactive.

### AD Domain

| Property | Value |
|----------|-------|
| Domain FQDN | northvalley.local |
| DNS Server | 192.168.88.250 |
| Service Account | NORTHVALLEY\svc.monitoring |

---

## Go Agent Architecture (Push-First)

Two tiers of Go binaries:
1. **Go Daemon** (`appliance-daemon`) — runs on NixOS appliance, manages site-level scanning, WinRM fallback, fleet orders, healing pipeline, signed heartbeats.
2. **Go Agents** (`osiris-agent`) — lightweight per-host agents that push compliance data directly to Central Command via HTTPS.

```
Go Agents (per-host)                 NixOS Appliance (Go Daemon)
┌─────────────────┐                  ┌─────────────────────────────┐
│  osiris-agent   │    HTTPS         │  appliance-daemon           │
│  (Win/Mac/Linux)│───────────────>  │  - Signed heartbeats        │
├─────────────────┤  Push checks     │  - WinRM fallback scanning  │
│  WMI/Registry   │  to Central      │  - Fleet order execution    │
│  SQLite Queue   │  Command         │  - L1/L2/L3 healing         │
│  RMM Detection  │                  │  - Evidence chain (Ed25519) │
└─────────────────┘                  │  - PHI scrub at egress      │
                                     └──────────────┬──────────────┘
                                                    │ HTTPS
                                                    ▼
                                     ┌─────────────────────────────┐
                                     │  Central Command (PHI-free) │
                                     │  api.osiriscare.net         │
                                     │  + Substrate Integrity Eng. │
                                     │  + canonical metric registry│
                                     └─────────────────────────────┘
```

### Deployed Go Agents

| Host | Agent ID | Platform | Deploy Method |
|------|----------|----------|---------------|
| NVDC01 (.250) | go-NVDC01-c2a2b3d8 | Windows Server 2019 | Fleet order (configure_workstation_agent) |
| NVWS01 (.251) | go-NVWS01-b2c70cd6 | Windows 10 | Fleet order (configure_workstation_agent) |
| iMac (.50) | go-MaCs-iMac.local-9235ea2c | macOS 11.7 | SSH + launchd daemon |

### Go Agent Checks

Daemon side: 25 drift checks (see `~/.claude/skills/windows-server-compliance/SKILL.md`). Workstation Go-agent side:

| Check | Method | Description |
|-------|--------|-------------|
| BitLocker | WMI: Win32_EncryptableVolume | Volume encryption status |
| Defender | WMI: MSFT_MpComputerStatus | Real-time protection, signature age |
| Firewall | Registry: SYSTEM\...\FirewallPolicy | All profiles (Domain/Private/Public) enabled |
| Patches | WMI: Win32_QuickFixEngineering + Registry | Recent updates + pending reboot detection |
| ScreenLock | Registry: Control Panel\Desktop | ScreenSaveActive, Timeout, IsSecure |
| RMM | WMI: Win32_Service | Detect ConnectWise/Datto/Ninja |

**`check_type_registry` is the single source of truth** (Migration 157) for check names, scoring categories, HIPAA controls, display labels, and monitoring-only flags. Go daemon check names are canonical. Hardcoded `CATEGORY_CHECKS` is fallback only.

### Firewall Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 50051 | TCP | Inbound to Appliance | gRPC from Go Agents |
| 8080 | TCP | Inbound to Appliance | Sensor API (PowerShell sensors) |
| 80 | TCP | Inbound to Appliance | Status page |
| 22 | TCP | Inbound to Appliance | SSH (emergency) |

### Current Status (2026-05-16)

- **Daemon:** v0.4.13+, slog structured logging across 15+ files
- **Heartbeat signatures:** Ed25519-signed at daemon, server-verified (D1, mig 313)
- **Push-first scanning:** Daemon skips WinRM for hosts with active Go agents
- **Fleet orders:** Ed25519-signed, `target_appliance_id` in signed payload (FLEET-WIDE table — no site/appliance columns)
- **Privileged orders:** Schema-enforced attestation chain (4 types + `delegate_signing_key` per mig 305)
- **L1 escalate-action fix (Session 220):** daemon now sets explicit `success: false` on escalate; backend downgrades L1→monitoring for monitoring-only checks. Mig 306 backfills 1,137 historical L1-orphans.
- **Substrate Integrity Engine:** ~60 invariants every 60s, per-assertion `admin_transaction` isolation (commit 57960d4b).

---

## Substrate Integrity Engine

<!-- updated 2026-05-16 — Session-220 doc refresh -->

A 60-second background loop in `assertions.py` asserts ~60 named invariants over production state and writes violations to `substrate_violations`. Operator surface: `/admin/substrate-health` panel.

**Per-assertion transaction isolation (Session 220 commit `57960d4b`):** each invariant runs in its own `admin_transaction(pool)` block. One `asyncpg.InterfaceError` costs 1 assertion (1.6% tick fidelity), not the full 60+ assertions (100%) as it did pre-fix. `_ttl_sweep` is independent of the assertion fan-out and runs even when one or more assertions fail. CI gate: `tests/test_assertions_loop_uses_admin_transaction.py`.

### Notable invariants added since 2026-04-15

| Invariant | Sev | Closes |
|-----------|-----|--------|
| `l2_resolution_without_decision_record` | sev2 | Mig 300 — ghost-L2 audit-gap (26 north-valley orphans) |
| `l1_resolution_without_remediation_step` | sev2 | Mig 306 — L1 escalate-false-heal class (1,137 orphans) |
| `daemon_heartbeat_unsigned` / `_signature_invalid` / `_signature_unverified` | sev1 | Mig 313 — D1 heartbeat verification |
| `cross_org_relocate_chain_orphan` | sev1 | RT21 bypass detector (`prior_client_org_id` set w/o relocate row) |
| `cross_org_relocate_baa_receipt_unauthorized` | sev1 | Mig 283 — counsel receipt-signature path |
| `appliance_status_dual_source_drift` | sev2 | RT33 P2 — MV-vs-base divergence |
| `canonical_compliance_score_drift` | sev2 | Counsel Rule 1 — score-source canon |
| `canonical_devices_freshness` | sev2 | Mig 319 — multi-appliance device dedup |
| `synthetic_soak_site_quarantined` (via `synthetic_l2_pps_rows` + mig 304/323) | sev2 | Phase 4 v2 MTTR soak quarantine |
| `sensitive_workflow_advanced_without_baa` | sev1 | Counsel Rule 6 BAA enforcement (Task #52 + #98) |
| `compliance_packets_stalled` / `merkle_batch_stalled` / `evidence_chain_stalled` | sev1 | OTS + chain progress monitors |
| `client_portal_zero_evidence_with_data` | sev1 | RT33 portal RLS coverage |
| `chronic_without_l2_escalation` / `l2_recurrence_partitioning_disclosed` | sev2 | Flywheel recurrence-disclosure |
| `schema_fixture_drift` | sev2 | Schema sidecar regen invariant (Task #77) |
| `substrate_sla_breach` / `substrate_assertions_meta_silent` / `bg_loop_silent` | sev1 | Meta-invariants |

Every invariant has a stub runbook under `backend/substrate_runbooks/<name>.md` (generated; `tests/test_substrate_docs_present.py` enforces 1:1 parity).

---

## Counsel Rule 1: Canonical Metric Registry

<!-- updated 2026-05-16 — Session-220 doc refresh -->

Per Counsel's Hard Rule 1 ("no non-canonical metric leaves the building"), every customer-facing metric declares a canonical source in `backend/canonical_metrics.py` (`CANONICAL_METRICS` dict, Task #50 / #103).

| Metric class | Canonical helper | Surfaces |
|--------------|------------------|----------|
| Per-site compliance score | `compliance_score.compute_compliance_score(conn, site_ids, *, include_incidents=False, window_days=30)` | `/api/client/dashboard`, `/api/client/reports/current`, `/api/client/sites/{id}/compliance-health` |
| Org-roll-up category-weighted score | `compliance_score.compute_category_weighted_overall(...)` (Session 220 #103) | Org dashboard, auditor kit, attestation letter |
| Per-device live compliance | live-compute from `go_agent_checks` + `device_compliance_archive` (Bug 3 Path C+D) | Device drill-down |
| Auditor-kit `kit_version` | pinned `2.1` across 4 surfaces; bump-in-lockstep | Header, chain, pubkeys, identity-chain, iso_ca payloads |

Ad-hoc `passed/total*100` formulas in endpoint code are banned (`tests/test_no_ad_hoc_score_formula_in_endpoints.py`). `canonical_metric_samples` table (mig 314) records sample-point evidence; substrate invariant `canonical_compliance_score_drift` (sev2) catches surfaces that diverge from the helper.

---

## BAA Enforcement Triad (Counsel Rule 6)

<!-- updated 2026-05-16 — Session-220 doc refresh -->

Per Counsel's Hard Rule 6 ("no legal/BAA state may live only in human memory"), every CE-mutating workflow MUST be gated against an active per-org BAA OR be explicitly registered as a `_DEFERRED_WORKFLOWS` carve-out with counsel-traceable justification.

**Triad (Session 220 #52 + #91 + #92, lockstep CI-gated):**

1. **List 1 — `baa_enforcement.BAA_GATED_WORKFLOWS`** (active set):
   - `owner_transfer`
   - `cross_org_relocate`
   - `evidence_export`
   - (planned/deferred) `ingest` — Exhibit C pending inside-counsel verdict (Task #37)
2. **List 2 — enforcing callsites:**
   - `require_active_baa(workflow)` factory for client-owner POST/PATCH context.
   - `enforce_or_log_admin_bypass(...)` for the admin carve-out path (logs `baa_enforcement_bypass` to `admin_audit_log`, never blocks; admin retains §164.524 access right).
   - `check_baa_for_evidence_export(_auth, site_id)` for the method-aware auditor-kit branches.
3. **List 3 — substrate invariant `sensitive_workflow_advanced_without_baa` (sev1):** scans state-machine tables + `admin_audit_log auditor_kit_download` rows in last 30d. Denormalized `site_id` + `client_org_id` on audit rows skips the JOIN. Excludes admin + legacy `?token=` carve-outs via `details->>'auth_method' IN ('client_portal','partner_portal')`.

CI gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔ List 2. `baa_status.baa_enforcement_ok()` is **deliberately separate** from `is_baa_on_file_verified()` (demo posture is FALSE everywhere; reusing would block every org on deploy). `baa_version` comparison is numeric (`_parse_baa_version` tuple) — `v10.0 > v2.0` holds.

**Deferred / explicit carve-outs:** `partner_admin_transfer` (partner-internal role swap, zero PHI flow; Task #90 Gate A 2026-05-15 confirmed via §164.504(e) test).

---

## Repository Structure

```
MSP-PLATFORM/
├── appliance/
│   ├── internal/daemon/       # Core daemon, StateManager, threat_detector
│   ├── internal/phiscrub/     # 14 patterns, 21 tests (egress scrubbing)
│   ├── internal/orders/       # Fleet order processor (22 handlers)
│   ├── internal/evidence/     # Ed25519 evidence bundle signing
│   ├── internal/healing/      # builtin_rules.go (9 escalate-action rules)
│   ├── internal/grpcserver/   # Agent registry, TLS enrollment
│   └── Makefile               # VERSION via internal/daemon.Version ldflag
├── agent/                     # Go per-host agent (osiris-agent)
├── packages/
│   └── compliance-agent/      # Python agent (DEPRECATED)
├── modules/                   # NixOS modules
├── mcp-server/central-command/
│   ├── backend/
│   │   ├── main.py            # FastAPI app
│   │   ├── routes.py / sites.py / agent_api.py
│   │   ├── assertions.py      # Substrate Integrity Engine
│   │   ├── canonical_metrics.py   # Counsel Rule 1 registry
│   │   ├── compliance_score.py    # Canonical score helper
│   │   ├── baa_enforcement.py     # Counsel Rule 6 enforcement
│   │   ├── privileged_access_attestation.py
│   │   ├── auditor_kit_zip_primitives.py  # Deterministic ZIP
│   │   ├── cross_org_site_relocate.py
│   │   ├── client_owner_transfer.py / partner_admin_transfer.py
│   │   ├── migrations/        # 300+ migrations; RESERVED_MIGRATIONS.md ledger
│   │   └── substrate_runbooks/    # 1 stub per invariant
│   └── frontend/              # React + TypeScript + Tailwind
├── iso/                       # Appliance ISO + disk image configs (v40.x)
└── docs/                      # This directory
```

## Migration Ledger (Task #59)

Every new `migrations/NNN_*.sql` must be **pre-claimed** in `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` AND have a line-anchored `<!-- mig-claim:NNN task:#TT -->` marker in the design doc (outside code fences). CI gate `tests/test_migration_number_collision.py` enforces no double-claims, ≤30 active rows, marker↔ledger parity. When the migration ships, REMOVE the ledger row in the same commit. Class introduced after 3-of-6 designs collided on mig numbers in a single Gate A sweep.

## Adversarial-Review Protocol (Two-Gate)

<!-- updated 2026-05-16 — Session-220 doc refresh -->

Every new system / migration / soak / load test / CE-mutating endpoint / class-B packet receives a **fork-based 4-lens adversarial review** (Steve / Maya / Carol / Coach) at BOTH gates:

- **Gate A (pre-execution):** before anything runs / migration applies / soak fires.
- **Gate B (pre-completion):** before any commit body says "shipped" / "complete" / task marked done.

Both gates run via `Agent(subagent_type="general-purpose")` with a fresh context window. Author-written "Steve says X / Maya says Y" sections do NOT count as a fork verdict. Findings are NOT advisory: P0 from either gate MUST be closed before advancing; P1 must be closed or carried as named TaskCreate items in the same commit. **Gate B must run the full pre-push test sweep** (`bash .githooks/full-test-sweep.sh` ~92s) — diff-only review = automatic BLOCK.

Three Session 220 deploy outages (39c31ade, 94339410, eea92d6c) traced directly to diff-scoped Gate B reviews missing things that were not in the diff.

## Partner/Reseller Infrastructure

Partners (MSPs) can white-label and provision their own clients. **Partner mutation role-gating (RT31):** every `/me/*` POST/PUT/PATCH/DELETE on `partners.py` MUST use `require_partner_role("admin")` or `("admin", "tech")` — bare `Depends(require_partner)` is forbidden (billing-role could rotate credentials pre-fix).

| Module | Purpose |
|--------|---------|
| `partners.py` | Partner management, QR code generation |
| `discovery.py` | Network discovery, asset classification |
| `provisioning.py` | Appliance first-boot provisioning |
| `partner_admin_transfer.py` | Partner-admin role state machine (mig 274) |
| `client_owner_transfer.py` | Client-org owner-transfer state machine (mig 273) |
| `cross_org_site_relocate.py` | Cross-org site relocate (mig 281+282+283, BAA-gated) |
| `client_portal.py` | Client portal (RLS org-scoped, mig 278) |

## Role-Based Access Control (RBAC)

| Role | Dashboard | Execute Actions | Manage Users | Audit Logs |
|------|-----------|-----------------|--------------|------------|
| Admin | Full | Full | Yes | Yes |
| Operator | Full | Yes | No | No |
| Readonly | Full | No | No | No |

### User Management API

| Endpoint | Method | Access | Description |
|----------|--------|--------|-------------|
| `/api/users` | GET | Admin | List all users |
| `/api/users/invite` | POST | Admin | Send invite email |
| `/api/users/invites` | GET | Admin | List pending invites |
| `/api/users/{id}` | PUT | Admin | Update user role/status |
| `/api/users/me` | GET | Any | Get current user profile |
| `/api/users/me/password` | POST | Any | Change own password |
| `/api/users/invite/validate/{token}` | GET | Public | Validate invite token |
| `/api/users/invite/accept` | POST | Public | Accept invite + set password |
| `/api/client/users/owner-transfer/*` | POST/PUT | Client owner | Owner transfer state machine (BAA-gated) |
| `/api/partners/me/admin-transfer/*` | POST/PUT | Partner admin | Admin transfer state machine |

## Technical Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Infrastructure | NixOS Flakes | Deterministic, auditable configuration |
| Appliance Daemon | Go 1.22+ | Drift detection, healing, evidence (active agent) |
| Workstation Agent | Go (cross-platform) | Per-host push compliance |
| Communication | MCP Protocol | Structured LLM-to-tool interface |
| LLM | GPT-4o / Claude | L2 incident triage, runbook selection |
| Queue | Redis Streams | Multi-tenant event durability |
| Evidence | WORM S3/MinIO | Tamper-evident storage |
| Signing | Ed25519 (per-appliance) | Cryptographic evidence bundles + heartbeats |
| Time anchor | OpenTimestamps | Bitcoin-anchored OTS proofs on every bundle |
| Secrets | Vault Transit (shadow) | Non-exportable keys; 1Password owns shares |
| Dashboard | React + Vite + Tailwind | Central Command UI |
| API | FastAPI | REST endpoints, phone-home |
| Database | PostgreSQL 16 + PgBouncer | Sites, incidents, evidence metadata |
| Reverse Proxy | Caddy | Auto TLS, HTTPS termination |
| Hosting | Hetzner VPS | Production infrastructure |
| Billing | Stripe (4 products via `lookup_keys`) | Pilot / Essentials / Professional / Enterprise |

## Three-Tier Remediation

```
Incident Flow:
┌─────────────┐
│  Incident   │
└──────┬──────┘
       ▼
┌─────────────────────────────────────────┐
│ L1: Deterministic Rules (YAML)          │
│ • 70-80% of incidents                   │
│ • <100ms response                       │
│ • $0 cost                               │
│ • Synced from /var/lib/msp/rules        │
│   (server-side overrides built-ins)     │
└──────┬──────────────────────────────────┘
       │ No match
       ▼
┌─────────────────────────────────────────┐
│ L2: LLM Planner                         │
│ • 15-20% of incidents                   │
│ • 2-5s response                         │
│ • ~$0.001/call                          │
│ • MUST record l2_decisions row before   │
│   setting resolution_tier='L2'          │
│   (mig 300 gate, Session 219)           │
└──────┬──────────────────────────────────┘
       │ Uncertain/Risky
       ▼
┌─────────────────────────────────────────┐
│ L3: Human Escalation                    │
│ • 5-10% of incidents                    │
│ • Operator alert via _send_operator_    │
│   alert; [ATTESTATION-MISSING] tag if   │
│   chain step failed                     │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Data Flywheel (Spine, Session 206)      │
│ • Event ledger + state machine          │
│ • Tracks L2 patterns                    │
│ • Promotes to L1 (5+ occurrences,       │
│   90%+ success), site-level recurrence  │
│ • promoted_rules unique key:            │
│   (site_id, rule_id) — NOT rule_id      │
└─────────────────────────────────────────┘
```

**L1 escalate-action class (Session 220 commits `3f0e5104` daemon + `3b2b8480` backend):** 9 builtin Go rules with `Action: "escalate"` were pre-fix mis-recorded as L1-success (1,137 prod orphans across 3 chaos-lab check_types). Two-layer fix: daemon now sets explicit `success: false`; backend downgrades `resolution_tier='L1' → 'monitoring'` for monitoring-only checks. Mig 306 backfill requires its own Gate A (Maya §164.528 retroactive impact).

## Cross-Org Site Relocate (RT21)

`cross_org_site_relocate.py` ships behind an **attestation-gated feature flag** (mig 281 + 282) that returns 503 until outside HIPAA counsel signs off on the v2 packet's four §-questions:

(a) §164.504(e) permitted-use scope under both source-org and target-org BAAs.
(b) §164.528 substantive completeness + retrievability of the disclosure accounting (legal test = content + producibility, NOT cryptographic immutability).
(c) Receiving-org BAA scope (likely commercial choke point).
(d) Opaque-mode email defaults (no clinic/org names in subjects/bodies).

Three-actor state machine with pinned `expected_*_email` columns, race-guarded execute, 24h cooling-off CHECK constraint, **dual-admin governance** (mig 282 — `lower(approver) <> lower(proposer)` enforced at DB CHECK). Flag-flip is INTENTIONALLY ABSENT from `ALLOWED_EVENTS` (FK incompatible — flag has no site anchor); audit lives in append-only `feature_flags` table + `admin_audit_log`. Substrate invariant `cross_org_relocate_chain_orphan` (sev1) is the bypass detector.

Mig 283 ships the BAA-receipt-authorization signature. Substrate invariant `cross_org_relocate_baa_receipt_unauthorized` (sev1) closes the unauthorized-receipt class.

## Service Catalog

### In Scope (Infra-Only)

| Layer | Automations |
|-------|-------------|
| OS & services | Restart systemd unit, rotate logs, clear /tmp, renew certs |
| Middleware | Bounce workers, re-index database, clear cache |
| Patching | Apply security updates, reboot off-peak, verify health |
| Network | Flush firewall state, reload BGP, fail over link |
| Observability | Detect pattern, run approved fix, generate evidence |

### Out of Scope

- End-user devices (laptops, printers)
- SaaS & desktop apps (QuickBooks, Outlook)
- Tier-1 ticket triage
- Compliance paperwork (SOC-2 docs, staff training)

## Guardrails & Safety

1. **Validation** — Reject unknown service names; ban `ALTER TABLE fleet_orders DISABLE TRIGGER`.
2. **Rate limit** — 5-min cooldown per host/tool; 3/site/week privileged orders.
3. **Privileged-chain attestation** — 4-element chain enforced at CLI + API + DB trigger (mig 175 + 305).
4. **BAA-gating** — 3 active workflows gated at runtime (List 2) + scanned in substrate (List 3).
5. **Adversarial 2-gate review** — Gate A + Gate B fork verdicts required for new systems.
6. **Audit log** — `admin_audit_log` append-only triggers; column `username` (NOT `actor`).
7. **Deterministic auditor kit** — pinned ZIP date_time + compress + `sort_keys` (10 integration tests open the actual ZIP).

## Key Differentiators

1. Evidence-by-architecture (Ed25519 chain inseparable from operations)
2. Deterministic builds (NixOS flakes = cryptographic proof)
3. Substrate Integrity Engine (~60 invariants, 60s tick, per-assertion isolation)
4. Two-gate adversarial review protocol (Gate A + Gate B)
5. Canonical metric registry (Counsel Rule 1)
6. BAA enforcement triad (Counsel Rule 6, machine-enforced)
7. PHI-free-by-design substrate with appliance-edge scrubbing
8. Cross-org relocate behind dual-admin attestation flag
9. Auditor kit determinism contract (byte-identical re-downloads)
10. Migration-ledger pre-claim protocol

## Reference Docs

- `docs/HIPAA_FRAMEWORK.md` — control mapping, BAA template, Counsel framing
- `docs/PHI_DATA_FLOW_ATTESTATION.md` — phiscrub patterns, egress points, defense-in-depth
- `docs/PROVENANCE.md` — Ed25519 signing, OTS anchoring, Vault Transit posture
- `docs/legal/v2.0-hardening-prerequisites.md` — MASTER_BAA v2.0 evidence preconditions
- `docs/lessons/sessions-2*.md` — session-by-session lessons archive
- `CLAUDE.md` — project rules, Counsel's 7 Hard Rules, Knowledge Index
- `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` — active migration claims
