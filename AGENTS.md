# AGENTS.md — routing for agents working in this repo

Small, ruthless. **This file is pure routing**: "for X, read Y." No technical content lives here. If you find yourself adding technical detail, it belongs in the linked doc.

## Project in one paragraph

MSP Compliance Platform — HIPAA compliance attestation substrate for healthcare SMBs. NixOS + MCP + LLM. Evidence-grade observability, drift detection, operator-authorized remediation. Target: 1–50 provider practices in NEPA. Pricing: $200–3000/mo. **Positioning:** this is an attestation substrate, not a coercive enforcement platform — remediation is operator-configured or human-escalated.

## Entry points for agents

| You're about to work on... | Read first |
|---|---|
| Anything — Claude-specific invariants (deploy/push discipline, privileged-access chain, 3-list lockstep) | [CLAUDE.md](./CLAUDE.md) |
| Backend FastAPI + asyncpg + Postgres | [mcp-server/central-command/backend/AGENTS.md](./mcp-server/central-command/backend/AGENTS.md) |
| Go appliance daemon + watchdog | [appliance/AGENTS.md](./appliance/AGENTS.md) |
| NixOS ISO, disk image, systemd units | [iso/AGENTS.md](./iso/AGENTS.md) |
| Docs conventions (ADR, post-mortems, runbooks, SOPs) | [docs/AGENTS.md](./docs/AGENTS.md) |
| Operational procedures (reflash an appliance, rotate a key, respond to an alert) | [docs/runbooks/](./docs/runbooks/) |
| Post-mortem process + template | [docs/postmortems/PROCESS.md](./docs/postmortems/PROCESS.md) |
| Why a decision was made | [docs/adr/](./docs/adr/) |
| HIPAA SOPs | [docs/sop/](./docs/sop/) |
| Session-specific context + recent work | `.agent/sessions/` (chronological operator log) |

## The three invariant documents

These live at their own paths and must be consulted before any structural change:

1. **[docs/adr/2026-04-24-source-of-truth-hygiene.md](./docs/adr/2026-04-24-source-of-truth-hygiene.md)** — every value has one authoritative location. Before adding a new column, field, or in-memory copy of an existing value, read this.
2. **[docs/postmortems/PROCESS.md](./docs/postmortems/PROCESS.md)** — Sev-2+ incidents require a post-mortem within 24 h. Template + trigger + review cadence.
3. **[CLAUDE.md](./CLAUDE.md)** — load-bearing Claude-specific rules (deploy via git push, privileged-access chain, three-list lockstep, RLS via tenant_connection, session tracking).

## Quick references

| Lookup | Path |
|---|---|
| Current fleet state (appliances, versions, checkin ages) | query `site_appliances` via the VPS Postgres |
| Lab credentials + WireGuard endpoints | `.agent/reference/LAB_CREDENTIALS.md` |
| Network + subnet map | `.agent/reference/NETWORK.md` |
| Quick-reference glossary (appliance states, HIPAA rules, risk tiers) | [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) |
| Known issues + active workarounds | [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) |

## Operating discipline (the short version)

- **Never commit without user approval.** The `CLAUDE.md` deploy rule (git push auto-deploys) means every commit is a production change.
- **Run the full local pytest set before any push that touches `mcp-server/` or `iso/`.** `.githooks/pre-push` enforces this — don't `--no-verify`.
- **Sev-2+ incident = 24 h to published post-mortem.** Template is in `docs/postmortems/PROCESS.md`.
- **Three-list lockstep is non-negotiable** — see CLAUDE.md §"Privileged-Access Chain of Custody". Drift = security incident.
- **Single source of truth** — see ADR 2026-04-24. Before adding a second place to store an existing value, default to a VIEW or read-at-request-time accessor.

## When in doubt

Prefer reading the scoped AGENTS.md for the directory you're editing over re-reading this file. This is the map; the terrain is next door.
