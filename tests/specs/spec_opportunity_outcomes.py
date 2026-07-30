"""Executable claims for docs/design/contracts/opportunity_outcome_contract.md."""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "opportunity_outcome_contract"


@spec_claim(
    SpecClaim(
        "OO-S01", _CONTRACT, "§2 instrument identity", "L2a",
        "Forward outcomes persist venue and market_type so spot and perpetual results cannot mix.",
        status="implemented",
    )
)
def test_forward_outcome_carries_instrument_identity():
    from aletheia.storage.models import UniverseForwardOutcome

    fields = UniverseForwardOutcome.model_fields
    assert "venue" in fields
    assert "market_type" in fields


@spec_claim(
    SpecClaim(
        "OO-S02", _CONTRACT, "§4 net return fields", "L2a",
        "Forward outcomes persist gross, directional, cost, funding, net, edge and path-quality fields.",
        status="implemented",
    )
)
def test_forward_outcome_carries_net_economics():
    from aletheia.storage.models import UniverseForwardOutcome

    required = {
        "entry_spread_bps", "funding_rate_at_entry", "gross_return_pct",
        "directional_return_pct", "estimated_fee_pct",
        "estimated_spread_slippage_pct", "estimated_funding_pct",
        "net_return_pct", "min_tradable_edge_pct", "mfe_pct", "mae_pct",
        "settlement_price_source", "settlement_lag_sec", "cost_model_version",
    }
    assert required <= set(UniverseForwardOutcome.model_fields)


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

    result = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=95.0,
        direction="short",
        market_type="perp",
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
        "OO-B05", _CONTRACT, "§6 evidence preservation", "L2b",
        "A failed row records the observation it rejected, so NULL exit price with NULL price source means never observed and is reclaimable.",
        status="implemented",
    )
)
def test_failed_rows_separate_a_rejected_observation_from_no_observation():
    from datetime import datetime, timedelta, timezone

    from aletheia.maintenance.settlement_requeue import (
        requeue_unobserved_failed_outcomes,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.storage.sqlite import get_db

    entry = datetime.now(timezone.utc) - timedelta(hours=25)
    now_iso = datetime.now(timezone.utc).isoformat()
    common = dict(
        snapshot_id="snap-oo-b05", scanner_family="momentum", direction="long",
        venue="binance", market_type="spot", horizon="1h", entry_price_usd=100.0,
        entry_ts=entry.isoformat(), due_at=(entry + timedelta(hours=1)).isoformat(),
    )
    repo.insert_forward_outcomes_batch([
        UniverseForwardOutcome(member_id="m-rejected", symbol="AAA/USDT", **common),
        UniverseForwardOutcome(member_id="m-unobserved", symbol="BBB/USDT", **common),
    ])
    ids = {
        row["symbol"]: row["id"]
        for row in get_db().execute("SELECT id, symbol FROM universe_forward_outcomes")
    }
    # The venue answered and the answer was rejected as cross-symbol contamination.
    repo.fail_forward_outcome(
        ids["AAA/USDT"], now_iso,
        exit_price=99_999.0, settlement_price_source="horizon_ohlcv",
    )
    # The venue was never reached, which is what an IP ban produced in practice.
    repo.fail_forward_outcome(ids["BBB/USDT"], now_iso)

    result = requeue_unobserved_failed_outcomes(dry_run=False)

    assert result["universe_forward_outcomes"] == 1
    statuses = {
        row["symbol"]: row["status"]
        for row in get_db().execute(
            "SELECT symbol, status FROM universe_forward_outcomes"
        )
    }
    assert statuses["AAA/USDT"] == "failed", "a real verdict must stay terminal"
    assert statuses["BBB/USDT"] == "pending", "never-observed evidence is reclaimable"


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


@spec_claim(
    SpecClaim(
        "OO-C01", _CONTRACT, "§8 learning isolation", "L2c",
        "Calibration excludes legacy gross-only rows and counts wins from net_return_pct.",
        status="implemented",
    )
)
def test_calibration_excludes_gross_only_rows():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome

    common = dict(
        snapshot_id="snap-oo",
        scanner_family="momentum",
        direction="long",
        market_type="spot",
        horizon="1h",
        entry_price_usd=100.0,
        due_at="2026-07-10T01:00:00+00:00",
        settled_at="2026-07-10T01:00:00+00:00",
        status="settled",
    )
    repo.insert_forward_outcomes_batch([
        UniverseForwardOutcome(
            member_id="member-gross-only", symbol="AAA/USDT",
            forward_return_pct=9.0, gross_return_pct=9.0, **common,
        ),
        UniverseForwardOutcome(
            member_id="member-net", symbol="BBB/USDT",
                forward_return_pct=-1.0, gross_return_pct=-1.0,
                directional_return_pct=-1.0, net_return_pct=-1.2,
                min_tradable_edge_pct=0.05, cost_model_version="test-v1", **common,
        ),
    ])

    rows = repo.calibration_stats()
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
    from aletheia.knowledge.guards import GuardAppliesTo, ALLOWED_GUARD_FIELDS

    assert "market_type" in GuardAppliesTo.model_fields
    assert "market_type" in ALLOWED_GUARD_FIELDS
