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
            venue=member.get("venue"),
            market_type=member.get("market_type") or "spot",
            direction=member.get("direction"),
            entry_price=member.get("entry_price_usd"),
            entry_ts=entry_ts,
            score=member.get("total_score"),
            scanner_family=member.get("scanner_family"),
            regime_tag=member.get("regime_tag"),
            entry_spread_bps=member.get("spread_bps"),
            funding_rate_at_entry=member.get("funding_rate"),
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
            venue=item.get("venue") or (
                selected_members[0].get("venue") if selected_members else None
            ),
            market_type=item.get("market_type") or "spot",
            direction=item.get("scanner_direction") or item.get("direction"),
            entry_price=item.get("last_price"),
            entry_ts=entry_ts,
            score=item.get("total_score"),
            scanner_family=item.get("scanner_family"),
            regime_tag=item.get("regime_tag"),
            entry_spread_bps=item.get("spread_bps"),
            funding_rate_at_entry=item.get("funding_rate"),
            module_path="aletheia/universe/allocator.py",
            metadata={
                "snapshot_id": snapshot_id,
                "scanner_family": item.get("scanner_family"),
                "regime_tag": item.get("regime_tag"),
                "control_source": "same_refresh_scored_pool",
            },
        ))
    inserted = _insert_rows(rows)
    if inserted:
        _record_universe_allocation_decisions(rows)
    return inserted


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
        venue=None,
        market_type=opportunity.get("market_type") or "spot",
        direction=opportunity.get("direction"),
        entry_price=_latest_ohlcv_price(symbol, entry_ts),
        entry_ts=entry_ts,
        score=opportunity.get("confidence_score"),
        scanner_family=None,
        regime_tag=None,
        entry_spread_bps=None,
        funding_rate_at_entry=None,
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
            venue=None,
            market_type="spot",
            direction=opportunity.get("direction"),
            entry_price=_latest_ohlcv_price(control_symbol, entry_ts),
            entry_ts=entry_ts,
            score=None,
            scanner_family=None,
            regime_tag=None,
            entry_spread_bps=None,
            funding_rate_at_entry=None,
            module_path="aletheia/agents/research_worker.py",
            metadata={"control_source": "active_universe_registry"},
        ))
    return _insert_rows(rows)


