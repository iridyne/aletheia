from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aletheia.agent_shell import operator_recommendations as recommendations
from aletheia.config.schema import AletheiaConfig
from aletheia.risk.policy import risk_policy_hash


def _divergent_risk_config() -> AletheiaConfig:
    """Return nested policies that intentionally disagree with legacy fields."""
    cfg = AletheiaConfig()

    # Simulate a process that still carries the one-release legacy snapshot.
    cfg.risk.max_demo_position_margin_usdt = 10.0
    cfg.risk.max_live_position_margin_usdt = 10.0
    cfg.risk.max_total_live_exposure_pct = 1.0
    cfg.risk.max_single_trade_risk_pct = 0.5
    cfg.risk.max_demo_single_trade_risk_pct = 1.0
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_futures_leverage_live = 1
    cfg.risk.max_futures_leverage_demo = 2
    cfg.risk.max_open_positions_live = 1
    cfg.risk.max_open_positions_demo = 2
    cfg.risk.allow_short_live = True
    cfg.risk.allow_short_demo = True

    # Mode policy replacement updates these nested objects directly.  It must be
    # authoritative even while the compatibility fields above remain stale.
    cfg.risk.demo.revision = 2
    cfg.risk.demo.capital.base_source = "custom"
    cfg.risk.demo.capital.base_usdt = 100.0
    cfg.risk.demo.capital.max_position_margin_usdt = 200.0
    cfg.risk.demo.loss.max_single_trade_risk_pct = 20.0
    cfg.risk.demo.loss.max_daily_loss_pct = 20.0
    cfg.risk.demo.leverage.futures_cap = 10
    cfg.risk.demo.positions.max_open_positions = 20
    cfg.risk.demo.positions.allow_short = False

    cfg.risk.live.revision = 3
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 100.0
    cfg.risk.live.capital.max_position_margin_usdt = 100.0
    cfg.risk.live.capital.max_total_exposure_pct = 10.0
    cfg.risk.live.loss.max_single_trade_risk_pct = 2.0
    cfg.risk.live.loss.max_daily_loss_pct = 1.0
    cfg.risk.live.leverage.futures_cap = 3
    cfg.risk.live.positions.max_open_positions = 3
    cfg.risk.live.positions.allow_short = False
    return cfg


@pytest.mark.parametrize(
    ("violation", "execution_mode", "boundary_key", "current", "suggested"),
    [
        (
            "exceeds_demo_position_margin:300.0>200.0",
            "paper_internal",
            "max_demo_position_margin_usdt",
            200.0,
            300.0,
        ),
        (
            "exceeds_max_position_margin:150.0>100.0",
            "bitget_live_limited",
            "max_live_position_margin_usdt",
            100.0,
            150.0,
        ),
        (
            "max_total_live_exposure_exceeded:15.0>10.0",
            "bitget_live_limited",
            "max_total_live_exposure_pct",
            10.0,
            15.0,
        ),
        ("exceeds_single_trade_risk:3.0>2.0", "bitget_live_limited", "max_single_trade_risk_pct", 2.0, 3.0),
        ("exceeds_demo_single_trade_risk:25.0>20.0", "paper_internal", "max_demo_single_trade_risk_pct", 20.0, 25.0),
        ("max_daily_loss_exceeded:-25.0<=-20.0", "paper_internal", "max_daily_loss_pct", 20.0, 25.0),
        ("exceeds_max_leverage:11.0>10.0", "paper_internal", "max_futures_leverage_demo", 10, 11),
        ("max_live_positions_reached:3", "bitget_live_limited", "max_open_positions_live", 3, 4),
        ("max_demo_positions_reached:20", "paper_internal", "max_open_positions_demo", 20, 21),
        ("short_not_allowed_live", "bitget_live_limited", "allow_short_live", False, True),
        ("short_not_allowed_demo", "paper_internal", "allow_short_demo", False, True),
    ],
)
def test_risk_boundary_candidates_read_current_value_from_resolved_mode_policy(
    violation,
    execution_mode,
    boundary_key,
    current,
    suggested,
):
    cfg = _divergent_risk_config()

    candidate = recommendations._risk_boundary_change_from_violation(
        violation,
        {"execution_mode": execution_mode},
        cfg,
    )

    assert candidate is not None
    expected_mode = "live" if "live" in execution_mode else "demo"
    policy = getattr(cfg.risk, expected_mode)
    assert candidate["risk_mode"] == expected_mode
    assert candidate["policy_id"] == policy.policy_id
    assert candidate["policy_revision"] == policy.revision
    assert candidate["policy_hash"] == risk_policy_hash(policy)
    assert candidate["boundary_key"] == boundary_key
    assert candidate["current_value"] == current
    assert candidate["suggested_value"] == suggested
    assert candidate["changes"] == {boundary_key: suggested}


@pytest.mark.parametrize(
    "execution_mode",
    ("bitget_live_limited", "binance_live_limited", "okx_live_limited"),
)
def test_every_live_venue_uses_live_leverage_policy(execution_mode):
    cfg = _divergent_risk_config()

    candidate = recommendations._risk_boundary_change_from_violation(
        "exceeds_max_leverage:4.0>3.0",
        {"execution_mode": execution_mode},
        cfg,
    )

    assert candidate is not None
    assert candidate["risk_mode"] == "live"
    assert candidate["boundary_key"] == "max_futures_leverage_live"
    assert candidate["current_value"] == 3
    assert candidate["suggested_value"] == 4


