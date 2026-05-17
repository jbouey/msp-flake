# #139 Phase B design — YAML coverage backfill

**Status:** DESIGN (no implementation). Fork-research only.
**Author:** research-fork (agent-a5c73e1d0bc13adec)
**Date:** 2026-05-17
**Prereqs:** Phase A shipped (`framework_allowlist.py`, `glba_templates.py`, `soc2_templates.py`)
**File touched:** `packages/compliance-agent/src/compliance_agent/frameworks/mappings/control_mappings.yaml` (only)

---

## Goal

Bring `control_mappings.yaml` per-framework coverage to the per-framework minimum that the Phase A allowlist's onboarding rule already demands (≥12 checks per framework):

- **GLBA: 0 → 12** checks gain a `glba:` block citing 16 CFR Part 314.4 subsections from the Phase A question bank.
- **SOC 2: 28 → 34** control_ids (NOT 27 as task title implied — verified count is 28; delta is **+6** to reach 34, not +7).

Brief assertion vs verified state:
```
Brief: 18 infra checks / 0 GLBA / 18 soc2 blocks / 27 soc2 control_ids
Verified: 18 / 0 / 18 / 28
```

Closing the +6 SOC2 gap is structurally trivial; the load-bearing question is the GLBA 12.

---

## GLBA 12 picks (defensible)

Authoritative subsections come from `glba_templates.py::GLBA_ASSESSMENT_QUESTIONS`. Each mapping cites at least one 16 CFR 314.4 subsection AND its question-bank `key` so a Safeguards Rule auditor walking the kit from question → check → evidence has a single chain.

| # | check_name | control_id | subsection text (paraphrased) | source key | mapping_strength | required |
|---|---|---|---|---|---|---|
| 1 | `backup_status` | **16 CFR 314.4(h)** | Incident-response plan must address recovery/business-continuity practices related to a security event (backup verification is the substrate evidence) | `admin_incident_response` | **supportive** | false |
| 2 | `encryption_at_rest` | **16 CFR 314.4(c)(3)** | "Encrypt all customer financial information held or transmitted... both at rest and in transit, using current cryptographic standards" | `tech_encryption_at_rest` | **satisfying** | true |
| 3 | `firewall_enabled` | **16 CFR 314.4(c)(2)** | Network segmentation to isolate systems containing customer financial information from other systems and the public internet where feasible | `tech_network_segmentation` | **supportive** | false |
| 4 | `antivirus_status` | **16 CFR 314.4(c)(6)** | Audit/logging/monitoring controls sufficient to detect actual and attempted attacks on or intrusions into information systems containing customer financial information | `tech_monitoring_logging` | **supportive** | false |
| 5 | `patch_status` | **16 CFR 314.4(c)(7)** | Procedures for the secure development of applications and systems, including vulnerability scanning and penetration testing at least annually | `tech_vulnerability_management` | **satisfying** | true |
| 6 | `audit_logging` | **16 CFR 314.4(c)(6)** | Audit/logging controls sufficient to detect actual and attempted attacks (this IS the canonical 314.4(c)(6) check) | `tech_monitoring_logging` | **satisfying** | true |
| 7 | `access_control` | **16 CFR 314.4(c)(1)** + **(c)(5)** | (c)(1) "Implement access controls on information systems"; (c)(5) MFA for any individual accessing customer financial information | `tech_access_controls`, `tech_mfa` | **satisfying** | true |
| 8 | `network_security` | **16 CFR 314.4(c)(2)** + **(c)(3)** | (c)(2) network segmentation; (c)(3) encryption in transit (this check verifies both segmentation evidence + TLS posture) | `tech_network_segmentation`, `tech_encryption_transit` | **satisfying** | true |
| 9 | `incident_response` | **16 CFR 314.4(h)** | Established incident response plan with goals/processes/roles/responsibilities/communication for a security event | `admin_incident_response` | **satisfying** | true |
| 10 | `prohibited_ports` | **16 CFR 314.4(c)(3)** | Cleartext protocols (FTP/Telnet/TFTP/rsh) violate the in-transit encryption mandate of (c)(3) | `tech_encryption_transit` | **satisfying** | true |
| 11 | `encrypted_services` | **16 CFR 314.4(c)(3)** | HTTPS-not-HTTP for web services is the canonical (c)(3) in-transit check | `tech_encryption_transit` | **satisfying** | true |
| 12 | `tls_web_services` | **16 CFR 314.4(c)(3)** | Alt-port web services (8080→8443) must have TLS counterparts under (c)(3) | `tech_encryption_transit` | **satisfying** | true |

