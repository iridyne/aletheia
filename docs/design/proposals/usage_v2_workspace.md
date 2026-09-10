---
id: usage-v2-workspace
status: implemented
related_contracts:
  - docs/design/api/api_reference.md
  - docs/design/data/data_utilization.md
supersedes: []
---

# Usage v2 production read model and workspace

## Summary

Usage becomes one read-only, decision-oriented page for LLM consumption, cost,
attribution, and service health. The same change replaces the existing
debug-shaped projection with a strongly typed `contract_version: 2` read model
whose costs are resolved against the immutable price snapshot effective when an
event occurred. This is D2 because it changes the product information
architecture and the externally observable API contract across collection,
economics, and frontend boundaries.

## Pre-change fact

`GET /api/economics/usage-insights` assembled dynamic sections for
coverage, token mix, cost provenance, attribution, tools, reliability,
budgets, trends, and research yield. The frontend discovered table columns from
arbitrary object keys and progressively requested `summary`, `operations`, and
`analysis`. Catalog backfill used a latest-snapshot map, so a read could apply a
new price to an older event instead of selecting the snapshot effective at the
event's `started_at`. The contracts remained aggregate-only and never exposed
raw prompts or responses.

## Goals and non-goals

The page answers, in order: selected UTC window and core results, trend,
consumption attribution, reliability, and configured budget. Unavailable cost
or token evidence is represented by JSON `null` and UI `N/A`; partial numeric
totals are never presented as complete. The page retains no write action.

This proposal does not add providers, dependencies, a configurable dashboard,
a parallel API, pricing maintenance UI, historical event mutation, budget
policy mutation, trading authority, deployment, or a release-per-change flow.
Local tools remain non-billable and provider-native fees stay attached to the
owning LLM event.

## Proposed design

### Cost invariants

For each billable LLM event, cost resolves in this order:

1. use an explicit provider-reported USD amount;
2. otherwise use observed usage and the immutable price snapshot satisfying
   `effective_from <= event.started_at`, choosing the newest such snapshot;
3. when cache counters were not provided, price all input tokens at the normal
   input rate;
4. when cache read or cache creation counters were provided, require their
   matching rates and never substitute the normal input rate for a missing
   special rate;
5. require actual usage, model identity, and every applicable rate; otherwise
   the event, containing bucket, containing breakdown row, and window cost are
   unavailable.

Failed calls with usage are billable. Failed calls without usage are not
assumed to be zero. A logical LLM call may contain multiple attempts; retries
increase Attempts but do not create new logical calls.

### API contract delta

`GET /api/economics/usage-insights` remains the sole endpoint and keeps
`days=1|7|30|90`. It now requires one of two progressive groups:

- `summary`: `contract_version`, `window`, `overview`, `comparison`,
  `token_composition`, and `trend`;
- `operations`: `contract_version`, `window`, `breakdowns` for agents, models,
  providers, and tools, plus `reliability`, `active_cooldowns`, and `budgets`.

Counts are integers, unavailable numbers are `null`, and optional token
subtypes are omitted when never observed. The v1 `analysis` group and dynamic
rows are removed. Summary completes before operations is requested.

### Product surface

The header contains only the page purpose, `1d / 7d / 30d / 90d`, and the UTC
update time. Five fixed KPIs show Cost, Total tokens, LLM calls, Success rate,
and Avg cost/call; prior-window deltas appear only when the comparison window
is complete. One full-width chart switches among Cost, Tokens, and Calls, using
UTC hour/day/week buckets; incomplete numeric buckets remain gaps.

Token composition always shows Input and Output, and conditionally shows Cache
read, Cache write, and Reasoning only when observed. One full-width table groups
by Agent, Model, Provider, or Tool with fixed columns. Missing attribution is
collected under `Unattributed`. Reliability shows Attempts, Errors, Retries,
Fallbacks, P50, and P95; error categories and cooldowns appear only when
present. Budget is absent when no policy exists and otherwise shows only Limit,
Spend, Remaining, Utilization, and Period.

## Trust and complexity review

The changed trust boundary is semantic: Usage must not turn missing billable
evidence into zero, a partial total, or a historical price rewrite. The design
keeps internal completeness reasons for logs and tests while removing them from
the user page. A frontend-only redesign is insufficient because it would hide
known aggregation errors; a parallel endpoint would duplicate authority. The
smallest complete change is one shared event-cost resolver, one v2 projection,
and one typed page using existing table, card, segmented-control, responsive,
and Recharts primitives.

## Verification contract

Backend regressions must prove event-time price selection, rejection of latest
price backfill, cache-unobserved normal pricing, exact cache pricing, missing
special-rate failure, incomplete-event propagation, logical calls versus
attempts, bucket granularity, and prior-window comparison. Provider tests must
prove complete, structured, and stream paths record usage once, preserve the
actual model and token subtypes, and do not turn absent usage into zero.

Frontend regressions must prove fixed typed columns, `N/A`, conditional token
subtypes, conditional error/cooldown/budget rendering, empty/error/loading
states, and absence of provenance, coverage, research, and pricing-maintenance
language. Required checks are focused Python tests, frontend unit tests,
TypeScript production build, documentation validation, and browser checks at
1440x900, 924x766, and 390x844.

## Rollout and rollback

Repository acceptance is exact-head CI plus merged-content verification. It is
not deployment proof and does not trigger a dedicated release. A later normal
Fedora update must verify the exact merged commit, Gateway pricing refresh,
contract v2 response, and the real `/usage` page, then sample raw usage against
the event-effective snapshot. Rollback restores the prior merged application
version; stored events and immutable price snapshots require no data rollback.

## Resolution

Implemented: the API reference and data-utilization contract are the normative
owners. The superseded frontend design remains historical evidence.

## Conformance addendum: trusted catalog fallback

The implemented cost invariants permit delayed binding of an actual provider
model to an immutable LiteLLM catalog observation captured before the physical
attempt began. The cached catalog receipt is content-addressed and records its
trusted observation time, source, entry count, and matching algorithm. A price
snapshot derived after the response may use that earlier observation time as
`effective_from`; this adds immutable price evidence and never updates the
historical usage event.

Catalog snapshot versions bind both the catalog content hash and the normalized
rate-vector hash. Re-parsing unchanged catalog bytes with newly supported cache
read, cache creation, reasoning, or provider-native tool rates therefore creates
a distinct immutable snapshot. A legacy cache without a receipt is historical
evidence only when an existing snapshot contains the same catalog hash; its
earliest `effective_from` becomes the recovered observation time.

This fallback never substitutes the requested model for a different actual
model, never applies a catalog first observed after the event, never treats a
positive special-use counter as ordinary input or output when its dedicated rate
is absent, and never assigns zero cost to an issued provider attempt without
usage evidence. A terminated logical call with zero physical attempts is the
only inference failure whose zero tokens and zero cost are directly confirmed.
