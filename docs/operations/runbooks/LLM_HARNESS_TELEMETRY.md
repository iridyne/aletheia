# LLM Harness Telemetry Runbook

## Purpose

Use this runbook to inspect Aletheia's framework-wide LLM usage. The early
operational metric is provider-reported token usage, not USD cost. The harness
records input/output tokens and, when supplied by a provider, cache-read,
cache-creation, total, and reasoning token fields.

## Runtime source of truth

**CASI is the runtime source of truth.** Run every operational command below on
the CASI host through SSH. The local macOS `~/.aletheia/` directory is an
archive and must not be used to infer current gateway state, current model
usage, pricing, budgets, or observation-window evidence.

```sh
ssh <casi-host>
cd <aletheia-checkout>
uv run aletheia gateway status
```

Do not restart the shared gateway merely to inspect telemetry.

## Token usage

```sh
# Human-readable 7-day summary
uv run aletheia audit tokens --days 7

# Machine-readable 14-day summary
uv run aletheia audit tokens --days 14 --json

# One logical AgentRun
uv run aletheia audit tokens --days 7 --run-id <agent-run-id> --json
```

The output reports:

- LLM physical attempt count;
- attempts with complete input/output usage and attempts with unavailable usage;
- input/output/total/reasoning/cache counters;
- aggregation by logical agent and harness role;
- token-integrity observation counts.

A missing field is unavailable, not zero. Cache counters are provider-specific
breakdowns and are not inferred from prompt text or timing.

The same read-only data is available at `GET /api/economics/tokens` and in the
Dashboard Settings → Economics panel.

## Token integrity observations

`integrity-v1` is always non-blocking. It records stable rule identifiers when
a provider usage payload is absent or internally contradictory. It never stores
prompt content, changes a retry/fallback, denies an LLM call, changes a tool
permission, or changes a trade.

Investigate repeated observations as provider adapter or telemetry-normalization
issues. Add a deterministic fixture before accepting a new usage-payload shape.
See [the policy record](../../research/llm_harness/token_observation_policy.md).

## Optional economics and budgets

USD estimates are optional enrichment, not a precondition for token telemetry.
They require an explicit immutable price snapshot.

```sh
uv run aletheia audit pricing-list --limit 50
uv run aletheia audit budget-list --json
uv run aletheia audit costs --days 7 --json
```

Budget policies are initially shadow observations. The default mode is
`warn_only`; no budget policy may silently block a trading-critical caller.
Enforcement requires its separately documented representative observation window
and owner decision. Do not create an arbitrary price, budget, or enforcement
policy merely to make a dashboard number non-null.

## Context adapters

The current default is `none`: caller prompts are unchanged. `minimal-v1` is a
disabled-by-default experiment that supplies only typed, truthful runtime facts.
It does not grant authority. `decision-v1` and `operator-v1` are not enabled
until their context facts and task-quality evidence are accepted.

## Developer checklist

When adding a provider or changing usage extraction:

1. preserve the original provider usage payload at the physical-attempt trace;
2. normalize only known input/output/cache/reasoning fields;
3. add a deterministic normalization fixture;
4. verify `audit tokens` and `/api/economics/tokens` expose the result;
5. do not add raw prompt storage, inferred cache usage, or control behavior.
