# Economic Viability Audit V1

## Purpose

This module answers two deterministic accounting questions without changing trading behavior:

1. **System Viability** — did verified, cost-aware paper outcomes cover all deployment costs in the same window?
2. **Operator Agentic Viability** — did the System Operator's accepted trades outperform exact same-cycle Shadow controls by more than the Operator's own dynamic intelligence cost?

The feature is measurement-only. It does not change prompts, model routing, candidate ranking, proposals, Risk, Execution, Position management, Learning authority, or live permissions.

## Runtime facts

The economics ledger is append-only and is installed idempotently on the first AgentRun, physical model call, or audit command.

### LLM attempts

Every physical provider `complete()` attempt is recorded separately, including retries and fallback models. A record contains:

- AgentRun and Runtime Session identity when available;
- configured provider alias and exact model;
- requested model and attempt index;
- start/end timestamps and latency;
- raw and normalized input/output/reasoning/cached-token usage;
- immutable pricing snapshot;
- estimated USD cost or an explicit `unknown` status;
- success/error status and error type.

A failed attempt with no billable usage is **unknown**, never zero.

Streaming providers are recorded when the stream terminates. A cancelled or failed stream remains an error attempt.

### Tool calls

Existing durable `agent_tool_calls` are mirrored into the economics ledger. Because the current Tool Call contract does not contain start time, end time, latency, retry, or external billing, the compatibility record is honest:

- per-call cost comes from an immutable Tool Cost Profile when configured;
- no matching profile means `cost_status=unknown`;
- latency remains `NULL` instead of being fabricated.

Explicitly free local tools must be configured with a per-call cost of `0`. External APIs can use their actual or allocated per-call rate.

### AgentRun aggregation

`AgentRun.cost_estimate` is a derived cache, not the accounting source of truth:

- it is populated only when every runtime cost event for that run is complete;
- otherwise it remains `NULL`;
- the known subtotal and unknown-event count remain available from `runtime_cost_events`;
- retries and fallback attempts are never collapsed into the final successful call.

## Pricing contracts

No current internet price is guessed and no historical call is silently repriced.

```bash
uv run aletheia audit pricing-set \
  --provider <configured-provider-name> \
  --model <exact-model-id> \
  --input <usd-per-million-input-tokens> \
  --output <usd-per-million-output-tokens> \
  --source <invoice-or-contract-reference>
```

Optional rates:

```text
--reasoning <usd-per-million-reasoning-tokens>
--cached-input <usd-per-million-cached-input-tokens>
--effective-from <ISO timestamp>
--version <contract version>
```

Tool/API profile:

```bash
uv run aletheia audit tool-pricing-set \
  --tool <exact-tool-name> \
  --per-call <usd-per-call> \
  --source <invoice-or-allocation-reference>
```

Use `--per-call 0` only when zero cost is an explicit fact.

## System Viability contract

```text
System Viability Amount
= verified net trading result
- LLM cost
- Tool/API cost
- allocated data cost
- allocated infrastructure cost
- allocated other deployment cost
```

`verified net trading result` comes only from the existing Outcome Integrity cohort. It already proves:

```text
realized_pnl
= gross price PnL
- entry fee
- exit fee
- validated funding cost
```

Therefore the viability audit never deducts fee, spread/slippage, or funding a second time.

Unrealized PnL, administrative closes, maintenance closes, incomplete fills, missing funding economics, and incomplete attribution are excluded by the existing outcome contract.

Data, infrastructure, and other fixed allocations must be supplied explicitly for the same window, including explicit zero values. Missing allocations produce `invalid`, not a partial positive result.

```bash
uv run aletheia audit viability \
  --scope system \
  --days 7 \
  --data-cost 0 \
  --infrastructure-cost 0 \
  --other-cost 0
```

## Operator Agentic Viability contract

For each eligible closed position, the audit requires:

- an executed Proposal with an Opportunity identity;
- a selected Shadow created no later than the Proposal;
- the same market type and direction;
- the same immutable close event as the Position;
- at least one unselected control in the same Shadow batch;
- the same Shadow cost-model version;
- cost-aware `net_pnl_pct` for all controls.

The per-trade calculation is:

```text
Operator Incremental Value
= actual verified realized PnL
- mean same-batch control net PnL expressed at actual trade notional
```

The window calculation is:

```text
Operator Agentic Viability Amount
= sum Operator Incremental Value
- all System Operator LLM costs in the window
- all System Operator Tool/API costs in the window
- explicit Operator-attributable dynamic data cost
```

All Operator runs are charged, including skipped decisions. This prevents the audit from assigning the cost of unsuccessful research to nobody.

Risk, execution, and deterministic exit behavior remain embedded in actual realized PnL. V1 does not claim that every difference from Shadow was caused only by the LLM.

```bash
uv run aletheia audit viability \
  --scope operator \
  --days 7 \
  --data-cost 0
```

## Verdicts

- `positive`: complete evidence and viability amount greater than zero;
- `neutral`: complete evidence and viability amount equal to zero within numerical tolerance;
- `negative`: complete evidence and viability amount below zero;
- `insufficient_data`: no eligible outcome or exact Operator control cohort;
- `invalid`: a required accounting input exists but is incomplete or unknown.

Every report stores its assumptions, exclusion counts, metric version, outcome contract version, and cost completeness.

For reproducible reruns, pin the window end:

```bash
uv run aletheia audit viability ... --as-of 2026-07-16T12:00:00+00:00
```

The same inputs produce the same report identity and do not create duplicate rows.

## Read-only inspection

```bash
uv run aletheia audit costs --days 7
uv run aletheia audit costs --days 7 --json
uv run aletheia audit costs --run-id <agent-run-id> --days 30
```

## Kill switches

```text
ALETHEIA_COST_TRACING=0
ALETHEIA_VIABILITY_AUDIT=0
```

Disabling tracing stops new cost facts. Disabling viability blocks report generation. Neither switch changes trading behavior or deletes historical evidence.

## Known V1 limits

- Existing Tool Calls lack measured latency; V1 exposes this gap as `NULL`.
- Provider pricing must be entered before a call; unknown historical calls are not retroactively guessed.
- Fixed data/infrastructure allocations are supplied per report rather than fetched from cloud billing APIs.
- Operator attribution is an exact accepted-vs-Shadow cohort comparison, not a causal decomposition of Prompt, Memory, Risk, Execution, and Exit components.
- V1 covers `paper_internal`; it does not qualify live trading economics.
