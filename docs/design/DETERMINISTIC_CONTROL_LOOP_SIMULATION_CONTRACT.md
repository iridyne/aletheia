# Deterministic Control-Loop Simulation Contract

## Purpose

The DCLS MVP verifies the Python System Operator as a closed control loop. It uses real configuration loading and saving, real SQLite migrations and repositories, real assessment, decision, dispatch, handlers, audit persistence, downstream candidate filtering, and restart behavior.

It is not a market simulator and cannot place orders. Network access, credentials, wall-clock sleeps, Risk, Execution, Exchange, and Live paths are outside this boundary.

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
