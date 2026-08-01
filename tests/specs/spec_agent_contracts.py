"""Spec: Agent Contracts (agent_contracts.md)

Scheduler job structure, agent IO shapes, max_workers cap.
"""

import inspect
from pathlib import Path
import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AC-S01", "agent_contracts", "§1 scheduler cap", "L2a",
        "ThreadPoolExecutor max_workers is capped at <= 3",
    ),
)
def test_scheduler_max_workers_cap():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    matches = re.findall(r"max_workers\s*=\s*(\d+)", source)
    assert matches, "No max_workers found in scheduler.py"
    for val in matches:
        assert int(val) <= 3, (
            f"Scheduler max_workers={val} exceeds cap of 3"
        )


@spec_claim(
    SpecClaim(
        "AC-S02", "agent_contracts", "§2 System Operator", "L2a",
        "System Operator (trading_decision) exists with evaluate_trading_opportunity entry point",
    ),
)
def test_pi_agent_entry_point():
    from aletheia.agent_shell import trading_decision

    assert callable(trading_decision.evaluate_trading_opportunity)


@spec_claim(
    SpecClaim(
        "AC-S03", "agent_contracts", "§3 paper_trader", "L2a",
        "paper_trader module exists and does not import LLM helpers",
    ),
)
def test_paper_trader_no_llm():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agents" / "paper_trader.py"
    if not src.exists():
        pytest.skip("paper_trader.py not found")
    imports = set()
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    llm_imports = [i for i in imports if "llm" in i.lower() or "openai" in i.lower()]
    assert llm_imports == [], (
        f"paper_trader must not call LLM directly: {llm_imports}"
    )


@spec_claim(
    SpecClaim(
        "AC-S04", "agent_contracts", "§4 knowledge manager", "L2a",
        "Knowledge manager exposes CRUD functions for all item types",
    ),
)
def test_knowledge_manager_crud():
    from aletheia.knowledge import manager

    required = [
        "create_research_note",
        "create_trading_guard",
        "create_guard_candidate",
        "archive_item",
    ]
    for fn_name in required:
        assert hasattr(manager, fn_name) and callable(getattr(manager, fn_name)), \
            f"knowledge/manager.py missing {fn_name}"


@spec_claim(
    SpecClaim(
        "AC-S05", "agent_contracts", "§5 guard evaluation", "L2a",
        "Guard evaluation pipeline exists (load -> evaluate -> format)",
    ),
)
def test_guard_pipeline_functions():
    from aletheia.knowledge import guards

    assert callable(guards.load_trading_guards)
    assert callable(guards.evaluate_guards)
    assert callable(guards.format_guard_block)


@spec_claim(
    SpecClaim(
        "AC-S06", "agent_contracts", "§2 equity_observer", "L2a",
        "equity_observer module exists (replaces stock_observer)",
    ),
)
def test_equity_observer_exists():
    from aletheia.agents import equity_observer

    assert hasattr(equity_observer, "EquityRead")


@spec_claim(
    SpecClaim(
        "AC-S07", "agent_contracts", "§2 paper_trader_ranker", "L2a",
        "paper_trader_ranker exists and does not import LLM helpers",
    ),
)
def test_paper_trader_ranker_no_llm():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agents" / "paper_trader_ranker.py"
    if not src.exists():
        pytest.skip("paper_trader_ranker.py not found")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    blocked = {"aletheia.agents.helpers", "aletheia.agents.helpers.llm"}
    llm_imports = [
        i for i in imports
        if "llm" in i.lower() or "openai" in i.lower() or i in blocked
    ]
    assert llm_imports == [], (
        f"paper_trader_ranker must not call LLM directly: {llm_imports}"
    )


@spec_claim(
    SpecClaim(
        "AC-S08", "agent_contracts", "§1 job count", "L2a",
        "Scheduler registers >= 28 jobs (matching SYSTEM_MAP)",
    ),
)
def test_scheduler_minimum_job_count():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    job_count = len(re.findall(r'_scheduler\.add_job\(', source))
    assert job_count >= 28, f"Expected >= 28 registered jobs, found {job_count}"


