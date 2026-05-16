# signing_backend_drifted_from_vault

**Severity:** sev2
**Display name:** Signing backend drifted from configured primary

## What this means (plain English)

`fleet_orders` rows signed in the last hour include a `signing_method`
value that differs from the configured `SIGNING_BACKEND_PRIMARY`
env var on mcp-server. The two should match: the primary backend
is the one that produces the signature on every order; if rows
exist with a different `signing_method`, either (a) a code path
silently fell back to a different backend (the silent-swallow bug
class that #114 hardened), or (b) the operator changed the env
var mid-hour without a coordinated restart of every worker.

Vault Phase C iter-4 (2026-05-16): runtime backstop for the
`current_signing_method()` silent-swallow class. The helper at
`signing_backend.py::current_signing_method` had a bare
`except Exception: return SIGNING_BACKEND_PRIMARY` (or
SIGNING_BACKEND when not in shadow mode) — masked Vault errors as
"signing_method=file" rows in `fleet_orders`. The substrate
invariant catches the drift; iter-4 Commit 2 also added structured
ERROR logging + a Prometheus counter on the fallback path so the
operator has direct evidence of the silent failure.

## Root cause categories

- Vault outage + silent fallback to file (the load-bearing case)
- Operator changed `SIGNING_BACKEND_PRIMARY` env without restart
  (no actual problem — clears on next deploy)
- Shadow-mode-to-vault cutover mid-hour without full restart cycle
- Code regression — a new fleet_order INSERT skipped
  `current_signing_method()` + hardcoded a string

## Immediate action

1. Tail mcp-server logs for `current_signing_method_fallback`
   structured errors (added in Vault P0 iter-4 Commit 2):
   ```bash
   ssh root@VPS 'docker logs mcp-server --since 1h 2>&1 | grep current_signing_method_fallback'
   ```
   If present: read the exception type + message. The Vault
   client raised somewhere — investigate by class (network /
   AppRole / Transit).
2. Check the Prometheus counter:
   ```bash
   curl -s http://localhost:8000/metrics | grep signing_backend_fallback_total
   ```
   Compare to the substrate invariant's `unexpected_count` —
   they should agree.
3. Query the actual distribution:
   ```sql
   SELECT signing_method, COUNT(*) FROM fleet_orders
    WHERE created_at > NOW() - INTERVAL '1 hour'
    GROUP BY signing_method;
   ```
4. If Vault is the issue: confirm reachability
   (`nc -zv 10.100.0.3 8200`) + AppRole token validity.
   If env-drift: restart mcp-server with the intended env.

## Verification

- Panel: invariant clears on next 60s tick once the next hour of
  fleet_orders shows signing_method=expected only.
- Cross-check: INV-SIGNING-BACKEND-VAULT startup invariant fires
  if the Vault probe fails — both invariants are aspects of the
  same class.

## Escalation

If `signing_backend_fallback_total` > 100/hour OR the invariant
fires for >3 consecutive ticks, follow the rollback runbook at
`docs/runbooks/VAULT_ROLLBACK_RUNBOOK.md` — Vault is degraded and
rolling back to file primary is the safe move while the Vault
issue is investigated. RED rollback severity.

## Related runbooks

- `docs/runbooks/VAULT_ROLLBACK_RUNBOOK.md` — Vault rollback
- `docs/security/vault-transit-migration.md` — Phase C cutover spec

## Change log

- 2026-05-16 — initial — Vault P0 iter-4 Commit 2
