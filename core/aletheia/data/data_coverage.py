"""Cached persisted-data coverage report for operator-facing diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from collections import Counter
import logging
import time

from aletheia.data.freshness import MAX_FUTURE_SKEW, assess_freshness
from aletheia.storage.sqlite import get_db
from aletheia.storage.repositories import LEGACY_DERIVATIVES_WHERE


logger = logging.getLogger("aletheia.data.data_coverage")
_CACHE_TTL_SEC = 30.0
_cache_lock = Lock()
_cached_at = 0.0
_cached_report: dict | None = None


@dataclass(frozen=True)
class DomainDefinition:
    domain: str
    label: str
    table: str
    newest_column: str
    symbol_column: str | None
    source_column: str | None
    max_age_sec: int
    critical: bool = False
    timestamp_ms: bool = False
    idle_when_old: bool = False
    where_sql: str = ""
    where_params: tuple = ()


_SECONDARY_DERIVATIVE_SOURCES = (
    "hyperliquid", "hyperliquid_predicted", "coinalyze",
)
_SECONDARY_PLACEHOLDERS = ",".join("?" for _ in _SECONDARY_DERIVATIVE_SOURCES)
_PRIMARY_DERIVATIVE_WHERE = (
    f"(exchange IS NULL OR exchange NOT IN ({_SECONDARY_PLACEHOLDERS})) AND {LEGACY_DERIVATIVES_WHERE}"
)
_PRIMARY_FRESHNESS_DOMAINS = {
    "primary_funding": "funding",
    "primary_open_interest": "open_interest",
    "long_short_ratio": "open_interest",
}

DOMAIN_DEFINITIONS = (
    DomainDefinition(
        "orderbook", "Orderbook", "orderbook_snapshots", "observed_at",
        "symbol", "exchange", 15 * 60, critical=True,
    ),
    DomainDefinition(
        "primary_funding", "Funding", "funding_rate_snapshots", "observed_at",
        "symbol", "exchange", 15 * 60, critical=True,
        where_sql=_PRIMARY_DERIVATIVE_WHERE,
        where_params=_SECONDARY_DERIVATIVE_SOURCES,
    ),
    DomainDefinition(
        "primary_open_interest", "Open interest", "open_interest_snapshots", "observed_at",
        "symbol", "exchange", 15 * 60, critical=True,
        where_sql=_PRIMARY_DERIVATIVE_WHERE,
        where_params=_SECONDARY_DERIVATIVE_SOURCES,
    ),
    DomainDefinition(
        "long_short_ratio", "Long/short ratio", "open_interest_snapshots", "observed_at",
        "symbol", "exchange", 15 * 60, idle_when_old=True,
        where_sql=f"({_PRIMARY_DERIVATIVE_WHERE}) AND long_short_ratio IS NOT NULL",
        where_params=_SECONDARY_DERIVATIVE_SOURCES,
    ),
    DomainDefinition(
        "liquidations", "Liquidations", "liquidation_events", "observed_at",
        "symbol", "exchange", 60 * 60, idle_when_old=True,
    ),
    DomainDefinition(
        "macro", "Macro", "macro_snapshots", "observed_at",
        None, None, 30 * 60, critical=True,
    ),
    DomainDefinition(
        "onchain", "On-chain", "onchain_snapshots", "observed_at",
        None, "source", 30 * 60, idle_when_old=True,
    ),
    DomainDefinition(
        "ohlcv", "OHLCV", "market_ohlcv", "ts",
        "symbol", "exchange", 2 * 60 * 60, critical=True, timestamp_ms=True,
    ),
)


def _query_domain(definition: DomainDefinition, *, targets: list[tuple] | None = None) -> dict:
    db = get_db()
    clauses = [definition.where_sql] if definition.where_sql else []
    params = definition.where_params
    identity = "symbol, exchange"
    if targets is not None:
        if definition.domain in {"orderbook", "ohlcv"}:
            identity += ", market_type"
        if targets:
            placeholders = ",".join("(" + ",".join("?" for _ in target) + ")" for target in targets)
            clauses.append(f"({identity}) IN ({placeholders})")
            params += tuple(value for target in targets for value in target)
        else:
            clauses.append("0")
    where = " WHERE " + " AND ".join(f"({clause})" for clause in clauses) if clauses else ""
    symbol_expr = f"COUNT(DISTINCT {definition.symbol_column})" if definition.symbol_column else "0"
    row = db.execute(
        f"SELECT COUNT(*) AS row_count, {symbol_expr} AS symbol_count, "
        f"MAX({definition.newest_column}) AS newest FROM {definition.table}{where}", params,
    ).fetchone()
    source_counts = {}
    if definition.source_column:
        source_counts = {
            str(item["source"]): int(item["count"])
            for item in db.execute(
                f"SELECT COALESCE({definition.source_column}, 'unknown') AS source, "
                f"COUNT(*) AS count FROM {definition.table}{where} GROUP BY source", params,
            )
        }
    result = {
        "row_count": int(row["row_count"] or 0),
        "symbol_count": int(row["symbol_count"] or 0),
        "newest": row["newest"],
        "source_counts": source_counts,
    }
    if definition.domain in _PRIMARY_FRESHNESS_DOMAINS:
        # Current positioning must describe the latest OI row, not resurrect an
        # older non-null ratio after the latest collector omitted that target.
        latest_where = where
        value_expr = "1"
        if targets is not None and definition.domain == "long_short_ratio":
            latest_clauses = [_PRIMARY_DERIVATIVE_WHERE, *clauses[1:]]
            latest_where = " WHERE " + " AND ".join(f"({clause})" for clause in latest_clauses)
            value_expr = "long_short_ratio IS NOT NULL"
        result["freshness_rows"] = [dict(item) for item in db.execute(
            "SELECT observed_at, source_observed_at, acquired_at, source_time_status, has_value "
            "FROM (SELECT observed_at, source_observed_at, acquired_at, source_time_status, "
            f"{value_expr} AS has_value, ROW_NUMBER() OVER ("
            "PARTITION BY symbol, COALESCE(exchange, '') "
            "ORDER BY COALESCE(acquired_at, observed_at) DESC, rowid DESC) AS freshness_rank "
            f"FROM {definition.table}{latest_where}) WHERE freshness_rank=1", params,
        )]
    elif targets is not None:
        result["freshness_rows"] = [dict(item) for item in db.execute(
            f"SELECT MAX({definition.newest_column}) AS observed_at "
            f"FROM {definition.table}{where} GROUP BY {identity}", params,
        )]
    return result


def _parse_newest(value: object, *, timestamp_ms: bool) -> datetime | None:
    if value in (None, ""):
        return None
    if timestamp_ms:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _summarize_domain(
    definition: DomainDefinition, values: dict, now: datetime, *, target_count: int | None = None,
) -> dict:
    newest = _parse_newest(values["newest"], timestamp_ms=definition.timestamp_ms)
    rows = values.get("freshness_rows")
    if rows is None:
        rows = [{"observed_at": values["newest"]}] if values["row_count"] else []
    missing_targets = 0 if target_count is None else target_count - sum(
        bool(row.get("has_value", True)) for row in rows
    )
    counts: Counter[str] = Counter()
    ages: list[float] = []
    for row in rows:
        if not row.get("has_value", True):
            continue
        if definition.domain in _PRIMARY_FRESHNESS_DOMAINS:
            verdict = assess_freshness(
                {**row, "source_time_status": row.get("source_time_status") or "missing"},
                _PRIMARY_FRESHNESS_DOMAINS[definition.domain], now=now,
            )
            state, age = verdict.state, verdict.age_seconds
        else:
            observed = _parse_newest(row["observed_at"], timestamp_ms=definition.timestamp_ms)
            age = (now - observed).total_seconds() if observed else None
            state = (
                "invalid" if observed is None
                else "future" if age < -MAX_FUTURE_SKEW.total_seconds()
                else "stale" if age > definition.max_age_sec
                else "fresh"
            )
        counts[state] += 1
        if age is not None:
            ages.append(max(0.0, age))
    unavailable = sum(counts[state] for state in ("missing", "invalid", "future"))
    if target_count == 0:
        state = "idle"
    elif values["row_count"] == 0:
        state = "idle" if definition.idle_when_old else "empty"
    elif unavailable:
        state = "invalid"
    elif missing_targets:
        state = "idle" if definition.idle_when_old else "invalid"
    elif counts["stale"]:
        state = "idle" if definition.idle_when_old else "stale"
    else:
        state = "fresh"
    if missing_targets:
        counts["missing"] += missing_targets
    return {
        "state": state,
        "row_count": values["row_count"],
        "symbol_count": values["symbol_count"],
        "source_counts": values["source_counts"],
        "newest_observed_at": newest.isoformat() if newest else None,
        "age_sec": round(max(ages), 1) if ages else None,
        "fresh_row_count": counts["fresh"],
        "stale_row_count": counts["stale"],
        "unavailable_row_count": unavailable + missing_targets,
        "freshness_state_counts": dict(sorted(counts.items())),
        "missing_target_count": missing_targets,
    }


def _build_report(now: datetime, timings: dict[str, float] | None = None) -> dict:
    from aletheia.agent_runtime.scheduler import get_collector_targets
    from aletheia.config.loader import load_config

    cfg = load_config(persist_migrations=False)
    instruments, derivative_symbols = get_collector_targets(cfg)
    instrument_targets = list(dict.fromkeys(
        (item.symbol, item.exchange, item.market_type) for item in instruments
    ))
    derivative_targets = [(symbol, cfg.market_data.ccxt_exchange) for symbol in derivative_symbols]
    domains: list[dict] = []
    for definition in DOMAIN_DEFINITIONS:
        started = time.perf_counter()
        targets = (
            instrument_targets if definition.domain in {"orderbook", "ohlcv"}
            else derivative_targets if definition.domain in _PRIMARY_FRESHNESS_DOMAINS
            else None
        )
        inventory = _summarize_domain(definition, _query_domain(definition), now)
        current = inventory if targets is None else _summarize_domain(
            definition, _query_domain(definition, targets=targets), now, target_count=len(targets),
        )
        domains.append({
            "domain": definition.domain,
            "label": definition.label,
            "critical": definition.critical,
            "max_age_sec": definition.max_age_sec,
            **current,
            "scope": "global" if targets is None else "current_targets",
            "target_count": None if targets is None else len(targets),
            "inventory": inventory,
        })
        if timings is not None:
            timings[definition.domain] = (time.perf_counter() - started) * 1000.0
    problems = [row for row in domains if row["state"] in {"empty", "invalid", "stale"}]
    return {
        "status": "empty" if all(row["state"] == "empty" for row in domains) else "attention" if problems else "healthy",
        "generated_at": now.isoformat(),
        "domain_count": len(domains),
        "fresh_count": sum(row["state"] == "fresh" for row in domains),
        "problem_count": len(problems),
        "critical_problem_count": sum(row["critical"] for row in problems),
        "domains": domains,
    }



def clear_data_coverage_cache() -> None:
    global _cached_at, _cached_report
    with _cache_lock:
        _cached_at = 0.0
        _cached_report = None


def get_data_coverage_report(
    *,
    now: datetime | None = None,
    timings: dict[str, float] | None = None,
) -> dict:
    """Return bounded aggregate coverage; normal runtime reads use a 30-second cache."""
    global _cached_at, _cached_report
    started = time.perf_counter()
    if now is not None:
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        report = _build_report(current.astimezone(timezone.utc), timings=timings)
        if timings is not None:
            timings["total"] = (time.perf_counter() - started) * 1000.0
        return report

    monotonic_now = time.monotonic()
    with _cache_lock:
        if _cached_report is not None and monotonic_now - _cached_at < _CACHE_TTL_SEC:
            if timings is not None:
                timings["cache_hit"] = 0.0
                timings["total"] = (time.perf_counter() - started) * 1000.0
            return _cached_report
        report = _build_report(datetime.now(timezone.utc), timings=timings)
        _cached_report = report
        _cached_at = monotonic_now
    duration_ms = (time.perf_counter() - started) * 1000.0
    if timings is not None:
        timings["total"] = duration_ms
    if duration_ms >= 500.0:
        domain_timings = timings or {}
        slowest = sorted(
            ((name, value) for name, value in domain_timings.items() if name != "total"),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        detail = ",".join(f"{name}:{value:.1f}" for name, value in slowest) or "unavailable"
        logger.warning(
            "data_coverage_refresh_slow duration_ms=%.1f slowest_ms=%s",
            duration_ms,
            detail,
        )
    return report
