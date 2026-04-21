# installed_but_silent

**Severity:** sev1
**Display name:** Install ran but installed system never phoned home

## What this means (plain English)

TODO — 2–4 sentences, operator audience, not engineer.

## Root cause categories

- TODO — most common cause
- TODO
- TODO

## Immediate action

- If the **Run action** button exists on the panel: TODO describe.
- Otherwise: run

  ```
  fleet_cli ... --actor-email you@example.com --reason "..."
  ```

## Verification

- Panel: invariant row should clear on next 60s tick.
- CLI: TODO query.

## Escalation

TODO — when NOT to auto-fix. Signals that suggest a real security event.

## Related runbooks

- TODO

## Change log

- 2026-04-21 — generated — stub created
