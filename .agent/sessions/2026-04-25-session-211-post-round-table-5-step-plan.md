# Session 211 — Post-round-table 5-step plan + sigauth identity-key closure

**Date:** 2026-04-25
**Span:** Single session, ~all-day
**Trajectory:** Round-table-driven consistency hardening across 8 strict CI
gates + 4-commit sigauth design correction.

## Headline

Closed the original 9-task round-table queue (Wave 1 + Wave 2) AND a
follow-up 5-step plan (steps 1-4 done, step 5 deferred behind the
fleet rollout that was step 4D). The trajectory was right but the
position is best described as *honest fragility* — every soft spot is
named, every named soft spot has a permanent gate, every gate has a
ratchet. Round-table caught **8+ substantive bugs** I would otherwise
have shipped, including 3 in code I'd written hours apart in the same
session.

## Strict CI gates locked today

| Gate | Baseline / mode |
|------|-----------------|
| SQL columns INSERT/UPDATE | 0/0 (ratchet) |
| ON CONFLICT uniqueness | 0 (strict) |
| admin_audit_log column lockstep | strict |
| Frontend mutation CSRF | 0 (brace-balanced parser, was regex) |
| FILTER-attaches-to-aggregate | 0 (strict) |
| Privileged-order 4-list lockstep (Py ↔ Go ↔ SQL) | strict |
| Demo-path button → endpoint contract | strict |
| Sigauth legacy-fallback regression guard | strict |
| **Lifespan-pg-smoke as real gate** (#183) | post-205 hard fail |
| **Documentation-drift gate** (#4) | strict |

Pre-session: only `test_no_new_duplicate_pydantic_model_names` was
strict. Net delta: +9 strict gates, +5 baseline ratchets locked at zero.

## #179 sigauth identity-vs-evidence key (4-commit chain)

The substrate `signature_verification_failures` invariant fired 100%
on `north-valley-branch-2` because `signature_auth.py` fell back to
`site_appliances.agent_public_key` (the EVIDENCE-bundle key) when
verifying sigauth (the IDENTITY-key purpose). They're different keys
by design — daemon writes evidence to `/var/lib/msp/keys/signing.key`
and identity to `/var/lib/msp/agent.key`. Closed across:

- **Commit A (`ba68584e`)** — migration 251 adds
  `site_appliances.agent_identity_public_key VARCHAR(64)` with a
  partial index on `(site_id, mac) WHERE NOT NULL`.
  `ApplianceCheckin` Pydantic model gains the field. `sites.py` STEP
  3.6c persists it scoped per-MAC like the evidence-key path.
- **Commit B (`eae1e65f`)** — daemon Go change. `phonehome.go`
  `CheckinRequest` gets `AgentIdentityPublicKey`; `daemon.go::runCheckin`
  populates it from `d.identity.PublicKeyHex()`. Version bump 0.4.12
  → 0.4.13.
- **Commit C (`c4b60d18`)** — `_resolve_pubkey` reads the new column
  ahead of `v_current_appliance_identity` (the claim-event view).
  4 new sigauth tests cover the priority chain.
- **Commit D (`9c895cff`)** — daemon binary built + uploaded to
  `https://api.osiriscare.net/updates/appliance-daemon-0.4.13`
  (sha256 `15e971565b3c1cb7f1b7b75c12d6ab864ff3c73bc6fcc5bea7758dbd3505c6ab`).
  Fleet rollout via `update_daemon` fleet_order
  `ffc754a7-d4db-4296-9aa3-3dd795f46c9a` issued 19:04Z.

**Substrate signal post-deploy:** `sigauth_crypto_failures` already
drained at Commit C. `signature_verification_failures` umbrella will
drain on `north-valley-branch-2` within ~1h of all 3 appliances
landing on v0.4.13 (rolling 1h sigauth_observations window).

## Round-table catches (the meat)

Each round-table iteration caught 1-3 substantive bugs before commit.
Highlights:

- **Step 1 sigauth split:** caught `bad_body_hash` ghost reason (in
  docstring, never emitted by `signature_auth.py`); fingerprint-path
  ambiguity (`/var/lib/msp/agent.fingerprint` vs
  `/etc/osiriscare-identity.json` — both exist, runbook now mentions
  both).
- **Step 2 lifespan-bootstrap:** rejected the heavy 19K-line
  `pg_dump --schema-only` fixture as asyncpg-COPY-incompatible AND a
  maintenance trap. Forced the `000a_legacy_sites_baseline.sql` minimal
  stub approach. Caught migration 067 `status` + `onboarding_stage`
  legacy columns I missed (only walked migrations 000-002 initially).
- **Step 3 sigauth fallback removal:** caught my own step-1 runbook
  prose pointing operators at the *exact wrong key* the same step was
  about to drop. Operational misdirection during a real incident
  averted.
- **Step 4 CSRF grind 41→0:** revealed `apiKey ? undefined : 'include'`
  anti-pattern in 11+ partner files (silent 403 for API-key-authed
  partners) — the consistency rewrite SILENTLY FIXED a class of bug
  nobody knew about.
- **Coach-plan #181 brace-balanced parser:** caught P1 paren-depth
  gate missing — `fetch(buildUrl({a:1}), {method:'POST'})` would treat
  inner `{a:1}` as the options blob.
- **Coach-plan #4 doc-drift gate:** caught P0 — `REMOVED_PATTERNS`
  seed entry didn't actually match its own example prose. Test would
  have passed silently while production drift continued.
- **Coach-plan #183 lifespan hard gate:** caught P0 dead code. The
  "Failed to apply NNN_name" string was only `print()`'d to stdout
  from `migrate.py`, never embedded in the exception. The version-
  extraction regex never matched in production. Self-tests passed
  because they constructed synthetic strings WITH the prefix.
  Fixed `migrate.py:171` to `raise RuntimeError(...)from e`.

Each catch was a 1-3 line code/prose fix made BEFORE commit. Total
round-table dispatch wallclock: ~30 min across the day. Catches: 8+.

## Migrations shipped

- `000a_legacy_sites_baseline.sql` — minimum bootstrap stub for fresh
  CI Postgres. Covers `sites` (with status + onboarding_stage),
  `runbooks` (with runbook_id + steps), `control_runbook_mapping`.
- `012a_compliance_bundles_appliance_id.sql` — closes 013
  multi_framework gap.
- `012b_compliance_bundles_outcome.sql` — same view's other gap.
- `048_hipaa_modules.sql` — fixed `(version, description)` →
  `(version, name)` typo (column doesn't exist; was pre-205 backfilled
  so body never ran in prod).
- `251_site_appliances_agent_identity_public_key.sql` — sigauth
  identity-key column for #179.

## Frontend rewrites

- 23 files migrated from raw mutation `fetch()` to canonical
  `credentials: 'include'` + `csrfHeaders()` pattern. Driven by 3
  parallel agents by directory (partner/portal/long-tail).
- 11+ partner files had `apiKey ? undefined : 'include'` anti-pattern
  corrected to additive `apiKey + cookies + CSRF`.
- 2 inline `// satisfy regex` workarounds removed when the
  brace-balanced parser made them unnecessary.
- `fetchApi` credentials policy aligned `'same-origin'` → `'include'`
  matching every fixed site + survives a future api.osiriscare.net
  subdomain split.
- Sidebar entries added for `/admin/substrate-health` +
  `/admin/substrate/runbooks` (Substrate Runbook Library) — they
  had routes but no navigation, "banner like the others" gap.

## Coach reflection

We're not at end-to-end stability. We are at *honest fragility*. The
round-table cadence is now a **working organ**, not a slogan — every
"ship it" decision today was preceded by a "fix-first" check, and
each check found something real. Two more sessions of this discipline
gets us measurably closer; ten more gets us to the place where
shipping a regression requires an explicit override.

## Next session priorities

1. **Step 5: strict-mode sigauth flip plan** — once the v0.4.13
   fleet rollout drains the substrate signal, design + execute the
   observe→strict transition. Today is too early; need ~24h of
   clean signal first.
2. **Verify v0.4.13 lands on all 3 appliances** within fleet_order
   TTL (24h). If `signature_verification_failures` doesn't drain
   on north-valley-branch-2 within 2h of the third appliance
   reporting v0.4.13, something in Commit B/C is wrong.
3. **P2 follow-ups filed today** (drive down at leisure):
   - #182 GET-only fetchOpts ternaries skip cookies
   - #184 CSRF parser hardening (regex literals + EOF escapes)
   - #185 Doc-drift SQL-context table.column check
   - #186 Lift SESSION_205_CUTOFF to shared constants
4. **Documentation-drift gate growth path** — the
   `REMOVED_PATTERNS` allowlist is the discipline anchor. Each
   future code-path removal should add a row.

## Commits (chronological)

1a565616 fix(schema): grind SQL INSERT/UPDATE baselines 16/9 → 0/0
d3b511a6 fix(metrics): two P0 prod query bugs in prometheus_metrics
04e9b851 fix(frontend): unify fetchApi credentials policy + drop CSRF baseline 58→41
69d568bd test: align test_partner_auth fixtures with magic_token_expires_at rename
3a384737 test: align api.test.ts with fetchApi credentials policy include
646c2d3e test(security): cross-language privileged-order four-list lockstep (#176)
499f6bca fix(frontend): unify score thresholds via getScoreStatus helpers (#172)
3a8c8524 fix(db): partial UNIQUE index — one primary credential per site (#177)
044d06a5 fix(sigauth): per-appliance signing key write at checkin (#148)
48ecce40 test: demo-path button → endpoint contract guards (#175)
2b93a6b9 ci: weekly audit-cadence reminder workflow (#174)
4983ef2f fix(fleet_cli): set app.is_admin for RLS-enforced writes (#163)
08f282ee test(ci): real-Postgres lifespan smoke (#154)
2ee33e90 fix(test): lifespan-pg-smoke skips on legacy sites bootstrap gap
ad8ac331 feat(substrate): split sigauth invariant — adversarial vs umbrella
22b90a22 fix(migrate): legacy sites baseline migration closes #178 bootstrap gap
ac0dae0f fix(migrate): bootstrap stubs for runbooks + control_runbook_mapping; fix 048 typo
cb54673b fix(migrate): 012a adds compliance_bundles.appliance_id before 013 references it
2fe1885f fix(migrate): 012b adds compliance_bundles.outcome (next 013 col gap)
8997a8bf fix(test): narrow lifespan-pg-smoke skip to bootstrap-gap errors
e5ccbcd5 fix(csrf): drive frontend mutation CSRF baseline 41 → 0 (#180)
2003150d fix(sigauth): drop unsound legacy pubkey fallback (#179)
efa29b84 feat(nav): sidebar entries for Substrate Health + Runbook Library
f77e02a6 fix(test): brace-balanced CSRF linter parser (#181)
6f05f386 feat(test): documentation-drift gate for runbook prose (#4)
76822046 fix(test): lifespan-pg-smoke as a real hard gate (#183)
ba68584e feat(sigauth): #179 commit A — schema + checkin model for identity key
eae1e65f feat(daemon): #179 commit B — upload identity pubkey at checkin (v0.4.13)
c4b60d18 feat(sigauth): #179 commit C — read identity column ahead of view fallback
9c895cff docs: #179 commit D — daemon v0.4.13 binary published + fleet rollout

**Total:** 30 commits on main.
