from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def _iso(hours_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _cfg(
    *,
    default_notional: float = 25.0,
    vol_enabled: bool = True,
    kelly: float = 0.25,
    risk_cap: float = 200.0,
    persona_enabled: bool = False,
    active_persona: str = "conservative",
):
    return SimpleNamespace(
        trading=SimpleNamespace(
            default_notional_usdt=default_notional,
            vol_sizing_enabled=vol_enabled,
            vol_target_risk_usdt=25.0,
            vol_kelly_cap_fraction=kelly,
            persona_sizing_enabled=persona_enabled,
            active_persona=active_persona,
            personas={},
        ),
        risk=SimpleNamespace(
            max_demo_position_margin_usdt=risk_cap,
            max_demo_single_trade_risk_pct=2.0,
            max_leverage_major_demo=5,
            max_leverage_mid_demo=3,
            max_leverage_alt_demo=2,
            max_futures_leverage_demo=5,
        ),
    )


def _patch_cfg(monkeypatch, cfg):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: cfg)


def _insert_proposal(
    db,
    idx: int,
    *,
    notional: float,
    conviction: int = 70,
    status: str = "executed",
    leverage: int | None = 1,
    strategy_id: str | None = "strat-sizing",
) -> str:
    pid = f"prop-sizing-{idx}"
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, strategy_id, opportunity_id, symbol, market_type, direction, order_type, "
        "quantity, price, leverage, execution_mode, status, created_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', ?, 100, ?, "
        "'bitget_demo', ?, ?)",
        (pid, strategy_id, f"opp-sizing-{idx}", notional / 100.0, leverage, status, _iso()),
    )
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, proposal_id, opportunity_id, created_at) "
        "VALUES (?, 'BTC/USDT', 'long', 'trade', ?, 'test', ?, ?, ?)",
        (f"pi-sizing-{idx}", conviction, pid, f"opp-sizing-{idx}", _iso()),
    )
    return pid


def _insert_position(db, idx: int, *, notional: float, leverage: int | None = 1) -> None:
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, symbol, market_type, direction, order_type, quantity, price, "
        "execution_mode, status, created_at, filled_at) "
        "VALUES (?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', ?, 100, "
        "'bitget_demo', 'filled', ?, ?)",
        (f"order-sizing-{idx}", f"prop-sizing-{idx}", notional / 100.0, _iso(), _iso()),
    )
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, execution_mode, "
        "strategy_id, proposal_id, order_id, status, leverage, opened_at) "
        "VALUES (?, 'BTC/USDT', 'usdt_futures', 'long', ?, 100, 'bitget_demo', "
        "'strat-sizing', ?, ?, 'open', ?, ?)",
        (
            f"pos-sizing-{idx}",
            notional / 100.0,
            f"prop-sizing-{idx}",
            f"order-sizing-{idx}",
            leverage,
            _iso(),
        ),
    )


def _insert_risk_rejection(db, idx: int, reason: str) -> None:
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, risk_score, violations, message, created_at) "
        "VALUES (?, ?, 0, 1.0, ?, ?, ?)",
        (f"risk-sizing-{idx}", f"prop-sizing-{idx}", f'["{reason}"]', reason, _iso()),
    )


