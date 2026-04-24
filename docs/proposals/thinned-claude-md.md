# Proposal — Thinned CLAUDE.md (380 → ~80 lines)

**Status:** DRAFT — operator review gate. Do not apply automatically.
**Author:** Knowledge-architecture phase 2 migration.
**Purpose:** CLAUDE.md today is a mixed-role doc — Claude-specific invariants, backend tips, Go daemon notes, ISO trivia, and a knowledge index all sharing the same bucket. Phase 1 landed AGENTS.md at root; phase 2 writes scoped AGENTS.md under `mcp-server/central-command/backend/`, `appliance/`, `iso/`, and `docs/`. With those in place, CLAUDE.md can shrink to ONLY the rules that are durable, non-delegable, and specifically about how Claude should behave in this repo.

Everything in this proposal is a proposal. **No file is modified by merging this proposal. The operator applies it manually after review.**

---

## Section 1 — Proposed thinned CLAUDE.md (what would be at repo root after)

```markdown
# CLAUDE.md — load-bearing Claude-specific invariants

Pure invariants. ~80 lines. For routing to anything else, see [AGENTS.md](./AGENTS.md). For local patterns in a directory you're editing, read that directory's AGENTS.md first.

## Deploy discipline — the single most expensive rule to break

**DEPLOY VIA GIT PUSH. NEVER scp or rsync by hand.** `.github/workflows/deploy-central-command.yml` auto-deploys backend + frontend to the VPS on push to `main`. The workflow restarts containers, verifies `/api/version` runtime/disk/GitHub SHA equality, and auto-rollbacks on health-check failure. Manual scp causes stale versions and silent "green" deploys. Every commit to `main` is a production change — never commit without user approval.

## Privileged-Access Chain of Custody (INVIOLABLE)

`client identity → policy approval → execution → attestation` is an unbroken cryptographically verifiable chain. Any privileged action on a customer appliance (`enable_emergency_access`, `disable_emergency_access`, `bulk_remediation`, `signing_key_rotation`) MUST carry the chain end-to-end. Enforced at three layers:

- **CLI** (`backend/fleet_cli.py`): refuses privileged orders without `--actor-email` + `--reason ≥20 ch` + successful `create_privileged_access_attestation()`. Rate-limited 3 / site / week.
- **API** (`backend/privileged_access_api.py`): partner-initiated + client-approved flow. Per-site `privileged_access_consent_config` gates whether client approval is required.
- **DB** (migration 175 `trg_enforce_privileged_chain`): REJECTS any `fleet_orders` INSERT of a privileged type unless `parameters->>'attestation_bundle_id'` matches a real `compliance_bundles WHERE check_type='privileged_access'` row for the same site.

The attestation is Ed25519-signed by server, hash-chained to the site's prior evidence bundle, OTS-anchored, and published to `/api/evidence/sites/{id}/auditor-kit` + client portal.

**Three lists MUST stay in lockstep** (any drift = security incident, not cleanup):
- `fleet_cli.PRIVILEGED_ORDER_TYPES`
- `privileged_access_attestation.ALLOWED_EVENTS`
- migration 175 `v_privileged_types` in `enforce_privileged_order_attestation()`

**Never** log actor as `system` / `fleet-cli` / `admin` — actor is always a named human email. **Never** flip `client_approval_required=false` without a consent-config attestation bundle. **Never** `ALTER TABLE fleet_orders DISABLE TRIGGER` for bulk ops. **Never** skip the OTS enqueue for `privileged_access` bundles.

Full detail: `docs/security/emergency-access-policy.md`.

## RLS posture — pool selection matters

- **`tenant_connection()`** — asyncpg, RLS-enforced via `SET LOCAL`. Use for anything that should see ONLY one tenant's data.
- **`admin_connection()`** — asyncpg, admin bypass via `SET LOCAL app.is_admin='true'` wired through the `after_begin` listener in `shared.py`. Use for cross-tenant admin reads + any `compliance_bundles` INSERT.
- **`Depends(get_db)`** — SQLAlchemy AsyncSession, also admin via the same listener. Use for admin CRUD.

Do NOT flip these. Switching an evidence-chain write from `admin_connection` to `tenant_connection` or `get_db` without the admin listener = RLS fail-closed P0 (2026-04-18 precedent: 2,608 InsufficientPrivilegeError rejections in 2 h).

## The three invariant documents

Every structural change consults these first:

1. **[AGENTS.md](./AGENTS.md)** — repo routing. The map of which directory's AGENTS.md to read for what you're editing.
2. **[docs/adr/2026-04-24-source-of-truth-hygiene.md](./docs/adr/2026-04-24-source-of-truth-hygiene.md)** — every logical value has one authoritative location. Before adding a new column, field, or in-memory copy of an existing value, read this.
3. **[docs/postmortems/PROCESS.md](./docs/postmortems/PROCESS.md)** — Sev-2+ = 24 h to published post-mortem. Template, trigger, review cadence.

## Pre-push hook is mandatory

`.githooks/pre-push` runs the full local pytest set for any push touching `mcp-server/` or `iso/`. Never `--no-verify`. If a hook fails, fix the underlying issue and create a NEW commit — NEVER amend the failed commit (the hook failure means the commit never happened in the pre-commit phase; amending would modify the previous commit and destroy work).

## Session tracking

```bash
python3 .agent/scripts/context-manager.py status        # view state
python3 .agent/scripts/context-manager.py new-session N description
python3 .agent/scripts/context-manager.py end-session
python3 .agent/scripts/context-manager.py compact       # archive old sessions
python3 .agent/scripts/context-manager.py validate      # memory hygiene
```

Primary state: `.agent/claude-progress.json` (schema v2). Log substantive session work under `.agent/sessions/YYYY-MM-DD-description.md`.

## Memory hygiene (one paragraph)

`~/.claude/projects/.../memory/MEMORY.md` is truncated at ~200 lines at session start. Keep it as a pure index — detail goes in topic files under `memory/`. Every topic file starts with YAML frontmatter (`name`, `description`, `type`, `decay_after_days`, `last_verified`). Run `python3 .agent/scripts/context-manager.py validate` before commit; CI enforces hygiene on every push touching `.agent/`.

## Quick lab reference

| System | IP | User |
|---|---|---|
| iMac host (SSH port 2222) | 192.168.88.50 | jrelly (SSH key) |
| Physical appliance | 192.168.88.241 | root (SSH key) |
| VPS | 178.156.162.116 | root (SSH key) |
| VPS WireGuard hub | 10.100.0.1 | UDP 51820 |
| Appliance WireGuard | 10.100.0.2 | `ssh root@10.100.0.2` from VPS |

Full credentials: `.agent/reference/LAB_CREDENTIALS.md`. Network map: `.agent/reference/NETWORK.md`.

## Rules summary (the one-line form)

- **Root cause first** — no fixes without investigation. One hypothesis at a time. 3+ failed fixes = question architecture.
- **Verify before claiming done** — run the command, read the output, show evidence. No "should pass."
- `now_utc()` not `datetime.utcnow()`.
- Debug before claiming. Never skip hooks. Never force-push to main.

## Where everything else went

Directory-specific rules (backend PgBouncer, Go daemon build, ISO `yq '.config'`, etc.) now live in the per-directory AGENTS.md. Session-specific rules tagged with a defunct version are archived in `.agent/sessions/`. The Knowledge Index is at `.claude/skills/INDEX.md`.
```

