# Gate A DELTA — Task #77 Phase B-lite: sidecar fixture vs in-place augment

**Date:** 2026-05-14
**Gate:** A (delta — material approach change from the original Phase B-lite Gate A in `coach-cast-gate-phase-b-gate-a-2026-05-14.md`)
**Reviewers (fork-based):** Steve / Maya / Coach / PM (Carol / Auditor / Counsel — N/A, pure test infra)
**Subject:** The original Gate A approved Phase B-lite with an *in-place augment* of `prod_columns.json` to `{table: {col: type}}` + a 4-consumer atomic commit. This delta evaluates a lower-risk alternative: a **sidecar** `prod_column_types.json`.

---

## 250-WORD SUMMARY

**Verdict: APPROVE-WITH-FIXES — implement the SIDECAR (`prod_column_types.json`), not the in-place augment.**

Two decisive findings flip the original Gate A's "augment in place" call:

1. **Repo precedent already chose the sidecar pattern.** `tests/fixtures/schema/` already holds THREE fixtures: `prod_columns.json` (names), `prod_column_widths.json` (`{table:{col:width}}`), `prod_unique_indexes.json`. `prod_column_widths.json` is the exact precedent — a per-column scalar schema facet split into its own file rather than fattening `prod_columns.json`. Augmenting `prod_columns.json` with types would make it the *odd one out* and break the established convention. A typed sidecar `prod_column_types.json` keyed `{table:{col:data_type}}` is the consistent choice.

2. **The "4-consumer atomic blast radius" is overstated.** All 3 real consumers of `prod_columns.json` iterate it via `set(...)` / `set(cols)` / `json.loads`-then-`set` — and `set(dict)` yields the dict's KEYS. So even an in-place augment to `{col:type}` would NOT break them by accident. But "survives by luck" ≠ "designed to survive": in-place augment silently widens a 5+-gate shared oracle's value-type contract with no guard. The sidecar leaves `prod_columns.json` untouched — zero blast radius — and gives the new gate a purpose-built typed oracle.

Sidecar drift hazard (two files, regen must touch both) is **fully mitigable** with two mandatory same-commit P0s (below). Effort ~1.5–2h incl. the drift guard — same as the original estimate. Carol/Auditor/Counsel confirmed N/A.

---

## CONSUMER-IMPACT ANALYSIS

`prod_columns.json` consumers (does a `{col:type}` dict-value shape break them?):

| Consumer | How it reads the fixture | Breaks on dict-value? |
|---|---|---|
| `test_sql_columns_match_schema.py:98` | `{tbl.lower(): {c.lower() for c in cols}}` — `set()` over the value | **NO** — `set({...})` yields keys |
| `test_migration_view_columns_exist.py:192` | `{tbl: set(cols)}` | **NO** — same |
| `test_no_raw_discovered_devices_count.py:90` | `json.loads(...)` then key-presence (`"canonical_devices" in fixture`) — *delta correction: it does NOT read as raw text; the original Gate A briefing was wrong on this* | **NO** — key-presence only |
| `test_no_uuid_cast_on_text_column.py` (#77 Phase A) | hardcoded `_TEXT_COLUMNS` frozenset — does **not** read the fixture yet | N/A |

So the original Gate A's "4 consumers all break atomically" is **incorrect** — they survive by virtue of `set(dict)`-yields-keys. But that is luck, not design. The sidecar is still the right call because it (a) follows repo precedent and (b) doesn't quietly widen a shared oracle's contract.

## THE DECISION

**Sidecar `tests/fixtures/schema/prod_column_types.json`**, keyed `{table: {column: data_type}}`, full `information_schema.data_type` strings (NOT a uuid/not-uuid bit — full fidelity costs nothing extra in the same SELECT and the gate's scope is all `::TYPE` casts). The #77 Phase B-lite gate reads this sidecar; `prod_columns.json` and its 3 consumers are untouched.

## DRIFT-GUARD DESIGN (two mandatory P0s, same commit — design-constituent, not deferrable)

- **P0-D1 — single regen command emits BOTH files.** The regen snippet documented in `test_sql_columns_match_schema.py` is extended to a single psql round-trip that writes `prod_columns.json` AND `prod_column_types.json` together. You physically cannot regen one without the other. Document the combined command in BOTH files' docstrings.
- **P0-D2 — key-set parity meta-test (~15 lines).** A test that asserts `prod_column_types.json` and `prod_columns.json` have identical table sets AND identical per-table column sets. Fails loudly on any stale/hand-edited divergence. This is the structural guarantee that the two-file split can never silently rot.

## TYPE GRANULARITY (Maya)

Store full `information_schema.data_type` (`character varying`, `text`, `uuid`, `inet`, `jsonb`, `integer`, `bigint`, `timestamp with time zone`, ...). The gate carries a small `_CAST_TO_PGTYPE` normalization/equivalence map: `uuid` distinct from `character varying`/`text`; `int`/`integer`/`bigint` equivalent for cast purposes; `timestamptz`/`timestamp with time zone` equivalent. The gate flags `col = $N::T` where the cast `T` is in a different equivalence class than the column's stored type.

## EFFORT (PM)

~1.5–2h including the drift guard. The in-place augment is NOT actually cheaper once done responsibly (docstring updates + shape asserts + regen-doc across the augmented file). The sidecar is lower-risk for the same cost and follows precedent.

## FINAL OVERALL VERDICT

**APPROVE-WITH-FIXES — SIDECAR `prod_column_types.json`.** The two drift-guard P0s (single regen command + key-set parity meta-test) are design-constituent and must ship in the same commit. If either is dropped, re-review required. Repo precedent (`prod_column_widths.json`) is the clincher; the original Gate A's "augment in place + 4-consumer atomic" rests on a consumer-blast-radius claim that the evidence (all consumers survive `set(dict)`) does not support.
