
# operations/ - Run, Recover, Develop

Use this directory for procedural instructions: running the gateway, local development, preview worktrees, testing, deployment, and recovery.

PR review budgets, lifecycle states, feedback validity, and merge readiness are
defined only in [`PR_REVIEW_LIFECYCLE.md`](development/agent-guides/PR_REVIEW_LIFECYCLE.md).

## Subfolders

| Folder | Use |
| --- | --- |
| `runbooks/` | Daily operations, diagnostics and manual reconciliation |
| `development/` | Local setup, tests, worktree preview, pytest optimization |
| `deployment/` | [Gateway session service](deployment/GATEWAY_SESSION_SERVICE.md), source-runtime release/deployment notes, source-update Observer continuity |
| `recovery/` | Outcome-history remediation |
| `architecture/` | Stability target architecture for operations |

For read-only Alpha, opportunity-funnel, and sizing diagnosis, use
[Trading Diagnostics](runbooks/TRADING_DIAGNOSTICS.md). For CASI-authoritative
LLM token usage, optional economics, budget-shadow, and context-adapter
operations, use the [LLM Harness Telemetry runbook](runbooks/LLM_HARNESS_TELEMETRY.md).
When a close-side submission loses its venue confirmation and the claim reaches
the human-owned `manual_reconciliation_pending` state, use the
[Manual Close Reconciliation runbook](runbooks/manual_close_reconciliation.md);
nothing in that path is automatic.

For Fedora source-runtime deployment and stable-Tag auto-updates, use
[Deployment](deployment/DEPLOYMENT.md).

## Agent Rule

Operational docs can contain commands. Prefer `uv run` for Python commands and preserve safety rules from `AGENTS.md`.
