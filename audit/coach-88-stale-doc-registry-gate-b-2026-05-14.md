# Class-B Gate B — Task #88: §8 POSTURE_OVERLAY frontmatter on v2.0-hardening-prerequisites.md

## 200-word summary

Gate B on the AS-IMPLEMENTED diff for Task #88: a 12-line YAML frontmatter block
prepended to `docs/legal/v2.0-hardening-prerequisites.md`, plus the Gate A verdict
file. **Full pre-push sweep: 258 passed, 0 failed, 0 skipped** (`.githooks/full-test-sweep.sh`).
No new test files in the diff; git-tracked-parity gate clean.

**Frontmatter schema fidelity finding:** the block carries all 8 fields POSTURE_OVERLAY
§8 specifies, correctly typed and ordered — BUT adds one field, `valid_until: "v2.0-ships"`,
that §8 does NOT specify. Gate A invented it. This is a **P2, not a P1**: `context-manager.py
validate` only enforces frontmatter on `memory/` files (presence-of-`---` check only — no
field-name validation, no extra-key rejection), POSTURE_OVERLAY's own §9 frontmatter
itself carries non-§8 extras (`gate_a_status`/`gate_b_status`), and §8 is not declared a
closed schema. So `valid_until` cannot break the unbuilt §7 gate; it is a documented
forward-compat risk, not theater.

**Body-line `**Supersedes:**` check:** line 17 (`Supersedes: nothing…`) and frontmatter
`supersedes: null` are CONSISTENT, not conflicting — human-readable twin of the
machine-readable field. No reconciliation required.

YAML is well-formed. No scope-creep into the §7 gate. **APPROVE-WITH-FIXES** (P2 only —
non-blocking, fold into a follow-up).

---

## 1. Full pre-push sweep

```
bash .githooks/full-test-sweep.sh   →   ✓ 258 passed, 0 skipped (need backend deps)
```

- **258 passed / 0 failed / 0 skipped.** No regressions.
- The diff contains two `.md` files (`audit/coach-88-…-gate-a-…md` NEW, staged;
  `docs/legal/v2.0-hardening-prerequisites.md` MODIFIED, staged). Neither is a test file,
  so `test_pre_push_allowlist_only_references_git_tracked_files` and the SOURCE_LEVEL_TESTS
  parity gate have nothing new to chase — confirmed clean in the sweep.
- Diff-only review explicitly NOT relied upon — full sweep executed per Session 220 lock-in.

## 2. Frontmatter schema fidelity (load-bearing probe)

**§8 schema (POSTURE_OVERLAY.md lines 224-236) declares these fields:**
`title`, `description`, `topic_area`, `last_verified`, `decay_after_days`,
`supersedes`, `superseded_by`, `posture_overlay_authoritative`.

**The diff's block (lines 1-11):**

| Field | In §8 spec? | Type matches §8? | Verdict |
|---|---|---|---|
| `title` | ✅ | ✅ quoted string | OK |
| `description` | ✅ | ✅ quoted string | OK |
| `topic_area` | ✅ (replaces memory `type`) | ✅ string | OK |
| `last_verified` | ✅ | ✅ `YYYY-MM-DD` unquoted date | OK |
| `decay_after_days` | ✅ | ✅ int (365) | OK |
| `supersedes` | ✅ | ✅ `null` (§8: "null for new docs") | OK |
| `superseded_by` | ✅ | ✅ `null` (§8: "null until superseded") | OK |
| `posture_overlay_authoritative` | ✅ | ✅ bool `true` | OK |
| **`valid_until`** | **❌ NOT in §8** | n/a | **invented by Gate A** |

**Finding:** 8/8 spec fields present, correctly typed, correctly ordered. The one
deviation is `valid_until: "v2.0-ships"` — Gate A's own §"Decay / supersession semantics"
section invented this as an event-trigger marker. It is **not** in POSTURE_OVERLAY §8,
and POSTURE_OVERLAY's own §9 self-frontmatter does not carry it either (§9 instead adds
`gate_a_status`/`gate_b_status` — themselves non-§8 extras).

**Why this is P2, not P1** (the brief asked whether this is "forward-compat theater"):

1. `context-manager.py validate` enforces frontmatter ONLY on `memory/` files
   (line 414 `# Topic-file frontmatter`, line 420 checks `starts with '---'`). It does
   **not** walk `docs/` at all today, and even for `memory/` it only checks the `---`
   block is *present* — no field-name allowlist, no extra-key rejection.
