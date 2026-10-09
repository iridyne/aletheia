"""Selected-Candidate read-only projection over filter_candidate_outcomes.

Replaces universe_forward_outcomes as the source for Risk family-edge and
calibration statistics. All functions are pure reads; no writes, caching or
inference.

Public API:
  family_edge_stats       — per-(family, direction, market) stats for Risk edge
  calibration_cohort      — per-(family, direction, market, horizon) for allocator/advisor
  spread_tolerance_cohort — low/high spread split for filter_advisor
  calibration_lineage     — row fingerprinting for calibration auditability
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def family_edge_stats(
    horizon: str = "24h",
    within_days: int = 7,
    *,
    cost_model_version: str,
) -> list[dict]:
    """Per-(scanner_family, direction, market_type) cost-aware edge from FCO.

    Selected Candidates only (allocation_selected = 1). Exact semantics
    preserved from the retired universe_forward_outcomes query.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import complete_outcome_economics_sql, net_clears_tradable_edge_sql

    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    economics = complete_outcome_economics_sql()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_MARKET_SQL} AS market_type, "
        "cost_model_version, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_return, "
        f"SUM(CASE WHEN {net_clears_tradable_edge_sql()} "
        "  THEN 1 ELSE 0 END) AS win_count "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND horizon = ? "
        "AND settled_at >= ? "
        "AND cost_model_version = ? "
        "AND allocation_selected = 1 "
        f"AND {economics} "
        "AND scanner_family IS NOT NULL "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_MARKET_SQL}, cost_model_version "
        "ORDER BY n DESC",
        (horizon, cutoff, cost_model_version),
    ).fetchall()
    return [_enrich(dict(r)) for r in rows]


def calibration_cohort(
    *,
    cost_model_version: str,
    cutoff: str,
) -> list[dict]:
    """Per-(family, direction, market, horizon) stats from FCO full-arm rows.

    All eligible FCO rows (selected + unselected) settled after cutoff are
    included so the denominator is the full filter arm.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import complete_outcome_economics_sql, net_clears_tradable_edge_sql

    db = get_db()
    economics = complete_outcome_economics_sql()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_MARKET_SQL} AS market_type, horizon, "
        "cost_model_version, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_return, "
        f"SUM(CASE WHEN {net_clears_tradable_edge_sql()} "
        "  THEN 1 ELSE 0 END) AS win_count, "
        "MIN(net_return_pct) AS min_return, "
        "MAX(net_return_pct) AS max_return "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND cost_model_version = ? "
        f"AND {economics} "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_MARKET_SQL}, horizon, cost_model_version "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (cutoff, cost_model_version),
    ).fetchall()
    return [dict(r) for r in rows]


def spread_tolerance_cohort(
    *,
    cost_model_version: str,
    cutoff: str,
    settled_through: str | None = None,
) -> list[dict]:
    """Per-family spread sensitivity stats from FCO full-arm rows."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import complete_outcome_economics_sql

    db = get_db()
    economics = complete_outcome_economics_sql()
    settled_clause = "AND settled_at <= ? " if settled_through is not None else ""
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_MARKET_SQL} AS market_type, horizon, "
        "cost_model_version, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_net, "
        "MIN(net_return_pct) AS min_net, "
        "COUNT(CASE WHEN entry_spread_bps <= 15 THEN 1 END) AS n_low_spread, "
        "AVG(CASE WHEN entry_spread_bps <= 15 THEN net_return_pct END) AS avg_net_low_spread, "
        "COUNT(CASE WHEN entry_spread_bps > 15 THEN 1 END) AS n_high_spread, "
        "AVG(CASE WHEN entry_spread_bps > 15 THEN net_return_pct END) AS avg_net_high_spread "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND cost_model_version = ? "
        "AND scanner_family IS NOT NULL "
        "AND entry_spread_bps IS NOT NULL "
        f"AND {economics} "
        "AND direction IN ('long', 'short') "
        "AND entry_ts >= ? "
        f"{settled_clause}"
        f"GROUP BY scanner_family, direction, {_MARKET_SQL}, horizon, cost_model_version "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (
            (cost_model_version, cutoff, settled_through)
            if settled_through is not None
            else (cost_model_version, cutoff)
        ),
    ).fetchall()
    return [dict(r) for r in rows]


