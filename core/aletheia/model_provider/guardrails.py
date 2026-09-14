"""Detect-only prompt guardrails for the tokenomics harness.

The detector intentionally returns only stable rule identifiers and counts.  It
never persists a prompt, matched text, or a derived secret value.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("secret_key_filename", re.compile(r"(?i)(?:^|[/\\])[^\s/\\]*\.secret\.key\b")),
)


def detect_prompt_secrets(parts: Iterable[str | None]) -> list[dict[str, int | str]]:
    """Return bounded, content-free observations for deterministic secret shapes."""
    text = "\n".join(part for part in parts if part)
    return [
        {"rule_id": rule_id, "match_count": len(pattern.findall(text))}
        for rule_id, pattern in _RULES
        if pattern.search(text)
    ]
