"""Build the fixed, sanitized runtime replay v1 SQLite pack.

The source database is opened read-only and is used only to prove that each
represented category exists in real runtime history. No source id, symbol,
timestamp, amount, rationale, path, or free text is copied to the output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


FIXED_TIMES = [f"2026-01-{day:02d}T00:00:00+00:00" for day in range(1, 25)]
SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT")
PRICES = {"BTC/USDT": 60_000.0, "ETH/USDT": 3_000.0, "SOL/USDT": 150.0, "AVAX/USDT": 40.0}


def _count(source: sqlite3.Connection, sql: str) -> int:
    return int(source.execute(sql).fetchone()[0])


def _require_represented_categories(source: sqlite3.Connection) -> None:
    checks = {
        "market snapshots": "SELECT COUNT(*) FROM market_snapshots",
        "universe candidates": "SELECT COUNT(*) FROM universe_members",
        "opportunities": "SELECT COUNT(*) FROM opportunities",
        "trade decisions": "SELECT COUNT(*) FROM pi_decisions WHERE action='trade'",
        "skip decisions": "SELECT COUNT(*) FROM pi_decisions WHERE action='skip'",
        "approved risk decisions": "SELECT COUNT(*) FROM risk_decisions WHERE approved=1",
        "rejected risk decisions": "SELECT COUNT(*) FROM risk_decisions WHERE approved=0",
        "closed winning positions": (
            "SELECT COUNT(*) FROM positions WHERE status='closed' AND realized_pnl>0"
        ),
        "closed losing positions": (
            "SELECT COUNT(*) FROM positions WHERE status='closed' AND realized_pnl<0"
        ),
        "settled positive net outcomes": (
            "SELECT COUNT(*) FROM universe_forward_outcomes "
            "WHERE status='settled' AND net_return_pct>0"
        ),
        "settled negative net outcomes": (
            "SELECT COUNT(*) FROM universe_forward_outcomes "
            "WHERE status='settled' AND net_return_pct<0"
        ),
        "refrozen Learning exposures": (
            "SELECT COUNT(*) FROM learning_exposures "
            "WHERE COALESCE(json_extract(manifest_json,'$.authority_applied'),0)=0"
        ),
    }
    missing = [label for label, sql in checks.items() if _count(source, sql) < 1]
    if missing:
        raise RuntimeError("source runtime lacks replay categories: " + ", ".join(missing))


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    available = {info[1] for info in conn.execute(f"PRAGMA table_info({table})")}
    filtered = {key: value for key, value in row.items() if key in available}
    columns = ", ".join(filtered)
    placeholders = ", ".join("?" for _ in filtered)
    conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(filtered.values()),
    )


def _seed_fixture(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE runtime_replay_manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    manifest = {
        "fixture_version": "runtime_replay_v1",
        "sanitization": "category-preserving-total-replacement-v1",
        "active_scope": "crypto-paper-mainline-v1",
        "source_values_copied": "false",
    }
    conn.executemany(
        "INSERT INTO runtime_replay_manifest(key,value) VALUES (?,?)",
        sorted(manifest.items()),
    )

    for index, symbol in enumerate(SYMBOLS):
        for market_index, market_type in enumerate(("spot", "perp")):
            _insert(conn, "market_snapshots", {
                "id": f"replay-market-{index}-{market_index}",
                "symbol": symbol,
                "market_type": market_type,
                "exchange": "fixture",
                "price": PRICES[symbol] * (1 + market_index * 0.001),
                "volume_24h": 1_000_000_000.0 / (index + 1),
                "change_pct_24h": (-1) ** index * (index + 1.0),
                "provider": "sanitized_replay",
                "snapshot_at": FIXED_TIMES[index],
            })

    _insert(conn, "universe_snapshots", {
        "id": "replay-universe-snapshot",
        "created_at": FIXED_TIMES[0],
        "market_type": "crypto",
        "status": "active",
        "expires_at": FIXED_TIMES[-1],
        "summary": json.dumps({"fixture": True}, sort_keys=True),
    })
    candidate_specs = [
        ("BTC/USDT", "perp", "momentum", "long", 92.0),
        ("ETH/USDT", "perp", "mean_reversion", "short", 86.0),
        ("SOL/USDT", "spot", "volatility", "long", 79.0),
        ("AVAX/USDT", "perp", "derivatives", "short", 73.0),
        ("BTC/USDT", "spot", "core", "long", 68.0),
        ("ETH/USDT", "spot", "event", "long", 62.0),
    ]
    for index, (symbol, market_type, family, direction, score) in enumerate(candidate_specs, 1):
        asset_id = f"replay-asset-{index}"
        member_id = f"replay-member-{index}"
        _insert(conn, "universe_registry", {
            "id": asset_id,
            "venue": "fixture",
            "market_type": market_type,
            "symbol": symbol,
            "base_asset": symbol.split("/")[0],
            "quote_asset": "USDT",
            "status": "active",
            "first_seen_at": FIXED_TIMES[0],
            "last_seen_at": FIXED_TIMES[1],
            "metadata": "{}",
        })
        _insert(conn, "universe_members", {
            "id": member_id,
            "snapshot_id": "replay-universe-snapshot",
            "asset_id": asset_id,
            "symbol": symbol,
            "market_type": market_type,
            "rank": index,
            "total_score": score,
            "opportunity_score": score - 5,
            "tradability_score": 90 - index,
            "liquidity_score": 85 - index,
            "estimated_cost": 1.0 + index / 10,
            "scanner_family": family,
            "scanner_direction": direction,
            "ttl_sec": 3600,
            "expires_at": FIXED_TIMES[-1],
            "execution_eligible": 1,
            "entry_price_usd": PRICES[symbol],
        })

        opportunity_id = f"replay-opportunity-{index}"
        _insert(conn, "opportunities", {
            "id": opportunity_id,
            "symbol": symbol,
            "market": market_type,
            "direction_bias": direction,
            "thesis": "Sanitized replay opportunity.",
            "confidence": "high" if index < 5 else "medium",
            "confidence_score": int(score),
            "source_agent": "market_observer",
            "created_at": FIXED_TIMES[index],
            "status": "reviewed" if index <= 4 else "open",
            "asset_class": "crypto",
            "execution_eligible": 1,
            "scanner_family": family,
            "metadata": json.dumps({
                "learning_candidate_key": f"replay-member-{index}"
            }, sort_keys=True),
        })

    decision_actions = ("trade", "trade", "trade", "trade", "skip", "skip")
    for index, action in enumerate(decision_actions, 1):
        proposal_id = f"replay-proposal-{index}" if action == "trade" else None
        _insert(conn, "pi_decisions", {
            "id": f"replay-decision-{index}",
            "created_at": FIXED_TIMES[index + 1],
            "symbol": candidate_specs[index - 1][0],
            "direction": candidate_specs[index - 1][3],
            "action": action,
            "conviction": int(candidate_specs[index - 1][4]),
            "reasoning": "Sanitized replay decision.",
            "proposal_id": proposal_id,
            "opportunity_id": f"replay-opportunity-{index}",
            "signal_source": "market_observer",
            "debate_ran": 0,
            "bear_case": "Sanitized adverse case.",
            "key_assumptions": "[]",
            "learning_manifest_json": "{}",
        })

    for index in range(1, 5):
        symbol, market_type, _, direction, _ = candidate_specs[index - 1]
        price = PRICES[symbol]
        approved = index <= 3
        _insert(conn, "trade_proposals", {
            "id": f"replay-proposal-{index}",
            "opportunity_id": f"replay-opportunity-{index}",
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "order_type": "limit",
            "quantity": round(25.0 / price, 8),
            "price": price,
            "leverage": 1 if market_type == "spot" else 2,
            "stop_price": price * (0.98 if direction == "long" else 1.02),
            "take_profit": price * (1.04 if direction == "long" else 0.96),
            "risk_reward": 2.0,
            "execution_mode": "paper_internal",
            "agent_rationale": "Sanitized replay rationale.",
            "status": "executed" if approved else "rejected",
            "source": "native",
            "created_at": FIXED_TIMES[index + 2],
        })
        _insert(conn, "risk_decisions", {
            "id": f"replay-risk-{index}",
            "proposal_id": f"replay-proposal-{index}",
            "approved": int(approved),
            "risk_score": 0.1 if approved else 0.9,
            "violations": "[]" if approved else '["sanitized_fixture_rejection"]',
            "allowed_execution_mode": "paper_internal",
            "proposal_execution_mode": "paper_internal",
            "message": "Sanitized replay risk decision.",
            "created_at": FIXED_TIMES[index + 3],
        })
        if not approved:
            continue
        order_id = f"replay-order-{index}"
        fill_id = f"replay-fill-{index}"
        position_id = f"replay-position-{index}"
        quantity = round(25.0 / price, 8)
        _insert(conn, "orders", {
            "id": order_id,
            "proposal_id": f"replay-proposal-{index}",
            "risk_decision_id": f"replay-risk-{index}",
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "order_type": "limit",
            "quantity": quantity,
            "price": price,
            "execution_mode": "paper_internal",
            "status": "filled",
            "source": "native",
            "created_at": FIXED_TIMES[index + 4],
            "filled_at": FIXED_TIMES[index + 4],
        })
        _insert(conn, "fills", {
            "id": fill_id,
            "order_id": order_id,
            "fill_price": price,
            "fill_quantity": quantity,
            "fee": 0.015,
            "fee_currency": "USDT",
            "fee_source": "estimated",
            "filled_at": FIXED_TIMES[index + 4],
        })
        is_closed = index < 3
        realized = 2.0 if index == 1 else -1.5 if index == 2 else 0.0
        _insert(conn, "positions", {
            "id": position_id,
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "quantity": quantity,
            "entry_price": price,
            "current_price": price * (1.02 if index == 1 else 0.98 if index == 2 else 1.0),
            "unrealized_pnl": 0.0,
            "realized_pnl": realized,
            "execution_mode": "paper_internal",
            "proposal_id": f"replay-proposal-{index}",
            "order_id": order_id,
            "opportunity_id": f"replay-opportunity-{index}",
            "status": "closed" if is_closed else "open",
            "close_reason": "take_profit" if index == 1 else "stop_loss" if index == 2 else None,
            # The source runtime has no verified strategy closes yet. Preserve
            # that absence: historical economics remain visible but ineligible.
            "close_category": "unknown",
            "exit_price_source": "unavailable",
            "exit_fill_verified": 0,
            "outcome_eligible": 0,
            "outcome_exclusion_reason": "unclassified",
            "outcome_semantics_version": None,
            "source": "native",
            "opened_at": FIXED_TIMES[index + 4],
            "closed_at": FIXED_TIMES[index + 10] if is_closed else None,
            "entry_fee": 0.015,
            "exit_fee": 0.015 if is_closed else None,
            "slippage_cost": 0.0,
            "trade_school": "short_term",
        })

    for index in range(1, 7):
        symbol, market_type, family, direction, _ = candidate_specs[index - 1]
        net = (2.0, -1.2, 1.1, -0.8, 0.6, -0.4)[index - 1]
        _insert(conn, "universe_forward_outcomes", {
            "id": f"replay-outcome-{index}",
            "outcome_candidate_key": f"replay-member-{index}:4h",
            "member_id": f"replay-member-{index}",
            "snapshot_id": "replay-universe-snapshot",
            "symbol": symbol,
            "venue": "fixture",
            "market_type": market_type,
            "scanner_family": family,
            "direction": direction,
            "horizon": "4h",
            "entry_price_usd": PRICES[symbol],
            "entry_spread_bps": 4.0,
            "entry_ts": FIXED_TIMES[index],
            "due_at": FIXED_TIMES[index + 1],
            "settled_at": FIXED_TIMES[index + 2],
            "exit_price_usd": PRICES[symbol] * (1 + net / 100),
            "gross_return_pct": net + 0.2,
            "directional_return_pct": net + 0.2,
            "estimated_fee_pct": 0.12,
            "estimated_spread_slippage_pct": 0.08,
            "estimated_funding_pct": 0.0,
            "net_return_pct": net,
            "min_tradable_edge_pct": 0.20,
            "mfe_pct": max(0.5, net + 0.5),
            "mae_pct": min(-0.2, net - 0.5),
            "settlement_price_source": "horizon_ohlcv",
            "settlement_lag_sec": 0.0,
            "cost_model_version": "outcome_economics.v1",
            "status": "settled",
        })

    _insert(conn, "learning_hypotheses", {
        "hypothesis_id": "replay-hypothesis",
        "fingerprint": "replay-fingerprint",
        "cohort_id": "replay-cohort",
        "path_id": "universe_family_calibration",
        "overlap_group": "universe_allocation",
        "source_type": "sanitized_runtime_replay",
        "scope_json": "{}",
        "action_json": "{}",
        "consumer": "universe_allocator",
        "target_horizon": "4h",
        "status": "shadow",
        "harm_budget_json": "{}",
        "created_at": FIXED_TIMES[0],
        "updated_at": FIXED_TIMES[0],
    })
    _insert(conn, "learning_experiments", {
        "experiment_id": "replay-experiment",
        "hypothesis_id": "replay-hypothesis",
        "stage": "shadow",
        "assignment_salt": "replay-salt",
        "allocation_pct": 0.5,
        "min_samples_per_arm": 50,
        "min_market_days": 7,
        "primary_metric": "net_return_pct",
        "promotion_gate_json": "{}",
        "harm_gate_json": "{}",
        "status": "registered",
        "created_at": FIXED_TIMES[0],
    })
    for index in range(1, 5):
        value = 80.0 + index
        _insert(conn, "learning_exposures", {
            "exposure_id": f"replay-exposure-{index}",
            "dedup_key": f"replay-experiment:member-{index}:shadow",
            "experiment_id": "replay-experiment",
            "hypothesis_id": "replay-hypothesis",
            "assignment": "treatment" if index % 2 else "control",
            "consumer": "universe_allocator",
            "consumer_stage": "opportunity_score:shadow_refrozen",
            "path_id": "universe_family_calibration",
            "overlap_group": "universe_allocation",
            "candidate_key": f"replay-member-{index}",
            "opportunity_id": f"replay-opportunity-{index}",
            "before_json": json.dumps({"value": value}, sort_keys=True),
            "after_json": json.dumps({"value": value}, sort_keys=True),
            "manifest_json": json.dumps({
                "authority_applied": False,
                "authority_reason": "refrozen_without_prospective_verdict",
                "proposed_value": value * 1.1,
            }, sort_keys=True),
            "outcome_candidate_key": f"replay-member-{index}:4h",
            "outcome_source": "fixed_horizon_candidate",
            "outcome_contract_version": "v1",
            "concurrent_policy_epoch": "replay-policy-epoch",
            "exposed_at": FIXED_TIMES[index],
        })

    for index in range(1, 3):
        _insert(conn, "knowledge_items", {
            "id": f"replay-review-{index}",
            "item_type": "post_trade_review",
            "title": f"Sanitized replay review {index}",
            "confidence": "medium_confidence",
            "status": "active",
            "tags": '["runtime-replay"]',
            "content": "Sanitized replay review content; no source text was copied.",
            "content_hash": f"replay-hash-{index}",
            "created_at": FIXED_TIMES[index + 10],
            "updated_at": FIXED_TIMES[index + 10],
        })


def build(source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        _require_represented_categories(source)
    finally:
        source.close()

    import aletheia.storage.sqlite as storage
    from aletheia.storage.migrations import run_migrations

    storage.close_db()
    original_db_path = storage.db_path
    storage.db_path = lambda: output_path
    try:
        conn = storage.get_db()
        run_migrations()
        conn.execute("BEGIN")
        _seed_fixture(conn)
        conn.commit()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # A committed read-only fixture must be one portable file, not a WAL trio.
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        storage.close_db()
        storage.db_path = original_db_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
