"""Tests for universe signal_emitter — deterministic direction inference + opportunity emission."""

import json

import pytest
from unittest.mock import patch

from aletheia.data.symbols import build_instrument_background, instrument_analysis_context
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
        assert _infer_direction("derivatives", _feat(funding_rate=0.00001, return_4h=3.0)) == "long"

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
        "asset_id": f"asset-{symbol}",
        "venue": "bitget",
        "symbol": symbol,
        "market_type": "spot",
        "scanner_family": family,
        "total_score": score,
        "expires_at": expires,
    }


def _make_feat(symbol, **kw):
    market_type = kw.get("market_type", "spot")
    venue = kw.get("venue", "bitget")
    market_info = kw.get("market_info") or {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "quote": "USDT",
        "info": {"instCategory": "1"} if venue == "okx" else {},
    }
    background = kw.get("instrument_background") or build_instrument_background(
        symbol=symbol,
        venue=venue,
        market_type=market_type,
        market_info=market_info,
    )
    feat = {"symbol": symbol, "asset_id": f"asset-{symbol}", "venue": venue,
            "market_type": market_type, "return_1h": 1.5, "return_4h": 3.0,
            "rsi_14": 60, "funding_rate": None,
            "reasons": [f"{symbol} 1h +1.5%"],
            "instrument_background": background,
            "analysis_context": instrument_analysis_context(background)}
    feat.update(kw)
    return feat


def _momentum_bars() -> list[dict]:
    rows = [
        {"ts": i * 900_000, "open": 100.0, "high": 109.0, "low": 91.0, "close": 100.0, "volume": 10.0}
        for i in range(20)
    ]
    rows[-1]["high"] = 110.0
    rows.append({"ts": 20 * 900_000, "open": 100.0, "high": 112.0, "low": 98.0, "close": 111.0, "volume": 20.0})
    return rows


def _insert_market_price(
    repo,
    symbol="BTC/USDT",
    market_type="spot",
    price=100.0,
    exchange="bitget",
):
    from aletheia.storage.models import MarketSnapshot

    repo.insert_market_snapshot(
        MarketSnapshot(
            symbol=symbol,
            market_type=market_type,
            exchange=exchange,
            price=price,
        )
    )


def test_build_setup_preserves_funding_observed_at():
    from aletheia.universe.signal_emitter import _build_setup

    observed_at = "2026-08-26T11:45:00+00:00"
    setup = _build_setup(
        member={
            "id": "member-1",
            "asset_id": "asset-1",
            "total_score": 75,
            "expires_at": "2026-08-26T13:00:00+00:00",
        },
        feature={
            "asset_id": "asset-1",
            "venue": "bitget",
            "funding_rate": 0.0004,
            "funding_observed_at": observed_at,
            "reasons": ["funding ok"],
        },
        snapshot_id="snapshot-1",
        member_index=0,
        symbol="BTC/USDT",
        market_type="perp",
        family="momentum",
        direction="long",
        material_fingerprint="fingerprint-1",
        regime_tag="bull",
    )

    motive = json.loads(setup.motive_evidence)
    assert setup.funding_observed_at == observed_at
    assert motive["funding_observed_at"] == observed_at


