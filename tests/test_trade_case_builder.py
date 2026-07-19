"""Tests for trade_case_builder module."""

from unittest.mock import patch, MagicMock

from aletheia.analytics.trade_case_builder import (
    _build_case_from_row,
    _render_case_body,
    _build_tags,
    _opened_after,
    _is_synthetic_closure,
    detect_guard_candidates,
    build_case_studies,
)


def _make_row(
    position_id="pos-1",
    symbol="BTC/USDT",
    direction="long",
    realized_pnl=-5.0,
    realized_pnl_pct=-2.5,
    holding_hours=12.5,
    close_reason="stop_loss",
    scanner_family="momentum",
    confidence="high",
    confidence_score=75,
    late_entry_penalty=5,
    crowding_risk=3,
    risk_flags=None,
    proposal_id="prop-1",
    opportunity_id="opp-1",
    strategy_id="strat-1",
    opened_at="2026-06-13T10:00:00+00:00",
):
    return {
        "position_id": position_id,
        "symbol": symbol,
        "direction": direction,
        "market_type": "spot",
        "execution_mode": "paper",
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "holding_hours": holding_hours,
        "close_reason": close_reason,
        "scanner_family": scanner_family,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "late_entry_penalty": late_entry_penalty,
        "crowding_risk": crowding_risk,
        "risk_flags": risk_flags or [],
        "proposal_id": proposal_id,
        "opportunity_id": opportunity_id,
        "strategy_id": strategy_id,
        "entry_price": 100.0,
        "exit_price": 97.5,
        "opened_at": opened_at,
        "closed_at": "2026-06-13T22:30:00+00:00",
        "universe_member_id": "um-1",
        "universe_rank": 3,
        "universe_total_score": 80,
        "source_agent": "market_observer",
    }


class TestBuildCaseFromRow:
    def test_loss_outcome(self):
        row = _make_row(realized_pnl_pct=-2.5)
        case = _build_case_from_row(row)
        assert case["outcome"] == "loss"
        assert case["realized_pnl_pct"] == -2.5

    def test_win_outcome(self):
        row = _make_row(realized_pnl_pct=3.2)
        case = _build_case_from_row(row)
        assert case["outcome"] == "win"

    def test_scratch_outcome(self):
        row = _make_row(realized_pnl_pct=0.1)
        case = _build_case_from_row(row)
        assert case["outcome"] == "scratch"

    def test_full_chain(self):
        row = _make_row()
        case = _build_case_from_row(row)
        assert case["has_full_chain"] is True

    def test_partial_chain(self):
        row = _make_row(proposal_id=None)
        case = _build_case_from_row(row)
        assert case["has_full_chain"] is False

    def test_strategy_id_not_required_after_strategy_engine_removal(self):
        row = _make_row(strategy_id=None, proposal_id="prop-1", opportunity_id="opp-1")
        case = _build_case_from_row(row)
        assert case["has_full_chain"] is True


class TestRenderCaseBody:
    def test_contains_metrics(self):
        case = _build_case_from_row(_make_row())
        body = _render_case_body(case)
        assert "Outcome: **loss**" in body
        assert "PnL: -2.50%" in body
        assert "Hold: 12.5h" in body

    def test_preserves_complete_decision_and_manifest_context(self):
        long_reason = "decision reason " + ("x" * 900)
        row = _make_row()
        row.update({
            "decision_context_status": "available",
            "pi_decision_reasoning": long_reason,
            "pi_decision_bear_case": "full bear case",
            "pi_decision_assumptions": ["assumption one"],
            "proposal_agent_rationale": "full proposal rationale",
            "decision_evidence_manifest": {"version": "evidence-v1", "ids": ["e1"]},
            "decision_learning_manifest": {"version": "learning-v1", "ids": ["l1"]},
        })

        body = _render_case_body(_build_case_from_row(row))

        assert long_reason in body
        assert "Context status: available" in body
        assert "full bear case" in body
        assert "assumption one" in body
        assert "full proposal rationale" in body
        assert '"e1"' in body
        assert '"l1"' in body

    def test_legacy_decision_context_is_explicitly_not_recorded(self):
        body = _render_case_body(_build_case_from_row(_make_row()))
        assert "PiDecision reasoning: not recorded" in body
        assert "Evidence manifest: not recorded" in body


