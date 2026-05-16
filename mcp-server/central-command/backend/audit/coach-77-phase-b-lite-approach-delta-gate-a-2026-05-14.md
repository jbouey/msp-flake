# Gate A DELTA Review — Task #77 Phase B-lite Approach Change

**Date:** 2026-05-14
**Gate:** A (pre-execution) — DELTA review of an approach change vs the prior Phase B-lite Gate A
**Lenses run:** Steve, Maya, Coach, PM, Carol/Auditor/Counsel (N/A check)
**Verdict:** **APPROVE the SIDECAR** (`prod_column_types.json`) — with a mandatory single-regen-command + meta-test drift guard. NOT in-place augment.

---

## 250-WORD SUMMARY

The prior Gate A said "augment `prod_columns.json` to `{table:{col:type}}` and update 4 consumers atomically." The proposed delta is a new sidecar fixture `prod_column_types.json` keyed `{table:{col:pg_type}}`, leaving the 3 existing name-only consumers untouched.

**The dict-keys insight partially holds but does not rescue the augment.** Two of three consumers (`test_sql_columns_match_schema.py`, `test_migration_view_columns_exist.py`) iterate/`set()` the value — `set(dict)` yields keys, so they survive shape change transparently. But the third, `test_no_raw_discovered_devices_count.py:90`, does `set(fixture["canonical_devices"]) == expected_cols` AND a sibling test reads the file as **raw text substring-checking `canonical_devices`** — the augment changes byte content under both, and the equality assert silently passes only by luck of `set()` semantics. "Near-zero blast radius" is false: it's low-but-real, and it mutates a fixture three unrelated gates depend on for a benefit only #77 needs.

**Decisive factor — repo precedent.** `tests/fixtures/schema/` already holds THREE fixtures: `prod_columns.json`, `prod_column_widths.json`, `prod_unique_indexes.json`. The codebase has ALREADY CHOSEN the sidecar pattern for typed/width/index schema facets. `prod_column_widths.json` is the exact precedent — a per-column scalar attribute (max width) split into its own file rather than fattening `prod_columns.json`. Phase B-lite's type sidecar is the same shape. Augment would be the *inconsistent* choice.

**Drift guard:** one regen command emits both files in a single psql round-trip; a meta-test asserts identical table+column key-sets. Effort holds at ~1.5–2h.

---

## 1. CONSUMER-IMPACT ANALYSIS — does `{col:type}` dict break each consumer?

| Consumer | How it reads the fixture | `{col:type}` dict break? | Notes |
|---|---|---|---|
| `test_sql_columns_match_schema.py:97-98` | `{tbl: {c.lower() for c in cols} ...}` — set-comprehends the value | **NO** — `{c for c in dict}` iterates keys | Survives transparently |
| `test_migration_view_columns_exist.py:193` | `{tbl: set(cols) ...}` | **NO** — `set(dict)` == set of keys | Survives transparently |
| `test_no_raw_discovered_devices_count.py:90-105` | `set(fixture["canonical_devices"]) == expected_cols` | **NO functionally** — `set(dict)` == keys, equality still holds | BUT see below |
| `test_no_raw_discovered_devices_count.py` (sibling raw-text read claim) | *Not found.* The file reads via `json.loads` then `set(...)`, not raw text. The "raw text substring" characterization in the briefing is **inaccurate** — it's a parsed `set()` compare. | **NO** | Briefing over-stated the risk here |

**Correction to the briefing:** `test_no_raw_discovered_devices_count.py` does NOT read the fixture as raw text — it `json.loads` it then does a `set()` equality. So all 3 consumers would, by luck of Python `set(dict)`-yields-keys semantics, survive an in-place augment **without code changes**.

**So why not augment?** Because "survives by luck" is not "designed to survive." The augment leaves three gates depending on a fixture whose value-type silently changed from `list` to `dict`. The next person who writes `for col in fixture[tbl]: if col == ...` is fine, but anyone who does `fixture[tbl][0]` or `", ".join(fixture[tbl])` or relies on list-ordering breaks. The fixture is a shared oracle for 5+ gates; widening its contract for one consumer's benefit is the wrong trade.

---

## 2. STEVE — is the sidecar genuinely lower-risk?

**Yes — and the drift hazard is fully mitigable.** The sidecar's only real risk is divergence: someone regenerates `prod_columns.json` but not `prod_column_types.json`, and the type fixture goes stale. Mitigation, both mandatory:

1. **Single regen command emits BOTH files.** `test_sql_columns_match_schema.py`'s docstring (lines 26-37) is the canonical regen source. Extend its psql `SELECT` to also pull `data_type`/`udt_name`, and have the Python post-processor `json.dump` two files in one invocation. One command, two outputs — you physically cannot regen one without the other. This is strictly better than `prod_column_widths.json`'s current state (its regen is documented separately in `test_check_constraint_fits_column.py` — a latent drift gap the codebase already tolerates).

