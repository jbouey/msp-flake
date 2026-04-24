# Post-Mortems — chronological index

Process + template: [PROCESS.md](./PROCESS.md).

Newest first. One line per incident. The post-mortem file itself has the full narrative.

| Date | Severity | Status | Incident | Duration |
|---|---|---|---|---|
| 2026-04-23 | sev2 | action-items-open | [v40.x appliance brick class (three source-of-truth splits)](./2026-04-23-v40-appliance-brick-class.md) | 8 h |
| 2026-04-13 | sev2 | action-items-closed | [Migration 162 auto-apply outage (fleet-wide deploy failure, 90 min)](./2026-04-13-migration-162-outage.md) | 1 h 30 m |

## Recurring themes (quarterly review)

Updated at each quarterly review. Latest review: TBD.

## How to add a new entry

1. Write the post-mortem at `YYYY-MM-DD-<slug>.md` using [PROCESS.md](./PROCESS.md) template.
2. Prepend a row to the table above (newest first).
3. Link from relevant ADRs, runbooks, commits that touch the same system.
4. Mark **draft** initially; update **Status** column when published (≤ 24 h) and again when action items close.