@spec_claim(
    SpecClaim(
        "AC-S09", "agent_contracts", "§1 executor pools", "L2a",
        "scheduler.py defines exactly the executor pools {default, io, fast_io, observer, agents} — no extras, no missing",
    ),
)
def test_scheduler_executor_pool_names():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    m = re.search(r'_EXECUTOR_WORKERS\s*=\s*\{([^}]+)\}', source, re.DOTALL)
    assert m, "_EXECUTOR_WORKERS dict not found in scheduler.py"
    keys = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
    expected = {"default", "io", "fast_io", "observer", "agents"}
    assert keys == expected, (
        f"Executor pool names mismatch. Expected={sorted(expected)} Got={sorted(keys)}"
    )


def test_scheduler_job_ids_unique():
    import ast
    from collections import Counter
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    # Only boot-time registrations (inside init_scheduler) must be unique.
    # Reconciliation helpers use conditional add_job (guarded by get_job() is None)
    # and are intentionally allowed to name the same IDs.
    init_fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "init_scheduler"),
        None,
    )
    assert init_fn is not None, "init_scheduler() not found in scheduler.py"

    ids = []
    for node in ast.walk(init_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(getattr(node.func, "attr", None), str)
            and node.func.attr == "add_job"
        ):
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    ids.append(kw.value.value)

    assert ids, "No job id= values found in init_scheduler()"
    counts = Counter(ids)
    dupes = {k: v for k, v in counts.items() if v > 1}
    assert not dupes, f"Duplicate scheduler job IDs in init_scheduler(): {dupes}"


@spec_claim(
    SpecClaim(
        "AC-S11", "agent_contracts", "§1 OS session service", "L2c",
        "Gateway start uses an on-demand OS user service; the macOS definition "
        "runs the foreground runner without KeepAlive, login scheduling, or "
        "credential-bearing environment variables.",
        status="implemented",
    ),
)
def test_gateway_session_service_has_safe_macos_launchd_contract():
    from aletheia.gateway import managed_service

    payload = managed_service._build_macos_plist(no_browser=True)
    assert payload["ProgramArguments"][-2].endswith("_gateway_runner.py")
    assert payload["ProgramArguments"][-1] == "--no-browser"
    assert "KeepAlive" not in payload
    assert "RunAtLoad" not in payload
    assert "StartInterval" not in payload
    assert "EnvironmentVariables" not in payload


@spec_claim(
    SpecClaim(
        "AC-S14", "agent_contracts", "§1 OS service lifecycle", "L2c",
        "Gateway service installation, enablement, and runtime are independent; "
        "machine installation never collects an administrator password.",
        status="implemented",
    ),
)
def test_gateway_service_lifecycle_contract_is_structurally_safe():
    from aletheia.gateway import service_control, windows_machine_service
    source = inspect.getsource(service_control) + inspect.getsource(windows_machine_service)
    assert "elevation_required" in source
    assert "LocalService" in source
    assert "password =" not in source.lower()
    assert "LocalSystem" not in windows_machine_service._build_winsw_xml(
        executable=Path("C:/Program Files/Aletheia/aletheia.exe")
    )
    from aletheia.agent_runtime import permissions
    forbidden = {"service_install", "service_uninstall", "gateway_enable", "gateway_disable"}
    assert forbidden.isdisjoint(permissions.TOOL_REGISTRY)


@spec_claim(
    SpecClaim(
        "AC-S12", "agent_contracts", "§1 OS session service", "L2c",
        "Git operations never restart the gateway implicitly; legacy standalone "
        "Aletheia hooks are cleanup-only and foreign hooks are preserved.",
        status="implemented",
    ),
)
def test_gateway_lifecycle_is_not_driven_by_git_hooks():
    from aletheia.gateway import git_hooks

    assert not hasattr(git_hooks, "ensure_git_hooks")
    assert callable(git_hooks.remove_managed_git_hooks)


