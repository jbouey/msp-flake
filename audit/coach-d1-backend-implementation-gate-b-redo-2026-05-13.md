# D1 Gate B re-fork — v2 (after BLOCK fixes)

**Reviewer:** Fresh-context Gate B re-fork (Class-B 4-lens: Steve / Maya / Carol / Coach)
**Date:** 2026-05-13
**Scope:** Re-verify the v2 artifacts after author claimed all 6 issues (3 BLOCK + 3 P1) from `audit/coach-d1-backend-implementation-gate-b-2026-05-13.md` were closed.
**Overall:** **BLOCK**

---

## 6-fix closure matrix

| # | Issue | Status | Evidence |
|---|---|---|---|
| BLOCK #1 | All 3 runbooks have `## Root cause categories` + `## Immediate action` + `## Verification` + `## Escalation` sections per `_TEMPLATE.md` | **PARTIAL** | Author added `## Root cause categories` (the section named in the prior verdict) but the canonical template `_TEMPLATE.md` and the CI gate `tests/test_substrate_docs_present.py::REQUIRED_SECTIONS` require **seven** sections, not five. All 3 runbooks are missing **`## Related runbooks`** AND **`## Change log`**. The prior verdict surfaced only the first-missing section because `assert section in body` short-circuits on the first miss. The author fixed the named symptom, not the class. `test_substrate_docs_present.py` STILL FAILS 3/83 with `missing required section: '## Related runbooks'`. |
| BLOCK #2 | `site_appliances.agent_public_key` literal removed from `*_sig*.md` runbooks | **CLOSED** | `grep -n "site_appliances.agent_public_key"` across all 3 runbooks → no matches. Prose rephrased to "registered evidence-bundle public key" + "legacy-key-window column". `test_runbook_truth_check.py::test_runbook_does_not_reference_removed_patterns` PASSES. |
| BLOCK #3 | New SELECT at signature_auth.py has `deleted_at IS NULL` filter | **CLOSED** | `signature_auth.py:614-623` adds `AND deleted_at IS NULL` to the WHERE clause with an explanatory comment ("D1 verifier never validates signatures for soft-deleted appliances"). `test_no_unfiltered_site_appliances_select.py::test_unfiltered_site_appliances_select_ratchet` PASSES (ratchet held at baseline). |
| P1 #1 | Mig 313 partial-index WHERE clause removed (NOW() = non-IMMUTABLE outage class) | **CLOSED** | `mig 313:90-91` `CREATE INDEX … ON appliance_heartbeats (site_id, observed_at DESC, signature_valid)` — no WHERE predicate. Inline COMMENT at `:84-89` cites the outage class + 2026-05-09 lesson. |
| P1 #2 | `daemon_on_legacy_path_b` runbook clarifies sev3-static + visual-escalation-only past deadline | **CLOSED** | `daemon_on_legacy_path_b.md:3` explicitly says "sev3-info … informational flag (`is_past_deprecation`) auto-set on the violation row past that date — runtime severity stays sev3, but operator dashboards SHOULD escalate visually past the deadline." Also at `:42-44`. |
| P1 #3 | All 3 runbooks contain explicit "DO NOT surface to clinic-facing channels" paragraph | **CLOSED** | `grep "DO NOT surface to clinic"` returns one hit per runbook (unsigned:18, sig_invalid:18, legacy_path_b:18). All three cite Session 218 task #42 opaque-mode parity rule. |

