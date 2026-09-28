# Runtime replay fixtures

`runtime_replay_v1.sqlite` and `runtime_replay_v1.expected.json` are immutable
historical fixtures for the retired local simulator. They preserve sanitized
market, decision, execution and Learning records; they are not current runtime
or external-account qualification evidence. Their identities, numeric values
and expected results must not be rewritten as exchange facts.

`test_runtime_replay_pack.py` checks historical readability, database integrity,
causal links and the frozen baseline. Its current outcome assessment admits zero
genuine outcomes from this pack. The simulator-specific generator is retired;
its provenance is recorded in [design history](../../docs/design/history/local_simulated_replay.md).

Current exchange execution checks use the existing UTA response fakes and
external entry/close pipeline tests. A new runtime qualification pack would need
its own version and provenance; this retirement does not manufacture one.

# Universe step-score golden baseline

`universe_step_score_v2.json` freezes the current piecewise/step behavior under
the scorer identity `universe-step-v2`. It contains two complementary evidence
sets:

- six sanitized numeric feature shapes derived from the Fedora Paper runtime;
  runtime identifiers, symbols, timestamps, prices, quantities, and free text
  are not copied;
- deterministic below/at/above probes for every numeric scoring boundary in the
  six scanner families, tradability score, late-entry penalty, final rank score,
  and the winner-level no-chase guard, plus categorical and missing-data cases.

The fixture is a measurement baseline only. It does not enable a new scorer,
change ranking authority, alter Learning behavior versions, or update the
Fedora runtime. `test_universe_score_golden.py` never regenerates expectations.

Do not edit this fixture merely to make a behavior change pass. A changed
scorer requires an explicitly reviewed new behavior version and either a new
versioned fixture or an approved, documented baseline migration. Preserve the
old fixture whenever old/new shadow comparison still needs it.

# Code, not data

This folder holds data only. Shared Python helpers — repository row builders,
seeds, seams and fakes — live in [`tests/helpers/`](../helpers); import them as
`tests.helpers.<module>`.
