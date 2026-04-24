# AGENTS.md — docs/

Scoped to documentation conventions. Root invariants live in [`/AGENTS.md`](../AGENTS.md) and [`/CLAUDE.md`](../CLAUDE.md) — read those first.

## Doc taxonomy — what goes where

| Artifact | Purpose | Lives under | Naming |
|---|---|---|---|
| **ADR** (Architecture Decision Record) | A durable decision + its reasoning. Written BEFORE or AS the decision lands. | `docs/adr/` | `YYYY-MM-DD-short-slug.md` |
| **Post-mortem** | Incident write-up after a Sev-2+ event, CI regression > 1 h, silent-corruption, or manual DB override. | `docs/postmortems/` | `YYYY-MM-DD-short-slug.md`, indexed in `INDEX.md` |
| **Runbook** | Step-by-step procedure for a known failure class. Written for someone on-call at 2 am. | `docs/runbooks/` | `SUBJECT_RUNBOOK.md` or `RB-<AREA>-<VERB>.md` |
| **SOP** (Standard Operating Procedure) | Recurring operational task the team performs regularly (onboarding, evidence-bundle verification, daily ops). | `docs/sop/` | `SOP-NNN_TITLE.md` or `EMERG-NNN_TITLE.md` for incident-adjacent SOPs |
| **Reference / architecture** | Stable system explanation (ARCHITECTURE.md, DATA_MODEL.md, HIPAA_FRAMEWORK.md). | `docs/` root is permitted ONLY for these stable root-level references | SCREAMING_SNAKE.md |

## Review cadence

| Artifact | Reviewed |
|---|---|
| ADR | At write time by the SWE round-table. Immutable after acceptance — supersede with a new ADR, do not edit. |
| Post-mortem | 24 h draft deadline, weekly async by on-call, quarterly sync by PM + tech lead, annual bulk re-read. See [`postmortems/PROCESS.md`](./postmortems/PROCESS.md). |
| Runbook | At every use. If the steps didn't match reality, edit before the next on-call. Runbook updates are not "nice to have" — they're the exit criterion of every incident. |
| SOP | Annually. Explicit `Review Cycle:` frontmatter, explicit owner. |

## Choosing the right artifact

- A failure just happened and we recovered → post-mortem (with keep-list).
- The post-mortem produced a decision → ADR referenced from the post-mortem's "Related" section.
- The post-mortem produced a repeatable procedure → runbook referenced from the post-mortem's "Related" section.
- A recurring operational task (billing run, monthly audit packet, client onboarding) → SOP.
- A one-off investigation, session log, or planning doc → `.agent/sessions/` (NOT under `docs/`).

## Do NOT

- **Do NOT add new loose markdown files at `docs/` root.** They belong in a subdirectory. New ADRs go in `adr/`, new runbooks go in `runbooks/`, new SOPs go in `sop/`, incident write-ups go in `postmortems/`. If you genuinely need a new top-level subject, open a PR that also adds it to the root [`AGENTS.md`](../AGENTS.md) routing table.
- Do NOT delete or edit a published post-mortem. Corrections go at the end in a `## Correction YYYY-MM-DD` section.
- Do NOT rename an ADR after it's merged — external references may cite the filename.
- Do NOT write technical content in this file or the root AGENTS.md. Both are pure routing. Technical detail lives in the linked doc.

## Three invariant documents (never stale)

1. [ADR 2026-04-24 — Source-of-Truth Hygiene](./adr/2026-04-24-source-of-truth-hygiene.md)
2. [Post-mortem PROCESS.md](./postmortems/PROCESS.md)
3. [Root CLAUDE.md](../CLAUDE.md)

## When in doubt

Prefer reading `docs/runbooks/README.md`, `docs/adr/` (by date), `docs/postmortems/INDEX.md` before adding anything. Most "new doc" instincts are covered by an existing one.