---

## Section 2 — Mapping table: current CLAUDE.md content → destination

| Current section | Lines (approx) | Disposition |
|---|---|---|
| Project one-paragraph + positioning | 1–8 | **DELETE** — already in root AGENTS.md `§Project in one paragraph` |
| Appliance Deployment | 10–17 | **DELETE** — covered by `iso/AGENTS.md` (build) + `docs/runbooks/` (reflash) |
| Directory Structure | 19–42 | **DELETE** — stale in places (`evidence_bundles` legacy mention outdated) + duplicated by root AGENTS.md routing table |
| Key Commands (pytest venv, nix flake check) | 44–51 | **DELETE** — each AGENTS.md has its own build/test block |
| Three-Tier Auto-Healing | 53–60 | **DELETE** — architecture, not a Claude invariant; belongs in `docs/ARCHITECTURE.md` |
| Type System (`_types` import) | 62–70 | **DELETE** — this is Python-agent-specific and that agent is DEPRECATED per the current CLAUDE.md itself |
| Reference Docs table | 72–82 | **DELETE** — duplicated by AGENTS.md routing table |
| Knowledge Index (60+ rows) | 84–163 | **DELETE** — lives in `.claude/skills/INDEX.md` already |
| Quick Lab Reference | 165–176 | **KEEP** — retained in thinned version, still useful mid-session |
| Session Tracking | 178–188 | **KEEP** — retained |
| Memory Hygiene block | 190–205 | **COMPRESS** to one paragraph. Detail lives in `context-manager.py validate` output + `docs/memory_hygiene.md`-style rules |
| Privileged-Access Chain of Custody | 207–224 | **KEEP** — full block retained. This is the highest-consequence invariant in the repo |
| Rules — "Debugging: root cause first" … "Use `now_utc()`" … "Run tests before AND after" | 228–232 | **KEEP** — compressed into the one-line Rules Summary |
| Rules — "DEPLOY VIA GIT PUSH, NOT SCP" | 233 | **KEEP** — promoted to its own top-level section (first after the project intro) |
| Rules — SQLAlchemy `asyncio.gather()` gotcha | 234 | **MOVE** to `mcp-server/central-command/backend/AGENTS.md` (already covered as "no silent write failures" adjacent) |
| Rules — `execution_telemetry.runbook_id` ID mismatch | 235 | **MOVE** to `mcp-server/central-command/backend/AGENTS.md` §local invariants (add a bullet) |
| Rules — Synced L1 rules override built-in | 236 | **MOVE** to backend AGENTS.md |
| Rules — "server.py DELETED (Session 185)" | 237 | **DELETE** — defunct-version session note. Archive reference lives in session logs |
| Rules — "All main.py endpoints require auth (Session 185)" | 238 | **MOVE** to backend AGENTS.md (already covered: `_enforce_site_id` rule) |
| Rules — "PHI scrubbing at appliance egress" | 239 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — Design system constants/copy.ts, score 90/70/50 | 240–241 | **MOVE** to a new `mcp-server/central-command/frontend/AGENTS.md` (out of this phase's scope — note for phase 3) |
| Rules — `check_type_registry` single source of truth | 242 | **MOVE** to backend AGENTS.md |
| Rules — `MONITORING_ONLY_CHECKS` from registry | 243 | **MOVE** to backend AGENTS.md |
| Rules — Latest-per-check scoring | 244 | **MOVE** to backend AGENTS.md |
| Rules — Recurrence-aware L2 escalation | 245 | **MOVE** to backend AGENTS.md |
| Rules — Time-travel reconciliation details | 246–250 | **DELETE** (session-specific) + pointer to `.agent/sessions/2026-04-12-session-205-time-travel-reconciliation.md` |
| Rules — Migration auto-apply FAIL-CLOSED | 251–255 | **MOVE** to backend AGENTS.md §"Migration file conventions" (already partially covered) |
| Rules — Checkin transactional steps log at ERROR | 256 | **MOVE** to backend AGENTS.md |
| Rules — `nix.gc` VPS automatic | 257 | **DELETE** — infra trivia, lives in `/etc/nixos/configuration.nix` + `.agent/reference/NETWORK.md` |
| Rules — asyncpg savepoint invariant | 258 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — No silent write failures | 259 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — Audit-trigger allowlist | 260 | **MOVE** to backend AGENTS.md |
| Rules — Evidence INSERT-only, no DELETE upsert | 261 | **MOVE** to backend AGENTS.md |
| Rules — Checkin is a delivery contract | 262 | **MOVE** to backend AGENTS.md |
| Rules — Persistence runbooks RB-WIN-PERSIST-001/2 | 263 | **DELETE** — lives in the runbook files themselves, and in `~/.claude/skills/windows-server-compliance/SKILL.md` |
| Rules — `configure_dns` fleet order | 264 | **MOVE** to `appliance/AGENTS.md` |
| Rules — Flywheel intelligence dashboard card | 265 | **MOVE** to backend AGENTS.md |
| Rules — `agent_api.py` router not registered | 266 | **MOVE** to backend AGENTS.md |
| Rules — L1 rule `incident_pattern` key | 267 | **MOVE** to backend AGENTS.md |
| Rules — CI/CD deploys + restarts | 268 | **KEEP** (consolidated into the §Deploy discipline block) |
| Rules — iMac SSH port 2222 | 269 | **KEEP** (moves up into Quick Lab Reference as a parenthetical) |
| Rules — Credential encryption key path | 270 | **MOVE** to backend AGENTS.md |
| Rules — `dashboard_api_mount` only mount | 271 | **MOVE** to backend AGENTS.md (deploy section) |
| Rules — Go build ldflags | 272 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — Fleet orders complete-before-create | 273 | **MOVE** to backend AGENTS.md |
| Rules — Backend-authoritative mesh | 274 | **MOVE** to backend AGENTS.md (add a bullet) |
| Rules — "drift" is dead terminology | 275 | **MOVE** to frontend AGENTS.md (future) + backend AGENTS.md copy-constants note |
| Rules — Legal language | 276 | **MOVE** to frontend AGENTS.md (future) — for now keep a one-line pointer in CLAUDE.md since it's a repo-wide posture |
| Rules — `auth.py` execute_with_retry | 277 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — Site credentials may need migration | 278 | **DELETE** — one-off gotcha; lives in `.agent/sessions/` if relevant |
| Rules — Per-appliance signing keys | 279 | **MOVE** to backend AGENTS.md |
| Rules — Appliance `display_name` | 280 | **MOVE** to backend AGENTS.md |
| Rules — `compliance_bundles` is the evidence table | 281 | **MOVE** to backend AGENTS.md |
| Rules — `compliance_packets` table | 282 | **MOVE** to backend AGENTS.md |
| Rules — `mark_proof_anchored()` only way to anchor | 283 | **MOVE** to backend AGENTS.md |
| Rules — Fleet orders GET pending requires auth | 284 | **MOVE** to backend AGENTS.md |
| Rules — Go daemon dangerous orders blocked | 285 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — Go daemon `ReloadRules()` | 286 | **MOVE** to `appliance/AGENTS.md` |
| Rules — Fleet mixed-version state | 287 | **DELETE** — ephemeral fleet state, not a Claude invariant. Query `site_appliances` to learn current state |
| Rules — Nonce replay TTL 2 h | 288 | **MOVE** to `appliance/AGENTS.md` |
| Rules — Download domain allowlist | 289 | **MOVE** to `appliance/AGENTS.md` (already covered — api.osiriscare.net only) |
| Rules — Checkin enforces `auth_site_id` | 290 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — Partner/client login lockout | 291 | **MOVE** to backend AGENTS.md |
| Rules — Session token hashing | 292 | **MOVE** to backend AGENTS.md |
| Rules — Incident dedup `ON CONFLICT (dedup_key)` | 293 | **MOVE** to backend AGENTS.md |
| Rules — Alert digest no `RETURNING COUNT(*)` | 294 | **MOVE** to backend AGENTS.md |
| Rules — `portal_access_log` partitioned | 295 | **MOVE** to backend AGENTS.md |
| Rules — `incident_remediation_steps` table | 296 | **MOVE** to backend AGENTS.md |
| Rules — Dual connection pools | 297 | **KEEP** (retained as §RLS posture in the thinned doc) |
| Rules — Checkin savepoints every step | 298 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — `normalize_mac_for_ring()` vs `normalize_mac()` | 299 | **MOVE** to backend AGENTS.md |
| Rules — Device sync 3 sources | 300 | **MOVE** to backend AGENTS.md |
| Rules — CSRF exempt paths | 301 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — Ops center endpoints | 302 | **MOVE** to backend AGENTS.md |
| Rules — `requirements.txt` exact pins | 303 | **MOVE** to backend AGENTS.md |
| Rules — `_enforce_site_id()` on appliance endpoints | 304 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — `execute_with_retry()` required | 305 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — SQLAlchemy `after_begin` on `AsyncSession.sync_session_class` (the CLASS) | 306–307 | **KEEP** — promoted into §RLS posture (this is THE subtle rule that caused the 2026-04-18 P0; keeping it Claude-visible) |
| Rules — `evidence_chain_stalled` sev1 invariant | 308 | **MOVE** to backend AGENTS.md |
| Rules — Flywheel event_type three-list lockstep | 309 | **MOVE** to backend AGENTS.md |
| Rules — Platform auto-promotion through spine | 310 | **MOVE** to backend AGENTS.md |
| Rules — `_flywheel_promotion_loop` Step 5 removed | 311 | **MOVE** to backend AGENTS.md |
| Rules — `safe_rollout_promoted_rule(scope='fleet')` | 312 | **MOVE** to backend AGENTS.md |
| Rules — `partition_maintainer_loop` | 313 | **MOVE** to backend AGENTS.md |
| Rules — No silent write failures in flywheel loop | 314 | **MOVE** to backend AGENTS.md (already covered) |
| Rules — Go daemon `slog` | 315 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — `go_agents.site_id` FK | 316 | **MOVE** to backend AGENTS.md |
| Rules — Go agent summary live on read | 317 | **MOVE** to backend AGENTS.md |
| Rules — `COMPLIANCE_CATEGORIES` single source | 318 | **MOVE** to backend AGENTS.md |
| Rules — `_send_smtp_with_retry()` | 319 | **MOVE** to backend AGENTS.md |
| Rules — asyncpg `$1` cast | 320 | **MOVE** to backend AGENTS.md |
| Rules — `client_audit_log` append-only | 321 | **MOVE** to backend AGENTS.md |
| Rules — `_audit_client_action()` single source | 322 | **MOVE** to backend AGENTS.md |
| Rules — Partner audit through `partner_activity_logger.py` | 323 | **MOVE** to backend AGENTS.md |
| Rules — `process_merkle_batch()` random suffix | 324 | **MOVE** to backend AGENTS.md |
| Rules — Auditor verification kit | 325 | **MOVE** to backend AGENTS.md |
| Rules — Public Merkle disclosure | 326 | **MOVE** to backend AGENTS.md |
| Rules — `/recovery` public page | 327 | **MOVE** to future frontend AGENTS.md |
| Rules — Vite Web Worker imports | 328 | **MOVE** to future frontend AGENTS.md |
| Rules — `verifyChainWorker.ts` full chain | 329 | **MOVE** to future frontend AGENTS.md |
| Rules — `/sites/{id}/bundles` include_signatures | 330 | **MOVE** to backend AGENTS.md |
| Rules — Packet determinism tests | 331 | **MOVE** to backend AGENTS.md |
| Rules — `/api/evidence/.../random-sample` | 332 | **MOVE** to backend AGENTS.md |
| Rules — Per-bundle OTS download | 333 | **MOVE** to backend AGENTS.md |
| Rules — Public changelog | 334 | **MOVE** to future frontend AGENTS.md |
| Rules — `appliance_framework_configs` | 335–336 | **MOVE** to backend AGENTS.md |
| Rules — `/api/partners/me/audit-log` | 337 | **MOVE** to backend AGENTS.md |
| Rules — Chain Status badge legacy ratio | 338 | **MOVE** to future frontend AGENTS.md |
| Rules — `PublicKeysPanel` | 339 | **MOVE** to future frontend AGENTS.md |
| Rules — Source-level pytest for TSX | 340 | **MOVE** to backend AGENTS.md (they live in backend/tests) |
| Rules — Migration 151 DELETE triggers | 341 | **MOVE** to backend AGENTS.md |
| Rules — Evidence submit enforces `auth_site_id` | 342 | **MOVE** to backend AGENTS.md |
| Rules — SSO user lookup scoped to org_id | 343 | **MOVE** to backend AGENTS.md |
| Rules — Device sync hostnames scrubbed | 344 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — Deploy result scrubbing | 345 | **MOVE** to `appliance/AGENTS.md` (already covered) |
| Rules — HIPAA 7-year retention | 346 | **MOVE** to backend AGENTS.md |
| Rules — Fleet order health check rollback (daemon 0.4.3) | 347 | **MOVE** to `appliance/AGENTS.md` + backend AGENTS.md |
| Rules — Fleet CLI `site_id` for v0.3.82 | 348 | **DELETE** — defunct-version workaround; archive in session |
| Rules — Flywheel Spine, orchestrator, safe_rollout | 349–351 | **MOVE** to backend AGENTS.md |
| Rules — Deploy bug session 206 | 352 | **DELETE** — session-specific; lives in `.agent/sessions/` |
| Rules — pgbouncer DuplicatePreparedStatement | 353 | **MOVE** to backend AGENTS.md |
| Rules — Substrate Integrity Engine | 354 | **MOVE** to backend AGENTS.md |
| Rules — Migration 208 row-guard bypass | 355 | **MOVE** to backend AGENTS.md (Migration conventions) |
| Rules — Migration 209 api_keys triggers | 356 | **MOVE** to backend AGENTS.md |
| Rules — fleet_cli URL DNS validation | 357 | **MOVE** to backend AGENTS.md + `appliance/AGENTS.md` (already covered) |
| Rules — install_sessions loop alert | 358 | **MOVE** to backend AGENTS.md |
| Rules — Daemon auto-rekey | 359 | **MOVE** to `appliance/AGENTS.md` |
| Rules — Installer v25/v26 BOOTX64 + compat gate | 360 | **MOVE** to `iso/AGENTS.md` (already covered — hardware gate) |
| Rules — Provisioning latency SLA | 361 | **MOVE** to backend AGENTS.md |
| Rules — Recovery-shell escape hatch | 362 | **MOVE** to backend AGENTS.md (fleet order) + `iso/AGENTS.md` |
| Rules — Break-glass retrieval hardened | 363 | **MOVE** to backend AGENTS.md |
| Rules — Billing PHI-free by design | 364 | **MOVE** to backend AGENTS.md |
| Rules — Stripe lookup_keys | 365 | **MOVE** to backend AGENTS.md |
| Rules — `stripe` Python lib rebuild | 366 | **MOVE** to backend AGENTS.md (deploy note) |
| Rules — BAA signatures append-only | 367 | **MOVE** to backend AGENTS.md |
| Rules — Pilot CTA self-serve | 368 | **MOVE** to future frontend AGENTS.md |
| Rules — `provisioning_stalled` invariant | 369 | **MOVE** to backend AGENTS.md |
| Rules — `ApplianceCard` staleness | 370 | **MOVE** to future frontend AGENTS.md |
| Rules — SEO canonical deploy gap | 371 | **MOVE** to future frontend AGENTS.md |
| Rules — `JsonLd` helper escaping | 372 | **MOVE** to future frontend AGENTS.md |
| Rules — Auditor kit rate-limited | 373 | **MOVE** to backend AGENTS.md |
| Rules — `upgrade_pending_proofs` logs ERROR | 374 | **MOVE** to backend AGENTS.md (already covered as no-silent-write-failures) |
| Rules — OTS tamper property tests | 375 | **MOVE** to backend AGENTS.md |
| Rules — Landing page positioning | 376 | **MOVE** to future frontend AGENTS.md |
| Rules — v38 audit kill-switch | 377 | **MOVE** to `iso/AGENTS.md` (already covered) |
| Rules — v38 install halt telemetry | 378 | **MOVE** to `iso/AGENTS.md` + backend AGENTS.md |
| Rules — v38 compat-match relaxed | 379 | **MOVE** to `iso/AGENTS.md` |
| Rules — `git add -A` on Nix dirty worktrees | 380 | **MOVE** to `appliance/AGENTS.md` (already covered) |

**Net result:** ~30 KEEP lines, ~250 MOVE to scoped AGENTS.md (mostly backend), ~100 DELETE (session-specific, defunct-version, duplicated).

---

## Section 3 — Migration note (how to apply this proposal)

### Pre-flight

1. Verify the three per-subdirectory AGENTS.md files landed in phase 2:
   ```bash
   ls mcp-server/central-command/backend/AGENTS.md \
      appliance/AGENTS.md \
      iso/AGENTS.md \
      docs/AGENTS.md
   ```
2. Confirm current line count:
   ```bash
   wc -l CLAUDE.md   # expects 380
   ```

### Apply

Replace `CLAUDE.md` with Section 1 above verbatim. Commit as a SINGLE commit (not squashed with moves) so the diff is reviewable:

```bash
git add CLAUDE.md
git commit -m "docs: thin CLAUDE.md (380 → ~80 lines) — scoped rules moved to AGENTS.md"
```

### Verify

```bash
# 1. CLAUDE.md is within target
wc -l CLAUDE.md   # expects ~80–100

# 2. No Claude invariant was silently deleted — grep the key phrases
grep -l "Privileged-Access Chain of Custody" CLAUDE.md   # MUST be present
grep -l "DEPLOY VIA GIT PUSH"                CLAUDE.md   # MUST be present
grep -l "tenant_connection\|admin_connection" CLAUDE.md  # MUST be present
grep -l "AsyncSession.sync_session_class"    CLAUDE.md   # MUST be present

# 3. Scoped content IS in the per-directory AGENTS.md
grep -l "execute_with_retry"  mcp-server/central-command/backend/AGENTS.md
grep -l "_enforce_site_id"    mcp-server/central-command/backend/AGENTS.md
grep -l "CredentialProvider"  appliance/AGENTS.md
grep -l "yq -y '.config'"     iso/AGENTS.md

# 4. No dangling references
grep -rn "CLAUDE.md" . | grep -v "^\./\.git" | grep -v "^\./\.claude/worktrees"
#   Should only point at the root-level file + AGENTS.md routing links.
```

### Phase-3 follow-ups (out of scope for this proposal)

The mapping table notes many "MOVE to future `frontend/AGENTS.md`" items. Phase 3 writes:

1. `mcp-server/central-command/frontend/AGENTS.md` — for constants/copy.ts source-of-truth, scoring thresholds, Vite worker imports, PortalScorecard/PortalVerify patterns, JsonLd escaping, landing-page positioning.
2. Optional: `agent/AGENTS.md` for the workstation agent (Go 1.24 amd64 / 1.26 arm64 split, EV code-signing path).
3. Review the "Knowledge Index" block in `.claude/skills/INDEX.md` to confirm parity with the deleted CLAUDE.md index.

### Rollback

If a consumer flags a missing invariant after CLAUDE.md lands:
- Restore only that invariant to CLAUDE.md (keep the thinning otherwise).
- File an ADR explaining why that invariant belongs at root rather than in a scoped AGENTS.md.
- Update this proposal's mapping table so the next revision knows.

---

**This file is advisory. No automation applies it. Operator review + manual edit required.**
