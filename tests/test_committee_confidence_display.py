"""Regression tests for committee confidence display.

Production bug: logs showed "LLM Committee ... conf=0%/1%" while the parsed
confidence was 30-60%.  Root cause: the log format used "%.0f%%" on the raw
0-1 confidence value instead of the percentage (0.57 -> "1%", 0.30-0.49 -> "0%").
The value chain (evaluate_with_llm -> enrich_candidate_llm -> candidate dict)
was correct all along; only the display was wrong.

Related display bug: committee_score_display() read the non-existent
"confidence" key from report.to_dict() (actual key: "final_confidence"),
so it always rendered 0%.

These tests lock down:
  - log formatting shows real percentages (LLM and deterministic paths)
  - candidate["committee_confidence"] keeps the raw 0-1 value (unchanged)
  - committee_score_display reads the correct key
  - fail-soft semantics are preserved (empty data / committee error)
"""

from __future__ import annotations

import logging

import pytest

from aletheia.agents.committee import integration
from aletheia.agents.committee.base import DecisionReport


def _make_report(conf: float, direction: str = "long") -> DecisionReport:
    return DecisionReport(
        symbol="UNI/USDT",
        market_type="perp",
        timestamp="",
        final_direction=direction,  # type: ignore[arg-type]
        final_confidence=conf,
        position_size_factor=0.8,
    )


class _FakeCommittee:
    def __init__(self, report: DecisionReport):
        self._report = report

    async def evaluate(self, **kwargs) -> DecisionReport:
        return self._report

    async def evaluate_with_llm(self, **kwargs) -> DecisionReport:
        return self._report


class _FakeCursor:
    def fetchone(self):
        return None


class _FakeDB:
    def execute(self, *args, **kwargs):
        return _FakeCursor()


@pytest.fixture
def fake_storage(monkeypatch):
    """No real DB: one opportunity carrying t0 indicators, no snapshot rows."""
    from aletheia.storage import repositories
    from aletheia.storage import sqlite as sqlite_mod

    monkeypatch.setattr(
        repositories,
        "get_opportunity",
        lambda oid: {
            "metadata": '{"evidence_manifest": {"t0": {"atr_pct": 0.021, "rsi": 58.0}}}'
        },
    )
    monkeypatch.setattr(sqlite_mod, "get_db", lambda: _FakeDB())


# --- Log formatting: percentage must be confidence * 100 ---


async def test_llm_committee_log_shows_percentage(monkeypatch, fake_storage, caplog):
    report = _make_report(0.57)
    monkeypatch.setattr(integration, "get_committee", lambda: _FakeCommittee(report))

    candidate = {
        "symbol": "UNI/USDT",
        "market": "perp",
        "opportunity_id": "opp-1",
        "trade_score": 50.0,
    }
    with caplog.at_level(logging.DEBUG, logger="aletheia.committee.integration"):
        result = await integration.enrich_candidate_llm(candidate)

    messages = [r.getMessage() for r in caplog.records]
    main = [m for m in messages if m.startswith("LLM Committee UNI/USDT:")]
    assert main, f"missing 'LLM Committee' log, got: {messages}"
    assert "conf=57%" in main[0]
    assert "conf=1%" not in main[0]

    # The candidate field keeps the raw 0-1 value; display fixes never touch it.
    assert result["committee_confidence"] == pytest.approx(0.57)
    assert result["committee_direction"] == "long"
    assert result["committee_llm_used"] is True


async def test_deterministic_committee_debug_log_shows_percentage(monkeypatch, caplog):
    report = _make_report(0.62)
    monkeypatch.setattr(integration, "get_committee", lambda: _FakeCommittee(report))

    candidate = {"symbol": "UNI/USDT", "market": "perp", "trade_score": 40.0}
    with caplog.at_level(logging.DEBUG, logger="aletheia.committee.integration"):
        await integration.enrich_candidate(candidate, features={"rsi": 55.0})

    messages = [r.getMessage() for r in caplog.records]
    assert any("conf=62%" in m for m in messages), f"got: {messages}"
    assert not any("conf=1%" in m for m in messages)


# --- committee_score_display key fix ---


def test_committee_score_display_uses_final_confidence():
    report = _make_report(0.57)
    candidate = {"committee_report": report.to_dict(), "committee_llm_used": True}
    display = integration.committee_score_display(candidate)
    assert "long(57%)" in display
    assert "0%" not in display