def test_sizing_trace_empty_db_returns_insufficient_data(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit

    _patch_cfg(monkeypatch, _cfg())

    report = build_sizing_trace_audit(hours=24)

    assert report["summary"]["sample_size"] == 0
    assert report["main_sizing_bottleneck"]["severity"] == "insufficient_data"
    assert report["traces"] == []


def test_sizing_trace_missing_fills_table_still_uses_positions(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=25.0, conviction=70)
    _insert_position(db, 1, notional=25.0)
    db.execute("DROP TABLE fills")
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    assert report["summary"]["sample_size"] == 1
    assert report["traces"][0]["executed_notional_usdt"] == 25.0
    assert report["schema_errors"]


def test_sizing_trace_detects_low_conviction(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=12.0, conviction=35)
    _insert_position(db, 1, notional=12.0)
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    assert report["traces"][0]["main_reason"] == "low_conviction"
    assert report["compression_reasons"][0]["reason"] == "low_conviction"


def test_sizing_trace_detects_low_base_notional(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(default_notional=2.0, vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=2.0, conviction=75)
    _insert_position(db, 1, notional=2.0)
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    assert report["traces"][0]["base_notional_usdt"] == 2.0
    assert report["traces"][0]["main_reason"] == "low_base_notional"


def test_sizing_trace_detects_sample_aware_scaling(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(vol_enabled=True, kelly=0.25))
    db = get_db()
    _insert_proposal(db, 1, notional=10.0, conviction=70, strategy_id="new-strategy")
    _insert_position(db, 1, notional=10.0)
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    trace = report["traces"][0]
    assert trace["paper_trade_count"] == 0
    assert trace["effective_kelly"] == 0.1
    assert trace["main_reason"] == "sample_aware_scaling"
    assert trace["effective_scaling_fraction"] == 0.1
    assert trace["legacy_parameter_name"] == "kelly_fraction"


def test_sizing_trace_detects_risk_cap(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(risk_cap=5.0))
    db = get_db()
    _insert_proposal(db, 1, notional=5.0, conviction=80, status="rejected")
    _insert_risk_rejection(db, 1, "max_demo_position_margin_usdt")
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    assert report["traces"][0]["risk_cap_hit"] is True
    assert report["traces"][0]["main_reason"] == "risk_cap"


def test_sizing_trace_marks_current_config_reconstruction(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=25.0, conviction=70)
    _insert_position(db, 1, notional=25.0)
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    assert "reconstructed_from_current_config" in report["traces"][0]["notes"]
    assert report["traces"][0]["intended_position_size_usdt"] == 25.0
    assert report["traces"][0]["current_policy_projection_usdt"] is not None


def test_sizing_trace_prefers_proposal_fills_over_later_position_size(monkeypatch):
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=25.0, conviction=70)
    _insert_position(db, 1, notional=50.0)
    db.execute(
        "INSERT INTO fills (id, order_id, fill_price, fill_quantity, filled_at) "
        "VALUES ('fill-sizing-1', 'order-sizing-1', 100, 0.25, ?)",
        (_iso(),),
    )
    db.commit()

    report = build_sizing_trace_audit(hours=24, min_sample_size=1)

    trace = report["traces"][0]
    assert trace["proposal_notional_usdt"] == 25.0
    assert trace["executed_notional_usdt"] == 25.0


def test_sizing_trace_render_labels_current_config_projection(monkeypatch):
    from aletheia.analytics.sizing_trace import (
        build_sizing_trace_audit,
        render_sizing_trace_audit,
    )
    from aletheia.storage.sqlite import get_db

    _patch_cfg(monkeypatch, _cfg(default_notional=100.0, vol_enabled=False))
    db = get_db()
    _insert_proposal(db, 1, notional=25.0, conviction=70)
    _insert_position(db, 1, notional=25.0)
    db.commit()

    rendered = render_sizing_trace_audit(build_sizing_trace_audit(hours=24, min_sample_size=1))

    assert "median_proposal_notional: 25.0U" in rendered
    assert "median_current_policy_projection:" in rendered
    assert "Current-policy projection bottleneck:" in rendered


def test_sizing_trace_cli_outputs_human_and_json_reports(monkeypatch):
    import json
    from typer.testing import CliRunner

    from aletheia.main import app

    _patch_cfg(monkeypatch, _cfg(vol_enabled=False))

    runner = CliRunner()
    human = runner.invoke(app, ["sizing", "trace", "--hours", "24"])
    machine = runner.invoke(app, ["sizing", "trace", "--days", "7", "--json"])

    assert human.exit_code == 0
    assert "Sizing Trace Audit" in human.output
    assert machine.exit_code == 0
    payload = json.loads(machine.output)
    assert payload["window"]["days"] == 7
    assert "main_sizing_bottleneck" in payload
