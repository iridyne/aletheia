"""Test #1227: analytics routes never return float('inf') in JSON."""
from __future__ import annotations

import json
import math

from aletheia.server.routes.analytics import _safe_pf


def test_safe_pf_all_wins():
    result = _safe_pf(100.0, 0.0)
    assert result["unbounded"] is True
    assert result["value"] is None


def test_safe_pf_normal():
    result = _safe_pf(100.0, 50.0)
    assert result["unbounded"] is False
    assert result["value"] == 2.0


def test_safe_pf_no_trades():
    result = _safe_pf(0.0, 0.0)
    assert result["value"] is None
    assert result["unbounded"] is False


def test_safe_pf_json_serializable():
    for scenario in [_safe_pf(100, 0), _safe_pf(100, 50), _safe_pf(0, 0)]:
        serialized = json.dumps(scenario)
        parsed = json.loads(serialized)
        if parsed["value"] is not None:
            assert math.isfinite(parsed["value"])
