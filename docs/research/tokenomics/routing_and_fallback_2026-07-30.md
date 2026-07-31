# Routing, Broker, Fallback Semantics — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: understand the existing routing, admission-control, and fallback
plumbing between `agents/helpers.py` and a physical provider call, so
the harness proposal builds on real primitives instead of assumed gaps.

Sources: `aletheia/model_provider/router.py`,
`aletheia/model_provider/broker.py`,
`aletheia/model_provider/fallback.py`,
`aletheia/model_provider/base.py`.

## Update to the tracking-issue gap list

An earlier draft of the umbrella issue said "no visible RPM/TPM/parallel
ceiling per provider tier". That is only partly correct. **The broker
already implements per-provider concurrency, RPM, and TPM limits with
priority-based admission and a sliding-window token bucket.** The
remaining gaps are per-caller-scope rate limiting and any form of
$-denominated budget enforcement. Details below.

## `router.py` — provider resolution and tier translation

- `get_provider(name)` / `get_provider_async(name)` — build a provider
  from `cfg.models.providers[name]`, resolve OAuth tokens
  (synchronous or async refresh with a 5-minute expiry margin), and
  cache the instance. Cache is process-wide; `clear_cache()` and
  `clear_cache_async()` invalidate and close HTTP pools per loop.
- `get_provider_for_tier(tier)` — translates
  `"deep" | "fast" | None` into a configured provider name via
  `cfg.models.deep_provider` / `fast_provider` / `default_provider`.
  Unknown tiers fall back to `default_provider`; never raises.
- `list_models_cached(name, provider)` — 15-minute cross-loop
  single-flight cache of the provider's live model catalogue,
  consulted by both fallback and health.
- `healthcheck_all()` — 30-second cross-loop single-flight cached
  health snapshot; each provider's healthcheck runs on the broker
  loop via `inference_broker.run_auxiliary`.

## `broker.py` — process-wide admission, priority, rate limiting

The broker owns a persistent asyncio loop on its own thread. Every
completion (streaming or not) crosses into this loop via
`brokered_complete` / `brokered_stream`. This is both a correctness
mechanism (one place for HTTP pools and rate limits) and a control
mechanism (one place to see and shape LLM traffic).

**Priority ladder** (`InferencePriority` — lower value goes first):

| Value | Name          | Assigned to                                                                              |
|-------|---------------|------------------------------------------------------------------------------------------|
| 0     | CRITICAL      | Not currently assigned by `priority_for_agent`; reserved.                                |
| 10    | INTERACTIVE   | Manual triggers; admin/UI probes (`/api/model/probe`).                                   |
| 20    | TRADING       | `paper_trader`, `pi_trading_decision`, `position_manager`.                               |
| 40    | OBSERVER      | `market_observer`, `equity_observer`, `prediction_observer`.                             |
| 50    | DEFAULT       | Unassigned agents; the ContextVar default.                                               |
| 80    | BACKGROUND    | `dreaming_agent`, `dreaming_daily`, `post_trade_reviewer`, `risk_critic`, `insight_synthesizer`, `research_agent`. |
| 90    | HEALTH        | Not currently assigned by `priority_for_agent`; reserved.                                |

Priority is set via the `inference_priority()` context manager, which
`orchestrator.py::run_agent` opens around every agent run using
`priority_for_agent(agent_name, trigger_type)`. It is an independent
ContextVar from the two identity ContextVars covered in the attribution
flow note.

**Per-provider policy** (`ProviderPolicy`, loaded from
`cfg.models.providers[name]`):

- `inference_max_concurrency` — concurrent workers per provider.
- `inference_requests_per_minute` — RPM ceiling (0 = unlimited).
- `inference_tokens_per_minute` — TPM ceiling (0 = unlimited).
- `inference_queue_capacity` — bounded queue size.
- `inference_reserved_high_priority_slots` — queue slots reserved for
  `TRADING`-or-higher priorities; low-priority requests are rejected
  when only reserved slots are left.
- `inference_timeout_sec` — total deadline covering queue + rate-wait
  + inference.

A `_SlidingWindowLimiter` enforces RPM and TPM together: a request
reserves `estimate_request_tokens(request)` tokens (character-based
estimate of the prompt plus `max_tokens`) before it can start, waits
if the window is saturated, and if its reservation is bigger than
the entire per-minute budget it is rejected immediately with
`InferenceRateLimitExceeded` (fail-fast to avoid an infinite queue).

**Well-defined admission errors:**

- `InferenceQueueFull` — bounded queue rejected the submission.
- `InferenceTimeout` — deadline exceeded during queue, rate-wait, or
  execution.
- `InferenceRateLimitExceeded` — request larger than the provider's
  per-minute token budget.
- `InferenceBrokerClosed` — broker shutting down.

Callers do not currently handle these distinctly; `helpers.py`
collapses everything to `(None, "llm_unavailable: <detail>")`, so
detailed admission signals are lost by the time an agent sees them.
This is a known adjustment surface for Phase 3.

**Live snapshot.** `inference_broker.snapshot()` returns per-provider
policy plus metrics (`submitted, started, completed, failed,
cancelled, timed_out, rejected_queue_full, rejected_rate_limit,
active, queued, outstanding, peak_active, peak_outstanding,
queue_wait_*_ms, rate_wait_*_ms, request_duration_*_ms,
reserved_tokens, actual_tokens`). This is Phase 1 observability
already in place — a Settings tile could render this directly.

