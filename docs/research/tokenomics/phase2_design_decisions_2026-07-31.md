# Phase 2 Design Decisions — 2026-07-31

Frozen owner-review evidence for the `tokenomics` branch's Phase 2
work. Two decisions were made during PR#480's landing window; this
note preserves them alongside the Proposal so future contributors do
not have to re-derive the reasoning from a chat log.

Source: owner review 2026-07-31, following completion of Phase 1
(PR #480 merged). Supersedes any earlier informal sketches of these
two points; the merged Proposal
(`docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md`)
will be amended by a follow-up doc PR to reflect these decisions
in its normative sections.

## Decision 1 — HTTP writer endpoints for `economics_budgets` are deferred

**Question raised.** Phase 2 needs a way for operators to declare
budgets (add / edit / disable). The Proposal §4 Phase 2 sketch said
"Settings gains a small Budgets table with add / edit / disable
controls," which implied a `POST /api/economics/budgets/*` writer
surface. Phase 1 (#480) shipped a hard rule that HTTP endpoints are
**read-only** and all writers stay in the CLI, matching how
`pricing-set` and `tool-pricing-set` already work.

**Decision.** Defer the HTTP writer question. Phase 2B will ship
CLI-only writers (a new `aletheia audit budget-set …` command); the
Settings UI will render declared budgets **read-only** and operators
open a terminal to change them.

**Why deferred rather than resolved.** The broader repository has
open issues describing HTTP authentication gaps on tunneled
runtimes (#395, #397, #398, #404). Adding a mutable HTTP surface
for budgets before those are resolved would put an accounting
control behind the same weak gate. Once the auth story is
consolidated across those issues — likely as a loopback / admin-token
mechanism applied uniformly — the budgets writer surface can be
reconsidered alongside the other mutations that surface would
authorise.

**Follow-up action item.** When any of #395/#397/#398/#404 lands
with a concrete auth mechanism, re-open the "should
`POST /api/economics/budgets` exist" question. It is intentionally
not tracked as a sub-issue of #475 today because the decision
depends on external progress.

## Decision 2 — `would_block` observations go in `metadata_json`

**Question raised.** Phase 2B's shadow mode records, on every LLM
call, whether the call would have breached any declared budget. The
observation needs to be stored so operators can inspect "which
calls would have been blocked last week" before flipping
`enforcement_mode` off `warn_only`. Three candidate storage shapes
were considered:

- **(a) Add a column to `runtime_cost_events`.** New column
  `would_block_json TEXT`, populated only when a budget was crossed.
  Requires ALTER TABLE on an existing production table.
- **(b) Reuse the existing `metadata_json` column.** Add a
  `would_block` key to the JSON blob when applicable. Zero schema
  change.
- **(c) Create a separate `economics_would_block_events` table.**
  Row per observation, foreign-key back to `runtime_cost_events`.
  Cleanest separation; two tables to join.

**Decision.** Ship option (b). Zero migration risk, no ALTER TABLE
on a table that already serves production traffic, no new join for
existing queries. Every current economics query (system-wide spend,
per-scope summary, unknown-count) already ignores metadata; adding
a new metadata key does not affect them.

**Recorded migration path to option (c).** If observation volume
grows to the point where either:

- typical `metadata_json` blobs exceed a few hundred bytes because
  every event now carries a would-block record, or
- operator queries need to filter or aggregate would-block
  observations at a rate where `json_extract(metadata_json,
  '$.would_block')` cannot keep up (a follow-up performance note
  should quantify this before acting),

then migrate the accumulated would-block observations to a
dedicated `economics_would_block_events` table with columns:

```sql
CREATE TABLE economics_would_block_events (
    id TEXT PRIMARY KEY,
    runtime_cost_event_id TEXT NOT NULL REFERENCES runtime_cost_events(id),
    policy_id TEXT NOT NULL REFERENCES economics_budgets(id),
    scope TEXT NOT NULL,
    subject TEXT,
    usd_cap REAL NOT NULL,
    current_window_spent_usd REAL NOT NULL,
    projected_over_usd REAL NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(runtime_cost_event_id, policy_id)
);
CREATE INDEX idx_would_block_scope_time ON economics_would_block_events(scope, observed_at);
```

Historical `metadata_json.$.would_block` observations get replayed
into the new table in a one-time backfill; new observations write
to the table going forward. The `metadata_json` key stays for a
compatibility window and is then removed.

**Why not do this now.** Speculative optimisation. We have no
volume data. Phase 2B's tests will bound the shape of the
observation blob so it does not silently grow, and Phase 1's
existing `runtime_cost_events` observability (`/api/economics/costs`)
will surface any pathological size early.

## Small implementation notes for Phase 2A

Phase 2A (this PR) ships the scaffold that both decisions depend on
without exercising either:

- `AletheiaConfig.economics: EconomicsConfig` with flags
  `enabled`, `budgets_shadow_enabled`, `enforcement_mode`,
  `harness_header_default_on`. All defaults preserve current
  behaviour.
- `economics_budgets` table via `ensure_economics_schema()`. The
  table exists idempotently but has no writer wired to it and no
  consumer in the provider layer until Phase 2B.

Phase 2B, when it starts, will:

- Add a CLI command `aletheia audit budget-set …` matching the
  existing `pricing-set` shape (decision 1).
- Add the shadow-mode check inside
  `aletheia/model_provider/fallback.py::complete_with_fallback` and
  populate `runtime_cost_events.metadata_json.$.would_block` when
  applicable (decision 2).
- Add a read-only budget listing to the Settings tile and a new
  `GET /api/economics/budgets/would-block` endpoint (decision 1
  again — no writer here).

Neither Phase 2A nor Phase 2B unlocks any hard blocking. Phase 3's
`enforcement_mode` toggle stays defaulted to `warn_only`.
