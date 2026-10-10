# operations/ - Run, Recover, Develop

> **Role: Navigation.** Procedural instructions only; overrides nothing. The runtime is Fedora
> (`u7p`); macOS is development only; pytest runs on CASI WSL.

PR review budgets, lifecycle states, feedback validity, and merge readiness are
defined only in [`PR_REVIEW_LIFECYCLE.md`](development/agent-guides/PR_REVIEW_LIFECYCLE.md).

## Subfolders

| Folder | Use |
| --- | --- |
| `runbooks/` | Daily operations, trading-path diagnostics, LLM telemetry, manual reconciliation |
| `development/` | Local setup, [jj workspaces](development/JJ_WORKSPACE_PLAYBOOK.md), previews, tests, pytest optimization |
| `deployment/` | [Deployment and verification](deployment/DEPLOYMENT.md), [Gateway session service](deployment/GATEWAY_SESSION_SERVICE.md), [service environment](deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md), source-update Observer continuity |
| `recovery/` | Outcome-history remediation |
| `architecture/` | Stability target architecture for operations |

## Where to start

- Deploy a commit to Fedora and verify it (commit, schema, model provider, settlement health):
  [Deployment §3](deployment/DEPLOYMENT.md#3-deploy-a-specific-commit-to-fedora-and-verify).
- Where the Candidate → Entry Plan → Order → Close chain stops:
  [Trading Diagnostics](runbooks/TRADING_DIAGNOSTICS.md).
- LLM token usage, optional economics, budget-shadow and context-adapter operations, read on the
  Fedora runtime (the source of truth): [LLM Harness Telemetry](runbooks/LLM_HARNESS_TELEMETRY.md).
- A close-side submission that lost venue confirmation and reached the human-owned
  `manual_reconciliation_pending` state:
  [Manual Close Reconciliation](runbooks/manual_close_reconciliation.md); nothing in that path is
  automatic.

## Agent Rule

Operational docs can contain commands. Prefer `uv run` for Python commands and preserve safety rules from `AGENTS.md`.