**Closure score: 5/6 CLOSED, 1/6 PARTIAL (BLOCK #1).**

---

## Pre-push sweep result

```
$ bash .githooks/full-test-sweep.sh
❌ 2 file(s) failed (out of 241 passed, 0 skipped):
  - tests/test_substrate_docs_present.py
  - tests/test_sql_columns_match_schema.py
```

**Total: 241 passed / 2 failed / 0 skipped (243 files).**

### Failure 1 — `tests/test_substrate_docs_present.py` (BLOCK #1 residual)

```
FAILED tests/test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_heartbeat_unsigned]
FAILED tests/test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_heartbeat_signature_invalid]
FAILED tests/test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_on_legacy_path_b]
3 failed, 80 passed in 1.01s
```

Failure message (identical class for all 3): `missing required section: '## Related runbooks'`.

Canonical template `_TEMPLATE.md` and `REQUIRED_SECTIONS` literal in the test enumerate **7 sections** — the v2 runbooks have **5** (added `## Root cause categories` per prior verdict; never added `## Related runbooks` or `## Change log`).

### Failure 2 — `tests/test_sql_columns_match_schema.py` (NEW, not surfaced in prior Gate B)

```
FAILED tests/test_sql_columns_match_schema.py::test_every_python_insert_references_real_columns
FAILED tests/test_sql_columns_match_schema.py::test_every_python_select_references_real_columns
FAILED tests/test_sql_columns_match_schema.py::test_baseline_doesnt_regress_silently
```

Failure messages:

```
- signature_auth.py:613: SELECT FROM site_appliances references unknown column(s)
  ['previous_agent_public_key', 'previous_agent_public_key_retired_at']
- INSERT violations=1 but INSERT_BASELINE_MAX=0. Adjust INSERT_BASELINE_MAX to match
  the actual count.
- 10 SELECT schema mismatches > baseline 9. A new bug joined the list.
```

Root cause: mig 313 adds two new columns (`previous_agent_public_key`, `previous_agent_public_key_retired_at`) to `site_appliances` and the verifier at `signature_auth.py:613` reads them, but `tests/prod_columns.json` (the schema fixture the test consults) was not regenerated against the post-mig-313 schema. This is the SAME column-drift class as `feedback_three_outage_classes_2026_05_09.md`. The prior Gate B did not surface this because it ran the test-by-name, not the full sweep with cross-test interactions; or the failure existed but was masked. Either way it is BLOCKING.

### Tests cited in the request

| Test | Prior Gate B status | This Gate B status |
|---|---|---|
| `tests/test_substrate_docs_present.py` | FAIL (missing `## Root cause categories`) | **STILL FAIL** (missing `## Related runbooks` + `## Change log`) |
| `tests/test_runbook_truth_check.py` | FAIL (REMOVED_PATTERNS reference) | **PASS** (7/7) |
| `tests/test_no_unfiltered_site_appliances_select.py` | FAIL (ratchet exceeded) | **PASS** (2/2) |

---

## Banned-word scan

```
$ grep -nE "\b(ensure|prevent|protect|guarantee)(s|d|ed|ing)?\b|audit-ready|PHI never leaves|100%|continuously monitored" \
    backend/substrate_runbooks/daemon_heartbeat_unsigned.md \
    backend/substrate_runbooks/daemon_heartbeat_signature_invalid.md \
    backend/substrate_runbooks/daemon_on_legacy_path_b.md
(no output)
```

**PASS** — zero hits across all 3 v2 runbooks. Maya + Carol legal-language gate clean.

---

## Per-lens summary

- **Lens 1 — Engineering (Steve):** **BLOCK** — 2 sweep failures (template-section partial close + schema fixture drift). The schema-fixture failure is a class previously called out in `feedback_three_outage_classes_2026_05_09.md` ("column-drift"); it should have been caught at Gate A.
- **Lens 2 — Coach (consistency):** **BLOCK** — the author closed the symptom in the prior verdict (`## Root cause categories`) but did not audit the canonical `_TEMPLATE.md` for the FULL set of required sections. This is exactly the "diff-only review" antipattern the Session 220 Gate B lock-in named — fix what was called out, miss what the template actually requires.
- **Lens 3 — Maya (legal-language):** **APPROVE** — banned-word scan clean; "DO NOT surface to clinic-facing channels" paragraph in all 3 runbooks satisfies the opaque-mode parity rule (Session 218 task #42).
- **Lens 4 — Carol (privacy/clinic boundary):** **APPROVE** — same evidence as Maya. Plus prose now uses "registered evidence-bundle public key" framing instead of leaking the deprecated column name.

---

## Required fixes to advance to APPROVE

1. **Add `## Related runbooks` + `## Change log` sections to all 3 runbooks.** Match `_TEMPLATE.md` shape. For `daemon_heartbeat_unsigned.md` and `daemon_heartbeat_signature_invalid.md` they cross-reference each other (and `daemon_on_legacy_path_b.md`); `## Change log` line: `2026-05-13 — initial — Task #40 D1 backend implementation`.
2. **Regenerate `tests/prod_columns.json`** to reflect the post-mig-313 `site_appliances` schema (adds `previous_agent_public_key` + `previous_agent_public_key_retired_at`). Or, if the fixture is auto-generated from a live DB, apply mig 313 on the fixture DB first. Then re-run `tests/test_sql_columns_match_schema.py` — the 10→9 SELECT-mismatch baseline should drop back to 9, and the INSERT count should return to 0.
3. **Re-run `bash .githooks/full-test-sweep.sh` until all 243 pass.** Cite the pass count in the next Gate B re-fork verdict.

After those land, this is APPROVE-class — the functional implementation (canonical-format byte-parity, rotation-grace, hybrid protocol, soft-verify, kit version 2.2) is sound per the prior Gate B's 8/8 P0 closure.

---

## Final recommendation

**BLOCK** — 2 sweep failures still blocking push. The author addressed 5/6 of the prior verdict's named fixes correctly; the 6th (BLOCK #1) was closed against the failing-assertion message rather than the template, leaving 2 of 7 required sections still missing. A second class (schema-fixture drift from mig 313's new columns) was not addressed at all — either it was not visible in the prior Gate B's narrower test run, or it was masked by the previously-failing tests aborting the sweep early.

**Per Session 220 Gate B lock-in:** this is the canonical worked example of "fix what was named, miss what's required." A diff-only re-review would have signed off; running the full sweep surfaces both classes. The lock-in works as designed.
