"""Markdown file writer for knowledge base documents."""

import os
import re
import yaml
from pathlib import Path
from datetime import datetime

from aletheia.config.loader import get_data_dir

_SAFE_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")


def write_research_note(title: str, content: str, metadata: dict | None = None) -> Path:
    """Write a research note markdown file."""
    return _write_knowledge_file("research_notes", title, content, metadata)


def write_strategy_doc(strategy_id: str, title: str, content: str) -> Path:
    """Write a strategy description markdown."""
    if not _SAFE_ID_RE.match(strategy_id):
        raise ValueError(f"Invalid strategy_id format: {strategy_id!r}")
    base = get_data_dir() / "knowledge" / "strategies" / strategy_id
    base.mkdir(parents=True, exist_ok=True)
    path = base / "strategy.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def write_dreaming_log(title: str, content: str) -> Path:
    """Write a dreaming log markdown."""
    return _write_knowledge_file("dreaming_logs", title, content)


def write_daily_review(content: str, title: str | None = None) -> Path:
    """Write a daily review markdown."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return _write_knowledge_file("daily_reviews", title or f"review-{today}", content)


def write_post_trade_review(title: str, content: str, metadata: dict | None = None) -> Path:
    """Write a post-trade review markdown."""
    return _write_knowledge_file("post_trade_reviews", title, content, metadata)


def write_trade_case_study(title: str, content: str, metadata: dict | None = None) -> Path:
    """Write a trade case study markdown."""
    return _write_knowledge_file("case_studies", title, content, metadata)


def write_trading_guard(title: str, content: str, metadata: dict | None = None) -> Path:
    """Write a trading guard markdown."""
    return _write_knowledge_file("guards", title, content, metadata)


def write_guard_candidate(title: str, content: str, metadata: dict | None = None) -> Path:
    """Write a guard candidate markdown."""
    return _write_knowledge_file("guard_candidates", title, content, metadata)


def _write_knowledge_file(subdir: str, title: str, content: str, metadata: dict | None = None) -> Path:
    base = get_data_dir() / "knowledge" / subdir
    base.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in title).strip().replace(" ", "-").lower()
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    # #129 fix: add 4-char hex suffix so multiple writes within the same second
    # don't overwrite each other (e.g. batch evidence extraction).
    import secrets as _sec
    suffix = _sec.token_hex(2)
    filename = f"{timestamp}-{suffix}-{safe_name}.md"
    path = base / filename

    header = f"# {title}\n\n"
    if metadata:
        header += "---\n"
        header += yaml.dump(metadata, default_flow_style=False, allow_unicode=True)
        header += "---\n\n"

    tmp = path.with_suffix(".tmp")
    tmp.write_text(header + content, encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path
