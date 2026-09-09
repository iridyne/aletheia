# Trading Mandate Contract

## Status and boundary

A Trading Mandate is a governance and provenance contract. It is **not a
trading strategy**, a Strategy Factory replacement, a mandate router, or a
second decision authority. The first supported contract is
`trading_mandate/v1`, with at most one configured mandate and
`enforcement_mode: observe_only`.

The System Operator remains the sole native entry decision role. The Risk Engine
remains the sole approval authority. Observe-only mandate fit cannot block,
admit, rank, resize, close, reduce, adjust, protect, or otherwise mutate
trading behavior. Archived records contain descriptive evidence only.

Current execution uses registered external accounts. The former automatic
mandate-provenance producer is retired; configuration and historical inspection
do not imply active entry attribution or execution authority.

## Configuration

Configuration is optional. The existing sanitized Settings read exposes the
resolved status, and the validated `POST /api/settings/trading-mandate` control
accepts one complete object (or `null` to disable it). The control persists no
secrets or unknown nested fields in its response or audit record. Missing or
disabled configuration is `not_configured`. An enabled malformed object is
reported as `mandate_unavailable`; no values are guessed.

Example template (not enabled by default):

```yaml
trading_mandate:
  enabled: true
  contract_version: trading_mandate/v1
  mandate_id: crypto-observe
  mandate_version: 1
  status: active
  enforcement_mode: observe_only
  market_scope:
    allowed_asset_classes: [crypto]
    allowed_physical_market_types: [spot, perp]
    allowed_venues: [bitget]       # omit to leave undeclared
    allowed_symbols: [BTC/USDT, ETH/USDT]
  edge_scope:
    allowed_candidate_families: [momentum]
    allowed_directions: [long, short]
    min_intended_holding_horizon_hours: 1
    max_intended_holding_horizon_hours: 12
  default_behavior:
    insufficient_evidence: prefer_skip
    conflicting_directional_evidence: not_declared
    unavailable_cost_estimate: not_declared
  holding_governance:
    expected_horizon_reached: review_required
    opening_hypothesis_invalidated: review_required
```

Declared collections are normalized, deduplicated, and sorted. An explicitly
empty collection is invalid rather than ambiguous. Physical market aliases are
normalized to `spot` or `perp`; exact Proposal/exchange values are not rewritten.
Horizon values must be finite and positive, and minimum cannot exceed maximum.
Unknown mandate fields are rejected by the strict domain model.

The default-behavior and holding-governance fields describe intent only:
`prefer_skip`, `review_required`, and `not_declared` never become authority.
They cannot close, reduce, adjust, or protect a Position.

## Canonical identity

The complete normalized mandate payload is serialized with stable sorted keys,
compact JSON, no timestamps, process IDs, or incidental collection ordering,
and hashed with SHA-256. The identity projection is:

```json
{
  "contract_version": "trading_mandate/v1",
  "mandate_id": "crypto-observe",
  "mandate_version": 1,
  "content_hash": "sha256:<64 lowercase hex characters>",
  "enforcement_mode": "observe_only"
}
```

Any declared mandate field change rotates `content_hash`. Equivalent semantic
input has the same hash. A Proposal's existing hash/evidence identity is never
rewritten when configuration changes.

## Fit assessment

`assess_trading_mandate_fit(...)` is a pure deterministic function under
`trading_mandate_fit/v1`. The caller supplies the exact Opportunity, Decision,
and Proposal facts already available in the causal chain. The function performs
no market-data refresh, database lookup, model call, or network request. Missing
facts are `not_assessable`; undeclared optional dimensions are `not_declared`;
a mismatch is `out_of_scope`.

The result contains the mandate identity, overall status, deterministic checks
in fixed dimension order, stable reason codes, observed/declared values, exact
source identities, descriptive policy declarations, and
`behavior_changed: false`. Allowed overall statuses are:

- `in_scope`
- `out_of_scope`
- `not_assessable`
- `not_configured`
- `mandate_unavailable`

`out_of_scope` remains observation only. Fit never changes System Operator
action, direction, score, conviction, Proposal eligibility, quantity, notional,
leverage, Stop Loss, Take Profit, expected horizon, Risk approval, execution
routing, Position protection, Learning assignment, or Universe family
calibration evidence.

## Frozen Proposal and Position provenance

New entries do not produce the retired frozen mandate pair. They retain the
independent `position_hypothesis/v1` snapshot. Supplying archived mandate evidence
for a new Proposal is rejected; existing records remain immutable and are never
backfilled from current configuration.

Position Replay and mandate-compliance inspection read the exact archived pair
through `positions.proposal_id -> trade_proposals.id`, preserving missing,
unavailable and malformed states. The former producer contract is
[archived](../history/local_simulated_mandate.md).

## Read-only inspection

```bash
aletheia inspect trading-mandate --json
aletheia inspect mandate-compliance --proposal-id <id> --json
aletheia inspect mandate-compliance --position-id <id> --json
aletheia inspect mandate-compliance --hours 720 --json
```

The single-record surface reports the configured and frozen identity, frozen fit,
exact Proposal/Decision/Opportunity/Position source identities, and honest
unavailable or `not_recorded` state. The aggregate surface reports complete
Proposal/Position denominators by fit status, reason-code distribution, exact
holding-duration compatibility, declared-horizon overrun counts, eligible
genuine net outcomes grouped by fit status, exclusions, and exact cohort/
behavior identities. Missing economics remain unavailable and are not converted
to zero. Completed holding-duration compatibility and declared-horizon overrun
counts require valid opening and closing timestamps inside the report window;
open, invalid, or future-dated rows remain explicit exclusions.

All reports are observational. They do not emit recommendations, enforcement
decisions, live Scheme leaderboards, promotion results, or claims that mandate
compliance caused profit or loss. Inspection performs zero authoritative database
writes.

## Explicit deferrals and non-goals

This contract does not add or imply:

- multiple mandates (deferred);
- automatic mandate switching or regime routing (deferred);
- prompt injection (deferred);
- portfolio allocation (deferred);
- mandate enforcement (deferred);
- a Strategy Factory, RuleSet, strategy lifecycle, or parallel strategy authority;
- a new Agent, scheduler, service, Settings page, or editable UI surface beyond
  the validated existing Settings API control.

Mandate compliance is descriptive until a separate registered experiment proves
contribution. Schema existence alone is not proof of configuration, runtime
activation, authority, or qualification support.
