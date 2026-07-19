"""News RSS feed collector — periodic ingestion into news_items table.

Architecture mirrors OpenAlice's news collector (RSS → dedup → persist) adapted
to Aletheia's Python/SQLite stack. Scheduled by APScheduler every N minutes.
"""

import asyncio
import hashlib
import html
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from time import struct_time

import feedparser

from aletheia.config.loader import load_config
from aletheia.data.news_entities import match_crypto_entities
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.data.news_collector")


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from feed text fields."""
    stripped = _HTML_TAG_RE.sub(" ", text)
    decoded = html.unescape(stripped)
    return re.sub(r"\s{2,}", " ", decoded).strip()


def _compute_dedup_key(entry: dict) -> str:
    """Dedup priority: guid > link > sha256(title+summary)[:16]."""
    guid = entry.get("id") or entry.get("guid")
    if guid:
        return f"guid:{guid}"
    link = entry.get("link")
    if link:
        return f"link:{link}"
    text = (entry.get("title", "") + entry.get("summary", ""))
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"hash:{h}"


def _parse_time(entry: dict, *, now: datetime | None = None) -> str:
    """Extract a valid non-future publication time, or preserve it as unknown."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    for key in ("published_parsed", "updated_parsed"):
        tp: struct_time | None = entry.get(key)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc)
                if dt <= current:
                    return dt.isoformat()
            except (TypeError, ValueError):
                pass
    return ""


def _extract_symbols(title: str, summary: str) -> list[str]:
    """Extract only exact ticker or asset-name entities from article text."""
    return match_crypto_entities(title, summary)


def _fetch_feed_sync(url: str) -> list[dict]:
    """Parse a single RSS feed (blocking I/O)."""
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        logger.debug("feed parse error for %s: %s", url, feed.bozo_exception)
        return []
    return feed.entries


async def fetch_news_feeds() -> dict:
    """Fetch all enabled RSS feeds, dedup, write new items to DB.

    NOTE: LLM sentiment analysis was removed from this function's tail step.
    News analysis is too slow when the LLM provider has high latency/low reliability,
    and it routinely caused the 120s news_fetch job to time out (abandoned thread).
    The analyzer can still be called separately if needed, but it no longer blocks
    the RSS fetch pipeline.
    """
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    cfg = load_config()
    if not cfg.news.enabled:
        return {"fetched": 0, "new": 0}

    active_feeds = [f for f in cfg.news.feeds if f.enabled]
    if not active_feeds:
        return {"fetched": 0, "new": 0}

    db = get_db()
    loop = asyncio.get_event_loop()
    total_fetched = 0
    total_new = 0

    for feed_cfg in active_feeds:
        try:
            entries = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_feed_sync, feed_cfg.url),
                timeout=20,
            )
        except asyncio.TimeoutError:
            logger.warning("news fetch timed out for %s (20s)", feed_cfg.name)
            continue
        except Exception as e:
            logger.warning("news fetch failed for %s: %s", feed_cfg.name, e)
            continue

        total_fetched += len(entries)

        for entry in entries:
            dedup_key = _compute_dedup_key(entry)

            existing = db.execute(
                "SELECT 1 FROM news_items WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
            if existing:
                continue

            title = entry.get("title", "").strip()
            if not title:
                continue

            raw_summary = entry.get("summary", "") or entry.get("description", "") or ""
            raw_content = entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""
            summary = _strip_html(raw_summary) if raw_summary else ""
            content = _strip_html(raw_content) if raw_content else ""
            link = entry.get("link", "")
            symbols = _extract_symbols(title, summary)

            news_id = secrets.token_hex(6)
            fetched_at = datetime.now(timezone.utc)
            published_at = _parse_time(entry, now=fetched_at)
            now = fetched_at.isoformat()

            was_in_tx = db.in_transaction
            cursor = db.execute(
                """INSERT OR IGNORE INTO news_items
                   (id, title, source, url, summary, content, symbols_mentioned,
                    event_type, sentiment, relevance_score, published_at, fetched_at,
                    status, dedup_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    news_id, title, feed_cfg.source, link,
                    summary[:500] if summary else None,
                    content[:2000] if content else None,
                    json.dumps(symbols),
                    "rss", "neutral", 0.5,
                    published_at, now, "new", dedup_key,
                ),
            )
            if not was_in_tx:
                db.commit()
            if cursor.rowcount:
                total_new += 1

    if total_new > 0:
        logger.info("news: fetched %d items from %d feeds, %d new", total_fetched, len(active_feeds), total_new)
    else:
        logger.debug("news: fetched %d items from %d feeds, 0 new", total_fetched, len(active_feeds))

    return {"fetched": total_fetched, "new": total_new}


def prune_old_news(retention_days: int = 7) -> int:
    """Delete news items older than retention_days. Called from scheduler cleanup."""
    db = get_db()
    cursor = db.execute(
        "DELETE FROM news_items WHERE fetched_at < datetime('now', ?)",
        (f"-{retention_days} days",),
    )
    deleted = cursor.rowcount
    if deleted:
        db.commit()
        logger.debug("news: pruned %d items older than %d days", deleted, retention_days)
    return deleted
