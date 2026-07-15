from pathlib import Path
import json


def test_vite_build_empties_output_dir():
    """Build must clean stale assets — emptyOutDir: true prevents dead file accumulation.

    The build is an offline operation (npm run build + commit), not a hot-swap
    while the gateway is serving. emptyOutDir: true ensures only current-build
    files exist in dashboard_static/ — no orphan hashed assets from prior builds.
    """
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "emptyOutDir: true" in cfg


def test_worktree_dev_launcher_uses_vite_hmr_without_gateway_lifecycle():
    """Worktree UI development is isolated from the stable gateway on :8787."""
    script = Path("scripts/dev.ps1").read_text(encoding="utf-8")
    assert "http://127.0.0.1:5173" in script
    assert "npm run dev" in script
    assert "gateway start" not in script
    assert "gateway restart" not in script

    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["dev"] == "vite"


def test_preview_docs_distinguish_stable_gateway_from_worktree_hmr():
    docs = [
        Path("AGENTS.md"),
        Path("README.md"),
        Path("docs/operations/RUNBOOK.md"),
        Path("docs/_ai/PROJECT_CONTEXT.md"),
        Path("docs/operations/DEVELOPMENT_PREVIEW.md"),
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "8787" in text, f"{path} must document the stable gateway"
        assert "5173" in text, f"{path} must document worktree HMR"


def test_vite_dev_proxy_blocks_writes_to_the_master_gateway():
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "preview_readonly_master_api" in cfg
    assert "VITE_API_TARGET" in cfg
    assert "isMasterGatewayTarget" in cfg
    assert "entryFileNames" not in cfg
