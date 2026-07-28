
# issues/ - Problem Registry

This directory records forensic sources, historical gaps, incidents, and fix
ledgers. It is not runtime truth and it is no longer the live work tracker.
Verify against current code/runtime, then use GitHub Issues for every problem
that remains open.

## Subfolders

| Folder | Use |
|---|---|
| `domain/` | Active backend, frontend, human-reported, and UI-gap issue files |
| `incidents/` | Runtime behavior issues, monitoring logs, historical incidents |
| `backlog/` | Fix plans, master ledgers, deferred engineering debt |
| `audits/` | Focused problem audits and integrity reviews |
| `ledgers/` | Raw sweep ledgers and broad issue inventories |
| `archive/` | Superseded or fixed-history issue files |

## Current Triage Entry Points

- Backend behavior: `domain/BACKEND_ISSUES.md`
- Frontend behavior: `domain/FRONTEND_ISSUES.md`
- Runtime behavior: `incidents/RUNTIME_BEHAVIOR_ISSUES.md`
- Human-reported UI: `domain/HUMAN_ISSUES.md`
- Active fix plan: `backlog/FIX_PLAN_2026-06-26.md`

## Agent Rule

Do not trust old fixed/open labels without checking current code. For safety-sensitive issues, follow `../governance/protocols/BUG_TRIAGE_PROTOCOL.md`.

An `open`, `pending`, or `待修` marker in a local document is historical unless
the row links to an open GitHub Issue. GitHub is the sole source for current
ownership, priority, progress, and closure. Local documents may preserve the
evidence and decision context, but must not become a parallel status system.

---

## GitHub Issues Workflow (2026-07-20 onwards)

**Why**: PR-only tracking cannot express work that spans multiple PRs (a large audit → GAP-1 in PR#N, GAP-2 in PR#M, GAP-3 pending) or persists across weeks. We use GitHub Issues as the *higher-than-PR* tracking layer. Audit docs in `audits/` remain the frozen forensic snapshots; issues carry the live status.

### When to open an issue

Open a GitHub issue when **any** of these holds:

- A gap survives one PR (multiple PRs will be needed to close it).
- A defect is discovered but out of scope for the current PR.
- An audit doc is added — one **umbrella issue** per audit + one **child issue** per pending gap.
- A production regression is observed and needs multi-cycle diagnosis.
- A cross-cutting concern (e.g. "learning module doesn't produce alpha") needs a home to accumulate progress.

**Don't** open one for: single-PR bugfixes, doc typos, cleanups you'll ship this session.

### Issue types (title prefix + label)

| Prefix | Label | Use |
|---|---|---|
| `[audit] <title>` | `type/audit` | Umbrella for a whole audit doc; body links to `docs/issues/audits/*.md` |
| `[gap] <audit-slug> · <GAP-id>` | `type/gap` | One concrete row of an audit's gap table |
| `[regression] <symptom>` | `type/regression` | Observed failing behavior (test red on master, prod anomaly) |

### Required labels

Every issue must carry at least:

- One `area/*` label (`area/opportunity`, `area/admission`, `area/learning`, `area/runtime`, `area/tests`)
- One `severity/*` label (`severity/critical`|`high`|`medium`|`low`)
- One `type/*` label (see above)
- One `status/*` label (`status/needs-scoping`, `status/scoped`, `status/pr-open`)

### Issue body template

```markdown
## Summary
<one sentence>

## Audit source (if any)
- Doc: docs/issues/audits/<FILE>.md
- Gap: <GAP-N> from that doc's §3

## Impact
- Who / what fails, or what invariant is silently broken

## Evidence
- file:line references (verified from current code)

## Fix plan
- [ ] Sub-task 1 (target PR: #<num>)
- [ ] Sub-task 2 (pending)

## Related PRs
- #<num> — one-line status
```

### PR ↔ Issue linking

- Fully closing an issue: PR body contains `Fixes #<n>` (GitHub auto-closes on merge).
- Partial progress: PR body contains `Refs #<n>` — commenter/author updates the issue's Fix plan checklist and Related PRs section.
- Every audit-derived PR references its umbrella issue in the body.

### Lifecycle

1. Audit lands → open umbrella issue (`type/audit`) + child gap issues (`type/gap`), all `status/needs-scoping`.
2. Scoping done (file:line evidence + fix plan) → flip to `status/scoped`.
3. PR opened → flip to `status/pr-open`, link PR in Related PRs.
4. PR merged that fully resolves → GitHub closes via `Fixes #<n>`.
5. PR merged that partially advances → check the box, keep issue open, update status back to `status/scoped`.
6. Superseded issues → close with a comment linking the replacement, no label change needed.

### Audit doc ↔ issue

- Audit doc's §5 "修复顺序" table gains an "Issue" column linking to `#<n>` for each row.
- When an issue closes, the audit doc's row updates to `✅ closed by PR #<num>`.
- Audit docs are otherwise immutable historical records — do not rewrite them, extend via comments/new sections.
