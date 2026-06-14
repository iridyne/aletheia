"""Tests for trade_case_builder module."""

from unittest.mock import patch, MagicMock

from aletheia.analytics.trade_case_builder import (
    _build_case_from_row,
    _render_case_body,
    _build_tags,
    _opened_after,
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


class TestRenderCaseBody:
    def test_contains_metrics(self):
        case = _build_case_from_row(_make_row())
        body = _render_case_body(case)
        assert "Outcome: **loss**" in body
        assert "PnL: -2.50%" in body
        assert "Hold: 12.5h" in body


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
