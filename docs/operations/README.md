
# operations/ - Run, Recover, Develop

Use this directory for procedural instructions: running the gateway, local development, preview worktrees, testing, deployment, and recovery.

PR review budgets, lifecycle states, feedback validity, and merge readiness are
defined only in [`PR_REVIEW_LIFECYCLE.md`](development/agent-guides/PR_REVIEW_LIFECYCLE.md).

## Subfolders

| Folder | Use |
| --- | --- |
| `runbooks/` | Daily operations and SOPs |
| `development/` | Local setup, tests, worktree preview, pytest optimization |
| `deployment/` | [Gateway session service](deployment/GATEWAY_SESSION_SERVICE.md), legacy autostart, release/deployment notes, [packaged Runtime](deployment/PACKAGED_RUNTIME.md), source-update Observer continuity |
| `recovery/` | Data reload and outcome-history remediation |
| `architecture/` | Stability target architecture for operations |
| `records/` | Dated operational recovery records |

For read-only Alpha, opportunity-funnel, and sizing diagnosis, use
[Trading Diagnostics](runbooks/TRADING_DIAGNOSTICS.md). For CASI-authoritative
LLM token usage, optional economics, budget-shadow, and context-adapter
operations, use the [LLM Harness Telemetry runbook](runbooks/LLM_HARNESS_TELEMETRY.md).
When a close-side submission loses its venue confirmation and the claim reaches
the human-owned `manual_reconciliation_pending` state, use the
[Manual Close Reconciliation runbook](runbooks/manual_close_reconciliation.md);
nothing in that path is automatic.

For Fedora source-runtime deployment and stable-Tag auto-updates, use
[Deployment](deployment/DEPLOYMENT.md). Release batching and exact candidate
authorization are defined by the
[modular-monolith release contract](deployment/MODULAR_MONOLITH_RELEASE.md).

## Packaged Runtime release lane

For the browser-first macOS installed product, start with
[Packaged Runtime Operations](deployment/PACKAGED_RUNTIME.md). For an exact `master` SHA that
must be dogfooded on one Mac or promoted through the signed Feed, then read the
[packaged Runtime dogfood playbook](deployment/packaged-runtime-dogfood-playbook.md).
These documents define the release path; do not substitute `git pull`, a development
gateway, or Docker Compose for an installed Runtime update.

If you are specifically optimizing packaged runtime battery/power behavior (tray-first remote control + background efficiency),
start from [Packaged Runtime Power Optimization](deployment/packaged-runtime-power-optimization-report.md).

## Agent Rule

Operational docs can contain commands. Prefer `uv run` for Python commands and preserve safety rules from `AGENTS.md`.
