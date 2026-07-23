"""Knowledge Base manager — handles lifecycle of KB items (no hard delete)."""

import hashlib
import logging

from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import (
    insert_knowledge_item, update_knowledge_item, list_knowledge_items,
    knowledge_hash_exists, knowledge_title_exists_within,
)

logger = logging.getLogger("aletheia.knowledge.manager")

_MIN_CONTENT_LENGTH = 80
_PLACEHOLDER_PATTERNS = [
    "Content here",
    "(LLM unavailable",
    "Synthesized 3 research notes",
    "LLM unavailable for synthesis",
]


def _content_hash(content: str) -> str:
    """SHA-256 prefix (16 hex chars) of stripped content."""
    return hashlib.sha256(content.strip().encode()).hexdigest()[:16]


def _is_quality_pass(content: str) -> bool:
    """Reject placeholder or near-empty content."""
    if len(content.strip()) < _MIN_CONTENT_LENGTH:
        return False
    return not any(p in content for p in _PLACEHOLDER_PATTERNS)
from aletheia.files.markdown import (
    write_daily_review,
    write_dreaming_log,
    write_guard_candidate,
    write_post_trade_review,
    write_research_note,
    write_strategy_doc,
    write_trade_case_study,
    write_trading_guard,
)

VALID_CONFIDENCES = {"high_confidence", "medium_confidence", "speculative", "deprecated"}
VALID_STATUSES = {"active", "archived", "deprecated"}


def _index(item, content: str) -> None:
    """Best-effort full-text index of a knowledge item (experience loop, §6).

    Only high_confidence items become retrievable (the quality gate); others are
    written/audited as before but kept out of decision retrieval. Never raises.
    """
    try:
        if item.confidence != "high_confidence":
            return
        from aletheia.knowledge.retrieval import index_knowledge_item
        # Extract outcome from frontmatter for structured ranker scoring (Phase 1a).
        outcome = ""
        try:
            from aletheia.knowledge.frontmatter import parse_frontmatter_text
            meta, _ = parse_frontmatter_text(content)
            outcome = (meta.get("outcome") or "") if meta else ""
            if not outcome and getattr(item, "file_path", None):
                from pathlib import Path
                path = Path(item.file_path)
                if path.exists():
                    file_meta, _ = parse_frontmatter_text(
                        path.read_text(encoding="utf-8", errors="replace")
                    )
                    outcome = (file_meta.get("outcome") or "") if file_meta else ""
        except Exception:
            pass
        index_knowledge_item(item, content, outcome=outcome)
    except Exception as e:
        logger.warning("_index failed for %s: %s", getattr(item, "id", "?"), e)


