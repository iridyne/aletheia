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


def test_windows_dev_launcher_supports_bounded_parallel_preview_ports():
    """Windows Worktrees may choose local ports without controlling stable Runtime ports."""
    script = Path("scripts/dev.ps1").read_text(encoding="utf-8")
    assert "[int]$UiPort = 5173" in script
    assert "[int]$ApiPort = 0" in script
    assert 'http://127.0.0.1:$ApiPort' in script
    assert "UiPort $UiPort is reserved" in script
    assert "Custom ApiPort $ApiPort is reserved" in script
    assert "[string]$ApiTarget" not in script


def test_preview_docs_distinguish_stable_gateway_from_worktree_hmr():
    docs = [
        Path("AGENTS.md"),
        Path("README.md"),
        Path("docs/operations/runbooks/RUNBOOK.md"),
        Path("docs/_ai/workflow/PROJECT_CONTEXT.md"),
        Path("docs/operations/development/DEVELOPMENT_PREVIEW.md"),
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "8787" in text, f"{path} must document the stable gateway"
        assert "5173" in text, f"{path} must document worktree HMR"


def test_multi_machine_protocol_separates_development_from_qualification():
    """The canonical protocol must prevent PR velocity from moving the evidence target."""
    protocol = Path("docs/_ai/workflow/DEV_PROTOCOL.md").read_text(encoding="utf-8")
    required_contracts = [
        "PR merge 与 Runtime deployment 分离",
        "一个 stable/qualification Runtime",
        "按需启动的临时验证进程",
        "外部交易账号单写者",
        "Qualification Runtime 不跟随 `master`",
        "不得把临时进程",
    ]
    for contract in required_contracts:
        assert contract in protocol


def test_vite_dev_proxy_blocks_writes_to_the_master_gateway():
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "preview_readonly_master_api" in cfg
    assert "VITE_API_TARGET" in cfg
    assert "isMasterGatewayTarget" in cfg
    assert "entryFileNames" not in cfg
