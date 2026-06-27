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
    """Parse YAML frontmatter from raw text. Returns (meta, body).

    Accepts two layouts: a bare ``---\\n...---\\n`` block at the very start
    (render_frontmatter output), OR a single leading ``# Title`` markdown header
    followed by the block (the _write_knowledge_file layout used for guard
    candidates / case studies). Without the second case, every guard_candidate
    file parsed to {} and the promotion chain saw zero candidates (L-Bug-D).
    """
    scan = text
    if not scan.startswith("---\n"):
        import re
        m = re.match(r"#[^\n]*\n+(?=---\n)", scan)
        if m:
            scan = scan[m.end():]
    if not scan.startswith("---\n"):
        return {}, text
    parts = scan.split("---\n", 2)
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
