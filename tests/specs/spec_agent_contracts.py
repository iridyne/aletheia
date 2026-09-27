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

    src = Path(__file__).parent.parent.parent / "core" / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    matches = re.findall(r"max_workers\s*=\s*(\d+)", source)
    assert matches, "No max_workers found in scheduler.py"
    for val in matches:
        assert int(val) <= 3, (
            f"Scheduler max_workers={val} exceeds cap of 3"
        )






@spec_claim(
    SpecClaim(
        "AC-S04", "agent_contracts", "§4 knowledge manager", "L2a",
        "Knowledge manager exposes CRUD functions for all item types",
    ),
)
def test_knowledge_manager_crud():
    from extensions.knowledge import manager

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
    from extensions.knowledge import guards

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
    from extensions.research import equity_observer

    assert hasattr(equity_observer, "EquityRead")




@spec_claim(
    SpecClaim(
        "AC-S09", "agent_contracts", "§1 executor pools", "L2a",
        "scheduler.py defines exactly the executor pools "
        "{default, io, audit, fast_io, protective, maintenance_io, "
        "observer, agents} — no extras, no missing",
    ),
)
def test_scheduler_executor_pool_names():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "core" / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    m = re.search(r'_EXECUTOR_WORKERS\s*=\s*\{([^}]+)\}', source, re.DOTALL)
    assert m, "_EXECUTOR_WORKERS dict not found in scheduler.py"
    keys = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
    expected = {
        "default",
        "io",
        "maintenance_io",
        "audit",
        "fast_io",
        "protective",
        "observer",
        "agents",
    }
    assert keys == expected, (
        f"Executor pool names mismatch. Expected={sorted(expected)} Got={sorted(keys)}"
    )


def test_long_maintenance_jobs_do_not_share_short_io_lane():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "core" / "aletheia" / "agent_runtime" / "scheduler.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    init_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "init_scheduler"
    )
    executors = {}
    for call in ast.walk(init_fn):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add_job"
        ):
            continue
        keywords = {item.arg: item.value for item in call.keywords if item.arg}
        job_id = keywords.get("id")
        executor = keywords.get("executor")
        if isinstance(job_id, ast.Constant) and isinstance(executor, ast.Constant):
            executors[job_id.value] = executor.value

    assert {
        job_id: executors[job_id]
        for job_id in (
            "universe_refresh",
            "outcome_settler",
            "ohlcv_nightly_backfill",
            "ohlcv_active_backfill",
        )
    } == {
        "universe_refresh": "maintenance_io",
        "outcome_settler": "maintenance_io",
        "ohlcv_nightly_backfill": "maintenance_io",
        "ohlcv_active_backfill": "maintenance_io",
    }
    assert executors["tpsl_fill_check"] == "io"


