# frontend_field_undefined_spike

**Severity:** sev2
**Display name:** Frontend reading fields the backend no longer returns

## What this means (plain English)

The dashboard in real users' browsers is asking the backend for a field
that the backend is no longer returning. Multiple sessions (from
multiple browser IPs) have all hit the same drifted field in the last
5 minutes, which means:

- **Not one user's flaky cache.** At least 2 distinct sessions, so
  it's a real contract break, not someone on a stale page.
- **Not theoretical.** The frontend code is SHIPPED and the backend is
  SHIPPED and they disagree on what a response contains.

This is the last-line-of-defense invariant in the Session 210 API
reliability plan. The pre-commit Pydantic check (Layer 6) and the
OpenAPI codegen (Layer 1) should have caught this earlier. If this
invariant fires, a layer upstream leaked.

## Root cause categories

- **Field was removed from the Pydantic model** without frontend updates
  matching. The pre-commit check should block this; a `BREAKING:`
  opt-in was probably used.
- **JSONB sub-field drift.** Type is `Dict[str, Any]` on both sides,
  so neither codegen nor contract checks catch it. A key in the dict
  got renamed/removed.
- **Enum value drift.** Frontend `switch` doesn't handle a new enum
  value; renders nothing. (Actually not a FIELD_UNDEFINED here, but
  often observed together.)
- **Conditional response shape.** Backend returns field only when a
  certain condition is met; some page hits that branch and the
  frontend assumes the field is always there.

## Immediate action

1. Look at `details.endpoint` + `details.field_name` in the violation
   row on `/admin/substrate-health`. That's the broken contract.

2. Find the Pydantic model:
   ```
   grep -rn "class.*BaseModel" mcp-server/central-command/backend/ | \
     xargs grep -l "<endpoint fragment>"
   ```

3. Check whether the field is still there. Three outcomes:
   - **Field was intentionally removed** → update the frontend
     component that still reads it, then:
     ```
     cd mcp-server/central-command/frontend && npm run generate-api
     ```
     Commit the updated `api-generated.ts` and the component change.
   - **Field is present but not populated in this code path** → the
     Pydantic response declares the field as `Optional[X] = None` but
     the handler didn't set it. Fix the handler.
   - **Field was never there** → the frontend has an outdated
     assumption. Remove the read or add a proper fallback.

4. Regenerate + commit the OpenAPI schema so future prevention layers
   work as intended:
   ```
   python3 scripts/export_openapi.py
   ```

## Verification

- Invariant auto-resolves when events stop arriving for the
  (endpoint, field) pair — either the field's back, or the frontend
  stopped reading it.
- Sanity query:
  ```sql
  SELECT COUNT(*), COUNT(DISTINCT ip_address) AS sessions
    FROM client_telemetry_events
   WHERE event_kind = 'FIELD_UNDEFINED'
     AND recorded_at > NOW() - INTERVAL '5 minutes'
     AND endpoint = :ep
     AND field_name = :field;
  ```

## Escalation

If the same (endpoint, field) continues firing >10/5min for more than
30 minutes after the apparent fix, either:
- The fix didn't actually deploy (check `/api/version` runtime_sha),
- Multiple components read the same missing field and you only fixed
  one,
- The frontend is cached; advise customers to hard-refresh or wait for
  the service worker to roll over.

## Related runbooks

- Layer 6 + Layer 1 prevention — if this fires, those layers need a
  hardening pass. Look for `BREAKING:` commits in the last 48 hours
  or forced regenerations of `api-generated.ts` without corresponding
  component updates.
- `evidence_chain_stalled.md` — different class (evidence not API),
  but same "silent degradation" pattern.

## Change log

- **2026-04-24** — initial. Shipped with Session 210 Layer 3 of
  enterprise API reliability. Fires on FIELD_UNDEFINED events from
  `apiFieldGuard.requireField` in the frontend.
