# Status Surface Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `SS` · **Owner code:** `frontend/src/lib/systemState.ts`

## Scope

The status surface is the one headline word (and operator badge) the dashboard shows
for "can this system trade right now". It is derived in the browser, per render, from
independent backend facts; this contract owns which facts are consulted, the order in
which they are consulted, and the coupling rule that keeps backend and consumers moving
together. It does not own table-level empty/loading/unavailable presentation
([Dashboard Data Presentation Contract](dashboard_data_presentation_contract.md)) or the
operator's own behavior ([Agent Contracts](agent_contracts.md)); the process that
produces the operator fact is described in [The Operator runtime](../architecture/OPERATOR_SHELL.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `frontend/src/lib/systemState.ts::deriveMasterState` | call | `frontend/src/components/StatusCluster.tsx::StatusCluster` |
| `frontend/src/lib/systemState.ts::getOperatorDisplayStatus` | call | `StatusCluster`, `frontend/src/pages/CommandCenter.tsx` |
| `GET /api/status`, `/api/operator/status`, `/api/health`, `/api/risk/status`, `/api/risk/kill-switch`, shell bootstrap | API inputs | `StatusCluster` queries |
| `core/aletheia/server/operator_service_auth.py::operator_service_route_allowed` | allowlist | the supervised `operator-shell/` process reading the same inputs |

## Durable state

The headline is not durable. Its inputs are:

| Input | Durable source | Consumed as |
|---|---|---|
| Gateway running | gateway pid/lifecycle | `gatewayRunning` |
| Kill switch | risk kill-switch state | `killed` |
| Risk verdict | risk status projection | `riskState` |
| Health | health report incl. `issues[]` | `healthState`, `healthIssues` |
| Operator | `agent_shell_heartbeats` (`agent_id=operator`) via `/api/operator/status` | `operatorStatus`, `operatorMode`, `operatorTradingBlocked` |
| Scheduler | scheduler runtime state | `paused`, `schedulerEnabled`, `schedulerRunning` |

The declared consumers of these inputs are the frontend derivation and the operator
shell (under its least-privilege allowlist). Adding a third consumer is a change to this
contract.

## Requirements

### SS-C01 Operator badge has three states

`The status surface shall map an operator heartbeat to exactly one of Offline, Blocked or Running, showing the raw mode only as debug text.`

- WHEN the heartbeat is missing or `offline` THEN the badge is Offline (red); WHEN it is `stale` THEN Offline (yellow).
- WHEN the mode is in `{Repairing, Degraded, Paused}` THEN the badge is Blocked (yellow) with that mode's `blockedDetail`.
- WHEN neither applies THEN the badge is Running (green).

Evidence: `frontend/src/lib/systemState.ts::getOperatorDisplayStatus`, `_TRADE_BLOCKING_MODES`.

### SS-C02 Headline order is the authority

`The status surface shall resolve one headline by taking the first matching rule in the order below, most severe first.`

| # | Condition | Headline |
|---|---|---|
| 1 | gateway stopped | `OFFLINE` red |
| 2 | kill switch engaged | `HALTED` red |
| 3 | `riskState === "blocked"` | `BLOCKED` red |
| 4 | health issue `exchange_unreachable` | `BLOCKED` red |
| 5 | operator offline | `OFFLINE` red |
| 6 | operator heartbeat stale | `STALE` yellow |
| 7 | operator mode in `{Repairing, Degraded, Paused}` | `BLOCKED` yellow |
| 8 | operator online but trading daemon not evaluating on a gateway health issue | `BLOCKED` yellow |
| 9 | scheduler paused | `PAUSED` yellow |
| 10 | scheduler enabled but stopped | `DEGRADED` yellow |
| 11 | health state in `{degraded, unhealthy, warning, attention}` | `CAUTION` yellow |
| 12 | `riskState === "warning"` | `CAUTION` yellow |
| 13 | issue `market_data_never_collected`, `agent_failure_ratio:*` or `health_check_error:*` | `DEGRADED` yellow |
| 14 | scheduler disabled or not observed | `STANDBY` blue |
| 15 | health not `healthy` | `STANDBY` blue |
| 16 | `riskState === "healthy"` | `OPERATIONAL` green |
| 17 | otherwise (risk not evaluated) | `STANDBY` blue |

- WHEN the kill switch is engaged and the scheduler is paused THEN the headline is `HALTED`.
- WHEN the scheduler is enabled and stopped THEN the headline is never `OPERATIONAL`.

Evidence: `frontend/src/lib/systemState.ts::deriveMasterState`.

### SS-C03 The headline is derived, never stored

`The status surface shall compute the headline only in the browser from the inputs above, and no backend field or producer shall name a headline.`

- WHEN a backend response gains a headline-like field THEN `deriveMasterState` does not read it.

### SS-C04 Absence is not health

`If an input is missing, stale or failed to read, then the status surface shall render a non-green headline (OFFLINE, STALE or STANDBY), never OPERATIONAL.`

- WHEN health is unavailable or unknown THEN the headline is not green.
- WHEN the operator status is absent THEN it is treated as `offline`.

Evidence: `frontend/src/components/StatusCluster.tsx::StatusCluster` (`operatorStatus` defaults to `offline`, `riskState`/`healthState` default to empty), `deriveMasterState` rules 14–17.

### SS-C05 A blocking mode is displayed, not hidden

`While the operator reports a trade-blocking mode, the status surface shall show BLOCKED, and the repair shall be made to the input, never to the panel.`

- WHEN the operator self-degrades on a broken dependency while its process stays alive THEN rule 7 renders `BLOCKED`.

### SS-C06 Producer error stays literal

`The operator shell shall keep its own last_error as the literal failure reason (for example gateway_request_failed:404:…).`

- WHEN a route the shell calls returns 404 THEN `last_error` starts with `gateway_request_failed:404:<METHOD> <path>`.

Evidence: `operator-shell/src/gateway_errors.ts::gatewayRequestFailure`.

### SS-C07 Coupling rule

`When any input route, response field or meaning changes, the change shall update every declared consumer — the operator shell, this derivation and their tests — in the same change.`

- WHEN the operator shell names a path the route table does not serve THEN `tests/test_api_consumer_paths.py` fails.
- WHEN the operator shell names a path the allowlist refuses THEN it fails.
- WHEN the allowlist names a path no route serves THEN it fails.

### SS-C08 Inputs degrade independently

`If one input read fails, then the status surface shall keep every other input's last value and degrade only that input.`

- WHEN the health read fails THEN a known risk verdict still drives rules 3 and 12.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| operator process dies | heartbeat ages out; supervisor restarts it | `STALE` then `OFFLINE`; clears without human action | absent heartbeat = offline |
| operator self-degrades on a broken dependency | mode carries the damage | `BLOCKED` (rule 7), mode-specific detail | — |
| backend route retired under a consumer | consumer's own error field carries the 404 | `BLOCKED`; guard test turns it red in CI | — |
| one input read fails | that input alone degrades | non-green headline if the failed input is required for green | `unavailable`, never "healthy" |

## Authority

The status surface displays; it never changes Risk, the kill switch, the scheduler, the
operator or any backend state.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| SS-C02, SS-C04 | `frontend/src/lib/systemState.test.ts` (order, health/scheduler never green when unknown or stopped) |
| SS-C02, SS-C04 | `frontend/src/components/StatusCluster.test.tsx` (scheduler snapshot passed to the derivation; missing infra key renders unavailable) |
| SS-C07 | `tests/test_api_consumer_paths.py` (incl. positive control on a retired route) |
| SS-C07 (operator refusals) | `tests/test_operator_service_auth.py` |
| SS-C01, SS-C03, SS-C05, SS-C06, SS-C08 | not covered by a dedicated test |

## Known conformance gaps

- **Kill-switch absence reads as "not engaged"** (SS-C04). `frontend/src/components/StatusCluster.tsx::StatusCluster`
  computes `killed = killSwitchQuery.data?.active ?? data?.kill_switch?.active ?? false`, so when neither the
  detail query nor the bootstrap carries the kill-switch fact, rule 2 cannot fire and the headline can reach
  `OPERATIONAL`.
- **Rule 8 matches free text.** `operatorTradingBlocked` requires `current_intent` to contain
  `"gateway health issue"`; a wording change in the operator shell silently disables rule 8.

## Current scope boundary

- The panel shows the operator's mode, not its `last_error`.
- `tests/test_api_consumer_paths.py` sees only surfaces that name API paths as literals; the frontend
  composes URLs from a base constant, so its coupling is covered only at the type level
  (`npm run check:api-types`), and the test asserts that limit itself.
- A consumer outside this repository is not visible to any static check here.

## Decision references

- #3702 retired `/api/strategies/summary` while the operator shell still called it; the shell degraded,
  the panel showed `BLOCKED` while `/api/health` was healthy (#3763). That incident is why the order,
  the coupling rule and `tests/test_api_consumer_paths.py` exist.
