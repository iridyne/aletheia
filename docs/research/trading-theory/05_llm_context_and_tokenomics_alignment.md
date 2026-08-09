# LLM Context and Tokenomics Alignment

Date: 2026-08-09
Role: Research evidence

## Decision

Calculated technical facts should enter an LLM through Aletheia's existing
unified inference harness as a versioned, typed context artifact. They should
not be appended ad hoc to individual prompts and should not create a second LLM
gateway, cost ledger, or experiment framework.

This report does not enable a context adapter or change a decision prompt.
Rendering new facts into a decision call is a behavior change under the
[Experiment Protocol](../../product/experiments/EXPERIMENT_PROTOCOL.md) and
requires a new identity and prospective window.

## Existing control plane

The current application path already supplies the needed control boundary:

1. `llm_complete`, `llm_structured`, and `llm_stream` in
   `aletheia/agents/helpers.py` prepare context and register one logical call.
2. `CALLER_SPECS` in `aletheia/model_provider/inference_contract.py` is the
   executable caller inventory. Each caller declares its prompt template,
   prompt version, output mode, and tool interface.
3. `build_inference_call()` records static and rendered prompt hashes, adapter
   identity and manifest, schema identity, provider request, decoding limits,
   fallback policy, and bounded evaluation metadata.
4. The logical inference record is content-free. Physical provider attempts,
   retries, fallback, token usage, latency, and cost status remain separately
   observable under the same logical call identity.
5. Structured attempts record parse/schema outcomes and sanitized failure
   categories. Raw prompts and model responses are not placed in the economics
   ledger.

This separation is important: context selection belongs above the provider;
provider attempt telemetry remains an honest measurement surface; Risk and
Execution remain independent downstream authorities.

## Relevant Tokenomics history

The implementation direction is not new. The current design follows a sequence
of already merged work:

| Work | Relevant result |
| --- | --- |
| [Issue #475](https://github.com/iridite/aletheia/issues/475) | Umbrella for identity, policy, routing, metering, and governance of LLM calls. |
| [PR #110](https://github.com/iridite/aletheia/pull/110) | Established physical runtime-cost evidence. |
| [PR #479](https://github.com/iridite/aletheia/pull/479) | Chose staged, measurement-first controls and kept broad prompt changes research-gated. |
| [Issue #547](https://github.com/iridite/aletheia/issues/547), [PR #559](https://github.com/iridite/aletheia/pull/559) | Tested a harness header. Format/schema compliance was complete, but the header added about 73-75 input tokens and blinded quality review remained incomplete; default stayed off. |
| [PR #579](https://github.com/iridite/aletheia/pull/579) | Added gated budget enforcement and prompt observations without treating text as authority. |
| [PR #597](https://github.com/iridite/aletheia/pull/597), [PR #599](https://github.com/iridite/aletheia/pull/599) | Added token-first telemetry and caller-role attribution. |
| [PR #694](https://github.com/iridite/aletheia/pull/694) | Unified logical inference control across application calls. |
| [PR #718](https://github.com/iridite/aletheia/pull/718), [PR #792](https://github.com/iridite/aletheia/pull/792) | Made structured failure and repair-attempt outcomes observable without storing raw content. |
| [Issue #523](https://github.com/iridite/aletheia/issues/523) | Preserves the rule that unavailable providers produce an honest blocker, not a fabricated completed decision. |

The lesson is direct: more context is not automatically better. Syntax success
does not establish decision quality, and a small recurring token increase can
be material at production call volume.

## Current technical facts

The shared `TechnicalSnapshot` can calculate these closed-bar facts:

- last close, SMA 20/50, and EMA 20/50;
- Wilder RSI 14 and ATR 14, including ATR as a percentage of close;
- Bollinger lower/mid/upper, percent-B, and width;
- 10-bar momentum;
- 20-bar volume z-score and volume ratio;
- 20-bar rolling VWAP;
- 20-bar Donchian high/low;
- normalized OBV.

Universe and Operator projections already expose a smaller decision-facing set,
including multi-timeframe RSI/ATR, returns, Bollinger width/squeeze, volume
ratio, relative strength, BTC beta, and selected derivatives/microstructure
facts. Several values computed in the shared snapshot, such as EMA, rolling
VWAP, Donchian, and normalized OBV, are not currently rendered as distinct AI
facts. Calculation availability must not be confused with model visibility.

## Proposed context contract

A future treatment should reference one immutable snapshot and render a bounded
selection such as:

```json
{
  "artifact_id": "technical-context:<instrument>:<as_of>:<fingerprint>",
  "adapter_id": "technical-context-v1",
  "as_of": "last closed-bar timestamp",
  "instrument": {
    "exchange": "bitget",
    "symbol": "BTC/USDT",
    "market_type": "swap"
  },
  "timeframes": {
    "1h": {
      "trend": {"ema20_vs_ema50": 0.0},
      "momentum": {"rsi14": 0.0, "return_10": 0.0},
      "volatility": {"atr_pct": 0.0, "bb_width_pct": 0.0},
      "location": {"bb_pct_b": 0.0, "distance_to_donchian_high_atr": 0.0},
      "participation": {"volume_zscore_20": 0.0}
    }
  },
  "quality": {"state": "complete", "reasons": []},
  "algorithm_version": "technical-analysis-v1",
  "input_fingerprint": "sha256"
}
```

The exact treatment fields are an experimental variable, not a final contract.
Each artifact needs a stable ID, source, observed time, freshness state, trust
class, rendered character count, algorithm version, and input fingerprint.

## Token and compute policy

- Compute once and reuse the shared snapshot; never ask the LLM to calculate an
  indicator from raw candles.
- Keep formula definitions in code and documentation, not in every prompt.
- Render normalized state, material deltas, and nearest levels; do not send long
  candle arrays or every intermediate value.
- Use one fact for aliases that share the same evidence. SMC, ICT, and Price
  Action wording must not triple-count one breakout.
- Keep the static system prompt unchanged and place volatile facts in one
  bounded dynamic block so prompt-cache opportunities remain possible.
- Omit unavailable facts or mark them unavailable. Never let the model fill a
  missing indicator.
- Record the adapter ID and artifact IDs in the existing logical call. Do not
  create a parallel telemetry table for the offline experiment.

## Minimal implementation gap

The existing harness is sufficient for an offline paired evaluation. The
minimum future code would be:

1. a registered `technical-context-v1` typed renderer;
2. frozen representative fixtures and an owner-approved review rubric;
3. paired caller identities or bounded evaluation metadata for control and
   treatment;
4. focused tests proving `none` leaves the prompt byte-for-byte unchanged and
   degraded/missing facts fail closed.

Production assignment, cohort identity, and outcome linkage are needed only if
the treatment advances to a Paper decision experiment. No new provider router,
agent framework, or general prompt-template service is justified.

## Recommendation

Start with an offline, paired `none` versus `technical-context-v1` test on the
existing Market Observer structured schema. Do not expose all computed fields
at once. Test small, semantically distinct bundles and keep a bundle only if it
improves blinded task quality after accounting for tokens, retries, latency,
schema validity, and context harm.
