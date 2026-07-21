"""Authoritative post-trade review coverage over eligible Position identities."""

from __future__ import annotations

import json


def _query_active_reviews(extra_where: str = "", params: tuple = ()) -> list[dict]:
    from aletheia.storage.sqlite import get_db

    where = "item_type = 'post_trade_review' AND (status = 'active' OR status IS NULL)"
    if extra_where:
        where += f" AND {extra_where}"
    rows = get_db().execute(
        f"SELECT * FROM knowledge_items WHERE {where} "
        "ORDER BY datetime(created_at) DESC, rowid DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def list_active_post_trade_reviews(*, created_after: str | None = None) -> list[dict]:
    """Return active reviews, optionally bounded to one recent creation window."""
    if created_after is None:
        return _query_active_reviews()
    return _query_active_reviews("created_at > ?", (created_after,))


def list_active_post_trade_reviews_for_strategy(strategy_id: str | None) -> list[dict]:
    """Return active reviews for one exact strategy identity (including global None)."""
    return _query_active_reviews("strategy_id IS ?", (strategy_id,))


def _position_ids_by_strategy(reviews: list[dict]) -> dict[str | None, set[str]]:
    covered: dict[str | None, set[str]] = {}
    for review in reviews:
        if (review.get("status") or "active") != "active":
            continue
        raw_strategy_id = review.get("strategy_id")
        strategy_id = None if raw_strategy_id is None else str(raw_strategy_id)
        try:
            tags = json.loads(review.get("tags") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(tags, list):
            continue
        position_ids = covered.setdefault(strategy_id, set())
        for tag in tags:
            if not isinstance(tag, str) or not tag.startswith("position:"):
                continue
            position_id = tag.removeprefix("position:").strip()
            if position_id:
                position_ids.add(position_id)
    return covered


def reviewed_position_ids_for_strategy(
    reviews: list[dict], strategy_id: str | None
) -> set[str]:
    """Return exact Position identities covered by active reviews for a strategy."""
    key = None if strategy_id is None else str(strategy_id)
    return _position_ids_by_strategy(reviews).get(key, set())


def missing_review_strategy_ids(
    positions: list[dict], reviews: list[dict]
) -> list[str]:
    """Return strategies with at least one eligible Position lacking exact coverage."""
    covered = _position_ids_by_strategy(reviews)
    missing: set[str] = set()
    for position in positions:
        raw_strategy_id = position.get("strategy_id")
        if raw_strategy_id is None:
            continue
        strategy_id = str(raw_strategy_id)
        position_id = str(position.get("id") or "")
        if not position_id or position_id not in covered.get(strategy_id, set()):
            missing.add(strategy_id)
    return sorted(missing)


def build_post_trade_review_coverage(
    *, closed_after: str, position_limit: int = 20, review_limit: int = 20
) -> dict:
    """Build the shared API/operator coverage view from reviewer-eligible outcomes."""
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

    positions = list(reversed(eligible_strategy_outcomes(closed_after=closed_after)))[
        :position_limit
    ]
    reviews = list_active_post_trade_reviews(created_after=closed_after)
    return {
        "recent_closed_positions": positions,
        "recent_post_trade_reviews": reviews[:review_limit],
        "missing_review_strategy_ids": missing_review_strategy_ids(positions, reviews),
    }