**Coverage shape:** 9 of 12 are `satisfying` (auditor accepts the technical evidence as direct proof of the cited subsection). 3 are `supportive` (the check supplies evidence relevant to the cited subsection but doesn't independently close it — the auditor will want corroborating policy/procedure docs).

---

## GLBA 6 rejections (no defensible 314.4 mapping)

| check_name | rejection rationale |
|---|---|
| `time_sync` | NTP accuracy is necessary infrastructure for audit log forensics but is NOT cited in any 16 CFR Part 314.4 subsection. Including it would be auditor-defensible only as 2nd-derivative evidence under (c)(6) — too weak; map under SOC 2 CC7.2 only. |
| `integrity_monitoring` | File integrity monitoring is a NIST/ISO/CIS construct. 314.4 does not mention integrity controls specifically (the Safeguards Rule speaks to access + encryption + monitoring + IR). Including would constitute over-claiming. |
| `database_exposure` | Detects misconfigured DB services on non-server devices. Defensible only as a derivative of (c)(2) network segmentation, but the check's positive signal is "device is not a server" — too operational/inferential to publish as a GLBA-cited control. |
| `snmp_security` | SNMP v1/v2 cleartext is a real risk, but no 314.4 subsection mentions network-management protocols. Already covered indirectly via `prohibited_ports`/(c)(3) for the cleartext class — duplicate-citation would dilute the canonical mapping. |
| `rdp_exposure` | RDP-on-non-workstation is a lateral-movement risk, not a Safeguards Rule subsection. (c)(1) access controls is too generic; would weaken the (c)(1) → `access_control` canonical anchor. |
| `device_inventory` | Asset inventory completeness is a NIST CSF ID.AM-1 / CIS Control 1 concept. 314.4(b) risk assessment requires identifying risks "in each relevant area of operation" but does NOT mandate an asset inventory artifact. Mapping would be aspirational, not defensible. |

**Pattern:** The rejected 6 fall into two classes — (a) infrastructure hygiene (time_sync, integrity_monitoring, device_inventory) and (b) duplicative network detections (database/snmp/rdp). Both classes would expand GLBA coverage cosmetically but weaken the per-subsection citation chain.

---

## SOC 2 backfill (+6 control_ids across existing 18 blocks)

Current 28 → target 34. Strict rule: every new control_id cites a real AICPA TSC criterion the check produces direct evidence for.

| # | check_name | NEW control_id | rationale | category |
|---|---|---|---|---|
| 1 | `audit_logging` | **CC4.1** "Monitoring Activities — ongoing evaluations" | Audit logs ARE the ongoing-evaluation evidence stream; missing today. (Current: CC7.2, CC7.3) | Common Criteria |
| 2 | `patch_status` | **CC7.2** "System Monitoring — Anomaly Detection" | A managed patch-status feed IS an anomaly detection input (out-of-band patch state = anomaly). (Current: CC6.1, CC7.1) | Common Criteria |
| 3 | `incident_response` | **CC7.3** "Evaluation and Communication of Anomalies" | Incident-response readiness includes the evaluation+comms loop, NOT just response (CC7.4) + recovery (CC7.5). (Current: CC7.4, CC7.5) | Common Criteria |
| 4 | `antivirus_status` | **CC7.1** "System Operations — Vulnerability Detection" | AV definition currency IS a vulnerability-detection function; current only cites CC6.8 malware. (Current: CC6.8) | Common Criteria |
| 5 | `encryption_at_rest` | **C1.1** "Confidentiality — protection of confidential info" | Disk encryption is the canonical C-criterion control; today only CC6.* is cited. Adds Confidentiality TSC coverage — currently zero C-criteria are referenced anywhere in YAML. (Current: CC6.1, CC6.7) | Confidentiality |
| 6 | `integrity_monitoring` | **PI1.3** "Output Validation" | FIM detects unauthorized modification of system outputs/binaries; complements PI1.2 data integrity. (Current: CC7.1, PI1.2) | Processing Integrity |

**Coverage shape post-backfill:**
- Common Criteria: gains CC4.1, CC7.1 (additional callsite), CC7.2 (additional callsite), CC7.3 (additional callsite)
- Confidentiality: 0 → 1 (C1.1) — closes a structural gap; without this, no infra check maps to the C-criterion at all
- Processing Integrity: gains PI1.3

**Deliberately not added:**
- `time_sync` does NOT get +CC4.1 — keep CC7.2 only (avoid double-pointing the same check at correlated criteria)
- `network_security` does NOT get +CC6.6 (already there)
- No P-criterion (Privacy) mappings — infra checks don't produce direct data-subject-rights evidence; would be over-claiming

---

## Risk + Counsel framing

**GLBA mappings published in customer-facing reports MUST be defensible at a Safeguards Rule audit (16 CFR Part 314).** This design ships under Counsel Rule 1 ("no non-canonical metric leaves the building"): every emitted control reference cites either an exact 16 CFR Part 314.4 subsection (`(a)`/`(b)`/`(c)(1)`-`(c)(8)`/`(e)`/`(f)`/`(h)`/`(i)`) or — for the 2 entries citing the Privacy/Disposal rules — `16 CFR 313` / `16 CFR 682`. An FS-vertical customer's auditor can independently look up each citation in the eCFR and verify the mapping in <5 minutes.

The `mapping_strength: supportive` vs `satisfying` distinction is the load-bearing honesty knob:

- `satisfying` = "the check's evidence directly proves the cited subsection's technical requirement". Examples: `encryption_at_rest`→(c)(3), `audit_logging`→(c)(6), `access_control`→(c)(1)/(c)(5).
- `supportive` = "the check supplies relevant evidence but the subsection requires additional policy/procedural artifacts to fully close". Examples: `backup_status`→(h) (backup verification is one input to IR readiness, not the whole IR program); `firewall_enabled`→(c)(2) (host firewall is one layer of segmentation, not the whole segmentation posture).

Auditors care about this distinction. Calling a `supportive` mapping `satisfying` is the kind of over-claim that turns a clean audit into a finding — and at enterprise scale (Counsel Rule 1) the substrate is the source of truth. The YAML schema MUST carry the field; the report generator MUST surface it; the auditor-kit MUST disclose it. **Without the strength flag, every GLBA mapping is implicitly published as `satisfying` — which is false for ≥3 of the 12 picks.** This is non-negotiable.

**No mappings ship that we'd refuse to defend.** The 6 rejections above are deliberate: each one is a check that an honest auditor would push back on if cited under 314.4 with no qualifier.

---

## YAML schema extension

Current per-control_id shape (from `backup_status`):
```yaml
- control_id: "164.308(a)(7)(ii)(A)"
  control_name: "Data Backup Plan"
  category: "Administrative Safeguards"
  subcategory: "Contingency Plan"
  required: true
```

Proposed extension — add OPTIONAL `mapping_strength` field (defaults to `satisfying` when absent for backward compatibility with existing 295 control_ids across all 11 frameworks; only NEW GLBA + flagged SOC2 entries set it explicitly):
```yaml
- control_id: "16 CFR 314.4(h)"
  control_name: "Incident Response Plan — Recovery Evidence"
  category: "Administrative Safeguards"
  required: false
  mapping_strength: supportive  # NEW — auditor-defensible qualifier
  question_bank_key: admin_incident_response  # NEW — back-pointer to glba_templates.py
```

`question_bank_key` is the substrate-side closure of Counsel Rule 1's "canonical source declared" — it lets the report-renderer cite the customer's own assessment answer alongside the technical evidence. SOC2 entries get `soc2_reference` back-pointers symmetrically (12 of the 18 existing soc2 blocks have an obvious 1:1 with a `SOC2_ASSESSMENT_QUESTIONS` row; the others get `null`).

---

## Test plan (source-shape gates)

Land in `mcp-server/central-command/backend/tests/` (where Phase A's `test_framework_allowlist_lockstep.py` lives):

```
tests/test_glba_coverage_minimum.py
  - test_glba_coverage_at_least_12_checks
      AST-parse YAML; assert exactly 12 checks have a glba: block (ratchet baseline 12, allow growth)
  - test_glba_every_control_id_cites_real_cfr_subsection
      Regex: ^16 CFR (314\.4\([a-i]\)(\(\d+\))?|313(\.\d+)?|682(\.\d+)?)$
      No "16 CFR 314" (must have subsection); no "GLBA-1" / "GLBA-2" placeholder IDs
  - test_glba_question_bank_key_resolves
      Every glba.question_bank_key value EXISTS in GLBA_ASSESSMENT_QUESTIONS keys
  - test_glba_mapping_strength_in_allowlist
      Field value ∈ {"satisfying", "supportive"} when present; reject "weak"/"strong"/"required"
  - test_glba_supportive_implies_required_false
      mapping_strength=supportive => required must be false (honesty invariant)

tests/test_soc2_coverage_minimum.py
  - test_soc2_coverage_at_least_34_control_ids
      Sum len(soc2 blocks) across all checks >= 34 (ratchet baseline 34)
  - test_soc2_every_control_id_matches_tsc_pattern
      Regex: ^(CC[1-9]\.[0-9]|A[1-2]\.[0-9]|C[1-2]\.[0-9]|PI1\.[0-9]|P[1-8]\.[0-9])$
  - test_soc2_at_least_one_confidentiality_mapping
      At least 1 control_id matches ^C[1-2]\. — closes the structural gap

tests/test_framework_yaml_schema.py  (consolidates schema-shape gates)
  - test_mapping_strength_field_when_present_is_lowercase
  - test_question_bank_key_is_snake_case_or_null
  - test_no_duplicate_control_ids_within_same_check_same_framework

# Optional / nice-to-have
tests/test_framework_coverage_parity.py
  - test_framework_min_12_checks (per Phase A onboarding rule docstring)
      For framework in SUPPORTED_FRAMEWORKS: count checks with that framework block >= 12
```

All tests are pure-Python source-shape gates (no DB, no asyncpg) — runnable in the pre-push fast lane.

---

## Substrate / downstream coupling (NOT in Phase B scope, flagged for Phase C)

- `framework_sync.py::_seed_framework_from_yaml` will need to (a) tolerate the new `mapping_strength` + `question_bank_key` columns, (b) populate them into `evidence_framework_mappings` so the report renderer can surface them. Pure-additive — existing rows stay valid via NULL defaults. NOT this PR.
- Customer-facing GLBA report renderer (separate task #?) MUST display `[supportive]` qualifier in published artifacts; otherwise this whole strength-flag mechanism is decoration.
- Auditor-kit README needs a one-paragraph addition explaining `mapping_strength` semantics (auditor education).

---

## Gate A questions for the fork's adversarial 4-lens review

**Steve (Principal SWE):** GLBA → SOC 2 → HIPAA frequently overlap on the same underlying control (access, encryption, IR). The current YAML lets ONE check (`access_control`) map to ALL THREE frameworks independently — is the resulting report a triple-counted score, or do we de-dupe by infrastructure check? If de-duped, the proposed `mapping_strength: supportive` flag needs to flow through the scoring function or it's invisible. If triple-counted, the customer sees inflated scores. Which is it today, and does Phase B make it worse?

**Maya (HIPAA/legal):** Of the 12 GLBA picks, the 3 marked `supportive` (backup_status→(h), firewall_enabled→(c)(2), antivirus_status→(c)(6)) are honest. But Maya's question: any of the 9 marked `satisfying` weak enough that a §164.514-style "minimum necessary" or §-equivalent under GLBA disclosure could be argued? Specifically: `prohibited_ports`/`encrypted_services`/`tls_web_services` all cite (c)(3) encryption-in-transit — that's three citations for the same subsection. Is that legally OK (defense-in-depth evidence) or does it look like padding (auditor red flag)?

**Carol (security/threat):** Per Counsel Rule 7 (no unauth context), the GLBA control-id strings (e.g. "16 CFR 314.4(h)") are themselves PHI-free and safe to surface in unauthenticated channels. BUT — does the proposed `question_bank_key` field leak the question text into customer-facing artifacts? If the renderer joins on question_bank_key and inlines the question text, we may be publishing assessment internals to opaque-mode email templates. Phase B should pin question_bank_key as REFERENCE-ONLY (not auto-inlined).

**Coach (consistency):** Phase B is a content task. Does it need its own `framework_sync` DB seed step (i.e. a migration to backfill `evidence_framework_mappings` rows for the new 12 GLBA + 6 SOC2 entries) — and if so, is that in scope or deferred to Phase C? The current docstring on `_seed_framework_from_yaml` says it runs on backend boot and processes the YAML fresh — meaning Phase B + a backend restart MIGHT be sufficient. But if it's a one-shot seed (idempotent only on framework_id change), we ship Phase B and the customer-facing reports see ZERO new mappings until a manual `framework_sync` invocation. Need a 5-line grep of `_seed_framework_from_yaml` to answer this BEFORE landing.

---

## Out-of-scope (explicit deferrals)

1. **Implementation.** This is design only.
2. **Schema-extension landing in `evidence_framework_mappings` table.** Phase C (separate task).
3. **Report renderer surfacing `mapping_strength` in PDFs / dashboards.** Phase C.
4. **Backfill of `question_bank_key` for existing 28 SOC2 entries that map cleanly to `SOC2_ASSESSMENT_QUESTIONS`.** Cosmetic enhancement, not coverage; Phase D.
5. **HIPAA mapping audit.** Already at 11+ control_ids per check via the 2025 NPRM citations; out of scope here.

---

## Verdict shape (for the implementing session)

**APPROVE-WITH-FIXES** pending Gate A fork verdict. The 12 GLBA picks are defensible per the 16 CFR Part 314.4 citation table; the 6 SOC2 backfills are tightly scoped to real AICPA TSC criteria. The two open architectural questions (Steve's de-dupe and Coach's `_seed_framework_from_yaml` behavior) MUST be resolved before the YAML PR opens — they determine whether Phase B has any customer-visible effect or whether it ships dark.
