# Observer Pre-filter Alpha Repair

Date: 2026-07-24

## Outcome

Repair the negative selection contribution currently attributed to the
Market Observer pre-filter without converting an observational correlation
into an unguarded production threshold change.

The current production rule is:

```text
abs(1h RSI - 50) > 12 OR 1h ATR percent > 0.8
```

It is a global attention gate. It saves model calls; it is not a Risk or
Execution rule. The repair therefore targets candidate admission and
measurement only. Risk approval, Execution Gateway semantics, sizing, broker
selection, credentials, Live state, and hard limits are excluded.

## Evidence baseline

The 2026-07-24 five-day mature report identified Universe Alpha as the only
measured negative contribution:

| Layer | Selected net | Control net | Delta | Evidence |
| --- | ---: | ---: | ---: | --- |
| Universe, aggregate | -0.6804% | -0.4602% | -0.2203pp | prospective same-time control |
| Universe, 24h | -1.2987% | -0.1935% | -1.1052pp | prospective same-time control |

The strongest persistent Observer cells were observational and belong to the
predecessor filter version `v1-055855b95c26`, whose last evidence was recorded
on 2026-07-19:

| Cell | Passed shrunk net | Rejected shrunk net | Gap | Stability |
| --- | ---: | ---: | ---: | --- |
| short_bias / short / perp / 24h | -2.261% | +2.811% | +5.072pp | 10/10 suspect runs |
| mean_reversion / long / perp / 24h | -1.468% | +2.259% | +3.727pp | 10/10 suspect runs |
| volatility / long / perp / 24h | -1.717% | +1.392% | +3.109pp | 10/10 suspect runs |

The current production filter version is `v1-5adde7c878b6`. At the 2026-07-24
cutoff its 24h evidence was not mature:

| Cell | Passed net n | Rejected net n | Coverage | Verdict |
| --- | ---: | ---: | ---: | --- |
| short_bias / short / perp / 24h | 118 | 2 | 2.27% | insufficient_data |
| mean_reversion / long / perp / 24h | 378 | 9 | 12.16% | insufficient_data |
| volatility / long / perp / 24h | 87 | 0 | 0% | insufficient_data |

The aggregate Universe comparison is strong evidence that the upstream layer
is a bottleneck. The predecessor per-filter cells locate a hypothesis, but a
production filter-version change invalidates them as an experiment trigger.
They authorize implementation of diagnostics and replay tooling only. A
prospective experiment remains blocked until the current exact version meets
the precommitted replay gate.

## First-principles model

An attention filter creates value only when:

```text
avoided model cost + avoided downstream loss
    >
missed downstream edge + delayed opportunity cost
```

The present binary OR gate collapses two different hypotheses:

1. RSI distance from neutral predicts an actionable directional setup.
2. ATR percent predicts an actionable volatility setup.

A passed/rejected average cannot identify which rule adds or destroys value,
whether the effect is near the threshold, or whether it is conditional on
family, direction, market, venue, or regime. The first repair must expose those
dimensions before changing admission.

## Classification

### A-class: repair now without opening a new behavior epoch

- Split persisted Observer evidence into `rsi_only`, `atr_only`,
  `rsi_and_atr`, and `neither`.
- Report bounded RSI-distance and ATR-percent buckets.
- Preserve first-exposure attribution, exact instrument identity, cost-aware
  net outcomes, outcome coverage, and filter version.
- Distinguish genuinely unconsumed carriers from older carriers evaluated in
  the current clock window. Do not change the funnel unless a focused fixture
  proves a semantic misclassification.
- Add a bounded, read-only two-dimensional replay. It must not write config,
  create Opportunities, invoke the model, or change production decisions.

### B-class: only after the prospective gate succeeds

- Any threshold, admission, priority, or model-call behavior change.
- Close the preceding observation window.
- Create a new policy epoch.
- Start with exactly one cell and paper-only authority.
- Never pool outcomes across epochs.

## Acceptance matrix

