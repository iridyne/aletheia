# LLM Call-Site Inventory — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: locate every code path that ends up in a physical `ModelProvider`
call, so the Phase 3 budget-enforcement gate can be placed where it will
catch all real spend without missing routes.

Sources of evidence: repository search over `aletheia/` for `ModelRequest(`,
`StructuredModelRequest(`, `.complete(`, `.structured(`, `.stream(`,
`get_provider(`, `get_provider_async(`, `get_provider_for_tier(`, imports
from `aletheia.model_provider.*`, and imports from `aletheia.agents.helpers`.

## The funnel: three shared helpers

**Almost every LLM call in the repository goes through `aletheia/agents/helpers.py`.**
That file exposes three entry functions and no other public LLM primitive:

- `llm_complete(system, user, *, max_tokens, provider) -> (content, error)` — plain text.
- `llm_structured(system, user, *, schema, max_tokens, provider, fallback_to_defaults) -> (dict, error)` — 3-attempt escalating retry, corrective repair prompt on parse failure, empty-content handling for reasoning models, `/no_think` prefix for Qwen-style models, `fallback_to_defaults` mode for non-critical callers.
- `llm_stream(system, user, *, max_tokens, provider) -> AsyncIterator[StreamChunk]` — routed through `brokered_stream`.

Common behavior across all three:

- Never raise. Failures become `(None, error_string)` or a single terminal `StreamChunk(done=True)`.
- Resolve the provider once via `get_provider(provider_name)`; the tier is chosen by the caller with `get_provider_for_tier("deep"|"fast")` and passed as `provider=`.
- Route the physical call through `complete_with_fallback` (or `brokered_stream` for streaming).
- Feed the health monitor via `_record_llm(success)` — a bounded consecutive-failure counter separate from the economics ledger.

This funnel is the natural insertion point for a pre-call budget gate, a
rate-limit check, and a prompt-redaction scan. Any caller that goes
through these three helpers inherits any new pre-flight enforcement for
free.

## Callers grouped by subsystem

The following files import `llm_complete`, `llm_structured`, or
`llm_stream` from `aletheia.agents.helpers` (or `log_tool` from the same
module, which is used alongside LLM calls):

**Agents (`aletheia/agents/`)**

- `divergence_scanner.py`
- `dreaming_agent.py`
- `equity_observer.py`
- `lesson_reviewer.py`
- `market_observer.py`
- `paper_trader.py`
- `portfolio_agent.py`
- `post_trade_reviewer.py`
- `prediction_observer.py`
- `research_agent.py`
- `research_worker.py`
- `risk_critic.py`
- `stock_observer.py`

**Agent shell (`aletheia/agent_shell/`)**

- `commands.py`
- `intent.py`
- `runtime.py`
- `synthesis.py`
- `trading_decision.py` — the PI daemon's trading decision path calls `llm_structured` here, so PI trading spend also flows through the funnel.

**Agent runtime (`aletheia/agent_runtime/`)**

- `orchestrator.py`
- `run_logger.py`

**Data, analytics, server**

- `aletheia/data/news_analyzer.py`
- `aletheia/analytics/insight_synthesizer.py`
- `aletheia/server/routes/operator.py`
- `aletheia/server/routes/news.py`
- `aletheia/storage/repositories.py` — imports `log_tool` for tool-call
  logging alongside the LLM helpers; not an LLM caller itself.

## Bypassers of `helpers.py`

Two paths call `complete_with_fallback` or `brokered_complete` directly
without going through `helpers.py`. Both are small verification probes,
not material spend, and both are still recorded by the `ModelProvider`
base-class tracing decorator, so their cost is visible in the ledger
even without helper coverage:

- `aletheia/agent_runtime/startup_gate.py` — issues a `"ping"` probe
  with `_PROBE_MAX_TOKENS` to verify a configured provider actually
  answers before allowing the system to unlock (PR #458).
- `aletheia/server/routes/model.py` — admin/UI test-completion
  endpoints (`"Say OK"`, `max_tokens=10` for a config-check route,
  `max_tokens=200` for an interactive probe on the settings page). Uses
  `brokered_complete` with an explicit `InferencePriority.INTERACTIVE`.

## Provider layer (not a caller — the callee)

The following files construct `ModelRequest` / `StructuredModelRequest`
inside the provider layer itself (they are the plumbing the helpers call
into, not the application-level call sites): `openai_compatible.py`,
`openai_native.py`, `anthropic_compatible.py`, `anthropic_native.py`,
`claude_cli.py`, `codex_cli.py`, plus `base.py` and `broker.py`.
`aletheia/update/packaged.py` matched the `.complete(` pattern for
unrelated update-checker logic, not LLM calls.

## Implications for the harness proposal

- **A pre-flight budget check in `helpers.py`** would cover essentially
  all agent traffic: the 13 agents, 5 agent-shell modules (including
  the PI trading decision), the 2 agent-runtime modules, and the
  data/analytics/server callers. Placement inside the three helpers
  keeps the check next to the existing `_record_llm` health signal and
  outside the ledger's post-call tracing.
- **A pre-flight check at `complete_with_fallback`** (one level lower)
  would additionally cover the `startup_gate` probe and the admin
  test-completion routes without special-casing. It would sit adjacent
  to the fallback/health logic that already keys on `provider_name`, so
  budget scopes could reuse the same key. Cost: a check on every retry
  and fallback attempt rather than once per logical call; the base-class
  tracing decorator already treats each attempt as a separate ledger
  event, so this is consistent with the existing "no collapsing of
  retries" invariant.
- **A pre-flight check in `ModelProvider.__init_subclass__`** (deepest,
  next to the tracing decorator) would catch literally every physical
  attempt. This is the most defensive placement but also the most
  invasive; the existing `__init_subclass__` is currently write-only
  measurement, and expanding it into a control decision would break the
  "measurement-only" boundary PR #110 established for that layer.

The likely design choice is to put the gate at `complete_with_fallback`
(covers helpers + the two bypassers), keep the base-class decorator
untouched as pure measurement, and let the three helpers surface the
denial to callers as an explicit error class rather than a hidden
`(None, error)` sentinel.

The `fallback_to_defaults` mode in `llm_structured` should keep its
current graceful-degradation semantics: a denied call is still an
outage from the caller's point of view; the harness only decides
whether it is a "budget exhausted" outage or a "provider down" outage.