**Priority quirk.** `CRITICAL == 0`, so `priority or _CURRENT_PRIORITY.get()`
would silently drop CRITICAL. The code correctly uses
`priority if priority is not None else _CURRENT_PRIORITY.get()`. Any
future gate wrapping `submit` needs the same guard.

## `fallback.py` — request-level model fallback and per-model cooldown

`complete_with_fallback` is the function `helpers.py` actually calls.
It composes on top of the broker without replacing it:

- Builds a candidate model list per request: `[primary]` plus
  `pcfg.fallback_models`, plus the live-catalogue slugs from
  `list_models_cached`. Deduped and capped at
  `_MAX_MODELS_PER_REQUEST = 6`.
- Skips any candidate currently cooling down; if all candidates are
  cooling, emits `llm_all_models_cooling_down` (critical severity) and
  raises `RuntimeError`.
- For each remaining candidate, submits the request via
  `brokered_complete` — so every attempt goes through the broker's
  admission and rate-limit control, not just the first one.
- On success: `_record_model_success` clears cooldown; if the model
  actually failed over, emits `llm_model_fallback_used` (warning
  severity, deduped 30s).
- On transient failure (429/502/503/504, network errors,
  rate/capacity text markers): `_record_model_failure` increments the
  per-`(provider, model)` failure counter; ≥ 2 → 300s cooldown +
  `llm_model_cooldown` event (warning severity).
- Non-transient errors are re-raised immediately without cooldown.
- Response is annotated with `raw.aletheia_model_fallback = {provider,
  requested_model, final_model, attempted_models,
  skipped_cooldown_models, fallback_used}` so downstream telemetry can
  see the actual model that answered.

Health state (`_MODEL_HEALTH`, guarded by `_MODEL_HEALTH_LOCK`) is
snapshot by `get_model_health_snapshot()` — another Phase 1 surface.

## How retries and fallback share `call_id`

`ModelProvider.__init_subclass__` in `base.py` wraps every provider's
`complete/structured/stream`. On each call it looks at the request
object for a `_aletheia_cost_call_id`; if absent it assigns one and
also initialises `_aletheia_cost_attempt_index = 0`. Each subsequent
attempt increments `attempt_index`. `fallback.py` reuses the exact
same `ModelRequest` object across attempts and only swaps the
provider's `model` attribute via `_clone_provider_with_model`, so:

- All attempts within one `complete_with_fallback` call share one
  `call_id`.
- Each attempt writes a distinct `runtime_cost_events` row with a
  unique `(call_id, attempt_index, event_type)` triple.
- The final ledger view therefore shows one logical call composed of
  N physical attempts, matching PR #110's stated invariant that
  "retries and fallback attempts are never collapsed into the final
  successful call".

## Insertion points for a future harness gate

Given the layers above, four candidate placements exist for a
pre-flight check that could deny a call for budget, rate, or
guardrail reasons.

| Placement | Covers | Trade-offs |
|---|---|---|
| Inside `helpers.py::llm_*` | All agents + agent_shell + agent_runtime + data/analytics/server callers. | Misses `startup_gate` and admin `/api/model/probe`. Simple to add; sits next to the existing `_record_llm` health signal. |
| Inside `fallback.py::complete_with_fallback` (entry, before the loop) | Everything the helpers cover **plus** the two bypassers, which also go through this function. | One check per logical call. Does not add per-attempt cost. Natural place because provider-name is resolved here. |
| Inside `broker.py::submit` (before enqueueing) | Every physical attempt including each fallback retry. | Charges every retry against the check, which is desirable for rate limits but wrong for once-per-logical-call budget checks. Consistent with the existing `estimate_request_tokens` reservation model. |
| Inside `base.py::__init_subclass__` (tracing decorator) | Every physical attempt at the lowest level, including `helpers` bypassers and any future direct callers. | Breaks PR #110's explicit "measurement-only" boundary for this layer. Rejected on principle unless the alternative is materially worse. |

The likely design is a **two-layer split**:

- Once-per-logical-call gates (budget, prompt redaction) go inside
  `complete_with_fallback` before candidate selection. Denial raises
  a new explicit error class the helpers can surface distinctly from
  `InferenceQueueFull` / `InferenceTimeout` / provider errors.
- Per-attempt gates (per-scope rate limiting extending the broker's
  existing per-provider limiter) go inside the broker, keyed by
  scope from the same identity ContextVars the ledger already reads.

This keeps the base-class tracing decorator untouched, keeps the
broker as the single source of truth for admission, and keeps
`helpers.py` free of policy logic.

## Small surface adjustments this suggests

- Have `helpers.py` distinguish `InferenceQueueFull` / `InferenceTimeout`
  / a future `BudgetExceededError` from generic
  `llm_unavailable` so callers (and the fallback-to-defaults path)
  can react differently. Today these all collapse into one string.
- `priority_for_agent` currently assigns nothing to `CRITICAL` or
  `HEALTH`. If Phase 5 introduces a "dry-run" or "harness self-check"
  mode, these lanes are available without adding new priority values.
- The broker already exposes a per-provider snapshot the Settings
  tile could render for Phase 1 without any new metrics plumbing.
