# Status Surface Contract

## Purpose and current scope

This contract owns the **status surface**: the set of backend facts the operator
UI turns into one headline word, the **order** in which they are consulted, and the
**coupling rule** that keeps both ends of that seam moving together.

It exists because the surface is derived in the browser from five independent
backend facts, so a large backend iteration can change what the panel says without
touching a single frontend line — and did: `#3702` retired a route the in-repo
operator shell still called, the shell degraded *itself*, and the panel honestly
rendered `BLOCKED` while `/api/health` was `healthy` (`#3763`). The display was
never the defect; the input was, and nothing connected the two.

Out of scope: table-level empty/loading/unavailable presentation (owned by the
[Dashboard Data Presentation Contract](dashboard_data_presentation_contract.md)),
and the operator's own behaviour (owned by [Agent Contracts](agent_contracts.md)).
The process that produces the operator fact is described in
[The Operator runtime](../architecture/OPERATOR_SHELL.md).

## Entry points

| End | Where |
|---|---|
| Derivation | `frontend/src/lib/systemState.ts` — `getOperatorDisplayStatus()`, `deriveMasterState()` |
| Rendering | `frontend/src/components/StatusCluster.tsx` (panel + popover), `MasterState` consumers |
| Producer of the operator fact | the supervised `operator-shell/` process — see [The Operator runtime](../architecture/OPERATOR_SHELL.md) |
| Backend inputs | `GET /api/status`, `GET /api/operator/status`, `GET /api/health`, `GET /api/risk/status`, `GET /api/risk/kill-switch`, and the shell bootstrap |

## Durable state and consumers

The status surface itself is **not** durable — it is derived per render. Its
**inputs** are:

| Input | Durable source | Consumed as |
|---|---|---|
| Gateway running | gateway pid/lifecycle | `gatewayRunning` |
| Kill switch | risk kill-switch state | `killed` |
| Risk verdict | `risk_decisions` / risk status projection | `riskState` |
| Health | health report incl. `issues[]` | `healthState`, `healthIssues` |
| Operator | `agent_shell_heartbeats` (`agent_id=operator`) via `/api/operator/status` | `operatorStatus`, `operatorMode`, `operatorTradingBlocked` |
| Scheduler | scheduler runtime state | `paused`, `schedulerEnabled`, `schedulerRunning` |

The **only** declared consumers of these inputs are the frontend derivation above
**and** the operator shell itself (which reads the same endpoints under its own
least-privilege allowlist, `core/aletheia/server/operator_service_auth.py`). No
third consumer is authorized; adding one is a change to this contract.

## Observable result

`getOperatorDisplayStatus` maps a heartbeat to exactly three user-facing states:

- `offline`/`stale` heartbeat → **Offline**;
- mode ∈ `{Repairing, Degraded, Paused}` (`_TRADE_BLOCKING_MODES`) → **Blocked**
  (yellow, with the mode-specific `blockedDetail`; the raw mode name is debug-only);
- anything else → **Running**.

`deriveMasterState` resolves one headline by **this order, most severe first** —
the order is part of the contract, not an implementation detail:

1. gateway stopped → `OFFLINE` (red)
2. kill switch engaged → `HALTED` (red)
3. `riskState === "blocked"` → `BLOCKED` (red)
4. health issue `exchange_unreachable` → `BLOCKED` (red)
5. operator process offline → `OFFLINE` (red)
6. operator heartbeat stale → `STALE` (yellow)
7. operator in a trade-blocking mode → `BLOCKED` (yellow)
8. operator online but not evaluating signals → `BLOCKED` (yellow)
9. scheduler paused → `PAUSED` (yellow)
10. scheduler enabled but stopped → `DEGRADED` (yellow)
11. degraded health state → `CAUTION` (yellow)
12. `riskState === "warning"` → `CAUTION` (yellow)
13. `market_data_never_collected` / `agent_failure_ratio:*` / `health_check_error:*` → `DEGRADED` (yellow)
14. scheduler disabled/unknown → `STANDBY` (blue)
15. otherwise → `OPERATIONAL` (green) / `STANDBY` when risk is unknown

## Invariants and trust boundaries

- **One headline, derived, never stored.** No backend field names the headline; the
  order above is the authority. A producer may not claim a headline.
- **Absence is not health.** Missing/stale inputs render `OFFLINE`/`STALE`/`STANDBY`,
  never green. An API failure is `unavailable`, never "healthy".
- **A blocking mode is a fact to display, not to hide.** If the operator reports
  `Repairing`/`Degraded`/`Paused`, the panel says `BLOCKED`. Fixing the *panel* is
  never the repair for a polluted input.
- **The coupling rule.** A change to any input above — a removed or renamed route,
  a field dropped from a response, a changed meaning — MUST update every declared
  consumer in the same change: the operator shell, this derivation's inputs, and the
  tests below. Where enforceable, that obligation is a test, not a sentence:
  `tests/test_api_consumer_paths.py` fails when the operator shell names a path the
  route table does not serve or the allowlist does not authorize, and fails when the
  allowlist authorizes nothing.
- Producer-side honesty still applies: the operator's own `last_error` must remain
  the literal reason (`gateway_request_failed:404:…`), because it is the only field
  that named the `#3763` incident.

## Failure and recovery semantics

- **Operator process death**: heartbeat ages past staleness → rules 6/5 → `STALE`
  then `OFFLINE`; the supervisor restarts it and the headline clears without human
  action.
- **Operator self-degrade on a broken dependency** (the `#3763` shape): the shell
  stays alive, so rules 5/6 do not fire; its *mode* carries the damage and rule 7
  renders `BLOCKED`. Recovery is repairing the dependency (or the shell's handling
  of it) — not the panel.
- **Backend route retired beneath a consumer**: the consumer's own error field
  carries it (a 404 in `last_error`), the mode degrades, the panel blocks. The guard
  test above turns this from "hours of archaeology" into a red test.
- **Partial input failure**: each input degrades alone — a failed health read does
  not erase a risk verdict, and vice versa.

## Executable evidence

- `frontend/src/lib/systemState.test.ts` — derivation order, the blocking-mode set,
  and the "never operational while the enabled scheduler is stopped" case.
- `tests/test_api_consumer_paths.py` — the consumer↔allowlist↔route-table coupling,
  with a positive control pinning the `#3763` path itself.
- `tests/test_operator_service_auth.py` — the operator surface's refusals (pause,
  settings, gateway restart, kill switch).
- `frontend/src/components/StatusCluster.test.tsx` (where present) — the panel's
  rendering of the derived states.

## Current scope boundary

- The panel shows the operator's **mode**, not its `last_error`; a 404-driven
  degrade therefore reads as a generic blocked detail. Surfacing the operator's own
  `last_error`/`current_intent` in the blocked area is tracked in `#3763` and is a
  display-only change.
- `tests/test_api_consumer_paths.py` sees surfaces that name API paths as literals.
  The frontend composes URLs from a base constant, so its coupling is covered only
  at the type level (`npm run check:api-types` pins the generated schema, not route
  existence). That boundary is asserted in the test itself so it cannot rot silently.
- Only the operator/backend and frontend/backend seams are checked. A consumer
  outside this repository is not visible to any static check here.
