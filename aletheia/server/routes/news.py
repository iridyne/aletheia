"""News Intelligence API routes."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aletheia.storage.sqlite import get_db
from aletheia.storage.migrations import run_migrations

router = APIRouter(prefix="/api", tags=["news"])
logger = logging.getLogger("aletheia.news")


def _ensure_db():
    # No-op: migrations run once at startup (see aletheia/server/routes/data.py:_ensure_db).
    pass


@router.get("/news")
async def list_news(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    symbol: str | None = Query(None),
):
    _ensure_db()
    db = get_db()
    query = "SELECT * FROM news_items"
    conditions = []
    params: list = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if symbol:
        conditions.append("symbols_mentioned LIKE ?")
        params.append(f"%{symbol}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        if item.get("symbols_mentioned"):
            try:
                item["symbols_mentioned"] = json.loads(item["symbols_mentioned"])
            except (json.JSONDecodeError, TypeError):
                item["symbols_mentioned"] = []
        results.append(item)
    return results


@router.get("/news/{news_id}")
async def get_news_item(news_id: str):
    _ensure_db()
    db = get_db()
    row = db.execute("SELECT * FROM news_items WHERE id = ?", (news_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="News item not found")
    item = dict(row)
    if item.get("symbols_mentioned"):
        try:
            item["symbols_mentioned"] = json.loads(item["symbols_mentioned"])
        except (json.JSONDecodeError, TypeError):
            item["symbols_mentioned"] = []
    return item


class AnalyzeRequest(BaseModel):
    pass


@router.post("/news/{news_id}/analyze")
async def analyze_news(news_id: str, req: AnalyzeRequest = AnalyzeRequest()):
    _ensure_db()
    db = get_db()
    row = db.execute("SELECT * FROM news_items WHERE id = ?", (news_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="News item not found")

    item = dict(row)
    title = item.get("title", "")
    summary = item.get("summary", "")

    try:
        from aletheia.agents.llm import llm_complete
        prompt = (
            f"Analyze this crypto news for trading impact:\n"
            f"Title: {title}\n"
            f"Summary: {summary}\n\n"
            f"Provide a brief (2-3 sentence) impact assessment for crypto markets. "
            f"Focus on: affected assets, direction of impact, and confidence level."
        )
        analysis = await llm_complete(prompt)
    except Exception as e:
        logger.warning("LLM analysis failed: %s", e)
        analysis = "Analysis unavailable — model provider not configured or unreachable."

    db.execute(
        "UPDATE news_items SET status = ? WHERE id = ?",
        ("reviewed", news_id),
    )
    db.commit()

    return {
        "news_id": news_id,
        "analysis": analysis,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


class CreateNewsRequest(BaseModel):
    title: str
    source: str = "manual"
    url: str | None = None
    summary: str | None = None
    symbols_mentioned: list[str] = []
    event_type: str = "other"
    sentiment: str = "neutral"
    relevance_score: float = 0.5


@router.post("/news")
async def create_news(req: CreateNewsRequest):
    """Manually create a news item (for testing or manual curation)."""
    import secrets
    _ensure_db()
    db = get_db()
    news_id = secrets.token_hex(6)
    now = datetime.now(timezone.utc).isoformat()

    db.execute(
        """INSERT INTO news_items (id, title, source, url, summary, symbols_mentioned,
           event_type, sentiment, relevance_score, published_at, fetched_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            news_id, req.title, req.source, req.url, req.summary,
            json.dumps(req.symbols_mentioned), req.event_type, req.sentiment,
            req.relevance_score, now, now, "new",
        ),
    )
    db.commit()
    return {"id": news_id, "status": "created"}