def create_research_note(title: str, content: str, confidence: str = "medium_confidence", metadata: dict | None = None) -> KnowledgeItem | None:
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=research_note len=%d", len(content))
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("research_note", ch):
        logger.info("knowledge_skipped:duplicate item_type=research_note hash=%s", ch)
        return None
    path = write_research_note(title, content, metadata)
    item = KnowledgeItem(
        item_type="research_note",
        title=title,
        file_path=str(path),
        # Dual-write body to DB so a lost/moved Markdown file cannot break the
        # experience-loop index. Disk file remains a readable artifact.
        content=content,
        confidence=confidence,
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_dreaming_log(title: str, content: str) -> KnowledgeItem | None:
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=dreaming_log len=%d", len(content))
        return None
    if knowledge_title_exists_within("dreaming_log", title, hours=6):
        logger.info("knowledge_skipped:title_window item_type=dreaming_log title=%s", title)
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("dreaming_log", ch):
        logger.info("knowledge_skipped:duplicate item_type=dreaming_log hash=%s", ch)
        return None
    path = write_dreaming_log(title, content)
    item = KnowledgeItem(
        item_type="dreaming_log",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience (see create_research_note)
        confidence="high_confidence",
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_daily_review(title: str, content: str) -> KnowledgeItem | None:
    if not _is_quality_pass(content):
        logger.warning("knowledge_skipped:quality_gate item_type=daily_review len=%d", len(content.strip()))
        return None
    path = write_daily_review(content, title=title)
    item = KnowledgeItem(
        item_type="daily_review",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience
        confidence="high_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_post_trade_review(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    confidence: str = "medium_confidence",
    tags: list[str] | None = None,
) -> KnowledgeItem | None:
    import json as _json
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=post_trade_review len=%d", len(content))
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("post_trade_review", ch):
        logger.info("knowledge_skipped:duplicate item_type=post_trade_review hash=%s", ch)
        return None
    path = write_post_trade_review(title, content, metadata)
    item = KnowledgeItem(
        item_type="post_trade_review",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience
        strategy_id=strategy_id,
        confidence=confidence,
        content_hash=ch,
        tags=_json.dumps(tags or [], ensure_ascii=False),
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_strategy_memory(strategy_id: str, title: str, content: str) -> KnowledgeItem:
    path = write_strategy_doc(strategy_id, title, content)
    item = KnowledgeItem(
        item_type="strategy_memory",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience
        confidence="high_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_trade_case_study(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    confidence: str = "high_confidence",
    tags: list[str] | None = None,
) -> KnowledgeItem | None:
    """Create a structured trade case study from a closed position."""
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        # WARNING (not INFO): quality gate failures indicate a content-generation
        # problem (too short, placeholder) — distinguishable from normal dedup.
        logger.warning("knowledge_skipped:quality_gate item_type=trade_case_study len=%d title=%r", len(content), title)
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("trade_case_study", ch):
        logger.info("knowledge_skipped:duplicate item_type=trade_case_study hash=%s", ch)
        return None
    path = write_trade_case_study(title, content, metadata)
    item = KnowledgeItem(
        item_type="trade_case_study",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience
        strategy_id=strategy_id,
        confidence=confidence,
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_trading_guard(
    title: str,
    content: str,
    metadata: dict | None = None,
    confidence: str = "high_confidence",
    tags: list[str] | None = None,
) -> KnowledgeItem:
    """Create an active trading guard (seed or human-confirmed)."""
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    path = write_trading_guard(title, content, metadata)
    item = KnowledgeItem(
        item_type="trading_guard",
        title=title,
        file_path=str(path),
        content=content,  # dual-write for FTS resilience
        confidence=confidence,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def _force_shadow_only_if_auto(metadata: dict | None) -> dict | None:
    """Every auto-generated guard candidate is created shadow-only (P0 2026-07-12).

    A learned/auto guard may observe but must never block or penalize until an
    experiment grants authority. Stamping shadow-only at the single creation
    chokepoint means no producer (trade_case_builder, outcome_recorder, guard_routing)
    can accidentally mint an auto candidate that the promotion job could turn into a
    hard block. Owner-authored (manual) candidates are left untouched.

    Also stamps created_at and expires_at so auto candidates have a finite lifetime
    and are excluded at load time once expired (#195).
    """
    if not metadata:
        return metadata
    auto = bool(metadata.get("auto_generated")) or str(metadata.get("guard_id") or "").startswith("auto_")
    if not auto:
        return metadata
    meta = dict(metadata)
    if meta.get("proposed_block_candidate") is None:
        meta["proposed_block_candidate"] = bool(
            meta.get("block_candidate") or meta.get("pre_shadow_block_candidate")
        )
    if meta.get("proposed_score_penalty") is None:
        meta["proposed_score_penalty"] = float(
            meta.get("score_penalty") or meta.get("pre_shadow_score_penalty") or 0.0
        )
    meta["shadow_only"] = True
    meta["block_candidate"] = False
    meta["score_penalty"] = 0
    tags = list(meta.get("tags") or [])
    if "shadow-only" not in tags:
        tags.append("shadow-only")
    meta["tags"] = tags
    # Finite TTL: auto-generated candidates expire after LEARNED_GUARD_TTL_DAYS (#195).
    if not meta.get("created_at") or not meta.get("expires_at"):
        from datetime import datetime, timedelta, timezone
        from aletheia.knowledge.guards import LEARNED_GUARD_TTL_DAYS
        now = datetime.now(timezone.utc)
        if not meta.get("created_at"):
            meta["created_at"] = now.isoformat()
        if not meta.get("expires_at"):
            meta["expires_at"] = (now + timedelta(days=LEARNED_GUARD_TTL_DAYS)).isoformat()
    return meta


def create_guard_candidate(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    tags: list[str] | None = None,
) -> KnowledgeItem:
    """Create a guard candidate (medium_confidence, not retrievable).

    Auto-generated candidates are forced shadow-only here — the single creation
    chokepoint for all guard producers.
    """
    metadata = _force_shadow_only_if_auto(metadata)
    path = write_guard_candidate(title, content, metadata)
    item = KnowledgeItem(
        item_type="guard_candidate",
        title=title,
        file_path=str(path),
        strategy_id=strategy_id,
        confidence="medium_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    # guard_candidate is medium_confidence → _index skips it (quality gate)
    _index(item, content)
    return item


def _unindex(item_id: str) -> None:
    """Best-effort removal from the FTS index (archived/deprecated → not recalled)."""
    try:
        from aletheia.knowledge.retrieval import remove_from_index
        remove_from_index(item_id)
    except Exception:
        pass


def archive_item(item_id: str) -> None:
    """Archive a KB item — soft removal, not deletion."""
    update_knowledge_item(item_id, status="archived")
    _unindex(item_id)


def deprecate_item(item_id: str) -> None:
    """Mark a KB item as deprecated — it stays but loses active relevance."""
    update_knowledge_item(item_id, status="deprecated", confidence="deprecated")
    _unindex(item_id)


def delete_item(item_id: str) -> None:
    """Hard delete is NOT allowed. Raises an error."""
    raise PermissionError("Hard deletion of knowledge items is not allowed. Use archive_item() or deprecate_item() instead.")


def detect_contradictions(items: list[dict]) -> list[dict]:
    """Find explicit high-level knowledge conflicts that require human review.

    Conservative by design: only reports when two active items share a symbol and
    direction, and one explicitly supports the direction while the other explicitly
    blocks/avoids it. It never mutates knowledge and never decides which side wins.
    """
    signals = [_extract_contradiction_signal(item) for item in items]
    signals = [s for s in signals if s is not None]

    by_key: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for sig in signals:
        key = (sig["symbol"], sig["direction"])
        bucket = by_key.setdefault(key, {"support": [], "block": []})
        bucket[sig["stance"]].append(sig)

    contradictions: list[dict] = []
    for (symbol, direction), grouped in sorted(by_key.items()):
        if not grouped["support"] or not grouped["block"]:
            continue
        contradictions.append({
            "kind": "support_vs_block",
            "symbol": symbol,
            "direction": direction,
            "severity": "review",
            "summary": f"{symbol} {direction}: supportive knowledge conflicts with blocking knowledge",
            "supporting_items": grouped["support"][:5],
            "blocking_items": grouped["block"][:5],
        })
    return contradictions


def _extract_contradiction_signal(item: dict) -> dict | None:
    status = str(item.get("status") or "active").lower()
    if status not in ("active", ""):
        return None

    meta, body = _item_meta_and_body(item)
    text = " ".join(
        str(part or "")
        for part in (
            item.get("title"),
            item.get("summary"),
            item.get("content"),
            item.get("tags"),
            body[:5000],
        )
    ).lower()

    symbol = _normalise_symbol(meta.get("symbol") or item.get("symbol") or _find_symbol(text))
    direction = _normalise_direction(meta.get("direction") or meta.get("direction_bias") or item.get("direction") or _find_direction(text))
    if not symbol or not direction:
        return None

    stance = _detect_stance(item, meta, text)
    if stance is None:
        return None

    return {
        "id": str(item.get("id") or item.get("guard_id") or ""),
        "item_type": str(item.get("item_type") or "unknown"),
        "title": str(item.get("title") or ""),
        "symbol": symbol,
        "direction": direction,
        "stance": stance,
        "confidence": str(item.get("confidence") or ""),
    }


def _item_meta_and_body(item: dict) -> tuple[dict, str]:
    from pathlib import Path
    import json

    from aletheia.knowledge.frontmatter import parse_frontmatter, parse_frontmatter_text

    meta: dict = {}
    body = ""
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        meta.update(metadata)
    elif isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
            if isinstance(parsed, dict):
                meta.update(parsed)
        except Exception:
            pass

    file_path = item.get("file_path")
    if file_path:
        try:
            path = Path(str(file_path))
            if path.exists():
                meta2, body = parse_frontmatter(path)
                meta.update(meta2)
        except Exception:
            pass
    elif str(item.get("content") or "").strip():
        meta2, body = parse_frontmatter_text(str(item.get("content")))
        meta.update(meta2)
    return meta, body


def _normalise_symbol(value) -> str | None:
    if not value:
        return None
    symbol = str(value).upper().replace(" ", "")
    return symbol if "/" in symbol else None


def _find_symbol(text: str) -> str | None:
    import re

    match = re.search(r"\b([A-Z0-9]{2,12}/USDT)\b", text.upper())
    return match.group(1) if match else None


def _normalise_direction(value) -> str | None:
    direction = str(value or "").lower().strip()
    if direction in ("long", "buy", "bull", "bullish", "做多"):
        return "long"
    if direction in ("short", "sell", "bear", "bearish", "做空"):
        return "short"
    return None


def _find_direction(text: str) -> str | None:
    if any(token in text for token in (" short ", " bearish ", "做空")):
        return "short"
    if any(token in text for token in (" long ", " bullish ", "做多")):
        return "long"
    return None


def _detect_stance(item: dict, meta: dict, text: str) -> str | None:
    item_type = str(item.get("item_type") or "").lower()
    block_words = ("avoid", "block", "blocked", "do not", "don't", "forbid", "禁止", "不要", "避免")
    support_words = ("support", "supported", "recommend", "candidate", "opportunity", "bullish", "bearish", "做多", "做空")

    if bool(meta.get("block_candidate")) or str(meta.get("enforcement") or "").lower() == "blocking":
        return "block"
    if item_type in ("trading_guard", "guard_candidate") and any(word in text for word in block_words):
        return "block"
    if any(word in text for word in block_words):
        return "block"
    if any(word in text for word in support_words):
        return "support"
    return None