2. The §7 gate that *would* walk `docs/**` is unbuilt (Gate A's central finding) and §8
   is not declared a closed/strict schema — POSTURE_OVERLAY §9 itself proves extras are
   tolerated by precedent.
3. Therefore `valid_until` **cannot break** anything now or at gate-build time under the
   current §8 wording. It is a *documented, justified* extra (Gate A §"Decay semantics"
   gives the rationale: event-trigger, not date-trigger, to avoid a day-30 false-positive
   stale warning while v2.0 drafting is in flight) — not silent theater.

**P2 fix (non-blocking):** EITHER (a) when the §7 gate is built, add `valid_until` to the
§8 schema as an optional field (it is a genuinely useful event-trigger marker — better
than POSTURE_OVERLAY §4's prose "Pending v2.0…"), OR (b) drop `valid_until` from this
block and rely on the prose `**Status:**`/`**Supersedes:**` body lines. Recommend (a).
Carry as a note on the §7-gate-build follow-up task — do NOT block #88 close on it.

**Precedent-doc comparison:** the only other doc carrying §8-style frontmatter is
`docs/POSTURE_OVERLAY.md` itself (§9). Field-name/type consistency between the two: all
8 shared fields match exactly. The new doc does NOT carry POSTURE_OVERLAY's
`gate_a_status`/`gate_b_status` extras — correct, those are overlay-specific governance
metadata, not general §8.

## 3. Body-line `**Supersedes:**` reconciliation check

- Body line 17: `**Supersedes:** nothing — this is a new gate doc (Task #70, 2026-05-14)`
- Frontmatter line 8: `supersedes: null`

**These are consistent, not conflicting.** Both assert the doc supersedes nothing. The
frontmatter field is the machine-readable twin of the human-readable body line — exactly
the dual-representation pattern Gate A described ("the frontmatter `superseded_by: null`
is the machine-readable twin"). No duplication-as-conflict, no drift risk: a future
supersession edit must touch both, but that is true of every metadata-pair in the repo.
**No reconciliation required.** Optional polish (not a fix): append `(see frontmatter)`
to line 17 — cosmetic only.

## 4. Per-lens verdict

**Steve (load-bearing) — YAML well-formedness + schema match.** YAML is well-formed:
`---` delimiters on lines 1 and 11, valid block, two quoted strings with em-dashes parse
fine, `null`/`true`/int/date all valid scalars, blank line 12 separates frontmatter from
the `# Master BAA v2.0…` H1. Parseable by any YAML 1.1/1.2 loader. Schema match: 8/8 §8
fields present + 1 extra (`valid_until`) — see §2. **Verdict: APPROVE** (the extra field
is P2, see §2).

**Coach (load-bearing — Session 220 "did the diff MISS anything?" antipattern).**
(a) Body-line `**Supersedes:**` — checked §3, consistent, no miss. (b) Other §8-adopter
docs — only POSTURE_OVERLAY itself; the new block is consistent with it (§2). No other
`docs/legal/` doc carries §8 frontmatter, so no precedent was violated and none was
available to copy verbatim. (c) Scope-creep into §7-gate-build — **confirmed NOT**: diff
touches only the one doc + the audit file; `context-manager.py` IS modified in the
working tree but that is pre-existing unstaged churn (`M .agent/scripts/context-manager.py`
unstaged) UNRELATED to this staged diff — the #88 staged diff is exactly 2 files, neither
is the gate. (d) `valid_until` event-trigger format — Gate A *did* invent it (§2);
flagged P2. **Verdict: APPROVE-WITH-FIXES** (P2: `valid_until` not in §8 — reconcile at
gate-build time).

**Maya — N/A.** No PHI surface; doc-metadata edit only. Confirmed N/A.

**Carol — N/A.** No customer-facing copy changed; frontmatter is non-rendered metadata.
The doc's existing legal-language posture is untouched. Confirmed N/A.

**Auditor — N/A.** No attestation/evidence path touched. Note: `last_verified: 2026-05-14`
+ `valid_until` make the doc's currency machine-checkable — audit-favorable. Confirmed N/A.

**PM — N/A.** Note: #88 close-note should record "registry half N/A — §7 gate unbuilt;
frontmatter added for forward-compat" per Gate A. Confirmed N/A.

**Counsel — N/A (confirmed accurate).** `posture_overlay_authoritative: true` is correct
and not an over-claim: this doc IS the owning authority for the "what gates v2.0 BAA
language" topic area, and it remains a non-binding internal engineering checklist — the
frontmatter asserts authority-of-record for a topic area, not legal-instrument status.
No inaccurate claim introduced. Confirmed N/A.

## 5. Final verdict

**APPROVE-WITH-FIXES.**

- Full pre-push sweep: **258 passed / 0 failed / 0 skipped.** No regressions.
- YAML well-formed and parseable; 8/8 §8 schema fields present, correctly typed/ordered.
- Body-line `**Supersedes:**` is consistent with frontmatter `supersedes: null` — no
  reconciliation needed.
- No scope-creep into the unbuilt §7 gate; staged diff is exactly 2 files.
- **P2 (non-blocking, do NOT hold #88 close on it):** `valid_until: "v2.0-ships"` is not
  in the POSTURE_OVERLAY §8 schema — Gate A invented it. It cannot break anything today
  (validate doesn't walk `docs/`, §8 is not strict, POSTURE_OVERLAY §9 itself carries
  non-§8 extras), and Gate A gave a sound rationale for it. Fix at §7-gate-build time:
  add `valid_until` as an optional §8 field (recommended) or drop it. Carry as a note on
  the §7-gate follow-up task.

#88 may be closed with Gate A's recommended note. The P2 is a forward-looking reconcile,
not a blocker — the as-implemented artifact is correct, low-risk, and additive.

— Class-B Gate B, Task #88, 2026-05-14
