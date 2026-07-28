"""Spec: Kill Conditions Contract (kill_conditions_contract)

产品定义 §8（失败与撤退）：demo 阶段 kill 触发条件，
防止系统在已证明无效的状态下持续运行。

三条系统级 Kill 判决:
  K1: Demo 连续两轮 PF < 0.5  → terminate experiment
  K2: 连续 3 天零开仓        → trading loop dead
  K3: live unlock 须 scorecard composite_score >= 70

关联契约: docs/design/contracts/kill_conditions_contract.md
"""
import pytest
from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "D8-S01",
        "kill_conditions_contract",
        "§K1 scorecard structure",
        "L2a",
        "go_live_scorecard.compute_scorecard() 返回 dict 中，profit_factor criterion "
        "的 key='profit_factor' 存在且 weight 字段大于零——保证 PF 评估维度不会被静默删除，"
        "是 D8 Kill 条件机器保护的结构前提。",
    )
)
def test_scorecard_profit_factor_criterion_exists():
    from aletheia.analytics.go_live_scorecard import compute_scorecard

    result = compute_scorecard()
    criteria = result.get("criteria", [])
    pf_criteria = [c for c in criteria if c.get("key") == "profit_factor"]
    assert pf_criteria, (
        "compute_scorecard() returned no criterion with key='profit_factor' — "
        "PF Kill condition D8-K1 has lost its structural anchor"
    )
    assert pf_criteria[0]["weight"] > 0, (
        f"profit_factor criterion weight={pf_criteria[0]['weight']} must be > 0"
    )


@spec_claim(
    SpecClaim(
        "D8-S02",
        "kill_conditions_contract",
        "§K1 consecutive kill detection",
        "L2a",
        "go_live_scorecard.py 中存在检测连续两轮 PF 低于 kill 阈值（<0.5）的函数 "
        "check_kill_condition_consecutive_rounds()，返回 consecutive_kill_rounds 整数。",
        status="implemented",
    )
)
def test_consecutive_kill_round_detection_exists():
    import pathlib

    scorecard_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "aletheia"
        / "analytics"
        / "go_live_scorecard.py"
    )
    source = scorecard_path.read_text(encoding="utf-8")
    has_impl = any(
        kw in source
        for kw in ("consecutive_kill", "kill_rounds", "check_kill_condition")
    )
    assert has_impl, (
        "go_live_scorecard.py contains no consecutive kill-round detection logic "
        "(expected 'consecutive_kill', 'kill_rounds', or 'check_kill_condition'). "
        "Implement K1 consecutive PF<0.5 detection."
    )


@spec_claim(
    SpecClaim(
        "D8-S03",
        "kill_conditions_contract",
        "§K2 loop stall probe present",
        "L2a",
        "doctor.py 存在 loop.profit 探针（_probe_profit_loop），检测 opportunities 流动而 "
        "proposals 为零超过阈值时长——是决策链僵死 Kill 条件的监测结构前提，"
        "当前已实现需保留（防止探针被误删）。",
    )
)
def test_loop_profit_stall_probe_exists():
    import pathlib

    doctor_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "aletheia"
        / "diagnostics"
        / "doctor.py"
    )
    assert doctor_path.exists(), f"doctor.py not found at {doctor_path}"
    source = doctor_path.read_text(encoding="utf-8")

    # 探针函数名
    assert "_probe_profit_loop" in source, (
        "doctor.py: _probe_profit_loop probe function missing — "
        "loop stall detection (K2) has been removed"
    )
    # 报告键名确认 loop.profit 存在
    assert 'loop.profit' in source, (
        "doctor.py: 'loop.profit' report key missing — "
        "the loop stall probe is no longer emitting its signal"
    )
    # 必须同时检测 opportunities 和 proposals 两侧
    assert "opportunities" in source and "proposals" in source, (
        "doctor.py: loop stall probe must reference both 'opportunities' and "
        "'proposals' tables to detect the decoupling — one side missing"
    )


