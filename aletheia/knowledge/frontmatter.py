"""YAML frontmatter parser/renderer for knowledge artifacts."""

from pathlib import Path
from typing import Any

import yaml


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file. Returns (meta, body)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, ""
    return parse_frontmatter_text(text)


def parse_frontmatter_text(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from raw text. Returns (meta, body)."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    meta_raw = parts[1]
    body = parts[2]
    try:
        meta = yaml.safe_load(meta_raw) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def render_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Render a markdown document with YAML frontmatter."""
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return "---\n" + fm + "---\n\n" + body.lstrip("\n")
