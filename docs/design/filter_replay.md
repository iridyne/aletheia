# Filter Feedback Assignment and Bounded Replay

## Problem

Scheduler retries can evaluate the same candidate more than once. A candidate may
pass while fresh and later fail the same TTL gate after it expires. Counting every
row places one outcome in both pass and reject cohorts, violating cohort
independence and exaggerating sample size.

## Causal assignment

Feedback uses one assignment per:

`filter_name + stage + filter_version + outcome_candidate_key`

The earliest persisted pass/reject row is the candidate's **first exposure**.
Later retries remain append-only evidence for troubleshooting, but do not
contribute another outcome to feedback statistics. A requested report window is
applied after ranking all retained rows; otherwise a pass immediately before the
window could be forgotten and a later rejection incorrectly promoted to first
exposure.

Controller output reports:

- candidate trajectories considered;
- later retry rows excluded from outcome cohorts;
- candidates whose later decision differed from first exposure.

## Replay contract

`filter_replay.py` is a read-only framework for supported monotonic threshold
gates. The first adapter is `family_ttl`.

Every replay is fixed to an exact filter stage, version, scanner family, direction,
market type, horizon, and evidence window. It never combines stages, spot/perp,
long/short, versions, or horizons.

For a rejected TTL candidate:

`required_ttl = persisted_ttl + max(0, evaluated_now - persisted_expires_at)`

An alternative admits only candidates whose required TTL is at or below that
alternative. Results report newly admitted candidates, settled net-result
coverage, raw net mean/median, and a conservative mean shrunk toward the current
pass cohort (`k=20`). Alternative groups are nested and observational.

Bounds:

- at most 4 alternative values per replay;
- at most 5,000 current-pass and 5,000 rejected candidates;
- at most 8 suspect cohorts attached to one controller report;
- alternative TTL values capped at 24 hours;
- default sample gate 20 and outcome coverage gate 70%;
- experiment readiness requires both a 5bp positive shrunk net result and a 5bp
  improvement over the current pass cohort.

## Interpretation limits

This is a **gate-only** replay. It does not pretend to replay later ranking,
portfolio conflicts, cooldown, risk, execution, exchange availability, or market
impact. `ready_for_controlled_experiment` permits a separately designed shadow
experiment; it never changes configuration. `insufficient_data` and `no_support`
mean the current threshold remains unchanged.
