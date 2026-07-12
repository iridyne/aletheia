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
