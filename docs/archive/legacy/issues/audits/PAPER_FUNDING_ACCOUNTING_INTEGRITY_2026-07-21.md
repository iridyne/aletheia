# Paper Funding Accounting Integrity Incident

Date: 2026-07-21

Status: Open, repair required

Classification: A-class accounting/outcome integrity defect

Severity: P0 for persisted PnL correctness, P1 for unavailable outcomes and
operational account blocking

Repair plan:
[`paper_funding_accounting_integrity_repair_2026-07-21.md`](../../archive/legacy/plans/trading-pipeline/paper_funding_accounting_integrity_repair_2026-07-21.md)

## Owner Outcome

Local paper futures PnL is not currently reliable enough to use without
qualification. The system correctly refuses to invent funding as zero, but two
timestamp-handling defects can produce opposite failures:

1. complete historical funding exists but the position remains unavailable;
2. one funding settlement is counted twice and a wrong numeric PnL is persisted.

The second failure has been observed on a closed position. This is not a UI-only
problem and not a request to weaken funding completeness. It is an accounting
identity and materialization defect.

Until the repair and audited reconciliation complete:

- `realized_pnl=NULL` rows remain excluded from outcome/learning statistics;
- existing numeric local-paper futures PnL is not assumed correct merely because
  the read model says `complete`;
- no direct database correction is permitted;
- no missing funding value may be replaced with zero;
- live, demo-exchange, Risk, credentials, and execution-gateway behavior remain
  unchanged.

## Scope

Affected:

- `paper_internal` positions with `market_type='usdt_futures'`;
- local funding observations and historical funding backfill;
- `positions.realized_pnl`, `positions.funding_cost`, and outcome eligibility;
- `position_close_events`;
- append-only `paper_account_events` cash settlement;
- outcome-hook delivery after delayed economics recovery;
- operator-facing PnL availability states.

Not affected by this incident classification:

- spot positions, where funding is not applicable;
- exchange-backed funding collection, which remains the separate open issue
  `TC-X01`;
- order placement, live unlock, credentials, signing, or hard risk limits;
- trade direction, ranking, sizing, strategy, prompt, or agent behavior.

## Required Invariants

The repair must preserve and make executable the following invariants.

| ID | Invariant |
|---|---|
| PFA-I01 | One economic funding settlement is charged exactly once per position. |
| PFA-I02 | Timestamp representation noise cannot create or erase a settlement. |
| PFA-I03 | A numeric realized PnL exists only when entry fills, exit fill, fees, and the complete funding window are proven. |
| PFA-I04 | Unknown funding remains unavailable and is never converted to zero. |
| PFA-I05 | `positions`, `position_close_events`, `paper_account_events`, reconciliation evidence, outcome eligibility, and outcome-hook jobs converge as one idempotent materialization. |
| PFA-I06 | Every correction is compensating and audited; append-only cash history is never rewritten silently. |
| PFA-I07 | Historical funding evidence and live observation coverage are explicit, separate completeness modes. Partial mixing cannot upgrade an outcome. |
| PFA-I08 | Recovery is retryable across restarts and cannot terminalize a recoverable row solely because time elapsed. |
| PFA-I09 | The operator can distinguish pending recovery, terminally unavailable, and complete/corrected economics. |
| PFA-I10 | Downstream outcome and learning jobs run exactly once after delayed eligibility becomes true. |

## Observed Symptoms

The closed-position blotter exposed three different states:

| UI state | Stored meaning | Current behavior |
|---|---|---|
| `complete` | realized economics read model is numeric | May be wrong if a near-duplicate settlement was counted twice. |
| `Incomplete Funding Economics` | funding recovery is still in flight | Can remain pending even after historical settlement rows exist. |
| `funding lost` | recovery stopped after the give-up window | Can represent a collection/recovery limitation rather than truly unavailable exchange history. |

The UI rendering itself is honest about `NULL` values in
`frontend/src/pages/TradesPositions.tsx:38-42,63-81`. The defect is in the
backend facts supplied to that UI.

## Confirmed Runtime Evidence

The following values were reproduced read-only from the normal repository and
accounting APIs on 2026-07-21. They are incident evidence, not a repair script and
not an authorization to write these values directly.

