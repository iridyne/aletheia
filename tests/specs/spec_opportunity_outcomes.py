"""Executable claims for docs/design/contracts/opportunity_outcome_contract.md."""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "opportunity_outcome_contract"


@spec_claim(
    SpecClaim(
        "OO-B01", _CONTRACT, "§4 cost formula", "L2b",
        "Net return is direction-adjusted gross return minus round-trip fees, spread/slippage and funding.",
        status="implemented",
    )
)
def test_net_return_formula_includes_all_costs():
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    result = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=102.0,
        direction="long",
        market_type="spot",
        hold_seconds=3600,
        entry_spread_bps=10.0,
        fallback_spread_bps=25.0,
        funding_rate_at_entry=None,
        execution_fee_pct=0.06,
        slippage_bps=5.0,
        range_high=103.0,
        range_low=99.0,
        price_quality_ok=True,
    )

    assert result["gross_return_pct"] == pytest.approx(2.0)
    assert result["directional_return_pct"] == pytest.approx(2.0)
    assert result["estimated_fee_pct"] == pytest.approx(0.12)
    assert result["estimated_spread_slippage_pct"] == pytest.approx(0.20)
    assert result["estimated_funding_pct"] == pytest.approx(0.0)
    assert result["net_return_pct"] == pytest.approx(1.68)


@spec_claim(
    SpecClaim(
        "OO-B02", _CONTRACT, "§4 incomplete costs", "L2b",
        "A perpetual outcome with unknown entry funding has NULL net return instead of optimistic zero funding.",
        status="implemented",
    )
)
def test_missing_perp_funding_keeps_net_unknown():
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    for market_type in ("perp", "futures", "swap", "usdt_futures"):
        result = estimate_outcome_economics(
            entry_price=100.0,
            exit_price=95.0,
            direction="short",
            market_type=market_type,
            hold_seconds=8 * 3600,
            entry_spread_bps=5.0,
            fallback_spread_bps=25.0,
            funding_rate_at_entry=None,
            execution_fee_pct=0.06,
            slippage_bps=5.0,
            price_quality_ok=True,
        )

        assert result["directional_return_pct"] == pytest.approx(5.0)
        assert result["estimated_funding_pct"] is None
        assert result["net_return_pct"] is None


@spec_claim(
    SpecClaim(
        "OO-B07", _CONTRACT, "§4 canonical cost domain", "L2b",
        "Invalid fee, slippage or consumed spread evidence fails before it can become optimistic zero-cost economics.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_fee_pct", -0.01),
        ("execution_fee_pct", float("nan")),
        ("slippage_bps", -0.01),
        ("slippage_bps", float("inf")),
        ("fallback_spread_bps", 0.0),
        ("fallback_spread_bps", float("inf")),
        ("entry_spread_bps", -0.01),
        ("entry_spread_bps", float("nan")),
    ],
)
def test_invalid_cost_inputs_never_become_optimistic_zero(field, value):
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    inputs = {
        "entry_price": 100.0,
        "exit_price": 101.0,
        "direction": "long",
        "market_type": "spot",
        "hold_seconds": 3600,
        "entry_spread_bps": None if field == "fallback_spread_bps" else 5.0,
        "fallback_spread_bps": 25.0,
        "funding_rate_at_entry": None,
        "execution_fee_pct": 0.06,
        "slippage_bps": 5.0,
    }
    inputs[field] = value

    with pytest.raises(ValueError, match=field):
        estimate_outcome_economics(**inputs)


