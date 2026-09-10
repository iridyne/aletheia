# Runtime Cost Accounting

Runtime model and tool costs remain independent of trading qualification.
The former Paper Internal economic viability calculations are [archived](../design/history/local_simulated_viability.md).

## Runtime facts

The economics ledger is append-only and is installed idempotently on the first AgentRun, physical model call, or audit command. Schema initialization refuses to run inside an active SQLite transaction, so measurement cannot implicitly commit trading state.

### LLM attempts

Every physical provider `complete()` attempt is recorded separately, including retries and fallback models. Structured provider calls currently delegate to the same physical `complete()` boundary, so their usage is captured before business JSON is returned. A record contains:

- AgentRun and Runtime Session identity when available;
- a logical System Operator identity for event-driven daemon calls that intentionally have no generic AgentRun;
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

Existing durable `agent_tool_calls` are mirrored into the economics ledger. Current real Agent tool operations record nullable monotonic `duration_ms`, which is mirrored as tool-event latency; legacy rows and bookkeeping rows with no operation remain `NULL`. The record is still intentionally limited:

- local orchestration tools have zero external marginal cost; their underlying model calls are metered separately;
- a missing duration remains `NULL` instead of being fabricated;
- tool calls carry no LLM token usage and are never allocated LLM tokens;
- retry and external billing facts remain unavailable unless their own durable contracts record them.

Provider-native tool charges follow the catalog and measured counters described below.

### AgentRun aggregation

`AgentRun.cost_estimate` is a derived cache, not the accounting source of truth:

- it is populated only when every runtime cost event for that run is complete;
- otherwise it remains `NULL`;
- the known subtotal and unknown-event count remain available from `runtime_cost_events`;
- retries and fallback attempts are never collapsed into the final successful call.

The event-driven System Operator daemon is deliberately not represented as a fabricated AgentRun. Its physical model calls carry `logical_agent=pi_trading_decision` in runtime cost events.

## Pricing contracts

Model pricing has exactly one source: LiteLLM's public GitHub price catalog.
It is provider/API-base independent and is refreshed automatically at startup or
through the audited Usage-page refresh action. There is no manual model-price
writer. Catalog prices remain explicitly estimated, and historical cost-event
rows are never rewritten.

Persisted catalog snapshots are inspectable with:

```bash
uv run aletheia audit pricing-list --json
```

Provider-native built-in tool charges are also read from the same public catalog
when both a catalog rate and a measured usage counter exist. Web-search request
counts are currently supported. Missing counters or rates remain Unknown.
Aletheia-local orchestration tools have zero external marginal cost; their
underlying LLM calls are priced separately, preventing double counting. There is
no manual tool-price writer.

## Read-only inspection

```bash
uv run aletheia audit costs --days 7
uv run aletheia audit costs --days 7 --json
uv run aletheia audit costs --run-id <agent-run-id> --days 30
```

## Tracing control

`ALETHEIA_COST_TRACING=0` stops new runtime cost facts. Historical records remain readable.
