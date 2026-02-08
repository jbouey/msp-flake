# Session 93: Context Bloat Fix + Firewall False-Positive Loop

**Date:** 2026-02-07
**Duration:** ~1 hour
**Agent Version:** 1.0.55 → 1.0.56 (code, not yet deployed)

## Problem 1: Context Limit Reached

User hitting "Context limit reached" errors in Claude Code.

### Diagnosis
- `settings.local.json`: 63KB, 505 permission entries accumulated over months
  - Contained leaked Anthropic API key, AWS credentials, bearer tokens, passwords
  - Hundreds of redundant entries subsumed by existing wildcards
- `CLAUDE.md`: 7.7KB with "MUST READ" / "ALWAYS CHECK" language triggering eager context loading
- Duplicate files in `.agent/` root AND `.agent/reference/` (~80KB wasted)

### Fix
- Rebuilt `settings.local.json`: 63KB → 2KB (73 clean wildcards)
- Rewrote `CLAUDE.md`: 7.7KB → 3.9KB (lazy-load language, same info)
- Deleted 4 duplicate files from `.agent/` root
- **Total savings: ~105KB per session startup**

## Problem 2: Firewall Healing Loop (100+ Noise Incidents)

Dashboard showed 100 "Firewall drift / L1 AUTO / Resolved" incidents from Test Appliance, cycling every 1-2 minutes.

### Root Cause
`drift.py:504`: `service_name = firewall_config.get('service', 'nftables')` defaulted to checking `systemctl is-active nftables`. The NixOS appliance uses **iptables** (legacy). `nftables` was always inactive → false critical drift every 60s.

Windows boxes (DC .250, WS .251) were fine — firewall enabled, no GPO overrides.

### Fix
1. **`drift.py`**: Firewall baseline check now tries nftables first, falls back to iptables (chain count > 3 + `iptables-save` hash)
2. **`auto_healer.py`**: Added flap detector — tracks resolve→recur cycles, escalates to L3 after 5 flaps in 30 minutes

## Commits
- `60d842e` - fix: reduce context bloat
- `ab54e8a` - fix: firewall false-positive healing loop + add flap detector

## Tests
903 passed, 3 pre-existing failures (dry_run kwarg), 11 skipped

## Not Completed
- Fleet deploy of v1.0.56 to physical appliance (researched workflow, not executed)
- Credential rotation for leaked keys
