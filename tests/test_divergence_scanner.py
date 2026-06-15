"""Tests for divergence_scanner — Polymarket/crypto price divergence detection."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.agents.divergence_scanner import (
    _map_question_to_symbol,
    _compute_divergence_score,
    run_divergence_scanner,
)
from aletheia.data.providers import Ticker


def _setup_db(tmp_path, monkeypatch):
    """Minimal DB setup for divergence tests."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "knowledge" / "research_notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "daily_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "dreaming_logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()


# --- Unit tests for pure functions ---

class TestKeywordMapping:
    def test_bitcoin_maps_to_btc(self):
        assert _map_question_to_symbol("Will Bitcoin reach $100k?") == "BTC/USDT"

    def test_eth_maps_to_eth(self):
        assert _map_question_to_symbol("Ethereum merge successful?") == "ETH/USDT"

    def test_solana_maps_to_sol(self):
        assert _map_question_to_symbol("Will Solana reach $500?") == "SOL/USDT"

    def test_broad_crypto_maps_to_btc(self):
        assert _map_question_to_symbol("Will crypto market cap exceed $5T?") == "BTC/USDT"

    def test_no_match_returns_none(self):
        assert _map_question_to_symbol("Will it rain tomorrow?") is None

    def test_case_insensitive(self):
        assert _map_question_to_symbol("BITCOIN to $200k by 2027?") == "BTC/USDT"

    def test_specific_over_broad(self):
        # "ethereum" is more specific than "crypto"
        assert _map_question_to_symbol("ethereum crypto market") == "ETH/USDT"


class TestDivergenceScore:
    def test_large_prob_change_high_score(self):
        score = _compute_divergence_score(
            prob_change=0.20, price_change_pct=0.5,
            volume_usd=100_000, liquidity=200_000,
        )
        assert score >= 55

    def test_small_prob_change_low_score(self):
        score = _compute_divergence_score(
            prob_change=0.10, price_change_pct=2.5,
            volume_usd=60_000, liquidity=30_000,
        )
        assert score <= 55

    def test_score_capped_at_70(self):
        score = _compute_divergence_score(
            prob_change=0.50, price_change_pct=0.0,
            volume_usd=500_000, liquidity=500_000,
        )
        assert score <= 70

    def test_score_floor_at_40(self):
        score = _compute_divergence_score(
            prob_change=0.10, price_change_pct=9.0,
            volume_usd=50_000, liquidity=10_000,
        )
        assert score >= 40


# --- Integration test ---

@pytest.mark.asyncio
async def test_divergence_scanner_creates_opportunity(tmp_path, monkeypatch):
    """When a prediction market moved but crypto didn't, scanner creates an opp."""
    _setup_db(tmp_path, monkeypatch)

    # Insert a qualifying prediction market row
    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_test_001",
        "event_id": "ev_test",
        "source": "polymarket",
        "external_market_id": "ext_001",
        "question": "Will Bitcoin reach $150k by end of 2026?",
        "yes_probability": 0.72,
        "no_probability": 0.28,
        "probability_change_24h": 0.15,  # +15pp — qualifies
        "volume_24h": 120000.0,          # >$50k — qualifies
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })

    # Mock ticker: BTC only moved +1% (divergence!)
    mock_ticker = Ticker(
        symbol="BTC/USDT",
        last_price=95000.0,
        volume_24h=5_000_000_000.0,
        change_pct_24h=1.0,
    )
    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=mock_ticker)

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert len(result["errors"]) == 0
    assert any("opportunity:" in o for o in result["outputs"])

    # Verify the opportunity was written
    opps = repo._select_all("opportunities", where="source_agent = ?",
                            params=("divergence_scanner",))
    assert len(opps) == 1
    opp = opps[0]
    assert opp["symbol"] == "BTC/USDT"
    assert opp["asset_class"] == "crypto"
    assert opp["execution_eligible"] == 1
    assert opp["direction_bias"] == "long"
    assert "divergence" in opp["thesis"].lower()

    meta = json.loads(opp["metadata"])
    assert meta["trigger"] == "prediction_divergence"
    assert meta["prob_change_24h"] == 0.15


@pytest.mark.asyncio
async def test_divergence_scanner_no_signal_when_price_moved(tmp_path, monkeypatch):
    """When crypto already moved >3%, no divergence signal."""
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_test_002",
        "event_id": "ev_test",
        "source": "polymarket",
        "external_market_id": "ext_002",
        "question": "Will Ethereum break $5000?",
        "yes_probability": 0.65,
        "no_probability": 0.35,
        "probability_change_24h": 0.12,
        "volume_24h": 80000.0,
        "liquidity": 60000.0,
        "status": "active",
        "updated_at": now,
    })

    # ETH already moved +5% — no divergence
    mock_ticker = Ticker(
        symbol="ETH/USDT",
        last_price=4800.0,
        volume_24h=2_000_000_000.0,
        change_pct_24h=5.0,
    )
    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=mock_ticker)

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert len(result["errors"]) == 0
    # No opportunities created
    opps = repo._select_all("opportunities", where="source_agent = ?",
                            params=("divergence_scanner",))
    assert len(opps) == 0


@pytest.mark.asyncio
async def test_divergence_scanner_no_qualifying_markets(tmp_path, monkeypatch):
    """When no markets meet thresholds, scanner returns early."""
    _setup_db(tmp_path, monkeypatch)

    result = await run_divergence_scanner(permission_mode="Paper")

    assert "no_qualifying_markets" in result["outputs"]
    assert len(result["errors"]) == 0
