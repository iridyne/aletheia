# Profit-loop, Profit-gradient and trade-rationale contract text (moved)

> **History (moved 2026-10-10).** Passages moved verbatim out of
> `docs/design/contracts/profit_loop_observability_contract.md`,
> `docs/design/contracts/profit_gradient_evidence_contract.md` and
> `docs/design/contracts/trade_decision_rationale_contract.md` when those files were
> rewritten into the numbered Contract form. They are runtime readings, progress
> notes, a historical registration, the long-form Profit Gradient v3 / reconciliation
> description (restated as numbered requirements in the
> [Profit Gradient Contract](../contracts/profit_gradient_evidence_contract.md)), and the
> verification line for the retired five-item entry rationale. Nothing here is
> current behavior or an acceptance criterion.

## From profit_loop_observability_contract.md — Purpose and current scope

Profit Gradient is an analysis capability, not an optimization authority. It
provides data, attribution views, and uncertainty; it does not automatically
select, create, start, authorize, or activate an experiment or change trading
policy. The owner + ChatGPT decide the next optimization direction manually.

This capability has no trading authority. It does not change Ranker, System
Operator, Risk, Execution, position protection, Learning, Live, or an existing
experiment cohort.

## From profit_loop_observability_contract.md — Profit-gradient v3 comparison method and reconciliation

### Profit-gradient v3 comparison method

The CLI accepts an outer compiler artifact with contract
`profit_gradient_evidence/v2`; after source-bound verification, the selector
emits a `profit_gradient/v3` report with one `evidence_context.comparison_method`:

```json
{
  "contract_version": "profit_gradient_evidence/v2",
  "evidence_context": {
    "comparison_method":
    "prospective_randomized|paired_same_unit|deterministic_decomposition|directional_observational"
  }
}
```

The CLI rejects a missing or older compiler wrapper before evaluating samples;
`profit_gradient/v3` identifies the selector result, not the input file. Historical
v1 artifacts remain archival-only and are not pooled with v2 evidence.

Version 3 also records the randomized estimand and cohort identity. Version 2 and
older results are historical and are not pooled with version 3.

A missing or unknown method is ineligible; v1 and v2 evidence are historical and are not
implicitly assigned a method, relabeled, pooled, or compared with version 3 evidence.
One artifact does not mix methods implicitly. A caller evaluating a different
method creates a separately identified evidence artifact.

All methods retain the existing coverage, epoch, unit-integrity, clustered
interval, practical-effect, temporal-stability, concentration, and Risk-layer
hard gates. The method-specific gates are:

| comparison method | additional method gate | unique winner |
| --- | --- | --- |
| `prospective_randomized` | at least 30 independent clusters, exact `control`/`treatment` arms with each cluster assigned to one arm, at least 20 clusters in the smaller arm, singular assignment/horizon/outcome/metric/statistical-unit/stage identities, and a positive quote contrast aligned with the basis-point direction | allowed |
| `paired_same_unit` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; no arm threshold or arm labels are required | allowed for selecting the next forward question, never automatic behavior change |
| `deterministic_decomposition` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; facts remain descriptive | prohibited; measured fact only |
| `directional_observational` | at least 30 independent resampling clusters and 30 unique units, with singular horizon/outcome/metric/statistical-unit/stage identities; association remains directional | prohibited; rank the next question only |

For `prospective_randomized`, the estimand is the equal-weight per-cluster
`treatment - control` outcome contrast; arm labels affect the estimate and the
cluster bootstrap. Quote harm converts the exposure-weighted treatment and control
means to the treatment-exposure cohort total before denominator normalization,
weighting each arm cluster's quote contribution by its own exposure count; both
the contrast and this total must be positive. Concentration uses
translation-invariant, centered per-cluster arm-effect contributions, not pooled
row sums. A pooled mean is not a randomized effect. For
`paired_same_unit`, the estimand is the unit-weighted mean across normalized
rows with a cluster bootstrap for dependence; each row contributes one complete, cost-aware
delta for one unique statistical unit (for the current Exit counterfactual,
one Position). Duplicate `unit_id` rows are excluded. The paired result is
temporal-stability-ineligible when its chronological midpoint is a timestamp tie
and is never made to satisfy the randomized arm gate by inventing `treatment` or
`control` labels. The report exposes the applied threshold profile, arm counts,
unassigned-arm counts for randomized evidence, and
`smaller_arm_count: null` when arms do not apply.

