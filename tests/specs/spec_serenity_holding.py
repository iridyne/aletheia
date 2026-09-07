"""Behavioral regression: Serenity holdings outlive short-term entry timing."""

from __future__ import annotations


from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_holding"


def _seren_cfg():
    """Realistic in-memory config for the position-manager helpers."""
    from aletheia.config.loader import load_config
    return load_config()


def _make_position(*, trade_school: str, opened_hours_ago: float, direction: str = "long") -> dict:
    """Build a Position-shaped dict for direct _decide_exit / _maybe_update_trailing_stop calls.

    Uses ISO 8601 with explicit UTC offset — matches Position.opened_at format.
    """
    from datetime import datetime, timedelta, timezone
    opened = datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
    return {
        "id": f"p-{trade_school}-{int(opened_hours_ago)}",
        "symbol": "NVDA",
        "direction": direction,
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
        "stop_price": None,
        "take_profit_price": None,
        "trade_school": trade_school,
        "opened_at": opened.isoformat(),
        "execution_mode": "paper_internal",
    }


@spec_claim(
    SpecClaim(
        "SH-B01", _CONTRACT, "§serenity position not killed by 72h short-term cap", "L2b",
        "A serenity position opened 100h ago does NOT trigger max_hold exit because the "
        "long-horizon cap (cfg.serenity.max_hold_days * 24) is much larger than the "
        "short-term 72h",
    ),
)
def test_decide_exit_serenity_survives_short_term_cap():
    from aletheia.portfolio.position_manager import _decide_exit

    cfg = _seren_cfg()
    # The short-term cap (cfg.trading.max_position_hold_hours, default 72) would
    # fire here; the serenity school must instead use cfg.serenity.max_hold_days.
    pos = _make_position(trade_school="serenity", opened_hours_ago=100.0)
    decision = _decide_exit(pos, price=100.0, cfg=cfg)
    assert decision is None, (
        f"serenity position aged 100h was killed by short-term cap: {decision!r} — "
        f"the school branch in _decide_exit is missing or wrong"
    )


@spec_claim(
    SpecClaim(
        "SH-B02", _CONTRACT, "§serenity trailing stop is a no-op", "L2b",
        "_maybe_update_trailing_stop does NOT write a stop_price for a serenity position "
        "regardless of price action — intraday ATR trailing is meaningless for "
        "long-horizon holdings",
    ),
)
def test_trailing_stop_noop_for_serenity():
    from aletheia.portfolio.position_manager import _maybe_update_trailing_stop

    cfg = _seren_cfg()
    pos = _make_position(trade_school="serenity", opened_hours_ago=10.0)
    # Significant favorable move would normally raise a trailing stop. Capture
    # state before and confirm no in-memory mutation occurs (DB writes would be
    # caught by an autouse fixture; in-memory assertion is the lightweight proof).
    before = dict(pos)
    _maybe_update_trailing_stop(pos, price=120.0, cfg=cfg)
    assert pos.get("stop_price") == before.get("stop_price"), (
        f"serenity position acquired a trailing stop {pos.get('stop_price')!r} — "
        f"the school gate at the top of _maybe_update_trailing_stop is missing"
    )
    assert pos.get("trail_high_water") == before.get("trail_high_water"), (
        "serenity position acquired a trail_high_water marker — the trailing "
        "machinery is still running"
    )


@spec_claim(
    SpecClaim(
        "SH-B03", _CONTRACT, "§short-term regression — 72h cap still fires", "L2b",
        "A short_term position aged past cfg.trading.max_position_hold_hours still "
        "triggers max_hold exit — Phase 4 must not regress the existing short-term loop",
    ),
)
def test_decide_exit_short_term_still_capped():
    from aletheia.portfolio.position_manager import _decide_exit

    cfg = _seren_cfg()
    cap_h = float(cfg.trading.max_position_hold_hours or 72)
    pos = _make_position(trade_school="short_term", opened_hours_ago=cap_h + 2.0)
    decision = _decide_exit(pos, price=100.0, cfg=cfg)
    assert decision is not None, (
        "short_term position aged past the cap was NOT closed — Phase 4 regressed "
        "the existing short-term max_hold guard"
    )
    reason, _ = decision
    assert reason == "max_hold", (
        f"short_term cap fired the wrong reason: {reason!r} (expected 'max_hold')"
    )
