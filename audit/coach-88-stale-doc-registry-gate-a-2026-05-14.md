# Class-B Gate A — Task #88: Register v2.0-hardening-prerequisites.md in stale-doc citation registry

## 200-word summary

Task #88 asks to register `docs/legal/v2.0-hardening-prerequisites.md` in "Task #51's
stale-doc citation CI gate." **Investigation finding: that gate does not exist yet.**
Task #51 shipped only the *design* — `docs/POSTURE_OVERLAY.md` v2.1 — which in §7
explicitly states the CI gate **ships LATER**, gated on three preconditions
(pointer-index landed, ≥2 owning docs adopt §8 frontmatter, ≥1 consumer commit).
`grep` across `.agent/scripts/context-manager.py`, `.github/workflows/memory-hygiene.yml`,
and all `tests/test_*.py` finds **zero** implementation: no `--posture-overlay` mode, no
`docs/**` walk, no `OVERLAY_REGISTRY_FILES` allowlist, no supersession-registry scanner.
There is no registry file and no frontmatter-enforcement-on-`docs/` to register into.

**Verdict: #88 is a near-no-op as written, but should NOT be blindly closed.** The
correct, cheap, durable action is **frontmatter-based**: add the §8 YAML frontmatter
block (already specified in POSTURE_OVERLAY §8) to the top of
`v2.0-hardening-prerequisites.md` now. When the §7 gate eventually ships and walks
`docs/**/*.md`, the doc is already compliant — no future registration step needed.
The exact copy-pasteable block is below, with living-checklist decay semantics
(`decay_after_days: 365` + a `valid_until` marker, NOT a 14/30/60-day cadence).

---

## Gate location + mechanism

| Question | Finding |
|---|---|
| Does the Task #51 stale-doc citation CI gate exist? | **No.** Only the design (`docs/POSTURE_OVERLAY.md` v2.1) exists. |
| Is it frontmatter-based or registry-based? | **Designed as BOTH-ish, but neither is built.** §7 says `context-manager.py validate` will be *extended* to (a) walk `docs/**/*.md` applying the §8 frontmatter standard [frontmatter-based] and (b) add a `--posture-overlay` mode that reads POSTURE_OVERLAY §4's supersession registry and greps for bare citations [registry-based]. |
| Where would the "registry" be? | POSTURE_OVERLAY.md **§4 — Supersession registry** is a Markdown table of *superseded* docs (3 rows today). It is a list of dead docs, NOT an allowlist of authoritative docs. There is no place to "register an authoritative doc" — authoritative docs are recognized by carrying current §8 frontmatter (`posture_overlay_authoritative` + non-stale `last_verified`). |
| Evidence of non-existence | `grep -rn 'posture.overlay\|docs/\*\*\|walk.*docs'` in `context-manager.py` → 0 hits. `memory-hygiene.yml` runs plain `validate` with no `--posture-overlay` flag and `paths:` filter is `.agent/**` only (doesn't even trigger on `docs/` changes). No `test_*.py` references `POSTURE_OVERLAY` / `supersession` / `v2.0-hardening`. The §7 "Producer note" itself states the gate ships *after* preconditions — those preconditions are unmet. |

So the premise of #88 ("add the doc to the registry") cannot be executed literally —
there is no registry list and no `docs/`-frontmatter enforcement to add it to.

## 7-lens pass

**Steve (load-bearing) — exact edit.** Since the gate is *designed* frontmatter-based
for `docs/` files (§8) and #70's Gate B P2 intent was "make this doc gate-compliant,"
the right move is to add the §8 frontmatter block now. It is a no-cost forward-compatible
edit: when the §7 gate ships and walks `docs/**`, the doc passes; if the gate never
ships, the frontmatter is harmless documentation. Exact block to prepend as **lines 1-N**
of `docs/legal/v2.0-hardening-prerequisites.md` (before the existing `# Master BAA v2.0…`
H1):

```yaml
---
title: "Master BAA v2.0 — Hardening Prerequisites Checklist"
description: "Living checklist: engineering-evidence preconditions that gate v2.0 master-BAA language. Consult before drafting any v2.0 article/exhibit."
topic_area: "legal-baa-v2-drafting"
last_verified: 2026-05-14
decay_after_days: 365
valid_until: "v2.0-ships"
supersedes: null
superseded_by: null
posture_overlay_authoritative: true
---
```

Then update the existing human-readable `**Status:**` / `**Supersedes:**` lines (4-5) for
consistency — change line 5 from `**Supersedes:** nothing — this is a new gate doc…` is
fine as-is, but the frontmatter `superseded_by: null` is the machine-readable twin. No
other file changes. **Do not** touch `context-manager.py` or `memory-hygiene.yml` — that
is the §7 gate-build work (a separate, larger task, gated on §7's own preconditions), not
#88.

**Coach (load-bearing) — is this a real gap or a no-op?** It is a **real but
mis-framed** gap. The literal task ("add to the registry") is a no-op — no registry
exists. But the *intent* behind #70's Gate B P2 (make the new living-checklist doc
gate-ready) is satisfiable cheaply via the frontmatter edit above, and doing it now is
strictly better than closing #88 empty: it removes a future "backfill frontmatter on
docs/legal/" chore and means the doc is already conformant the day the §7 gate lands.
Honest call: **do the frontmatter edit, then close #88** with a note that the registry
half is N/A because the gate isn't built. Also note for the record: even when the §7
gate ships, its `docs/**` walk would cover `docs/legal/` (the glob is `docs/**/*.md`,
no path exclusion) — so `docs/legal/` *is* in future scope; the frontmatter is not wasted.

**Maya — N/A.** No PHI surface; this is a test-infra/doc-metadata edit.

**Carol — N/A.** No customer-facing copy, no signature/attestation path touched. The
doc's existing legal-language posture is unchanged (frontmatter is metadata only).

**Auditor — N/A**, with one note: frontmatter `last_verified: 2026-05-14` and the
`valid_until: "v2.0-ships"` marker are themselves audit-favorable — they make the doc's
currency machine-checkable rather than relying on the prose `**Status:**` line.

**PM — N/A**, note: #88 should be re-titled on close to reflect reality, e.g.
"#70 Gate B P2 — add §8 frontmatter to v2.0-hardening-prerequisites.md (registry half
N/A — §7 gate not built)."

