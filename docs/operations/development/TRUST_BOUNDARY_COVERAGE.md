# Trust-boundary coverage

## Why not a repo-wide %

There is deliberately no global line-coverage gate. A single number for the
whole tree would over-count boilerplate (schemas, dataclasses, CLI wiring)
and under-weight the places that actually decide something. Whole-repo 90%
would be a claim the tests do not support, and would make new modules feel
"covered" the moment they are imported by any adjacent test.

We gate the paths where a wrong answer misleads a decision, corrupts state,
or bypasses a check — nothing else.

## What is gated

`.coveragerc` scopes measurement to eight files:

| Boundary | File |
|---|---|
| Profit Gradient | `aletheia/analytics/profit_gradient.py` |
| Profit Gradient estimators | `aletheia/analytics/profit_gradient_estimators.py` |
| Profit Gradient evidence | `aletheia/analytics/profit_gradient_evidence.py` |
| Policy epoch | `aletheia/learning/policy_epochs.py` |
| Evidence epoch / cutoff | `aletheia/learning/evidence_epoch.py` |
| Context-artifact compiler | `aletheia/economics/context_artifacts.py` |
| Position replay | `aletheia/analytics/position_replay.py` |
| Filter replay | `aletheia/counterfactual/filter_replay.py` |
| Cost / order reconciliation | `aletheia/execution/reconciliation.py` |

Measurement is **branch coverage on top of line coverage** — the same
combined percentage `pytest-cov` prints in its terminal `Cover` column.
Branch coverage is required because the bugs we care about (a fall-through
`elif`, an unhandled `None`, an epoch-mismatch that returns the wrong
window) usually hide behind an untaken branch, not an unexecuted line.

Per-module floors live in `scripts/coverage_floors.py`. They match today's
measured values. A PR that pushes any module below its floor fails CI. A
PR that raises measured coverage may (optionally, in a follow-up commit)
ratchet its floor upward.

There is no repo-wide `fail-under` and no PR-only "changed lines" gate.
Ratcheting floors already cover both: any regression on a trust module
fails; a new file that touches these boundaries must be added to the
`include=` list explicitly, so it cannot slip in unmeasured.

## How the gate runs

The `review` job in `.github/workflows/ci.yml` runs `pytest --cov` against
the tests that exercise the boundaries, then:

1. `scripts/coverage_floors.py --selftest` (hermetic; asserts the
   percentage math and regression detection).
2. `scripts/coverage_floors.py` (compares measured coverage to `FLOORS`).

Local reproduction:

```sh
uv run pytest \
  tests/test_profit_gradient.py \
  tests/test_profit_gradient_evidence.py \
  tests/test_p3_evidence_epoch.py \
  tests/test_filter_replay.py \
  tests/test_position_replay.py \
  tests/test_reconciliation.py \
  tests/test_oco_reconcile.py \
  tests/test_cr_ex01_reconcile.py \
  tests/test_position_reconciler.py \
  tests/test_scheduler_reconcile.py \
  tests/test_reconciliation_integrity_incident.py \
  tests/test_context_effectiveness.py \
  --cov --cov-report=term-missing:skip-covered -q
uv run python scripts/coverage_floors.py
```

## Frozen baseline (2025-11, post property tests)

| Module | Combined line+branch |
|---|---|
| `aletheia/analytics/profit_gradient_estimators.py` | 97.6 % |
| `aletheia/analytics/profit_gradient.py` | 92.3 % |
| `aletheia/learning/evidence_epoch.py` | 88.5 % |
| `aletheia/learning/policy_epochs.py` | 85.3 % |
| `aletheia/analytics/position_replay.py` | 76.3 % |
| `aletheia/economics/context_artifacts.py` | 61.2 % |
| `aletheia/counterfactual/filter_replay.py` | 57.8 % |
| `aletheia/analytics/profit_gradient_evidence.py` | 53.4 % |
| `aletheia/execution/reconciliation.py` | 31.5 % |

Floors are set 2-15 points below measured, so ordinary refactors do not
false-positive. Bring a module up by adding **invariant** tests, not by
padding the line count.

## Invariant tests (implemented)

Line/branch coverage says a branch was taken. It does not say the answer
was right. Each trust module has property tests that state, in
Hypothesis form, the invariants a wrong answer would violate:

| Module | Property test file | Invariants locked |
|---|---|---|
| `execution/reconciliation.py` | `tests/test_reconciliation_properties.py` | Fill-quantity conservation (permutation, split, malformed input); CR-EX-01 no-look-ahead (adding an open id only shrinks the resolved set); every resolved id backed by a qualifying order |
| `analytics/profit_gradient_evidence.py` | `tests/test_profit_gradient_evidence_properties.py` | Epoch-hash determinism; `code_commit` insensitivity; salt on kind/paths/safety; `_hash` canonicalisation across dict order, sets, NaN; `_redact` scrubs secrets at every depth; `_parse_utc` fails closed on naive/malformed input |
| `counterfactual/filter_replay.py` | `tests/test_filter_replay_properties.py` | `_bounded_alternatives` monotonicity and cap; `_bootstrap_delta_lcb` determinism (seed threads through, no global RNG leak); `_outcome_metrics` permutation invariance; `_shrunk_mean` boundary behaviour |
| `learning/policy_epochs.py` | `tests/test_policy_epoch_properties.py` | Epoch-hash order-independence; secret-fragment stripping; top-level `code_commit` stripping (nested preserved); `compare_policy_epochs` decision branches |
| `economics/context_artifacts.py` | `tests/test_context_artifacts_properties.py` | `_bounded_text` cap and idempotence; `_metadata` canonical JSON (key-order independent); recorder never raises on poisoned input; kind/freshness allow-lists locked |

Each property test was validated by mutation: a plausible bug in the
corresponding module makes at least one property fail.

### Known residual gap (out of scope for the coverage PR)

`_SECRET_FRAGMENTS` in both `analytics/profit_gradient_evidence.py` and
`learning/policy_epochs.py` uses underscore-separated fragments
(`api_key`, `private_key`). The lowercase match does not catch
camelCase keys like `ApiKey` or `PrivateKey`; those leak into the audit
trail today. Bare fragments (`token`, `secret`, `credential`,
`passphrase`) match as substrings and DO catch camelCase forms. See
`test_redact_camelcase_gap_is_documented` — the test locks the current
gap so a fix will trip it and force an intentional update in both
files.

### When to add more invariants

Add a property test when the corresponding trust module is being
touched anyway. Do not bulk-write invariants purely to lift the
coverage number — the ratcheting floor already blocks regressions, and
invariants that are not violated in practice add maintenance noise.

## Adding or removing a boundary

- **Adding.** Add the file path to `.coveragerc` `include=` and to
  `FLOORS` in `scripts/coverage_floors.py`. Set the floor to the current
  measured value minus 2 – 5 points. Add the test file(s) that exercise
  it to the `pytest` invocation in `.github/workflows/ci.yml` if not
  already covered by the transitive imports there.
- **Removing.** Only if the module is genuinely no longer a trust
  boundary (e.g. it became a pure schema / DTO). Document why in the PR.
