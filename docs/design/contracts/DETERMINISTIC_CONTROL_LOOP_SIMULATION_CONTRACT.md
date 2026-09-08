# Deterministic Control-Loop Simulation Contract

## Purpose

The DCLS MVP verifies the Python System Operator as a closed control loop. It uses real configuration loading and saving, real SQLite migrations and repositories, real assessment, decision, dispatch, handlers, audit persistence, downstream candidate filtering, and restart behavior.

It is not a market simulator and cannot place orders. Network access, credentials,
wall-clock sleeps, Risk, Exchange, and Live paths are outside this boundary.

**Contract Delta (2026-09-07, D1).** Execution was previously listed alongside them.
The simulator's synthetic entry now goes through `execution.paper_broker` and
`execution.entry_plan_execution` — the local paper fill model and its receipt type —
so the real broker owns depth walking and fill evidence, which the previous
`portfolio.paper_account` seam did not produce. Those two modules, and only those
two, are inside the boundary.

The reason this is not a weakening: it was measured. The transitive
`aletheia`-import closure of `portfolio.paper_account` and of
`execution.paper_broker` are **both 359 modules**, and both reach 5
`aletheia.exchanges` modules, `aletheia.config.secrets`, 5 `aletheia.risk` modules
and 13 `aletheia.gateway` modules. The runner's own closure is 366 and has always
reached the same set. So the module-prefix rule never enforced unreachability and
could not — the package is too densely coupled for any seam here to be isolated by
import graph. What it enforces, and what DCLS-S01 still asserts, is **direct-import
hygiene in the simulator's own source**: `runner.py` must not name Risk, Exchange,
Live or secrets, so no author can casually add a venue broker, a credential read, or
a Risk evaluation to the loop.

Determinism and offline behaviour are guaranteed by `VirtualClock` and the
artifact/replay contracts below, not by the import rule. Do not read DCLS-S01 as a
reachability guarantee.

## Controlled boundaries

- Time is supplied by `VirtualClock`; advancing time never sleeps.
- Signal-health input is the only replaceable external observation boundary.
- Each run owns a temporary data directory, migrated SQLite database, and config file.
- Operator decision, classification, dispatch, handlers, repositories, migrations, and candidate filtering are production implementations.
- Safe snapshots contain only runtime pause/kill state, paper permission/mode, Operator enablement, confidence, stop loss, and demo sizing. They never contain providers, tokens, keys, or credentials.

## Required operations

The state machine can change signal quality, change verified trade performance, advance time, seed candidates, run an Operator cycle, record an outcome, restart storage/config state, and record a canonical result.

## Independent invariants

1. Tunable values stay within declared bounds.
2. One cycle cannot emit opposing confidence or sizing actions.
3. Automatic confidence and sizing controls cannot become unconsumed proposals.
4. A tightened confidence gate must recover after a four-hour fill drought or leave a durable visible blocker.
5. Every executed configuration action has durable audit evidence.
6. The same initial state and operation sequence produce the same canonical final state.

These checks live outside `operator_loop.py`; tests must not copy its decision conditions.

## Fixed incidents and mutation canaries

`confidence_deadlock_20260713.yaml` and `sizing_ratchet_20260713.yaml` are permanent regression scenarios. Six mutation canaries alter classification, decision, dispatch, handler behavior, bounds, and audit persistence one at a time. A canary passes only when an independent invariant rejects the mutation.

## Failure evidence

Failures write under `test-results/control-loop/<run-id>/`: run metadata, seed, initial/final safe config, full and minimized operation sequences, timeline JSONL, migrated SQLite database, invariant failure, and an exact reproduction command. CI uploads `test-results/` even when a test fails.

## CI budgets

Pull requests use a small deterministic state-machine budget plus both fixed incidents, mutation canaries, executable Operator constraints, runtime replay, and the active Golden Path. The scheduled nightly workflow raises the state-machine example and step counts. Neither mode uses network access or real sleeps.
