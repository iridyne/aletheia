"""Shadow trade read helpers for counterfactual analytics.

The write path (persist_shadow_batch, resolve_stale_shadows, resolve_shadow_trades_for_position)
was retired in #3704 after the sole production writer (paper_trader.py) was deleted in
commit 2e69baae9 (#2945). The physical shadow_trades table will be dropped in a separate
migration (#3701 Batch E).

Remaining surface: list_deduped_shadows and _detect_window_epochs, used by advisories,
calibration, propensity, and family_scorecard.  _detect_window_epochs raises on a failed
read (including a missing table) so callers report "unavailable"; list_deduped_shadows
logs and returns [].
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.counterfactual.trades")


def _shadow_time_window_sql(
    *,
    days: int | None,
    since_iso: str | None,
    until_iso: str | None,
) -> tuple[str, list[object]]:
    """Build the exact-instant predicate shared by shadow cohort readers."""
    from aletheia.storage.sqlite import iso_instant_us

    if (days is None) == (since_iso is None):
        raise ValueError("requires exactly one of days= or since_iso=")

    if since_iso is not None:
        since_us = iso_instant_us(since_iso)
        if since_us is None:
            raise ValueError("invalid since_iso timestamp")
        clauses = ["aletheia_instant_us(created_at) >= ?"]
        params: list[object] = [since_us]
        if until_iso is not None:
            until_us = iso_instant_us(until_iso)
            if until_us is None:
                raise ValueError("invalid until_iso timestamp")
            clauses.append("aletheia_instant_us(created_at) < ?")
            params.append(until_us)
        return " AND ".join(clauses), params

    assert days is not None
    return (
        "aletheia_instant_us(created_at) >= "
        "aletheia_instant_us(datetime('now', ?))",
        [f"-{int(days)} days"],
    )


def _invalid_shadow_created_at_count(
    connection,
    *,
    clauses: tuple[str, ...] = (),
    params: tuple[object, ...] = (),
) -> int:
    """Count scoped measurement rows rejected by the timestamp authority gate."""
    where = [
        "net_pnl_pct IS NOT NULL",
        "outcome_candidate_key IS NOT NULL",
        *clauses,
        "aletheia_instant_us(created_at) IS NULL",
    ]
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE " + " AND ".join(where),
            params,
        ).fetchone()
    except Exception:
        return 0
    return int(row[0] or 0)


def list_deduped_shadows(
    *,
    days: int | None = None,
    since_iso: str | None = None,
    until_iso: str | None = None,
    symbols: list[str] | None = None,
    policy_epoch_id: str | None = None,
    scanner_family: str | None = None,
    columns: tuple[str, ...] = (
        "symbol",
        "direction",
        "trade_score",
        "net_pnl_pct",
        "was_selected",
    ),
) -> list[dict]:
    """Return one resolved shadow row per candidate (latest tick per ``outcome_candidate_key``).

    A candidate is re-scanned many times, appending a shadow row each tick;
    reading the raw table therefore over-weights long-lived candidates. Every
    analytics/reporting consumer must collapse to one vote per candidate — this
    helper is the canonical implementation. Prior to its introduction each
    consumer maintained its own ``PARTITION BY outcome_candidate_key`` CTE,
    which was memory-flagged as an easy-to-forget correctness trap.

    Time window is one of:

    - ``days=N`` — rows with ``created_at >= now - N days`` (legacy shortcut).
    - ``since_iso[, until_iso]`` — absolute ISO timestamps. ``until_iso`` is
      exclusive; when omitted, the window is open-ended on the right.

    Passing both ``days`` and ``since_iso`` is an error; passing neither is
    also an error (no implicit "all history" mode — window intent must be
    explicit).

    Args:
        days: lookback window in days.
        since_iso, until_iso: absolute ISO datetime window.
        symbols: canonical symbol filter (e.g. ``["BTC/USDT"]``); ``None`` for all.
        policy_epoch_id: when set, restrict to rows written under the same
            policy epoch — required whenever the caller crosses an epoch
            rotation (see counterfactual guidance L0 completeness).
        scanner_family: restrict to one scanner family (e.g. ``"momentum"``).
        columns: projection list. Every column must exist in ``shadow_trades``.
    """
    from aletheia.storage.repositories import valid_keyed_shadow_projection_sql
    from aletheia.storage.sqlite import get_db, register_instant_us

    connection = get_db()
    register_instant_us(connection)
    time_where, time_params = _shadow_time_window_sql(
        days=days,
        since_iso=since_iso,
        until_iso=until_iso,
    )

    scope_clauses: list[str] = []
    scope_params: list[object] = []
    symbol_clause = ""
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        symbol_clause = f"AND symbol IN ({placeholders})"
        scope_clauses.append(f"symbol IN ({placeholders})")
        scope_params.extend(symbols)
    epoch_clause = ""
    if policy_epoch_id:
        epoch_clause = "AND policy_epoch_id = ?"
        scope_clauses.append("policy_epoch_id = ?")
        scope_params.append(policy_epoch_id)
    family_clause = ""
    if scanner_family:
        family_clause = "AND scanner_family = ?"
        scope_clauses.append("scanner_family = ?")
        scope_params.append(scanner_family)

    invalid_count = _invalid_shadow_created_at_count(
        connection,
        clauses=tuple(scope_clauses),
        params=tuple(scope_params),
    )
    if invalid_count:
        logger.warning(
            "shadow_cohort_invalid_created_at_excluded count=%d",
            invalid_count,
        )

    projection = ", ".join(columns)
    authority_where = valid_keyed_shadow_projection_sql()
    sql = f"""
        WITH resolved AS (
            SELECT
                {projection},
                outcome_candidate_key,
                ROW_NUMBER() OVER (
                    PARTITION BY outcome_candidate_key
                    ORDER BY aletheia_instant_us(created_at) DESC
                ) AS rn
            FROM shadow_trades
            WHERE net_pnl_pct IS NOT NULL
              AND outcome_candidate_key IS NOT NULL
              AND aletheia_instant_us(created_at) IS NOT NULL
              AND {authority_where}
              AND {time_where}
              {symbol_clause}
              {epoch_clause}
              {family_clause}
        )
        SELECT {projection}
        FROM resolved
        WHERE rn = 1
    """
    params = [*time_params, *scope_params]
    try:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("shadow_cohort_read_failed error=%s", exc)
        return []


def _detect_window_epochs(
    *,
    days: int | None = None,
    since_iso: str | None = None,
    until_iso: str | None = None,
) -> tuple[str | None, str]:
    """Return (epoch_id, status) for resolved shadow rows in the given window.

    status values:
      "single"   — exactly one distinct non-null epoch; epoch_id is that value.
      "mixed"    — multiple epochs, or NULL rows alongside non-NULL ones.
      "all_null" — no non-null epoch rows (pure legacy data).
    """
    from aletheia.storage.repositories import valid_keyed_shadow_projection_sql
    from aletheia.storage.sqlite import get_db, register_instant_us

    connection = get_db()
    register_instant_us(connection)
    time_where, params = _shadow_time_window_sql(
        days=days,
        since_iso=since_iso,
        until_iso=until_iso,
    )
    invalid_count = _invalid_shadow_created_at_count(connection)
    if invalid_count:
        logger.warning(
            "shadow_cohort_invalid_created_at_excluded count=%d",
            invalid_count,
        )

    authority_where = valid_keyed_shadow_projection_sql()
    sql = f"""
        SELECT
            COUNT(DISTINCT policy_epoch_id) AS distinct_epochs,
            SUM(CASE WHEN policy_epoch_id IS NULL THEN 1 ELSE 0 END) AS null_count,
            MAX(policy_epoch_id) AS epoch_id
        FROM shadow_trades
        WHERE net_pnl_pct IS NOT NULL
          AND outcome_candidate_key IS NOT NULL
          AND aletheia_instant_us(created_at) IS NOT NULL
          AND {authority_where}
          AND {time_where}
    """
    try:
        row = connection.execute(sql, params).fetchone()
    except Exception as exc:
        # A failed read is an unavailable source, not "pure legacy data": callers
        # map the raise to status "unavailable" + policy_epoch_detection_failed.
        logger.warning("shadow_epoch_detection_failed error=%s", exc)
        raise
    if not row:
        return None, "all_null"
    distinct = row["distinct_epochs"] or 0
    null_count = row["null_count"] or 0
    if distinct > 1 or (distinct == 1 and null_count > 0):
        return None, "mixed"
    if distinct == 1:
        return row["epoch_id"], "single"
    return None, "all_null"


_EPOCH_DETECTION_FAILURE_REASON = "policy_epoch_detection_failed"
