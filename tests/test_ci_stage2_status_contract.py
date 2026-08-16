from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
EXPECTED_STAGE1_GATE = (
    "${{ !cancelled() && needs.review.result == 'success' && "
    "needs.unit.result == 'success' }}"
)


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_stage2_lane_heads_override_skipped_review_ancestors() -> None:
    jobs = _workflow()["jobs"]

    for job_name in ("runtime-state-tests", "integration"):
        job = jobs[job_name]
        assert job["needs"] == ["review", "unit"]
        assert job["if"] == EXPECTED_STAGE1_GATE
