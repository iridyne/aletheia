"""Sample-aware sizing must learn only from genuine strategy outcomes."""

from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position


def test_administrative_closes_do_not_advance_paper_kelly_sample_count():
    strategy_id = "strategy-admin-only"
    closed_at = datetime.now(timezone.utc).isoformat()
    for index in range(30):
        repo.insert_position(Position(
            id=f"admin-close-{index:02d}",
            symbol=f"ADMIN{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            current_price=1.0,
            realized_pnl=-99.0,
            execution_mode="paper_internal",
            strategy_id=strategy_id,
            status="closed",
            close_reason="reconciled_gone",
            close_category="administrative",
            outcome_eligible=False,
            outcome_exclusion_reason="administrative_close",
            closed_at=closed_at,
        ))

    from aletheia.agents.paper_trader import _get_strategy_paper_trade_count

    assert _get_strategy_paper_trade_count(strategy_id) == 0
