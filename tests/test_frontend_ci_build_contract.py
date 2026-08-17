from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
FRONTEND_PACKAGE_PATH = ROOT / "frontend" / "package.json"
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
EXPECTED_BUILD = (
    "tsc -b && vite build && node scripts/compress-assets.mjs && "
    "node scripts/check-performance.mjs"
)


def _command_tokens(line: str) -> list[str]:
    tokens = shlex.split(line)
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
    while tokens and ENV_ASSIGNMENT.fullmatch(tokens[0]):
        tokens = tokens[1:]
    return tokens


def _is_direct_typecheck(line: str) -> bool:
    tokens = _command_tokens(line)
    return (
        len(tokens) >= 3
        and tokens[:2] == ["npx", "tsc"]
        and any(argument in {"-b", "--build"} for argument in tokens[2:])
    )


def test_direct_typecheck_detection_normalizes_environment_prefixes() -> None:
    assert _is_direct_typecheck("npx tsc -b --pretty false")
    assert _is_direct_typecheck("NODE_OPTIONS=--max-old-space-size=4096 npx tsc -b")
    assert _is_direct_typecheck('NODE_OPTIONS="--max-old-space-size=4096" npx tsc --build')
    assert _is_direct_typecheck("env NODE_OPTIONS=ci npx tsc -b")
    assert not _is_direct_typecheck('VITE_BUILD_OUT_DIR="$build_dir" npm run build')


def test_frontend_review_typechecks_once_via_production_build() -> None:
    workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    review_node = workflow["jobs"]["review-node"]
    dashboard = next(step for step in review_node["steps"] if step["name"] == "Check dashboard")
    commands = str(dashboard["run"])

    executed_lines = [
        line.strip()
        for line in commands.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(_is_direct_typecheck(line) for line in executed_lines)
    assert "npm run check:settings-types" in executed_lines
    assert any("npm run build" in line for line in executed_lines)
    assert 'test -s "$build_dir/performance.json"' in executed_lines

    package = json.loads(FRONTEND_PACKAGE_PATH.read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == EXPECTED_BUILD


def test_settings_schema_changes_run_the_generated_contract_check() -> None:
    workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    core = workflow["jobs"]["review-core"]
    detector = next(step for step in core["steps"] if step["name"] == "Detect affected review surfaces")
    script = str(detector["run"])

    for path in (
        "aletheia/server/routes/settings.py",
        "aletheia/config/schema.py",
        "scripts/export_openapi_schema.py",
    ):
        assert path in script
