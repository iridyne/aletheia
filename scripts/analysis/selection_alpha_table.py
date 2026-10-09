#!/usr/bin/env python3
"""Read-only selection-alpha table from ``filter_candidate_outcomes`` (FCO).

Answers: which (opportunity family, direction, regime, horizon) cells of the
USDT-perpetual product earn money net of the estimated costs, pooling every code
version (owner decision 2026-10-09: this evidence accumulates across versions,
so nothing here filters by ``producer_commit``, session or policy epoch).

* Rows: ``market_type IN ('perp','futures')``, ``status='settled'``,
  ``net_return_pct IS NOT NULL`` (opportunity_outcome_contract §8). FCO holds one
  row per ``outcome_candidate_key + horizon`` (§2), so no further dedup.
* Family: nearly every row carries ``scanner_family='candidate_selection_utility'``;
  the opportunity family is the applicable mechanism persisted on the matching
  ``universe_twin_shadow_records`` row. A key with several applicable mechanisms
  in one direction is counted once per mechanism, so cells are not disjoint.
* CI: day-cluster bootstrap of the mean (resample UTC entry days). Rows from one
  day share the market move; a row-level bootstrap would be falsely narrow. With
  fewer than ``MIN_CI_DAYS`` days the resample only spans the day means, so the
  CI is reported as n/a instead of as inference.
* win%: share of rows with ``net_return_pct > min_tradable_edge_pct``.

The database is opened ``mode=ro`` with ``query_only``; this script never writes.

Usage:
  uv run python scripts/analysis/selection_alpha_table.py [--db PATH] [--top-symbols 15]
  ssh u7p 'cd ~/aletheia && uv run --no-sync python - --db /home/admin/.aletheia/app.db' \
      < scripts/analysis/selection_alpha_table.py
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROWS_SQL = """
WITH mech AS (
  SELECT DISTINCT k.value AS outcome_candidate_key,
         json_extract(m.value, '$.mechanism_id') AS mechanism_id
  FROM universe_twin_shadow_records r,
       json_each(r.compact_output_json, '$.candidate_selection.opportunity_evidence.mechanisms') m,
       json_each(r.compact_output_json, '$.outcome_candidate_keys') k
  WHERE json_extract(m.value, '$.applicability') = 'applicable'
    AND k.key = json_extract(m.value, '$.direction')
)
SELECT COALESCE(mech.mechanism_id,
                CASE o.scanner_family WHEN 'candidate_selection_utility'
                     THEN 'csu_unlabeled' ELSE o.scanner_family END) AS family,
       o.outcome_candidate_key, o.direction, COALESCE(o.regime_tag, 'null') AS regime,
       o.horizon, o.symbol,
       substr(o.entry_ts, 1, 10) AS day, o.net_return_pct, o.min_tradable_edge_pct,
       o.mfe_pct, o.mae_pct, COALESCE(o.allocation_selected, 0) AS selected
FROM filter_candidate_outcomes o
LEFT JOIN mech ON mech.outcome_candidate_key = o.outcome_candidate_key
WHERE o.market_type IN ('perp', 'futures')
  AND o.status = 'settled'
  AND o.net_return_pct IS NOT NULL
"""

MIN_CI_DAYS = 5

COVERAGE_SQL = """
SELECT horizon, status, net_return_pct IS NOT NULL AS has_net,
       COALESCE(allocation_selected, 0) AS selected, COUNT(*) AS n
FROM filter_candidate_outcomes
WHERE market_type IN ('perp', 'futures')
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4
"""

PROVENANCE_SQL = """
SELECT COUNT(*), COUNT(DISTINCT producer_commit), MIN(entry_ts), MAX(entry_ts),
       MAX(settled_at), GROUP_CONCAT(DISTINCT cost_model_version)
