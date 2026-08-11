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

    def test_broad_crypto_does_not_invent_btc_proxy(self):
        assert _map_question_to_symbol("Will crypto market cap exceed $5T?") is None

    def test_no_match_returns_none(self):
        assert _map_question_to_symbol("Will it rain tomorrow?") is None

    def test_case_insensitive(self):
        assert _map_question_to_symbol("BITCOIN to $200k by 2027?") == "BTC/USDT"

    def test_specific_over_broad(self):
        # "ethereum" is more specific than "crypto"
        assert _map_question_to_symbol("ethereum crypto market") == "ETH/USDT"

    @pytest.mark.parametrize(
        "question",
        [
            "Will BitcoinCash exceed $1,000?",
            "Will Bitcoin or Ethereum exceed $100k?",
            "Will the crypto market exceed $5T?",
        ],
    )
    def test_asset_mapping_requires_one_exact_asset(self, question):
        assert _map_question_to_symbol(question) is None


class TestPredictionPolarity:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Will Bitcoin rise above $150k?", 1),
            ("Will Ethereum exceed $5,000?", 1),
            ("Will Bitcoin fall below $70k?", -1),
            ("Will Solana drop under $80?", -1),
            ("Will Bitcoin fail to reach $150k?", None),
            ("Will Bitcoin avoid falling below $70k?", None),
            ("Will Bitcoin not fall below $70k?", None),
            ("Will Bitcoin never exceed $150k?", None),
            ("Will Bitcoin be legal tender in another country?", None),
            ("Will Bitcoin rise above $150k or fall below $70k?", None),
            ("Will Bitcoin rise this year?", None),
            ("Will Bitcoin fall after the election?", None),
        ],
    )
    def test_question_polarity_is_deterministic_and_ambiguous_fails_closed(
        self, question, expected,
    ):
        from aletheia.agents.divergence_scanner import _prediction_polarity

        assert _prediction_polarity(question) == expected


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

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_score_rejects_nonfinite_evidence(self, bad):
        with pytest.raises(ValueError, match="finite"):
            _compute_divergence_score(bad, 1.0, 100_000.0, 100_000.0)


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
        replay = await run_divergence_scanner(permission_mode="Paper")

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
    assert opp["market"] == "spot"
    assert datetime.fromisoformat(opp["valid_until"]) == (
        datetime.fromisoformat(now) + timedelta(hours=2)
    )
    assert "divergence" in opp["thesis"].lower()

    meta = json.loads(opp["metadata"])
    assert meta["trigger"] == "prediction_divergence"
    assert meta["prob_change_24h"] == 0.15
    assert meta["prediction_market_id"] == "pm_test_001"
    assert meta["prediction_external_market_id"] == "ext_001"
    assert meta["prediction_updated_at"] == now
    assert meta["instrument_market"] == "spot"
    assert meta["source_version"].startswith("prediction_divergence:polarity-v1:")
    assert meta["prediction_polarity_version"] == "polarity-v1"
    manifest = meta["evidence_manifest"]
    assert manifest["version"] == "opportunity-information-integrity-v1"
    assert manifest["policy_epoch"] == "opportunity_information_integrity/v2"
    assert manifest["t0"]["trigger_type"] == "prediction_divergence"
    assert manifest["t0"]["prediction_market_id"] == "pm_test_001"
    assert manifest["t0"]["prediction_updated_at"] == now
    assert manifest["t0"]["source_version"] == meta["source_version"]
    assert manifest["t1"] == {"status": "unavailable"}
    assert any(
        output.startswith("skip_duplicate_source_version:")
        for output in replay["outputs"]
    )
    evidence = repo._select_all(
        "filter_decisions", where="filter_name = ?", params=("prediction_divergence",),
    )
    assert json.loads(evidence[0]["parameter_snapshot"])[
        "prediction_polarity_version"
    ] == "polarity-v1"
    mock_provider.get_ticker.assert_awaited_once_with("BTC/USDT")