@spec_claim(
    SpecClaim(
        "AC-B01", "agent_contracts", "§3 catch-all isolation", "L2a",
        "Every _run_* job wrapper in scheduler.py contains a try/except block — "
        "an unhandled exception cannot propagate out of a scheduled job",
    ),
)
def test_scheduler_job_wrappers_have_try_except():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    run_fns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_run_")
    ]
    assert run_fns, "No _run_* functions found in scheduler.py"
    missing = [
        fn.name for fn in run_fns
        if not any(isinstance(node, ast.Try) for node in ast.walk(fn))
    ]
    assert missing == [], (
        f"These _run_* job wrappers lack try/except (exceptions would propagate to scheduler): {missing}"
    )


@spec_claim(
    SpecClaim(
        "AC-B05", "agent_contracts", "§3 scheduler failure telemetry", "L2a",
        "Every registered scheduler job wrapper records swallowed failures via _record_job_failure",
    ),
)
def test_registered_scheduler_jobs_record_swallowed_failures():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }

    registered: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        guarded_call = node.args[0]
        if not (
            isinstance(guarded_call.func, ast.Name)
            and guarded_call.func.id == "_guarded"
            and len(guarded_call.args) >= 2
        ):
            continue
        job_arg, fn_arg = guarded_call.args[:2]
        if isinstance(job_arg, ast.Constant) and isinstance(fn_arg, ast.Name):
            registered.append((str(job_arg.value), fn_arg.id))

    assert registered, "No _guarded scheduler jobs found"

    missing = []
    for job_id, fn_name in registered:
        fn = functions.get(fn_name)
        has_failure_record = fn is not None and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_record_job_failure"
            for call in ast.walk(fn)
        )
        if not has_failure_record:
            missing.append(f"{job_id}->{fn_name}")

    assert missing == [], (
        "Registered scheduler jobs must record caught failures so repeated "
        f"failures surface as infra_events: {missing}"
    )


@spec_claim(
    SpecClaim(
        "AC-B02", "agent_contracts", "§3 LLM fallback", "L2b",
        "complete_with_fallback() retries with a secondary model when the primary raises a transient error",
    ),
)
def test_llm_fallback_retries_on_transient_error():
    import asyncio
    import aletheia.model_provider.fallback as fb

    from aletheia.model_provider.base import ModelProvider, ModelRequest, ModelResponse
    from aletheia.model_provider.fallback import complete_with_fallback, reset_model_health

    call_log: list[str] = []

    class _DualProvider(ModelProvider):
        """Single provider object whose .model attribute is swapped by _clone_provider_with_model."""
        name = "test_prov"
        model = "primary-model"

        async def complete(self, request: ModelRequest) -> ModelResponse:
            call_log.append(self.model)
            if self.model == "primary-model":
                raise RuntimeError("503 Service Unavailable")
            return ModelResponse(content="ok", model=self.model)

        async def list_models(self):
            return []

    reset_model_health()
    provider = _DualProvider()
    request = ModelRequest(messages=[{"role": "user", "content": "hi"}])

    async def _run():
        original = fb._candidate_models

        async def _stub(provider_name, prov):
            return ["primary-model", "fallback-model"]

        fb._candidate_models = _stub
        try:
            return await complete_with_fallback(
                request, provider_name="test_prov", provider=provider,
            )
        finally:
            fb._candidate_models = original

    resp = asyncio.run(_run())
    assert resp.content == "ok", f"Expected fallback response, got: {resp.content}"
    assert "primary-model" in call_log, "Primary model should have been attempted"
    assert "fallback-model" in call_log, "Fallback model should have been used after primary failure"


