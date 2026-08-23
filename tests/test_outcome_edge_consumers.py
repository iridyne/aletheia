"""Profitability consumers must exclude outcomes without a valid safety edge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from typer.testing import CliRunner


def _insert_outcome(
    outcome_id: str,
    edge: object,
    *,
    net_return_pct: object = 0.1,
    cost_model_version: object = "v1",
    market_type: object = "spot",
    directional_return_pct: object = 0.1,
    estimated_fee_pct: object = 0.0,
    estimated_spread_slippage_pct: object = 0.0,
    estimated_funding_pct: object = 0.0,
    direction: str = "long",
) -> None:
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    if isinstance(directional_return_pct, (int, float)):
        price_return_pct = (
            directional_return_pct if direction == "long" else -directional_return_pct
        )
        exit_price_usd = 100.0 * (1.0 + price_return_pct / 100.0)
    else:
        exit_price_usd = None
    get_db().execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, venue, scanner_family, market_type, direction, "
        "horizon, entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, forward_return_pct, "
        "gross_return_pct, directional_return_pct, estimated_fee_pct, "
        "estimated_spread_slippage_pct, estimated_funding_pct, net_return_pct, "
        "min_tradable_edge_pct, cost_model_version, settlement_price_source, "
        "settlement_lag_sec, status) "
        "VALUES (?, ?, 'edge-snapshot', 'BTC/USDT', 'okx', 'momentum', ?, ?, "
        "'24h', 100, ?, ?, ?, ?, 0.1, 0.1, ?, ?, ?, ?, ?, ?, ?, "
        "'horizon_ohlcv', 0, 'settled')",
        (
            outcome_id, f"member-{outcome_id}", market_type, direction,
            exit_price_usd, now, now, now,
            directional_return_pct, estimated_fee_pct,
            estimated_spread_slippage_pct, estimated_funding_pct,
            net_return_pct, edge, cost_model_version,
        ),
    )


def test_operator_diagnostics_exclude_incomplete_safety_edges():
    from aletheia.agent_shell.trading_context import _get_data_health_summary
    from aletheia.diagnostics import doctor
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    _insert_outcome("complete", 0.05)
    _insert_outcome("legacy-complete", 0.05, cost_model_version="legacy-v0")
    _insert_outcome("missing", None)
    _insert_outcome("negative", -0.01)
    _insert_outcome("infinite", float("inf"))
    _insert_outcome("malformed", "malformed")
    _insert_outcome("blob", b"0.05")
    _insert_outcome("max-finite", float.fromhex("0x1.fffffffffffffp+1023"))
    _insert_outcome("missing-cost-model", 0.05, cost_model_version=None)
    _insert_outcome("blank-cost-model", 0.05, cost_model_version=" ")
    _insert_outcome("tab-cost-model", 0.05, cost_model_version="\t")
    _insert_outcome("unicode-space-cost-model", 0.05, cost_model_version="\N{NO-BREAK SPACE}")
    _insert_outcome("blob-cost-model", 0.05, cost_model_version=b"v1")
    _insert_outcome("malformed-net", 0.05, net_return_pct="malformed")
    _insert_outcome("blob-net", 0.05, net_return_pct=b"0.1")
    _insert_outcome("missing-fee", 0.05, estimated_fee_pct=None)
    _insert_outcome("mismatched-net", 0.05, net_return_pct=0.2)
    _insert_outcome("unknown-market", 0.05, market_type="crypto")
    _insert_outcome("blob-market", 0.05, market_type=b"spot")
    _insert_outcome(
        "spot-funding", 0.05, directional_return_pct=0.2,
        estimated_funding_pct=0.1,
    )
    get_db().commit()

    summary = _get_data_health_summary()
    assert summary["signal_win_rate_24h"] == 50.0
    assert summary["cost_model_version"] == "v1"

    report = doctor.Report(generated_at="test")
    doctor._probe_signal_effectiveness(report)
    signal = next(check for check in report.checks if check.name == "data.signal")
    assert "(2 settled" in signal.detail
    assert "50.0%" in signal.detail
    assert "cost_model=v1" in signal.detail

    result = CliRunner().invoke(app, ["audit", "data", "--json"])
    assert result.exit_code == 0, result.output
    effectiveness = json.loads(result.output)["signal_effectiveness_7d"]
    assert effectiveness["settled_outcomes"] == 2
    assert effectiveness["win_rate_pct"] == 50.0
    assert effectiveness["cost_model_version"] == "v1"


def test_sql_cost_model_whitespace_matches_python_strip():
    from aletheia.storage.sqlite import SQLITE_PYTHON_STRIP_CHARS_SQL, get_db

    whitespace = "".join(chr(codepoint) for codepoint in range(0x110000) if chr(codepoint).isspace())
    stripped = get_db().execute(
        f"SELECT TRIM(?, {SQLITE_PYTHON_STRIP_CHARS_SQL})",
        (whitespace,),
    ).fetchone()[0]
    assert stripped == ""


def test_sql_and_python_accept_the_same_reconciled_large_returns():
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.storage.repositories import complete_outcome_economics_sql
    from aletheia.storage.sqlite import get_db

    values = (
        (100.0, "long"),
        (100.0001, "long"),
        (200.0, "long"),
        (-100.0, "short"),
        (-100.0001, "short"),
        (-250.0, "short"),
    )
    for index, (value, direction) in enumerate(values):
        _insert_outcome(
            f"large-{index}", 0.05,
            directional_return_pct=value,
            net_return_pct=value,
            direction=direction,
        )

    rows = get_db().execute(
        "SELECT *, "
        f"CASE WHEN {complete_outcome_economics_sql()} THEN 1 ELSE 0 END AS sql_eligible "
        "FROM universe_forward_outcomes WHERE id LIKE 'large-%' ORDER BY id"
    ).fetchall()

    assert len(rows) == len(values)
    for raw in rows:
        row = dict(raw)
        assert validated_outcome_economics(row) is not None
        assert row["sql_eligible"] == 1


def test_sql_and_python_reject_directional_returns_not_reconciled_to_prices():
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.storage.repositories import complete_outcome_economics_sql
    from aletheia.storage.sqlite import get_db

    base = {
        "venue": "okx",
        "direction": "long",
        "entry_price_usd": 100.0,
        "exit_price_usd": 100.1,
        "directional_return_pct": 0.1,
        "estimated_fee_pct": 0.0,
        "estimated_spread_slippage_pct": 0.0,
        "estimated_funding_pct": 0.0,
        "net_return_pct": 0.1,
        "min_tradable_edge_pct": 0.05,
        "market_type": "spot",
        "cost_model_version": "v1",
        "horizon": "1h",
        "settlement_price_source": "horizon_ohlcv",
        "settlement_lag_sec": 0.0,
    }
    fields = tuple(base)
    aliases = ", ".join(f"? AS {field}" for field in fields)

    def eligibility(row: dict[str, object]) -> tuple[bool, int]:
        sql_eligible = get_db().execute(
            "SELECT CASE WHEN "
            f"{complete_outcome_economics_sql()} THEN 1 ELSE 0 END "
            f"FROM (SELECT {aliases})",
            tuple(row[field] for field in fields),
        ).fetchone()[0]
        return validated_outcome_economics(row) is not None, sql_eligible

    invalid_mutations = (
        ("missing-venue", "venue", None),
        ("blank-venue", "venue", " \t"),
        ("blob-venue", "venue", b"okx"),
        ("forged", "exit_price_usd", 100.0),
        ("missing-exit", "exit_price_usd", None),
        ("text-exit", "exit_price_usd", "100.1"),
        ("blob-exit", "exit_price_usd", b"100.1"),
        ("infinite-exit", "exit_price_usd", float("inf")),
        ("zero-exit", "exit_price_usd", 0.0),
        ("negative-exit", "exit_price_usd", -1.0),
        ("missing-entry", "entry_price_usd", None),
        ("text-entry", "entry_price_usd", "100"),
        ("blob-entry", "entry_price_usd", b"100"),
        ("infinite-entry", "entry_price_usd", float("inf")),
        ("zero-entry", "entry_price_usd", 0.0),
        ("negative-entry", "entry_price_usd", -1.0),
        ("unknown-direction", "direction", "flat"),
    )
    for _, field, value in invalid_mutations:
        assert eligibility({**base, field: value}) == (False, 0)

    assert eligibility({**base, "exit_price_usd": 100.1 + 5e-9}) == (True, 1)
    assert eligibility({**base, "exit_price_usd": 100.1 + 2e-8}) == (False, 0)

    direct = dict(base)
    direct["entry_price_usd"] = True
    assert validated_outcome_economics(direct) is None


def test_sql_and_python_enforce_the_same_settlement_price_provenance():
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.storage.repositories import complete_outcome_economics_sql
    from aletheia.storage.sqlite import get_db

    base = {
        "venue": "okx",
        "direction": "long",
        "horizon": "1h",
        "settlement_price_source": "near_due_ticker",
        "settlement_lag_sec": 360.0,
        "entry_price_usd": 100.0,
        "exit_price_usd": 100.1,
        "directional_return_pct": 0.1,
        "estimated_fee_pct": 0.0,
        "estimated_spread_slippage_pct": 0.0,
        "estimated_funding_pct": 0.0,
        "net_return_pct": 0.1,
        "min_tradable_edge_pct": 0.05,
        "market_type": "spot",
        "cost_model_version": "v1",
    }
    fields = tuple(base)
    aliases = ", ".join(f"? AS {field}" for field in fields)

    def eligibility(row: dict[str, object]) -> tuple[bool, int]:
        sql_eligible = get_db().execute(
            "SELECT CASE WHEN "
            f"{complete_outcome_economics_sql()} THEN 1 ELSE 0 END "
            f"FROM (SELECT {aliases})",
            tuple(row[field] for field in fields),
        ).fetchone()[0]
        return validated_outcome_economics(row) is not None, sql_eligible

    assert eligibility(base) == (True, 1)
    for source in ("horizon_ohlcv", "horizon_ohlcv_local"):
        assert eligibility({
            **base,
            "settlement_price_source": source,
            "settlement_lag_sec": 86_400.0,
        }) == (True, 1)
    for horizon, lag in (("15m", 90.0), ("4h", 1440.0), ("24h", 8640.0)):
        assert eligibility({**base, "horizon": horizon, "settlement_lag_sec": lag}) == (
            True,
            1,
        )

    invalid_mutations = (
        ("settlement_price_source", "late_ticker"),
        ("settlement_price_source", "HORIZON_OHLCV"),
        ("settlement_price_source", ""),
        ("settlement_price_source", None),
        ("settlement_price_source", b"horizon_ohlcv"),
        ("settlement_lag_sec", 360.000001),
        ("settlement_lag_sec", -0.001),
        ("settlement_lag_sec", None),
        ("settlement_lag_sec", "0"),
        ("settlement_lag_sec", b"0"),
        ("settlement_lag_sec", float("nan")),
        ("settlement_lag_sec", float("inf")),
        ("horizon", "2h"),
    )
    for field, value in invalid_mutations:
        assert eligibility({**base, field: value}) == (False, 0), (field, value)
    assert validated_outcome_economics({**base, "settlement_lag_sec": True}) is None


def test_keyed_shadow_net_requires_exact_canonical_24h_projection():
    from unittest.mock import patch

    from aletheia.counterfactual.trades import list_deduped_shadows
    from aletheia.counterfactual.trades import persist_shadow_batch
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from tests.shadow_outcome_fixture import (
        insert_canonical_outcome_for_shadow,
        settle_pending_keyed_shadows,
    )

    candidate = {
        "opportunity_id": "shadow-authority",
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "direction": "long",
        "scanner_family": "momentum",
        "trade_score": 60.0,
    }
    with patch("aletheia.counterfactual.trades._current_price", return_value=100.0):
        shadow_id = persist_shadow_batch([candidate], candidate)[0]
    settle_pending_keyed_shadows(1.0)

    def rows() -> list[dict]:
        return list_deduped_shadows(days=9999, columns=("id", "net_pnl_pct"))

    assert rows() == [{"id": shadow_id, "net_pnl_pct": 1.0}]

    db = get_db()
    original = dict(db.execute(
        "SELECT opportunity_id,venue,symbol,market_type,direction,entry_price,"
        "created_at,resolved_at "
        "FROM shadow_trades WHERE id=?",
        (shadow_id,),
    ).fetchone())
    canonical_entry_at = db.execute(
        "SELECT entry_ts FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon='24h'",
        (f"opportunity:{candidate['opportunity_id']}",),
    ).fetchone()["entry_ts"]
    aligned_boundary = (
        datetime.fromisoformat(str(canonical_entry_at).replace("Z", "+00:00"))
        + timedelta(seconds=60)
    ).isoformat()
    db.execute(
        "UPDATE shadow_trades SET created_at=? WHERE id=?",
        (aligned_boundary, shadow_id),
    )
    assert rows() == [{"id": shadow_id, "net_pnl_pct": 1.0}]
    db.execute(
        "UPDATE shadow_trades SET created_at=? WHERE id=?",
        (original["created_at"], shadow_id),
    )
    late_created_at = (
        datetime.fromisoformat(str(original["created_at"]).replace("Z", "+00:00"))
        + timedelta(hours=12)
    ).isoformat()
    for field, forged in (
        ("opportunity_id", "borrowed-canonical-victim"),
        ("venue", "borrowed-venue"),
        ("symbol", "ETH/USDT"),
        ("market_type", "perp"),
        ("direction", "short"),
        ("entry_price", 101.0),
        ("created_at", late_created_at),
        ("resolved_at", "2099-01-01T00:00:00+00:00"),
    ):
        db.execute(f"UPDATE shadow_trades SET {field}=? WHERE id=?", (forged, shadow_id))
        assert rows() == [], field
        db.execute(
            f"UPDATE shadow_trades SET {field}=? WHERE id=?",
            (original[field], shadow_id),
        )

    db.execute("UPDATE shadow_trades SET net_pnl_pct=2.0 WHERE id=?", (shadow_id,))
    assert rows() == []
    db.execute(
        "UPDATE shadow_trades SET net_pnl_pct=1.0,settlement_price_source='late_ticker' "
        "WHERE id=?",
        (shadow_id,),
    )
    assert rows() == []
    db.execute(
        "UPDATE shadow_trades SET settlement_price_source='horizon_ohlcv' WHERE id=?",
        (shadow_id,),
    )

    insert_canonical_outcome_for_shadow(
        shadow_id,
        horizon="4h",
        net_return_pct=0.5,
    )
    assert repo.get_decision_grade_shadow_outcome(shadow_id, "24h") is not None
    assert repo.get_decision_grade_shadow_outcome(shadow_id, "4h") is not None
    db.execute("UPDATE shadow_trades SET net_pnl_pct=2.0 WHERE id=?", (shadow_id,))
    assert repo.get_decision_grade_shadow_outcome(shadow_id, "24h") is None
    assert repo.get_decision_grade_shadow_outcome(shadow_id, "4h") is not None
    db.execute("UPDATE shadow_trades SET net_pnl_pct=1.0 WHERE id=?", (shadow_id,))
    with patch("aletheia.counterfactual.trades._current_price", return_value=100.0):
        late_shadow_id = persist_shadow_batch([candidate], candidate)[0]
    db.execute(
        "UPDATE shadow_trades SET created_at=? WHERE id=?",
        (late_created_at, late_shadow_id),
    )
    settle_pending_keyed_shadows(1.0)
    late_shadow = dict(db.execute(
        "SELECT resolved_at,net_pnl_pct FROM shadow_trades WHERE id=?",
        (late_shadow_id,),
    ).fetchone())
    assert late_shadow == {"resolved_at": None, "net_pnl_pct": None}
    assert rows() == [{"id": shadow_id, "net_pnl_pct": 1.0}]
    assert repo.get_decision_grade_shadow_outcome(late_shadow_id, "4h") is None

    db.execute("DELETE FROM filter_candidate_outcomes")
    assert rows() == []


def test_linked_projection_settles_all_time_aligned_same_key_shadows():
    from unittest.mock import patch

    from aletheia.counterfactual.trades import persist_shadow_batch
    from aletheia.storage.sqlite import get_db
    from tests.shadow_outcome_fixture import settle_pending_keyed_shadows

    candidate = {
        "opportunity_id": "shadow-many-to-one",
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "direction": "long",
        "scanner_family": "momentum",
        "trade_score": 60.0,
    }
    with patch("aletheia.counterfactual.trades._current_price", return_value=100.0):
        first_id = persist_shadow_batch([candidate], candidate)[0]
        second_id = persist_shadow_batch([candidate], candidate)[0]
    db = get_db()
    first_created_at = db.execute(
        "SELECT created_at FROM shadow_trades WHERE id=?", (first_id,)
    ).fetchone()["created_at"]
    aligned_created_at = (
        datetime.fromisoformat(str(first_created_at).replace("Z", "+00:00"))
        + timedelta(seconds=30)
    ).isoformat()
    db.execute(
        "UPDATE shadow_trades SET created_at=? WHERE id=?",
        (aligned_created_at, second_id),
    )

    settle_pending_keyed_shadows(1.0)

    assert db.execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key='opportunity:shadow-many-to-one' "
        "AND horizon='24h'"
    ).fetchone()[0] == 1
    rows = db.execute(
        "SELECT id,exit_price,pnl_pct,gross_return_pct,net_pnl_pct,"
        "estimated_fee_pct,estimated_spread_slippage_pct,estimated_funding_pct,"
        "min_tradable_edge_pct,settlement_price_source,settlement_lag_sec,"
        "cost_model_version,resolved_at FROM shadow_trades "
        "WHERE id IN (?,?) ORDER BY id",
        (first_id, second_id),
    ).fetchall()
    canonical = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key='opportunity:shadow-many-to-one' "
        "AND horizon='24h'"
    ).fetchone()
    assert {row["id"] for row in rows} == {first_id, second_id}
    for row in rows:
        assert row["exit_price"] == canonical["exit_price_usd"]
        assert row["pnl_pct"] == canonical["directional_return_pct"]
        assert row["gross_return_pct"] == canonical["gross_return_pct"]
        assert row["net_pnl_pct"] == canonical["net_return_pct"]
        assert row["estimated_fee_pct"] == canonical["estimated_fee_pct"]
        assert (
            row["estimated_spread_slippage_pct"]
            == canonical["estimated_spread_slippage_pct"]
        )
        assert row["estimated_funding_pct"] == canonical["estimated_funding_pct"]
        assert row["min_tradable_edge_pct"] == canonical["min_tradable_edge_pct"]
        assert row["settlement_price_source"] == canonical["settlement_price_source"]
        assert row["settlement_lag_sec"] == canonical["settlement_lag_sec"]
        assert row["cost_model_version"] == canonical["cost_model_version"]
        assert row["resolved_at"] == canonical["settled_at"]


def test_public_shadow_diagnostics_sanitize_unauthorized_results():
    from unittest.mock import patch

    from aletheia.cli.inspect_cmd import inspect_app
    from aletheia.counterfactual.trades import (
        list_shadow_trade_diagnostics,
        persist_shadow_batch,
    )
    from aletheia.server.routes.data import get_shadow_trades
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import ShadowTrade
    from aletheia.storage.sqlite import get_db
    from tests.shadow_outcome_fixture import (
        insert_canonical_outcome_for_shadow,
        settle_pending_keyed_shadows,
    )

    def persist(opportunity_id: str) -> str:
        candidate = {
            "opportunity_id": opportunity_id,
            "symbol": "BTC/USDT",
            "venue": "bitget",
            "direction": "long",
            "scanner_family": "momentum",
            "trade_score": 60.0,
        }
        with patch("aletheia.counterfactual.trades._current_price", return_value=100.0):
            return persist_shadow_batch([candidate], candidate)[0]

    valid_id = persist("diagnostic-valid")
    settle_pending_keyed_shadows(1.0)
    pending_id = persist("diagnostic-pending")
    orphan_id = persist("diagnostic-orphan")
    invalid_id = persist("diagnostic-invalid")
    insert_canonical_outcome_for_shadow(
        invalid_id,
        horizon="24h",
        net_return_pct=1.0,
    )
    repo.settle_linked_shadow_outcomes("opportunity:diagnostic-invalid")

    now = datetime.now(timezone.utc).isoformat()
    repo.insert_shadow_trade(ShadowTrade(
        id="diagnostic-legacy",
        opportunity_id="diagnostic-legacy",
        symbol="BTC/USDT",
        venue="bitget",
        direction="long",
        entry_price=100.0,
        exit_price=102.0,
        pnl_pct=2.0,
        resolved_at=now,
    ))
    repo.insert_shadow_trade(ShadowTrade(
        id="diagnostic-blank-key-legacy",
        opportunity_id="diagnostic-blank-key-legacy",
        outcome_candidate_key=" \t",
        symbol="BTC/USDT",
        venue="bitget",
        direction="long",
        entry_price=100.0,
        exit_price=104.0,
        pnl_pct=4.0,
        resolved_at=now,
    ))
    db = get_db()
    db.execute(
        "UPDATE shadow_trades SET exit_price=105,pnl_pct=5 WHERE id=?",
        (pending_id,),
    )
    db.execute(
        "UPDATE shadow_trades SET exit_price=109,pnl_pct=9,gross_return_pct=9,"
        "net_pnl_pct=9,estimated_fee_pct=0,estimated_spread_slippage_pct=0,"
        "estimated_funding_pct=0,min_tradable_edge_pct=0,"
        "settlement_price_source='horizon_ohlcv',settlement_lag_sec=0,"
        "cost_model_version='forged',btc_return_pct=3,resolved_at=? WHERE id=?",
        (now, orphan_id),
    )
    db.execute(
        "UPDATE shadow_trades SET settlement_price_source='late_ticker' WHERE id=?",
        (invalid_id,),
    )
    db.execute(
        "UPDATE filter_candidate_outcomes SET settlement_price_source='late_ticker' "
        "WHERE outcome_candidate_key='opportunity:diagnostic-invalid' "
        "AND horizon='24h'",
    )
    db.commit()

    result_fields = (
        "exit_price", "pnl_pct", "gross_return_pct", "net_pnl_pct",
        "estimated_fee_pct", "estimated_spread_slippage_pct",
        "estimated_funding_pct", "min_tradable_edge_pct",
        "settlement_price_source", "settlement_lag_sec", "cost_model_version",
        "btc_return_pct", "resolved_at",
    )
    raw = {row["id"]: row for row in repo.list_shadow_trades(limit=20)}
    assert raw[orphan_id]["pnl_pct"] == 9
    assert raw[orphan_id]["resolved_at"] == now
    assert raw[invalid_id]["settlement_price_source"] == "late_ticker"

    projected = {
        row["id"]: row for row in list_shadow_trade_diagnostics(limit=20)
    }
    assert projected[valid_id]["authority_status"] == "canonical_24h"
    assert projected[valid_id]["authorized"] is True
    assert projected[valid_id]["pnl_pct"] == 1.0
    assert projected[pending_id]["authority_status"] == "pending"
    assert projected[pending_id]["authority_reason"] == "canonical_24h_pending"
    assert all(projected[pending_id][field] is None for field in result_fields)
    for row_id in (orphan_id, invalid_id):
        assert projected[row_id]["authority_status"] == "unauthorized"
        assert projected[row_id]["authority_reason"] == (
            "canonical_24h_projection_invalid"
        )
        assert all(projected[row_id][field] is None for field in result_fields)
    legacy = projected["diagnostic-legacy"]
    assert legacy["authority_status"] == "legacy_observation"
    assert legacy["authorized"] is False
    assert legacy["authority_reason"] == "legacy_unkeyed_shadow"
    assert legacy["pnl_pct"] == 2.0
    assert legacy["resolved_at"] == now
    blank_key_legacy = projected["diagnostic-blank-key-legacy"]
    assert blank_key_legacy["authority_status"] == "legacy_observation"
    assert blank_key_legacy["pnl_pct"] == 4.0
    assert blank_key_legacy["resolved_at"] == now

    api_rows = {row["id"]: row for row in get_shadow_trades(limit=20)}
    assert api_rows == projected
    resolved_rows = {
        row["id"]: row for row in get_shadow_trades(resolved=True, limit=20)
    }
    assert pending_id not in resolved_rows
    assert resolved_rows[orphan_id]["authority_status"] == "unauthorized"
    assert resolved_rows[orphan_id]["pnl_pct"] is None
    unresolved_rows = {
        row["id"]: row for row in get_shadow_trades(resolved=False, limit=20)
    }
    assert set(unresolved_rows) == {pending_id}
    assert unresolved_rows[pending_id]["pnl_pct"] is None

    cli = CliRunner().invoke(inspect_app, ["shadow-trades", "--resolved", "--json"])
    assert cli.exit_code == 0, cli.output
    cli_rows = json.loads(cli.output)
    assert any(row["authority_status"] == "canonical_24h" for row in cli_rows)
    unauthorized = [
        row for row in cli_rows if row["authority_status"] == "unauthorized"
    ]
    assert len(unauthorized) == 2
    assert all(row["pnl_pct"] is None and row["resolved_at"] is None for row in unauthorized)
    assert any(row["authority_status"] == "legacy_observation" for row in cli_rows)


def test_persisted_outlier_policy_not_consumer_cap_controls_large_returns():
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    _insert_outcome(
        "large-valid", 0.05,
        directional_return_pct=200.0,
        net_return_pct=200.0,
    )
    _insert_outcome(
        "physical-outlier", 0.05,
        directional_return_pct=1_000_001.0,
        net_return_pct=1_000_001.0,
    )
    get_db().commit()

    assert repo.mark_outlier_outcomes_failed() == 1
    statuses = {
        row["id"]: row["status"]
        for row in get_db().execute(
            "SELECT id, status FROM universe_forward_outcomes"
        ).fetchall()
    }
    assert statuses == {
        "large-valid": "settled",
        "physical-outlier": "failed",
    }
    calibration = repo.calibration_stats(cost_model_version="v1")
    assert len(calibration) == 1
    assert calibration[0]["n"] == 1
    assert calibration[0]["avg_return"] == 200.0
