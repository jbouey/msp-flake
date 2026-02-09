# Session 102: Firewall Drift Loop Fix + Domain Discovery Dedup

**Date:** 2026-02-08
**Duration:** ~2 hours

## Problems Solved

### 1. Firewall Drift Circular Loop (100+ incidents)

**Symptom:** VM appliance (Test Appliance Lab B3c40c) fired "Firewall drift" every ~10 minutes, L1 AUTO "resolved" it, but the fix never stuck. Dashboard showed 100+ identical incidents.

**Root cause chain:**
1. `_check_firewall()` detects no nftables/iptables on NixOS VM → `status: "warning"`
2. L1-FW-001 matches → action `restore_firewall_baseline`
3. Maps to Windows runbook RB-WIN-SEC-001 → runs on fallback Windows target (not NixOS)
4. Incident marked "resolved" even though NixOS wasn't fixed
5. 600s cooldown expires → repeat forever

**Why flap detector didn't catch it:**
- Max incidents in 30min with 600s cooldown = 3
- Flap threshold was 5 in 30min → **mathematically unreachable**

**Fix (3 layers):**
1. **Flap thresholds:** 5/30min → 3/120min (auto_healer.py)
2. **Platform guard:** L1-FW-001 skips NixOS (level1_deterministic.py, mcp-server/main.py, DB)
3. **Cooldown extension:** 1hr per-check override on flap (appliance_agent.py)

**Also fixed:** `ResolutionLevel.LEVEL3_ESCALATION` → `LEVEL3_HUMAN` (latent enum bug)

**Confirmed:** Flap detector triggered after 3 recurrences in 23 minutes, cooldown extended to 1 hour.

### 2. Domain Discovery Notification Spam

**Symptom:** Repeated "Domain Discovered: northvalley.local" notifications on every agent restart.

**Root cause:**
- Backend `report_discovered_domain()` does unconditional INSERT INTO notifications
- Appliance `_domain_discovery_complete` flag is in-memory only, resets on restart

**Fix (2 layers):**
1. **Backend dedup:** Check for existing notification within 24h before INSERT (sites.py)
2. **Persistent flag:** Write `.domain-discovery-reported` to state_dir (appliance_agent.py)

## Files Modified

| File | Changes |
|------|---------|
| `auto_healer.py` | Flap thresholds 5→3, window 30→120min, escalated=True, LEVEL3_HUMAN fix |
| `appliance_agent.py` | Per-check cooldown overrides, 1hr extension on flap, persistent domain flag |
| `level1_deterministic.py` | platform!=nixos on L1-FW-001 |
| `mcp-server/main.py` | platform!=nixos on synced L1 firewall rules |
| `mcp-server/central-command/backend/sites.py` | 24h notification dedup |
| `test_auto_healer.py` | 5 new flap detector tests |
| `test_drift_cooldown.py` | 5 new cooldown/platform tests |

## Commits

- `99c31b9` — Agent-side flap fix + NixOS guard + tests
- `ac60603` — Server-side L1 rules platform guard
- `4a89ca9` — Domain discovery notification dedup

## Key Lessons

- Synced rules at `/var/lib/msp/rules/l1_rules.json` override built-in rules — must update both agent and server
- Agent overlay at `/var/lib/msp/agent-overlay/` must be removed after nixos-rebuild to use nix-built code
- Flap detector math must account for cooldown intervals (threshold must be ≤ window/cooldown)
