# Session 115: NixOS Trace Warnings + Full Docs Audit

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

| Hash | Description |
|------|-------------|
| `5629e34` | fix: resolve NixOS trace warnings (health-check ordering + root password conflict) |
| `7207c52` | docs: audit and correct all technical skill docs against codebase |

## NixOS Trace Warning Fixes

Two warnings that appeared on every `nixos-rebuild`:

1. **msp-health-check ordering** — Had `after = ["network-online.target"]` but no `wants`. NixOS requires both. Added `wants`.

2. **Root multiple password options** — `configuration.nix` set `hashedPassword`, installer ISO profile set `initialHashedPassword`, and `appliance-disk-image.nix` set `initialPassword`. Consolidated: moved `hashedPassword` to `appliance-disk-image.nix` only, removed redundant `initialPassword` from both image configs.

Both appliances rebuilt: exit 0, zero warnings, zero failed services.

## Full Documentation Audit

Ran 6 parallel explore agents to audit all 9 skill docs against the actual codebase. Findings and fixes:

| Doc | Key Corrections |
|-----|-----------------|
| CLAUDE.md | Tests 950→1037, migrations 41→49, hooks 77→78, added VM IP (.254), replaced missing preflight.sh |
| testing.md | Files 39→45, backend tests 55→114, added Go tests, removed nonexistent conftest.py |
| database.md | Migrations 47→49 |
| hipaa.md | PHI patterns 12→14, L1 rules 22→38, fixed rules file path |
| performance.md | Removed virtual scrolling (not installed), removed React.memo (not used) |
| infrastructure.md | Replaced fictional A/B partition with actual 3-partition layout + rebuild watchdog |
| frontend.md | Hooks 77→78 |
| backend.md | Fixed rules path, removed healing_orders reference |
