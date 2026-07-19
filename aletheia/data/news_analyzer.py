"""News LLM sentiment analyzer for the independent bounded scheduler job.

Uses llm_structured to extract
sentiment, related symbols, relevance score, and one-sentence impact summary.
Updates news_items rows in place, sets status='analyzed'.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from aletheia.agents.helpers import llm_structured
from aletheia.data.news_entities import match_crypto_entities
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.data.news_analyzer")

_ANALYSIS_ITEM_TIMEOUT_SEC = 30.0

_ANALYSIS_SYSTEM = (
    "You are a crypto market news analyst. Given a news headline and summary, "
    "extract structured trading-relevant metadata. Return ONLY a JSON object with:\n"
    '- "sentiment": one of "bullish", "bearish", "neutral", "mixed"\n'
    '- "symbols": array of affected crypto symbols in "BTC/USDT" format (empty if none)\n'
    '- "relevance": float 0-1 (how relevant to crypto/macro trading)\n'
    '- "impact": one sentence describing potential market impact\n'
    "Be concise. If the news is not relevant to crypto or financial markets, "
    'set relevance to 0.1 and impact to "Not directly relevant to crypto markets."'
)

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["bullish", "bearish", "neutral", "mixed"]},
        "symbols": {"type": "array", "items": {"type": "string"}},
        "relevance": {"type": "number", "minimum": 0, "maximum": 1},
        "impact": {"type": "string"},
    },
    "required": ["sentiment", "symbols", "relevance", "impact"],
}


class _AnalysisSchema:
    @staticmethod
    def model_json_schema():
        return _ANALYSIS_SCHEMA


async def analyze_pending_news(batch_size: int = 3) -> int:
    """Analyze a fair mix of oldest new items and retries due after 30 minutes."""
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    batch_size = max(1, min(int(batch_size), 3))
    db = get_db()
    rows = db.execute(
        "WITH eligible AS ("
        "  SELECT id, title, summary, content, status, published_at, analyzed_at, "
        "         ROW_NUMBER() OVER ("
        "           PARTITION BY status "
        "           ORDER BY CASE WHEN status='analysis_retry' "
        "                         THEN datetime(analyzed_at) "
        "                         ELSE datetime(published_at) END ASC, rowid ASC"
        "         ) AS lane_rank "
        "  FROM news_items "
        "  WHERE status='new' OR ("
        "    status='analysis_retry' "
        "    AND datetime(analyzed_at)<=datetime('now', '-30 minutes')"
        "  )"
        ") "
        "SELECT id, title, summary, content, status FROM eligible "
        "ORDER BY lane_rank ASC, "
        "         CASE status WHEN 'analysis_retry' THEN 0 ELSE 1 END ASC "
        "LIMIT ?",
        (batch_size,),
    ).fetchall()

    if not rows:
        return 0

    analyzed = 0

    def _record_failed_attempt(row, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        next_status = (
            "analysis_failed"
            if row["status"] == "analysis_retry"
            else "analysis_retry"
        )
        was_in_tx = db.in_transaction
        cursor = db.execute(
            "UPDATE news_items SET status=?, analyzed_at=? WHERE id=? AND status=?",
            (next_status, now, row["id"], row["status"]),
        )
        if not was_in_tx:
            db.commit()
        if cursor.rowcount:
            logger.debug(
                "news analysis attempt failed for %s (%s -> %s): %s",
                row["id"], row["status"], next_status, reason,
            )

    for row in rows:
        title = row["title"] or ""
        summary = row["summary"] or ""
        user_msg = f"Headline: {title}\nSummary: {summary[:300]}"

        try:
            result, err = await asyncio.wait_for(
                llm_structured(
                    system=_ANALYSIS_SYSTEM,
                    user=user_msg,
                    schema=_AnalysisSchema,
                    max_tokens=256,
                ),
                timeout=_ANALYSIS_ITEM_TIMEOUT_SEC,
            )
        except TimeoutError:
            result, err = None, "item_timeout"
        except Exception as exc:
            result, err = None, f"provider_error:{type(exc).__name__}"

        if err or not result:
            _record_failed_attempt(row, str(err or "empty_result"))
            continue
        if not isinstance(result, dict):
            _record_failed_attempt(row, "schema_non_object")
            continue

        sentiment = str(result.get("sentiment") or "neutral").lower()
        if sentiment not in {"bullish", "bearish", "neutral", "mixed"}:
            _record_failed_attempt(row, "schema_invalid_sentiment")
            continue
        try:
            relevance = float(result["relevance"])
        except (KeyError, TypeError, ValueError):
            _record_failed_attempt(row, "schema_invalid_relevance")
            continue
        if not 0 <= relevance <= 1:
            _record_failed_attempt(row, "schema_relevance_out_of_range")
            continue
        if not isinstance(result.get("symbols"), list) or not isinstance(
            result.get("impact"), str
        ):
            _record_failed_attempt(row, "schema_missing_required_fields")
            continue
        impact = str(result.get("impact") or "").strip()
        symbols = match_crypto_entities(title, summary, row["content"] or "")
        now = datetime.now(timezone.utc).isoformat()

        was_in_tx = db.in_transaction
        cursor = db.execute(
            """UPDATE news_items SET
                sentiment = ?, symbols_mentioned = ?, relevance_score = ?,
                impact_summary = ?, status = 'analyzed', analyzed_at = ?
               WHERE id = ? AND status = ?""",
            (
                sentiment, json.dumps(symbols), relevance, impact[:500], now,
                row["id"], row["status"],
            ),
        )
        if not was_in_tx:
            db.commit()
        if cursor.rowcount:
            analyzed += 1

    if analyzed:
        logger.info("news: analyzed %d items", analyzed)

    return analyzed