@spec_claim(
    SpecClaim(
        "OO-B06", _CONTRACT, "§2 instrument identity", "L2b",
        "An unknown market identity cannot become funding-free, cost-complete spot economics.",
        status="implemented",
    )
)
def test_unknown_market_identity_keeps_net_unknown():
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    for market_type in (None, "", "   ", "crypto", "unknown", "perpp"):
        result = estimate_outcome_economics(
            entry_price=100.0,
            exit_price=105.0,
            direction="long",
            market_type=market_type,
            hold_seconds=8 * 3600,
            entry_spread_bps=5.0,
            fallback_spread_bps=25.0,
            funding_rate_at_entry=None,
            execution_fee_pct=0.06,
            slippage_bps=5.0,
            price_quality_ok=True,
        )

        assert result["gross_return_pct"] == pytest.approx(5.0)
        assert result["directional_return_pct"] == pytest.approx(5.0)
        assert result["estimated_funding_pct"] is None
        assert result["net_return_pct"] is None


@spec_claim(
    SpecClaim(
        "OO-B04", _CONTRACT, "§6 settlement status", "L2b",
        "An unreachable venue leaves the row pending for the next cycle; only an answered-but-unmeasurable row becomes failed.",
        status="implemented",
    )
)
def test_unreachable_venue_is_not_a_measurement_failure(monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes
    from aletheia.universe.outcome_recorder import deferred_observation

    entry = datetime.now(timezone.utc) - timedelta(hours=25)
    outcome = FilterCandidateOutcome(
        outcome_candidate_key="oo-b04", venue="okx", symbol="BTC/USDT",
        market_type="spot", direction="long", scanner_family="momentum",
        horizon="24h", entry_price_usd=100.0, entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=24)).isoformat(),
    )
    repo.insert_filter_candidate_outcomes_batch([outcome])

    def _status():
        return get_db().execute(
            "SELECT status FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
        ).fetchone()["status"]

    async def _never_reached(rows, *, now):
        return {r["id"]: deferred_observation("ohlcv:RateLimitExceeded") for r in rows}

    async def _answered_with_no_price(rows, *, now):
        return {}

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _never_reached,
    )
    asyncio.run(settle_due_filter_outcomes(max_rows=8))
    assert _status() == "pending"

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _answered_with_no_price,
    )
    asyncio.run(settle_due_filter_outcomes(max_rows=8))
    assert _status() == "failed"


@spec_claim(
    SpecClaim(
        "OO-B03", _CONTRACT, "§7 counterfactual verdict", "L2b",
        "Missed alpha requires net return above its safety edge; positive gross alone has a separate verdict.",
        status="implemented",
    )
)
def test_missed_alpha_requires_tradable_net_edge():
    from aletheia.analytics.outcome_semantics import classify_counterfactual

    assert classify_counterfactual(0.20, 0.45, 0.05) == "missed_alpha_suspect"
    assert classify_counterfactual(0.01, 0.45, 0.05) == "positive_gross_move_only"
    assert classify_counterfactual(None, 0.45, 0.05) == "positive_gross_move_only"
    assert classify_counterfactual(-0.20, -0.10, 0.05) == "protective"
    for invalid_edge in (
        None, float("nan"), float("inf"), -0.01, "0.05", b"0.05", True,
    ):
        assert (
            classify_counterfactual(0.20, 0.45, invalid_edge)
            == "positive_gross_move_only"
        )
        assert classify_counterfactual(-0.20, -0.10, invalid_edge) == "insufficient_data"
    assert classify_counterfactual(float("inf"), 0.45, 0.05) == "positive_gross_move_only"
    assert classify_counterfactual(float("nan"), -0.10, 0.05) == "insufficient_data"
    for invalid_net in ("0.20", b"0.20", True):
        assert classify_counterfactual(invalid_net, -0.10, 0.05) == "insufficient_data"


