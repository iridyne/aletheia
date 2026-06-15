from pathlib import Path
import json


def test_vite_build_does_not_empty_served_dashboard_dir():
    """Production UI builds must not create an API-only restart window.

    Vite's default `emptyOutDir` deletes dashboard_static before writing the new
    bundle. If the gateway starts in that window, "/" can stay unavailable until
    the next restart. The shipped config keeps old hashed assets while writing
    the new bundle.
    """
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "emptyOutDir: false" in cfg


def test_dev_launcher_uses_single_gateway_port():
    """Daily/dev UI should both use the gateway origin on :8787.

    Regression guard for the old split-brain mode where the operator used
    Vite :5173 during development but the shipped dashboard lived on :8787.
    """
    script = Path("scripts/dev.ps1").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8787" in script
    assert ":5173" not in script
    assert "/api/dev/reload/status" in script
    assert "gateway start --no-browser" in script
    assert "gateway restart --no-browser" in script

    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["dev"] == "npm run build:watch"


def test_current_docs_point_development_to_8787_not_5173():
    docs = [
        Path("AGENTS.md"),
        Path("README.md"),
        Path("docs/operations/RUNBOOK.md"),
        Path("docs/_ai/PROJECT_CONTEXT.md"),
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "127.0.0.1:8787" in text or ":8787" in text
        assert "http://127.0.0.1:5173" not in text, f"{path} still points operators at :5173"
        assert "Iterate against `:5173`" not in text
        assert "Vite on `:5173`" not in text