| Position | Symptom | Confirmed evidence |
|---|---|---|
| PROM short | pending | Historical settlements at `20:00:00.002` and `00:00:00.001` exist, but computation stops at `missing_event_observation`. |
| BANK long | pending | Historical settlements at `20:00:00.002`, `00:00:00.001`, and `04:00:00.000` exist, but computation stops at `missing_event_observation`. |
| UAI short | pending | Historical settlements at `20:00:00.002`, `00:00:00.001`, and `04:00:00.000` exist, but computation stops at `missing_event_observation`. |
| ACE long | persisted wrong value | `16:00:00.000` and `16:00:00.004` were treated as separate settlements. Stored funding is `-0.17737406`; one-event-per-settlement recomputation is approximately `-0.12541881`. |
| FIL short | terminal unavailable | Last pre-close primary observation was about `1037.4s` before close, over the `900s` limit. Current recomputation also exposes a duplicated `16:00` settlement candidate. |
| CL long | terminal unavailable | Last pre-close primary observation was about `1038.3s` before close, over the `900s` limit. Current recomputation exposes duplicated `12:00` candidates. |
| ALLO long | terminal unavailable | Last pre-close primary observation was about `1037.2s` before close, over the `900s` limit. |

Diagnostic recomputation using the already stored historical settlement events,
one event per canonical settlement, and the existing price/fee formula produced:

| Position | Stored PnL | Diagnostic PnL |
|---|---:|---:|
| PROM short | unavailable | approximately `+1.52661484` |
| BANK long | unavailable | approximately `-0.79669326` |
| UAI short | unavailable | approximately `+0.03888583` |
| ACE long | `-0.80541165` | approximately `-0.85736690` |

These diagnostic values must not be persisted until the fixed algorithm passes
the executable contract and the repair runs through the normal atomic
materialization path.

## Root Cause

### PFA-01: Schedule identity uses exact timestamps

`aletheia/portfolio/local_funding.py:490-505` requires:

```python
row.scheduled_at == current_target
```

Live CCXT observations commonly represent a target as `HH:00:00.000`, while
historical funding endpoints preserve exchange timestamps such as
`HH:00:00.001`, `HH:00:00.002`, or `HH:00:00.004`.

These values refer to the same economic settlement but are different Python
`datetime` values. Exact equality therefore turns representation noise into a
missing event.

### PFA-02: The same skew creates duplicate charges

`aletheia/portfolio/local_funding.py:478-588` advances through raw scheduled
timestamps. When both `16:00:00.000` and `16:00:00.004` are reachable, the loop
visits both and charges funding twice.

Historical rows intentionally retain the exchange timestamp in
`aletheia/data/collectors/derivatives.py:329-387`. The collector behavior is
reasonable for raw evidence, but the accounting layer lacks a canonical
settlement identity and precedence rule.

### PFA-03: Observation heartbeats and settled history are conflated

The live algorithm uses a 15-minute observation window to prove that the
collector stayed alive around entry, every settlement, and close. Historical
backfill instead provides settled funding events. These evidence types answer
different questions:

- a live observation proves liveness and a then-current scheduled target;
- a settled-history row proves an actual economic event;
- a complete historical sequence can bracket a closed holding interval after the
  next settlement has occurred.

The current implementation inserts event and transition anchor pairs, then feeds
them into the live-observation state machine. This creates exact-timestamp
collisions and still cannot reliably prove a mid-window close.

### PFA-04: Delayed recovery only partially materializes the close

`aletheia/portfolio/paper_account.py:393-417` updates the Position and appends a
paper cash event after recovery. It does not update or materialize all dependent
facts:

- `position_close_events.realized_pnl` remains `NULL`;
- `positions.reconciliation_evidence` can still say funding was incomplete;
- no `outcome_hook_jobs` rows are enqueued when delayed recovery changes the
  outcome from ineligible to eligible;
- the original close audit remains correct as an initial fact, but there is no
  complete, linked economics-finalization record for all consumers.

This has been observed for recovered ACE and EVAA positions: the Position and
cash event are numeric, while the close-event PnL is still `NULL` and no outcome
hook job exists.

### PFA-05: Recovery crosses the two-table invariant non-atomically

The recovery path calls `_update_position_economics()` and then
`settle_position_closed()` as separate transactions. A failure between them can
leave `positions.realized_pnl` set without the required `paper_account_events`
settlement. Existing orphan reconciliation reduces the duration of this mismatch
but does not make the state transition atomic.

This conflicts with the repository's paper-ledger two-table invariant and can
trigger `paper_ledger_mismatch` in daily-loss checks.

### PFA-06: Retry semantics and observability drift

The recovery comment says the settler runs every 15 minutes and receives about 96
attempts in 24 hours. Scheduler registration currently runs every 30 minutes in
`aletheia/agent_runtime/scheduler.py:1073-1078`, which gives at most 48 scheduled
attempts before other failures.

Operational logs also showed repeated `Cannot operate on a closed database`
failures from the funding settler. The age-based give-up path does not distinguish
successful deterministic retries from attempts that never completed.

## Failure Chain

