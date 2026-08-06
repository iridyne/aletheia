"""Pipeline 5: Market Observer signal → position_manager → exit/tighten.

Verifies that Market Observer's opportunity output is correctly consumed
by the position manager's market-aware exit rules. Tests the SEAM between
the observer (writer) and position_manager (reader).
"""

import pytest
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import Opportunity, Position
from aletheia.storage import repositories as repo
from aletheia.portfolio.market_signal_reader import (
    MarketSignalRequest,
    get_market_signals_for_positions,
)


def _seed_instrument(
    db,
    *,
    asset_id: str,
    member_id: str,
    symbol: str,
    venue: str,
    market_type: str,
) -> None:
    observed_at = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.execute(
        "INSERT INTO universe_registry "
        "(id, venue, market_type, symbol, status, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (asset_id, venue, market_type, symbol, observed_at, observed_at),
    )
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, market_type, rank, total_score, "
        "ttl_sec, expires_at) VALUES (?, ?, ?, ?, ?, 1, 90, 3600, ?)",
        (member_id, f"snapshot-{member_id}", asset_id, symbol, market_type, expires),
    )


def _insert_signal(
    *,
    member_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    score: int,
    created_at: datetime | None = None,
    valid_until: datetime | None = None,
    asset_class: str = "crypto",
) -> Opportunity:
    created_at = created_at or datetime.now(timezone.utc)
    valid_until = valid_until or created_at + timedelta(hours=1)
    opportunity = Opportunity(
        symbol=symbol,
        market="usdt_futures" if market_type == "perp" else market_type,
        direction_bias=direction,
        confidence="high" if score >= 75 else "medium",
        confidence_score=score,
        source_agent="market_observer",
        asset_class=asset_class,
        universe_member_id=member_id,
        created_at=created_at.isoformat(),
        valid_until=valid_until.isoformat(),
    )
    repo.insert_opportunity(opportunity)
    return opportunity


