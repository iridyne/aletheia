# Task 4 Step-Authoritative Twin Shadow Wiring Review

> Date: 2026-08-07
>
> Verdict: PASS after 2026-08-08 adversarial corrections, for task 4 code wiring only
>
> Authority scorer: `universe-step-v1`
>
> Diagnostic scorer: `universe-continuous-shadow-v1`
>
> Twin record: `universe-twin-shadow-record-v1`
>
> Runtime deployment: not performed

## 1. Implemented boundary

The production Universe refresh now computes one in-memory twin record for
every candidate that survives the coarse hard tradability filters. The insertion
point is after the existing scanner and allocator total-score calculation and
before fine filtering and allocation.

The existing collections remain separate:

```text
coarse-filtered feature
  -> step scanner -> step total -> pre_scored -> fine filter -> allocator
  -> copied TwinShadowInput -> background diagnostic batch -> local summary log
```

Only `pre_scored`, `unique_scored`, and `allocated` reach fingerprinting,
Universe member publication, Opportunity emission, full-arm Learning outcome
registration, Risk, or Execution. Twin records never enter those collections.
They are not written to SQLite, returned by an API, or retained after the
refresh completes.

## 2. Authority invariants

Every twin record states:

- authority behavior is `universe-step-v1`;
- continuous behavior is `universe-continuous-shadow-v1`;
- `shadow_authority_applied=false`;
- `promotion_eligible=false`;
- missing future identities and `task4_diagnostic_only` are explicit blockers.

The record retains the authoritative family, direction, opportunity score,
total score, coarse rank, fine-filter result, and final allocator-selected fact.
It computes continuous point and pessimistic scores, their families and
directions, coarse rank deltas, missingness, raw-feature sensitivities, group
concentration, influence-budget flags, and crossings of the frozen `20`, `45`,
and `60` diagnostic thresholds.

It deliberately does not run a continuous allocator. Continuous selected/dropped
facts therefore remain `not available`, represented as `None`, rather than being
inferred from score rank. Quotas, regime allocation, random exploration, live
price eligibility, and deduplication make rank alone insufficient to reconstruct
selection.

## 3. Concurrent policy isolation

The authoritative allocator now exposes a write-only audit output containing
the multiplier actually realized by the current Learning decision and operator
scanner weight. Supplying this audit object does not alter the returned score.

The Twin applies the same realized external opportunity multiplier to the
continuous arm and records it separately. This avoids attributing an existing
calibration or operator-weight difference to curve continuity. It does not open
a second Learning exposure, calculate a new treatment assignment, or give the
continuous family its own adaptive multiplier.

If the authority audit contract or multiplier is unavailable or invalid, the
Twin defaults to `1.0`, records an explicit blocker, and remains ineligible for
promotion. The step result is unchanged.

## 4. Raw evidence completion

The already-computed Bollinger squeeze degree is now exposed transiently as:

```text
bb_squeeze_ratio = current_width / historical_average_width
```

The existing Boolean remains exactly `current_width < 0.6 × average_width`.
The ratio is added only to the in-memory feature dictionary. It is intentionally
absent from `UniverseFeatureSnapshot`, migrations, and repository writes in
task 4.

Raw-feature declarations and factor evidence now carry source-controlled units,
neutral values, consumer ids, and frozen influence budgets. A joint-neutral
interaction residual is recorded only when the complete comparison remains
valid. Neutralizing all directional evidence can make the candidate direction
undefined; that condition is `interaction_residual_unavailable`, not a false
zero, and is a promotion blocker.

## 5. Failure and performance behavior

Per-candidate continuous exceptions are converted into a content-free invalid
reason containing only the exception type. Policy construction, the entire
batch, authority-selection annotation, and summary construction are wrapped by
non-fatal boundaries. The batch has a 10-second diagnostic timeout. Dynamic
tests force each boundary to fail and verify the authority refresh receives an
empty diagnostic result instead of failing.

