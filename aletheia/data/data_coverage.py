"""Cached persisted-data coverage report for operator-facing diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
import time

from aletheia.storage.sqlite import get_db


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
    f"exchange IS NULL OR exchange NOT IN ({_SECONDARY_PLACEHOLDERS})"
)

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


def _query_domain(definition: DomainDefinition) -> dict:
    db = get_db()
    where = f" WHERE {definition.where_sql}" if definition.where_sql else ""
    symbol_expr = (
        f"COUNT(DISTINCT {definition.symbol_column})" if definition.symbol_column else "0"
    )
    row = db.execute(
        f"SELECT COUNT(*) AS row_count, {symbol_expr} AS symbol_count, "
        f"MAX({definition.newest_column}) AS newest FROM {definition.table}{where}",
        definition.where_params,
    ).fetchone()

    source_counts: dict[str, int] = {}
    if definition.source_column:
        source_rows = db.execute(
            f"SELECT COALESCE({definition.source_column}, 'unknown') AS source, "
            f"COUNT(*) AS count FROM {definition.table}{where} GROUP BY source",
            definition.where_params,
        ).fetchall()
        source_counts = {str(item["source"]): int(item["count"]) for item in source_rows}

    return {
        "row_count": int(row["row_count"] or 0),
        "symbol_count": int(row["symbol_count"] or 0),
        "newest": row["newest"],
        "source_counts": source_counts,
    }


def _parse_newest(value: object, *, timestamp_ms: bool) -> datetime | None:
    if value in (None, ""):
        return None
    if timestamp_ms:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_report(now: datetime) -> dict:
    domain_rows: list[dict] = []
    critical_problem_count = 0
    problem_count = 0

    for definition in DOMAIN_DEFINITIONS:
        values = _query_domain(definition)
        newest = _parse_newest(values["newest"], timestamp_ms=definition.timestamp_ms)
        age_sec = max(0.0, (now - newest).total_seconds()) if newest else None
        if values["row_count"] == 0:
            state = "idle" if definition.idle_when_old else "empty"
        elif newest is None:
            state = "invalid"
        elif age_sec is not None and age_sec > definition.max_age_sec:
            state = "idle" if definition.idle_when_old else "stale"
        else:
            state = "fresh"

        is_problem = state in {"empty", "invalid", "stale"}
        if is_problem:
            problem_count += 1
            if definition.critical:
                critical_problem_count += 1
        domain_rows.append({
            "domain": definition.domain,
            "label": definition.label,
            "state": state,
            "critical": definition.critical,
            "row_count": values["row_count"],
            "symbol_count": values["symbol_count"],
            "source_counts": values["source_counts"],
            "newest_observed_at": newest.isoformat() if newest else None,
            "age_sec": round(age_sec, 1) if age_sec is not None else None,
            "max_age_sec": definition.max_age_sec,
        })

    status = "healthy" if problem_count == 0 else "attention"
    if all(row["state"] == "empty" for row in domain_rows):
        status = "empty"
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "domain_count": len(domain_rows),
        "fresh_count": sum(row["state"] == "fresh" for row in domain_rows),
        "problem_count": problem_count,
        "critical_problem_count": critical_problem_count,
        "domains": domain_rows,
    }


def clear_data_coverage_cache() -> None:
    global _cached_at, _cached_report
    with _cache_lock:
        _cached_at = 0.0
        _cached_report = None


def get_data_coverage_report(*, now: datetime | None = None) -> dict:
    """Return bounded aggregate coverage; normal runtime reads use a 30-second cache."""
    global _cached_at, _cached_report
    if now is not None:
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return _build_report(current.astimezone(timezone.utc))

    monotonic_now = time.monotonic()
    with _cache_lock:
        if _cached_report is not None and monotonic_now - _cached_at < _CACHE_TTL_SEC:
            return _cached_report
        report = _build_report(datetime.now(timezone.utc))
        _cached_report = report
        _cached_at = monotonic_now
        return report