**Counsel — N/A.** No change to legal instrument content; the doc remains a
non-binding internal engineering checklist. `posture_overlay_authoritative: true` is
appropriate — this doc IS the owning authority for the "what gates v2.0 BAA language"
topic area.

## Decay / supersession semantics for the living-checklist doc

The doc is consulted continuously until `MASTER_BAA_v2.0` ships (~2026-06-03 per task
context), at which point v2.0 supersedes it. It must **not** decay on a 14/30/60-day
cadence — a "stale" warning at day 30 would be a false positive while v2.0 drafting is
still in flight. Chosen semantics:

- `decay_after_days: 365` — long horizon; the doc is a project-reference-class artifact
  (matches the memory-hygiene `reference` default of 365), not a feedback-class
  30-day artifact.
- `valid_until: "v2.0-ships"` — explicit human/machine marker that the real expiry
  trigger is an *event* (v2.0 publication), not a date. When v2.0 ships, that commit
  sets `superseded_by: ["docs/legal/MASTER_BAA_v2.0.md"]` and adds a POSTURE_OVERLAY §4
  supersession row.
- `last_verified: 2026-05-14` — creation date; bump on each substantive checklist edit.

This mirrors how POSTURE_OVERLAY.md §4 already records "Pending v2.0 outside-counsel
hardening" as an *event-triggered* supersession rather than a dated one.

## Final verdict

**APPROVE-WITH-FIXES.**

- The literal task ("register in the stale-doc citation registry") is a **no-op** — the
  Task #51 CI gate is unbuilt and there is no registry list to add to.
- **Fix (do this, then close #88):** prepend the §8 frontmatter block above to
  `docs/legal/v2.0-hardening-prerequisites.md`. One file, ~10 lines, additive, zero risk.
  This satisfies #70 Gate B P2's actual intent (gate-readiness) and is forward-compatible
  with the §7 gate whenever it ships.
- **Do NOT** attempt to build the §7 gate under #88 — that is separate, larger, and has
  its own unmet preconditions (§7 "Producer note": ≥2 §8-adopter docs + ≥1 consumer
  commit). If desired, file a follow-up task for the §7 gate build; #88 is not it.
- Close #88 with the finding noted: "registry half N/A (gate unbuilt); frontmatter
  added for forward-compat."