class TestSignalReaderConsumeObserverOutput:
    """market_signal_reader correctly reads Market Observer's opportunity rows."""

    def test_reads_direction_and_score(self, e2e_db):
        """The context preserves exact instrument and source provenance."""
        _seed_instrument(
            e2e_db,
            asset_id="asset-sol-spot",
            member_id="member-sol-spot",
            symbol="SOL/USDT",
            venue="binance",
            market_type="spot",
        )
        opportunity = _insert_signal(
            member_id="member-sol-spot",
            symbol="SOL/USDT",
            market_type="spot",
            direction="short",
            score=65,
        )
        request = MarketSignalRequest.from_parts("binance", "SOL/USDT", "spot")

        signals = get_market_signals_for_positions([request])
        ctx = signals[request]
        assert ctx.direction_bias == "short"
        assert ctx.confidence_score == 65
        assert ctx.signal_age_min is not None
        assert ctx.signal_age_min < 1.0
        assert ctx.symbol == "SOL/USDT"
        assert ctx.market_type == "spot"
        assert ctx.venue == "binance"
        assert ctx.source_opportunity_id == opportunity.id
        assert ctx.asset_id == "asset-sol-spot"
        assert ctx.universe_member_id == "member-sol-spot"
        assert ctx.evidence_status == "exact_signal_features_unavailable"

    def test_ignores_non_crypto_opportunities(self, e2e_db):
        """Only asset_class='crypto' opportunities are read."""
        _seed_instrument(
            e2e_db,
            asset_id="asset-equity",
            member_id="member-equity",
            symbol="AAPL/USDT",
            venue="binance",
            market_type="spot",
        )
        _insert_signal(
            member_id="member-equity",
            symbol="AAPL/USDT",
            market_type="spot",
            direction="long",
            score=80,
            asset_class="equity",
        )
        request = MarketSignalRequest.from_parts("binance", "AAPL/USDT", "spot")

        assert request not in get_market_signals_for_positions([request])

    def test_picks_most_recent_for_exact_instrument(self, e2e_db):
        _seed_instrument(
            e2e_db,
            asset_id="asset-eth-spot",
            member_id="member-eth-spot",
            symbol="ETH/USDT",
            venue="binance",
            market_type="spot",
        )
        _insert_signal(
            member_id="member-eth-spot",
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            score=55,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        newest = _insert_signal(
            member_id="member-eth-spot",
            symbol="ETH/USDT",
            market_type="spot",
            direction="short",
            score=78,
        )
        request = MarketSignalRequest.from_parts("binance", "ETH/USDT", "spot")

        ctx = get_market_signals_for_positions([request])[request]
        assert ctx.direction_bias == "short"
        assert ctx.confidence_score == 78
        assert ctx.source_opportunity_id == newest.id

    def test_spot_and_perp_signals_are_independent(self, e2e_db):
        now = datetime.now(timezone.utc)
        _seed_instrument(
            e2e_db,
            asset_id="asset-btc-spot",
            member_id="member-btc-spot",
            symbol="BTC/USDT",
            venue="binance",
            market_type="spot",
        )
        _seed_instrument(
            e2e_db,
            asset_id="asset-btc-perp",
            member_id="member-btc-perp",
            symbol="BTC/USDT",
            venue="binance",
            market_type="perp",
        )
        _insert_signal(
            member_id="member-btc-perp",
            symbol="BTC/USDT",
            market_type="perp",
            direction="long",
            score=68,
            created_at=now - timedelta(minutes=5),
        )
        _insert_signal(
            member_id="member-btc-spot",
            symbol="BTC/USDT",
            market_type="spot",
            direction="short",
            score=90,
            created_at=now,
        )

        spot = MarketSignalRequest.from_parts("binance", "BTC/USDT", "spot")
        perp = MarketSignalRequest.from_parts("binance", "BTC/USDT", "usdt_futures")
        signals = get_market_signals_for_positions([spot, perp])

        assert signals[spot].direction_bias == "short"
        assert signals[spot].market_type == "spot"
        assert signals[perp].direction_bias == "long"
        assert signals[perp].market_type == "perp"

    def test_venue_mismatch_fails_closed(self, e2e_db):
        _seed_instrument(
            e2e_db,
            asset_id="asset-btc-binance",
            member_id="member-btc-binance",
            symbol="BTC/USDT",
            venue="binance",
            market_type="perp",
        )
        _insert_signal(
            member_id="member-btc-binance",
            symbol="BTC/USDT",
            market_type="perp",
            direction="short",
            score=90,
        )
        bitget_request = MarketSignalRequest.from_parts(
            "bitget", "BTC/USDT", "usdt_futures"
        )

        assert bitget_request not in get_market_signals_for_positions([bitget_request])

    def test_position_source_identity_mismatch_fails_closed(self, e2e_db):
        _seed_instrument(
            e2e_db,
            asset_id="asset-source-spot",
            member_id="member-source-spot",
            symbol="BTC/USDT",
            venue="binance",
            market_type="spot",
        )
        _seed_instrument(
            e2e_db,
            asset_id="asset-source-perp",
            member_id="member-source-perp",
            symbol="BTC/USDT",
            venue="binance",
            market_type="perp",
        )
        position_source = _insert_signal(
            member_id="member-source-spot",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            score=60,
        )
        _insert_signal(
            member_id="member-source-perp",
            symbol="BTC/USDT",
            market_type="perp",
            direction="short",
            score=90,
        )
        request = MarketSignalRequest.from_parts(
            "binance",
            "BTC/USDT",
            "perp",
            position_opportunity_id=position_source.id,
        )

        assert request not in get_market_signals_for_positions([request])

    def test_expired_signal_fails_closed(self, e2e_db):
        _seed_instrument(
            e2e_db,
            asset_id="asset-sol-perp",
            member_id="member-sol-perp",
            symbol="SOL/USDT",
            venue="binance",
            market_type="perp",
        )
        _insert_signal(
            member_id="member-sol-perp",
            symbol="SOL/USDT",
            market_type="perp",
            direction="short",
            score=90,
            valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        request = MarketSignalRequest.from_parts("binance", "SOL/USDT", "perp")

        assert request not in get_market_signals_for_positions([request])

    def test_unscoped_signal_fails_closed(self, e2e_db):
        repo.insert_opportunity(
            Opportunity(
                symbol="SOL/USDT",
                market="usdt_futures",
                direction_bias="short",
                confidence="high",
                confidence_score=90,
                source_agent="legacy_observer",
                valid_until=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            )
        )
        request = MarketSignalRequest.from_parts("binance", "SOL/USDT", "perp")

        assert request not in get_market_signals_for_positions([request])

    def test_features_use_exact_asset_not_newest_symbol(self, e2e_db):
        now = datetime.now(timezone.utc)
        _seed_instrument(
            e2e_db,
            asset_id="asset-feature-spot",
            member_id="member-feature-spot",
            symbol="BTC/USDT",
            venue="binance",
            market_type="spot",
        )
        _seed_instrument(
            e2e_db,
            asset_id="asset-feature-perp",
            member_id="member-feature-perp",
            symbol="BTC/USDT",
            venue="binance",
            market_type="perp",
        )
        _insert_signal(
            member_id="member-feature-spot",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            score=70,
        )
        e2e_db.execute(
            "INSERT INTO universe_feature_snapshots "
            "(id, asset_id, ts, rsi_14, return_1h, return_4h, atr_pct, funding_rate) "
            "VALUES ('feature-spot', 'asset-feature-spot', ?, 45.0, -0.5, 2.1, 1.5, NULL)",
            ((now - timedelta(minutes=1)).isoformat(),),
        )
        e2e_db.execute(
            "INSERT INTO universe_feature_snapshots "
            "(id, asset_id, ts, rsi_14, return_1h, return_4h, atr_pct, funding_rate) "
            "VALUES ('feature-perp', 'asset-feature-perp', ?, 99.0, 9.0, 9.0, 9.0, 0.01)",
            (now.isoformat(),),
        )
        request = MarketSignalRequest.from_parts("binance", "BTC/USDT", "spot")

        ctx = get_market_signals_for_positions([request])[request]
        assert ctx.rsi_14 == 45.0
        assert ctx.atr_pct == 1.5
        assert ctx.return_1h == -0.5
        assert ctx.funding_rate is None
        assert ctx.feature_snapshot_id == "feature-spot"
        assert ctx.feature_asset_id == "asset-feature-spot"
        assert ctx.evidence_status == "exact_signal_and_features"


class TestExitRulesConsumeSignal:
    """exit_rules correctly interprets the MarketSignalContext."""

    def test_counter_signal_detected(self, e2e_db):
        """Long position + short signal = counter detected → action."""
        from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
        from aletheia.portfolio.market_signal_reader import MarketSignalContext

        pos = {
            "id": "p1", "symbol": "SOL/USDT", "direction": "long",
            "entry_price": 100.0, "stop_price": None,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "execution_mode": "paper_internal",
        }
        signal = MarketSignalContext(
            symbol="SOL/USDT", direction_bias="short",
            confidence_score=65, signal_age_min=5.0, atr_pct=2.0,
        )

        class Cfg:
            class trading:
                class market_aware_exits:
                    enabled = True
                    min_hold_hours_before_market_exit = 0.5
                    max_signal_age_min = 35.0
                    urgent_exit_score = 80
                    urgent_exit_max_pnl_pct = -0.3
                    tighten_stop_score = 60
                    confidence_collapse_score = 30
                    tighten_atr_mult = 0.8
                    tighten_pct = 0.8

        result = evaluate_market_exit_rules(pos, 98.0, signal, Cfg())
        assert result.action == "tighten_stop"
        assert result.new_stop_price is not None
        # 98 - (98 * 0.02 * 0.8) = 98 - 1.568 = 96.432
        assert abs(result.new_stop_price - 96.432) < 0.01

    def test_confirming_signal_no_action(self, e2e_db):
        """Long position + long signal = confirming → no action."""
        from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
        from aletheia.portfolio.market_signal_reader import MarketSignalContext

        pos = {
            "id": "p2", "symbol": "BTC/USDT", "direction": "long",
            "entry_price": 60000.0, "stop_price": None,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
            "execution_mode": "paper_internal",
        }
        signal = MarketSignalContext(
            symbol="BTC/USDT", direction_bias="long",
            confidence_score=75, signal_age_min=3.0, atr_pct=1.5,
        )

        class Cfg:
            class trading:
                class market_aware_exits:
                    enabled = True
                    min_hold_hours_before_market_exit = 0.5
                    max_signal_age_min = 35.0
                    urgent_exit_score = 80
                    urgent_exit_max_pnl_pct = -0.3
                    tighten_stop_score = 60
                    confidence_collapse_score = 30
                    tighten_atr_mult = 0.8
                    tighten_pct = 0.8

        result = evaluate_market_exit_rules(pos, 61000.0, signal, Cfg())
        assert result.action == "none"
