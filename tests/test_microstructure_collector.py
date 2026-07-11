"""Focused regression tests for orderbook normalization."""

import pytest


def test_parse_orderbook_accepts_ccxt_count_column_and_contract_size():
    from aletheia.data.collectors.microstructure import _parse_orderbook

    snapshot = _parse_orderbook(
        "TEST/USDT",
        "okx",
        {
            "bids": [[99.95, 2.0, 7], [99.80, 4.0, 3]],
            "asks": [[100.05, 3.0, 8], [100.20, 5.0, 2]],
        },
        latency_ms=12,
        contract_size=10.0,
    )

    assert snapshot is not None
    assert snapshot.bid_depth_10bps_usd == pytest.approx(1_999.0)
    assert snapshot.ask_depth_10bps_usd == pytest.approx(3_001.5)
    assert snapshot.total_depth_10bps_usd == pytest.approx(5_000.5)
    assert snapshot.imbalance_ratio == pytest.approx(-0.1429)
