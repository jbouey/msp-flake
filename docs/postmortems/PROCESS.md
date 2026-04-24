# Post-Mortem Process

**Owner:** SRE / on-call engineer
**Review cadence:** quarterly (PM + tech lead read the previous quarter's post-mortems, elevate top-3 recurring themes into next-quarter planning)
**Template:** below

## When to write one

A post-mortem is **required** within 24 hours of:

- Any Sev-2+ outage lasting > 30 minutes.
- Any CI regression that blocks `main` deploys > 1 hour.
- Any customer-visible error rate spike (e.g., `evidence_chain_stalled` sev1 firing).
- Any silent-data-corruption incident regardless of duration.
- Any manual-operator-override on production DB (e.g., rescue SQL insert into `api_keys`).
- Any incident where the on-call engineer couldn't resolve via the documented runbook.

**Optional** for: Sev-3 noise, flaky tests resolved by rerun, transient 5xx under 5 min with auto-recovery.

## File naming + location

```
docs/postmortems/YYYY-MM-DD-<short-slug>.md
```

- `YYYY-MM-DD` is the date the incident STARTED (not when you wrote the doc).
- `<short-slug>` is 2–5 kebab-case words, e.g. `v40-appliance-brick-class` or `pg-disk-full-postgres-panic`.
- If two incidents share a day, append `-a`, `-b`, etc.

Add the file to `docs/postmortems/INDEX.md` in reverse-chronological order the moment you create it. Don't wait for the post-mortem to be "finished" — the INDEX entry is the first thing.

## Template

Use the structure below exactly. Every heading is required. `TBD` is acceptable in draft; fill within 24 h.

```markdown
# Post-Mortem: <title>

**Incident ID:** YYYY-MM-DD-<slug>
**Severity:** sev1 / sev2 / sev3
**Status:** draft / published / action-items-closed
**Author:** <name or handle>
**Published:** YYYY-MM-DD
**Duration:** <hh:mm> (start → recovery)

## Summary

<Two sentences. What happened, what the impact was. No technical jargon yet —
someone reading JUST this block should know whether they care.>

## Impact

- <Who was affected: customers / site IDs / fleet counts>
- <What they saw: 401 storm / blank dashboard / bricked appliance>
- <Downstream effects: evidence chain paused / audit trail gap / SLA breach>

## Timeline (UTC)

<Minute-by-minute, the narrative of the incident. Include external events
(commits, deploys, customer reports) and detection points.>

- `HH:MM` — <event>
- `HH:MM` — <event>
- ...

## Contributing factors

<Numbered. Each factor = one line, class-labeled. Classes: architectural,
procedural, tooling, observability, external.>

1. [architectural] <factor>
2. [procedural] <factor>
3. [tooling] <factor>
...

## Root cause

<ONE sentence. If you need two, write two post-mortems — there were two
incidents.>

## Detection

- **Who/what detected it:** <operator / Prometheus alert / customer ticket / Claude Code session>
- **How long from start to detection:** <hh:mm>
- **What SHOULD have detected it first:** <invariant that didn't exist, alert that was mistuned>

## Recovery

- **What we did:** <numbered steps>
- **What would have been faster:** <hindsight — the 2-line SQL, the missing CLI>
- **Total recovery time from detection:** <hh:mm>

## Action items

<Numbered. Each item: owner, due date, tracking ID. No action item without
an owner + date. "Someone should look at this" is not an action item.>

| # | Item | Owner | Due | Tracked in |
|---|------|-------|-----|------------|
| 1 | <item> | <name> | YYYY-MM-DD | Task #N or ADR # or PR # |
| 2 | <item> | <name> | YYYY-MM-DD | Task #N |

## What worked (keep-list)

<Explicit. "The invariant engine correctly detected the symptom." "The
Ed25519 evidence chain held across the outage." Don't let the post-mortem
imply the whole system failed — it didn't.>

## Related

- Link to `docs/adr/<id>.md` if this post-mortem produced a decision record.
- Link to `docs/runbooks/<task>.md` if this caused a runbook update.
- Link to `.agent/sessions/<file>.md` if there's a richer session log.
- Link to predecessor incidents with recurring themes.

## References

- <commits, PRs, dashboards, Prometheus screenshots, customer tickets>
```

## Quality bar

- **Length:** aim for 2–4 screens. Less = incomplete. More = novel, go back and compress.
- **Blame-neutral:** describe actions, not agents. "The commit was pushed without full local pytest" not "Alice forgot to run tests."
- **Action items must be SMART:** specific, measurable, assigned, realistic, time-boxed. A line without owner + due date is not an action item.
- **Linkable:** incident ID is cited in commits, ADRs, runbooks that reference the post-mortem. Keep IDs stable.

## What makes a post-mortem BAD

- Rhetorical flourishes ("a perfect storm", "cascading failures")
- Grand-unified-theory narratives that absorb three different failure classes into one story
- Action items without dates
- Missing the keep-list (implies everything failed, which is never true)
- Published more than 48 hours after the incident (stale context, fading memory)

## Review cadence

- **Weekly (async):** on-call engineer reads all post-mortems published that week, comments on anything ambiguous.
- **Quarterly (sync, 1 h):** PM + tech lead read last quarter's post-mortems, tag recurring themes, pick top-3 as next-quarter planning inputs.
- **Annual (1 day):** all post-mortems re-read in bulk. Identify patterns that span multiple quarters. Rewrite relevant ADRs.

## Closure

An action item is **closed** when the tracked work lands (commit merged, runbook updated, ADR written). Update the post-mortem's "Status" field to `action-items-closed` when the last item closes.

Do not delete or edit a published post-mortem. If something was wrong, add a `## Correction YYYY-MM-DD` section at the end. Permanent record.