The current delayed-entry, breakeven, and hypothesis-invalidation registry is
an exploratory paired counterfactual and remains `promotion_eligible=false`.
If its deltas are adapted into gradient evidence, the adapter must declare
`paired_same_unit` and preserve the frozen Position, cost, epoch, cluster,
time, and concentration identities.

### Ownership and reconciliation (`profit_gradient/reconciliation/v1`)

Every admitted sample carries a machine-checkable component, ownership stage,
stage window, positive quote notional, harm/attribution identities, evidence
references, and a reconciliation declaration. `exclusive` declarations contain
one component and reconcile its quote delta exactly to the declared total.
`residual` declarations may contain several components, including components in
different layers, but all components must share one unit scope and sum exactly
to the declared total and its basis-point equivalent within
`1e-8` quote units. A shared harm or attribution event across layers is rejected
unless this residual contract proves that decomposition; a shared cost event is
always rejected. Missing or malformed ownership, alternative-cost, execution
isolation, or reconciliation evidence is unavailable/ineligible and never
converted to zero.

Execution uses gross fill/routing deviation against a compatible execution
alternative; fees and funding are explicitly excluded. Costs are admitted only
when a persisted, realizable alternative identifies an avoidable extra cost and
its cost event/model identity. A production gross-minus-net ledger by itself is
not an actionable Costs comparison, so it remains unavailable rather than being
counted beside Execution.

## From profit_loop_observability_contract.md — Invariants (historical registration)

- The hard-coded registration opened on 2026-08-13 cannot accept the producer
  identity of the build that actually ran it. It remains immutable historical
  evidence with zero valid samples and is neither deleted nor reinterpreted.

## From profit_loop_observability_contract.md — Failure and recovery semantics (runtime reading)

No protected OHLCV retention was added. The Fedora Paper evidence checked during
implementation contained 327 closed Positions, a maximum hold of 1,705.28
minutes, and only 1,414 retained ADA perpetual one-minute rows—well below the
50,000-row pruning cap. The missing older ADA window therefore does not prove
rolling-prune loss, overwrite, or an on-demand backfill performance boundary.
A manifest remains prohibited until one of those observable triggers occurs.

## From profit_loop_observability_contract.md — Known gaps

The first post-repair deployment evidence cohort must accumulate before any
empirical winner or retention trigger can be claimed. Its cutoff is established
only after deploying the exact merged commit and recording the new durable epoch;
the historical 2026-09-12 cutoff is not reused. GitHub issue #964 is the parent
scope and #974 records the producer-registration repair. This remains exploratory
with no promotion authority and does not modify or reset UFC.

## From profit_gradient_evidence_contract.md — status line and authority boundary

Status: implemented, read-only. Contract: `profit_gradient_evidence/v2`.

Profit Gradient provides data, attribution views, and profitability analysis only.
It does not select, create, start, authorize, or activate an optimization
experiment; it does not mutate trading, Risk, Execution, Holding, Exit, or
Learning policy. The owner + ChatGPT decide the next optimization direction
manually after inspecting the result.

## From trade_decision_rationale_contract.md — §5 verification of the retired five-item entry rationale

- `tests/specs/spec_pipeline_risk_gate.py` (`PL-RG-12`)、`tests/specs/spec_pi_decision_chain.py` (`PI-S05`) 与 `tests/specs/spec_pipeline_entry_enrichment.py`（`PL-ENRICH-02`）：五项开仓事实（entry/stop/take-profit/notional/leverage 的理由与来源）在入场路径的缺项拦截、native limit 四要素门槛，以及持久化 rationale 回链。五项 JSON 的历史定义见 [退役入场生命周期](../history/decision_lifecycle_retired_entry.md)。
