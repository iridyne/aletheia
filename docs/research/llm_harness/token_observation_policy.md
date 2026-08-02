# Default Token Observation Policy

## Decision

Aletheia's default token policy is `integrity-v1`: it is always
**non-blocking**, records only stable rule identifiers, and does not require a
price snapshot, budget policy, prompt capture, or baseline history.

It is deliberately not a token quota. Quotas need representative observation
windows and an explicit owner decision; integrity checks do not.

## Observations

| Rule ID | Condition | Meaning |
| --- | --- | --- |
| `input_or_output_usage_unavailable` | either core token counter is absent | provider usage coverage gap |
| `total_tokens_below_input_plus_output` | provider total contradicts core counters | inconsistent provider payload |
| `reasoning_tokens_exceed_output` | reasoning breakdown exceeds output | inconsistent provider payload |
| `cache_read_tokens_exceed_input` | cache-read count exceeds reported input | incompatible cache semantics/payload |
| `cache_creation_tokens_exceed_input` | cache-creation count exceeds reported input | incompatible cache semantics/payload |

The observation is stored as `token_integrity_observations` in the existing
LLM attempt metadata. It contains only a rule ID. No prompt, provider payload
fragment, token estimate, cost estimate, decision change, retry, tool
permission, or order behavior is changed.

## Why this is the initial default

At the current maturity level, provider payload integrity and field availability
are factual and immediately actionable. Spend or anomaly thresholds are not:
they need enough representative traffic, stable provider/model configuration,
and an owner-approved comparison window. Treating missing usage as zero would
make a threshold policy misleading.

## Evaluation and escalation

1. Track rule counts by provider/model/logical-agent/role through the token
   telemetry surfaces.
2. Investigate a new rule only as an observability or adapter-normalization
   issue; do not feed it into Risk or Execution.
3. Add a deterministic fixture before accepting a new provider field shape.
4. Consider an opt-in alert threshold only after a representative CASI window
   establishes a baseline. A threshold remains a notification, not a denial.
5. Token quotas or USD budgets stay separately gated by their explicit policy
   and observation requirements.
