# Trading Diagnostics

> **Role: Runbook.** Read-only diagnosis of where the current entry path stops:
> Candidate → Planning Generation → Entry Plan → Proposal → Risk → Order → Fill → Position → Close.
> Commands run on the Fedora runtime checkout (`ssh u7p 'cd ~/aletheia && …'`); every command
> below only reads. Behaviour authority is the contracts linked per step; owner/test map is
> [SYSTEM_MAP](../../design/architecture/SYSTEM_MAP.md). The retired Opportunity/Ranker funnel view
> is in [history](../../design/history/trading_diagnostics_legacy_funnel.md).

## Start here

```sh
uv run aletheia doctor                       # data.candidate_planning, loop.execution_liveness
uv run aletheia inspect entry-status         # current planning summary + candidate attention
uv run aletheia inspect lifecycle --candidate-id <id>   # one chain, any one anchor
```

`inspect lifecycle` accepts exactly one of `--candidate-id`, `--generation-id`,
`--entry-plan-id`, `--proposal-id`, `--order-id`, `--position-id` and prints the lineage across
the whole chain (`core/aletheia/analytics/candidate_lifecycle/`). Add `--json` to any `inspect`
command; `inspect query "<SELECT …>"` runs one engine-enforced read-only SQL statement.

The Entry Workbench API exposes the same facts: `GET /api/universe/workbench`,
`/api/universe/planning-history`, `/api/universe/entry-plans/{plan_id}`,
`/api/universe/candidates/{candidate_id}` (`core/aletheia/server/routes/universe.py`).

## Step by step

| Step | Read with | Durable state | Typical stop tokens (code) |
| --- | --- | --- | --- |
| Candidate | `inspect universe`, `inspect candidates`, `inspect candidate-facts --horizon 24h`, `inspect candidate-diagnosis --horizon 24h` | `universe_members` | `execution_eligible`, `inclusion_reason`, `risk_flags` fields on the member row |
| Planning Generation | `inspect entry-status`, `inspect candidates`, doctor `data.candidate_planning` | `candidate_planning_generations` (`status`, `outcome_class`, `outcome_reason`, `last_stage`) | `outcome_class` ∈ `entry_plan` / `no_plan` / `planning_blocked` / `planning_failed` / `planning_unavailable` (`storage/models.py`); blocked: `entry_permission_insufficient`, `entry_live_authority_unavailable`, `entry_execution_route_unsupported` (`trading/entry_authority.py::resolve_entry_runtime_authority`), `EXECUTION_VENUE_UNAVAILABLE:<reason>`, `CANDIDATE_CAPABILITY:<status>`, `NO_VALID_ENTRY_PROFILE` (`agent_shell/entry_planner.py`) |
| Entry Plan | `inspect lifecycle --generation-id <id>`, `GET /api/universe/entry-plans/{plan_id}` | `entry_plans` | `planning_failed`: `ENTRY_PLAN_COMPILE_INVALID:<code>`; `planning_unavailable` (retried, attention turns terminal after 3 attempts — `repositories.py::defer_candidate_planning`): `ENTRY_PLAN_COMPILE_UNAVAILABLE:<code>` (e.g. `VENUE_MARGIN_TIERS_STALE`, `NO_FEASIBLE_FINANCING`), `COMPILER_INTERNAL:<type>` (`agent_shell/entry_planner.py::_compile_current_entry_plan`) |
| Proposal | `inspect proposals --status rejected`, `inspect lifecycle --entry-plan-id <id>` | `trade_proposals` | execution re-checks authority (`execution/entry_plan_execution.py` → `require_entry_runtime_authority`): `entry_execution_route_changed`, `entry_minimum_permission_mismatch`, plus the authority tokens above |
| Risk | `inspect risk --rejected-only` (prints `violations`) | `risk_decisions` | `kill_switch_active`, `live_disabled`, `account_balance_unavailable`, `entry_plan_expired`, `entry_plan_generation_superseded`, `entry_plan_max_risk_exceeded`, `entry_plan_max_leverage_exceeded`, `max_daily_loss_exceeded:<…>`, `NO_FEASIBLE_FINANCING`, `SIZING_RESOLUTION_DRIFT` (`risk/engine.py`) |
| Order | `inspect orders --status all`, `inspect orders --stats --days 7`, doctor `loop.execution_liveness` | `orders` (`status`, `error_reason`) | non-terminal states `pending`, `open`, `submitted`, `partially_filled`, `submission_unknown`, `materialization_missing` (`storage/order_states.py`); `uta_account_identity_mismatch` (`uta/manager.py`, `execution/gateway.py`) — doctor reports it as "identity gate refuses new entries on …" (`execution/liveness.py::identity_blocked_modes`); `entry_cancel_pending:<reason>` |
| Fill | `inspect fills --order-id <id>` | `fills` | no fill for a submitted Order → see `loop.execution_liveness` ("N Entry Plans … 0 filled") |
| Position | `inspect positions --status open`, `inspect position-story --id <id>`, `inspect position-path --id <id>` | `positions` | — |
| Close | `inspect position-story --id <id>`, `inspect positions --status closed` | `positions.pending_close_status` | close-claim states (`execution/close_convergence.py::CLOSE_CLAIM_STATES`): `submission_unknown`, `position_absence_unverified`, `venue_trade_rate_limited`, `account_identity_unproven`, `manual_reconciliation_pending` → [Manual Close Reconciliation](manual_close_reconciliation.md) |

Relevant contracts: [Entry Plan](../../design/contracts/entry-plan-contract.md),
[Execution](../../design/contracts/execution_contract.md) (§3.0b identity gate, §6.1 liveness).

## Evidence boundaries

- These commands read local SQLite and current configuration. They submit no orders, create no
  proposals, call no LLM or exchange and trigger no scheduler job.
- A missing record is reported as missing evidence, not reconstructed certainty.
- A diagnostic warning is not a reason to loosen an identity, freshness or Risk gate; the doctor
  fix hints say so explicitly.
- `doctor --fix` only repairs infrastructure (orphan processes, a stopped Gateway); it never changes
  trading, Risk, sizing or prompt configuration.

## Known gaps

- Hidden compatibility aliases still registered in `core/aletheia/main.py`: `aletheia funnel audit`,
  `aletheia sizing trace`, `aletheia alpha audit` (`hidden=True`), plus the legacy
  `opportunity audit` views `controller` and `ranker-objective` (`cli/opportunity.py::_VIEWS`).
  They read Opportunity-era evidence only; deleting them is a convergence Issue candidate.
- `inspect proposals` still displays the legacy `opportunity_id` column and no `entry_plan_id`;
  use `inspect lifecycle --proposal-id` for the Entry Plan link.
