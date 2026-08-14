# Profit-gradient evidence compiler contract

Status: implemented, read-only.  Contract: `profit_gradient_evidence/v1`.

## Producer

```text
uv run aletheia inspect profit-gradient-evidence \
  --epoch <EPOCH_ID> \
  --cutoff <UTC_TIMESTAMP> \
  --cost-model-id <COST_MODEL_ID> \
  --output profit-gradient-evidence.json
```

`build_profit_gradient_evidence()` accepts only those three identity selectors.
It does not accept layer samples.  It reads the registered
`evidence_anchor:profit_loop_observability` epoch, fixed-horizon candidate facts,
closed Position replays, and persisted cost-model identities.  It performs no
writes, network calls, LLM calls, or trading actions.

The artifact always contains:

```json
{
  "contract_version": "profit_gradient_evidence/v1",
  "registration": {},
  "evidence_context": {},
  "layer_samples": {},
  "exclusion_waterfall": {},
  "source_hashes": {},
  "content_hash": "..."
}
```

The compiler bounds facts to the registered epoch and cutoff, requires exact
exposure identity and producer provenance, requires an explicit one-treatment /
one-control assignment-unit pair, emits one pair-level contrast (with separate arm
counts), rejects duplicate units, and records the first exclusion reason.
Missing evidence stays unavailable; it is never converted to zero. `content_hash` is an integrity check for the frozen artifact. Before
`profit-gradient` adjudicates, the CLI rebuilds the source-bound artifact from
its exact inputs and requires the rebuilt hash to match; static metadata alone
cannot authorize hand-authored samples.

The compiler's paired contrasts declare `evidence_context.comparison_method` as
`paired_same_unit`. The same context carries the singular `horizons`,
`outcome_contract_versions`, `metric_versions`, `statistical_unit_versions`, and
`stages` arrays consumed by the v3 selector. A missing or mixed outcome-contract
or stage identity remains ineligible; the compiler never labels a precomputed
pair contrast as a prospective randomized arm observation.

Candidate outcomes use one fixed `24h` net-return contract, a persisted
`settled_at <= cutoff`, and the exact supplied cost-model version. Compressed
candidate batches are unavailable when their declared count differs from the
decoded rows, and deterministic exclusion records are part of the source hash.
Position samples additionally require the replay's explicit cost-model identity
`paper-execution-reference/v1`, exact order/fill/Position reconciliation,
accounting agreement, and assignment before entry; they also require runtime
commit/fingerprint provenance.
Position economics use the verified accounting
identity `net = gross - entry_fee - exit_fee - funding`; candidate costs are not
deducted again from Position economics.

The current durable contracts provide no confirmatory seven-layer path mapping;
all layers remain unavailable until their own registered layer contracts and
paired sources exist. They also do not provide a market-data epoch for this path.
Those gates therefore remain unavailable until the source contract is extended. The fixed
profit-loop counterfactual registry is emitted only under `exploratory_evidence`,
marked `promotion_eligible: false`, and is excluded from `layer_samples`. This
prevents exploratory evidence from becoming confirmatory evidence by operator
recombination.

## Consumer

`uv run aletheia inspect profit-gradient --evidence <FILE>` accepts only the
outer compiler artifact `profit_gradient_evidence/v1`. It verifies the source
bound `content_hash` before passing the frozen `layer_samples` and
`evidence_context` to the selector, whose report contract is
`profit_gradient/v3`. The selector's `automatic_behavior_change_authorized`
result remains permanently `false`.