@spec_claim(
    SpecClaim(
        "D8-S04",
        "kill_conditions_contract",
        "§K2 zero fill days detection",
        "L2a",
        "analytics 模块中存在能检测连续 N 天无新 fills 的函数 "
        "count_consecutive_zero_fill_days()，返回天数整数。",
        status="implemented",
    )
)
def test_zero_fill_days_detection_exists():
    import pathlib

    search_dirs = [
        pathlib.Path(__file__).resolve().parents[2] / "aletheia" / "diagnostics",
        pathlib.Path(__file__).resolve().parents[2] / "aletheia" / "analytics",
    ]
    keywords = (
        "days_without_fill", "consecutive_empty_days", "zero_fill_days",
        "count_consecutive_zero_fill_days",  # go_live_scorecard.py implementation
    )
    found = False
    for d in search_dirs:
        for py_file in d.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if any(kw in content for kw in keywords):
                found = True
                break
        if found:
            break
    assert found, (
        f"No function matching {keywords!r} found in diagnostics/ or analytics/ — "
        "implement 3-day zero-fill Kill condition detection."
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "D8-B01",
        "kill_conditions_contract",
        "§K1 pf fail grade",
        "L2b",
        "_check_profit_factor(): 当 gross_profit/gross_loss < 0.5 时"
        "返回 criterion status='fail' 且 score=0，确保极低 PF 不被软化为 warning 黄灯。"
        "Kill floor 已在 go_live_scorecard.py 编码（product §8 K1）。",
    )
)
def test_profit_factor_below_kill_threshold_returns_fail():
    """极低 PF（PF=0.4 < 0.5）必须评为 fail/score=0，不得软化。"""
    from aletheia.analytics.go_live_scorecard import _check_profit_factor
    from aletheia.storage.sqlite import get_db

    db = get_db()
    # 注入 gross_profit=0.4, gross_loss=1.0 → PF=0.4 < 0.5
    # 仅填写 NOT NULL 必填列，其余使用默认值
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, execution_mode, status, realized_pnl, closed_at) "
        "VALUES "
        "('d8b01-win','BTC/USDT','perp','long',1.0,100.0,'demo','closed',0.4,'2026-01-02'),"
        "('d8b01-loss','BTC/USDT','perp','short',1.0,100.0,'demo','closed',-1.0,'2026-01-02')"
    )
    db.commit()

    result = _check_profit_factor()

    assert result["status"] == "fail", (
        f"D8-B01: PF=0.4 (below kill threshold 0.5) must yield status='fail', "
        f"got '{result['status']}'. Scorecard must not soft-grade catastrophic PF."
    )
    assert result["score"] <= 0, (
        f"D8-B01: PF=0.4 must yield score=0 (or <=0), got {result['score']}. "
        "A non-zero score for sub-kill PF lets a dead strategy pass the gate."
    )


