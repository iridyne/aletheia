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


def test_windows_dev_launcher_supports_bounded_isolated_helper_ports():
    """The current dev helper may choose local ports without controlling Runtime ports."""
    script = Path("scripts/dev.ps1").read_text(encoding="utf-8")
    assert "[int]$UiPort = 5173" in script
    assert "[int]$ApiPort = 0" in script
    assert 'http://127.0.0.1:$ApiPort' in script
    assert "UiPort $UiPort is reserved" in script
    assert "Custom ApiPort $ApiPort is reserved" in script
    assert "[string]$ApiTarget" not in script


def test_instance_docs_distinguish_runtime_from_worktree_hmr():
    docs = [
        Path("AGENTS.md"),
        Path("README.md"),
        Path("docs/operations/runbooks/RUNBOOK.md"),
        Path("docs/_ai/workflow/PROJECT_CONTEXT.md"),
        Path("docs/operations/development/MULTI_INSTANCE_RUNTIME.md"),
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "8787" in text, f"{path} must document the stable gateway"
        assert "5173" in text, f"{path} must document worktree HMR"


def test_multi_machine_protocol_separates_development_from_qualification():
    """The canonical protocol must prevent PR velocity from moving the evidence target."""
    protocol = Path("docs/_ai/workflow/DEV_PROTOCOL.md").read_text(encoding="utf-8")
    required_contracts = [
        "所有启动实例都是同一种 Production-grade Runtime",
        "运行意图不是 Runtime 类型",
        "同一个 `exchange account + environment + execution mode`",
        "不跟随 `master`",
        "#425 是 #422 的设计后继",
        "不能声称“多 Production-grade 实例已支持”",
    ]
    for contract in required_contracts:
        assert contract in protocol


def test_vite_dev_proxy_blocks_writes_to_the_master_gateway():
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "preview_readonly_master_api" in cfg
    assert "VITE_API_TARGET" in cfg
    assert "isMasterGatewayTarget" in cfg
    assert "entryFileNames" not in cfg
