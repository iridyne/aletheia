"""Market-regime authority must expire without relabelling old evidence."""

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.regime import detector
from aletheia.storage import repositories as repo


def _insert_regime(detected_at: str) -> None:
    repo.get_db().execute(
        "INSERT INTO market_regimes (id, state, confidence, long_bias, short_bias, signals, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("freshness-test", "bull", 0.9, 0.75, 0.25, "{}", detected_at),
    )
    repo.get_db().commit()


@pytest.mark.parametrize(
    ("offset", "state"),
    [
        (-timedelta(hours=2), "stale"),
        (timedelta(minutes=10), "future"),
        (None, "invalid"),
    ],
)
def test_latest_market_regime_keeps_unavailable_provenance(offset, state):
    detected_at = (
        (datetime.now(timezone.utc) + offset).isoformat()
        if offset is not None
        else "not-a-timestamp"
    )
    _insert_regime(detected_at)

    row = repo.get_latest_market_regime()

    assert row is not None
    assert row["freshness"] == state
    assert row["state"] is None
    assert row["confidence"] is None
    assert row["detected_at"] == detected_at


def test_detector_recovery_preserves_source_timestamp(monkeypatch):
    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    detector.reset_state()
    monkeypatch.setattr(
        repo,
        "get_latest_market_regime",
        lambda: {
            "state": "bear",
            "confidence": 0.9,
            "detected_at": detected_at,
        },
    )

    recovered = detector.get_current_regime()

    assert recovered.state == "bear"
    assert recovered.detected_at == detected_at


def test_cached_detector_regime_expires(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    detector.reset_state()
    detector._published_regime = detector.MarketRegime(
        state="bull", confidence=0.9, detected_at=old,
    )
    monkeypatch.setattr(repo, "get_latest_market_regime", lambda: None)

    current = detector.get_current_regime()

    assert current.state == "sideways"
    assert current.confidence == 0.5


def test_entry_quality_does_not_use_stale_cached_regime(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    detector.reset_state()
    detector._published_regime = detector.MarketRegime(
        state="bull", confidence=0.9, detected_at=old,
    )
    monkeypatch.setattr(repo, "get_latest_market_regime", lambda: {
        "state": None,
        "freshness": "stale",
    })

    from aletheia.risk.demo_entry_quality import load_published_regime

    assert load_published_regime() == (None, None)


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("aletheia.trading.entry_snapshot", "_current_regime"),
        ("aletheia.execution.position_enrichment", "current_regime"),
    ],
)
def test_stale_regime_never_falls_back_to_macro_as_current(monkeypatch, module_name, function_name):
    module = __import__(module_name, fromlist=[function_name])
    monkeypatch.setattr(repo, "get_latest_market_regime", lambda: {
        "state": None,
        "freshness": "stale",
    })
    monkeypatch.setattr(repo, "latest_macro_snapshot", lambda: {"risk_regime": "risk_on"})

    assert getattr(module, function_name)() == "unknown"