@pytest.mark.asyncio
async def test_divergence_scanner_short_requires_explicit_perp_fact(tmp_path, monkeypatch):
    """A bearish prediction cannot be relabeled as futures without a perp fact."""
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_test_short",
        "event_id": "ev_test_short",
        "source": "polymarket",
        "external_market_id": "ext_short",
        "question": "Will Bitcoin fall below $70k by end of 2026?",
        "yes_probability": 0.35,
        "no_probability": 0.65,
        "probability_change_24h": 0.15,
        "volume_24h": 120000.0,
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })

    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT",
        last_price=95000.0,
        volume_24h=5_000_000_000.0,
        change_pct_24h=-1.0,
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert result["errors"] == []
    assert "skip:BTC/USDT:short_requires_perp_instrument" in result["outputs"]
    assert repo._select_all(
        "opportunities", where="source_agent = ?", params=("divergence_scanner",)
    ) == []
    mock_provider.get_ticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_question_probability_drop_maps_to_long(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_negative_inverse",
        "event_id": "ev_negative_inverse",
        "source": "polymarket",
        "external_market_id": "ext_negative_inverse",
        "question": "Will Bitcoin fall below $70k by end of 2026?",
        "yes_probability": 0.35,
        "no_probability": 0.65,
        "probability_change_24h": -0.15,
        "volume_24h": 120000.0,
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })
    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT",
        last_price=95000.0,
        volume_24h=5_000_000_000.0,
        change_pct_24h=1.0,
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert result["errors"] == []
    opps = repo._select_all(
        "opportunities", where="source_agent = ?", params=("divergence_scanner",),
    )
    assert len(opps) == 1
    assert opps[0]["direction_bias"] == "long"


@pytest.mark.asyncio
async def test_ambiguous_prediction_question_fails_closed(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_ambiguous",
        "event_id": "ev_ambiguous",
        "source": "polymarket",
        "external_market_id": "ext_ambiguous",
        "question": "Will Bitcoin be legal tender in another country?",
        "yes_probability": 0.72,
        "no_probability": 0.28,
        "probability_change_24h": 0.15,
        "volume_24h": 120000.0,
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })
    mock_provider = AsyncMock()

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert result["errors"] == []
    assert "skip:BTC/USDT:prediction_polarity_ambiguous" in result["outputs"]
    assert repo._select_all(
        "opportunities", where="source_agent = ?", params=("divergence_scanner",),
    ) == []
    mock_provider.get_ticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonfinite_ticker_evidence_fails_closed(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_nonfinite",
        "event_id": "ev_nonfinite",
        "source": "polymarket",
        "external_market_id": "ext_nonfinite",
        "question": "Will Bitcoin exceed $150k?",
        "yes_probability": 0.72,
        "no_probability": 0.28,
        "probability_change_24h": 0.15,
        "volume_24h": 120000.0,
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })
    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT",
        last_price=float("nan"),
        volume_24h=5_000_000_000.0,
        change_pct_24h=float("nan"),
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert "skip:BTC/USDT:invalid_numeric_evidence" in result["outputs"]
    assert repo.list_opportunities(limit=10) == []


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


@pytest.mark.asyncio
async def test_divergence_rejections_have_stable_terminal_evidence(tmp_path, monkeypatch):
    """Every qualifying source row is durably accounted for when it is rejected."""
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert

    rows = [
        {
            "id": "pm_unmapped",
            "event_id": "ev_unmapped",
            "source": "polymarket",
            "external_market_id": "ext_unmapped",
            "question": "Will it rain in London tomorrow?",
            "yes_probability": 0.7,
            "no_probability": 0.3,
            "probability_change_24h": 0.12,
            "volume_24h": 80_000.0,
            "liquidity": 60_000.0,
            "status": "active",
            "updated_at": now,
        },
        {
            "id": "pm_short",
            "event_id": "ev_short",
            "source": "polymarket",
            "external_market_id": "ext_short_terminal",
            "question": "Will Bitcoin fall below $70k?",
            "yes_probability": 0.3,
            "no_probability": 0.7,
            "probability_change_24h": 0.12,
            "volume_24h": 80_000.0,
            "liquidity": 60_000.0,
            "status": "active",
            "updated_at": now,
        },
        {
            "id": "pm_already_priced",
            "event_id": "ev_already_priced",
            "source": "polymarket",
            "external_market_id": "ext_already_priced",
            "question": "Will Ethereum break $5000?",
            "yes_probability": 0.7,
            "no_probability": 0.3,
            "probability_change_24h": 0.12,
            "volume_24h": 80_000.0,
            "liquidity": 60_000.0,
            "status": "active",
            "updated_at": now,
        },
    ]
    for row in rows:
        _insert("prediction_markets", row)

    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="ETH/USDT",
        last_price=5000.0,
        volume_24h=2_000_000_000.0,
        change_pct_24h=5.0,
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")
        replay = await run_divergence_scanner(permission_mode="Paper")

    assert result["errors"] == []
    assert replay["errors"] == []
    evidence = repo._select_all(
        "filter_decisions",
        where="filter_name = ?",
        params=("prediction_divergence",),
        order="reason_code ASC",
    )
    assert len(evidence) == 3
    assert len({row["candidate_key"] for row in evidence}) == 3
    assert all(
        row["candidate_key"].startswith("prediction_observation:")
        for row in evidence
    )
    assert {row["reason_code"] for row in evidence} == {
        "price_already_moved",
        "short_requires_perp_instrument",
        "symbol_unmapped",
    }


