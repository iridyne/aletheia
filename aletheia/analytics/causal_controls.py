"""Prospective control-cohort logging for Alpha Attribution.

These helpers record observation-only cohorts. They do not affect trading
selection, ranking, risk approval, or order execution.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


_HORIZONS = {
    "universe": ("1h", "4h", "24h"),
    "serenity": ("1d", "3d", "7d"),
}

_HORIZON_DELTA = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}


def record_universe_control_cohort(
    *,
    snapshot_id: str,
    selected_members: list[dict],
    scored_candidates: list[dict],
    entry_ts: str,
    max_controls: int = 10,
) -> int:
    """Persist selected universe members and same-pool controls for causal audit."""
    selected_symbols = {m.get("symbol") for m in selected_members}
    controls = [
        c for c in scored_candidates
        if c.get("symbol") and c.get("symbol") not in selected_symbols and c.get("last_price")
    ]
    controls = _deterministic_pick(controls, seed=f"universe:{snapshot_id}:{entry_ts}", limit=max_controls)

    rows: list[dict] = []
    cohort_id = f"universe:{snapshot_id}"
    for member in selected_members:
        if not member.get("symbol") or not member.get("entry_price_usd"):
            continue
        rows.extend(_rows_for_item(
            layer="universe",
            cohort_id=cohort_id,
            treatment_id=member.get("id") or snapshot_id,
            role="selected",
            symbol=member["symbol"],
            direction=member.get("direction"),
            entry_price=member.get("entry_price_usd"),
            entry_ts=entry_ts,
            score=member.get("total_score"),
            module_path="aletheia/universe/allocator.py",
            metadata={
                "snapshot_id": snapshot_id,
                "scanner_family": member.get("scanner_family"),
                "regime_tag": member.get("regime_tag"),
            },
        ))
    for item in controls:
        rows.extend(_rows_for_item(
            layer="universe",
            cohort_id=cohort_id,
            treatment_id=snapshot_id,
            role="control",
            symbol=item["symbol"],
            direction=item.get("scanner_direction") or item.get("direction"),
            entry_price=item.get("last_price"),
            entry_ts=entry_ts,
            score=item.get("total_score"),
            module_path="aletheia/universe/allocator.py",
            metadata={
                "snapshot_id": snapshot_id,
                "scanner_family": item.get("scanner_family"),
                "control_source": "same_refresh_scored_pool",
            },
        ))
    return _insert_rows(rows)


def record_serenity_control_cohort(opportunity: dict, max_controls: int = 10) -> int:
    """Persist a Serenity opportunity and contemporaneous observable controls."""
    symbol = opportunity.get("symbol")
    entry_ts = opportunity.get("created_at") or _utcnow()
    if not symbol:
        return 0

    cohort_id = f"serenity:{opportunity.get('id') or uuid4().hex[:12]}"
    rows = _rows_for_item(
        layer="serenity",
        cohort_id=cohort_id,
        treatment_id=opportunity.get("id"),
        role="selected",
        symbol=symbol,
        direction=opportunity.get("direction"),
        entry_price=_latest_ohlcv_price(symbol, entry_ts),
        entry_ts=entry_ts,
        score=opportunity.get("confidence_score"),
        module_path="aletheia/agents/research_worker.py",
        metadata={
            "theme_id": opportunity.get("theme_id"),
            "asset_class": opportunity.get("asset_class"),
            "market_type": opportunity.get("market_type"),
        },
    )
    for control_symbol in _observable_controls(
        exclude_symbol=symbol,
        entry_ts=entry_ts,
        equity_like=(opportunity.get("asset_class") == "equity" or "/" not in symbol),
        limit=max_controls,
    ):
        rows.extend(_rows_for_item(
            layer="serenity",
            cohort_id=cohort_id,
            treatment_id=opportunity.get("id"),
            role="control",
            symbol=control_symbol,
            direction=opportunity.get("direction"),
            entry_price=_latest_ohlcv_price(control_symbol, entry_ts),
            entry_ts=entry_ts,
            score=None,
            module_path="aletheia/agents/research_worker.py",
            metadata={"control_source": "observable_ohlcv_pool"},
        ))
    return _insert_rows(rows)


def _rows_for_item(
    *,
    layer: str,
    cohort_id: str,
    treatment_id: str | None,
    role: str,
    symbol: str,
    direction: str | None,
    entry_price: float | None,
    entry_ts: str,
    score: float | None,
    module_path: str,
    metadata: dict,
) -> list[dict]:
    rows: list[dict] = []
    for horizon in _HORIZONS[layer]:
        rows.append({
            "id": uuid4().hex,
            "layer": layer,
            "cohort_id": cohort_id,
            "treatment_id": treatment_id,
            "role": role,
            "symbol": symbol,
            "direction": direction,
            "horizon": horizon,
            "entry_price": entry_price,
            "entry_ts": entry_ts,
            "score": score,
            "module_path": module_path,
            "metadata": json.dumps({**metadata, "due_at": _due_at(entry_ts, horizon)}, ensure_ascii=False),
            "created_at": _utcnow(),
        })
    return rows


def _insert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        was_in_tx = db.in_transaction
        for row in rows:
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            db.execute(
                f"INSERT OR IGNORE INTO alpha_control_observations ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        if not was_in_tx:
            db.commit()
        return len(rows)
    except Exception:
        return 0


def _deterministic_pick(items: list[dict], *, seed: str, limit: int) -> list[dict]:
    return sorted(
        items,
        key=lambda item: hashlib.sha256(f"{seed}:{item.get('symbol')}".encode("utf-8")).hexdigest(),
    )[:limit]


def _observable_controls(*, exclude_symbol: str, entry_ts: str, equity_like: bool, limit: int) -> list[str]:
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        rows = db.execute("SELECT DISTINCT symbol FROM market_ohlcv").fetchall()
        symbols = [r["symbol"] for r in rows if r["symbol"] and r["symbol"] != exclude_symbol]
        if equity_like:
            equity_symbols = [s for s in symbols if "/" not in s]
            if equity_symbols:
                symbols = equity_symbols
        picked = sorted(
            symbols,
            key=lambda s: hashlib.sha256(f"serenity:{entry_ts}:{exclude_symbol}:{s}".encode("utf-8")).hexdigest(),
        )
        return picked[:limit]
    except Exception:
        return []


def _latest_ohlcv_price(symbol: str, entry_ts: str) -> float | None:
    try:
        from aletheia.storage.sqlite import get_db

        dt = _parse_dt(entry_ts)
        if dt is None:
            return None
        ts = int(dt.timestamp() * 1000)
        row = get_db().execute(
            "SELECT close FROM market_ohlcv WHERE symbol = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (symbol, ts),
        ).fetchone()
        return float(row["close"]) if row and row["close"] is not None else None
    except Exception:
        return None


def _due_at(entry_ts: str, horizon: str) -> str | None:
    dt = _parse_dt(entry_ts)
    if dt is None:
        return None
    return (dt + _HORIZON_DELTA[horizon]).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
