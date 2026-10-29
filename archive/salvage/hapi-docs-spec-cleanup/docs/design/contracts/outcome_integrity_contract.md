# Outcome Integrity Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `OI` · **Owner code:** `core/aletheia/analytics/outcome_integrity.py`
> **Split from:** [execution contract](execution_contract.md) §11.3 (economic commit) and §11.4–11.6 (2026-10-10).
> Paths below are relative to `core/aletheia/` unless they start with a top-level directory.

## Scope

This contract owns what counts as a cost-complete trading result once a Position is closed:
the one central closure semantics and its three read surfaces, the shared economic close
commit, the durable reconciliation incident breaker, and futures funding settlement. It
applies to USDT perpetuals on `bitget_demo`. It does not own how a Position gets closed
([exit and close contract](exit_and_close_contract.md)) or how Outcomes are joined to
Candidates ([opportunity outcome contract](opportunity_outcome_contract.md)). Learning
consumes only the eligible read surface; its design belongs to
[spec 0001](../specs/0001-learning-loop/spec.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/analytics/outcome_integrity.py::raw_account_events` | read | ledger, audit, diagnostics |
| `core/aletheia/analytics/outcome_integrity.py::eligible_strategy_outcomes` | read | KPI, Operator, `portfolio/pnl.py::today_eligible_strategy_pnl` |
| `core/aletheia/analytics/outcome_integrity.py::administrative_closures` | read | reporting |
| `core/aletheia/portfolio/pnl.py::position_realized_pnl_view` | read | `server/routes/data.py` (position list and detail) |
| `core/aletheia/portfolio/pnl.py::close_position` | call | `execution/exchange_materialization.py` (verified close Fill) |
| `core/aletheia/portfolio/account_loss.py::authoritative_account_realized_loss` | read | `risk/engine.py` daily-loss check |
| `core/aletheia/portfolio/broker_funding_settlement.py::settle_unsettled_exchange_closes` | scheduler job `broker_funding_settler` | `agent_runtime/scheduler.py::_run_broker_funding_settler` |
| `core/aletheia/portfolio/position_reconciler.py::detect_reconcile_thrash` | call | `position_reconciler.py::run_reconcile` |
| `core/aletheia/cli/guard_cmd.py::resolve_reconcile_incident` | CLI `aletheia guard resolve-reconcile-incident --actor --reason` | owner |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `positions` economics (`realized_pnl`, `funding_cost`, `funding_source`, `outcome_eligible`, `outcome_exclusion_reason`) | `portfolio/pnl.py::_commit_close_economics` | read surfaces, Risk, UI | final amounts never overwritten |
| `position_close_events` | `portfolio/pnl.py::_commit_close_economics` | PnL readers | append-only per close |
| `outcome_hook_jobs` | `portfolio/pnl.py::_commit_close_economics` | outcome hook replay | queued only for eligible results |
| `reconciliation_integrity_incidents` | `portfolio/reconciliation_integrity.py` | position reconciler, startup gate, `extensions/learning/control.py` | monotonic: active → resolved |
| `broker_funding_settlement_cursor` | `portfolio/broker_funding_settlement.py` | same | replaceable scan cursor |
| `audit_events` (funding, `audit_mirror_status`) | `portfolio/broker_funding_settlement.py` | audit reconciler | `pending → mirrored` |

## Requirements

### 1. Read surfaces

#### OI-C01 Three non-interchangeable read surfaces

`The system shall read closed Positions only through analytics/outcome_integrity.py, using exactly one of raw_account_events, eligible_strategy_outcomes or administrative_closures per consumer.`

- `raw_account_events`: every closed event; ledger, audit, diagnostics and labelled raw views.
- `eligible_strategy_outcomes`: current semantics version (`OUTCOME_SEMANTICS_VERSION="oi-v2"`),
  strategy category, verified real fill, trusted price source, complete identity, time and
  economics.
- `administrative_closures`: administrative and maintenance closes, kept forever, never used
  for judgement.
- The module imports no Learning, Risk, Execution, exchange or agent-shell code; the reason
  registry lives only there.

#### OI-C02 Synthetic reasons win

`When a closed row carries a known synthetic or maintenance reason, the central classifier shall classify it by that reason regardless of other fields.`

- `outcome_eligible=1` never overrides `reconciled_gone` or a maintenance reason.
- A legacy row without structured evidence is `legacy_unverified_close`; a PnL number alone
  never proves an exit.

#### OI-C03 Known economics are a non-final projection

`The position API shall expose known_gross_pnl, known_net_before_unresolved_costs and known_economics_missing_components as a read-only projection that never changes realized_pnl or outcome_eligible.`

- `known_gross_pnl` needs verified `broker_fill` entry and exit prices and exact quantity;
  the net subtracts only evidenced fees and funding; unknown costs are listed, never zero.
- Slippage is not subtracted again (fill prices include it). The UI labels the amount
  "known, before unresolved costs, not final".

#### OI-C04 Daily PnL readers

`The system shall report daily PnL as today_raw_closed_realized_pnl or today_eligible_strategy_pnl, and Risk shall read account loss only from authoritative_account_realized_loss.`

- `today_raw_closed_realized_pnl` returns unavailable when any close of the day lacks net
  economics, never summing it as 0. `today_realized_pnl` is a raw compatibility alias.
- `authoritative_account_realized_loss` excludes unverified exit economics and fails closed
  when account truth is missing.

### 2. Incident circuit breaker

#### OI-C05 Reopen after administrative close is an incident

`When, under the current semantics version, two independent scoped account/instrument identities show a native or adopted open event after an administrative or maintenance close within the detector window, the position reconciler shall record one active reconciliation_integrity_incidents row.`

- The detector (`position_reconciler.py::detect_reconcile_thrash`) runs before demo
  auto-adopt and orphan-close; while an incident is active those actions and every
  behavior-changing Learning entry stay frozen across restarts.
- WHEN the detector is unavailable THEN demo reconciliation is suspended.
- Live only classifies and alerts; it gains no automatic close authority.

#### OI-C06 Resolution needs clean evidence and an owner

`If an owner asks to resolve an incident, then the system shall resolve it only after at least two clean observations at least five minutes apart and a clean detector now, with an explicit actor and reason.`

- Constants: `portfolio/reconciliation_integrity.py::MIN_CLEAN_OBSERVATIONS`,
  `MIN_CLEAN_OBSERVATION_SPACING`. Any renewed loop resets the clean count; a clean poll never
  self-clears.
- Resolution writes `audit_events` and the hash-chain JSONL.

### 3. Futures funding and close economics

#### OI-C07 One economic close commit

`The system shall commit close economics only through portfolio/pnl.py::_commit_close_economics, which writes Position, full close event, Outcome eligibility and hook intent in the caller's writer transaction.`

- Native closes and verified disappearances validate evidence in `pnl.close_position` /
  `outcome_closure.py::close_position_verified`, then call it. It opens no transaction and
  does no external I/O; a completed final amount is never overwritten.
- Outcome attribution follows `Order.entry_plan_id = Proposal.entry_plan_id`; a wrong or
  missing current Order→Plan link is never repaired from a stale cache.
- Administrative closes never use this entry; history is not rewritten.

#### OI-C08 Unknown funding is unavailable, not zero

`If a futures close has no trusted account funding ledger, then the system shall still close the Position with its real Order and Fill and write funding_cost=NULL, funding_source='unavailable', realized_pnl=NULL, outcome_eligible=false and outcome_exclusion_reason='incomplete_funding_economics'.`

- No eligible Outcome hook is created and nothing enters Attribution, Feedback or Learning.
- Only `broker_ledger` (the account's private bills) is accepted; public funding rates never
  substitute for a real charge.
- Validation covers positive price and quantity, non-negative fees and a trusted funding
  source; matching Position and close-event amounts are not enough.

#### OI-C09 Funding reader identity

`The broker funding settler shall pass the complete Position to the UTA funding reader and take the account identity from the Position's persisted entry Order.`

- There is no legacy venue client factory or compatibility wrapper.

#### OI-C10 Late exit-fee completion

`When a later private Fill report supplies a missing exit fee amount or currency, materialize_exchange_close_costs shall complete only the unfinished Position and close-event fee projection after re-verifying account, Order/Fill identity, full exit quantity, price and fill time in one transaction.`

- A conflicting existing non-null fee or currency is refused; the original Fill and close
  evidence are never rewritten; new observations go to reconciliation evidence and the audit
  outbox.
- WHEN the fee becomes known but funding is still unknown THEN final PnL stays unavailable
  and the Outcome ineligible; the fair queue reads funding again next round.
- Unconvertible third-currency fees stay unavailable; missing entry cost is never presumed;
  this path does not rebuild historical entry fees.
- The recovery queue and its pending count use the real cost gap plus verified broker-fill
  evidence; a verified external `administrative_close` may complete account net economics but
  never gains strategy Outcome eligibility; a no-fill administrative close never enters.

#### OI-C11 Funding retention expiry is terminal

`When the UTA funding ledger reports funding_history_retention_expired for a closed, verified Position with NULL economics and unavailable funding, the broker funding settler shall mark it broker_funding_history_expired in one immediate transaction.`

- The state only stops retries: `funding_cost` and `realized_pnl` stay `NULL`,
  `funding_source='unavailable'`, `outcome_eligible=false`; Order, Fill, Position, close event
  and Outcome/Learning authority are unchanged.
- The transition writes versioned credential-free reconciliation evidence and a durable
  funding audit/outbox entry.
- Malformed evidence, identity mismatch, stale snapshot, credential/network/permission
  failure and incomplete-but-unexpired reports stay retryable.

#### OI-C12 Fair recovery queue

`The broker funding settler shall rotate its recovery queue with a durable (closed_at, rowid) keyset cursor and separate limits for local retention classification and broker I/O.`

- Expired, retryable or CAS-conflict prefixes never permanently hide later Positions or
  another execution mode; the cursor changes only the next scan position, never business
  state; each round keeps explicit scan and I/O caps.

#### OI-C13 Funding audit delivery is independent

`When a broker funding settlement commits, the settler shall write its audit to audit_events with a stable id inside the accounting transaction and attempt the hash-chain JSONL append only after commit.`

- WHEN the append fails THEN committed Position/PnL/hook authority stays,
  `audit_mirror_status=pending` is returned and the scheduler/audit job health is degraded;
  accounting status is never set to error and accounting is never replayed.
- `already_current` retries only an existing pending mirror; a crash between append and ack is
  deduplicated by the stable id; success is monotonic to `mirrored`. Historical audits without
  a shared id are not replayed.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| any close of the day lacks net economics | raw daily PnL unavailable | `None` | never summed as 0 |
| funding ledger missing | close kept, economics `NULL` | `incomplete_funding_economics` | `funding_cost=NULL`, not 0 |
| funding history past retention | stop retries | `broker_funding_history_expired` | still `NULL` |
| exit fee unknown / third currency | final PnL unavailable | `known_economics_missing_components` | `NULL` fee ≠ 0 |
| reopen after administrative close | freeze demo adopt/orphan-close and Learning | active incident, startup gate | — |
| incident detector unavailable | suspend demo reconciliation | `detector_unavailable` | — |
| JSONL mirror failure | accounting kept | `audit_mirror_status=pending`, degraded job | — |

## Authority

This capability may classify closes, commit close economics once and freeze reconciliation
and Learning during an incident; it may never fabricate fills, fees, funding or PnL, change
Risk limits, place or cancel orders, or grant Outcome eligibility to an administrative close.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| OI-C01 | `OI-S02` (`tests/specs/spec_outcome_integrity.py`); `tests/test_outcome_consumers.py` |
| OI-C02 | `OI-S01`, `OI-S02` |
| OI-C03 | `OI-C04` (claim); `tests/test_outcome_closure.py` |
| OI-C04 | `OI-C03` (claim); `tests/test_pnl_pipeline.py` |
| OI-C05 | `OI-B04` |
| OI-C06 | `tests/test_reconciliation_integrity_incident.py` |
| OI-C07 | `OI-B03`; `tests/test_position_update_ownership.py` |
| OI-C08 | `tests/test_broker_funding_economics.py` |
| OI-C09 | `EX-B21` (`tests/specs/spec_broker_funding_settlement.py`) |
| OI-C10 | `EX-B19`; `tests/test_outcome_closure.py`, `tests/test_broker_funding_economics.py` |
| OI-C11 | `tests/test_broker_funding_economics.py`, `tests/test_outcome_closure.py` |
| OI-C12 | `EX-B18` (`tests/specs/spec_broker_funding_fairness.py`) |
| OI-C13 | `tests/test_broker_funding_economics.py` |

The executable claims in the `outcome_integrity` claim namespace (`OI-S*`, `OI-B*`, `OI-C*`)
predate this contract and number their own sections; a claim id such as `OI-C03` is not the
requirement with the same id here. The table above maps them explicitly.

## Decision references

- Central semantics version `oi-v2`; consumer migration enforced by `OI-C02`…`OI-C04`
  inventory tests; Risk moved to authoritative account loss in RG-T09.
- Incident breaker and owner resolution: OI-B04 lineage.
- Local simulated funding model retired:
  [history](../history/local_paper_account_controls.md#retired-funding-model).
- Settlement ownership investigation:
  [settlement ownership evidence](../../research/audits/SLIM_CORE_SETTLEMENT_OWNERSHIP_2026-09-12.md).
- Spot funding (`funding_cost=0`, `funding_source='not_applicable'`) remains as a residual
  branch in `portfolio/pnl.py`; spot is out of product scope (owner 2026-10-09).