```text
primary-venue/proxy outage or scheduler delay
  -> live funding heartbeat gap
  -> protective position close still succeeds
  -> realized_pnl remains NULL, correctly fail-closed
  -> historical funding rows are fetched
  -> raw settlement timestamps differ by 1-4ms
  -> exact timestamp join either misses or double-counts the event
  -> retry remains pending or persists a wrong number
  -> delayed settlement updates only part of the close materialization
  -> UI, cash ledger, close-event ledger, and outcome hooks disagree
```

## Impact

### Accounting

- At least one numeric local-paper futures PnL was persisted with duplicate
  funding.
- Pending positions have price and fee facts but no publishable net result.
- Terminal rows can remain unavailable despite later historical evidence.

### Account Safety

- Any non-terminal unsettled close makes local paper equity unavailable and
  available balance zero.
- Risk therefore fails closed. This is safer than invented equity, but a
  recoverable timestamp mismatch can unnecessarily stop all new local-paper
  trading.

### Outcome and Learning

- Pending and terminal rows are excluded, which prevents false learning but loses
  valid samples.
- Recovered rows can become eligible without the required outcome hooks, so trade
  cases and post-trade consumers may never run.
- A wrong numeric row can contaminate expectancy, profit factor, drawdown, and
  post-trade analysis.

### Auditability

- Position projection, close-event ledger, cash ledger, and stored reconciliation
  evidence can disagree.
- Recovery does not currently produce one durable economics-finalization identity
  shared by all downstream facts.

## Classification

This incident is A-class under `BUG_TRIAGE_PROTOCOL.md` because it includes:

- Accounting/Outcome falsehood;
- wrong persisted PnL;
- partial materialization across accounting tables;
- missing downstream outcome delivery after eligibility changes.

The repair must not start a new strategy policy epoch because it does not change
what the agent sees or how it chooses trades. Historical performance windows must,
however, record the accounting-correction boundary and must not compare uncorrected
and corrected PnL as if they were one homogeneous accounting version.

## Immediate Safety Posture

- Keep `NULL` PnL unavailable.
- Keep pending and terminal rows excluded from strategy outcome and learning.
- Do not unlock or alter live trading.
- Do not relax the 15-minute live-coverage requirement globally.
- Do not write guessed funding or current-rate substitutes.
- Do not edit `positions.realized_pnl` directly.
- Any repair of a numeric PnL must also compensate the append-only paper cash
  ledger and synchronize the close-event projection.

## Acceptance Criteria

The issue may close only when all conditions are met:

1. Settlement identity is canonical and tested against millisecond skew.
2. The same settlement cannot be counted twice.
3. Actual historical settlement evidence has deterministic precedence over a
   pre-settlement observation for the same canonical event.
4. Live coverage and historical-ledger coverage have separate completeness rules.
5. Partial history remains unavailable.
6. Delayed recovery atomically materializes Position, close-event PnL, cash event
   or compensation, reconciliation evidence, eligibility, and outcome-hook jobs.
7. Re-running recovery is a no-op with no duplicate cash or hook jobs.
8. Transaction-failure tests prove full rollback at every materialization stage.
9. A dry-run historical report identifies pending, terminal, duplicate-event, and
   numeric-mismatch rows before any correction.
10. Applying approved corrections writes per-position before/after audit evidence
    and append-only cash compensation where required.
11. Doctor/health diagnostics detect future duplicate settlement identities,
    cross-ledger mismatches, overdue recovery, and settler job failure.
12. Focused specs, outcome E2E, lint, CI, and a post-deploy observation window pass.
13. Execution, data-model, data-utilization, risk, scheduler, and agent contracts
    match the repaired runtime semantics and executable spec IDs.

## Related Surfaces

- `aletheia/portfolio/local_funding.py`
- `aletheia/data/collectors/derivatives.py`
- `aletheia/execution/paper_broker.py`
- `aletheia/portfolio/pnl.py`
- `aletheia/portfolio/paper_account.py`
- `aletheia/portfolio/outcome_hooks.py`
- `aletheia/agent_runtime/scheduler.py`
- `frontend/src/pages/TradesPositions.tsx`
- `tests/specs/spec_pipeline_local_funding.py`
- `tests/specs/spec_pipeline_paper_close_materialization.py`
- `tests/test_authoritative_account_loss.py`
- `tests/e2e/test_outcome_integrity_full_lifecycle.py`
- `docs/design/contracts/execution_contract.md`
- `docs/design/data/data_model.md`
- `docs/design/data/data_utilization.md`
- `docs/design/contracts/risk_engine_contract.md`
- `docs/design/contracts/scheduler_contract.md`
- `docs/design/contracts/agent_contracts.md`
