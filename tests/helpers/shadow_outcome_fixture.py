"""Small canonical settlement fixture for keyed shadow consumer tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from aletheia.storage.sqlite import get_db

_HORIZON_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "24h": 24.0}


def insert_canonical_outcome_for_shadow(
    shadow_id: str,
    *,
    horizon: str,
    net_return_pct: float,
) -> dict:
    db = get_db()
    raw = db.execute("SELECT * FROM shadow_trades WHERE id=?", (shadow_id,)).fetchone()
    if raw is None:
        raise AssertionError(f"missing shadow fixture: {shadow_id}")
    row = dict(raw)
    venue_value = row.get("venue")
    if not isinstance(venue_value, str) or not venue_value.strip():
        raise AssertionError(f"shadow fixture requires exact venue: {shadow_id}")
    venue = venue_value.strip()
    existing = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon=?",
        (row["outcome_candidate_key"], horizon),
    ).fetchone()
    if existing is not None:
        canonical = dict(existing)
        return {
            "exit_price": canonical["exit_price_usd"],
            "gross_return_pct": canonical["gross_return_pct"],
            "directional_return_pct": canonical["directional_return_pct"],
            "net_return_pct": canonical["net_return_pct"],
            "settled_at": canonical["settled_at"],
            "venue": canonical["venue"],
            "settlement_price_source": canonical["settlement_price_source"],
            "settlement_lag_sec": canonical["settlement_lag_sec"],
            "cost_model_version": canonical["cost_model_version"],
        }
    entry = float(row["entry_price"])
    direction = str(row["direction"]).lower()
    gross_return_pct = net_return_pct if direction == "long" else -net_return_pct
    exit_price = entry * (1.0 + gross_return_pct / 100.0)
    entry_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    settled_at = (entry_at + timedelta(hours=_HORIZON_HOURS[horizon])).isoformat()
    db.execute(
        """INSERT INTO filter_candidate_outcomes (
            id,outcome_candidate_key,venue,symbol,market_type,direction,scanner_family,
            horizon,entry_price_usd,entry_ts,due_at,settled_at,exit_price_usd,
            gross_return_pct,directional_return_pct,estimated_fee_pct,
            estimated_spread_slippage_pct,estimated_funding_pct,net_return_pct,
            min_tradable_edge_pct,settlement_price_source,settlement_lag_sec,
            cost_model_version,status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"canonical-{horizon}-{row['id']}",
            row["outcome_candidate_key"],
            venue,
            row["symbol"],
            row["market_type"],
            row["direction"],
            row["scanner_family"],
            horizon,
            entry,
            entry_at.isoformat(),
            settled_at,
            settled_at,
            exit_price,
            gross_return_pct,
            net_return_pct,
            0.0,
            0.0,
            0.0,
            net_return_pct,
            0.0,
            "horizon_ohlcv",
            0.0,
            "shadow-test-v1",
            "settled",
        ),
    )
    db.commit()
    return {
        "exit_price": exit_price,
        "gross_return_pct": gross_return_pct,
        "directional_return_pct": net_return_pct,
        "net_return_pct": net_return_pct,
        "settled_at": settled_at,
        "venue": venue,
        "settlement_price_source": "horizon_ohlcv",
        "settlement_lag_sec": 0.0,
        "cost_model_version": "shadow-test-v1",
    }
