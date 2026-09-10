# Archived: local simulated protection and exit experiments

Paper Internal protection activation, forward adoption, completed-bar execution,
local funding calculation, shadow settlement and trailing-recovery production are
retired. The simulator modules and their runtime tests are removed. This document
preserves their former contract; it does not authorize these actions.

External exchange Positions save their current ExitPlan through
`portfolio.exit_plan.save_exit_plan`. Historical simulation tables and identities
remain intact. Reports read stored outcomes and recovery observations; an absent
observation remains unavailable and is not fetched or reconstructed. New closes
do not enroll in these experiments or enqueue their retired outcome hooks.

The former `PositionExitPlanVersion`, `PositionProtectionState`,
`PositionProtectionTrigger`, `ExitBreakevenShadowAssignment` and
`ExitBreakevenShadowOutcome` construction models are deleted as well. Historical
reports read stored rows directly; the schema and its immutable evidence remain.

## Former data-model contract

## 11. Protective Barrier Contract (schema v9 storage; Paper runtime active)

Schema v9 provides the durable substrate. The runtime consumer activates it
inside the transaction that opens a new `paper_internal` Position with at least
one resolved protective level. Its periodic cycle also forward-adopts a still-open
local-paper Position that lacks protection: the current resolved plan becomes
effective at the adoption instant and the cursor starts at the first complete
1-minute bar whose open is not earlier than that instant. No pre-adoption candle
is replayed, and a mid-hold adoption is not assigned to the prospective exit
shadow. A level-less Paper entry does not fabricate an ExitPlan or cursor.
Migration never creates these facts. The protective scheduler evaluates only
the frozen exact instrument's completed 1-minute bar;
missing data remains a visible gap and a bar bisected by an ExitPlan change is
skipped as `exit_plan_boundary_unordered` rather than assigned to either plan.
Before evaluation, the same protective worker refreshes only missing due bars
from the frozen venue with an explicit `since` cursor, bypassing the general
OHLCV TTL cache and forbidding a fallback venue. Available backlog bars are then
consumed in chronological order under a bounded per-position catch-up limit.

The storage invariants are:

1. `positions.exit_plan_effective_at` is nullable. It advances atomically only
   when a contiguous immutable `position_exit_plan_versions` row is inserted;
   it cannot be cleared, fabricated, or regressed.
2. ExitPlan identity is `(position_id, version, effective_at, plan_hash)`. The
   stored JSON hash and resolved stop/take-profit/exit-limit values must agree,
   and an applied version cannot be updated or deleted.
3. Each activated Position has at most one retained
   `position_protection_state` row. Activation freezes lowercase venue,
   canonical `BASE/QUOTE` symbol, physical market (`spot` or `perp`), and
   `timeframe='1m'`. Only logical `spot -> spot` and
   `perp|usdt_futures -> perp` mappings are accepted; unknown market types fail
   closed. Activation is explicit; forward adoption never backdates its plan or
   cursor and never claims historical barrier coverage.
4. `next_bar_open_ms`, `last_inspected_bar_open_ms`, and
   `last_evaluated_bar_open_ms` are monotonic compare-and-set cursors. A gap
   retains its reason, detection time, count, and bounded recent error without
   silently advancing the evaluation cursor.
5. The first trigger is one complete row in the append-only
   `position_protection_triggers` table: UUID/detection time,
   frozen identity, exact 1-minute bar and provider/hash, exact ExitPlan
   identity and resolved barriers, hit flags/reason, and
   `collision_policy='stop_first_v1'`. Inserting it atomically projects
   `trigger_id` and `triggered` onto state. Neither repositories nor direct SQL
   may replace any trigger field after the first successful compare-and-set. A
   trigger bar must be fully covered by one authoritative ExitPlan: a stale
   version, a plan effective after bar open, or a later plan effective before
   bar close is rejected instead of retroactively applying either plan.
6. `close_order_id`, then the exact
   `close_fill_id`/price/time/final state, are write-once and must resolve to the
   same Position and native close chain. Trigger evidence authorizes only a call
   to the existing `paper_internal` routed close: the close re-reads a fresh mark,
   applies adverse slippage and writes its normal Order/Fill. Barrier prices are
   never fabricated as fills. This consumer grants no Risk, external Execution,
   or Live authority.

Status is restricted to `active`, `gap`, `triggered`, `materialized`,
`superseded`, or `integrity_blocked`; terminal and triggered states cannot
regress to an evaluable state. A native non-barrier close supersedes an
untriggered cursor before any post-close candle is evaluated; a recorded trigger
continues through its exact close-linkage recovery instead. All three new tables
are permanent trust-bearing evidence and are excluded from retention pruning.
schema and central ledger writer reject NaN, both infinities, and finite-input
arithmetic overflow before any append or audit event.

## 12. Exit Breakeven Shadow Contract (schema v11, observe-only)

Schema v11 adds a single-variable prospective experiment for the concrete MVP
failure mode "a trade was profitable, then exited at a loss." For each newly
protected native `paper_internal` short-term Position, activation best-effort
freezes one rule before the first eligible bar: after a completed exact 1-minute
bar reaches 1.5% favorable return, an additional shadow stop at the entry fill
becomes active from the next completed bar. The treatment has
`behavior_authority=0`; it never changes the Position, ExitPlan, RiskDecision,
Order, Fill, scheduler authority, or Learning authority.

Settlement runs through the existing durable eligible-outcome hook. It compares
the real net result with the paired shadow result only when exact instrument
identity and contiguous completed 1-minute bars are available. A same-bar
trigger/stop cannot activate retrospectively. A shadow stop touch uses that
completed bar's close as the observable detection mark, then applies the frozen
adverse Paper slippage and fee model. Perpetual rows additionally require a
complete local funding window through the shadow close time. Any missing identity,
bar, ordering, arithmetic, actual Outcome, or funding fact produces an immutable
`unavailable` row with no invented delta.

Assignments and outcomes are retained append-only evidence. The descriptive
report can total paired incremental net PnL, but no sample count or result grants
promotion or parameter-mutation authority; a later owner-reviewed behavior trial
requires a separate measured Learning contract.
