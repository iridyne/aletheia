"""News LLM sentiment analyzer — batch-analyzes new items after RSS fetch.

Called as a tail step of the news_fetch job. Uses llm_structured to extract
sentiment, related symbols, relevance score, and one-sentence impact summary.
Updates news_items rows in place, sets status='analyzed'.
"""

import json
import logging
from datetime import datetime, timezone

from aletheia.agents.helpers import llm_structured
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.data.news_analyzer")

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


async def analyze_pending_news(batch_size: int = 10) -> int:
    """Analyze up to batch_size news items with status='new'."""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, summary FROM news_items WHERE status = 'new' "
        "ORDER BY published_at DESC LIMIT ?",
        (batch_size,),
    ).fetchall()

    if not rows:
        return 0

    analyzed = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        title = row["title"] or ""
        summary = row["summary"] or ""
        user_msg = f"Headline: {title}\nSummary: {summary[:300]}"

        result, err = await llm_structured(
            system=_ANALYSIS_SYSTEM,
            user=user_msg,
            schema=_AnalysisSchema,
            max_tokens=256,
        )

        if err or not result:
            logger.debug("news analysis failed for %s: %s", row["id"], err)
            db.execute(
                "UPDATE news_items SET status = 'analyzed', analyzed_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            analyzed += 1
            continue

        sentiment = result.get("sentiment", "neutral")
        symbols = result.get("symbols", [])
        relevance = result.get("relevance", 0.5)
        impact = result.get("impact", "")

        db.execute(
            """UPDATE news_items SET
                sentiment = ?, symbols_mentioned = ?, relevance_score = ?,
                impact_summary = ?, status = 'analyzed', analyzed_at = ?
               WHERE id = ?""",
            (sentiment, json.dumps(symbols), relevance, impact, now, row["id"]),
        )
        analyzed += 1

    if analyzed:
        db.commit()
        logger.info("news: analyzed %d items", analyzed)

    return analyzed
