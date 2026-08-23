"""Committee stays advisory; unconfirmed skip does not freeze the funnel for 2h."""

from __future__ import annotations

import pytest

from aletheia.agents.committee.base import DecisionReport, RuleCheckResult
from aletheia.agents.committee.integration import apply_committee_trade_score
from aletheia.agent_shell.skip_policy import (
    NEUTRAL_SKIP_DEFER_REASON,
    STALE_SNAPSHOT_DEFER_REASON,
    defer_window_minutes_for_reason,
    emit_suppression_applies_to_defer,
    skip_policy_context,
)


def _report(*, direction: str, confidence: float, blocked: bool) -> DecisionReport:
    report = DecisionReport(
        symbol="UNI/USDT",
        market_type="perp",
        timestamp="",
        final_direction=direction,  # type: ignore[arg-type]
        final_confidence=confidence,
        position_size_factor=0.5,
    )
    if blocked:
        report.rule_checks = [
            RuleCheckResult(
                rule_name="min_confidence",
                passed=False,
                severity="block",
                message="置信度过低 (<0.3)",
            )
        ]
    return report


def test_committee_direction_conflict_is_veto_when_votes_oppose():
    from aletheia.agents.committee.integration import committee_direction_conflict_reason

    assert committee_direction_conflict_reason(
        {"direction": "short", "committee_direction": "long"}
    ) == "committee_direction_conflict:long!=short"
    assert committee_direction_conflict_reason(
        {"direction": "short", "committee_direction": "short"}
    ) is None
    assert committee_direction_conflict_reason(
        {"direction": "short", "committee_direction": "neutral"}
    ) is None
    assert committee_direction_conflict_reason(
        {"direction": "short", "committee_blocked": True}
    ) is None
    ranker = 39.85
    for direction, blocked in (("neutral", True), ("neutral", False), ("short", True)):
        candidate = {"trade_score": ranker}
        apply_committee_trade_score(
            candidate, _report(direction=direction, confidence=0.29, blocked=blocked)
        )
        assert candidate["committee_score_applied"] is False
        assert candidate["trade_score"] == pytest.approx(ranker)


def test_directional_unblocked_committee_still_mixes_into_trade_score():
    candidate = {"trade_score": 40.0}
    apply_committee_trade_score(
        candidate, _report(direction="short", confidence=0.50, blocked=False)
    )
    assert candidate["committee_score_applied"] is True
    # 40 * 0.6 + (-0.50) * 40 * 0.4 = 16.0
    assert candidate["trade_score"] == pytest.approx(16.0)


def test_b19_skip_policy_does_not_life_support_stale_or_shrug():
    from aletheia.agent_shell.skip_policy import defer_extends_carrier_valid_until

    values = skip_policy_context()
    assert values["version"] == "b1.9"
    assert values["direct_skip_confirmation_required"] is True
    assert values["technical_or_shrug_defer_extends_valid_until"] is False
    assert defer_window_minutes_for_reason(NEUTRAL_SKIP_DEFER_REASON, 120) == 30
    assert defer_window_minutes_for_reason(STALE_SNAPSHOT_DEFER_REASON, 120) == 20
    assert defer_extends_carrier_valid_until(STALE_SNAPSHOT_DEFER_REASON) is False
    assert defer_extends_carrier_valid_until(NEUTRAL_SKIP_DEFER_REASON) is False
    assert defer_extends_carrier_valid_until("operator_low_conviction") is True
    assert defer_extends_carrier_valid_until(
        "direct_skip_not_confirmed:unverified_adverse_reason:regime_mismatch"
    ) is True


def test_unconfirmed_skip_records_a_30_minute_defer_not_two_hours(tmp_path, monkeypatch):
    import asyncio
    import json
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "aletheia.agent_shell.decision_facts.candidate_context_unavailable_reason",
        lambda *_args, **_kwargs: None,
    )
    from aletheia.agent_shell import commands, trading_decision
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-shrug-window",
        symbol="UNI/USDT",
        market="usdt_futures",
        direction_bias="short",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-shrug-window",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="short",
        market_type="usdt_futures",
    ))
    context = {
        "layer1": {"data_health": {"ohlcv_ready": True}},
        "layer2": {
            "pending_signals": [{
                "symbol": opportunity.symbol,
                "direction": "short",
                "market_type": "usdt_futures",
                "opportunity_id": opportunity.id,
                "signal_source": "market_observer",
                "trade_score": 39.85,
            }],
            "pending_queue": {
                "unresolved_total": 1, "visible_count": 1, "overflow_count": 0,
            },
        },
        "layer3": {},
    }
    shrug = {
        "action": "skip",
        "symbol": opportunity.symbol,
        "direction": "short",
        "market_type": "usdt_futures",
        "conviction": 29,
        "reasoning": "mixed 1h bounce vs 4h downtrend",
        "bear_case": "continuation may resume",
        "skip_kind": None,
        "adverse_reason_codes": [],
        "adverse_evidence": [],
    }

    async def assess(_ctx):
        return shrug

    async def confirm(_ctx, _initial, _reason):
        return shrug

    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)
    monkeypatch.setattr(trading_decision, "attach_candidate_entry_mark", lambda *_a, **_k: None)
    monkeypatch.setattr(commands, "_configured_paper_execution_mode", lambda: "paper_internal")

    before = datetime.now(timezone.utc)
    result = asyncio.run(commands.evaluate_trading_opportunity({}))
    after = datetime.now(timezone.utc)

    assert result["action"] == "evaluation_deferred"
    assert result["defer_reason"] == NEUTRAL_SKIP_DEFER_REASON
    row = get_db().execute(
        "SELECT queue_snapshot_json FROM operator_evaluation_events WHERE id=?",
        (result["operator_evaluation_event_id"],),
    ).fetchone()
    snapshot = json.loads(row["queue_snapshot_json"])
    defer_until = datetime.fromisoformat(snapshot["defer_until"])
    elapsed = (defer_until - before).total_seconds()
    # 30-minute window, aligned down to the 30-minute bucket, plus test slack.
    assert 0 < elapsed <= 30 * 60 + 5
    assert (defer_until - after).total_seconds() < 2 * 60 * 60


def test_shrug_defer_does_not_suppress_a_fresh_emit():
    assert emit_suppression_applies_to_defer(None) is False
    assert emit_suppression_applies_to_defer(
        {"defer_reason": NEUTRAL_SKIP_DEFER_REASON, "material_fingerprint": "abc"}
    ) is False
    assert emit_suppression_applies_to_defer(
        {
            "defer_reason": "direct_skip_not_confirmed:unverified_adverse_reason:regime_mismatch",
            "material_fingerprint": "abc",
        }
    ) is True
