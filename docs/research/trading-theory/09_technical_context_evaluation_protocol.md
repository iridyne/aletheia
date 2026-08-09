# Technical-Context Evaluation Protocol

Date: 2026-08-09
Role: Research evidence

## Purpose

This report makes the path from calculated indicators to LLM context concrete.
It reuses Aletheia's inference and experiment contracts and distinguishes three
questions that must not be collapsed:

1. Does the context help the model interpret a frozen case?
2. Can the production pipeline preserve identity, assignment, and outcomes?
3. Does using the context improve prospective Paper economics after costs?

No stage changes Risk, Execution, Live defaults, or credential authority.

## Stage 1: offline paired context evaluation

### Question

Does a bounded technical context improve structured market reads enough to
justify its token and reliability cost?

### Reused mechanism

Reuse the pattern in `aletheia/economics/context_effectiveness.py`:

- one frozen fixture is rendered once for each arm;
- arm order and blind labels are randomized;
- the same system prompt, output schema, provider, model, temperature, and
  maximum tokens are used;
- model fallback is disabled;
- schema-invalid, missing-arm, fallback, or provider/model-drift pairs are
  invalid;
- raw review packets remain outside the repository with restrictive file
  permissions; durable records contain identities and aggregate scores.

Control is the current caller prompt and context policy. Treatment is identical
except for one `technical-context-v1` typed artifact. Do not change tool policy,
schema wording, or the market snapshot between arms.

### Fixture set

Use a small, frozen, auditable set covering:

- trend, range, breakout, failed breakout, volatility expansion, and squeeze;
- long, short, and legitimately uncertain cases;
- complete, insufficient-warmup, stale, and gap-degraded technical snapshots;
- multiple symbols and timeframes without using future outcomes in the input;
- deliberately correlated indicators to test whether the model double-counts
  them.

Start with historical point-in-time fixtures or carefully labeled synthetic
edge cases. Synthetic cases prove behavior, not market profitability.

### Treatment ladder

Evaluate additions, not one maximal prompt:

| Variant | Added facts | Reason |
| --- | --- | --- |
| T1 | RSI, ATR%, selected returns, data quality | Small baseline already close to current model-visible facts. |
| T2 | T1 plus trend/location summaries from EMA, Bollinger, Donchian | Tests whether distinct categories improve interpretation. |
| T3 | T2 plus causal structure state and nearest support/resistance zones | Tests market structure without raw candles. |
| T4 | T3 plus observed profile/flow only when source coverage is complete | Tests genuinely new microstructure evidence. |

Stop when added context is neutral, harmful, or redundant. A later variant is
not presumed better.

### Measurements

Automated integrity and cost:

- input, output, cache, and reasoning tokens when supplied;
- latency, retry count, fallback, and provider/model identity;
- parse and schema validity;
- context characters and artifact coverage;
- fact-use and hallucination checks that can be deterministic.

Blinded owner-approved quality rubric:

- factual grounding in supplied data;
- correct handling of stale, missing, and degraded facts;
- separation of observation, inference, uncertainty, and action proposal;
- no duplicate voting from correlated indicators or theory aliases;
- usefulness of direction, invalidation, and uncertainty reasoning;
- absence of invented levels, institutional intent, Risk approval, or execution
  authority.

Report paired effect sizes and failure examples. An LLM judge may triage but
cannot replace the required human quality gate.

### Exit

The adapter remains disabled if quality is neutral or negative, evidence is
insufficient, safety semantics regress, or token/retry impact exceeds the
predeclared bound. Passing this stage authorizes only a proposal for prospective
measurement, not a production decision change.

## Stage 2: Shadow integrity observation

### Purpose

Compute and persist the candidate artifact prospectively while leaving the
actual decision model on the static baseline. This proves:

- closed-bar and event-time alignment;
- artifact, algorithm, and input-fingerprint stability;
- assignment, continuity, and outcome plumbing;
- coverage, freshness, and fail-closed behavior under real runtime conditions.

Under the current Experiment Protocol, Shadow treatment and control both run
the static baseline. Therefore Shadow has no actual LLM intervention and cannot
show positive or negative economic effect. Rendering the treatment into the
production decision prompt would no longer be a pure Shadow observation.

The current integrity-readiness reference is 10 complete assignment units per
arm inside at least one continuous eligible UTC market day. This is a plumbing
gate, not profitability proof.

## Stage 3: owner-admitted Paper decision experiment

### Entry

Only an owner-approved behavior change can move from Shadow to `paper_10`.
Before the first prospective exposure, freeze:

- exact deployed commit, Runtime/session, data root, and Paper mode;
- hypothesis, path, experiment ID, stage, and owner;
- prompt template/version, technical adapter/version, schema hash, tool policy,
  provider/model, decoding settings, and fallback policy;
- technical algorithm/input identity and source coverage;
- assignment unit, salt, control/treatment allocation, and exclusions;
- outcome contract, horizon, costs, sample/time gates, confidence rule, and
  harm thresholds.

Any change to these decision inputs or semantics closes the cohort, versions
the identity, and restarts from zero. Pre/post samples are never pooled.

### Assignment

Use the complete candidate cluster as the unit so related decisions sharing the
same 1h/4h market path do not cross-contaminate arms. Control retains the
baseline context. Treatment adds only the frozen adapter. Analyze intention to
treat first; record actual context delivery and model use as separate integrity
facts.

### Outcomes

Predeclare economics net of fees, funding, and slippage. Include at least:

- net return or PnL over the frozen horizon;
- adverse and favorable excursion;
- turnover, holding time, abstention, and proposal-direction changes;
- tail loss and existing safety/harm violations;
- missing, stale, continuity, fallback, and identity invalidation rates.

The current protocol retains 50 complete units per arm and seven consecutive
market days for each Paper stage's economic promotion evidence. Adjacent
decisions are not IID observations. Underpowered evidence is indeterminate and
cannot authorize promotion.

Progression remains one stage at a time: `paper_10`, `paper_25`, `paper_50`,
then `paper_active`, with a prospective control holdout and protected owner
admission. A controller may recommend but cannot promote autonomously.

## Invalidating conditions

At minimum, invalidate or refreeze for:

- a forming/future bar or retrospective pivot entering a decision input;
- mismatched venue, symbol, market type, timeframe, session, or algorithm;
- provider/model drift or fallback in a frozen paired comparison;
- canonical/fallback outcome disagreement;
- continuity gaps, unrepairable event loss, or unrecorded missingness;
- cross-arm learning or outcome contamination;
- prompt, schema, adapter, assignment, cost, horizon, or Risk changes during the
  frozen window;
- raw prompt/response retention outside the approved evaluation boundary.

## Recommended first test

Run Stage 1 only, comparing current baseline with T1 and then the smallest
non-redundant T2 bundle. Do not wait for a production Observation window to
discover basic prompt quality or token regressions. Do not start Stage 2 or 3
until the adapter definition, rubric, provider availability, and owner admission
are all explicit.

This sequence uses the existing harness at every step and spends expensive
prospective market evidence only after cheaper paired tests reject obviously
unhelpful context.
