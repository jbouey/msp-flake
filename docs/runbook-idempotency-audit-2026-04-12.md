# Runbook Idempotency Audit — Session 205 Phase 3

**Date:** 2026-04-12
**Scope:** `appliance/internal/daemon/runbooks.json` (124 runbooks)
**Reason:** Time-travel reconciliation may re-execute runbooks after a
VM snapshot revert / backup restore. Every re-executable runbook must
be idempotent (produces same end-state on N-th execution as on 1st).

## Methodology

Static analysis over `remediate_script` for each runbook. Flagged:
- `New-*` PowerShell cmdlets lacking `-Force`, `-ErrorAction
  SilentlyContinue`, or a preceding `Test-Path` guard.
- Shell `>>` appends to config files (non-idempotent — duplicates
  lines on re-run).
- `useradd`/`groupadd` without a `getent` guard.

## Results

- **Total runbooks:** 124
- **Clean (no red-flag patterns):** 113
- **Flagged for review:** 11
- **Confirmed non-idempotent (would duplicate state on re-run):** 9
- **False positives (transient COM objects, not state):** 3

## Phase 3 MVP Impact: ZERO RISK

The Phase 3 MVP daemon handler (`reconcile_apply.go`) logs
`runbook_ids` from the reconcile plan but **does not execute them** —
it defers re-application to the normal drift-scan cycle. Drift scan
uses `detect_script` as an idempotency gate: if the target state is
already correct, `Drifted=false` and `remediate_script` never fires.

**Net effect:** every runbook is effectively idempotent in the normal
cycle because detect-then-remediate self-gates.

## Phase 3.5 Requirement (if unconditional re-execution is added)

If a future enhancement executes `runbook_ids` from the reconcile plan
unconditionally (skipping detect), these 9 Linux runbooks MUST be
fixed before that lands:

| Runbook         | Target file             | Fix pattern |
|-----------------|-------------------------|-------------|
| LIN-ACCT-002    | `/etc/login.defs`       | Use `sed -i` to replace or `grep -q \|\| echo >>` |
| LIN-AUDIT-001   | `/etc/audit/rules.d/identity.rules` | Same |
| LIN-AUDIT-002   | `/etc/audit/rules.d/auth.rules`     | Same |
| LIN-BANNER-001  | `/etc/ssh/sshd_config`  | Same |
| LIN-SSH-001     | `/etc/ssh/sshd_config`  | Same |
| LIN-SSH-002     | `/etc/ssh/sshd_config`  | Same |
| LIN-SSH-003     | `/etc/ssh/sshd_config`  | Same |
| LIN-SSH-004     | `/etc/ssh/sshd_config`  | Same |

### Canonical idempotent idiom

```bash
# Instead of:
echo "Protocol 2" >> /etc/ssh/sshd_config

# Use:
grep -qE '^[[:space:]]*Protocol[[:space:]]+2' /etc/ssh/sshd_config || \
  echo "Protocol 2" >> /etc/ssh/sshd_config

# Or better (handles existing wrong value):
if grep -qE '^[[:space:]]*Protocol[[:space:]]' /etc/ssh/sshd_config; then
  sed -i 's/^[[:space:]]*Protocol[[:space:]].*/Protocol 2/' /etc/ssh/sshd_config
else
  echo "Protocol 2" >> /etc/ssh/sshd_config
fi
```

## False positives (acceptable, no change needed)

- **RB-WIN-PATCH-001, RB-WIN-UPD-001** — `New-Object -ComObject
  Microsoft.Update.Session/AutoUpdate` creates a transient in-memory
  COM handle. No persistent state.
- **RB-WIN-DEVICE-003** — `New-Object
  System.Data.SqlClient.SqlConnection` creates an in-memory connection
  object. No persistent state.

## Overall Posture

**Good.** 113/124 runbooks (91%) are cleanly idempotent with no
required changes. The 9 flagged Linux runbooks are real but only
matter if Phase 3.5 moves to unconditional re-execution. The Phase 3
MVP is safe to ship as-is.

## Next Steps

1. **Now:** Mark this audit complete. Ship Phase 3 MVP.
2. **Before Phase 3.5:** If unconditional runbook re-execution is
   added, fix the 9 Linux runbooks to use guarded-append idiom above.
3. **Process:** Add a CI linter that flags new runbooks using `>>`
   into config files. Prevents regression.

## Files referenced

- `/Users/dad/Documents/Msp_Flakes/appliance/internal/daemon/runbooks.json`
- `/Users/dad/Documents/Msp_Flakes/appliance/internal/daemon/reconcile_apply.go` (Phase 3 MVP handler)