def _rows_for_item(
    *,
    layer: str,
    cohort_id: str,
    treatment_id: str | None,
    role: str,
    symbol: str,
    venue: str | None,
    market_type: str,
    direction: str | None,
    entry_price: float | None,
    entry_ts: str,
    score: float | None,
    scanner_family: str | None,
    regime_tag: str | None,
    entry_spread_bps: float | None,
    funding_rate_at_entry: float | None,
    module_path: str,
    metadata: dict,
) -> list[dict]:
    rows: list[dict] = []
    outcome_material = (
        f"{layer}|{cohort_id}|{role}|{treatment_id}|{venue}|{symbol}|"
        f"{market_type}|{direction}|{entry_ts}"
    )
    outcome_candidate_key = (
        f"control:{hashlib.sha256(outcome_material.encode('utf-8')).hexdigest()[:20]}"
    )
    if venue and direction in {"long", "short"} and entry_price:
        try:
            from aletheia.analytics.filter_evidence import ensure_candidate_outcomes

            ensure_candidate_outcomes(
                outcome_candidate_key=outcome_candidate_key,
                venue=venue,
                symbol=symbol,
                market_type=market_type,
                direction=direction,
                scanner_family=scanner_family,
                regime_tag=regime_tag,
                entry_price_usd=float(entry_price),
                entry_spread_bps=entry_spread_bps,
                funding_rate_at_entry=funding_rate_at_entry,
                entry_ts=entry_ts,
            )
        except Exception:
            pass
    for horizon in _HORIZONS[layer]:
        rows.append({
            "id": uuid4().hex,
            "layer": layer,
            "cohort_id": cohort_id,
            "treatment_id": treatment_id,
            "role": role,
            "symbol": symbol,
            "venue": venue,
            "market_type": market_type,
            "scanner_family": scanner_family,
            "direction": direction,
            "horizon": horizon,
            "entry_price": entry_price,
            "entry_ts": entry_ts,
            "score": score,
            "module_path": module_path,
            "metadata": json.dumps({**metadata, "due_at": _due_at(entry_ts, horizon)}, ensure_ascii=False),
            "outcome_candidate_key": outcome_candidate_key if venue else None,
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


def _record_universe_allocation_decisions(rows: list[dict]) -> int:
    """Route scanner_weights_quota allocations through the compressed batch stream.

    A universe refresh emits ~20 (selected + control) items that share the same
    scanner_weights / family_quota parameter block. Writing each item as its own
    filter_decisions row multiplied that shared snapshot by candidate count. The
    batched writer zlib-encodes the per-fact list once with a single parameter
    snapshot, cutting the on-disk footprint of this filter roughly 10-20x while
    preserving every per-candidate field needed for cohort assignment.
    """
    try:
        from aletheia.analytics.filter_evidence import record_filter_decision_batch
        from aletheia.config.loader import load_config

        universe_cfg = load_config().universe
        parameters = {
            "scanner_weights": universe_cfg.scanner_weights,
            "family_quota": universe_cfg.family_quota,
            "family_quota_by_regime": universe_cfg.family_quota_by_regime,
            "scanner_regime_scoring": universe_cfg.scanner_regime_scoring,
        }
        # Group by (cohort_id, evaluated_at) so each universe refresh yields one
        # batch row. The batch's dedup_key already keys on (run_id, filter_name,
        # stage, filter_version), so redelivery is idempotent.
        batches: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            if row.get("layer") != "universe" or row.get("horizon") != "1h":
                continue
            cohort_id = str(row.get("cohort_id") or "")
            if not cohort_id:
                continue
            metadata = json.loads(row.get("metadata") or "{}")
            role = row.get("role")
            market_type = row.get("market_type") or "spot"
            evaluated_at = str(row.get("entry_ts") or _utcnow())
            candidate_key = (
                f"allocation:{cohort_id}:{role}:"
                f"{row.get('symbol')}:{market_type}"
            )
            fact = {
                "candidate_key": candidate_key,
                "symbol": row["symbol"],
                "venue": row.get("venue"),
                "market_type": market_type,
                "direction": row.get("direction"),
                "scanner_family": row.get("scanner_family"),
                "regime_tag": metadata.get("regime_tag"),
                "universe_snapshot_id": metadata.get("snapshot_id"),
                "decision": "passed" if role == "selected" else "rejected",
                "reason_code": (
                    "selected_by_allocator" if role == "selected"
                    else "same_pool_control_not_selected"
                ),
                "role": role,
                "score": row.get("score"),
                "outcome_candidate_key": row.get("outcome_candidate_key"),
                "entry_price_usd": row.get("entry_price"),
                "evaluated_at": evaluated_at,
            }
            batches.setdefault((cohort_id, evaluated_at), []).append(fact)

        written = 0
        for (cohort_id, evaluated_at), facts in batches.items():
            if not facts:
                continue
            record_filter_decision_batch(
                run_id=cohort_id,
                candidate_kind="universe_allocation",
                filter_name="scanner_weights_quota",
                stage="universe_allocation",
                candidate_facts=facts,
                parameters=parameters,
                evaluated_at=evaluated_at,
            )
            written += len(facts)
        return written
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
        # The active registry is the contemporaneously observable control pool.
        # Scanning every historical OHLCV row was both dataset-size-sensitive and
        # semantically weaker because stale/delisted symbols could become controls.
        rows = db.execute(
            "SELECT DISTINCT symbol FROM universe_registry "
            "WHERE status = 'active' AND COALESCE(is_blacklisted, 0) = 0"
        ).fetchall()
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
        db = get_db()
        instrument = db.execute(
            "SELECT venue, market_type FROM universe_registry "
            "WHERE symbol = ? AND status = 'active' "
            "AND COALESCE(is_blacklisted, 0) = 0 "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if not instrument:
            return None
        row = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE exchange = ? AND symbol = ? AND market_type = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (instrument["venue"], symbol, instrument["market_type"], ts),
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
