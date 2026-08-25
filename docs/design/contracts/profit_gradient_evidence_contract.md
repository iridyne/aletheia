# Profit-gradient evidence compiler contract

Status: implemented, read-only. Contract: `profit_gradient_evidence/v2`.

## Authority boundary

Profit Gradient provides data, attribution views, and profitability analysis only.
It does not select, create, start, authorize, or activate an optimization
experiment; it does not mutate trading, Risk, Execution, Holding, Exit, or
Learning policy. The owner + ChatGPT decide the next optimization direction
manually after inspecting the result.

## Producer

```text
uv run aletheia inspect profit-gradient-evidence \
  --epoch <EPOCH_ID> \
  --cutoff <UTC_TIMESTAMP> \
  --cost-model-id <COST_MODEL_ID> \
  --output profit-gradient-evidence.json
```

`build_profit_gradient_evidence()` accepts only those three identity selectors.
It reads the registered `evidence_anchor:profit_loop_observability` epoch,
fixed-horizon candidate facts, closed Position replays, and persisted
cost-model identities. It performs no writes, network calls, LLM calls, or
trading actions. One SQLite read snapshot supplies totals, layer rows,
metadata, and source hashes.

The artifact contains:

```json
{
  "contract_version": "profit_gradient_evidence/v2",
  "registration": {},
  "evidence_context": {},
  "layer_samples": {},
  "exclusion_waterfall": {},
  "source_status": {},
  "source_hashes": {},
  "reconciliation": {},
  "exploratory_evidence": {},
  "content_hash": "..."
}
```

`content_hash` covers the complete source-bound artifact. Before
`profit-gradient` adjudicates it, the CLI rebuilds the exact inputs and requires
the rebuilt hash to match; static metadata or hand-authored samples cannot
authorize analysis.

## Cutoff and producer identity

All candidate, Position, economics, and OHLCV evidence is bounded by the exact
UTC cutoff. Missing or malformed availability facts remain unavailable.

- Candidate outcomes use one fixed `24h` net-return contract and the explicit
  ex-ante `analysis_notional_quote` basis. Proposal/Position sizing is never a
  fallback for filtered or skipped candidates; legacy outcomes without this
  basis remain unavailable.
- Candidate outcomes must carry recorded producer and settlement Runtime
  Session, commit, and behavior-fingerprint identities matching the registered
  behavior. Missing or cross-runtime identity remains unavailable.
- Position economics are admitted only when the durable economics materialization
  audit event and current fingerprint prove that the values were available by the
  cutoff. A later funding/economics settlement or correction cannot rewrite an
  older replay.
- OHLCV rows used by an explicit historical replay require
  `first_persisted_at` and `last_mutated_at` at or before the cutoff. A later
  backfill or correction is unavailable because prior row versions are not
  reconstructed.

## Layer samples

The selector consumes only compiler-produced samples. It requires exact units,
identity-complete assignment pairs, valid ownership and stage windows, positive
notional, finite cost-aware deltas, and executable reconciliation. Each paired
contrast preserves both treatment and control source identities, evidence refs,
baselines, and component metadata. Execution component loss must reconcile to
the paired delta; avoidable Costs require a realizable baseline and cost-model
identity.

The seven layers remain:

- Selection
- Decision
- Risk
- Execution
- Holding
- Exit
- Costs

No confirmatory layer-path mapping is currently registered. Empty mappings are
valid; the compiler does not invent Holding/Exit experiments or populate
`_LAYER_PATH_IDS` speculatively. The fixed profit-loop counterfactual registry
is emitted only under `exploratory_evidence` with `promotion_eligible: false` and
is excluded from `layer_samples`.

The evidence context declares the selector contract `profit_gradient/v3`,
comparison method, singular horizons/outcome/metric/statistical-unit/stage
identities, coverage, runtime/data identities, and reconciliation contract.
Unavailable coverage is not converted to zero.

## Consumer

`uv run aletheia inspect profit-gradient --evidence <FILE>` accepts the current
outer compiler artifact `profit_gradient_evidence/v2`. It verifies the source
bound `content_hash` before passing frozen `layer_samples` and
`evidence_context` to the `profit_gradient/v3` selector. The selector's
`automatic_behavior_change_authorized` result is permanently `false`.

Historical v1 artifacts remain verifiable for archival integrity but are not
accepted by the current Profit Gradient CLI and are not mixed with v2 evidence.