| ID | Requirement | Proof |
| --- | --- | --- |
| OBS-01 | Controller reports the four mutually exclusive matched-rule cohorts | focused filter-evidence test |
| OBS-02 | RSI and ATR buckets are deterministic and boundary-tested | focused unit test |
| OBS-03 | Each cohort reports total, net-complete, coverage, raw net, and shrunk net | fixture with settled and incomplete outcomes |
| OBS-04 | First exposure remains the only assignment when a candidate later changes decision | retry-transition regression |
| OBS-05 | Reports remain read-only | database before/after assertion |
| OBS-06 | Persistent evidence from a superseded filter version is labeled stale and cannot trigger replay or experiment registration | version-change regression |
| FUN-01 | A cross-window evaluation is not called an unconsumed in-window carrier | focused trade-funnel fixture, only if current behavior fails |
| RPL-01 | Replay evaluates a fixed, bounded RSI/ATR grid from persisted ex-ante inputs | focused replay test |
| RPL-02 | Replay never searches an unbounded parameter space | exact grid and candidate-cap assertions |
| RPL-03 | Replay excludes directionless, incomplete-cost, late-price, and wrong-version rows | outcome-contract fixture |
| RPL-04 | Replay declares limitations and never mutates configuration | before/after config and DB assertions |
| EXP-01 | Prospective assignment occurs at first exposure and is stable | deterministic assignment test |
| EXP-02 | Production filter version changes invalidate the experiment | invalidation regression |
| EXP-03 | Shadow stage does not call the model or create a proposal | end-to-end negative assertion |
| CAN-01 | Canary is paper-only, one cell, default off, and bounded by an admission budget | behavior and config contract tests |
| CAN-02 | Risk and Execution remain mandatory and unchanged | existing decision-chain specs plus focused E2E |
| DOC-01 | Issues, outcome contract, and experiment protocol identify the active epoch and gates | executable document contract |

## Delivery sequence

### Batch 1 — diagnostic truth

1. Add matched-rule and distance-bucket summaries to the read-only controller.
2. Verify the current funnel interpretation with a cross-window fixture; change
   it only if the fixture contradicts the intended cohort semantics.
3. Update owner-facing documentation.

No decision output may change in this batch.

### Batch 2 — bounded offline replay

Compare the production control with a fixed grid:

```text
RSI deviation: 12, 10, 8, 6
ATR percent:   0.8, 0.7, 0.6, 0.5
```

The grid is an admission expansion only. It does not remove current passes.
Reports must include newly admitted candidates separately from the union cohort,
because a good rejected cohort can still be diluted by a poor existing pass
cohort.

Replay readiness requires, per exact cell:

- at least 200 net-complete newly admitted outcomes;
- at least 80% net-outcome coverage;
- at least +0.25pp shrunk net improvement over control;
- a bootstrap lower confidence bound above zero;
- no single symbol or UTC day contributing more than 30% of net improvement;
- no material 1h/4h degradation hidden by the 24h primary horizon;
- an explicit estimate of extra model calls per Observer run.

These thresholds are precommitted for this repair and must not be changed after
examining replay winners.

### Batch 3 — one-cell prospective shadow

Pilot only:

```text
observer_pre_filter / short_bias / short / perp
primary horizon: 24h
```

Assignment is persisted at first exposure. Control keeps the production rule.
Treatment records `would_admit` under the selected replay rule but does not call
the model or create an Opportunity. Run for at least seven days and wait for the
last 24h outcome to mature.

This batch must not start from the predecessor `v1-055855b95c26` result. It
requires a replay-ready alternative under the then-current exact production
filter version.

### Batch 4 — paper canary

Only after Batch 3 satisfies its precommitted gate:

- close the old observation window;
- create `observer_prefilter_short_bias_expansion/v1`;
- default off;
- paper-only and Live fail-closed;
- admit at most 10% of formerly rejected treatment candidates;
- cap additional model calls per Observer run;
- retain all existing Risk and Execution gates.

Stop the canary on missing causal/economic evidence, negative treatment
confidence bound, proposal profit factor below 0.85, two-times baseline Operator
P95 latency/backlog, or more than 30% contribution from one symbol.

### Batch 5 — decision

- Promote only the proven cell, gradually, if the full lifecycle improves.
- Otherwise close the experiment and retain the negative result.
- Do not change another threshold inside the same epoch.
- Evaluate mean-reversion and volatility only as later independent epochs.

## Verification

Run focused tests first:

```sh
uv run pytest tests/specs/spec_filter_evidence.py -q
uv run pytest tests/filter_eval/test_filter_controller_eval.py -q
uv run pytest tests/test_trade_funnel_audit.py -q
```

Then run the directly affected executable specifications. Do not run the full
suite on the macOS device. Frontend build is required only if a Settings control
or owner-facing UI is changed.
