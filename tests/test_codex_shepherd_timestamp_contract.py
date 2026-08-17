from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "codex-shepherd.yml"


def test_shepherd_rejects_equal_timestamp_completion_for_newer_request() -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")

    # GitHub comment/review timestamps have second-level granularity. Equality is
    # therefore ambiguous across an old completion and a newly posted request and
    # must fail closed rather than authorize or drain the newer request.
    assert "completedAt > requestMarker.createdAt" in source
    assert "completedAt >= requestMarker.createdAt" not in source
    assert "review.submitted_at > latestReviewRequestAt" in source
    assert "review.submitted_at >= latestReviewRequestAt" not in source
