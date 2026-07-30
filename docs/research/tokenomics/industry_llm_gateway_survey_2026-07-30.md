# Industry LLM Gateway Survey — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: document how two mature open-source LLM gateways structure their
control plane, so Aletheia's harness proposal is informed by real prior art
rather than reinvented from scratch.

Non-goal: this file does **not** propose an Aletheia design or commit the
project to any of these patterns. It is inputs to the eventual proposal.

Sources consulted via `WebFetch` on 2026-07-30:

- LiteLLM proxy — virtual keys, budgets, rate limits
  (`https://docs.litellm.ai/docs/proxy/virtual_keys`).
- Portkey AI Gateway — unified API, configs, guardrails, cache
  (`https://portkey.ai/docs/product/ai-gateway`).

`WebSearch` was unavailable on the session model at the time of collection,
so this snapshot is intentionally narrow. Broader references (Langfuse,
Helicone, Braintrust, OpenRouter) should be added in a future dated survey
if a wider decision surface is needed.

## LiteLLM: virtual-key-centric governance

- **Virtual keys** are the identity primitive. A proxy admin mints them
  from a `master_key` via `/key/generate`, backed by a Postgres database.
- **Three-tier isolation.** A key can be standalone, attached to a
  `user_id`, or attached to a `team_id`; spend rolls up into the
  matching tables (`LiteLLM_VerificationTokenTable`,
  `LiteLLM_UserTable`, `LiteLLM_TeamTable`).
- **Automatic spend tracking.** Costs accumulate on the key on every
  `/completions`, `/chat/completions`, and `/embeddings` call via
  `completion_cost()` against a per-model price table.
- **Budgets** are declared at key generation via `max_budget` and
  `budget_duration`. `upperbound_key_generate_params` gives admins a
  global ceiling that clamps caller-supplied values.
- **Default budgets** in `default_key_generate_params` apply when the
  caller omits them.
- **Rate-limit primitives** on each key: `tpm_limit` (tokens/min),
  `rpm_limit` (requests/min), `max_parallel_requests`.
- **Model access control** via a `models` list on each key; **model
  aliases** silently remap a requested model to an internal group
  (e.g., `gpt-3.5-turbo` → `my-free-tier`).
- **Temporary budget increases** through `/key/update`
  (`temp_budget_increase`, `temp_budget_expiry`).
- **Key lifecycle.** `/key/block`, `/key/unblock`, `/key/{key}/regenerate`
  (with `grace_period` for cutover); scheduled auto-rotation under
  `LITELLM_KEY_ROTATION_ENABLED` runs a background worker.
- **Key-generation restrictions.** `key_generation_settings.team_key_generation`
  and `personal_key_generation` gate who can mint keys and what
  parameters they must supply. `custom_generate_key_fn` allows arbitrary
  pre-generation validation returning `{"decision": bool, "message": str}`.
- **Identity leakage control.** `overwrite_user_with_key_hash: true`
  forces the outgoing `user` field to a stable hash of the key.
- **Spend inspection.** `/key/info`, `/user/info`, `/team/info` return
  `spend` fields for humans and dashboards.
- **Not documented on the surveyed page:** whether callers receive
  remaining-budget headers on completion responses, and the exact
  block-vs-reject semantics when a limit is hit. Both would need to be
  confirmed against the code or another page.

## Portkey: config-driven gateway

- **Unified LLM API** across many providers plus custom hosts for
  privately hosted models. Multimodal (vision, audio, image generation)
  and non-completion routes (rerank, video, transcription) proxied
  through the same base URL.
- **Budget & rate-limit controls.** Budgets can be denominated in cost
  or tokens; rate limits are configurable per minute, hour, or day, on
  requests or tokens.
- **Configs** are the declarative policy object: they tie routing,
  fallbacks, retries, circuit breakers, load-balancing across keys,
  canary rollouts, timeouts, and guardrails into one composable unit.
- **Guardrails and conditional routing.** Traffic can be redirected to
  different targets based on custom checks, and the gateway advertises
  integrated guardrails as first-class.
- **Resilience.** Cross-provider and cross-model fallbacks; retry
  policies; per-strategy circuit breaker; load balancing across API
  keys; request timeouts; canary testing.
- **Caching.** Both simple (exact-hit) and semantic cache modes to
  reduce cost and latency.
- **Observability.** Unified logging and configs apply consistently
  across unified and passthrough routes. Optional gRPC transport for
  lower latency.
- **Self-hostable** as `npx @portkey-ai/gateway`.

## Cross-tool pattern

Both gateways converge on the same five conceptual layers:

1. **Identity** — who is calling (virtual key, user, team).
2. **Policy** — what they may do (which models, how much budget, at
   what rate, with what guardrails).
3. **Routing** — which physical target serves the call (tier, fallback,
   canary, load balance).
4. **Metering** — what actually happened (spend, tokens, latency,
   status).
5. **Observability** — how humans see it (dashboards, logs, endpoints).

The layers are independent enough that a project can adopt them
incrementally, and mature enough that both projects treat metering as a
prerequisite for policy enforcement rather than the other way around.

## What this implies for Aletheia (framing only)

Aletheia already has an internal identity primitive: `AgentRun` and, for
event-driven daemons that intentionally have no generic AgentRun, an
explicit `logical_agent` such as `pi_trading_decision` (PR #110). It also
already has a full metering layer (`aletheia/economics/`) with immutable
pricing snapshots, an append-only `runtime_cost_events` ledger, and
deterministic viability reports.

The identity is internal, not exposed to external callers, so the parts
of LiteLLM concerned with virtual-key lifecycle (rotation, revocation,
external-caller minting, key-generation permissions) do not translate.
What translates is the **policy layer** — declared budgets scoped to
`AgentRun` / `logical_agent` / provider tier, rate-limit primitives on
the same scopes — and the **guardrail** slot that both tools reserve.

The tokenomics branch should treat this survey as input, propose a
concrete Aletheia design in `docs/design/proposals/`, and preserve
PR #110's explicit "measurement-only" boundary: metering never changes
trading behavior; policy only ever denies a call with an explicit,
loggable error, never silently mutates prompts or routing.
