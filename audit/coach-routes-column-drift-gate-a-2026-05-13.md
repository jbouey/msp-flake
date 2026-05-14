---
gate: A
topic: routes.py column-drift fix-then-migrate (Task #76)
date: 2026-05-13
verdict: APPROVE-WITH-FIXES
author: Gate-A fork (Steve + Maya + Coach + PM)
---

# Gate A — routes.py:6442 + 8638 column-drift latent-bug-fix THEN canonical migration

## Scope

Two `discovered_devices` SELECTs in `mcp-server/central-command/backend/routes.py`
reference columns that do not exist on the live schema. Brief said the
callsites are at 6421 + 8617; **actual SQL lines are 6442 + 8638** —
6421/8617 are the surrounding function/serializer lines. Fix shape is unchanged.

Phase 2 Batch 2 fork deferred these out of the canonical-migration batch
on the (correct) grounds that you don't migrate broken code — fix the
drift first, prove the endpoint runs green, *then* move it to canonical.
This Gate A approves that fix-then-migrate sequencing.

## Steve — column-drift enumeration (verified against `device_sync.py:1057` CREATE TABLE + post-007 ALTERs)

Canonical `discovered_devices` schema (post mig 080 + 096 + 114):

| code reference | actual column | status |
|---|---|---|
| `first_seen` | `first_seen_at` | WRONG — would 500 |
| `last_seen` | `last_seen_at` | WRONG — would 500 |
| `os_type` | `os_name` | WRONG — would 500 |
| `vendor` | (does not exist) | WRONG — would 500 |
| `id`, `site_id`, `mac_address`, `ip_address`, `hostname`, `device_type`, `compliance_status` | exist | OK |

### routes.py:6440-6446 — `export_site_data` (`/sites/{site_id}/export`)

```python
SELECT id, site_id, mac_address, ip_address, hostname,
       device_type, vendor, compliance_status, first_seen, last_seen
FROM discovered_devices
WHERE site_id = $1
ORDER BY last_seen DESC NULLS LAST
```

**FOUR wrong columns**: `vendor` (does not exist), `first_seen` → `first_seen_at`,
`last_seen` → `last_seen_at` (×2 — SELECT + ORDER BY). Brief said two; the
fork enumeration found four. **Update Task #76 to reflect the real count.**

### routes.py:8637-8641 — `generate_site_compliance_packet` (`/admin/sites/{site_id}/compliance-packet`)

```python
SELECT hostname, os_type, compliance_status, last_seen
FROM discovered_devices
WHERE site_id = $1
ORDER BY last_seen DESC NULLS LAST
```

**TWO wrong columns**: `os_type` → `os_name`, `last_seen` → `last_seen_at` (×2 —
SELECT + ORDER BY). Downstream serializer at 8788-8791 also references the
wrong dict keys (`d["os_type"]`, `d["last_seen"]`) — those must be patched
in the same commit OR aliased in SQL (`os_name AS os_type, last_seen_at AS last_seen`)
to keep the customer-facing JSON shape stable.

### Are these endpoints CURRENTLY broken in production?

**Yes — at runtime, on first call.** Both are admin-facing/operator-only and
exercised rarely:
- `/sites/{id}/export` is gated by `require_operator` and intended for
  decommission/archival workflows (comment line 6347: "HIPAA 6-year retention").
- `/admin/sites/{id}/compliance-packet` is gated for partner auditor-packet
  generation (line 8617).

The fact that neither has surfaced in `journal_api` `endpoint_500` ledger
nor in operator alerts is consistent with the call-volume hypothesis: low
admin-only traffic, not "code is correct." A single decommission attempt
or a single partner-driven packet pull TODAY would 500. **This is a latent
production bug, not a theoretical one.** Treat as P1 (latent prod issue,
no customer-visible impact yet).

## Maya — production-call evidence

Carol+Maya signal: check the `admin_audit_log` + journal endpoint-error
ledger for prior 500s on either path. If either path has a 500 in the
last 90d, that elevates to P0 (we've observed harm). If clean (most
likely), it stays P1 latent.

Out-of-scope for this Gate A — verify in Gate B with VPS psql:
```sql
SELECT action, target, COUNT(*)
  FROM admin_audit_log
 WHERE action ILIKE '%export%' OR action ILIKE '%compliance-packet%'
   AND created_at > NOW() - INTERVAL '90 days'
 GROUP BY action, target;
```

§164.524 access-right framing: customer-facing compliance-packet pull
returning 500 is a §164.524 timeliness risk. Maya recommends shipping
the column-drift fix as **P0-deferred (1-day SLA)** not the calendar-flexible
P1.

## Coach — commit plan

**Two commits, sequenced:**

1. **Commit 1 — column-drift fix-only.**
   - `routes.py:6440-6446`: `vendor` dropped (no schema source); `first_seen → first_seen_at`, `last_seen → last_seen_at` ×2 (alias both `AS first_seen` and `AS last_seen` to preserve dict-key contract used by `dict(row)` serializer at 6449).
   - `routes.py:8637-8641`: `os_type → os_name AS os_type` (alias preserves serializer key at 8789); `last_seen → last_seen_at AS last_seen` ×2 (alias preserves serializer key at 8791).
   - Zero behavior change to the customer-facing JSON shape.
   - Test: add `tests/test_export_endpoints_column_drift.py` — a parametrized AST-shape gate that catches `discovered_devices` SELECTs referencing `first_seen`/`last_seen`/`os_type`/`vendor` without an aliasing `AS first_seen_at`-like form. **This closes the latent-drift class** — the bug exists because we never had a gate for it.

2. **Commit 2 — canonical_devices migration (separate Gate A/B cycle as part of Task #74).**
   - Follow the Phase 2 Batch 1 pattern (`routes.py:5314-5325` shape): `JOIN canonical_devices cd … JOIN discovered_devices dd ON dd.id = ANY(...)` with `ORDER BY cd.canonical_id, dd.last_seen_at DESC` for per-canonical-device deduplication.
   - Preserves columns canonical doesn't carry (`hostname`, `os_name`, `compliance_status`, `vendor` if/when added).
   - Two-eye verify the JSON shape is byte-identical to today's response post-fix-only commit.

**Sequencing rationale:** Commit 1 is small, surgical, behavior-preserving, and shippable today. Commit 2 carries shape-change risk (dedupe semantics — same IP across appliances) and deserves its own Gate A/B + soak. Bundling them masks the drift-fix in the larger diff and makes rollback harder if the dedupe change leaks PHI-adjacent context.

## PM — effort + sequencing

- Commit 1: ~30 min (4 col fixes + AST gate + run pre-push sweep).
- Commit 2: ~30 min implementation + 24h soak per Phase 2 pattern.
- Total clock: 30 min today + 24h soak = task closes ~2026-05-15.
- **Update Task #76 description**: drift count is 6 (4 + 2), not 4 (2 + 2). PM action.

## Final verdict — APPROVE-WITH-FIXES

**Gate A passes** with these required mods before Commit 1 lands:

- **P0**: Use SQL aliases (`AS first_seen`, `AS last_seen`, `AS os_type`) to preserve dict-key contract — do NOT change `dict(row)` serializer keys, downstream consumers (JSON-archival workflows) may key off them.
- **P0**: Drop `vendor` from `routes.py:6442` SELECT (no schema source — cannot alias). If `vendor` is contractually required, file a separate `ALTER TABLE discovered_devices ADD COLUMN vendor TEXT` migration in its own commit — out of scope here.
- **P0**: Land the AST gate (`tests/test_export_endpoints_column_drift.py`) in Commit 1, not Commit 2. The drift class is the cause; the migration is the cleanup. Gate FIRST.
- **P1**: Maya — check 90d journal for prior 500s during Commit 1 Gate B. Elevates severity if seen.
- **P1**: Commit 2 (canonical migration) requires its own Gate A/B per Phase 2 Batch 2 protocol.

Author MUST run the full pre-push sweep (`bash .githooks/full-test-sweep.sh`) in Gate B and cite the pass/fail count.

## 200-word summary

Task #76 asks to fix latent column-drift in `routes.py` at the two
`discovered_devices` SELECTs, then migrate them to `canonical_devices`.
Verification against `device_sync.py:1057` schema confirms the drift is
real and broader than the brief stated: six wrong column references
across two endpoints (`first_seen`, `last_seen` ×2, `vendor` at line
6442; `os_type`, `last_seen` ×2 at line 8638). Both endpoints are
admin/operator-only (decommission export + partner auditor packet), so
likely uncalled in production — but a single call TODAY would 500. This
is a latent production bug. Approve the fix-then-migrate plan with two
sequenced commits: Commit 1 surgically fixes the drift with SQL aliases
to preserve the customer-facing JSON shape, drops the unbackable `vendor`
reference, and adds an AST gate (`test_export_endpoints_column_drift.py`)
that closes the entire drift class. Commit 2 is the canonical_devices
migration in its own Gate A/B cycle following the established Phase 2
Batch 1 JOIN pattern. Effort: 30 min for Commit 1 today; 24h soak for
Commit 2. Maya P1 check: scan 90d journal for prior 500s on either
endpoint during Gate B.
