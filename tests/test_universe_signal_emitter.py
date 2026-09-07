"""Scanner direction inference regressions."""

from aletheia.universe.scanners import _infer_direction


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
