# Runtime replay fixtures

`runtime_replay_v1.sqlite` is a fixed, sanitized replay pack for the active
crypto `paper_internal` mainline. Its category mix is admitted only after the
builder confirms those categories exist in a real runtime database. The builder
then replaces every source ID, symbol sequence, timestamp, price, quantity,
return, rationale, review, path, and free-text value with deterministic fixture
data. No runtime row or current machine fact is copied.

The committed pack includes market snapshots, Universe candidates,
opportunities, System Operator decisions, proposals, risk decisions, orders,
fills, positions, fixed-horizon outcomes, Learning exposures, and reviews.

Regenerate only when intentionally creating a new fixture version:

```bash
uv run python tests/fixtures/build_runtime_replay_v1.py \
  --source /path/to/runtime/data/app.db \
  --output tests/fixtures/runtime_replay_v1.sqlite
```

Ordinary mainline changes must replay the existing file. Any baseline change
requires an explicit task explanation, a versioned fixture or deliberate
baseline update, and matching assertions in `test_runtime_replay_pack.py`.

# Universe step-score golden baseline

`universe_step_score_v1.json` freezes the current piecewise/step behavior under
the scorer identity `universe-step-v1`. It contains two complementary evidence
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