2. **Meta-test asserts key-set parity.** A new test (`test_type_fixture_matches_name_fixture`) loads both files and asserts `set(prod_columns) == set(prod_column_types)` at the table level AND `set(cols) == set(typed_cols.keys())` per table. If anyone hand-edits one file or runs a stale regen, this fails loudly. ~15 lines.

With both, the sidecar is **lower risk than the augment**: the augment's risk (silent value-type contract change under 3 gates) has no equivalent guard — nothing asserts "the value is still a list" because two consumers stopped caring. The sidecar's risk is *named, tested, and CI-enforced*.

**Steve verdict: APPROVE sidecar.** The drift hazard is real but the single-command + meta-test pair closes it deterministically. The augment's risk is quieter and unguarded.

---

## 3. MAYA — regen command + type granularity

**Current regen SELECT** (`test_sql_columns_match_schema.py:26-37`) pulls `column_name` only, `GROUP BY table_name`, `json_agg(column_name ORDER BY ordinal_position)`. **It can cleanly also emit `data_type`** — `information_schema.columns` already exposes `data_type` and `udt_name` on the same row. Change `json_agg(column_name)` → `json_object_agg(column_name, data_type)` for the type file, keep the existing `json_agg` for the name file. Both come from one `SELECT ... FROM information_schema.columns` — a single round-trip, two `json_*_agg` projections, split in the Python post-processor.

**Type granularity — what does the #77 gate actually need?** The gate's job: given `col = $N::uuid`, decide "is this column NOT uuid-typed?" The bug class is `::uuid` cast against a non-uuid column. So the gate needs to know the column's actual type to compare against the cast type.

- **`information_schema.data_type`** returns `'character varying'`, `'text'`, `'uuid'`, `'inet'`, `'integer'`, `'bigint'`, `'timestamp with time zone'`, `'jsonb'`, etc. This is the right granularity. The original outage was `appliance_id` = `'character varying'` vs cast `::uuid` — `data_type` distinguishes them perfectly.
- **Do NOT collapse to a uuid/not-uuid bit.** Phase B-lite's stated scope is "ALL `col = $N::TYPE` casts" — not just `::uuid`. A `::int` cast against a `text` column, a `::inet` cast against `varchar` — all are the same bug class. Full `data_type` fidelity costs nothing extra (it's one column in the same SELECT) and future-proofs the gate. Collapsing to a bit would force a re-augment the first time someone wants `::int` coverage.
- **One normalization caveat:** the gate must map cast spellings to `data_type` spellings. `$N::uuid` → `'uuid'`, `$N::int`/`$N::integer` → `'integer'`, `$N::text` → `'text'`, `$N::varchar` → `'character varying'`. A small `_CAST_TO_PGTYPE` dict in the gate handles this — same pattern as the existing regex gate's `_TEXT_COLUMNS` frozenset. `udt_name` is NOT needed (it disambiguates domains/composites, irrelevant here).

**Maya verdict: APPROVE sidecar.** `data_type` is the correct granularity, emits cleanly from the existing SELECT, and full fidelity (not a uuid-bit) is the right call for the stated all-`::TYPE` scope.

---

## 4. COACH — sibling-fixture consistency

**Decisive.** `ls tests/fixtures/schema/` returns THREE files today:
- `prod_columns.json` — `{table: [col,...]}` (name list)
- `prod_column_widths.json` — `{table: {col: int}}` (per-column width scalar)
- `prod_unique_indexes.json` — `{table: [[col,...],...]}` (unique-index column-sets)

**The codebase has ALREADY chosen the sidecar pattern for schema facets.** `prod_column_widths.json` is the *exact* precedent for Phase B-lite: a per-column scalar attribute (max length) that the team split into its own file rather than fattening `prod_columns.json` into `{col: {name, width}}`. The type sidecar is structurally identical — per-column scalar attribute, separate file, separate consumer. Augmenting `prod_columns.json` would make it the **odd one out**: the only multi-facet fixture in a directory where every other facet got its own file.

Minimum-surface-area vs single-source-of-truth: here they don't actually conflict. The "single source" is **prod's `information_schema`** — and it stays single. The fixtures are just *projections* of it. Three projection files all regen'd from the same prod schema is not three sources of truth; it's one source, three views. The single-source-of-truth principle is satisfied as long as regen is atomic (which the drift guard enforces). So minimum-surface-area wins cleanly with no SSoT cost.

