# Task 5 Bounded Twin Evidence and Replay Review

> Date: 2026-08-08
>
> Verdict: PASS for bounded diagnostic persistence and replay only
>
> Authority scorer: `universe-step-v1`
>
> Runtime/Fedora validation: pending

## 1. Accepted scope

Task 5 retains a sampled, replayable comparison of step authority against the
continuous point and pessimistic paths. Both continuous paths replay the current
fine filter, preferred-market deduplication, family quotas, regime context, and
allocator mechanics. The change does not switch the scorer, alter a quota, emit
a continuous Opportunity, create a Learning treatment, or touch Risk/Execution
authority.

The production capture is deliberately bounded to one scanner run per 55
minutes. Within that run, every coarse-filter survivor is retained so ranks and
selected/dropped comparisons remain internally coherent.

## 2. Rejected first draft

The first persistence draft was not accepted. Direct measurement found:

- about 23,537 bytes of record JSON per representative candidate;
- four fixed horizons per direction, producing four to eight shared outcome
  rows per candidate;
- at 240 candidates on a 10-minute refresh, about 24 GiB of record body over 30
  days, before SQLite/index overhead;
- roughly 138,000 or more additional outcome rows per day, plus settlement load.

That design would have reintroduced the database-growth and memory-pressure
risk this hardening cycle is removing. It also mixed diagnostic scorer evidence
into Learning's shared outcome table before a primary horizon, prospective
assignment, or causal contract had been approved. The original draft is
therefore explicitly rejected for merge or deployment.

## 3. Corrections in the accepted design

The bounded design applies all of the following:

1. Capture cadence is 55 minutes. A service precheck avoids unnecessary bundle
   construction and the repository repeats the check inside `BEGIN IMMEDIATE`.
2. `filter_candidate_outcomes` and all fixed-horizon outcome construction are
   absent from the Twin persistence path.
3. Full dataclass serialization is replaced with compact score/rank/selection,
   threshold, missingness, flag, group, and top-eight sensitivity evidence.
4. Replay inputs remain source-allowlisted and sufficient to regenerate the
   pure scorer and allocator comparison.
5. Batch and candidate hashes are canonical; equal-score input order is frozen
   by contiguous `candidate_index`.
6. Identical retries are idempotent; identity reuse with different content
   fails closed.

The representative candidate now uses 2,442 bytes of compact output and 844
bytes of replay input. At 240 candidates, 24 daily samples, and 30 days, the
payload projection is 567,820,800 bytes before SQLite/index overhead. Automated
guards cap output at 4 KiB, input at 2 KiB, and that projection at 600 MB.

## 4. Experimental facts remain unavailable

| Identity | Value | Reason |
| --- | --- | --- |
| policy epoch | `NULL` | no prospective continuous policy epoch |
| availability profile | `NULL` | no frozen prospective availability cohort |
| exact observation lineage | unavailable blocker | feature rows lack complete provider lineage |
| assignment arm | not collected | no randomized/admissible assignment |
| propensity | not collected | no assignment or overlap probability |
| fixed-horizon outcome | not collected | no approved primary horizon/outcome contract |
| authority applied | `false` | step scorer remains sole authority |
| promotion eligible | `false` | diagnostic evidence cannot self-promote |

This prevents retrospective return differences from being mislabeled as causal
Learning evidence or feeding a self-reinforcing score loop.

## 5. Random exploration evidence boundary

The runtime captures the process-global allocator random state immediately
before step allocation and uses it for Shadow replay. This is useful for
integrity comparison, but it is not a prospective randomization contract.
Concurrent global-random consumers could still advance state between capture
and allocation. Such drift must surface as a replay mismatch; it cannot be
silently relabeled as exact causal selection.

A later experiment design should separate deterministic core ranking from
exploration treatment and own a dedicated random source. That work is outside
Task 5 and requires owner approval.

## 6. Current verification

| Check | Result |
| --- | --- |
| Persistence and migration focused suite | `28 passed` |
| Ruff on touched runtime/storage/tests | passed |
| Representative output/input | `2,442 B / 844 B` |
| 30-day 240-candidate payload projection | `567,820,800 B` |
| Outcome rows written by Twin | `0` |
| Fedora focused validation | pending |
| Runtime deployment | not performed |

These checks prove only the focused code contract. Wider focused regression,
Semgrep, documentation governance, Fedora validation, and runtime observation
remain required before the dependent PR can be ready.

## 7. Stop gate

Task 6 has not started. Opening a policy epoch, assigning arms, choosing a
primary outcome horizon, estimating causal contribution, or allowing factor
Learning requires a separate plan and explicit owner approval. This Task-5 PASS
is not evidence of positive runtime effect or profitability.
