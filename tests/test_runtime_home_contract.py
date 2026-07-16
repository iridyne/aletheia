"""Architecture tripwires for repository-independent runtime state."""

from __future__ import annotations

import re
from pathlib import Path


def test_runtime_consumers_inherit_selected_home(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.agent_shell.reflection as reflection
    from aletheia.config import secrets
    from aletheia.gateway.frontdoor_route import frontdoor_route_path
    from aletheia.gateway.logs import errors_log_path, log_path
    from aletheia.gateway.operator_shell import operator_shell_pid_path
    from aletheia.gateway.pidfile import pid_path
    from aletheia.storage.sqlite import db_path

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", None)

    assert db_path() == tmp_path / "app.db"
    assert secrets._fallback_path() == tmp_path / "secrets.enc"
    assert pid_path() == tmp_path / "gateway.pid"
    assert operator_shell_pid_path() == tmp_path / "operator-shell.pid"
    assert frontdoor_route_path() == tmp_path / "frontdoor-route.json"
    assert log_path() == tmp_path / "logs" / "gateway.log"
    assert errors_log_path() == tmp_path / "logs" / "errors.log"
    assert reflection._reflection_file() == tmp_path / "pi_reflection.json"


def test_production_python_has_no_repository_relative_runtime_writes():
    root = Path(__file__).resolve().parents[1]
    allowed = {
        root / "aletheia" / "runtime_home_migration.py",
        root / "aletheia" / "simulation" / "control_loop" / "runner.py",
    }
    patterns = (
        re.compile(r"Path\([\"']data(?:[\"'/])"),
        re.compile(r"(?:_PROJECT_ROOT|project_root|root)\s*/\s*[\"']data[\"']"),
        re.compile(r"sqlite3\.connect\([\"']data/app\.db[\"']"),
    )
    violations: list[str] = []
    for base in (root / "aletheia", root / "scripts"):
        for path in base.rglob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if any(pattern.search(text) for pattern in patterns):
                violations.append(str(path.relative_to(root)))
    assert violations == [], f"repository-relative runtime paths found: {violations}"