**Coach verdict: APPROVE sidecar.** Following the established `prod_column_widths.json` precedent. Augment would be the inconsistent, surprising choice in this directory.

---

## 5. PM — does the sidecar stay in ~1.5h?

**Yes, ~1.5–2h, drift guard included:**
- Extend regen command + post-processor to emit second file: ~20 min (one extra `json_object_agg`, one extra `json.dump`).
- Regenerate from prod, commit `prod_column_types.json`: ~10 min.
- New #77 gate (`test_no_param_cast_against_mismatched_column.py`) — regex for `col = $N::TYPE`, `_CAST_TO_PGTYPE` map, compare vs typed fixture, ratchet baseline: ~45 min. (The Phase A regex gate is the template — this is its structural sibling.)
- Drift-guard meta-test (key-set parity): ~15 min.
- Run, set baseline, verify: ~15 min.

The drift guard does NOT push it out — it's 15 lines. The augment is *not* meaningfully faster: even though the dict-keys insight means the 3 consumers don't strictly *need* code changes, a responsible augment would still (a) update the docstring shape description in all 3, (b) update `test_schema_fixture_loaded`'s shape assertions, (c) update the regen command — that's ~30-45 min of touching 4 files plus the same new gate. The sidecar's "untouched consumers" genuinely saves that.

**On the "list OR dict accessor shim":** technically possible (`cols = fixture[tbl]; cols = cols.keys() if isinstance(cols, dict) else cols`) but this is a code smell — it spreads "the fixture might be either shape" defensive code across 3 files forever. Reject. The sidecar avoids needing any shim.

**PM verdict: APPROVE sidecar.** On-budget, and the augment isn't actually cheaper once done responsibly.

---

## 6. CAROL / AUDITOR / COUNSEL — customer-facing / chain-of-custody?

**N/A — confirmed.** Both approaches are pure test-infrastructure. `tests/fixtures/schema/*.json` is CI-only, never shipped, never customer-facing, touches no attestation chain, no PHI boundary, no evidence bundle. `prod_column_types.json` would contain only `{table: {column: pg_type}}` — public schema metadata, no row data, no PHI, no secrets. No Counsel 7-rule implication. No Gate B legal review needed for this delta.

---

## DECISION

**SIDECAR — `tests/fixtures/schema/prod_column_types.json`**, keyed `{table: {col: data_type}}`.

Rejected: in-place augment of `prod_columns.json` (inconsistent with the established `prod_column_widths.json` precedent; silently widens a 3-consumer shared oracle's value-type contract with no guard).
Rejected: augment-with-keys-still-iterable — the dict-keys insight is *true* (all 3 consumers survive `set(dict)`) but "survives by luck" is not a design; it leaves an unguarded silent contract change.

### Mandatory drift-guard design (both required)

1. **Single regen command, two outputs.** Extend the canonical regen in `test_sql_columns_match_schema.py`'s docstring: one `SELECT column_name, data_type FROM information_schema.columns`, the Python post-processor `json.dump`s `prod_columns.json` (names, via `json_agg`/list) AND `prod_column_types.json` (`json_object_agg(column_name, data_type)`/dict) in the same invocation. Update BOTH docstrings (`test_sql_columns_match_schema.py` and the new #77 gate) to show the unified command.

2. **Key-set parity meta-test** — new `test_type_fixture_matches_name_fixture` (in the #77 gate file or `test_sql_columns_match_schema.py`): load both, assert `set(names_fixture) == set(types_fixture)` at table level, and per-table `set(name_list) == set(type_dict.keys())`. Fails loudly on any hand-edit or stale single-file regen.

### Type granularity
Store full `information_schema.data_type` strings (`'character varying'`, `'uuid'`, `'inet'`, `'integer'`, ...). NOT a uuid/not-uuid bit — Phase B-lite scope is all `::TYPE` casts. The #77 gate carries a small `_CAST_TO_PGTYPE` normalization map (`uuid→uuid`, `int|integer→integer`, `varchar→character varying`, `text→text`, ...).

### Effort
~1.5–2h. Drift guard included (≈30 min of the total). On the prior Gate A's budget.

---

## FINAL VERDICT: **APPROVE-WITH-FIXES**

Approved to proceed with the **sidecar** approach. The two "fixes" are not blockers-pending-revision — they are mandatory components of the approved design and must ship in the same commit as the fixture + gate:

- **P0:** Single regen command emits both files (no separate regen path).
- **P0:** Key-set parity meta-test (`test_type_fixture_matches_name_fixture`).

Both P0s are design-constituent, not follow-ups. If either is dropped, the sidecar's drift hazard becomes real and unguarded — re-review required. Gate B (pre-completion) must run the full source-level test sweep and cite that both fixtures regen from one command and the parity meta-test passes.
