
# operations/ - Run, Recover, Develop

Use this directory for procedural instructions: running the gateway, local development, preview worktrees, testing, deployment, and recovery.

## Subfolders

| Folder | Use |
|---|---|
| `runbooks/` | Daily operations and SOPs |
| `development/` | Local setup, tests, worktree preview, pytest optimization |
| `deployment/` | Gateway lifecycle, autostart, release/deployment notes |
| `recovery/` | Data reload and outcome-history remediation |
| `architecture/` | Stability target architecture for operations |
| `records/` | Dated operational recovery records |

## Agent Rule

Operational docs can contain commands. Prefer `uv run` for Python commands and preserve safety rules from `AGENTS.md`.
