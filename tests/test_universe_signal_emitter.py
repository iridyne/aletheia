"""Tests for universe signal_emitter — deterministic direction inference + opportunity emission."""

import pytest
from unittest.mock import patch, MagicMock

from aletheia.universe.signal_emitter import _infer_direction, emit_deterministic_opportunities


# ── _infer_direction ────────────────────────────────────────────────────────

def _feat(**kw):
    base = {"return_1h": None, "return_4h": None, "return_24h": None, "rsi_14": None, "funding_rate": None}
    base.update(kw)
    return base


class TestInferDirection:
    # momentum
    def test_momentum_long(self):
        assert _infer_direction("momentum", _feat(return_1h=2.0, return_4h=4.0, rsi_14=60)) == "long"

    def test_momentum_long_via_4h_rsi(self):
        # 4h positive + RSI > 50 → long (direction now from 4h, not 1h)
        assert _infer_direction("momentum", _feat(return_1h=1.0, return_4h=2.0, rsi_14=55)) == "long"

    def test_momentum_short(self):
        assert _infer_direction("momentum", _feat(return_1h=-2.0, return_4h=-3.0, rsi_14=40)) == "short"

    def test_momentum_short_via_4h_rsi(self):
        # 4h negative + RSI < 50 → short
        assert _infer_direction("momentum", _feat(return_1h=-0.5, return_4h=-1.0, rsi_14=45)) == "short"

    def test_momentum_24h_overextended_rejects(self):
        # 24h moved >10% → mean-reversion zone, reject
        assert _infer_direction("momentum", _feat(return_1h=2.0, return_4h=4.0, rsi_14=60, return_24h=12.0)) is None

    def test_momentum_funding_conflict_rejects(self):
        # Long direction but funding > 0.01 (crowded longs) → reject
        assert _infer_direction("momentum", _feat(return_4h=3.0, rsi_14=60, funding_rate=0.02)) is None

    def test_momentum_ambiguous_conflicting(self):
        # 1h up but 4h down and RSI < 50 → ambiguous
        assert _infer_direction("momentum", _feat(return_1h=1.0, return_4h=-2.0, rsi_14=48)) is None

    def test_momentum_no_data(self):
        assert _infer_direction("momentum", _feat()) is None

    # mean_reversion
    def test_mean_reversion_long_rsi_oversold(self):
        assert _infer_direction("mean_reversion", _feat(rsi_14=25)) == "long"

    def test_mean_reversion_short_rsi_overbought(self):
        assert _infer_direction("mean_reversion", _feat(rsi_14=75)) == "short"

    def test_mean_reversion_long_overextended_down(self):
        assert _infer_direction("mean_reversion", _feat(return_4h=-6.0)) == "long"

    def test_mean_reversion_short_overextended_up(self):
        assert _infer_direction("mean_reversion", _feat(return_4h=6.0)) == "short"

    def test_mean_reversion_rsi_neutral_no_extension(self):
        assert _infer_direction("mean_reversion", _feat(rsi_14=50, return_4h=1.0)) is None

    def test_mean_reversion_no_data(self):
        assert _infer_direction("mean_reversion", _feat()) is None

    # volatility — now uses 4h direction (more stable than 1h noise)
    def test_volatility_long(self):
        assert _infer_direction("volatility", _feat(return_4h=2.0)) == "long"

    def test_volatility_short(self):
        assert _infer_direction("volatility", _feat(return_4h=-2.0)) == "short"

    def test_volatility_no_4h(self):
        assert _infer_direction("volatility", _feat(return_1h=2.0)) is None

    def test_volatility_small_4h_no_signal(self):
        # abs(ret_4h) <= 1.0 → no clear breakout direction
        assert _infer_direction("volatility", _feat(return_4h=0.5)) is None

    def test_volatility_funding_conflict_rejects(self):
        assert _infer_direction("volatility", _feat(return_4h=3.0, funding_rate=0.02)) is None

    # derivatives
    def test_derivatives_long_negative_funding(self):
        assert _infer_direction("derivatives", _feat(funding_rate=-0.01)) == "long"

    def test_derivatives_short_positive_funding(self):
        assert _infer_direction("derivatives", _feat(funding_rate=0.01)) == "short"

    def test_derivatives_neutral_funding_fallback_4h(self):
        assert _infer_direction("derivatives", _feat(funding_rate=0.001, return_4h=3.0)) == "long"

    def test_derivatives_no_data(self):
        assert _infer_direction("derivatives", _feat()) is None

    # event — sharp 1h displacement (mirrors score_event ±4% thresholds)
    def test_event_below_threshold_no_direction(self):
        assert _infer_direction("event", _feat(return_1h=2.0)) is None

    def test_event_sharp_up_long(self):
        assert _infer_direction("event", _feat(return_1h=9.0)) == "long"

    def test_event_sharp_down_short(self):
        assert _infer_direction("event", _feat(return_1h=-9.0)) == "short"

    # truly unknown family
    def test_unknown_family(self):
        assert _infer_direction("nonsense", _feat(return_1h=2.0)) is None

    def test_none_family(self):
        assert _infer_direction(None, _feat(return_1h=2.0)) is None


