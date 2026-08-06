"""News Intelligence API routes."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aletheia.storage.sqlite import get_db

router = APIRouter(prefix="/api", tags=["news"])
logger = logging.getLogger("aletheia.news")


def _ensure_db():
    # No-op: backend startup already verified the durable migration ledger.
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
    row = db.execute("SELECT id FROM news_items WHERE id = ?", (news_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="News item not found")

    from aletheia.data.news_analyzer import analyze_one_item
    result = await analyze_one_item(news_id)

    if result["success"]:
        item = result["item"]
        return {
            "news_id": news_id,
            "success": True,
            "status": item["status"],
            "sentiment": item.get("sentiment"),
            "relevance_score": item.get("relevance_score"),
            "impact_summary": item.get("impact_summary"),
            "symbols_mentioned": item.get("symbols_mentioned") or [],
            "analyzed_at": item.get("analyzed_at"),
        }
    return {
        "news_id": news_id,
        "success": False,
        "status": result["status"],
        "retry_eligible": result.get("retry_eligible", False),
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