@pytest.mark.asyncio
async def test_divergence_evidence_failure_cannot_report_success(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_evidence_failure",
        "event_id": "ev_evidence_failure",
        "source": "polymarket",
        "external_market_id": "ext_evidence_failure",
        "question": "Will Bitcoin reach $150k?",
        "yes_probability": 0.72,
        "no_probability": 0.28,
        "probability_change_24h": 0.15,
        "volume_24h": 120_000.0,
        "liquidity": 80_000.0,
        "status": "active",
        "updated_at": now,
    })

    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT",
        last_price=95_000.0,
        volume_24h=5_000_000_000.0,
        change_pct_24h=1.0,
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ), patch(
        "aletheia.analytics.filter_evidence.record_filter_decision",
        side_effect=RuntimeError("db unavailable"),
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    assert not any(output.startswith("opportunity:") for output in result["outputs"])
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith(
        "divergence_evidence_failed:prediction_observation:"
    )
    assert repo.list_opportunities(limit=10) == [], (
        "Opportunity creation and its terminal producer evidence must be atomic"
    )


@pytest.mark.asyncio
async def test_provider_closed_on_success(tmp_path, monkeypatch):
    """#255: the per-call provider must be closed exactly once on success."""
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_close_test",
        "event_id": "ev_close",
        "source": "polymarket",
        "external_market_id": "ext_close",
        "question": "Will Bitcoin exceed $200k?",
        "yes_probability": 0.72,
        "no_probability": 0.28,
        "probability_change_24h": 0.15,
        "volume_24h": 120000.0,
        "liquidity": 80000.0,
        "status": "active",
        "updated_at": now,
    })
    from aletheia.data.providers import Ticker
    mock_provider = AsyncMock()
    mock_provider.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT",
        last_price=95000.0,
        volume_24h=5_000_000_000.0,
        change_pct_24h=0.5,
    ))

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        await run_divergence_scanner(permission_mode="Paper")

    mock_provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_closed_on_candidate_rejection(tmp_path, monkeypatch):
    """#255: the per-call provider must be closed even when all candidates are rejected."""
    _setup_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc).isoformat()
    from aletheia.storage.repositories import _insert
    _insert("prediction_markets", {
        "id": "pm_close_reject",
        "event_id": "ev_close_reject",
        "source": "polymarket",
        "external_market_id": "ext_close_reject",
        "question": "Will Bitcoin be legal tender in another country?",
        "yes_probability": 0.60,
        "no_probability": 0.40,
        "probability_change_24h": 0.20,
        "volume_24h": 100000.0,
        "liquidity": 70000.0,
        "status": "active",
        "updated_at": now,
    })
    mock_provider = AsyncMock()

    with patch(
        "aletheia.agents.divergence_scanner.get_market_data_provider",
        return_value=mock_provider,
    ):
        result = await run_divergence_scanner(permission_mode="Paper")

    # Candidate rejected (ambiguous polarity) — provider still closed
    assert "skip:BTC/USDT:prediction_polarity_ambiguous" in result["outputs"]
    mock_provider.close.assert_awaited_once()
