# The Operator runtime (`operator-shell/`)

> **What this is**: the current, factual description of the supervised TypeScript
> process that acts as Aletheia's top-level operator. It answers "what is this
> thing, what may it do, how is it kept alive, and how do I diagnose it".
>
> **What this is not**: the operator's *design history*. The concept document is
> [`../history/operator_layer.md`](../history/operator_layer.md), which opens
> with its own warning that it is not implementation status. Anything about the
> retired Python assessment loop lives there and in
> [`../history/`](../history).

## 1. What it is, in one paragraph

A long-running Node process, started and restarted by the Gateway supervisor, that
watches the system from the outside and turns what it sees into **structured
operator state**: a heartbeat, sessions, tasks, events, artifacts and
recommendations — all persisted through the backend's
`/api/operator/internal/*` surface into SQLite. It does not trade, does not hold
Risk authority, and does not run its own model loop: when it needs reasoning it
calls back into the Python side. Its job is supervision, diagnosis, delegation and
making state legible; every consequential action still goes through the same
Trading/Risk/Execution boundaries as any other caller.

## 2. Entry points and lifecycle

| Concern | Where |
|---|---|
| Process entry | `operator-shell/src/index.ts` → `OperatorLoop.start()`, SIGINT/SIGTERM → `stop()` |
| Supervision | `core/aletheia/gateway/operator_shell.py` (`OperatorShellMonitor`), started by `core/aletheia/_gateway_runner.py`; PID file `<data>/operator-shell.pid` |
| Checkout used | `ALETHEIA_OPERATOR_SHELL_ROOT` if set, otherwise `<repo>/operator-shell` |
| Log | `<data>/logs/operator-shell-errors.log` (+ the doctor's redacted tail) |
| Heartbeat | `agent_shell_heartbeats` with `agent_id=operator`; interval `ALETHEIA_OPERATOR_HEARTBEAT_SEC` (default 10s) |
| Identity | `mainSessionId` (`ALETHEIA_OPERATOR_MAIN_SESSION_ID`, default `operator-main`), `modelProvider` (default `pi-shell`), `runtimeId` |
| Liveness probe | `aletheia doctor` → `operator.shell` (`diagnostics/probes/trading.py`) |
| Status projection | `GET /api/operator/status` — the shell's own scope (`status_scope=operator_shell`) plus a **separate** `trading_daemon` fact |

Because the supervisor starts it from the deployed checkout, **a change to
`operator-shell/` takes effect on the next Gateway restart**, not on its own.

## 3. What it actually does (the loop)

1. **Heartbeat** — writes the durable row that everything else keys off
   (`heartbeat.ts`), including the mode, permission mode, busy flag, current task
   and a human-readable `current_intent`.
2. **Subsystem review** — reads a fixed set of backend summaries (§4) once per
   cycle and diffs them against what it already knows.
3. **Recommendation synthesis** — `recommendation_engine.ts` turns those facts into
   typed recommendations (`dreaming_due`, `daily_review_refresh_due`,
   `worker_health_review_due`, `*_proposal_due`, …), each carrying the evidence and
   the permission level it would require.
4. **Task materialization** — recommendations that the current permission level
   allows become durable `agent_shell_tasks`, dispatched to the owning agent
   (`task_runtime.ts` maps a task type to an agent), with sessions and message
   traces so a task is auditable rather than implicit.
5. **Repair decisions** — `repair_engine.ts` may materialize a *proposal* or a
   low-risk diagnostic action, gated on permission mode.
6. **Reasoning, when needed** — `pi_runtime_adapter.ts` posts to
   `/api/operator/internal/llm-session`; the model call itself is the Python
   side's business, which is what keeps the operator's authority outside the shell.

## 4. What it may read and write (its whole surface)

Least-privilege, enforced by `core/aletheia/server/operator_service_auth.py` on a
service credential — **not** owner auth, and not a broad API token:

- **Read**: `/api/status`, `/api/health`, `/api/operator/status`, `/api/operator/events`,
  `/api/operator/sessions`, `/api/operator/commands`, `/api/operator/tasks`,
  `/api/operator/extensions`, `/api/dreaming/summary`, `/api/reviews/summary`,
  `/api/risk/summary`, `/api/portfolio`, `/api/market/portfolio-history`, `/api/agent-runs`.
- **One bounded specialist-delegation mutation**: `POST /api/agent-runs/start`.
- **Internal bridge** (`/api/operator/internal/*`, GET/POST/PATCH): heartbeat,
  messages, tasks, task updates, events, artifacts, sessions, context query,
  command execution, compaction, and the LLM session call.

It cannot pause the system, change settings, restart the gateway, touch the kill
switch, or place orders — `tests/test_operator_service_auth.py` pins those refusals.

## 5. Permission ladder

`ReadOnly(0) < Research(1) < PaperAuto(2) < AllowEditing(3) < Auto(4)`, from
`ALETHEIA_OPERATOR_PERMISSION_MODE` (default `ReadOnly`). Task types declare the
level they need (`Research` for reviews, `AllowEditing` for proposals, `PaperAuto`
for operational commands); `RepairEngine` may auto-execute above `ReadOnly` and
create proposals only at `AllowEditing`/`Auto`. A task the current level does not
permit is not silently dropped — it is not materialized, which is why "the task
never appeared" is usually a permission fact, not a bug.

## 6. How it fails, and what that looks like

| Failure | Symptom | Where to look |
|---|---|---|
| Process gone or crash-looping | heartbeat ages out; doctor `operator.shell` warns with a restart hint | `operator-shell-errors.log`, `operator-shell.pid`, `systemctl --user` |
| A backend route it calls is retired or renamed | its own `last_error` records `gateway_request_failed:404:…`, its mode degrades, **and the status panel derives a blocked headline from that mode** | `GET /api/operator/status` |
| A field it reads disappears from a response | same shape: `last_error`/degrade, or a missing prompt section | the same endpoint, plus the shell's own tests |
| Permission mode too low | recommendations exist but no tasks appear | `permission_mode` on the status endpoint |

**Worked example (2026-09-29, #3763)**: the Schema Convergence program retired the
Current Strategy product surfaces in `#3702`, and the operator shell — the eighth
consumer, in the same repository — kept calling `GET /api/strategies/summary` and
reading `missing_review_strategy_ids`. The backend answered 404, the shell recorded
`gateway_request_failed:404:…` and degraded its mode to `Degraded`, and the status
panel honestly rendered BLOCKED while `/api/health` was `healthy`. The display was
never wrong; the input was. `tests/test_api_consumer_paths.py` now makes that seam
loud: every path this shell names must exist in the served route table and be
allowlisted, and the allowlist may not name anything dead.

## 7. Related authority

- [Agent Contracts §E](../contracts/agent_contracts.md) — the operator's behavioural
  contract, requirements OP-C01..OP-C06; this page is the diagnostic companion.
- [Status Surface Contract](../contracts/status_surface_contract.md) — how the
  operator's state and the other status inputs become what the UI shows, and the
  coupling rule that keeps the two ends of this seam moving together.
- [System Map](SYSTEM_MAP.md) — where the code lives.
- [Cross-platform service environment](../../operations/deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md)
  — supervision and `ALETHEIA_OPERATOR_SHELL_ROOT`.
- `operator_layer.md` — the concept/history document this one deliberately is not.
