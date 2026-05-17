# Gate B — #139 Phase B — 2026-05-17

**Commit:** 149aaed1
**Verdict:** APPROVE-WITH-FIXES

## Steve
- **12 GLBA picks all landed** (verified via yaml.safe_load + per-check enumeration):
  backup_status, encryption_at_rest, firewall_enabled, antivirus_status,
  patch_status, audit_logging, access_control (2), network_security (2),
  incident_response, prohibited_ports, encrypted_services, tls_web_services.
- **Paired entries verified**: `access_control` has 2 GLBA entries (c)(1)+(c)(5);
  `network_security` has 2 GLBA entries (c)(2)+(c)(3). Both pairs landed.
- **YAML loads clean**: `yaml.safe_load` returns no warnings; 1265 lines parse.
- **`mapping_strength` present on EVERY GLBA entry** (12 of 12), correctly
  distributed: 9 satisfying / 3 supportive (backup_status, firewall_enabled,
  antivirus_status — matches design).
- **P1 — `mapping_strength` + `question_bank_key` silently dropped at DB seed.**
  `framework_sync.py::_seed_from_yaml` (lines 425-447) reads only
  `control_id/control_name/category/subcategory/required` → both new fields are
  dead in DB until a Phase C extension. Test gate proves YAML well-formedness
  but `check_control_mappings` table never sees them. Reference-only per design
  (commit body acknowledges), but the gap is undocumented in the seed function
  itself. Add an inline comment so a future contributor doesn't burn an hour
  hunting "why is mapping_strength NULL in the DB".

## Maya
- **3 supportive picks auditor-defensible**: backup_status→(h) (recovery as part
  of IR plan, not the plan itself), firewall_enabled→(c)(2) (perimeter is one
  of many segmentation controls), antivirus_status→(c)(6) (AV is one detection
  control of many under "audit/logging/monitoring sufficient to detect attacks").
  Each correctly carries `required=false` so we don't over-claim.
- **4× (c)(3) clustering acknowledged in commit body, rationale tolerable.**
  Each citation covers a distinct evidence shape (at-rest, cleartext-protocol,
  HTTPS-vs-HTTP, alt-port-TLS) — auditor reading the report sees 4 separate
  technical checks satisfying one CFR subsection from 4 angles. Not padding.
- **P1 — Counsel Rule 6 (BAA enforcement) NOT addressed for GLBA scope.** GLBA
  customers are financial services orgs — different subprocessor flow than
  HIPAA-BAA covered entities. `baa_enforcement.py::BAA_GATED_WORKFLOWS` has
  NO entry for `glba_report_export` or similar. Reports could be exported via
  the auditor-kit endpoint by a GLBA customer with no GLBA-specific
  subprocessor agreement on file. **Mitigant:** the kit content remains
  technical-evidence-only (no PHI flow class), so this is a contracts gap
  not a data-flow gap. Counsel Rule 2 (PHI boundary) UNAFFECTED. Carry as
  TaskCreate followup for Phase C scope.
- Honesty invariant (`supportive ⇒ required=false`) pinned in test #5 — strong.

## Carol
- **Pre-push sweep**: `bash .githooks/full-test-sweep.sh` → **287 passed, 0
  failed, 0 skipped (need backend deps)**. No SOC2/GLBA-touching gates
  surfaced beyond the new 8.
- **8 new gates**: ALL 8 PASS locally (`pytest tests/test_framework_yaml_coverage.py -v` → 8 passed in 0.94s).
- **`question_bank_key` leak gate**: verified ZERO existing renderer reads the
  field (grep across `backend/` + `packages/compliance-agent/` returned only
  YAML lines + the new gate file). Phase A concern remains closed for Phase B
  — but the gate `test_glba_question_bank_keys_resolve` correctly fails
  forward if a future renderer references a missing key.
