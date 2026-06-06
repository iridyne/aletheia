"""News Intelligence API — structured news feed for the dashboard."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.files.jsonl import read_jsonl, append_jsonl, write_audit

router = APIRouter(prefix="/api", tags=["news"])

NEWS_FILE = "news.jsonl"


class NewsItem(BaseModel):
    headline: str
    source: str = ""
    url: str = ""
    related_assets: list[str] = []
    event_type: str = "other"
    sentiment: str = "unknown"
    urgency: str = "low"
    market_impact: str = "unknown"
    summary: str = ""
    key_points: list[str] = []


@router.get("/news")
async def get_news(limit: int = 50, status: str | None = None):
    items = read_jsonl(NEWS_FILE)
    if status:
        items = [i for i in items if i.get("status") == status]
    items.sort(key=lambda x: x.get("ingested_at", ""), reverse=True)
    return items[:limit]


@router.get("/news/{news_id}")
async def get_news_item(news_id: str):
    items = read_jsonl(NEWS_FILE)
    for item in items:
        if item.get("id") == news_id:
            return item
    raise HTTPException(404, "News item not found")


@router.post("/news")
async def create_news_item(item: NewsItem):
    import uuid
    from datetime import datetime, timezone
    record = item.model_dump()
    record["id"] = str(uuid.uuid4())[:8]
    record["ingested_at"] = datetime.now(timezone.utc).isoformat()
    record["status"] = "new"
    record["linked_opportunity_ids"] = []
    record["linked_research_note_ids"] = []
    record["linked_strategy_ids"] = []
    append_jsonl(NEWS_FILE, record)
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "news_ingested",
        "entity_type": "news", "entity_id": record["id"],
        "message": f"News ingested: {item.headline[:80]}",
    })
    return record


@router.post("/news/{news_id}/archive")
async def archive_news(news_id: str):
    items = read_jsonl(NEWS_FILE)
    found = False
    for item in items:
        if item.get("id") == news_id:
            item["status"] = "archived"
            found = True
            break
    if not found:
        raise HTTPException(404, "News item not found")
    _rewrite_jsonl(NEWS_FILE, items)
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "news_archived",
        "entity_type": "news", "entity_id": news_id,
        "message": "News item archived",
    })
    return {"ok": True, "status": "archived"}


@router.post("/news/{news_id}/create-research-note")
async def create_research_note_from_news(news_id: str):
    items = read_jsonl(NEWS_FILE)
    news = None
    for item in items:
        if item.get("id") == news_id:
            news = item
            break
    if not news:
        raise HTTPException(404, "News item not found")

    import uuid
    from datetime import datetime, timezone
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo
    run_migrations()

    note_id = str(uuid.uuid4())[:8]
    note = {
        "id": note_id,
        "type": "research_note",
        "title": f"Research: {news.get('headline', '')}",
        "content": news.get("summary", "") + "\n\nKey points:\n" + "\n".join(f"- {p}" for p in news.get("key_points", [])),
        "tags": news.get("related_assets", []),
        "source": f"news:{news_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.insert_knowledge_item(note)

    news["status"] = "linked"
    if news_id not in news.get("linked_research_note_ids", []):
        news.setdefault("linked_research_note_ids", []).append(note_id)
    _rewrite_jsonl(NEWS_FILE, items)

    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "research_note_created",
        "entity_type": "knowledge", "entity_id": note_id,
        "message": f"Research note created from news: {news.get('headline', '')[:60]}",
    })
    return {"ok": True, "note_id": note_id}


def _rewrite_jsonl(filename: str, items: list[dict]):
    """Rewrite a JSONL file with updated items."""
    import json
    from aletheia.config.loader import get_data_dir
    path = get_data_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, default=str) + "\n")