def test_committee_score_display_without_report():
    assert integration.committee_score_display({}) == "committee:n/a"


# --- fail-soft semantics preserved ---


async def test_llm_enrich_skips_when_no_data(monkeypatch):
    from aletheia.storage import repositories
    from aletheia.storage import sqlite as sqlite_mod

    monkeypatch.setattr(repositories, "get_opportunity", lambda oid: None)
    monkeypatch.setattr(sqlite_mod, "get_db", lambda: _FakeDB())

    candidate = {"symbol": "X/USDT", "market": "perp", "opportunity_id": "opp-9"}
    result = await integration.enrich_candidate_llm(candidate)
    assert "committee_confidence" not in result


async def test_llm_enrich_fail_soft_on_committee_error(monkeypatch, fake_storage):
    class _BoomCommittee(_FakeCommittee):
        async def evaluate_with_llm(self, **kwargs) -> DecisionReport:
            raise RuntimeError("llm unavailable")

    monkeypatch.setattr(
        integration, "get_committee", lambda: _BoomCommittee(_make_report(0.5))
    )

    candidate = {
        "symbol": "UNI/USDT",
        "market": "perp",
        "opportunity_id": "opp-1",
        "trade_score": 50.0,
    }
    result = await integration.enrich_candidate_llm(candidate)
    assert "committee_confidence" not in result
    # original score untouched on failure
    assert result["trade_score"] == pytest.approx(50.0)


# --- latest-snapshot fallback for opportunities without feature_snapshot_id ---

_SNAPSHOT_ROW = (
    0.95,   # atr_pct
    42.0,   # rsi_14
    0.01,   # return_1h
    -0.02,  # return_4h
    0.05,   # return_24h
    None,   # funding_rate
    123.0,  # open_interest
    None,   # oi_delta_1h
    None,   # taker_buy_ratio
    1.2,    # vol_ratio
    None,   # relative_strength_24h
    None,   # oi_price_divergence
    None,   # rsi_4h
    None,   # bb_squeeze
)


class _FallbackFakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FallbackFakeDB:
    """Returns a snapshot row only for the universe_members-based fallback query."""

    def execute(self, sql, params=()):
        if "universe_members" in sql:
            return _FallbackFakeCursor(_SNAPSHOT_ROW)
        return _FallbackFakeCursor(None)


class _RecordingCommittee(_FakeCommittee):
    def __init__(self, report):
        super().__init__(report)
        self.calls: list[dict] = []

    async def evaluate_with_llm(self, **kwargs) -> DecisionReport:
        self.calls.append(kwargs)
        return self._report


async def test_snapshot_fallback_supplies_indicators_when_fid_missing(monkeypatch):
    """Production regression: ~half the opportunities carry no
    feature_snapshot_id, leaving the LLM with only ATR+macro so it refused
    to call a direction.  The fallback must fetch the latest fresh snapshot
    for the symbol via universe_members."""
    from aletheia.storage import repositories
    from aletheia.storage import sqlite as sqlite_mod

    # t0 manifest WITHOUT feature_snapshot_id and without direct indicators
    monkeypatch.setattr(
        repositories,
        "get_opportunity",
        lambda oid: {
            "metadata": '{"evidence_manifest": {"t0": {"direction": "long"}}}'
        },
    )
    monkeypatch.setattr(sqlite_mod, "get_db", lambda: _FallbackFakeDB())

    committee = _RecordingCommittee(_make_report(0.5))
    monkeypatch.setattr(integration, "get_committee", lambda: committee)

    candidate = {
        "symbol": "AVAX/USDT",
        "market": "perp",
        "opportunity_id": "opp-2",
        "trade_score": 50.0,
    }
    result = await integration.enrich_candidate_llm(candidate)

    assert committee.calls, "evaluate_with_llm was not called"
    features = committee.calls[0]["features"]
    # fallback row indicators must reach the LLM
    assert features["rsi_14"] == pytest.approx(42.0)
    assert features["atr_pct"] == pytest.approx(0.95)
    assert features["vol_ratio"] == pytest.approx(1.2)
    assert result["committee_confidence"] == pytest.approx(0.5)


