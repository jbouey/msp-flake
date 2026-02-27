# Session 130 - HIPAA Doc Upload, Module Guidance, Companion CSRF, Learning Loop Fix

**Date:** 2026-02-25
**Started:** 00:07
**Previous Session:** 129

---

## Goals

- [x] Wire document uploads into compliance scoring (upload = module evidence)
- [x] Fix Learning Loop 500 error (PromotionCandidate.description nullable)
- [x] Policy template preview + download + inline content view
- [x] Guidance captions on all 10 HIPAA modules (What is this? / How to complete it)
- [x] Widen guidance blocks + verify companion portal visibility
- [x] Fix companion notes CSRF 403 (missing X-CSRF-Token header)

---

## Progress

### Completed

1. **Document upload → compliance scoring** (7bf4282)
   - Added `hipaa_documents` count query to overview endpoints in `hipaa_modules.py` and `companion.py`
   - Modules with docs but no structured data get 100% score (upload = evidence provided)
   - Added `documents` field to API response + `DOC_KEY_MAP` in `ClientCompliance.tsx`
   - Module status shows "N Docs" badge when docs exist but no structured data

2. **Learning Loop 500 fix** (0e2cddf)
   - Root cause: `PromotionCandidate.description: str` in models.py but DB had NULL values
   - Fixed: `Optional[str] = None` in models.py + `or ""` coalescing in routes.py

3. **Policy template preview + download** (f5e3498)
   - Added `GET /policies/templates` (list all 8) and `GET /policies/templates/{key}` (full content)
   - Template cards with Preview/Download/Adopt buttons in PolicyLibrary.tsx

4. **Policy templates inline view** (e007a44)
   - Replaced modal Preview with inline View/Collapse toggle
   - Template content cached in state, expandable per-card

5. **Guidance captions on all 10 modules** (731ccc1)
   - Added teal "What is this?" + "How to complete it" blocks to all 10 compliance module .tsx files
   - Written in near-lay language for office managers, still industry-relevant
   - Consistent styling: `bg-teal-50/60 rounded-2xl border border-teal-100`

6. **Widen guidance blocks** (4443e6d)
   - Changed from `p-4 rounded-xl` to `px-6 py-5 rounded-2xl` with `leading-relaxed`
   - Companion portal shares same components (CompanionModuleWork.tsx lazy-imports) — no changes needed

7. **Companion notes CSRF fix** (0f5be62)
   - Root cause: `/api/companion/` NOT in CSRF exempt prefixes (unlike `/api/client/`)
   - `useCompanionApi.ts` POST/PUT/DELETE helpers didn't include `X-CSRF-Token` header
   - Added `getCsrfToken()` + `csrfHeaders()` functions, spread into all state-changing requests

### Blocked

- Companion 404 on `/api/companion/clients/{orgId}/policies/templates/{key}` — template endpoints exist on client router only, companion lazy-imports use companion apiBase which routes don't match

---

## Files Changed

| File | Change |
|------|--------|
| `backend/hipaa_modules.py` | Document count query + doc-based scoring + template list/detail endpoints |
| `backend/companion.py` | Document count query + doc-based scoring in `_compute_overview()` |
| `backend/models.py` | `PromotionCandidate.description: Optional[str] = None` |
| `backend/routes.py` | `c.get("description") or ""` null coalescing |
| `frontend/src/client/ClientCompliance.tsx` | `documents` field, `DOC_KEY_MAP`, doc-based module status |
| `frontend/src/client/compliance/PolicyLibrary.tsx` | Template cards, inline View/Collapse, guidance caption |
| `frontend/src/client/compliance/SRAWizard.tsx` | Guidance caption |
| `frontend/src/client/compliance/BAATracker.tsx` | Guidance caption |
| `frontend/src/client/compliance/TrainingTracker.tsx` | Guidance caption |
| `frontend/src/client/compliance/IncidentResponsePlan.tsx` | Guidance caption |
| `frontend/src/client/compliance/ContingencyPlan.tsx` | Guidance caption |
| `frontend/src/client/compliance/WorkforceAccess.tsx` | Guidance caption |
| `frontend/src/client/compliance/PhysicalSafeguards.tsx` | Guidance caption |
| `frontend/src/client/compliance/OfficerDesignation.tsx` | Template download button + guidance caption |
| `frontend/src/client/compliance/GapWizard.tsx` | Guidance caption |
| `frontend/src/companion/useCompanionApi.ts` | CSRF token extraction + headers on POST/PUT/DELETE |

## Commits

| Hash | Message |
|------|---------|
| 7bf4282 | feat: document upload marks module complete — no LLM, no BAA needed |
| 0e2cddf | fix: PromotionCandidate description nullable — learning loop 500 error |
| f5e3498 | feat: policy template preview + download — guidance before adoption |
| e007a44 | fix: policy templates show content inline — View/Collapse replaces modal |
| 731ccc1 | feat: guidance captions on all 10 HIPAA compliance modules |
| 4443e6d | fix: widen guidance blocks — more padding, relaxed line height |
| 0f5be62 | fix: companion notes CSRF — add X-CSRF-Token to POST/PUT/DELETE |

---

## Next Session

1. Mirror policy template endpoints on companion router (fix 404 on `/api/companion/clients/{orgId}/policies/templates/`)
2. Test full document upload flow end-to-end (upload → list → download → delete)
3. Create MinIO `hipaa-documents` bucket on VPS if not already done
4. Companion portal UX testing — verify notes CRUD, module navigation, guidance display
5. Consider document retention policy for soft-deleted records
