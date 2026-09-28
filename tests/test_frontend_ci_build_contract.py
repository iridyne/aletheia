from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
FRONTEND_PACKAGE_PATH = ROOT / "frontend" / "package.json"
EXPECTED_BUILD = (
    "tsc -b && vite build && node scripts/compress-assets.mjs && "
    "node scripts/check-performance.mjs"
)


def test_manual_full_validation_keeps_frontend_contract_checks() -> None:
    workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["full-ci-verdict"]
    dashboard = next(
        step for step in job["steps"] if step["name"] == "Test and build dashboard"
    )
    commands = str(dashboard["run"])

    assert "npm ci" in commands
    assert "npm run check:settings-types" in commands
    assert "npm run check:api-types" in commands
    assert "npm test" in commands
    assert "npm run build" in commands

    package = json.loads(FRONTEND_PACKAGE_PATH.read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == EXPECTED_BUILD


def test_settings_schema_changes_remain_known_to_local_planner() -> None:
    planner = (ROOT / "scripts" / "ci" / "ci_plan.py").read_text(encoding="utf-8")

    for path in (
        "core/aletheia/server/routes/settings.py",
        "core/aletheia/config/",
        "scripts/ci/export_openapi_schema.py",
    ):
        assert path in planner
