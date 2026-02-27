# Session 140 — Skill Docs + agents.md Restructure

**Date:** 2026-02-27
**Previous Session:** 139

---

## Goals

- [x] Research expert Go patterns across the internet
- [x] Research advanced NixOS patterns (lesser-known features)
- [x] Scan SkillsJars.com for relevant skills
- [x] Create Go skill doc from research
- [x] Create NixOS advanced skill doc from research
- [x] Restructure knowledge index per Vercel agents.md findings
- [x] Integrate obra/superpowers debugging + verification methodology

---

## Progress

### Completed

- **Go skill doc** (`.claude/skills/docs/golang/golang.md`) — concurrency, pgx, slog, testing, security, production patterns. 5 research agents compiled.
- **NixOS advanced skill doc** (`.claude/skills/docs/nixos/advanced.md`) — module system, sops-nix, impermanence, deploy-rs, disko, systemd hardening. 2 research agents compiled.
- **Workflow skill doc** (`.claude/skills/docs/workflow/workflow.md`) — systematic debugging (4-phase, 95% fix rate) + verification-before-completion from obra/superpowers.
- **Vercel agents.md restructure** — compressed index with critical snippets moved into CLAUDE.md (always in context). Skills requiring invocation: +0pp. Always-in-context index: +47pp per Vercel evals.
- **CLAUDE.md rules** — added root-cause-first debugging and evidence-before-claims verification as always-loaded rules.
- **SkillsJars scan** — no Go/NixOS/Python skills. Catalog dominated by Java/Spring Boot and marketing.
- **INDEX.md** — slimmed from full pipe table to doc map pointer (CLAUDE.md now holds the index).

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/docs/golang/golang.md` | NEW — Go expert patterns |
| `.claude/skills/docs/nixos/advanced.md` | NEW — NixOS advanced patterns |
| `.claude/skills/docs/workflow/workflow.md` | NEW — debugging + verification |
| `.claude/skills/INDEX.md` | Slimmed to pointer |
| `CLAUDE.md` | Expanded knowledge index + retrieval instruction + rules |

---

## Commits

- `71c8b98` feat: add Go + NixOS advanced skill docs, restructure index per Vercel agents.md findings
- `ab8f996` feat: add systematic debugging + verification workflow from obra/superpowers

---

## Next Session

1. Monitor v0.3.6 fleet deployment (fleet order 3f148df6)
2. Pre-existing test failure: TestWindowsRulesMatch/smb_signing in internal/healing
