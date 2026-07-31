# Config Schema and Operator Surfaces — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: locate the existing shapes for provider configuration and
operator-facing UI, so any new budget / rate-limit / harness config
lands in idiomatic slots with the mandatory Settings UI attached.

Sources: `aletheia/config/schema.py`,
`frontend/src/pages/Settings.tsx`,
`aletheia/server/routes/`,
`aletheia/cli/audit_cmd.py`,
AGENTS.md rule "A new config-schema field needs a Settings UI control".

## What `ModelsConfig` / `ProviderConfig` already carry

Every model routing and admission control field the tokenomics work
would touch already exists in the schema. `ProviderConfig` (per
provider) covers:

- **Identity / auth**: `type`, `base_url`, `protocol`, `model`,
  `enabled`, `command`, `mode`, `api_key_secret_ref`, `codex_model`,
  `workspace_url`, `auth_scheme`, `auth_type`, `oauth_token_ref`,
  `oauth_refresh_ref`, `oauth_expires_at`.
- **Model fallback** (used by `fallback.py`): `model_fallback_enabled`,
  `fallback_models`.
- **Broker admission** (used by `broker.py`):
  `inference_max_concurrency` (1–16),
  `inference_requests_per_minute` (0–10 000, 0 = unlimited),
  `inference_tokens_per_minute` (0–100 000 000, 0 = unlimited),
  `inference_queue_capacity` (1–1 000),
  `inference_reserved_high_priority_slots` (0–1 000),
  `inference_timeout_sec` (1.0–1 800.0).

`ModelsConfig` (top-level):

- `default_provider`, `deep_provider`, `fast_provider` — three-tier
  routing keys consumed by `router.get_provider_for_tier`.
- `providers: dict[str, ProviderConfig]` — the provider bag.

**No** `budgets`, `rate_limits`, `harness`, or `guardrails` block
currently exists at either level. New fields for those concepts would
be additive.

## Where new tokenomics fields would idiomatically slot in

- Provider-scoped tokenomics fields (e.g., per-provider daily USD cap
  as a soft ceiling used together with the ledger) fit alongside the
  `inference_*` group in `ProviderConfig`.
- Caller-scoped fields (per-`AgentRun` or per-`logical_agent` budgets,
  RPM/TPM ceilings by scope) do **not** belong on `ProviderConfig`.
  They want a new top-level block on `ModelsConfig`, or a new
  sibling `EconomicsConfig` on `AletheiaConfig` alongside
  `models: ModelsConfig`. The latter keeps `models` cleanly focused
  on routing while grouping tokenomics under one owner.
- Kill switches equivalent to `ALETHEIA_COST_TRACING` /
  `ALETHEIA_VIABILITY_AUDIT` should stay environment-variable-driven
  to remain consistent with PR #110's operator affordance.

## Server routes today

`aletheia/server/routes/` currently exposes: `analytics`, `bitget`,
`capital`, `command`, `data`, `dev_reload`, `gateway`, `health`,
`live`, `model`, `news`, `operator`, `persistence`, `prediction`,
`risk`, `runtime`, `settings`, `telegram`, `universe`, `update`, `ws`.

**No `economics` or `audit` route exists.** The metering data lives in
SQLite (`runtime_cost_events`, `viability_reports`,
`pricing_snapshots`, `tool_cost_profiles`), and the only interface is
the `aletheia audit costs / viability / pricing-set / tool-pricing-set`
CLI in `aletheia/cli/audit_cmd.py`. This is why Phase 1 needs a new
read-only API surface before any Settings tile can render usage.

**Adjacent live sources** that are already API-reachable and useful for
Phase 1's dashboard:

- `inference_broker.snapshot()` (`aletheia.model_provider.broker`) —
  per-provider queue, active, RPM/TPM headroom, wait times.
- `get_model_health_snapshot()` (`aletheia.model_provider.fallback`) —
  per-`(provider, model)` cooldown and consecutive-failure state.

Both would sit naturally under a new `routes/economics.py` alongside
ledger-derived summaries (`aletheia audit costs` in structured form).

## Frontend surfaces today

Settings is a single tabbed page (`frontend/src/pages/Settings.tsx`).
The tabs include an "Agent & Models" tab (`AgentModelsTab`), rendered
from the same page. The tab contains multiple `<Section>` blocks; the
one relevant here is:

```tsx
<Section title="AI Model Providers" description="LLM endpoints used by all agents. Verify & Save tests the connection before persisting. API keys are write-only.">
  <ModelProviderCard qc={qc} />
</Section>
```

Constants list the providers the UI knows how to render:
`openai_compatible, anthropic_native, openai_native, claude_cli,
codex_cli` (with `anthropic_compatible` inferred via `HTTP_PROVIDERS`).
`ModelProviderCard` is the write-path form; there is no read-only
"live inference metrics" or "spend" view alongside it.

Other pages (`TradesPositions.tsx`, `PnLZone.tsx`, `AccountSnapshot.tsx`,
`api/client.ts`) match the word "economics" only in the sense of
**trading** economics (PnL, fees, funding); none of them read the
LLM-cost ledger.

## What Phase 1 needs to build

- **Backend**: a new `routes/economics.py` that exposes read-only
  endpoints for:
  - Cost summary over a window (mirror of `aletheia audit costs`),
    per scope (system / operator / `run_id` / `logical_agent`).
  - Broker live snapshot (from `inference_broker.snapshot()`).
  - Model-health snapshot (from `get_model_health_snapshot()`).
  - Recent unknown-cost events (rows with `cost_status='unknown'`).
- **Frontend**: a new Section (either inside the existing "Agent &
  Models" tab or as a new "LLM Cost & Usage" tab) rendering:
  - "Spent today / last 7 days" tile with unknown-count callout.
  - Per-provider queue / RPM / TPM headroom bar (already computable
    from the broker snapshot).
  - Cooldown badges for models currently paused by fallback.
- **Schema**: unchanged for Phase 1. New fields land in Phase 2
  alongside the budget policy declarations.

## Phase 2 and later: where new fields land

Sketch, not commitment:

- `AletheiaConfig` gains `economics: EconomicsConfig`.
- `EconomicsConfig` gains `budgets: list[BudgetPolicy]` and
  `rate_limits: list[RateLimitPolicy]`.
- Each policy carries `scope` (one of `system`, `agent_run`,
  `logical_agent`, `provider`, `provider_tier`), `subject` (the
  key), `usd_cap` or `requests_per_window`, `window`, `enabled`.
- Per AGENTS.md, every new field gets a Settings control. That
  argues for keeping the policy list small and structured so the UI
  can render it with one table plus an "Add policy" dialog rather
  than many per-provider knobs.

## Small things worth flagging

- The frontend enumerates providers via `ALL_PROVIDERS` (line 25 of
  `Settings.tsx`). Any new provider added to the backend registry
  must also be added here; otherwise the UI cannot edit it. This is
  orthogonal to tokenomics but worth remembering when the harness
  proposal reviews routing surface coverage.
- Kill-switch env vars (`ALETHEIA_COST_TRACING`,
  `ALETHEIA_VIABILITY_AUDIT`) are not currently exposed in the UI.
  Keeping them env-only is consistent with them being an emergency
  operator affordance, not a routine toggle.
