# Gate B verdict — L1-orphan Phase 2A diagnostic (2026-05-11)

**Verdict: BLOCK**

The diagnostic's TL;DR root-cause theory ("MONITORING_ONLY_CHECKS overwritten
by daemon-default `resolution_tier=L1` on by-type resolve") is partially
wrong in ways that would make Phase 3 ship the **wrong fix**. The actual
race vector for the larger orphan class (`rogue_scheduled_tasks`, 49 rows)
is **NOT monitoring-only and NOT a backend COALESCE gap**. It is a daemon-
side hardcoded `"L1"` literal on an `escalate` action path. Shipping
Phase 3 Option C as written would close 46/95 (`net_unexpected_ports`)
and miss 49/95 (`rogue_scheduled_tasks`).

## Root-cause verification
- **Monitoring-only class is THE discriminator: ✗.**
  `migrations/157_check_type_registry.sql:46` shows
  `rogue_scheduled_tasks` with `scored=true, monitoring_only=false`.
  Only `net_unexpected_ports` (`migrations/157:130`) is monitoring-only.
  The doc's "Both 100%-orphan classes are in `MONITORING_ONLY_CHECKS`"
  is FALSE.
- **Race window for `net_unexpected_ports`: partially correct, but
  attribution to daemon is wrong.** Verified `agent_api.py:977-984` sets
  `monitoring`. Verified no daemon callsite resolves a monitoring-only
  check: daemon's only two `ReportHealed` callers are gated on a local
  L1 rule match + successful execute (`daemon.go:1706` and
  `healing_executor.go:649`). `net_unexpected_ports` has no daemon-side
  L1 rule, so the daemon never calls `/incidents/resolve` for it. The
  46 L1 rows must come from a different overwrite source (TBD —
  Phase 2A did not find it).
- **Line numbers cited: 2/3 correct.**
  - `agent_api.py:977-983` ✓ (monitoring path UPDATE)
  - `agent_api.py:1597` ✓ (`COALESCE(:resolution_tier, resolution_tier)`)
  - `agent_api.py:1645-1648` ✓ (no COALESCE on by-type)
  - BUT the doc's framing "by-id is protected by COALESCE; by-type is
    not" is misleading — both endpoints default body to `"L1"`
    (`agent_api.py:1589` + `1627`), so COALESCE is a **no-op** in
    practice. Neither path protects.

## Adversarial findings

### P0 — Steve: ROOT CAUSE FOR `rogue_scheduled_tasks` IS WRONG.
`appliance/internal/healing/builtin_rules.go:816-833` defines
`L1-WIN-ROGUE-TASKS-001` with `Action: "escalate"`.
`healing_executor.go:92-98` returns `{escalated: true}` with no error.
`daemon.go:1690-1706` then hits the `if result.Success` branch and
calls `ReportHealed(req.Hostname, req.CheckType, "L1", match.Rule.ID)`
— **the tier is a hardcoded literal `"L1"` regardless of whether the
local action was a runbook execution or an escalation.** This produces
49 `rogue_scheduled_tasks` L1/no-step rows that Phase 3 Option C
(backend `monitoring` → `L1` defense) would NOT catch — because the
backend never set `monitoring` for this class. The fix shape for
`rogue_scheduled_tasks` is the daemon-side change: `ReportHealed`
must take the **rule's action** into account (escalate → `"L3"`,
runbook → `"L1"`).

