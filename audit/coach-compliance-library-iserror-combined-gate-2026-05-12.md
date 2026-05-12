# Combined Gate A + Gate B — ComplianceLibrary `isError` banner (Task #127)

- **Date:** 2026-05-12
- **Reviewer:** Fork (Opus 4.7 · 1M ctx, fresh context window)
- **Subject:** `mcp-server/central-command/frontend/src/pages/ComplianceLibrary.tsx` lines 342-360 (uncommitted patch)
- **Scope:** Combined Gate A (pre-execution) + Gate B (pre-completion). Justified by tiny diff (~18 LoC), frontend-only, UX-only.
- **Companion task:** #120 PR-B (planned `Depends(require_admin)` on `framework_sync.py:trigger_sync_all` + `trigger_sync_one`). PR-B is BLOCKED on its own P0s and not shipped; this banner ships independently so the error surface exists once PR-B lands.

---

## VERDICT: APPROVE-WITH-FIXES (1 P0, 0 P1, 2 P2)

P0 must be closed before commit. P2s carried as followup tasks.

---

## P0 — `bg-system-red`, `border-system-red`, `text-system-red` are UNDEFINED Tailwind classes (Steve #3)

**Evidence:**

- `tailwind.config.js` lines 10-75 enumerate the theme palette. Red-family colors defined:
  - `ios.red: '#FF3B30'`
  - `health.critical: '#FF3B30'`
  - `level.l3: '#FF3B30'`
  - boxShadow `glow-red`
- **No `system-red` color is defined.** Verified with `grep -rn "system-red"` across `tailwind.config.js`, `src/index.css`, all `src/**/*.tsx` — the patch site (line 350) is the ONLY occurrence in the entire repo.

**Impact:**

Tailwind's JIT compiler will not emit a rule for `bg-system-red/10`, `border-system-red/30`, or `text-system-red`. Result: banner renders as:
- transparent background (no `bg-` rule emitted),
- transparent border (no `border-color` rule emitted; `border` utility alone yields the default `currentColor` which inherits → likely `text-label-primary` via cascade, making the border ALSO invisible against the page),
- text color falls through to inherited `text-label-primary` (white-ish in dark mode, near-black in light mode) — INDISTINGUISHABLE from a normal page paragraph.

In other words: **the banner is invisible.** A non-admin user clicking "Sync All" after task #120 PR-B lands will see the spinner stop and NOTHING ELSE — the exact failure mode this patch claims to fix. This is a worse outcome than the pre-patch state because the developer will believe the fix is in place.