class TestEmitDeterministicOpportunities:
    def _run(self, members, feats):
        decisions = []

        def _record(**kwargs):
            decisions.append(kwargs)

        with patch(
            "aletheia.universe.signal_emitter._record_emission_decision",
            side_effect=_record,
        ), patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            count = emit_deterministic_opportunities(members, "snap-1", feats)

        return count, decisions

    def test_non_momentum_candidate_arms_setup_without_exact_market_fact(self):
        members = [_make_member("BTC/USDT", "mean_reversion", 75)]
        feats = [_make_feat("BTC/USDT", rsi_14=25)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["decision"] == "passed"
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"
        assert decisions[0]["direction"] == "long"

    def test_non_momentum_candidate_score_threshold_is_not_opportunity_confidence(self):
        members = [_make_member("ETH/USDT", "mean_reversion", 55)]
        feats = [_make_feat("ETH/USDT", rsi_14=25)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"

    def test_high_score_candidate_still_waits_for_exact_entry_fact(self):
        members = [_make_member("BNB/USDT", "mean_reversion", 60)]
        feats = [_make_feat("BNB/USDT", rsi_14=25)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["inputs"]["setup_id"]
        assert decisions[0]["inputs"]["has_exact_market_fact"] is False

    def test_momentum_candidate_persists_armed_setup_without_exact_market_fact(self, tmp_path, monkeypatch):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from aletheia.storage.sqlite import get_db

        member = {
            **_make_member("BTC/USDT", "momentum", 75, member_id="member-setup"),
            "market_type": "perp",
        }
        feat = {
            **_make_feat(
                "BTC/USDT",
                market_type="perp",
                atr_15m_pct=1.0,
                _closed_ohlcv_15m=_momentum_bars(),
            ),
        }
        with patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            count = emit_deterministic_opportunities([member], "snap-1", [feat])

        assert count == 0
        setup = get_db().execute(
            "SELECT member_id,state,symbol,market_type,direction FROM universe_setups"
        ).fetchone()
        assert dict(setup) == {
            "member_id": "member-setup",
            "state": "armed",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "direction": "long",
        }
        assert get_db().execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0

    def test_initially_entry_eligible_candidate_triggers_v2_opportunity(
        self, tmp_path, monkeypatch
    ):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        _insert_market_price(repo, symbol="ETH/USDT", price=100.0)
        member = _make_member(
            "ETH/USDT", "mean_reversion", 75, member_id="member-trigger"
        )
        feat = _make_feat("ETH/USDT", rsi_14=25)
        with patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            count = emit_deterministic_opportunities([member], "snap-1", [feat])

        assert count == 1
        row = get_db().execute(
            "SELECT source_agent,setup_id,generation_version,signal_mark "
            "FROM opportunities WHERE universe_member_id='member-trigger'"
        ).fetchone()
        assert row["source_agent"] == "universe_scanner"
        assert row["setup_id"]
        assert row["generation_version"] == "opportunity-generation/v2"
        assert row["signal_mark"] == "100.0"
        setup = get_db().execute(
            "SELECT state,scanner_family FROM universe_setups WHERE id=?",
            (row["setup_id"],),
        ).fetchone()
        assert dict(setup) == {
            "state": "executed",
            "scanner_family": "mean_reversion",
        }

    def test_emits_supported_stock_linked_perp_without_crypto_regime(
        self, tmp_path, monkeypatch
    ):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        background = build_instrument_background(
            symbol="SOXL/USDT:USDT",
            venue="binance",
            market_type="perp",
            market_info={
                "symbol": "SOXL/USDT:USDT",
                "base": "SOXL",
                "quote": "USDT",
                "info": {
                    "contractType": "TRADIFI_PERPETUAL",
                    "underlyingType": "EQUITY",
                },
            },
        )
        member = {
            **_make_member(
                "SOXL/USDT",
                "momentum",
                75,
                member_id="member-stock-linked",
            ),
            "venue": "binance",
            "market_type": "perp",
        }
        feature = _make_feat(
            "SOXL/USDT",
            market_type="perp",
            venue="binance",
            instrument_background=background,
            analysis_context=instrument_analysis_context(background),
        )
        _insert_market_price(
            repo,
            symbol="SOXL/USDT",
            market_type="perp",
            price=100.0,
            exchange="binance",
        )

        with patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "blocked_candidate"},
        ):
            count = emit_deterministic_opportunities(
                [member],
                "snap-1",
                [feature],
                regime=type("Regime", (), {
                    "state": "bear",
                    "confidence": 1.0,
                    "long_bias": 0.0,
                    "short_bias": 1.0,
                })(),
            )

        assert count == 1
        row = get_db().execute(
            "SELECT market,asset_class,asset_subclass,execution_eligible,"
            "setup_id,generation_version FROM opportunities "
            "WHERE universe_member_id='member-stock-linked'"
        ).fetchone()
        assert dict(row) == {
            "market": "perp",
            "asset_class": "equity",
            "asset_subclass": "stock_linked_perp",
            "execution_eligible": 1,
            "setup_id": row["setup_id"],
            "generation_version": "opportunity-generation/v2",
        }

    def test_rejects_arbitrary_unsupported_equity_context(self):
        members = [{
            **_make_member("ACME/USDT", "momentum", 75),
            "market_type": "perp",
        }]
        feature = _make_feat("ACME/USDT", market_type="perp")
        feature["instrument_background"].update({
            "asset_class": "equity",
            "product_kind": "tradifi_perpetual",
        })
        feature["analysis_context"] = None

        count, decisions = self._run(members, [feature])

        assert count == 0
        assert decisions[0]["reason_code"] == "unsupported_analysis_context:tradifi_perpetual"

    @pytest.mark.parametrize(
        ("family", "feature_overrides", "expected_direction"),
        [
            ("momentum", {"return_4h": 3.0, "rsi_14": 60}, "long"),
            ("mean_reversion", {"rsi_14": 25}, "long"),
            ("volatility", {"return_4h": -2.0}, "short"),
            ("derivatives", {"funding_rate": 0.01}, "short"),
            ("event", {"return_1h": 6.0}, "long"),
            ("short_bias", {"scanner_direction": "short"}, "short"),
        ],
    )
    def test_all_current_universe_families_share_setup_trigger_lifecycle(
        self, family, feature_overrides, expected_direction, tmp_path, monkeypatch
    ):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        symbol = f"{family.upper()}/USDT"
        _insert_market_price(repo, symbol=symbol, market_type="spot", price=100.0)
        member = _make_member(symbol, family, 80, member_id=f"member-{family}")
        feat = _make_feat(symbol, **feature_overrides)
        with patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            count = emit_deterministic_opportunities([member], "snap-1", [feat])

        assert count == 1
        opportunity = get_db().execute(
            "SELECT direction_bias,scanner_family,setup_id,generation_json "
            "FROM opportunities WHERE universe_member_id=?",
            (f"member-{family}",),
        ).fetchone()
        assert opportunity["direction_bias"] == expected_direction
        assert opportunity["scanner_family"] == family
        setup = get_db().execute(
            "SELECT state,scanner_family,trigger_spec_version FROM universe_setups "
            "WHERE id=?",
            (opportunity["setup_id"],),
        ).fetchone()
        assert dict(setup) == {
            "state": "executed",
            "scanner_family": family,
            "trigger_spec_version": "universe-entry-eligibility/v1",
        }

    def test_skips_low_score(self):
        members = [_make_member("LOW/USDT", "momentum", 30)]
        feats = [_make_feat("LOW/USDT")]
        count, _ = self._run(members, feats)
        assert count == 0

    def test_skips_ambiguous_direction(self):
        # momentum with conflicting signals → _infer_direction returns None
        members = [_make_member("AMB/USDT", "momentum", 60)]
        feats = [_make_feat("AMB/USDT", return_1h=1.0, return_4h=-3.0, rsi_14=48)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "direction_missing"

    def test_regime_rejection_seeds_exact_instrument_outcomes(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        member = {
            **_make_member("BTC/USDT", "momentum", 55, member_id="member-regime"),
            "venue": "okx",
            "market_type": "perp",
            "entry_price_usd": 100.0,
            "spread_bps": 7.0,
            "funding_rate": 0.0005,
        }
        regime = SimpleNamespace(state="bear", long_bias=0.1, short_bias=0.9)

        with patch(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            return_value={"status": "watch"},
        ):
            assert emit_deterministic_opportunities(
                [member], "snap-1", [_make_feat("BTC/USDT", market_type="perp", venue="okx")],
                regime=regime,
            ) == 0

        from aletheia.storage.sqlite import get_db

        decision = get_db().execute(
            "SELECT reason_code, outcome_candidate_key FROM filter_decisions "
            "WHERE universe_member_id='member-regime'"
        ).fetchone()
        outcomes = get_db().execute(
            "SELECT venue, market_type, direction, horizon "
            "FROM filter_candidate_outcomes WHERE outcome_candidate_key=?",
            (decision["outcome_candidate_key"],),
        ).fetchall()
        assert decision["reason_code"] == "regime_filtered"
        assert {row["horizon"] for row in outcomes} == {"15m", "1h", "4h", "24h"}
        assert {(row["venue"], row["market_type"], row["direction"]) for row in outcomes} == {
            ("okx", "perp", "long")
        }

    def test_funding_crowding_rejection_preserves_counterfactual_side(
        self, tmp_path, monkeypatch
    ):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        member = {
            **_make_member("BTC/USDT", "momentum", 60, member_id="member-crowded"),
            "venue": "okx",
            "market_type": "perp",
            "entry_price_usd": 100.0,
            "spread_bps": 5.0,
            "funding_rate": 0.02,
        }

        assert emit_deterministic_opportunities(
            [member],
            "snap-1",
            [_make_feat(
                "BTC/USDT",
                market_type="perp",
                venue="okx",
                return_4h=3.0,
                rsi_14=60,
                funding_rate=0.02,
            )],
        ) == 0

        from aletheia.storage.sqlite import get_db

        decision = get_db().execute(
            "SELECT reason_code, direction, outcome_candidate_key FROM filter_decisions "
            "WHERE universe_member_id='member-crowded'"
        ).fetchone()
        outcomes = get_db().execute(
            "SELECT DISTINCT direction FROM filter_candidate_outcomes "
            "WHERE outcome_candidate_key=?",
            (decision["outcome_candidate_key"],),
        ).fetchall()
        assert (decision["reason_code"], decision["direction"]) == (
            "funding_crowding_conflict",
            "long",
        )
        assert [row["direction"] for row in outcomes] == ["long"]

    def test_multiple_non_momentum_members_arm_setups_without_market_facts(self):
        members = [
            _make_member("BTC/USDT", "volatility", 80),
            _make_member("ETH/USDT", "mean_reversion", 65),
        ]
        feats = [
            _make_feat("BTC/USDT"),
            _make_feat("ETH/USDT", rsi_14=25),
        ]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert [d["reason_code"] for d in decisions] == [
            "setup_armed_entry_not_eligible",
            "setup_armed_entry_not_eligible",
        ]

    def test_mean_reversion_direction_correct(self):
        members = [_make_member("ETH/USDT", "mean_reversion", 70)]
        feats = [_make_feat("ETH/USDT", rsi_14=25, return_4h=-3.0)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["direction"] == "long"  # oversold → long

    def test_skips_member_with_no_feature(self):
        members = [_make_member("GHOST/USDT", "momentum", 80)]
        feats = []  # no features for this symbol
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "feature_missing"

    def test_candidate_ttl_is_not_opportunity_valid_until(self):
        members = [_make_member("BTC/USDT", "mean_reversion", 75, expires="2099-06-01T00:00:00")]
        feats = [_make_feat("BTC/USDT", rsi_14=25)]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"

    def test_perp_candidate_does_not_check_spot_snapshot_for_execution_eligibility(
        self, tmp_path, monkeypatch
    ):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from aletheia.storage import repositories as repo

        _insert_market_price(repo, symbol="BTC/USDT", market_type="spot", price=100.0)
        members = [{**_make_member("BTC/USDT", "volatility", 75), "market_type": "perp"}]
        feats = [_make_feat("BTC/USDT", market_type="perp")]

        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["market_type"] == "perp"
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"

    def test_derivatives_context_remains_candidate_evidence_not_opportunity(self):
        members = [_make_member("BTC/USDT", "derivatives", 75)]
        feats = [_make_feat(
            "BTC/USDT",
            funding_rate=-0.012,
            open_interest=1_250_000_000.0,
            oi_delta_1h=7.5,
        )]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"

    def test_candidate_reasons_are_not_rewritten_into_opportunity_thesis(self):
        members = [_make_member("BTC/USDT", "mean_reversion", 75)]
        feats = [_make_feat("BTC/USDT", rsi_14=25, reasons=["RSI 25 oversold"])]
        count, decisions = self._run(members, feats)
        assert count == 0
        assert decisions[0]["reason_code"] == "setup_armed_entry_not_eligible"

    def test_every_member_persists_one_terminal_candidate_fact(self, tmp_path, monkeypatch):
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        members = [
            _make_member("LOW/USDT", "momentum", 30, member_id="member-low"),
            _make_member("MISSING/USDT", "momentum", 70, member_id="member-missing"),
            _make_member("GOOD/USDT", "mean_reversion", 70, member_id="member-good"),
        ]
        feats = [
            _make_feat("LOW/USDT"),
            _make_feat("GOOD/USDT", rsi_14=25),
        ]

        with patch(
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
        assert count == 0
        assert facts == {
            "member-good": ("passed", "setup_armed_entry_not_eligible"),
            "member-low": ("rejected", "score_below_minimum"),
            "member-missing": ("rejected", "feature_missing"),
        }

    def test_rejection_evidence_failure_is_not_silent(self):
        members = [
            _make_member(
                "LOW/USDT", "momentum", 30, member_id="member-low-retry"
            ),
        ]
        feats = [_make_feat("LOW/USDT")]

        with patch(
            "aletheia.analytics.filter_evidence.record_filter_decision",
            side_effect=RuntimeError("db unavailable"),
        ), pytest.raises(
            RuntimeError,
            match=(
                "opportunity_emission_evidence_failed:"
                "snap-1:member-low-retry"
            ),
        ):
            emit_deterministic_opportunities(members, "snap-1", feats)

    @pytest.mark.parametrize("caller_owns_transaction", [False, True])
    def test_candidate_pass_rolls_back_when_evidence_fails(
        self, tmp_path, monkeypatch, caller_owns_transaction
    ):
        """A candidate pass without its durable fact is retryable, not success."""
        from tests.test_entry_logic import setup_env

        setup_env(tmp_path, monkeypatch)
        from contextlib import nullcontext
        from aletheia.analytics import filter_evidence
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db
        from aletheia.universe.signal_emitter import EmissionEvidenceError

        members = [
            _make_member(
                "BTC/USDT", "mean_reversion", 70, member_id="member-emission-atomic"
            ),
        ]
        feats = [_make_feat("BTC/USDT", rsi_14=25)]
        real_insert = filter_evidence.repo.insert_filter_decision

        def fail_only_emitted_fact(fact):
            if (
                fact.filter_name == "opportunity_emission"
                and fact.reason_code == "setup_armed_entry_not_eligible"
            ):
                raise RuntimeError("candidate evidence unavailable")
            return real_insert(fact)

        monkeypatch.setattr(
            filter_evidence.repo,
            "insert_filter_decision",
            fail_only_emitted_fact,
        )
        monkeypatch.setattr(
            "aletheia.universe.signal_emitter.lookup_factor_signal",
            lambda *_args, **_kwargs: {"status": "watch"},
        )

        db = get_db()
        caller_transaction = (
            repo.transaction() if caller_owns_transaction else nullcontext()
        )
        with caller_transaction:
            with pytest.raises(
                EmissionEvidenceError,
                match=(
                    "opportunity_emission_evidence_failed:"
                    "snap-1:member-emission-atomic"
                ),
            ):
                emit_deterministic_opportunities(members, "snap-1", feats)

            assert db.execute(
                "SELECT COUNT(*) FROM opportunities WHERE universe_member_id=?",
                ("member-emission-atomic",),
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM filter_decisions WHERE universe_member_id=? "
                "AND reason_code='setup_armed_entry_not_eligible'",
                ("member-emission-atomic",),
            ).fetchone()[0] == 0


def _persist_emitter_snapshot(repo, snapshot_id: str, member_id: str) -> dict:
    from aletheia.storage.models import UniverseMember, UniverseSnapshot

    repo.insert_universe_snapshot(
        UniverseSnapshot(id=snapshot_id, market_type="spot", universe_size=1)
    )
    member = UniverseMember(
        id=member_id,
        snapshot_id=snapshot_id,
        asset_id=f"asset-{member_id}",
        symbol="BTC/USDT",
        market_type="spot",
        rank=1,
        total_score=75.0,
        scanner_family="mean_reversion",
        scanner_direction="long",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_universe_member(member)
    return member.model_dump()


def test_publication_replaces_open_generation_before_snapshot_cascade(
    tmp_path, monkeypatch
):
    """Candidate refresh no longer creates open Opportunity generations."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        lambda *_args, **_kwargs: {"status": "watch"},
    )
    feature = _make_feat("BTC/USDT", market_type="spot", rsi_14=25)
    first_member = _persist_emitter_snapshot(repo, "snap-integrity-1", "member-integrity-1")
    assert emit_deterministic_opportunities(
        [first_member], "snap-integrity-1", [feature]
    ) == 0

    second_member = _persist_emitter_snapshot(repo, "snap-integrity-2", "member-integrity-2")
    assert emit_deterministic_opportunities(
        [second_member], "snap-integrity-2", [feature], allow_open_replacement=True
    ) == 0

    repo.expire_universe_snapshots("spot", except_id="snap-integrity-2")
    assert repo.expire_opportunities_from_superseded_snapshots("spot") == 0


def test_queued_material_identity_suppresses_unchanged_and_reopens_changed_setup(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    _insert_market_price(repo, symbol="BTC/USDT", market_type="spot", price=100.0)

    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        lambda *_args, **_kwargs: {"status": "watch"},
    )
    first_member = _persist_emitter_snapshot(repo, "snap-material-1", "member-material-1")
    feature = _make_feat("BTC/USDT", market_type="spot", rsi_14=25)
    assert emit_deterministic_opportunities(
        [first_member], "snap-material-1", [feature]
    ) == 1

    unchanged_member = _persist_emitter_snapshot(repo, "snap-material-2", "member-material-2")
    assert emit_deterministic_opportunities(
        [unchanged_member], "snap-material-2", [feature]
    ) == 0

    changed_member = _persist_emitter_snapshot(repo, "snap-material-3", "member-material-3")
    changed = _make_feat("BTC/USDT", market_type="spot", return_4h=9.0, rsi_14=20)
    assert emit_deterministic_opportunities(
        [changed_member], "snap-material-3", [changed]
    ) == 1
    assert get_db().execute("SELECT COUNT(*) FROM universe_setups").fetchone()[0] == 2
    assert get_db().execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 2


def test_reprocessing_same_entry_transition_is_idempotent(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    _insert_market_price(repo, symbol="BTC/USDT", market_type="spot", price=100.0)
    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        lambda *_args, **_kwargs: {"status": "watch"},
    )
    member = _persist_emitter_snapshot(repo, "snap-idem-1", "member-idem-1")
    feature = _make_feat("BTC/USDT", market_type="spot", rsi_14=25)

    assert emit_deterministic_opportunities([member], "snap-idem-1", [feature]) == 1
    assert emit_deterministic_opportunities([member], "snap-idem-1", [feature]) == 0
    assert get_db().execute("SELECT COUNT(*) FROM universe_setups").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
