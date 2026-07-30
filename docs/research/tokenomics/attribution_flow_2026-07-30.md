# Attribution Flow: Caller → Ledger — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: understand how "who caused this LLM call" flows from a caller
into `runtime_cost_events`, so a future budget gate can scope by the
same identities the ledger already knows how to attribute.

Sources: `aletheia/agent_runtime/run_context.py`,
`aletheia/agent_runtime/orchestrator.py`,
`aletheia/agent_runtime/pi_daemon.py`,
`aletheia/economics/tracing.py`,
`aletheia/model_provider/base.py`,
`aletheia/economics/viability.py`.

## Two independent identity ContextVars

`run_context.py` exposes two `ContextVar`s and their bind/reset helpers:

- `_CURRENT_AGENT_RUN_ID` — bound by `bind_agent_run(run_id)` or the
  raw `set_current_agent_run_id`. Represents the surrounding
  `agent_run` row. Read back by
  `current_agent_run_id()` and consumed by
  `economics/tracing.py::_current_run_id()`.
- `_CURRENT_ECONOMIC_ATTRIBUTION` — bound by
  `bind_economic_attribution(**values)`. Holds a dict of explicit
  accounting fields. `set_current_economic_attribution` **strips**
  None and empty values, and stores `None` when the resulting dict is
  empty — a "clear" and a "set with no fields" are indistinguishable
  by design. Read back by `current_economic_attribution()` and
  consumed by `ModelProvider._cost_metadata` in `base.py`.

The two vars are independent: setting one does not affect the other,
and both can be active simultaneously (though the daemon path
deliberately does not bind `_CURRENT_AGENT_RUN_ID`).

## Where identity is bound

**Normal agent runs — `orchestrator.py::run_agent`**

At the start of every scheduled agent run:

```python
run_token = set_current_agent_run_id(run.id)
try:
    with inference_priority(priority_for_agent(agent_name, trigger_type)):
        result = await agent_fn(permission_mode=permission_mode, run_id=run.id)
finally:
    reset_current_agent_run_id(run_token)
```

Every LLM call the agent makes (through `helpers.llm_*` → provider) is
therefore automatically stamped with `run.id` in the ledger. The
`inference_priority` block adds a second dimension used by the
inference broker (see `routing_and_fallback_2026-07-30.md`, to be
written next) — it is not an economics attribution.

**Event-driven trading decisions — `pi_daemon.py`**

The PI daemon deliberately does **not** create an AgentRun (PR #110
rationale: it would fabricate identity for every event). It binds a
per-evaluation attribution instead:

```python
evaluation_id = "operator_eval_" + uuid4().hex[:16]
with bind_economic_attribution(
    logical_agent="pi_trading_decision",
    evaluation_id=evaluation_id,
    ...
):
    ...
```

The physical LLM call inside this block therefore has `run_id=NULL`
but `metadata_json.$.logical_agent = "pi_trading_decision"` and
`metadata_json.$.evaluation_id = "operator_eval_..."`.

**Runtime session provenance — a third dimension**

`tracing.py::_current_provenance` reads
`aletheia.agent_runtime.runtime_session.current_provenance()` and
merges it into the ledger row alongside `run_id` and the attribution
metadata. This gives every attempt a runtime-session identity as well
(surviving process restart identity). Not consulted for budget scoping
directly, but noted for completeness.

## How identity reaches the ledger

Two independent paths, both flowing into a single `runtime_cost_events`
row per attempt:

1. **`run_id` path.** `ModelProvider.__init_subclass__` wraps each
   provider's `complete/structured/stream`. When it calls
   `record_llm_attempt`, that function reads
   `current_agent_run_id()` (via `_current_run_id`) and writes it into
   the row's `run_id` column.

2. **Attribution metadata path.** The wrapper builds `metadata` via
   `_cost_metadata(self, mode=...)`, which pulls
   `current_economic_attribution()` into the dict. `record_llm_attempt`
   serialises that dict into the row's `metadata_json` column, with an
   `identity_source = "explicit_economic_context"` marker when non-empty.

Both paths always run, regardless of which caller path the LLM call
came from. If neither ContextVar is set (e.g., a `startup_gate` probe
or an admin `/api/model/probe` route), the row still lands — with
`run_id = NULL` and only the `provider_class` metadata — and gets
`cost_status = 'unknown'` if no pricing snapshot matches.

## How the ledger reads identity back

`viability.py` shows the read-side pattern: it treats an attempt as an
"Operator" cost when either:

- `metadata_json.$.logical_agent IN ("pi_trading_decision", "system_operator")`, or
- the attempt's `run_id` maps to an AgentRun for one of those agents.

Neither the daemon nor the orchestrator path is privileged; both are
valid attribution shapes for the same audit.

## Weak spots for a future budget gate

**Empty attribution.** Startup-gate probes and admin/UI test-completion
routes bypass both bindings. Their attempts land with
`run_id = NULL` and no `logical_agent`. A per-scope budget would either
have to ignore them, group them under a synthetic `system_probe` scope,
or explicitly bind an attribution in those code paths.

**Nested runs.** ContextVars propagate across `await` naturally, so an
async task inherits its caller's `run_id`. A thread pool executor does
not — `run_agent`'s `run.id` will not reach an LLM call made from a
`threading.Thread` unless the code copies the context. No current
callers appear to do this, but it is a footgun to remember.

**Ambiguous binding when both are set.** Nothing prevents a caller
from being inside both `bind_agent_run` and `bind_economic_attribution`
simultaneously (an orchestrator agent that then calls
`bind_economic_attribution` for a sub-evaluation, for example). Both
identities would flow into the row: `run_id` set, and `metadata_json`
populated. A budget gate must decide precedence (currently the ledger
reader treats these as OR, not AND).

**Attribution clearing.** `set_current_economic_attribution({})` is
equivalent to a clear, because empty values collapse to `None`. A
caller wanting to explicitly signal "no attribution" cannot distinguish
that from "attribution unset". This is a design choice, but a budget
gate that treats "no attribution" as "unbudgeted global scope" must be
prepared for a caller to have cleared attribution deliberately.

## Implications for the harness proposal

The scopes available "for free" today, without any new plumbing, are:

- `run_id` (via `current_agent_run_id`) — per-AgentRun spend.
- `logical_agent` (via `current_economic_attribution`) — per-daemon
  spend, including `pi_trading_decision`.
- `provider` (already on the ledger row) — per-provider or per-tier
  spend.
- Global / no-scope — aggregate.

Phase 2 (shadow-mode budget declarations) can therefore introduce
`economics_budgets` with `scope` values drawn from this set without
requiring any changes to the identity-binding paths. Phase 3
enforcement would consult the same identities. Phase 5's proposed
"uniform harness environment header" is the natural place to expose
the caller-scoped budget-remaining value to the caller itself, if we
decide callers should see it.
