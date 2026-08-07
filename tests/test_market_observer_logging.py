"""Logging-severity contract for the Market Observer hot path."""

from __future__ import annotations

import ast
from pathlib import Path


def test_market_observer_diagnostics_are_debug_only() -> None:
    """Routine per-symbol diagnostics must never masquerade as warnings."""

    root = Path(__file__).resolve().parents[1]
    source = (root / "aletheia" / "agents" / "market_observer.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    levels: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "logger"
        ):
            continue
        message = node.args[0]
        if isinstance(message, ast.Constant) and "[MO DIAG]" in str(message.value):
            levels.append(function.attr)

    assert levels, "expected the Market Observer diagnostic log contract to exist"
    assert set(levels) == {"debug"}


def test_market_observer_failures_remain_warnings() -> None:
    """Demoting duplicate diagnostics must not hide the authoritative failures."""

    root = Path(__file__).resolve().parents[1]
    source = (root / "aletheia" / "agents" / "market_observer.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    warning_messages = {
        str(node.args[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
        and node.func.attr == "warning"
        and isinstance(node.args[0], ast.Constant)
    }

    assert any("LLM call FAILED" in message for message in warning_messages)
    assert any("prefetch batch failed" in message for message in warning_messages)
    assert any("prefetch exhausted" in message for message in warning_messages)