**Sibling-pattern evidence (Coach #7):**

Three established alert-banner patterns in the repo, none use `system-red`:

| File | Class shape |
| --- | --- |
| `partner/PartnerWeeklyRollup.tsx:134` | `bg-rose-50 border-rose-200 text-rose-700` |
| `partner/PartnerAttestations.tsx:1124` | `bg-rose-50 border-rose-200 text-rose-700` |
| `components/composed/DangerousActionModal.tsx:289` | `bg-red-50 border-red-200 text-red-700` |
| `portal/PracticeHomeCard.tsx:299` | `text-amber-300` |

**REQUIRED FIX:** Pick ONE of:

1. **(Preferred — minimum-surprise sibling parity)** Match `PartnerAttestations.tsx` pattern:
   `bg-rose-50 border-rose-200 text-rose-700`
2. **(Theme-aware option)** Use the defined `health.critical` token:
   `bg-health-critical/10 border-health-critical/30 text-health-critical`
   — verified-defined in tailwind.config.js:53. Will render correctly in both light + dark mode.

Recommendation: **option 2** — `health.critical` is the canonical theme token for failure state and is already used by `level.l3` + the score thresholds in `constants/status.ts`. `bg-rose-50` is a Tailwind default color that does not adapt to dark mode (will be near-white in dark theme, with `text-rose-700` near-black on near-white = readable but inconsistent with the rest of the page's glass aesthetic).

---

## P2 — Race-case error-message preference (Maya #5)

If `syncAllMutation.isError && syncOneMutation.isError` simultaneously, only `syncAllMutation.error.message` renders. Acceptable for sprint follow-up — the race is extremely rare (user would need to fire both within ms of each other and have both fail simultaneously). Note as a TaskCreate followup if desired; not blocking.

## P2 — eslint not run as part of this review

Per brief, lint is deferred to CI. Coach #8 — skipped per brief. CI will catch syntactic issues. (P2, not blocking.)

---

## Lens findings — passes

### Steve

1. **JSX syntactic validity:** PASS. The `{(condA || condB) && (<div>...</div>)}` shape is valid TSX. The ternary nesting on lines 352-358 is parenthesized correctly. No missing closing tags. Already type-checks at the source level (the file compiled before this patch and the new JSX matches existing patterns at lines 311-340).

2. **React Query type story:** PASS. `useMutation` from `@tanstack/react-query` types `mutation.error` as `TError | null` where `TError` defaults to `Error`. The hooks at `useFleet.ts:1202-1224` do not override the generic, so `error: Error | null` is the inferred type. `instanceof Error` is sound — and because `fetchApi` (via `api.ts:139`) throws `ApiError extends Error` (api.ts:12-17), the instanceof check will be TRUE for every API failure path. The fallback string ('Framework sync failed. Refresh and try again.') only triggers if a non-Error is thrown (e.g. a string rejection from a custom mutationFn) — defensive and correct.

3. **CSS classes:** **FAIL — see P0 above.**

### Maya

4. **`role="alert"`:** PASS. `role="alert"` is the correct ARIA live region for an error banner that should be announced immediately to screen readers. Matches established sibling pattern (4 prior callsites). NVDA / VoiceOver / JAWS will announce on insertion.

5. **Simultaneous-error race:** Deferred — see P2 above.

### Carol

6. **Information disclosure via `error.message`:** PASS. `parseApiErrorMessage` in `utils/api.ts:41-51` returns one of:
   - The raw `detail` string if backend supplied one (FastAPI convention — these are intentionally user-safe like `"CSRF validation failed. Refresh the page and try again."` or `"You do not have permission to perform this action."`),
   - Generic fallback strings keyed by status code (500/403/404/429), no stacktrace, no DB error text, no internal path information.
   - For the 403 case (the precise scenario task #120 PR-B creates), the message will be **"You do not have permission to perform this action."** — clean, user-actionable, no information leak.
   No PHI, no internal hostnames, no SQL fragments can reach this banner via the documented `parseApiErrorMessage` contract. Backend would have to violate the FastAPI `HTTPException(detail=...)` discipline to leak — that's a backend-side concern, not a frontend display concern.

### Coach

7. **Sibling pattern parity:** PARTIAL — `role="alert"` matches siblings; class shape DIVERGES from siblings (see P0).

8. **eslint:** SKIPPED per brief; P2.

9. **Full pre-push backend test sweep:** ATTEMPTED — `.githooks/full-test-sweep.sh` invocation was denied by the sandbox permission layer in this session. Per brief direction ("frontend change so backend sweep should be no-op"), the backend touchpoints for this diff are zero — no `.py` files, no migrations, no SQL — so the sweep would exercise its full ratchet baseline against an unchanged backend tree. The fast-lane `tests/test_pre_push_ci_parity.py SOURCE_LEVEL_TESTS` set similarly contains no tests that read frontend `.tsx` files. **Risk delta from skipping the sweep on this diff: zero.** Citing this as best-effort; full CI on push will execute it server-side regardless.

---

## Commit-readiness checklist

- [ ] **P0 closed:** replace `system-red` → `health-critical` (or `rose-*` if user prefers sibling-parity)
- [x] Gate A approves design (banner shape, ARIA role, error.message handling)
- [x] Gate B approves implementation (TSX valid, types sound, no info-disclosure)
- [ ] Re-run lint locally OR accept CI gating
- [ ] Cite this verdict file in commit body

**Once the P0 is closed, this patch is APPROVE.** No re-review required for the color-class swap alone — it is a literal find/replace on three tokens in one file.

---

## Followup tasks suggested (not blocking)

1. **TaskCreate:** "ComplianceLibrary banner — surface both syncAll + syncOne errors simultaneously when both mutations error in the same tick" (P2 — Maya #5 race)
2. **TaskCreate:** "Frontend lint: add CI gate that fails on undefined Tailwind class names" — this exact failure class (`bg-system-red` typo / unmapped token) would be caught structurally by `eslint-plugin-tailwindcss` `no-custom-classname` rule against the resolved theme. Closes a recurring class.
