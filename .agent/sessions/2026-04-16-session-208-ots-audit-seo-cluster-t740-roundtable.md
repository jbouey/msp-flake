# Session 208 — OTS audit + SEO content cluster + t740 debug round-table

**Date:** 2026-04-16
**Agent:** v0.4.6  |  **ISO:** v37  |  **Schema:** v2.0

## Context

Multiple threads converged today:
- Verify v37 ISO scp (SHA `c7eda339...`) — completed cleanly.
- Enterprise-grade audit of the OpenTimestamps blockchain-stamping feature end-to-end (user clarified: "attestation audit = blockchain stamping from OpenTimestamps").
- Post-audit round-table convened, commentary executed.
- SEO content cluster for the 2026 HIPAA NPRM — 12 marketing pages, JSON-LD, structured data.
- User pushback: don't frame the product as "small practices" — surface the mesh. Positioning rewrite from "1–50 providers" to "single clinic → multi-site provider network → DSO".
- Round-table fixes from an overnight t740 provisioning-failure debug (Pi-hole DNS filter blocking `api.osiriscare.net`): new substrate invariant + dashboard staleness marking.

## Commits landed

| SHA | Summary |
|---|---|
| `bfcefe6` | 2026-ready SEO cluster — 12 marketing pages, mesh-scoped positioning |
| `e5f95db` | SEO audit remediation — canonical collision, deploy static/ gap, JsonLd hardening |
| `eed0958` | OTS audit remediation — upgrade-loop error visibility, auditor-kit rate limit, 13 tamper property tests |
| `821edca` | Make `check_rate_limit` import pytest-safe (try/except relative vs direct-module) |
| `ebc4ee4` | Landing page rework — humans now see the 2026-ready narrative |
| `96ce738` | `provisioning_stalled` invariant + ApplianceCard staleness (t740 round-table) |

## OTS audit findings + remediation

**State of the chain (verified on production):**
- 236,348 total evidence bundles
- 134,099 anchored / 102,170 legacy / 76 pending / 3 batching
- 528 Merkle batches (527 anchored to real Bitcoin blocks 945,322–945,333)
- Reverify sampling 10/10 clean
- Healthy overall

**Three medium-severity findings remediated:**
1. **Logging silent on OTS upgrade failure** — `logger.warning` → `logger.error(exc_info=True)` with structured extras. Double-failure (inner savepoint) now visible.
2. **Auditor kit unrate-limited** — capped at 10/hr via extended `check_rate_limit(..., window_seconds, max_requests)` signature. 429 with `Retry-After` on exceed.
3. **No property-based tamper tests** — added `test_ots_tamper_property.py` (13 cases, pynacl). Exhaustively mutates every byte of chain_hash, bundle_hash, prev_hash, chain_position, signed_data, signature, pubkey; content-swap.

**Residual (non-blocking):**
- `bitcoin_txid` NULL on anchored proofs (OpenTimestamps Python lib returns block-header-only; block height is cryptographically sufficient).

## SEO cluster + positioning rewrite

**New pages (bfcefe6):**
- `/2026-hipaa-update` — NPRM table, 9 controls, FAQ schema
- `/for-msps` — partner-facing positioning
- `/compare/vanta`, `/compare/drata`, `/compare/delve` — comparison matrices
- `/blog`, `/blog/hipaa-2026-ops`, `/blog/prove-fast`, `/blog/evidence-vs-policy`, `/blog/multi-site-dso`
- `/changelog` — public security/feature/fix/disclosure log
- `/recovery` — compliance-refugees landing page

**Infrastructure (e5f95db):**
- Canonical tag ripped out of `index.html`; each SPA route sets its own in `useEffect`
- `JsonLd` helper in `frontend/src/components/marketing/JsonLd.tsx` escapes `<` → `\u003c` (prevents script-tag breakout)
- Deploy workflow rsyncs `sitemap.xml` + `robots.txt` to `/opt/mcp-server/static/` in lockstep with frontend_dist switch + rollback
- CI smoke test fails on byte-size drift
- 5 vitest cases in `marketing.test.tsx` for JsonLd + NPRM anchors + blog posts

**Landing page (ebc4ee4):**
- Hero copy: "single clinic to a multi-site provider network or DSO" — NOT "small practices"
- 2026-Ready teal ribbon → `/2026-hipaa-update`
- NPRM 3×3 callout (9 controls)
- "One platform · every scale" — three tiers (solo / multi-location / DSO)
- Challenge section rewritten (fleet drift / evidence fragmentation / silent failures / trust-the-vendor)
- Footer Resources: 2026 HIPAA Rule, Blog, vs Vanta, vs Drata, Changelog

**Production verified:** `runtime_sha == ebc4ee4`, `LandingPage-CZ3yxA65.js` contains "2026-Ready HIPAA Platform · NPRM mapped", "single clinic to a multi-site", "Built for the 2026 HIPAA Security Rule".

## t740 round-table — provisioning_stalled invariant

**Incident:** A Pi-hole DNS filter at a healthcare clinic blocked `api.osiriscare.net` for the box's MAC. Installer reached Central Command (DNS query from installer environment resolved), but the installed system never checked in. Dashboard showed stale "Uptime: 2m" / "IP: 192.168.88.232" at 41-min-old checkin — mislead 20 min of debugging down subnet/NAT rabbit holes.

**Fix (96ce738):**
1. **`_check_provisioning_stalled` substrate invariant** in `assertions.py` — fires when a MAC appears in `install_sessions` within the last hour (`checkin_count ≥ 3`) but `site_appliances.last_checkin` is NULL or > 15 min stale. Details JSONB carries hint naming Pi-hole / Umbrella / Fortinet / Sophos / Barracuda and the action "whitelist `api.osiriscare.net` (port 443)". Invariant count now 28.
2. **ApplianceCard staleness** — when `live_status != 'online'`, IP / uptime / agent version / nixos version render line-through + muted with `(frozen Xm ago)` annotation. `Last Checkin` itself stays live (it IS the real-time field).

No schema change. No ISO rebuild. Backend-only + frontend-only.

## Deferred

- ISO-side pre-reboot network check (user said "done with the iso right now" re: v37)
- `install_sessions.provision_error` column for exact curl-exit reporting (requires daemon work)
- BAA policy doc `docs/legal/billing-phi-boundary.md` (flagged in memory, unrelated to tonight)

## Verification

- `claude-progress.json` bumped to session 208, agent 0.4.6, ISO v37
- `python3 .agent/scripts/context-manager.py validate` → passed
- `CLAUDE.md` updated with 9 new invariant/rules from today
