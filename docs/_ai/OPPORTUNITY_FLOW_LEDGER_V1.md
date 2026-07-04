# Opportunity Flow Ledger V1

## Boundary

Opportunity Flow Ledger V1 is a read-only reconstruction layer. It reads existing opportunity, decision, proposal, risk, order, position, shadow, forward-outcome, and audit-summary evidence to answer:

> Why did this opportunity not become a trade?

It must not create thesis text, call an LLM, rerun ranker selection, rerun guard evaluation, invoke debate, invoke risk, submit proposals, place orders, or change trading behavior.

Missing legacy evidence stays `insufficient_data`.

## CLI

Primary entrypoint:

```text
uv run aletheia flow audit --hours 24
uv run aletheia flow trace --opportunity-id <id>
```

Useful filters:

```text
uv run aletheia flow audit --hours 24 --stage debate_downgraded
uv run aletheia flow audit --hours 24 --verdict missed_alpha_suspect
uv run aletheia flow audit --hours 24 --symbol TRX/USDT
```

V1 integrates the read-only summaries that are already useful:

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

## Old Audit Command Routing Plan

Do not delete or hard-route existing commands in V1. They are still useful as focused views and may be used by scripts.

Recommended migration:

1. V1: keep old commands unchanged and introduce `aletheia flow`.
2. V1.1: add a short note to old human-readable outputs:
   `This report is also summarized by: uv run aletheia flow audit --hours <n>`.
   Implemented for `inspect debate-impact` and `inspect guard-impact`.
3. V2: if the flow ledger is stable, optionally make old commands thin section views:
   - `aletheia funnel audit` -> flow funnel section
   - `aletheia thesis audit` -> flow thesis section
   - `aletheia sizing trace` -> flow sizing section
   - `aletheia inspect debate-impact` -> flow debate section

This keeps compatibility while making the opportunity flow ledger the collaboration entrypoint.