@spec_claim(
    SpecClaim(
        "AC-B02b", "agent_contracts", "§3 LLM fallback network errors", "L2b",
        "Connection-class provider failures enter the configured secondary-model chain",
    ),
)
def test_llm_fallback_retries_on_connect_error():
    import asyncio
    import httpx
    import aletheia.model_provider.fallback as fb

    from aletheia.model_provider.base import ModelProvider, ModelRequest, ModelResponse

    call_log: list[str] = []

    class _NetworkProvider(ModelProvider):
        name = "test_network_provider"
        model = "primary-model"

        async def complete(self, request: ModelRequest) -> ModelResponse:
            call_log.append(self.model)
            if self.model == "primary-model":
                raise httpx.ConnectError("connection reset")
            return ModelResponse(content="ok", model=self.model)

        async def list_models(self):
            return []

    async def _run():
        original = fb._candidate_models

        async def _stub(provider_name, provider):
            return ["primary-model", "fallback-model"]

        fb._candidate_models = _stub
        try:
            return await fb.complete_with_fallback(
                ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                provider_name="test_network_provider",
                provider=_NetworkProvider(),
            )
        finally:
            fb._candidate_models = original

    fb.reset_model_health()
    response = asyncio.run(_run())
    assert response.content == "ok"
    assert call_log == ["primary-model", "fallback-model"]


@spec_claim(
    SpecClaim(
        "AC-B03", "agent_contracts", "§2 System Operator safety", "L2c",
        "trading_decision.py does NOT import any live execution module "
        "(gateway, bitget_broker, okx_broker, binance_broker) — System Operator advises, never orders",
    ),
)
def test_pi_agent_no_live_execution_imports():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_shell" / "trading_decision.py"
    if not src.exists():
        pytest.skip("trading_decision.py not found")

    tree = ast.parse(src.read_text(encoding="utf-8"))
    live_execution_modules = {
        "aletheia.execution.gateway",
        "aletheia.execution.bitget_broker",
        "aletheia.execution.okx_broker",
        "aletheia.execution.binance_broker",
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in live_execution_modules:
                found.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in live_execution_modules:
                    found.append(alias.name)
    assert found == [], (
        f"System Operator (trading_decision.py) must not import live execution modules: {found}. "
        "System Operator advises only — live orders must not be reachable from this path."
    )


@spec_claim(
    SpecClaim(
        "AC-B04", "agent_contracts", "§3 LLM-isolated agents", "L2c",
        "paper_trader.py and paper_trader_ranker.py do not import aletheia.model_provider "
        "or any of its sub-modules (these agents are rule-based, not LLM-driven)",
    ),
)
def test_paper_traders_no_model_provider_imports():
    import ast
    from pathlib import Path

    agents_dir = Path(__file__).parent.parent.parent / "aletheia" / "agents"
    targets = ["paper_trader.py", "paper_trader_ranker.py"]
    for fname in targets:
        fpath = agents_dir / fname
        if not fpath.exists():
            pytest.skip(f"{fname} not found")
        tree = ast.parse(fpath.read_text(encoding="utf-8"))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "model_provider" in node.module:
                    bad.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "model_provider" in alias.name:
                        bad.append(alias.name)
        assert bad == [], (
            f"{fname} must not import model_provider (rule-based agent, not LLM-driven): {bad}"
        )


@spec_claim(
    SpecClaim(
        "AC-S13", "agent_contracts", "§3 paper_trader trigger", "L2a",
        "Every committed Ranker work source has an on-demand wake and the scheduled fallback remains.",
        status="implemented",
    ),
)
def test_paper_trader_on_demand_sources_and_fallback_are_wired():
    from pathlib import Path

    root = Path(__file__).parent.parent.parent
    expected = {
        "aletheia/agents/market_observer.py": "market_observer_opportunity",
        "aletheia/agents/equity_observer.py": "equity_observer_opportunity",
        "aletheia/agents/prediction_observer.py": "prediction_observer_opportunity",
        "aletheia/agents/divergence_scanner.py": "divergence_scanner_opportunity",
        "aletheia/server/routes/settings.py": "ranker_config_changed",
        "aletheia/analytics/guard_promotion.py": "guard_set_changed",
        "aletheia/agent_shell/commands.py": "operator_queue_slot_released",
    }
    for relative, source in expected.items():
        assert source in (root / relative).read_text(encoding="utf-8")

    scheduler = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    assert '_guarded("paper_trader", _run_paper_trader)' in scheduler
    assert '"interval", minutes=3, id="paper_trader"' in scheduler
    assert "on_demand:" in scheduler