def test_scheduler_job_ids_unique():
    import ast
    from collections import Counter
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "core" / "aletheia" / "agent_runtime" / "scheduler.py"
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

    src = Path(__file__).parent.parent.parent / "core" / "aletheia" / "agent_runtime" / "scheduler.py"
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
    from tests.module_source import ROOT, seam_modules

    source = "core/aletheia/agent_runtime/scheduler.py"
    trees = [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in [ROOT / source, *seam_modules(source)]
    ]
    functions = {
        node.name: (path, node)
        for path, tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    registered = []
    for path, tree in trees:
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_job"
                and node.args
                and isinstance(node.args[0], ast.Call)
            ):
                continue
            guarded = node.args[0]
            if not (
                isinstance(guarded.func, ast.Name)
                and guarded.func.id == "_guarded"
                and len(guarded.args) >= 2
            ):
                continue
            job, callback = guarded.args[:2]
            if isinstance(job, ast.Constant) and isinstance(callback, ast.Name):
                registered.append((str(job.value), callback.id, path, node.lineno))
    assert registered, "No _guarded scheduler jobs found"
    missing = []
    for job_id, name, path, line in registered:
        owner = functions.get(name)
        if owner is not None:
            path, function = owner
            line = function.lineno
            if any(
                isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "_record_job_failure"
                for call in ast.walk(function)
            ):
                continue
        missing.append(f"{path.relative_to(ROOT)}:{line}: {job_id}->{name}")
    assert not missing, f"Registered wrappers must record caught failures: {missing}"


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
        "AC-S15", "agent_contracts", "§3 exchange boundary", "L2a",
        "Upper trading domains consume normalized capability facts and never import "
        "concrete exchange clients or concrete execution brokers",
    ),
)
def test_trading_domains_do_not_import_concrete_venue_implementations():
    import ast

    root = Path(__file__).parent.parent.parent
    forbidden_brokers = {
        "aletheia.execution.okx_broker",
        "aletheia.execution.bitget_broker",
        "aletheia.execution.binance_broker",
    }
    violations: list[str] = []
    for domain in ("agents", "trading", "universe", "risk"):
        for source in sorted((root / "core" / "aletheia" / domain).rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [
                        node.module,
                        *(
                            f"{node.module}.{alias.name}"
                            for alias in node.names
                            if node.module in {"aletheia.exchanges", "extensions.exchanges", "aletheia.execution"}
                        ),
                    ]
                for dependency in imported:
                    if (
                        dependency.startswith(("aletheia.exchanges.", "extensions.exchanges"))
                        or dependency in forbidden_brokers
                    ):
                        violations.append(
                            f"{source.relative_to(root)}:{node.lineno} -> {dependency}"
                        )

    assert violations == [], (
        "Upper trading domains must not import concrete venue implementations:\n"
        + "\n".join(violations)
    )


@spec_claim(
    SpecClaim(
        "AC-S13", "agent_contracts", "§B canonical planner wake", "L2a",
        "Durable Candidate planning wakes the daemon, the ~15min heartbeat remains the "
        "fallback, and no retired Ranker wake source comes back.",
        status="implemented",
    ),
)
def test_canonical_planner_wake_sources_and_fallback_are_wired():
    """agent_contracts.md §B: the planner's only on-demand wake is durable Candidate
    planning; §3 retires the Ranker/PaperTrader queue *and its on-demand notification*.

    The retired half is asserted as ABSENT on purpose. Reading only the deleted call
    sites makes those five wake edges look like a #2945 regression, and re-adding them
    would contradict `agent_contracts.md:174` ("does not wake Ranker"), `:191`
    ("research Opportunities do not wake a retired entry queue") and `:468` ("old
    pending cannot ... wake a retired evaluator"). The three observers involved all
    commit `execution_eligible=False` Opportunities, so waking an entry planner for
    them can never produce an entry.
    """
    from pathlib import Path

    root = Path(__file__).parent.parent.parent

    # The live wake path: durable Candidate planning, routed through pi_daemon.
    service = (root / "core/aletheia/universe/service.py").read_text(encoding="utf-8")
    assert "notify_pi_daemon(source=source)" in service
    for wake_source in (
        "candidate_planning_generation_created",
        "universe_refresh_new_snapshot",
    ):
        assert wake_source in service

    # The scheduled fallback that must survive (contract row 6).
    scheduler = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    assert '_guarded("pi_trading_heartbeat", _run_pi_trading_decision)' in scheduler
    assert 'id="pi_trading_decision"' in scheduler
    assert "_pi_fallback_min = 15" in scheduler

    # The retired wake sources must stay retired.
    retired = {
        "extensions/research/equity_observer.py": "equity_observer_opportunity",
        "extensions/research/prediction_observer.py": "prediction_observer_opportunity",
        "extensions/research/divergence_scanner.py": "divergence_scanner_opportunity",
        "extensions/analytics/guard_promotion.py": "guard_set_changed",
        "core/aletheia/server/routes/settings.py": "ranker_config_changed",
    }
    for relative, retired_source in retired.items():
        assert retired_source not in (root / relative).read_text(encoding="utf-8"), (
            f"{relative} re-added the retired wake source {retired_source!r}; "
            "agent_contracts.md \u00a73 retires it"
        )

    # notify_paper_trader itself is gone; nothing may reintroduce the symbol.
    for relative in ("core/aletheia/agent_runtime/scheduler.py", "core/aletheia/agent_shell/commands.py"):
        assert "notify_paper_trader" not in (root / relative).read_text(encoding="utf-8")