### P0 — Steve: `net_unexpected_ports` OVERWRITE SOURCE NOT IDENTIFIED.
The doc names the daemon `ReportHealed` as the overwrite vector, but
the daemon never fires that callback for a monitoring-only check (no
local L1 rule match). The 46 L1/no-step rows for `net_unexpected_ports`
must originate elsewhere — possibly evidence_chain `'recovered'`
(`evidence_chain.py:1499`, but that's `'recovered'` not `'L1'`),
a script/backfill, or a code path the diagnostic did not enumerate.
Phase 3 Option C will not close this class until the real source is
named.

### P1 — Carol: daemon-trust posture is correct but doc misstates it.
Even after the P0 fixes, the doc's Option C "backend defends against
malicious/buggy daemon-supplied labels" is sound. Confirmed daemon
auth is `Bearer + site-id-enforce` only — a compromised daemon could
post any `resolution_tier`. The privileged-chain rule
("backend never trusts daemon-supplied labels for state machine
transitions") applies. Keep this design pressure in Phase 3.

### P1 — Maya: mig 306 backfill marker is wrong.
Doc proposes `result='backfill_synthetic'` + `runbook_id='L1-ORPHAN-BACKFILL-MIG-306'`.
That keeps the historical orphan tagged as `tier='L1'`, which — for
the 49 `rogue_scheduled_tasks` rows — is **factually wrong** (the
daemon action was `escalate` → should be L3). Mig 306 should split
by class: rogue_scheduled_tasks orphans → `tier='L3'` synthetic step;
net_unexpected_ports → `tier='monitoring'` synthetic step. Single
flat `tier='L1'` backfill freezes a lie into immutable rows.

### P1 — Coach: doc shape vs. siblings.
Compared to `audit/healing-pipeline-l1-orphan-investigation` peers
(no exact predecessor; closest sibling is the design-doc class).
Doc has the canonical sections (TL;DR + Evidence + Callsite map +
Fix options + Recommendation) — shape OK. But it presents a single
race theory and treats both orphan classes as one bug; they are two
different bugs with two different fixes. Restructure as **two
sub-investigations**, one per class.

### P2 — Coach: COALESCE-vs-default framing is misleading.
Lines 1589 + 1627 both default `body.get("resolution_tier", "L1")`,
so COALESCE at 1597 is a no-op when the body is silent. Doc's "by-id
has COALESCE protection" implies a real defense that doesn't exist.
Fix the framing OR ship a defense by switching to
`body.get("resolution_tier")  # may be None` + COALESCE.

## Recommendation for Phase 3 design

Phase 3 cannot be a single Option C. It must be **TWO fixes**:

**Phase 3a (rogue_scheduled_tasks class — daemon-side):** Modify
`appliance/internal/daemon/daemon.go:1706` to derive `resolution_tier`
from `match.Rule.Action`: `escalate` → `"L3"`, `run_*_runbook` →
`"L1"`. Backward-compat: backend tolerates daemon sending either
shape until appliance fleet rev'd.

**Phase 3b (net_unexpected_ports class — find the source FIRST):**
Phase 2A is INCOMPLETE for this class. Re-run with a SQL trace to
find which backend code path writes `resolution_tier='L1'` for a
row that started as `'monitoring'`. Candidates not yet eliminated:
admin manual-resolve, scripts, evidence_chain edge case,
a cron/backfill. Until the source is named, Phase 3 design for this
class is speculative.

**Backend defense (Option C-lite) stays valid as belt-and-suspenders:**
agent_api.py:1645-1648 should refuse `monitoring → L1` demotion. But
it is NOT the primary fix for either class.

## Recommendation

**BLOCK.** Send back to Phase 2A author with:

1. Re-issue diagnostic split per class.
2. For `rogue_scheduled_tasks`: name `daemon.go:1706` hardcoded `"L1"`
   on escalate-action as the bug.
3. For `net_unexpected_ports`: run the SQL trace
   (`SELECT * FROM admin_audit_log WHERE action LIKE '%incident%'
    AND target IN (<sample 5 orphan ids>)`)
   to find the actual overwriter. Do NOT speculate.
4. Restructure mig 306 backfill to use per-class synthetic tier
   (`L3` for rogue_scheduled_tasks orphans; `monitoring` for net).
5. Reframe "COALESCE protection" — either ship the defense (default
   to `None` not `"L1"`) or remove the framing.

After re-diagnosis, re-run Gate B. Do NOT advance to Phase 3 design
until the `net_unexpected_ports` overwrite source is named with a
file:line citation, same as `rogue_scheduled_tasks` is now.

---
**Author:** Gate B fork (fresh-context adversarial)
**Date:** 2026-05-11
**Doc reviewed:** `audit/healing-pipeline-l1-orphan-investigation-2026-05-11.md`
**Verdict status:** BLOCK — root-cause theory wrong for 49/95 orphans,
unidentified for 46/95.