- **Schema-test infrastructure for YAML**: `test_sql_columns_match_schema.py`
  works on SQL columns, not YAML. The new 8 gates ARE the typo/regression
  fallback. Adequate for content-only Phase B; if Phase C adds DB columns for
  the new fields, schema-fixture sidecars (per the Session 220 #129 rule) will
  need refreshing in lockstep.

## Coach
- **GLBA WILL show up in customer dropdown**: `framework_allowlist.py:24` has
  `SUPPORTED_FRAMEWORKS = frozenset({"hipaa", "soc2", "glba"})` — GLBA already
  in the allowlist BEFORE Phase B. Phase B's YAML mappings will populate the
  existing GLBA dropdown after next boot/sync.
- **Phase B/Phase A consistency**: nothing in `framework_allowlist.py` or
  `framework_templates.py` changes — Phase B is a pure content-layer extension.
  Correct scope.
- **P0 — Commit body claim "5min warmup" is UNVERIFIED.** Commit asserts
  `_seed_from_yaml` runs on backend boot after 5min warmup + weekly via
  `framework_sync_loop`. Verified: `bg_heartbeat.py:119` shows
  `framework_sync: 604800` (7 days). NO 5-min boot warmup grep hit in main.py
  or framework_sync.py — the only boot-time invocation path is
  `_run_full_sync()` (line 274) which has no visible scheduler trigger from
  main.py lifespan. The 7-day cadence claim IS true; the 5-min boot warmup
  claim appears to be aspirational. Implication: customer-facing GLBA reports
  may not populate until the next 7-day tick (worst case 6 days 23h post-deploy)
  unless an admin manually invokes `POST /api/frameworks/{glba}/sync`. **Fix:**
  either (a) verify + cite the boot-warmup path with a line:column, or (b)
  retract the 5-min claim + document that customer-visible GLBA mapping
  population requires either manual admin re-sync or up to 7-day wait.
- **P0 — Phase C has NO TaskCreate; lost to commit-body prose.** Commit body
  cites "Phase C scope" for: renderer wire-up, `evidence_framework_mappings`
  DB seed step, mapping_strength surface in PDFs. None of these have a
  TaskCreate followup or referenced task #. Per TWO-GATE rule: "P1 from
  EITHER gate MUST be closed OR carried as named TaskCreate followup items
  in the same commit." Phase C deferral is fine; losing the work is not.
- **Gate A questions in commit body — verified**:
  - Steve N/A claim: ✓ — `check_control_mappings` consumers grep returned only
    framework_sync.py (no scoring path consumes yet).
  - Maya (c)(3) clustering: ✓ — design rationale stands.
  - Carol question_bank_key leak: ✓ — verified zero consumers.
  - Coach `ON CONFLICT DO NOTHING`: ✓ — verified at framework_sync.py:446
    (`ON CONFLICT (check_id, framework, control_id) DO NOTHING`).

## P0
1. **Commit body claim "5min warmup" unverified** — only the 7-day cadence
   exists in `bg_heartbeat.py`. Either find + cite the boot-trigger
   line:column, or document the customer-visible-delay in a follow-up commit.
2. **Phase C work has no TaskCreate** — open named tasks for: (a) renderer
   wire-up for `mapping_strength` field, (b) `question_bank_key` consumer,
   (c) BAA-enforcement gate for GLBA-scope exports.

## P1
1. Add inline comment in `framework_sync.py::_seed_from_yaml` (line 419
   loop) noting that `mapping_strength` + `question_bank_key` are
   reference-only in YAML + intentionally not seeded.
2. Open Phase C scoping doc / Gate A for the BAA-enforcement-for-GLBA
   question (Maya P1) — design decision belongs in counsel-queue, not
   eng-followup.

## Recommendation
APPROVE-WITH-FIXES: content is correct + tests are strong + sweep is clean,
but commit body makes one verifiable-false claim (5min warmup) and Phase C
work is at risk of slipping out of the task ledger. Address P0s in a 1-commit
fix-forward before claiming #139 Phase B fully complete.