class TestBuildTags:
    def test_basic_tags(self):
        case = _build_case_from_row(_make_row())
        tags = _build_tags(case)
        assert "loss" in tags
        assert "momentum" in tags
        assert "stop_loss" in tags

    def test_late_entry_tag(self):
        row = _make_row(late_entry_penalty=25)
        case = _build_case_from_row(row)
        tags = _build_tags(case)
        assert "late-entry" in tags

    def test_wide_spread_tag(self):
        row = _make_row(risk_flags=["wide_spread"])
        case = _build_case_from_row(row)
        tags = _build_tags(case)
        assert "wide-spread" in tags


class TestOpenedAfter:
    def test_after_cutoff(self):
        assert _opened_after({"opened_at": "2026-06-14T00:00:00+00:00"}, 0) is True

    def test_before_cutoff(self):
        from datetime import datetime, timezone
        future = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
        assert _opened_after({"opened_at": "2026-06-14T00:00:00+00:00"}, future) is False

    def test_missing_opened_at(self):
        assert _opened_after({}, 0) is False


class TestDetectGuardCandidates:
    @patch("aletheia.analytics.trade_case_builder._get_existing_guard_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_no_rows_no_candidates(self, mock_rows, mock_guards):
        mock_rows.return_value = []
        mock_guards.return_value = set()
        result = detect_guard_candidates()
        assert result == []

    @patch("aletheia.analytics.trade_case_builder.create_guard_candidate")
    @patch("aletheia.analytics.trade_case_builder._get_existing_guard_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_generates_candidate_on_repeated_loss(self, mock_rows, mock_guards, mock_create):
        mock_guards.return_value = set()
        mock_create.return_value = MagicMock(id="gc-1")

        rows = [
            _make_row(
                position_id=f"pos-{i}",
                realized_pnl=-5.0,
                realized_pnl_pct=-2.5,
                late_entry_penalty=15,
            )
            for i in range(4)
        ]
        mock_rows.return_value = rows

        result = detect_guard_candidates(min_losses=3)
        assert len(result) >= 1
        assert result[0]["guard_candidate_id"] == "gc-1"

    @patch("aletheia.analytics.trade_case_builder._get_existing_guard_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_skips_existing_guard(self, mock_rows, mock_guards):
        rows = [
            _make_row(position_id=f"pos-{i}", realized_pnl=-5.0, realized_pnl_pct=-2.5)
            for i in range(4)
        ]
        mock_rows.return_value = rows
        # Pre-register the guard ID that would be generated
        from aletheia.analytics.trade_attribution import factor_key
        fk = factor_key(rows[0])
        guard_id = f"auto_factor_loss_{fk.replace(':', '_').replace('/', '_')}"
        mock_guards.return_value = {guard_id}

        result = detect_guard_candidates(min_losses=3)
        assert result == []

    @patch("aletheia.analytics.trade_case_builder.create_guard_candidate")
    @patch("aletheia.analytics.trade_case_builder._get_existing_guard_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_excludes_synthetic_closure(self, mock_rows, mock_guards, mock_create):
        """LP-S08: a guard built from synthetic closures would penalise a factor pattern
        on the basis of a fabricated (non-)loss. Synthetic-closure rows are filtered out
        before factor grouping, so they cannot manufacture a guard candidate."""
        mock_guards.return_value = set()
        mock_rows.return_value = [
            _make_row(
                position_id=f"pos-{i}",
                realized_pnl=-5.0,
                realized_pnl_pct=-2.5,
                late_entry_penalty=15,
                close_reason="reconciled_gone",
            )
            for i in range(4)
        ]

        result = detect_guard_candidates(min_losses=3)
        assert result == []
        mock_create.assert_not_called()


class TestIsSyntheticClosure:
    def test_reconciled_gone_is_synthetic(self):
        assert _is_synthetic_closure({"close_reason": "reconciled_gone"}) is True

    def test_demo_cleanup_is_synthetic(self):
        assert _is_synthetic_closure({"close_reason": "demo_validation_cleanup"}) is True

    def test_units_bug_cleanup_is_synthetic(self):
        assert _is_synthetic_closure({"close_reason": "units_bug_cleanup"}) is True

    def test_retry_after_fix_is_synthetic(self):
        # One-off remediation for the OKX clOrdId-hyphen bug (5e9ceb5): positions
        # whose earlier close was blocked by the bug got re-closed post-fix and
        # tagged retry_after_fix. It's an administrative close, not a trade outcome —
        # keep it out of scorecard/learning/factor-feedback.
        assert _is_synthetic_closure({"close_reason": "retry_after_fix"}) is True

    def test_genuine_reasons_not_synthetic(self):
        for reason in ("stop_loss", "take_profit", "manual", "signal_exit", None, ""):
            assert _is_synthetic_closure({"close_reason": reason}) is False


class TestBuildCaseStudies:
    @patch("aletheia.analytics.trade_case_builder._get_existing_case_position_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_no_rows_empty(self, mock_rows, mock_existing):
        mock_rows.return_value = []
        mock_existing.return_value = set()
        assert build_case_studies() == []

    @patch("aletheia.analytics.trade_case_builder.create_trade_case_study")
    @patch("aletheia.analytics.trade_case_builder._get_existing_case_position_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_creates_case_study(self, mock_rows, mock_existing, mock_create):
        mock_existing.return_value = set()
        mock_create.return_value = MagicMock(id="cs-1")
        mock_rows.return_value = [_make_row()]

        result = build_case_studies(since_hours=9999)
        assert len(result) == 1
        assert result[0]["case_study_id"] == "cs-1"
        assert result[0]["outcome"] == "loss"

    @patch("aletheia.analytics.trade_case_builder._get_existing_case_position_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_skips_existing_position(self, mock_rows, mock_existing):
        mock_existing.return_value = {"pos-1"}
        mock_rows.return_value = [_make_row(position_id="pos-1")]

        result = build_case_studies(since_hours=9999)
        assert result == []

    @patch("aletheia.analytics.trade_case_builder.create_trade_case_study")
    @patch("aletheia.analytics.trade_case_builder._get_existing_case_position_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_skips_synthetic_closure(self, mock_rows, mock_existing, mock_create):
        """LP-S08: administrative/synthetic closures (orphan reconciliation, demo
        cleanup) have a fabricated exit price — they must NOT enter the learning
        corpus, and create_trade_case_study must never be called for them."""
        mock_existing.return_value = set()
        mock_rows.return_value = [
            _make_row(position_id="pos-gone", close_reason="reconciled_gone"),
            _make_row(position_id="pos-demo", close_reason="demo_validation_cleanup"),
        ]

        result = build_case_studies(since_hours=9999)
        assert result == []
        mock_create.assert_not_called()

    @patch("aletheia.analytics.trade_case_builder.create_trade_case_study")
    @patch("aletheia.analytics.trade_case_builder._get_existing_case_position_ids")
    @patch("aletheia.analytics.trade_case_builder.build_trade_attribution_rows")
    def test_genuine_tpsl_close_not_skipped(self, mock_rows, mock_existing, mock_create):
        """A reconciler close that recovered a REAL exchange TPSL fill keeps its genuine
        close_reason (take_profit/stop_loss) — that is a real outcome and IS kept. The
        filter keys on the close_reason VALUE, not on whether a reconciler was involved."""
        mock_existing.return_value = set()
        mock_create.return_value = MagicMock(id="cs-tp")
        mock_rows.return_value = [
            _make_row(position_id="pos-tp", close_reason="take_profit", realized_pnl_pct=3.0)
        ]

        result = build_case_studies(since_hours=9999)
        assert len(result) == 1
        assert result[0]["case_study_id"] == "cs-tp"
        mock_create.assert_called_once()


def test_case_carries_market_type_for_perp_vs_spot():
    """Round 2: the case dict tags spot vs perp so the learning loop can separate
    'momentum perp' from 'momentum spot' patterns (deferred #8, the useful half).
    The metric realized_pnl_pct stays price-move% (leverage-neutral) — no leveraged
    return is introduced."""
    row = _make_row()
    row["market_type"] = "usdt_futures"
    case = _build_case_from_row(row)
    assert case["market_type"] == "usdt_futures"

    row2 = _make_row()  # default market_type="spot"
    assert _build_case_from_row(row2)["market_type"] == "spot"
