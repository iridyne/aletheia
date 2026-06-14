"""Tests for knowledge frontmatter parser."""

from aletheia.knowledge.frontmatter import parse_frontmatter_text, render_frontmatter


def test_parse_valid_frontmatter():
    text = "---\ntype: Trading Guard\nguard_id: test_v1\n---\n\n# Body here\n"
    meta, body = parse_frontmatter_text(text)
    assert meta["type"] == "Trading Guard"
    assert meta["guard_id"] == "test_v1"
    assert "# Body here" in body


def test_no_frontmatter_returns_empty_meta():
    text = "# Just a markdown file\n\nSome content."
    meta, body = parse_frontmatter_text(text)
    assert meta == {}
    assert body == text


def test_invalid_yaml_returns_empty_meta():
    text = "---\n: [invalid yaml\n---\n\nbody\n"
    meta, body = parse_frontmatter_text(text)
    assert meta == {}


def test_render_frontmatter_roundtrip():
    meta = {"type": "Trading Guard", "guard_id": "test_v1", "status": "active"}
    body = "# Rationale\n\nSome explanation.\n"
    rendered = render_frontmatter(meta, body)
    assert rendered.startswith("---\n")
    assert "guard_id: test_v1" in rendered
    assert "# Rationale" in rendered


def test_render_preserves_unicode():
    meta = {"title": "追涨 Guard", "tags": ["中文", "测试"]}
    body = "中文内容\n"
    rendered = render_frontmatter(meta, body)
    assert "追涨 Guard" in rendered
    assert "中文内容" in rendered