def test_row_verdict_requires_reconcilable_cost_economics():
    from aletheia.analytics.outcome_semantics import classify_outcome_row

    positive = {
        "venue": "bitget",
        "market_type": "spot",
        "direction": "long",
        "entry_price_usd": 100.0,
        "exit_price_usd": 100.5,
        "directional_return_pct": 0.5,
        "estimated_fee_pct": 0.1,
        "estimated_spread_slippage_pct": 0.1,
        "estimated_funding_pct": 0.0,
        "net_return_pct": 0.3,
        "min_tradable_edge_pct": 0.05,
        "cost_model_version": "v1",
        "horizon": "1h",
        "settlement_price_source": "horizon_ohlcv",
        "settlement_lag_sec": 0.0,
    }
    assert classify_outcome_row(positive) == "missed_alpha_suspect"
    for incomplete in (
        {**positive, "estimated_fee_pct": None},
        {**positive, "net_return_pct": 0.4},
        {**positive, "market_type": "crypto"},
        {**positive, "cost_model_version": " "},
        {
            **positive,
            "estimated_funding_pct": 0.1,
            "net_return_pct": 0.2,
        },
    ):
        assert classify_outcome_row(incomplete) == "positive_gross_move_only"

    negative = {
        **positive,
        "exit_price_usd": 99.5,
        "directional_return_pct": -0.5,
        "net_return_pct": -0.7,
    }
    assert classify_outcome_row(negative) == "protective"
    assert (
        classify_outcome_row({**negative, "estimated_spread_slippage_pct": None})
        == "insufficient_data"
    )


@spec_claim(
    SpecClaim(
        "OO-C01", _CONTRACT, "§8 learning isolation", "L2c",
        "Calibration excludes legacy gross-only rows and counts wins from net_return_pct.",
        status="implemented",
    )
)
def test_calibration_excludes_gross_only_rows():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome

    common = dict(
        venue="bitget",
        scanner_family="momentum",
        direction="long",
        market_type="spot",
        horizon="1h",
        entry_price_usd=100.0,
        due_at="2026-07-10T01:00:00+00:00",
        settled_at="2026-07-10T01:00:00+00:00",
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
        status="settled",
    )
    # gross-only row: no net economics — excluded by complete_outcome_economics_sql filter
    repo.insert_filter_candidate_outcomes_batch([
        FilterCandidateOutcome(
            outcome_candidate_key="key-gross-only:long", symbol="AAA/USDT",
            exit_price_usd=109.0, gross_return_pct=9.0, **common,
        ),
        FilterCandidateOutcome(
            outcome_candidate_key="key-net:long", symbol="BBB/USDT",
            exit_price_usd=99.0, gross_return_pct=-1.0,
            directional_return_pct=-1.0, net_return_pct=-1.2,
            estimated_fee_pct=0.1, estimated_spread_slippage_pct=0.1,
            estimated_funding_pct=0.0,
            min_tradable_edge_pct=0.05, cost_model_version="test-v1", **common,
        ),
    ])

    rows = repo.calibration_stats(cost_model_version="test-v1")
    assert len(rows) == 1
    assert rows[0]["n"] == 1
    assert rows[0]["win_count"] == 0
    assert rows[0]["avg_return"] == pytest.approx(-1.2)


@spec_claim(
    SpecClaim(
        "OO-C02", _CONTRACT, "§8 display isolation", "L2c",
        "Universe UI consumes cohort calibration and displays family, direction and market type.",
        status="implemented",
    )
)
def test_universe_ui_does_not_refold_calibration_cohorts():
    from pathlib import Path

    source = (
        Path(__file__).parents[2] / "frontend" / "src" / "pages" / "Universe.tsx"
    ).read_text(encoding="utf-8")
    assert "by_cohort_horizon" in source
    assert "row.direction" in source
    assert "row.market_type" in source
    assert "by_family_horizon" not in source


@spec_claim(
    SpecClaim(
        "OO-C03", _CONTRACT, "§8 guard isolation", "L2c",
        "Net-loss guard candidates are scoped by direction and market_type; perp evidence cannot block spot.",
        status="implemented",
    )
)
def test_guard_market_scope_is_supported_end_to_end():
    from extensions.knowledge.guards import ALLOWED_GUARD_FIELDS, GuardAppliesTo

    assert "market_type" in GuardAppliesTo.model_fields
    assert "market_type" in ALLOWED_GUARD_FIELDS