# ── emit_deterministic_opportunities ───────────────────────────────────────

def _make_member(symbol, family, score, member_id=None, expires="2099-01-01T00:00:00"):
    return {
        "id": member_id or f"m-{symbol}",
        "snapshot_id": "snap-1",
        "symbol": symbol,
        "scanner_family": family,
        "total_score": score,
        "expires_at": expires,
    }


def _make_feat(symbol, **kw):
    feat = {"symbol": symbol, "return_1h": 1.5, "return_4h": 3.0, "rsi_14": 60,
            "funding_rate": None, "reasons": [f"{symbol} 1h +1.5%"]}
    feat.update(kw)
    return feat


class TestEmitDeterministicOpportunities:
    def _run(self, members, feats, existing=None, inserted=None):
        if inserted is None:
            inserted = []

        def _fake_find(symbol, snapshot_id, market=None):
            return existing.get(symbol) if existing else None

        def _fake_insert(opp):
            inserted.append(opp)
            return opp.model_dump(), True

        with patch("aletheia.universe.signal_emitter.find_opportunity_by_symbol_snapshot",
                   side_effect=_fake_find), \
             patch("aletheia.universe.signal_emitter.insert_opportunity_once_by_snapshot",
                   side_effect=_fake_insert), \
             patch("aletheia.universe.signal_emitter._record_emission_decision"):
            count = emit_deterministic_opportunities(members, "snap-1", feats)

        return count, inserted

    def test_emits_for_eligible_member(self):
        members = [_make_member("BTC/USDT", "momentum", 75)]
        feats = [_make_feat("BTC/USDT")]
        count, opps = self._run(members, feats)
        assert count == 1
        opp = opps[0]
        assert opp.symbol == "BTC/USDT"
        assert opp.direction_bias == "long"
        assert opp.source_agent == "universe_scanner"
        assert opp.confidence == "high"
        assert opp.confidence_score == 75

    def test_medium_confidence_below_threshold(self):
        members = [_make_member("ETH/USDT", "momentum", 55)]
        feats = [_make_feat("ETH/USDT")]
        count, opps = self._run(members, feats)
        assert count == 1
        assert opps[0].confidence == "medium"

    def test_high_confidence_at_threshold(self):
        # 60 is the reachable "high" boundary (allocator ceiling ~68.5); a 70
        # threshold made "high" impossible. Score exactly at threshold → high.
        members = [_make_member("BNB/USDT", "momentum", 60)]
        feats = [_make_feat("BNB/USDT")]
        count, opps = self._run(members, feats)
        assert count == 1
        assert opps[0].confidence == "high"

    def test_skips_low_score(self):
        members = [_make_member("LOW/USDT", "momentum", 30)]
        feats = [_make_feat("LOW/USDT")]
        count, _ = self._run(members, feats)
        assert count == 0

    def test_skips_ambiguous_direction(self):
        # momentum with conflicting signals → _infer_direction returns None
        members = [_make_member("AMB/USDT", "momentum", 60)]
        feats = [_make_feat("AMB/USDT", return_1h=1.0, return_4h=-3.0, rsi_14=48)]
        count, _ = self._run(members, feats)
        assert count == 0

    def test_dedup_skips_existing_opportunity(self):
        members = [_make_member("BTC/USDT", "momentum", 75)]
        feats = [_make_feat("BTC/USDT")]
        existing = {"BTC/USDT": {"id": "existing-opp"}}
        count, opps = self._run(members, feats, existing=existing)
        assert count == 0
        assert opps == []

    def test_emits_multiple_members(self):
        members = [
            _make_member("BTC/USDT", "momentum", 80),
            _make_member("ETH/USDT", "mean_reversion", 65),
        ]
        feats = [
            _make_feat("BTC/USDT"),
            _make_feat("ETH/USDT", rsi_14=25),
        ]
        count, opps = self._run(members, feats)
        assert count == 2
        symbols = {o.symbol for o in opps}
        assert "BTC/USDT" in symbols and "ETH/USDT" in symbols

    def test_mean_reversion_direction_correct(self):
        members = [_make_member("ETH/USDT", "mean_reversion", 70)]
        feats = [_make_feat("ETH/USDT", rsi_14=25, return_4h=-3.0)]
        count, opps = self._run(members, feats)
        assert count == 1
        assert opps[0].direction_bias == "long"  # oversold → long

    def test_skips_member_with_no_feature(self):
        members = [_make_member("GHOST/USDT", "momentum", 80)]
        feats = []  # no features for this symbol
        count, _ = self._run(members, feats)
        assert count == 0

    def test_valid_until_set_from_expires_at(self):
        members = [_make_member("BTC/USDT", "momentum", 75, expires="2099-06-01T00:00:00")]
        feats = [_make_feat("BTC/USDT")]
        count, opps = self._run(members, feats)
        assert count == 1
        assert opps[0].valid_until == "2099-06-01T00:00:00"

    def test_perp_eligibility_never_uses_a_spot_snapshot(self):
        members = [{**_make_member("BTC/USDT", "momentum", 75), "market_type": "perp"}]
        feats = [{**_make_feat("BTC/USDT"), "market_type": "perp"}]
        inserted = []

        def _snapshots(*, symbol, market_type, limit):
            assert symbol == "BTC/USDT"
            assert market_type == "perp"
            return []

        with patch("aletheia.universe.signal_emitter.find_opportunity_by_symbol_snapshot", return_value=None), \
             patch(
                 "aletheia.universe.signal_emitter.insert_opportunity_once_by_snapshot",
                 side_effect=lambda opp: (inserted.append(opp) or opp.model_dump(), True),
             ), \
             patch("aletheia.universe.signal_emitter._record_emission_decision"), \
             patch("aletheia.universe.snapshot_freshness.list_market_snapshots", side_effect=_snapshots):
            count = emit_deterministic_opportunities(members, "snap-1", feats)

        assert count == 1
        assert inserted[0].market == "usdt_futures"
        assert inserted[0].execution_eligible is False

    def test_derivatives_context_stamped_on_universe_opportunity(self):
        members = [_make_member("BTC/USDT", "derivatives", 75)]
        feats = [_make_feat(
            "BTC/USDT",
            funding_rate=-0.012,
            open_interest=1_250_000_000.0,
            oi_delta_1h=7.5,
        )]
        count, opps = self._run(members, feats)
        assert count == 1
        opp = opps[0]
        assert opp.funding_rate == -0.012
        assert opp.open_interest == 1_250_000_000.0
        assert opp.oi_delta_1h == 7.5

    def test_thesis_includes_reasons(self):
        members = [_make_member("BTC/USDT", "momentum", 75)]
        feats = [_make_feat("BTC/USDT", reasons=["1h +3.5%", "RSI 65 bullish"])]
        count, opps = self._run(members, feats)
        assert count == 1
        assert "1h +3.5%" in opps[0].thesis
        assert "momentum" in opps[0].thesis

    def test_insert_failure_is_non_fatal(self):
        members = [
            _make_member("BTC/USDT", "momentum", 80),
            _make_member("ETH/USDT", "momentum", 70),
        ]
        feats = [_make_feat("BTC/USDT"), _make_feat("ETH/USDT")]
        inserted = []
        call_count = 0

        def _failing_insert(opp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("db error")
            inserted.append(opp)
            return opp.model_dump(), True

        with patch("aletheia.universe.signal_emitter.find_opportunity_by_symbol_snapshot",
                   return_value=None), \
             patch("aletheia.universe.signal_emitter.insert_opportunity_once_by_snapshot",
                   side_effect=_failing_insert), \
             patch("aletheia.universe.signal_emitter._record_emission_decision"):
            count = emit_deterministic_opportunities(members, "snap-1", feats)

        assert count == 1  # only the second one succeeded
        assert len(inserted) == 1

    def test_every_member_persists_one_terminal_emission_fact(self, tmp_path, monkeypatch):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        members = [
            _make_member("LOW/USDT", "momentum", 30, member_id="member-low"),
            _make_member("MISSING/USDT", "momentum", 70, member_id="member-missing"),
            _make_member("GOOD/USDT", "momentum", 70, member_id="member-good"),
        ]
        feats = [
            _make_feat("LOW/USDT"),
            _make_feat("GOOD/USDT"),
        ]

        with patch(
            "aletheia.universe.signal_emitter.find_opportunity_by_symbol_snapshot",
            return_value=None,
        ), patch(
            "aletheia.universe.signal_emitter.insert_opportunity_once_by_snapshot",
            side_effect=lambda opp: (opp.model_dump(), True),
        ), patch(
            "aletheia.universe.signal_emitter.has_fresh_market_snapshot",
            return_value=True,
        ), patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            count = emit_deterministic_opportunities(members, "snap-1", feats)

        from aletheia.storage.sqlite import get_db

        rows = get_db().execute(
            "SELECT universe_member_id, decision, reason_code FROM filter_decisions "
            "WHERE filter_name='opportunity_emission' ORDER BY universe_member_id"
        ).fetchall()
        facts = {
            row["universe_member_id"]: (row["decision"], row["reason_code"])
            for row in rows
        }
        assert count == 1
        assert facts == {
            "member-good": ("passed", "opportunity_emitted"),
            "member-low": ("rejected", "score_below_minimum"),
            "member-missing": ("rejected", "feature_missing"),
        }