`asyncio` cancellation cannot forcibly terminate a Python worker thread. A
timed-out pure batch may finish in the background, but its result is discarded.
The timeout bounds refresh waiting; it is not proof against a hypothetical
infinite-loop defect or repeated worker accumulation.

The batch runs through `asyncio.to_thread`, so the pure CPU work does not block
the async refresh event loop. A local diagnostic benchmark over 200 complete
candidates took 1.54 seconds, approximately 7.70 ms per candidate. This is a
development-machine measurement, not a Fedora runtime latency or capacity
claim.

Only bounded aggregate counts are logged once per refresh: valid/invalid,
family/direction changes, threshold crossings, missingness, concentration,
budget excess, unavailable residuals, and authoritative selection count. Raw
features and per-candidate values are not logged.

## 6. Verification and adversarial review

The focused task-4 review covers:

- audit-output score equality and realized multiplier capture;
- external multiplier equality across step and continuous arms;
- stable scanner-run, feature, asset, symbol, and market identities;
- raw evidence units, consumers, neutral values, budgets, and sensitivities;
- deterministic rank deltas and threshold crossing semantics;
- authoritative selection annotation without a continuous selection claim;
- no-chase hard-gate blocker preservation;
- transient squeeze ratio with unchanged Boolean behavior;
- no persistence, Learning, allocator, emitter, Risk, or Execution import from
  the Twin module;
- AST proof that authority consumers never receive a Twin collection;
- per-candidate and whole-batch failure isolation;
- input non-mutation;
- unchanged frozen step score and existing Universe pipeline regressions.

Passing these checks proves code isolation and deterministic diagnostic
behavior. It does not prove a running instance, durable evidence, rank
improvement, profitable contribution, safe Paper promotion, or safe factor
Learning.

Final local verification evidence:

| Check | Result |
| --- | --- |
| Task-4 Twin plus pure continuous scorer suites | `84 passed` |
| Focused Universe pipeline, route, and frozen step-score regression set | `298 passed` |
| Ruff correctness/style and new-module complexity checks | passed |
| Semgrep Python rules on changed Universe modules | passed, zero findings |
| Python bytecode compilation | passed |
| AST authority-consumer isolation audit | passed |
| Documentation governance | passed |

These are focused macOS checks, not a full local pytest run and not Fedora
runtime evidence.

### 2026-08-08 adversarial correction

The first implementation review overstated failure isolation. It protected the
background batch, but policy construction and post-batch annotation/summary
could still raise inside the authoritative refresh. Those paths now fail
non-fatally and have direct failure-injection tests.

The first review also reported only summed per-feature local sensitivity at the
evidence-group level. The pure scorer now records an explicit group object with
the jointly neutralized group delta and validity state. The global interaction
residual compares the all-group joint-neutral delta with those group-level
deltas; an invalid joint comparison remains unavailable rather than zero.

The diagnostic `20`, `45`, and `60` thresholds remain locally declared to avoid
an authority-module dependency, but a regression test locks them to the current
allocator and emitter constants so later authority drift cannot silently make
the Twin comparison stale.

The accepted Proposal's continuous-arm selected/dropped comparison is not part
of task 4. Leaving its fields as `None` is semantically honest but does not
satisfy that later full-record requirement. Task 5 must compute and persist the
isolated continuous allocation replay before the record can be described as
complete.

## 7. Residual gate

Task 5 may add durable full-candidate records only after separate approval. It
must define migrations, retention, write-transaction boundaries, replay, source
fingerprint, availability profile, policy/behavior identity, outcome/cost/metric
versions, assignment facts, and explicit handling of records whose interaction
residual is unavailable.

Until that gate passes, the local summary is operational visibility only. It
cannot be pooled as an experiment, shown as calibrated confidence, or used to
change allocation, Opportunity emission, Paper, Risk, Execution, or Learning.

Task 5 subsequently passed its separate diagnostic persistence/replay review on
2026-08-08. See
[`07_task5_persistence_replay_review_2026-08-08.md`](07_task5_persistence_replay_review_2026-08-08.md).
That later result does not retroactively change what Task 4 alone proved.
