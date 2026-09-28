# CLI surface review — 2026-09-28

## Scope and method

Base: `eab61a5`. Primary-agent review; no delegated reviewer. This is a static
error-path and structural scan, with executable proof for the finding below,
not a certification of every command against a deployed runtime.

Inventoried all 33 Python files under `core/aletheia/cli` and their 107 exception
handlers. Reviewed exit/error reporting, explicit unavailable projections,
rendering fallbacks and oversized functions. Counts are navigation aids, not
proof of defects. Both installed entry points were exercised for the changed path.

| Coverage | Modules |
| --- | --- |
| Operational commands | `control_cmd`, `gateway`, `runtime_cmd`, `update_cmd`, `install`, `tunnel`, `network_cmd`, `doctor_cmd` |
| Read/evidence commands | `inspect_cmd`, `observe_cmd`, `audit_cmd`, `data_cmd`, `evidence_cmd`, `migration_progress`, `flow`, `funnel`, `alpha` |
| Domain commands | `bitget_cmd`, `exchange_cmd`, `guard_cmd`, `knowledge_cmd`, `learning_cmd`, `model`, `operator`, `opportunity`, `permission`, `risk_cmd`, `secrets_cmd`, `sizing`, `thesis` |
| Development/package | `dev`, `dev_seed`, `__init__` |

## Findings

| Location | Finding | Consumer effect | Disposition |
| --- | --- | --- | --- |
| `core/aletheia/cli/network_cmd.py:test_cmd` | An unreachable proxy produced `Result: warn` but exit 0; missing results also returned success. Unprobed informational results were rendered as unreachable. | Shell callers could accept a failed connectivity check as success. | D0, Contract Delta `NONE`: exit 1 unless the diagnostic result is `ok`; represent unprobed reachability as `unknown`. Existing doctor/model diagnostics establish failure exit conventions. |
| `core/aletheia/cli/observe_cmd.py:_collect_overview` | 337-line aggregation function on the scanned base. | Size alone does not establish wrong behavior or duplicate authority. | No speculative decomposition. |
| Read-model and formatting exception boundaries | Several broad handlers return explicit unavailable/error projections or render unavailable values. | These are not silently healthy results merely because an exception is caught. | No blanket exception rewrite. |

## Frozen verification evidence

- Before repair: a real bound, non-listening localhost proxy produced
  `Reachable: no`, `Result: warn`, and exit 0.
- After repair: `uv run --no-sync aletheia network test` and
  `uv run --no-sync ato network test` against a temporary configuration and
  bound non-listening localhost socket both returned exit 1 and `Reachable: no`.
- `uv run --no-sync python scripts/dev_check.py`: PASS; focused
  `tests/test_network_cli.py`: 7 passed. The regression exercises the real probe
  rather than substituting its failure result.
- Runtime/data isolation: temporary data root; no shared Gateway, live account,
  credentials, deployment or qualification state was changed.

This scan does not establish network reachability for a configured deployment,
and does not certify the behavior of every CLI command under every failure.