FROM filter_candidate_outcomes
WHERE market_type IN ('perp', 'futures') AND net_return_pct IS NOT NULL
"""


def day_cluster_ci(rows: list[dict], *, draws: int, rng: random.Random) -> tuple[float, float] | None:
    by_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for row in rows:
        acc = by_day[row["day"]]
        acc[0] += row["net_return_pct"]
        acc[1] += 1
    days = list(by_day.values())
    if len(days) < MIN_CI_DAYS:
        return None
    means = []
    for _ in range(draws):
        sample = rng.choices(days, k=len(days))
        means.append(sum(s for s, _ in sample) / sum(n for _, n in sample))
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def summarize(rows: list[dict], *, draws: int, rng: random.Random) -> dict:
    nets = [row["net_return_pct"] for row in rows]
    mfe = [row["mfe_pct"] for row in rows if row["mfe_pct"] is not None]
    mae = [row["mae_pct"] for row in rows if row["mae_pct"] is not None]
    return {
        "n": len(rows),
        "days": len({row["day"] for row in rows}),
        "mean": statistics.fmean(nets),
        "median": statistics.median(nets),
        "ci": day_cluster_ci(rows, draws=draws, rng=rng),
        "win": sum(row["net_return_pct"] > row["min_tradable_edge_pct"] for row in rows) / len(rows),
        "mfe": statistics.fmean(mfe) if mfe else None,
        "mae": statistics.fmean(mae) if mae else None,
        "sel": sum(row["selected"] for row in rows) / len(rows),
    }


def _f(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def table(groups: dict[tuple, list[dict]], key_names: list[str], *, draws: int, rng: random.Random,
          min_n: int) -> tuple[str, list[str]]:
    head = [*key_names, "n", "days", "mean%", "median%", "CI95 low", "CI95 high", "win%", "MFE%", "MAE%", "sel%"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    positive = []
    for key, rows in sorted(groups.items(), key=lambda item: -len(item[1])):
        if len(rows) < min_n:
            continue
        s = summarize(rows, draws=draws, rng=rng)
        low, high = s["ci"] or (None, None)
        if low is not None and low > 0:
            positive.append(" / ".join(map(str, key)))
        lines.append("| " + " | ".join([*map(str, key), str(s["n"]), str(s["days"]), _f(s["mean"]),
                                        _f(s["median"]), _f(low), _f(high), f"{s['win'] * 100:.1f}",
                                        _f(s["mfe"], 2), _f(s["mae"], 2), f"{s['sel'] * 100:.0f}"]) + " |")
    return "\n".join(lines), positive


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only selection-alpha table (filter_candidate_outcomes, all versions)")
    default_home = Path(os.environ.get("ALETHEIA_DATA_DIR") or Path.home() / ".aletheia")
    parser.add_argument("--db", type=Path, default=default_home / "app.db")
    parser.add_argument("--top-symbols", type=int, default=15)
    parser.add_argument("--min-n", type=int, default=30, help="hide cells with fewer rows")
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args()

    conn = sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    rng = random.Random(args.seed)

    total, commits, first, last, settled, cost_models = conn.execute(PROVENANCE_SQL).fetchone()
    print(f"futures rows with net: {total}; producer_commits pooled: {commits}; "
          f"entry {first} .. {last}; latest settled {settled}; cost_model_version {cost_models}\n")
    print("Coverage (futures, all statuses):\n\n| horizon | status | has_net | selected | n |\n|---|---|---|---|---|")
    for row in conn.execute(COVERAGE_SQL):
        print("| " + " | ".join(str(v) for v in tuple(row)) + " |")

    rows = [dict(row) for row in conn.execute(ROWS_SQL)]
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        cells[(row["family"], row["direction"], row["regime"], row["horizon"])].append(row)
    text, positive = table(cells, ["family", "direction", "regime", "horizon"],
                           draws=args.draws, rng=rng, min_n=args.min_n)
    print(f"\nCells: family x direction x regime x horizon (n >= {args.min_n}; {len(rows)} labelled rows)\n")
    print(text)
    print("\nCells with CI95 lower bound > 0: " + ("; ".join(positive) if positive else "none"))

    # One row per outcome here: the symbol view must not count multi-mechanism keys twice.
    four_hour = list({row["outcome_candidate_key"]: row for row in rows if row["horizon"] == "4h"}.values())
    counts: dict[str, int] = defaultdict(int)
    for row in four_hour:
        counts[row["symbol"]] += 1
    top = set(sorted(counts, key=lambda symbol: -counts[symbol])[: args.top_symbols])
    per_symbol: dict[tuple, list[dict]] = defaultdict(list)
    for row in four_hour:
        if row["symbol"] in top:
            per_symbol[(row["symbol"], row["direction"])].append(row)
    text, positive = table(per_symbol, ["symbol", "direction"], draws=args.draws, rng=rng, min_n=args.min_n)
    print(f"\nPer symbol: top {args.top_symbols} symbols by 4h count, symbol x direction, 4h, all families\n")
    print(text)
    print("\nSymbol cells with CI95 lower bound > 0: " + ("; ".join(positive) if positive else "none"))


if __name__ == "__main__":
    main()
