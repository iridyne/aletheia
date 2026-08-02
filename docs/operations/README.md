
# operations/ - Run, Recover, Develop

Use this directory for procedural instructions: running the gateway, local development, preview worktrees, testing, deployment, and recovery.

## Subfolders

| Folder | Use |
| --- | --- |
| `runbooks/` | Daily operations and SOPs |
| `development/` | Local setup, tests, worktree preview, pytest optimization |
| `deployment/` | [Gateway session service](deployment/GATEWAY_SESSION_SERVICE.md), legacy autostart, release/deployment notes |
| `recovery/` | Data reload and outcome-history remediation |
| `architecture/` | Stability target architecture for operations |
| `records/` | Dated operational recovery records |

For read-only Alpha, opportunity-funnel, and sizing diagnosis, use
[Trading Diagnostics](runbooks/TRADING_DIAGNOSTICS.md). For CASI-authoritative
LLM token usage, optional economics, budget-shadow, and context-adapter
operations, use the [LLM Harness Telemetry runbook](runbooks/LLM_HARNESS_TELEMETRY.md).

## Packaged Runtime release lane

For the browser-first macOS installed product, start with
[Packaged Runtime Operations](PACKAGED_RUNTIME.md). For an exact `master` SHA that
must be dogfooded on one Mac or promoted through the signed Feed, then read the
[packaged Runtime dogfood playbook](deployment/packaged-runtime-dogfood-playbook.md).
These documents define the release path; do not substitute `git pull`, a development
gateway, or Docker Compose for an installed Runtime update.

If you are specifically optimizing packaged runtime battery/power behavior (tray-first remote control + background efficiency),
start from [Packaged Runtime Power Optimization](packaged-runtime-power-optimization-report.md).

## Agent Rule

Operational docs can contain commands. Prefer `uv run` for Python commands and preserve safety rules from `AGENTS.md`.
