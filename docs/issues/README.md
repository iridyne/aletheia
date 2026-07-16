
# issues/ - Problem Registry

This directory records defects, gaps, incidents, and fix ledgers. It is not runtime truth; verify against code and current runtime before claiming an item is fixed or still open.

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
