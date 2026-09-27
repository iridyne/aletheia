# tier_unit reclassification — 2026-09-27

Phase 1 of [#3562](https://github.com/iridyne/aletheia/issues/3562). Read-only
profiling and classification; **no test was moved, renamed or deleted by this
report**.

## 1. How `tier_unit` is assigned (the actual cause of the drift)

`tests/conftest.py::_classify_test_tier` assigns a tier from the **filename**:

```
e2e  ← tests/e2e/, *_e2e.py, *_e2e
spec ← tests/specs/, spec_*.py
integration ← filename contains one of 19 tokens (routes, storage, scheduler, …)
unit ← everything else
```

So `tier_unit` is not a claim about the test's content; it is the **residue** of
a filename filter. `fast` is then `tier_unit AND not slow`, i.e. "the residue
minus a hand-maintained path list".

`tests/test_per_symbol_cap_derivation.py` (229 tests, zero boundary imports) is
genuinely unit: **2.2 ms/test**. `tier_unit` averages **40 ms/test**. The marker
has not drifted from a stated meaning — it never had a content-based meaning.

## 2. Baseline (measured on this workstation, macOS, serial)

```
uv run pytest tests -q -m tier_unit --timeout=90 --durations=0
8555 passed, 8 skipped, 2958 deselected, 21 warnings in 344.46s (0:05:44)
```

Wall-clock split: `call` 291.5 s / `setup` 34.3 s / `teardown` 0.1 s /
rows below the 5 ms report floor ≤ 101 s.

Concentration — the cost is **not** in a few outliers:

| threshold | tests | summed | share of wall |
|---|---:|---:|---:|
| call ≥ 1.0 s | 37 | 89.7 s | 26.0 % |
| call ≥ 0.5 s | 175 | 181.1 s | 52.6 % |
| call ≥ 0.2 s | 336 | 230.7 s | 67.0 % |
| call ≥ 0.05 s | 837 | 272.2 s | 79.0 % |

`tier_unit` is 8562 tests taking 344 s, of which 837 tests take 272 s. There is
no single hotspot to delete; the work is 119 files ≥ 0.4 s (293.5 s combined).

## 3. Static classification

For each of the **537** modules currently classified `tier_unit` by name, its
own top-level imports were parsed (no transitive closure — a test module that
itself imports `sqlite3`, `subprocess`, `httpx`, `fastapi`, or
`aletheia.storage|server|gateway|agent_runtime|execution|portfolio|uta|…`
cannot be a pure-function test).

| verdict | modules | basis |
|---|---:|---|
| `RECLASSIFY` | **271** | module's own imports cross a boundary |
| `SPEED_UP` | **266** | imports are clean; expense is repo-wide scanning |

Boundary signal frequency across the 271:

| signal | modules |
|---|---:|
| `aletheia.*` (storage / server / runtime / execution / portfolio / uta) | 168 |
| `sqlite3` | 96 |
| `subprocess` | 46 |
| `httpx` | 28 |
| `extensions.*` | 11 |
| `fastapi` / `starlette` | 9 |

Cross-check against the measurements: of the **30 slowest files**, 20 are
flagged `RECLASSIFY`. The 10 not flagged are repo-scanning tests (see § 5) —
which are genuinely unit in *kind* and expensive in *implementation*, exactly
the `SPEED_UP` bucket. The static signal and the timing agree.

## 4. Top of the table (files ≥ 0.4 s; full set = 119 files / 293.5 s)

`RECLASSIFY` = 191.4 s in 79 files. `SPEED_UP` = 102.1 s in 40 files.

| sec | tests | verdict | file | reason |
|---:|---:|---|---|---|
| 17.4 | 16 | SPEED_UP | `tests/test_architecture_fitness.py` | repo-wide AST scan per test |
| 12.3 | 69 | RECLASSIFY | `tests/test_update.py` | `sqlite3` |
| 10.5 | 20 | RECLASSIFY | `tests/test_advisory_agent_composition.py` | `subprocess` |
| 10.3 | 21 | RECLASSIFY | `tests/test_optional_family_cutover.py` | `subprocess` |
| 10.0 | 18 | SPEED_UP | `tests/test_candidate_selection_utility.py` | DB-backed setup, imports clean |
| 8.4 | 33 | SPEED_UP | `tests/test_planning_writer_boundary.py` | repo-wide AST scan |
| 8.2 | 17 | RECLASSIFY | `tests/test_data_compartment.py` | `subprocess` |
| 8.2 | 13 | RECLASSIFY | `tests/test_telegram_route_boundary.py` | `subprocess` |
| 7.5 | 17 | RECLASSIFY | `tests/test_regime_boundary.py` | `subprocess` |
| 7.2 | 7 | SPEED_UP | `tests/test_documentation_truth.py` | scans every markdown file |
| 6.6 | 132 | RECLASSIFY | `tests/test_exchange_submission_integrity.py` | `aletheia`, `httpx`, `sqlite3` |
| 6.5 | 9 | RECLASSIFY | `tests/test_position_update_ownership.py` | `aletheia` |
| 6.4 | 127 | SPEED_UP | `tests/test_profit_gradient.py` | per-test DB fixture |
| 5.5 | 65 | SPEED_UP | `tests/test_universe_continuous_scoring.py` | hypothesis fuzz over scoring |
| 5.3 | 51 | RECLASSIFY | `tests/test_doctor.py` | `sqlite3` |
| 5.2 | 13 | RECLASSIFY | `tests/test_migration_fast_path.py` | `sqlite3` |
| 5.2 | 8 | RECLASSIFY | `tests/test_public_pricing_composition.py` | `subprocess` |
| 4.9 | 6 | RECLASSIFY | `tests/test_analytics_report_compartment.py` | `subprocess` |
| 4.4 | 8 | SPEED_UP | `tests/test_proposal_writer_boundary.py` | repo-wide AST scan |
| 4.3 | 16 | RECLASSIFY | `tests/test_bitget_adapter.py` | `aletheia`, `extensions` |
| 4.3 | 25 | RECLASSIFY | `tests/uta/test_import_boundaries.py` | `subprocess` |
| 3.9 | 5 | SPEED_UP | `tests/test_doctor_trading_diagnostics.py` | repo-wide AST scan |
| 3.8 | 9 | RECLASSIFY | `tests/test_entry_plan_execution.py` | `aletheia` |
| 3.8 | 21 | RECLASSIFY | `tests/test_connectivity.py` | `aletheia`, `sqlite3` |
| 3.8 | 2 | SPEED_UP | `tests/test_order_writer_boundary.py` | repo-wide AST scan |
| 3.5 | 11 | RECLASSIFY | `tests/test_market_listing_boundary.py` | `subprocess` |
| 3.1 | 93 | RECLASSIFY | `tests/test_entry_planner.py` | `aletheia`, `sqlite3` |
| 2.9 | 49 | RECLASSIFY | `tests/test_risk_checks_full.py` | `aletheia`, `sqlite3` |
| 2.9 | 6 | SPEED_UP | `tests/test_doctor_json.py` | repo-wide AST scan |

## 5. Correction to the issue's plan

> "PR A alone should get us most of the way to 60s."

It does not, and the arithmetic is now measurable: reclassifying every boundary
module removes **209 s of the 344 s**, leaving ≈ **115 s**. Frames, setup and
the sub-5 ms floor do not disappear with it. `SPEED_UP` work is required in the
same change, not after it.

Projection with both buckets applied:

| step | remaining |
|---|---:|
| baseline | 344 s |
| − `RECLASSIFY` (271 modules, 209 s) | ≈ 115 s |
| − `SPEED_UP` on the 30 repo-scanning files (96.8 s → ≈ 25 s) | ≈ 65 s |

60 s is reachable, but only as `RECLASSIFY` **and** `SPEED_UP` together, and the
margin is thin — the remaining ~140 clean files are the floor.

## 6. Measured `SPEED_UP` result already landed

`tests/authority_writer_checks.py::assert_writer_boundary` re-parses the whole
tree (~1.5 k files) on **every call**, and the writer-boundary tests call it
once per owned table across four modules. `_parse_cached` memoises
`ast.parse` on `(path, mtime_ns, size)` — no assertion changes.

A/B, same conditions, four files (`planning_writer_boundary`,
`proposal_writer_boundary`, `order_writer_boundary`, `architecture_fitness`):

| | wall | pytest |
|---|---:|---|
| before | 35.47 s | 429 passed in 31.89 s |
| after | 30.20 s | 429 passed in 25.98 s |

−5.9 s (−19 %) on those four files, and the saving repeats for every additional
`assert_writer_boundary` caller.

## 7. Decision at the time of the initial audit (historical)

Reclassifying 271 of 537 modules moves ~5500 tests out of `-m fast`, which is
the documented every-bug-fix-loop gate. That is a change to what the mandatory
local gate covers, not just a timing fix. Three options:

1. **Content-based classifier in `conftest.py`** — one file; `fast` becomes
   "modules whose own imports cross no boundary". Matches the marker's stated
   meaning ("cheap local tests suitable for every bug-fix loop"). Largest
   reduction in gate coverage.
2. **Keep the name-based tier, add a separate `fast` allowlist** — the
   every-loop set stays explicit and reviewable; new test modules are opt-in.
3. **Only do `SPEED_UP`** — shrink `tier_unit` toward ~250 s with no membership
   change; the loop stays slow but nothing leaves the gate.

Nothing in this report has been applied to `tests/conftest.py`; option (1) and
(2) both need the owner's acceptance before the default gate changes.

## 8. Follow-up measurement — 2026-09-28

The content classifier is now present; the current campaign (#3438) authorizes
content-based classification. Filename integration rules still run before its
top-level-import check. The historical counts above are not current receipts.

At `77e143885`, a serial `uv run pytest tests -q -m tier_unit --timeout=90
--durations=30` run produced **3789 passed, 1 skipped, 7807 deselected**,
12 warnings, pytest 102.80 s, wall **107.64 s**. This does not meet 60 s.

An unchanged four-file selection (`test_planning_writer_boundary.py`,
`test_proposal_writer_boundary.py`, `test_order_writer_boundary.py`,
`test_architecture_fitness.py`) was measured before/after avoiding enclosing-owner
lookups for AST node kinds that have no writer/SQL checks in the final scan.
No assertion, marker, case, or scan directory changed.

| Selection | Before | After |
|---|---:|---:|
| Collected/passed | 429/429 | 429/429 |
| Pytest seconds | 23.98 | 19.30 |
| Wall seconds | 27.32 | 22.65 |

This is a same-selection **4.67 s** reduction, not a reclassification gain and
not the required three-run unit-tier acceptance. Existing non-owner/alias/SQL
negative cases remain in the selection. Further unit-tier work is still needed.
