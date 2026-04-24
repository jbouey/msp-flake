# ADR: Source-of-Truth Hygiene

**Date:** 2026-04-24
**Status:** Accepted
**Owner:** Principal SWE round-table (session 210)

## Context

On 2026-04-23 a full-day fleet outage (7+ hours, 3 bricked appliances, 5 consecutive CI failures) exposed **three concurrent source-of-truth splits** in the appliance provisioning pipeline:

1. **DB split.** `appliance_provisioning.api_key` (raw key, written at initial provision) vs `api_keys.active WHERE appliance_id=X` (hash-indexed, rotation-aware). The daemon's auto-rekey path wrote the latter; the `/api/provision/{mac}` endpoint returned the former. Drift was guaranteed the moment any appliance auto-rekeyed. Every subsequent reflash produced an AUTH_KEY_MISMATCH 401 loop that ended only in manual rescue.

2. **On-disk split.** The installer wrote `/var/lib/msp/config.yaml` by dumping the raw `/api/provision/{mac}` response verbatim (`yq -y '.'`). That response ships both a signed `config:` envelope AND legacy top-level flat duplicates. Two different readers — shell `grep -m1 api_key:` and Go's `yaml.Unmarshal` against a flat struct — extracted **different** api_key values from the same file. Rescue debugging cost 45 minutes when a rescue INSERT targeted the wrong hash.

3. **In-memory split.** The daemon's `incident_reporter`, `telemetry`, and `logshipper` each captured `cfg.APIKey` **by value** at construction. On auto-rekey, `d.config.APIKey` updated; the N sub-component copies stayed stale. Result: `/api/appliances/checkin` returned 200 with the fresh key while `/api/evidence/submit`, `/api/logs/ingest`, `/api/agent/executions` returned 401 with the stale one. Evidence chain silently broken for every auto-rekeyed appliance.

None of the three splits were visible to our invariant-assertion engine. The engine fires on **outcomes** (`evidence_chain_stalled`, `auth_failure_lockout`, `offline_appliance_over_1h`) — it correctly detected the symptoms, but the cause was upstream of any detectable signal.

This was not bad luck. It was a structural pattern: **any value stored in N places with no enforced sync mechanism will drift, and the drift will be invisible until a downstream invariant fires late.**

## Decision

> Every logical value has exactly one authoritative location. Everything else is a reader, a derived view, or a tracked mirror with enforced synchronization.

### Acceptable patterns

1. **One writer, N readers reading from the writer's location.** Canonical.
2. **One writer, N−1 derived views.** `CREATE VIEW`, `GENERATED … STORED` column, computed property on a struct, live `func() T` accessor. The reader cannot observe a stale value.
3. **One writer, N−1 write-through mirrors with enforced synchronization.** Trigger keeps mirror in lockstep; a source-level test or runtime invariant fails loudly on drift. Requires explicit justification in code review.

### Forbidden patterns

- Same value stored in N places with no sync mechanism.
- Same value stored in N places "by convention" (every writer "remembers" to update both).
- Captured-by-value copies in long-lived in-memory state when the value can change. Use a `func() T` or interface-based accessor instead.
- "Backwards-compat duplicates" left in API responses without a deprecation deadline.

## Implementation — the three 2026-04-24 splits closed

| # | Split | Closure | Status |
|---|---|---|---|
| 1 | DB `appliance_provisioning.api_key` vs `api_keys.active` | Stage 1: all four writers removed (commit a87dc9a6). Stage 2: `ALTER TABLE DROP COLUMN` (migration 241, this commit). | Landing now |
| 2 | config.yaml nested `config:` block + top-level flat duplicates | Shell writes only the signed `.config` subtree (`yq -y '.config'`). On-disk file is flat — one reader path, no ambiguity. (commit aef915e6) | Landed |
| 3 | Daemon sub-component `cfg.APIKey` copies | Introduce `CredentialProvider` interface. Daemon is authoritative; sub-components call `creds.APIKey()` per-request, never cache. Replaces the 0.4.8 `SetAPIKey` N-mirror workaround. | Next push |

## Enforcement

Each split is closed by **both** a code change AND a source-level regression test that makes the split impossible to re-introduce silently:

- `test_no_writes_to_deprecated_appliance_provisioning_api_key` (Split #1)
- `test_config_yaml_write_filters_to_config_block_only` + `test_no_full_response_yaml_dump_remains` (Split #2)
- (Split #3) — to be added with the CredentialProvider refactor; the test asserts all sub-components share one `CredentialProvider`, not N private `apiKey` fields.

Additionally, a **QEMU boot-integration test** (`checks.x86_64-linux.appliance-boot` in flake.nix, landing this push) provides a runtime sanity check — it boots the full installed-system config in a VM and asserts `multi-user.target`, `appliance-daemon.service`, `/var/lib/msp/config.yaml`, and `:8443` beacon. This catches classes of regression (missing binary, Python heredoc SyntaxError, systemd deadlock) that text-only tests cannot see.

## Consequences

- **For code review:** every PR touching stored state must explicitly answer: "Is this a new source of truth, a view over an existing one, or a tracked mirror? If mirror, where's the sync mechanism and the enforcement test?"
- **For design:** when a legacy endpoint returns backward-compat duplicate fields, add a deprecation deadline in the same PR. "Temporary" backwards-compat is a decade long without one.
- **For triage:** when an invariant fires, the FIRST question is "which source-of-truth split generated the drift?" — not "which service is flaky?"

## Related

- Task #129 (next session): CredentialProvider refactor, QEMU boot test integration into CI deploy gate, ADR referenced from CONTRIBUTING.md.
- Skill `systematic-debugging` now mandatory after 3+ CI failures in 2h. 2026-04-23 violated this at fail #5; prevents compound-thrash next time.
- Session notes: `.agent/sessions/2026-04-23-v40-brick-root-cause.md` (writeup pending).

## References

- Google SRE Book, chapter on configuration management: "one canonical source, N derived artifacts."
- Jepsen's split-brain writeups: every distributed outage traceable to some value being two things at once.
- Our own `CLAUDE.md` memory: **privileged-access chain of custody** (Session 205) is the same principle at a different layer — single-sourced identity chain, tamper-evident mirror in audit bundles, failure = security event.