async def test_snapshot_fallback_not_used_when_data_sufficient(
    monkeypatch, fake_storage
):
    """When the exact feature_snapshot_id resolves to a full row (>=5 keys),
    the fallback query must not run (no redundant DB churn)."""
    from aletheia.storage import sqlite as sqlite_mod

    executed: list[str] = []

    class _SpyDB:
        def execute(self, sql, params=()):
            executed.append(sql)
            if "universe_members" in sql:
                return _FallbackFakeCursor(_SNAPSHOT_ROW)
            if "WHERE id = ?" in sql:
                return _FallbackFakeCursor(_SNAPSHOT_ROW)
            return _FallbackFakeCursor(None)

    # opportunity WITH feature_snapshot_id
    from aletheia.storage import repositories

    monkeypatch.setattr(
        repositories,
        "get_opportunity",
        lambda oid: {
            "metadata": '{"evidence_manifest": {"t0": {"feature_snapshot_id": "snap-1"}}}'
        },
    )
    monkeypatch.setattr(sqlite_mod, "get_db", lambda: _SpyDB())

    committee = _RecordingCommittee(_make_report(0.5))
    monkeypatch.setattr(integration, "get_committee", lambda: committee)

    candidate = {"symbol": "UNI/USDT", "market": "perp", "opportunity_id": "opp-3"}
    await integration.enrich_candidate_llm(candidate)

    assert committee.calls
    assert not any("universe_members" in s for s in executed), (
        "fallback query must not run when the exact snapshot row is complete"
    )


# --- Evidence key compatibility (explainability-score regression) ---


async def _run_committee(monkeypatch, llm_result: dict):
    import aletheia.agents.committee.llm_agent as llm_agent_mod
    from aletheia.agents.committee.committee import InvestmentCommittee

    async def _fake_analyze(symbol, market_type, data, provider=None):
        return dict(llm_result)

    monkeypatch.setattr(llm_agent_mod, "analyze_with_llm", _fake_analyze)
    committee = InvestmentCommittee()
    return await committee.evaluate_with_llm(
        "UNI/USDT", "perp", features={"rsi": 50.0}
    )


async def test_llm_evidence_key_populates_opinion_evidence(monkeypatch):
    """Production regression: LLMs return 'evidence' (248/248 observed), but the
    code only read 'key_evidence', so evidence stayed empty and the
    explainability score was artificially depressed."""
    report = await _run_committee(monkeypatch, {
        "direction": "long",
        "confidence": 0.6,
        "reasoning": "RSI recovery",
        "evidence": ["RSI 48 turning up", "volume expansion", "funding neutral"],
    })
    opinion = report.agent_opinions[0]
    assert len(opinion.evidence) == 3
    # 3 evidence items => evidence component of ES saturates (min(3/3,1)=1.0)
    assert report.explainability_score > 0.5


async def test_llm_key_evidence_key_still_supported(monkeypatch):
    report = await _run_committee(monkeypatch, {
        "direction": "short",
        "confidence": 0.5,
        "reasoning": "weak structure",
        "key_evidence": ["breakdown below support"],
    })
    assert len(report.agent_opinions[0].evidence) == 1


async def test_llm_missing_evidence_keys_never_crashes(monkeypatch):
    report = await _run_committee(monkeypatch, {
        "direction": "neutral",
        "confidence": 0.4,
        "reasoning": "mixed signals",
    })
    assert report.agent_opinions[0].evidence == []


async def test_llm_malformed_evidence_entries_are_coerced(monkeypatch):
    report = await _run_committee(monkeypatch, {
        "direction": "long",
        "confidence": 0.55,
        "reasoning": "ok",
        "evidence": "single string, not a list",
    })
    ev = report.agent_opinions[0].evidence
    assert len(ev) == 1
    assert ev[0].value == "single string, not a list"


async def test_llm_risk_warnings_captured_for_persistence(monkeypatch):
    """Decision-output contract: 风险提示 must be carried on the report so the
    persisted committee record includes them (production key is 'risks')."""
    report = await _run_committee(monkeypatch, {
        "direction": "long",
        "confidence": 0.6,
        "reasoning": "ok",
        "risks": ["funding spike risk", "low liquidity"],
    })
    assert report.risk_warnings == ["funding spike risk", "low liquidity"]

    # missing risks key -> empty list, never a crash
    report2 = await _run_committee(monkeypatch, {
        "direction": "neutral", "confidence": 0.4, "reasoning": "ok",
    })
    assert report2.risk_warnings == []
