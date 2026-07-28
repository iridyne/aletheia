# issues/ - Frozen Problem Evidence

<!-- FORENSIC-FROZEN -->

This directory preserves historical defect lists, incidents, audits, and fix
ledgers. It is not runtime truth and it is not a live work tracker. GitHub
Issues is the sole source for current ownership, priority, progress, and
closure.

## Historical contents

| Folder | Preserved evidence |
|---|---|
| `domain/` | Backend, frontend, human-reported, and UI-gap snapshots |
| `incidents/` | Runtime behavior reports and historical incidents |
| `backlog/` | Previous fix plans and deferred-work ledgers |
| `audits/` | Focused problem audits and integrity reviews |
| `ledgers/` | Raw sweeps and broad issue inventories |
| `archive/` | Material already classified as superseded or fixed history |

## Agent rule

An `open`, `pending`, or `待修` marker in this tree is historical. Do not update
local rows, labels, checklists, owners, priorities, PR links, or closure states.
Verify the problem against current code and runtime facts. If it remains real,
create or update its GitHub Issue and leave the historical source unchanged.

For safety-sensitive problems, follow
[`BUG_TRIAGE_PROTOCOL.md`](../governance/protocols/BUG_TRIAGE_PROTOCOL.md).
Local documents may preserve evidence and decision context, but must never
become a parallel issue database.