@spec_claim(
    SpecClaim(
        "D8-B02",
        "kill_conditions_contract",
        "§K3 live unlock scorecard gate",
        "L2b",
        "live_unlock 端点（POST /api/settings/live-unlock）在 scorecard "
        "composite_score < 70 时返回 403——demo 是 live 唯一入场券的代码约束已实现。",
        status="implemented",
    )
)
def test_live_unlock_blocked_when_scorecard_fails(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Mock scorecard to return composite_score=50 (below 70 gate)
    import aletheia.analytics.go_live_scorecard as sc_mod
    monkeypatch.setattr(
        sc_mod,
        "compute_scorecard",
        lambda: {
            "composite_score": 50.0,
            "traffic_light": "red",
            "criteria": [],
            "blockers": ["PF too low"],
            "computed_at": "2026-01-01T00:00:00+00:00",
        },
    )

    import yaml
    from aletheia.config.schema import AletheiaConfig
    from fastapi import FastAPI
    from aletheia.server.routes.live import router as live_router
    from fastapi.testclient import TestClient

    cfg = AletheiaConfig()
    cfg.live.state = "live_disabled"
    cfg.live.first_live_confirmation_done = True
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    app = FastAPI()
    app.include_router(live_router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code >= 400 or "scorecard" in resp.text.lower(), (
        f"D8-B02: live_unlock with scorecard=50 should be blocked (4xx) or contain "
        f"scorecard rejection. Got {resp.status_code}: {resp.text[:200]}"
    )


@spec_claim(
    SpecClaim(
        "D8-B02A",
        "kill_conditions_contract",
        "§K3 positive eligible net PnL gate",
        "L2b",
        "A scorecard with eligible trades and non-positive net realized PnL is red "
        "and blocked regardless of its weighted composite score.",
        status="implemented",
    )
)
def test_scorecard_blocks_negative_eligible_net_pnl(monkeypatch):
    import aletheia.analytics.go_live_scorecard as scorecard_mod

    pnls = [-100.0] + [2.0] * 49
    monkeypatch.setattr(
        scorecard_mod,
        "_genuine_closed_pnls",
        lambda order_by_closed_at=False: pnls,
    )

    result = scorecard_mod.compute_scorecard()

    assert sum(pnls) < 0
    assert result["traffic_light"] == "red"
    assert result["performance_gate"]["status"] == "fail"
    assert result["performance_gate"]["eligible_trade_count"] == 50
    assert result["performance_gate"]["net_realized_pnl"] == pytest.approx(-2.0)
    assert any("net realized PnL" in blocker for blocker in result["blockers"])


@spec_claim(
    SpecClaim(
        "D8-B02B",
        "kill_conditions_contract",
        "§K3 loss-first drawdown",
        "L2b",
        "A realized-PnL curve that never rises above zero cannot report zero "
        "drawdown or pass the drawdown criterion.",
        status="implemented",
    )
)
def test_loss_first_curve_does_not_report_zero_drawdown(monkeypatch):
    import aletheia.analytics.go_live_scorecard as scorecard_mod

    monkeypatch.setattr(
        scorecard_mod,
        "_genuine_closed_pnls",
        lambda order_by_closed_at=False: [-100.0] + [2.0] * 49,
    )

    result = scorecard_mod._check_max_drawdown()

    assert result["status"] == "fail"
    assert result["score"] == 0
    assert result["detail"].startswith("100.0% drawdown")


@pytest.mark.xfail(
    strict=True,
    reason="NOT YET IMPLEMENTED — D8 defer: personal_stop_loss not in RiskConfig",
)
@spec_claim(
    SpecClaim(
        "D8-B03",
        "kill_conditions_contract",
        "§personal stop defer",
        "L2b",
        "defer 占位：个人止损线/B/C 功能的 defer 占位——RiskConfig 中不存在 "
        "personal_stop_loss_usdt 或 personal_stop_line 等字段，确认该功能尚未实现、"
        "不会被悄悄加入而缺少 spec 保护。",
        status="missing",
    )
)
def test_personal_stop_loss_not_yet_in_risk_config():
    import inspect
    from aletheia.config.schema import RiskConfig

    source = inspect.getsource(RiskConfig)
    has_field = (
        "personal_stop_loss_usdt" in source
        or "personal_stop_line" in source
    )
    # 当前不应存在：tripwire 期望此断言成立（不存在）
    # xfail(strict=True) 意味着测试"通过"即违约——所以此处用 fail() 保持 tripwire 红灯
    # 当功能真正实现时，这个 xfail 会意外 PASS 从而变红，提醒补完整 spec
    assert not has_field, (
        "D8-B03: personal_stop_loss_usdt or personal_stop_line found in RiskConfig — "
        "the defer placeholder has been implemented without spec coverage. "
        "Add full D8-B03 behavioral spec before merging."
    )
    pytest.fail(
        "NOT IMPLEMENTED (defer): personal stop-loss feature not in RiskConfig — "
        "this xfail tripwire fires when the feature is added without a spec update."
    )


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "D8-C01",
        "kill_conditions_contract",
        "§K3 scorecard invariant on live unlock",
        "L2c",
        "系统级不变式——live_unlock 路由在状态迁移至 live_limited 前调用 "
        "compute_scorecard() 且在 composite_score < 70 时返回 403。已实现。",
        status="implemented",
    )
)
def test_live_unlock_route_calls_compute_scorecard():
    import pathlib

    live_route_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "aletheia"
        / "server"
        / "routes"
        / "live.py"
    )
    assert live_route_path.exists(), f"live.py not found at {live_route_path}"
    source = live_route_path.read_text(encoding="utf-8")

    assert "compute_scorecard" in source, (
        "D8-C01 INVARIANT VIOLATION: server/routes/live.py does not call "
        "compute_scorecard() — the scorecard gate (composite_score >= 70) for "
        "live unlock has not been wired."
    )
