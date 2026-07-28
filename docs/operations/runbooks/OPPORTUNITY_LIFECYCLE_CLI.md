# Opportunity Lifecycle CLI

## Boundary

The opportunity lifecycle CLI is a read-only reconstruction layer. It reads existing opportunity, decision, proposal, risk, order, position, shadow, forward-outcome, and audit-summary evidence to answer:

> Why did this opportunity not become a trade?

It must not create thesis text, call an LLM, rerun ranker selection, rerun guard evaluation, invoke debate, invoke risk, submit proposals, place orders, or change trading behavior.

Missing legacy evidence stays `insufficient_data`.

## CLI

Primary entrypoint:

```text
uv run aletheia opportunity audit --hours 24
uv run aletheia opportunity trace --id <id>
```

Useful filters:

```text
uv run aletheia opportunity audit --hours 24 --stage debate_downgraded
uv run aletheia opportunity audit --hours 24 --verdict missed_alpha_suspect
uv run aletheia opportunity audit --hours 24 --symbol TRX/USDT
```

Specialized views are available under the same command:

```text
uv run aletheia opportunity audit --view funnel
uv run aletheia opportunity audit --view sizing
uv run aletheia opportunity audit --view alpha
uv run aletheia opportunity audit --view thesis
```

`funnel`, `sizing`, `alpha`, and `thesis` are not separate top-level owner workflows. They are focused views of the opportunity lifecycle:

- Funnel: where opportunity flow stops before an opened position.
- Sizing: why intended or executed position sizes are small.
- Alpha: whether layers appear to add, miss, or destroy value.
- Thesis: whether the original trading rationale links through decision, proposal, execution, and outcome evidence.

The overview integrates the read-only summaries that are already useful:

- Trade Funnel: overall opportunity-to-position choke point.
- Debate Impact: persisted debate skip trace and forward outcome verdicts.
- Thesis Lineage: thesis completeness and missed-alpha summary.
- Sizing Trace: proposal/position sizing bottleneck summary.

Guard Impact remains a standalone diagnostic in V1 because it evaluates active guard rules against current opportunities. The flow ledger only marks `guard_blocked` when persisted evidence already says a candidate was guard-blocked.

## Stage Coverage

V1 reconstructs these stages when evidence exists:

- `created`
- `ranker_excluded`
- `guard_blocked`
- `venue_unsupported`
- `ttl_expired`
- `signal_pending`
- `operator_skip`
- `debate_downgraded`
- `proposal_created`
- `risk_rejected`
- `order_executed`
- `position_opened`
- `position_closed`
- `shadow_resolved`
- `forward_outcome_resolved`

## Compatibility Routing

`aletheia flow` is now a hidden compatibility alias for `aletheia opportunity`.

The old focused commands remain executable for scripts, but human output points back to the opportunity command:

```text
uv run aletheia flow audit      -> uv run aletheia opportunity audit
uv run aletheia flow trace      -> uv run aletheia opportunity trace
uv run aletheia funnel audit    -> uv run aletheia opportunity audit --view funnel
uv run aletheia sizing trace    -> uv run aletheia opportunity audit --view sizing
uv run aletheia alpha audit     -> uv run aletheia opportunity audit --view alpha
uv run aletheia thesis audit    -> uv run aletheia opportunity audit --view thesis
```

`inspect opportunities` remains the raw row listing: it answers "which opportunities exist?" The opportunity CLI answers "what happened to this opportunity lifecycle?"
