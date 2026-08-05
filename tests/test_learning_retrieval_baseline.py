"""Learning A: fixed offline baseline for the current FTS retriever.

This freezes expected behavior of the existing high-confidence, active knowledge
gate. It does not change retrieval, prompts, or runtime authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.test_experience_loop import setup_env


@dataclass(frozen=True)
class _Scenario:
    query: str
    expected: str | None
    forbidden: str | None = None


def test_learning_a_fts_baseline(tmp_path, monkeypatch):
    """Current FTS baseline is relevant, excludes stale items, and stays quiet on misses."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import archive_item, create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons
    from aletheia.storage.sqlite import reset_db

    def add_item(title: str, content: str):
        item = create_research_note(
            title,
            content + " This evidence-backed note is retained for market-observer risk calibration.",
            confidence="high_confidence",
        )
        assert item is not None
        return item

    items = {
        "btc_funding": add_item(
            "BTC funding crowding",
            "BTC perpetual funding is extreme; crowded longs can unwind through liquidation.",
        ),
        "eth_breakout": add_item(
            "ETH breakout confirmation",
            "ETH breakout needs spot volume confirmation before a momentum continuation entry.",
        ),
        "sol_drawdown": add_item(
            "SOL drawdown control",
            "SOL momentum failure after a stop loss requires smaller size and no immediate re-entry.",
        ),
        "stale_btc": add_item(
            "BTC obsolete funding note",
            "BTC funding crowding liquidation old historical note that must not remain active.",
        ),
    }
    archive_item(items["stale_btc"].id)

    scenarios = (
        _Scenario("BTC funding crowded longs liquidation", "btc_funding", "stale_btc"),
        _Scenario("ETH breakout volume confirmation", "eth_breakout"),
        _Scenario("SOL stop loss momentum re-entry", "sol_drawdown"),
        _Scenario("XRP governance vote", None),
    )
    expected_count = hits = returned = relevant = stale_or_conflict = false_retrievals = 0
    for scenario in scenarios:
        result_ids = [lesson.item_id for lesson in retrieve_lessons(scenario.query, k=3)]
        returned += len(result_ids)
        if scenario.expected is None:
            false_retrievals += int(bool(result_ids))
            continue
        expected_count += 1
        expected_id = items[scenario.expected].id
        hits += int(expected_id in result_ids)
        relevant += int(expected_id in result_ids)
        if scenario.forbidden:
            stale_or_conflict += int(items[scenario.forbidden].id in result_ids)

    # Freeze the current retriever's baseline; this is observational, not a quality gate.
    assert hits / expected_count == 1.0  # Recall@3
    assert relevant / returned == 0.75  # Precision@3
    assert stale_or_conflict == 0
    assert false_retrievals == 0
    reset_db()
