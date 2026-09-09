> Archived OpenWiki snapshot from `b7ec0d670`. This page is historical context,
> not current code, configuration, execution authority or acceptance criteria.

---
type: reference
title: CLI Reference
description: Command-line interface for Aletheia Trading OS, organized by functional area.
tags: [cli, reference, commands]
openwiki:
  roles: [operations, repository]
  change_kinds: [public-api]
  source_paths:
    - aletheia/main.py
    - aletheia/cli/gateway.py
    - aletheia/cli/doctor_cmd.py
    - aletheia/cli/inspect_cmd.py
    - aletheia/cli/data_cmd.py
    - aletheia/cli/exchange_cmd.py
    - aletheia/cli/operator.py
    - aletheia/cli/runtime_cmd.py
    - aletheia/cli/knowledge_cmd.py
    - aletheia/cli/guard_cmd.py
    - aletheia/cli/audit_cmd.py
    - aletheia/cli/control_cmd.py
    - aletheia/cli/evidence_cmd.py
    - aletheia/cli/update_cmd.py
    - aletheia/cli/network_cmd.py
    - aletheia/cli/tunnel.py
    - aletheia/cli/dev.py
    - aletheia/cli/opportunity.py
    - aletheia/cli/thesis.py
    - aletheia/cli/bitget_cmd.py
    - aletheia/cli/model.py
    - aletheia/cli/permission.py
    - aletheia/cli/risk_cmd.py
    - aletheia/cli/secrets_cmd.py
  symbols:
    - app
    - gateway_app
    - doctor_app
    - agent_app
    - runtime_app
    - inspect_app
  test_paths:
    - tests/test_cli.py
  invariants:
    - The CLI is the bootstrap surface only; most configuration is done via the dashboard.
    - Deprecated commands still work as escape hatches.
  validation_commands:
    - uv run aletheia --help
    - uv run pytest tests/test_cli.py
---

# CLI Reference

The **CLI** is Aletheia's bootstrap surface. It provides the essential commands for starting, stopping, and diagnosing the system. Most configuration is done via the dashboard's Settings page.

## Entry Point

```bash
uv run aletheia <command>
# or after installation:
aletheia <command>
```

The CLI is built with [Typer](https://typer.tiangolo.com/) and is defined in `aletheia/main.py`.

## Command Categories

### Bootstrap Surface (kept on CLI)

These commands do what a not-yet-running web UI cannot:

| Command | Description | Source |
|---|---|---|
| `gateway` | Start, stop, restart, enable autostart | `cli/gateway.py`, `gateway/lifecycle.py` |
| `doctor` | Probe this machine's real state (single source of truth) | `cli/doctor_cmd.py` |
| `audit` | Audit trail verification (hash chain integrity) | `cli/audit_cmd.py` |
| `agent` | Operator Agent control-plane commands | `cli/operator.py` |
| `runtime` | PI runtime continuity commands | `cli/runtime_cmd.py` |
| `tunnel` | Cloudflare Tunnel (public access) | `cli/tunnel.py` |
| `network` | System proxy / network egress configuration | `cli/network_cmd.py` |
| `inspect` | Query runtime state (read-only, agent-friendly) | `cli/inspect_cmd.py` |
| `data` | Historical data management | `cli/data_cmd.py` |
| `exchange` | Switch active exchange, mode, and view config | `cli/exchange_cmd.py` |
| `update` | Install the latest verified update for this runtime | `cli/update_cmd.py` |

### Operator/Debug Tools (no web equivalent)

| Command | Description | Source |
|---|---|---|
| `knowledge` | Knowledge base / experience-loop tools (reindex, search) | `cli/knowledge_cmd.py` |
| `guard` | Trading-guard admin: audit learned guards, downgrade to shadow-only | `cli/guard_cmd.py` |
| `bitget` | Bitget connection (test deprecated → dashboard; sync is a debug tool) | `cli/bitget_cmd.py` |
| `dev` | Isolated worktree development preview commands | `cli/dev.py` |
| `evidence` | Evidence epoch management and observation window anchoring | `cli/evidence_cmd.py` |
| `control` | Machine governance control plane: SHA, epochs, authority, window continuity | `cli/control_cmd.py` |
| `opportunity` | Analyze opportunity lifecycle and outcomes | `cli/opportunity.py` |
| `thesis` | Thesis lineage snapshots and record export | `cli/thesis.py` |

### Read-Only Audits (hidden)

| Command | Description | Source |
|---|---|---|
| `alpha` | Read-only alpha attribution audit | `cli/alpha.py` |
| `flow` | Deprecated alias for opportunity | `cli/flow.py` |
| `funnel` | Read-only trade funnel audit | `cli/funnel.py` |
| `sizing` | Read-only sizing trace audit | `cli/sizing.py` |

### Deprecated (→ dashboard)

These commands are superseded by the web UI but still work as escape hatches:

| Command | Description | Source |
|---|---|---|
| `model` | [deprecated → Settings] Model provider commands | `cli/model.py` |
| `permission` | [deprecated → Settings] Agent permission mode | `cli/permission.py` |
| `secrets` | [deprecated → Settings] Secrets management | `cli/secrets_cmd.py` |
| `risk` | [deprecated → Risk panel] Risk / kill-switch commands | `cli/risk_cmd.py` |

## Gateway Commands

```bash
aletheia gateway start      # Start the gateway (background)
aletheia gateway start --foreground  # Start in foreground
aletheia gateway stop       # Stop the gateway
aletheia gateway restart    # Restart the gateway
aletheia gateway status     # Show gateway status
aletheia gateway enable-autostart  # Enable OS autostart
```

## Doctor Commands

```bash
aletheia doctor             # Run full system diagnostics
aletheia doctor --json      # Output in JSON format
```

## Agent Commands

```bash
aletheia agent status       # Show agent status
aletheia agent run          # Run a specific agent
```

## Data Commands

```bash
aletheia data backfill      # Backfill historical data
aletheia data status        # Show data collection status
```

## Key Invariants

1. **Bootstrap surface** — The CLI is the bootstrap surface only; most configuration is done via the dashboard.
2. **Deprecated escape hatches** — Deprecated commands still work but are superseded by the web UI.
3. **Read-only audits** — Alpha, funnel, sizing, and flow are read-only audit commands.
4. **Hidden commands** — Some commands are hidden from help but still functional.

## Relationship to Other Components

- [Gateway](services/gateway.md) — The gateway lifecycle commands
- [Agent Decision Loop](../architecture/agent-decision-loop.md) — The agent control plane
- [Trading Loop](../architecture/trading-loop.md) — Full evidence pipeline