@pytest.mark.parametrize(
    "proposal",
    (None, {}, {"execution_mode": ""}, {"execution_mode": "unknown_mode"}),
)
def test_risk_boundary_candidate_fails_closed_without_registered_execution_mode(proposal):
    cfg = _divergent_risk_config()

    assert (
        recommendations._risk_boundary_change_from_violation(
            "max_demo_positions_reached:20",
            proposal,
            cfg,
        )
        is None
    )


def test_current_demo_daily_loss_does_not_recommend_legacy_one_point_seven_percent():
    cfg = _divergent_risk_config()
    cfg.risk.demo.capital.base_usdt = 200.0

    assert (
        recommendations._risk_boundary_change_from_violation(
            "max_daily_loss_exceeded:-3.4<=-2.0",
            {"execution_mode": "paper_internal"},
            cfg,
        )
        is None
    )


def _risk_summary_rows(
    cfg,
    *,
    demo_count: int,
    live_count: int,
    demo_loss: float,
    live_loss: float,
):
    created_at = datetime.now(timezone.utc).isoformat()
    proposals: dict[str, dict] = {}
    decisions: list[dict] = []
    for risk_mode, count, loss in (
        ("demo", demo_count, demo_loss),
        ("live", live_count, live_loss),
    ):
        policy = getattr(cfg.risk, risk_mode)
        execution_mode = "paper_internal" if risk_mode == "demo" else "bitget_live_limited"
        for index in range(count):
            proposal_id = f"{risk_mode}-proposal-{index}"
            proposals[proposal_id] = {
                "id": proposal_id,
                "symbol": f"{risk_mode.upper()}{index}/USDT",
                "execution_mode": execution_mode,
            }
            decisions.append(
                {
                    "id": f"{risk_mode}-decision-{index}",
                    "proposal_id": proposal_id,
                    "approved": False,
                    "created_at": created_at,
                    "violations": json.dumps([f"max_daily_loss_exceeded:{loss}<=-{abs(loss)}"]),
                    "message": "Rejected",
                    "risk_mode": risk_mode,
                    "policy_id": policy.policy_id,
                    "policy_revision": policy.revision,
                    "policy_hash": risk_policy_hash(policy),
                }
            )
    return decisions, proposals


def _stub_risk_rows(monkeypatch, decisions, proposals):
    monkeypatch.setattr(
        recommendations.repo,
        "list_risk_decisions",
        lambda limit=200: decisions[:limit],
    )
    monkeypatch.setattr(
        recommendations.repo,
        "get_trade_proposal",
        lambda proposal_id: proposals.get(proposal_id),
    )


def test_fedora_demo_daily_loss_regression_uses_nested_twenty_percent_policy(monkeypatch):
    cfg = _divergent_risk_config()
    cfg.risk.demo.capital.base_usdt = 200.0
    decisions, proposals = _risk_summary_rows(
        cfg,
        demo_count=5,
        live_count=0,
        demo_loss=-3.4,
        live_loss=0.0,
    )
    for decision in decisions:
        decision["policy_revision"] = 1
        decision["policy_hash"] = "demo-revision-1-hash"
    _stub_risk_rows(monkeypatch, decisions, proposals)

    summary = recommendations.build_risk_summary(cfg=cfg, recent_artifacts=[])

    assert cfg.risk.demo.revision == 2
    assert cfg.risk.demo.loss.max_daily_loss_pct == 20.0
    assert cfg.risk.live.loss.max_daily_loss_pct == 1.0
    assert cfg.risk.max_daily_loss_pct == 1.0  # deliberately stale compatibility field
    assert len(summary["recent_rejected_decisions"]) == 5
    assert summary["needs_operator_risk_boundary_proposals"] == []


def test_daily_loss_candidates_and_recent_proposals_are_isolated_by_mode_and_policy(monkeypatch):
    cfg = _divergent_risk_config()
    decisions, proposals = _risk_summary_rows(
        cfg,
        demo_count=2,
        live_count=2,
        demo_loss=-25.0,
        live_loss=-2.0,
    )
    _stub_risk_rows(monkeypatch, decisions, proposals)

    unsuppressed = recommendations.build_risk_summary(cfg=cfg, recent_artifacts=[])
    candidates = unsuppressed["needs_operator_risk_boundary_proposals"]
    assert {candidate["risk_mode"] for candidate in candidates} == {"demo", "live"}
    assert {candidate["current_value"] for candidate in candidates} == {20.0, 1.0}

    demo_policy = cfg.risk.demo
    artifact = {
        "id": "demo-daily-loss-proposal",
        "title": "Demo daily-loss proposal",
        "artifact_type": "risk_boundary_change_proposal",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content": {
            "risk_mode": "demo",
            "policy_id": demo_policy.policy_id,
            "policy_revision": demo_policy.revision,
            "policy_hash": risk_policy_hash(demo_policy),
            "changes": {"max_daily_loss_pct": 25.0},
        },
    }
    scoped = recommendations.build_risk_summary(cfg=cfg, recent_artifacts=[artifact])
    remaining = scoped["needs_operator_risk_boundary_proposals"]
    assert [candidate["risk_mode"] for candidate in remaining] == ["live"]

    artifact["content"]["policy_hash"] = "stale-policy-hash"
    stale = recommendations.build_risk_summary(cfg=cfg, recent_artifacts=[artifact])
    assert {candidate["risk_mode"] for candidate in stale["needs_operator_risk_boundary_proposals"]} == {
        "demo",
        "live",
    }
