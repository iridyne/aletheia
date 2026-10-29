---
name: aletheia-master-red-triage
description: Diagnose and repair a master-wide Aletheia test regression caused by a merged commit that changed a production seam but not the test fixtures.
---

# Aletheia master-red triage

Use when a test fails on master and the failure "is not from my change" — especially a
cluster of failures with one repeated error code (e.g. `COMPILER_RISK_BUDGET_UNAVAILABLE`).
The real defect is usually a merged commit that changed a production seam while its
fixtures stayed stale; one fixture repair then clears dozens of tests.

## 1. Establish the baseline before touching anything

Run the whole suspect family, not just the reported test:

```sh
uv run pytest <file1> <file2> ... -q -p no:randomly --tb=no 2>&1 | grep '^FAILED' \
  | sed 's/::.*//' | sort | uniq -c | sort -rn
```

Counts per file tell you whether it is one root cause or several. `-p no:randomly` keeps
ordering stable across runs.

## 2. Prove pre-existence, never assume it

Stash only the production file you touched and re-run the single test:

```sh
git stash push -q -- path/to/production_file.py && uv run pytest <test> -q | tail -4; git stash pop -q
```

If it still fails, the failure is master's. Do **not** clear `.pytest_cache/lastfailed` to
get past `dev_check`'s previous-red step — that only masks it. Fix the cause; the cache
entry disappears by itself (verify with
`python3 -c "import json,pathlib;print(json.loads(pathlib.Path('.pytest_cache/v/cache/lastfailed').read_text()))"`).

## 3. Find the seam the merged commit moved

```sh
git log -1 --format='%h %ad %s' -L <start>,<end>:path/to/source.py   # who last touched these lines
git show <sha> -- path/to/source.py                                  # the full seam change
```

Read the new callee, then find what makes it unavailable in tests:

```sh
uv run python -c "from aletheia.config.loader import load_config; ..."   # probe real values
```

DB-backed caches (`get_unified_cached_state()` -> `settings` table) are always empty in a
fixture's temp `ALETHEIA_DATA_DIR`, so any seam reading them returns `None` and the caller
fails closed.

## 4. Repair the shared fixture, not each test

Find every fixture that builds the affected input:

```sh
grep -rn "<helper_name>(\|compile_entry_plan(" tests | grep -v '::'
```

Pin the new seam next to the existing pins, mirroring the file that already does it
(fixtures converge by copy, so the newest usage is the pattern):

```python
monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *a, **k: 5000.)
```

Comment **why** (which caller, and that no test data dir can supply it), or the next
reader deletes it as dead weight.

## 5. Separate the fixture repair from real production findings

A stale fixture and a production contradiction can share one symptom. Test whether the
*production* behaviour is self-inconsistent before touching money-path code:

- Zeros/clamps/config defaults may make two gates mutually exclusive (example: a tier
  leverage cap whose implied liquidation distance is below the policy's own buffer floor).
- If so, do **not** silently retune production, and do **not** weaken the spec's
  assertion. Give the spec an explicit, justified fixture policy so it can exercise its
  actual claim, and file the production contradiction as its own Issue with the numbers.

## 6. Verify at claim scope

```sh
uv run pytest <family files> -q -p no:randomly      # the repaired slice
uv run pytest tests -q -m fast -p no:randomly       # bounded repo-wide sweep (~1958 tests, ~46 s)
uv run python scripts/ci/dev_check.py --staged
uv run python scripts/ci/ci_local.py --pr-body-file <body> --base-ref origin/master
```

`ci_local` fails the `clean tracked worktree` check whenever the user has unrelated WIP;
stash it for the run and pop it immediately, or commit the WIP as its own PR.
