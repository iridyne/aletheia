# docs/ Conventions

Rules for adding and naming documentation files. Read this before creating a new doc.

## 1. Directory decides category, filename describes content

Don't add category prefixes to filenames — the directory already says what it is.

- `references/openalice_prism_review.md` not `REFERENCE_openalice_prism_review.md`
- `issues/BACKEND_ISSUES.md` not `docs/BACKEND_ISSUES.md` at root

## 2. UPPERCASE vs lowercase

| Style | Use for |
|---|---|
| **UPPERCASE** (`RUNBOOK.md`, `ROADMAP.md`, `BACKEND_ISSUES.md`) | Append-only flow documents and top-level operational entry points (industry convention: README, LICENSE, CHANGELOG) |
| **lowercase_snake** (`operator_layer.md`, `data_layer.md`) | Design docs, reference analyses, fact records — anything that's written once and rarely appended |

## 3. Date-stamped archives

One-time snapshots that become stale get a date suffix and move to `archive/` or `issues/archive/`:
- `issue_register_2026-06-06.md` (superseded by BACKEND_ISSUES.md)
- `WORKTREEDEV_SYNC_POINT_2026-06-11.md` (expired sync point)

## 4. Where to put new content

| You're writing... | Put it in... |
|---|---|
| Session progress / decision / handoff | `_ai/` (update existing files, don't create new ones) |
| "We should never do X" | `governance/` |
| "How to operate/recover Y" | `operations/` |
| Progress update / direction change | `status/` (update ROADMAP or PIPELINE_STATUS) |
| New subsystem architecture | `design/` |
| Bug / contract drift / dead code finding | `issues/BACKEND_ISSUES.md` or `issues/FRONTEND_ISSUES.md` |
| Code audit with file:line evidence | `facts/` |
| Analysis of external framework | `references/` |
| Step-by-step build plan | `plans/` |
| Raw research material | `research/` |

## 5. Cross-references

Use repo-root-relative paths in backtick references: `` `docs/references/north_star.md` ``

Use relative paths in markdown links: `[ROADMAP](../status/ROADMAP.md)`

Historical entries (AI_HANDOFF, DEVLOG) keep paths as-written at the time — don't rewrite history.
