"""Small canonical settlement fixture for keyed shadow consumer tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

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


def settle_pending_keyed_shadows(net_return_pct: float) -> None:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM shadow_trades WHERE outcome_candidate_key IS NOT NULL "
        "AND net_pnl_pct IS NULL "
        "ORDER BY outcome_candidate_key, datetime(created_at), id"
    ).fetchall()
    settled_keys: set[str] = set()
    for raw in rows:
        row = dict(raw)
        key = str(row["outcome_candidate_key"])
        if key in settled_keys:
            continue
        settled_keys.add(key)
        project_keyed_shadow(str(row["id"]), horizon="24h", net_return_pct=net_return_pct)
    db.commit()


# --- retired-producer stand-ins -------------------------------------------------
# #3704 retired the shadow_trades write path (persist_shadow_batch and its
# repository layer) after its sole production caller was deleted, so fixtures
# build the rows the surviving readers consume instead of calling a producer.

_PROJECT_SQL = """
UPDATE shadow_trades SET (
    exit_price, pnl_pct, gross_return_pct, net_pnl_pct, estimated_fee_pct,
    estimated_spread_slippage_pct, estimated_funding_pct, min_tradable_edge_pct,
    min_cost_covering_edge_pct, settlement_price_source, settlement_lag_sec,
    cost_model_version, resolved_at
) = (
    SELECT
        exit_price_usd, directional_return_pct, gross_return_pct, net_return_pct,
        estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct,
        min_tradable_edge_pct, min_cost_covering_edge_pct, settlement_price_source,
        settlement_lag_sec, cost_model_version, settled_at
    FROM filter_candidate_outcomes AS canonical_outcome
    WHERE canonical_outcome.outcome_candidate_key = shadow_trades.outcome_candidate_key
      AND canonical_outcome.horizon = ?
      AND canonical_outcome.status = 'settled'
)
WHERE outcome_candidate_key = ? AND resolved_at IS NULL
  AND EXISTS (
    SELECT 1 FROM filter_candidate_outcomes AS canonical_outcome
    WHERE canonical_outcome.outcome_candidate_key = shadow_trades.outcome_candidate_key
      AND canonical_outcome.horizon = ?
      AND canonical_outcome.status = 'settled'
  )
"""


def insert_shadow_row(
    *,
    opportunity_id: str,
    symbol: str,
    venue: str,
    market_type: str = "spot",
    direction: str = "long",
    scanner_family: str | None = None,
    trade_score: float = -999.0,
    rank: int = 0,
    entry_price: float | None = None,
    entry_spread_bps: float | None = None,
    funding_rate_at_entry: float | None = None,
    was_selected: bool = False,
    batch_id: str | None = None,
    policy_epoch_id: str | None = None,
    regime_tag: str | None = None,
    outcome_candidate_key: str | None = None,
    created_at: str | None = None,
    shadow_id: str | None = None,
) -> str:
    """Insert one ``shadow_trades`` row and return its id."""
    from aletheia.storage.models import new_id

    shadow_id = shadow_id or new_id()
    db = get_db()
    db.execute(
        """INSERT INTO shadow_trades (
            id,batch_id,opportunity_id,symbol,venue,market_type,direction,
            scanner_family,trade_score,rank,entry_price,entry_spread_bps,
            funding_rate_at_entry,outcome_candidate_key,regime_tag,was_selected,
            created_at,policy_epoch_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            shadow_id,
            batch_id,
            opportunity_id,
            symbol,
            venue,
            market_type,
            direction,
            scanner_family,
            trade_score,
            rank,
            entry_price,
            entry_spread_bps,
            funding_rate_at_entry,
            outcome_candidate_key or f"opportunity:{opportunity_id}",
            regime_tag,
            1 if was_selected else 0,
            created_at or datetime.now(timezone.utc).isoformat(),
            policy_epoch_id,
        ),
    )
    db.commit()
    return shadow_id


def insert_shadow_batch(
    candidates: list[dict],
    selected: dict | list[dict] | None,
    *,
    top_n: int = 5,
    entry_price: float = 100.0,
    policy_epoch_id: str | None = None,
    regime_tag: str | None = None,
) -> list[str]:
    """Insert one shadow batch with the row shape the retired producer wrote.

    ``candidates`` is the ranked candidate list, ``selected`` what was actually
    emitted; rows beyond ``top_n`` are appended when they were selected (the
    forced-selection semantics the propensity reader depends on).
    """
    if not candidates:
        return []
    selected_candidates = selected if isinstance(selected, list) else ([selected] if selected else [])
    selected_opp_ids = {
        item.get("opportunity_id") for item in selected_candidates if item.get("opportunity_id")
    }
    rank_by_opportunity = {
        candidate.get("opportunity_id"): rank
        for rank, candidate in enumerate(candidates, start=1)
        if candidate.get("opportunity_id")
    }
    batch_candidates = list(candidates[:top_n])
    included = {c.get("opportunity_id") for c in batch_candidates if c.get("opportunity_id")}
    for candidate in selected_candidates:
        opp_id = candidate.get("opportunity_id")
        if opp_id and opp_id not in included:
            batch_candidates.append(candidate)
            included.add(opp_id)

    batch_id = uuid4().hex[:12]
    inserted: list[str] = []
    for fallback_rank, candidate in enumerate(batch_candidates, start=1):
        opp_id = candidate.get("opportunity_id")
        inserted.append(
            insert_shadow_row(
                opportunity_id=str(opp_id or ""),
                symbol=str(candidate.get("symbol") or ""),
                venue=str(candidate.get("venue") or ""),
                market_type=str(candidate.get("market_type") or ""),
                direction=str(candidate.get("direction") or "long"),
                scanner_family=candidate.get("scanner_family"),
                trade_score=float(candidate.get("trade_score") or -999),
                rank=rank_by_opportunity.get(opp_id, fallback_rank),
                entry_price=entry_price,
                was_selected=bool(opp_id and opp_id in selected_opp_ids),
                batch_id=batch_id,
                policy_epoch_id=policy_epoch_id,
                regime_tag=regime_tag,
            )
        )
    return inserted


def project_keyed_shadow(
    shadow_id: str,
    *,
    horizon: str = "24h",
    net_return_pct: float,
) -> int:
    """Project the canonical settled outcome of ``shadow_id`` onto its keyed shadows.

    Stands in for the retired ``settle_linked_shadow_outcomes``: the surviving
    readers only accept a shadow row whose result columns equal its canonical
    settled FCO row (``valid_keyed_shadow_projection_sql``).
    """
    insert_canonical_outcome_for_shadow(
        shadow_id,
        horizon=horizon,
        net_return_pct=net_return_pct,
    )
    db = get_db()
    key = db.execute(
        "SELECT outcome_candidate_key FROM shadow_trades WHERE id=?", (shadow_id,)
    ).fetchone()["outcome_candidate_key"]
    cursor = db.execute(_PROJECT_SQL, (horizon, key, horizon))
    db.commit()
    return cursor.rowcount or 0
