# Entry Workbench design

Status: current owner-facing contract for the `/universe` page. The historical Decision
Blotter and Universe Board specifications are not current product authority.

## Ownership and causal path

The primary owner path is Candidate → Entry Plan → Risk → Order → Fill →
Position. A Trigger exists only inside a `TRIGGERED` Entry Plan. The page lives
at `/universe`; `/blotter` and `/proposals` are compatibility redirects to the
default Entry Plans tab.

Current Candidates belong to the current valid Snapshot's `universe_members`.
Candidate attention belongs to `candidate_planning_generations`, while immutable
plan facts and monotonic plan lifecycle belong to `entry_plans`. Snapshot members
remain selection evidence and are not an economic ledger. Static fallback symbols
are instrument availability only and never become Candidate attention implicitly.

## Read model

| Endpoint | Contract |
| --- | --- |
| `GET /api/universe/workbench` | All current Snapshot Candidates, bounded active Entry Plans and Candidate attention, exact summary counts, Universe/fallback state, and per-section availability. `current_candidates` is `null` on read failure and `[]` only after a successful read with no current Candidates. |
| `GET /api/universe/entry-plans/{plan_id}` | Any active or terminal plan by exact identity; validates the stored canonical or read-only historical payload and hash. |
| `GET /api/universe/candidates/{candidate_id}` | Exact Candidate evidence; `/members/{id}` remains compatibility. |
| `GET /api/universe/planning-history?limit=&before=` | Stable `(updated_at,id)` pagination over planning generation plus Entry Plan ledgers. |
| `GET /api/trades?entry_plan_id=` | Exact server-side execution lookup. |

Activity and summary projections read the same ledgers. A failed section is
`unavailable`, never an empty list or zero. Realtime updates reuse the shared
WebSocket fingerprints for `candidate_planning_generations` and `entry_plans`;
30-second REST polling remains the fallback.

Cycle history projects every persisted trigger activation from the existing
activation table, including superseded authorizations. Monitoring evidence records
the persistence time of each material readiness/generation transition, without a
per-tick ledger. An expired authorization uses its exact expiry for the effective
state timestamp; a generation mismatch uses the recorded monitoring transition.
Older evidence without that timestamp remains unknown. The linked Plan owns the
projection's Plan identity even if a generation's old convenience pointer is empty.

## Page structure

The first viewport has one summary row: Current Candidates, Planning Eligible,
Blocked, and Active Entry Plans. Tabs are Current Candidates (default), Entry Plans,
Planning Queue, and Planning History. Manual Review is absent because the producer audit found no
durable review-required authority; `TradeProposal.status='pending'` is not such
authority.

Active Entry Plan statuses are `waiting`, `risk_reserved`, `triggered`, `ordered`,
and `partially_filled`. Terminal plans remain available by exact ID and in Planning
History. Candidate rows show attention start, last real requalification, soft and
hard deadlines, planning status, and terminal reason. Plan detail keeps immutable
geometry primary and links to exact Candidate, execution, and chart overlay.

## Truthful states and responsiveness

`Universe disabled`, `no active snapshot`, `fallback instruments available`, `no
active Candidates`, empty ledger, stale cache, and read unavailable are distinct.
Fallback symbols never contribute to Candidate counts. The shared Tabs, DataTable,
DetailDrawer, Card, Button, Badge, and MetricTile primitives retain keyboard
behavior. Summary columns are 4 at 1280/1024, 2 below 1024, and 1 below 640; table
panels scroll horizontally without changing semantic order at 640/768/1024/1280.

## Settings and charts

Snapshot TTL, Candidate soft lease (`entry_ttl_sec`), and frozen maximum continuous
Candidate attention age (`candidate_max_continuous_age_sec`) are separate controls.
The hard age must be at least the soft lease. A plan chart deep link carries exact
instrument identity plus `entry_plan_id`; Charts reads the stored plan locally and
overlays Entry Zone/resting limit, registered-trigger numeric geometry,
invalidation, Stop, and Target. It never performs a per-plan remote market fetch.