def calibration_lineage(
    *,
    cost_model_version: str,
    cutoff: str,
) -> dict[tuple, dict]:
    """Hash every exact row consumed by calibration_cohort (no data retained)."""
    import hashlib
    import json

    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import complete_outcome_economics_sql

    db = get_db()
    economics = complete_outcome_economics_sql()
    cursor = db.execute(
        "SELECT scanner_family, direction, "
        f"{_MARKET_SQL} AS market_type, horizon, id, "
        "outcome_candidate_key, symbol, venue, entry_ts, settled_at, net_return_pct, "
        "min_tradable_edge_pct, cost_model_version "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND entry_ts >= ? "
        "AND cost_model_version = ? "
        f"AND {economics} "
        "AND direction IN ('long', 'short') "
        f"ORDER BY scanner_family, direction, {_MARKET_SQL}, horizon, id",
        (cutoff, cost_model_version),
    )
    hashers: dict[tuple, object] = {}
    counts: dict[tuple, int] = {}
    group_rows: dict[tuple, list] = {}
    for raw in cursor:
        row = dict(raw)
        key = (
            row.pop("scanner_family"),
            row.pop("direction"),
            row.pop("market_type"),
            row.pop("horizon"),
        )
        symbol = row.pop("symbol", None) or ""
        venue = row.pop("venue", None) or ""
        entry_ts = row.get("entry_ts")
        hasher = hashers.setdefault(key, hashlib.sha256())
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        hasher.update(canonical.encode("utf-8"))
        hasher.update(b"\n")
        counts[key] = counts.get(key, 0) + 1
        if entry_ts:
            group_rows.setdefault(key, []).append((symbol, venue, entry_ts))
    result = {}
    for key, hasher in hashers.items():
        horizon = key[3]
        ind_n, dist_sym, dist_days = _horizon_separated_counts(group_rows.get(key, []), horizon)
        result[key] = {
            "fingerprint": hasher.hexdigest(),
            "count": counts[key],
            "independent_n": ind_n,
            "distinct_symbols": dist_sym,
            "distinct_days": dist_days,
        }
    return result


# ---------------------------------------------------------------------------
# Helpers (moved from repositories.py)
# ---------------------------------------------------------------------------

_MARKET_SQL = (
    "CASE WHEN market_type IN ('perp','futures','swap','usdt_futures') "
    "THEN 'perp' ELSE COALESCE(market_type, 'spot') END"
)


def _enrich(d: dict) -> dict:
    n = d.get("n") or 0
    d["win_rate"] = round((d.get("win_count") or 0) / n * 100, 1) if n else None
    if d.get("avg_return") is not None:
        d["avg_return"] = round(d["avg_return"], 3)
    return d


def _horizon_to_seconds(h: str) -> int:
    if h.endswith("m"):
        return int(h[:-1]) * 60
    if h.endswith("h"):
        return int(h[:-1]) * 3600
    if h.endswith("d"):
        return int(h[:-1]) * 86400
    raise ValueError(f"unknown horizon: {h!r}")


def _horizon_separated_counts(
    rows: list[tuple[str, str, str]],
    horizon: str,
) -> tuple[int, int, int]:
    """Greedy horizon-separated counts for (symbol, venue, entry_ts) rows."""
    try:
        horizon_sec = _horizon_to_seconds(horizon)
    except (ValueError, TypeError):
        horizon_sec = 0

    sorted_rows = sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    last_admitted: dict[tuple, float] = {}
    admitted_symbols: set[str] = set()
    admitted_days: set[str] = set()
    independent_n = 0

    for symbol, venue, entry_ts_str in sorted_rows:
        try:
            ts = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        sv = (symbol, venue)
        last = last_admitted.get(sv)
        if last is None or ts >= last + horizon_sec:
            last_admitted[sv] = ts
            independent_n += 1
            admitted_symbols.add(symbol)
            admitted_days.add(entry_ts_str[:10])

    return independent_n, len(admitted_symbols), len(admitted_days)
