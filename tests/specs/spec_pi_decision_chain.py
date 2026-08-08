"""Spec: System Operator decision-chain fixes (Round 2 PI-agent audit, 2026-06-28).

Locks the two PI decision-chain fixes made during the PI-agent SDD audit
(see docs/archive/legacy/issues/audits/PI_AGENT_SDD_AUDIT_2026-06-28.md):

  PI-S01 (A5) — the pi_trading_decision scheduler job is a ~15min safety-net
  fallback, DECOUPLED from pi_daemon.heartbeat_sec. The old code computed its
  interval as `heartbeat_sec / 60`, which (with the 60s default) fired it every
  1 minute instead of the contracted ~15min.

  PI-S02 (E1) — the guard chain on the PI proposal path is ADVISORY (pre-risk
  observability), not a hard block. A guard "block" verdict must NOT silently
  drop the PI's decision (`return None`); the verdict is surfaced in the proposal
  rationale and the proposal flows to the Risk Engine, which is the real gate.

Structural (L2a) checks — they assert the fix is present in source and guard
against a regression that silently reverts either behavior.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (_project_root() / rel).read_text(encoding="utf-8")


@spec_claim(
    SpecClaim("PI-S01", "agent_contracts", "§pi_trading_decision cadence", "L2a",
              "pi_trading_decision scheduler fallback interval is ~15min and is NOT coupled "
              "to pi_daemon.heartbeat_sec (decoupling fix A5)"),
)
def test_pi_trading_decision_fallback_decoupled_from_heartbeat():
    src = _src("aletheia/agent_runtime/scheduler.py")
    # The fallback interval must be a fixed ~15min, not derived from heartbeat_sec.
    assert "_pi_fallback_min = 15" in src, (
        "pi_trading_decision fallback interval is not the fixed ~15min safety-net (A5)"
    )
    # The old coupling (heartbeat_sec / 60) must be gone from the job-interval computation.
    assert "cfg.pi_daemon.heartbeat_sec / 60" not in src, (
        "pi_trading_decision interval still coupled to heartbeat_sec — fires far more often "
        "than the contracted ~15min (A5 regression)"
    )


@spec_claim(
    SpecClaim("PI-S02", "agent_contracts", "§guard chain advisory", "L2a",
              "guard chain on the PI proposal path is advisory: a block verdict is logged + "
              "surfaced in rationale but does NOT early-return None; Risk Engine is the gate"),
)
def test_guard_chain_is_advisory_not_hard_block():
    src = _src("aletheia/agent_shell/trading_decision.py")
    # New advisory behavior present.
    assert "guard_chain advisory (not blocking" in src, (
        "guard advisory log marker missing — guard may still hard-block the PI decision"
    )
    # Guard verdict must still be surfaced for observability/audit.
    assert '"guards": guard_summary' in src, (
        "guard_summary no longer attached to the proposal rationale (lost observability)"
    )
    # The old hard-block path must be gone: locate prepare_decision_as_proposal and
    # assert no `return None` follows a `guard_summary["blocks"]` test.
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "prepare_decision_as_proposal"),
        None,
    )
    assert fn is not None, "prepare_decision_as_proposal not found"
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            cond = ast.unparse(node.test)
            if "guard_summary" in cond and "blocks" in cond:
                body = ast.unparse(node.body)
                assert "return None" not in body, (
                    "guard block branch still hard-returns None — not advisory (E1 regression)"
                )


@spec_claim(
    SpecClaim("PI-S03", "agent_contracts", "§operator_loop ⇄ TS convergence", "L2a",
              "Python operator_loop.decide_actions no longer EMITS the operator actions that "
              "converged to the TS operator-shell (retire_strategy / demote_strategy / "
              "request_backfill); it keeps only dynamic config tuning. Prevents the dead-write / "
              "no-op-stub mechanisms from re-appearing as a parallel operator path"),
)
def test_operator_loop_converged_actions_not_emitted():
    src = _src("aletheia/agent_shell/operator_loop.py")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "decide_actions"),
        None,
    )
    assert fn is not None, "decide_actions not found"
    # Collect every action_type=... string literal produced by decide_actions.
    emitted: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg == "action_type":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                emitted.add(node.value.value)
    converged = {"retire_strategy", "demote_strategy", "request_backfill"}
    leaked = converged & emitted
    assert not leaked, (
        f"decide_actions still emits converged-to-TS actions {leaked} — these are owned by "
        "the TS operator-shell now (retire/demote via command registry, backfill via Auto "
        "Repair). Python operator_loop must keep only config tuning (owner decision 2026-06-28)."
    )
    # Aggregate outcome quality is diagnosis-only; the retained Python mutation is
    # the bounded recovery from a previously automatic tightening.
    assert "tighten_confidence" not in emitted
    assert "loosen_confidence" in emitted and "notify_operator" in emitted, (
        "Python must retain bounded confidence recovery and aggregate-quality diagnosis"
    )


@spec_claim(
    SpecClaim("PI-S04", "legacy_promotion_gates", "historical §4 PI approval gate", "L2b",
              "promotion to paper_running requires a recommend_promote PiDecision: blocked when "
              "absent, allowed when present (the PI/operator approval gate is the single chokepoint)",
              status="superseded"),
)
def test_recommend_promote_gates_paper_running(tmp_path, monkeypatch):
    # Position C (2026-07-02): strategy FSM gate deleted.
    # paper_running is now legacy attribution only — this gate no longer exists.
    import pytest as _pytest
    _pytest.skip("PI-S04 superseded — FSM promotion gate deleted in Position C")


# ---------------------------------------------------------------------------
# PI-S05 — §B three-layer context assembly (L2a structural)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PI-S05",
        "agent_contracts",
        "§three-layer context assembly",
        "L2a",
        "build_enriched_trading_context wires all three layer builders "
        "(_build_layer1_snapshot, _build_layer2_short_term, _build_layer3_wisdom) "
        "and returns their results under 'layer1', 'layer2', 'layer3' keys. "
        "Guards against plumbing regressions where a builder call or key assignment "
        "is accidentally dropped.",
    )
)
def test_three_layer_context_assembly_wired():
    src = _src("aletheia/agent_shell/trading_context.py")
    # All three builders must be called inside the function body.
    assert "_build_layer1_snapshot()" in src, (
        "build_enriched_trading_context no longer calls _build_layer1_snapshot() "
        "— layer1 plumbing broken (§B regression)"
    )
    assert "_build_layer2_short_term()" in src, (
        "build_enriched_trading_context no longer calls _build_layer2_short_term() "
        "— layer2 plumbing broken (§B regression)"
    )
    assert "_build_layer3_wisdom()" in src, (
        "build_enriched_trading_context no longer calls _build_layer3_wisdom() "
        "— layer3 plumbing broken (§B regression)"
    )
    # The assembled ctx dict must expose all three layer keys.
    assert '"layer1": layer1' in src, (
        "layer1 key missing from the ctx dict returned by build_enriched_trading_context"
    )
    assert '"layer2": layer2' in src, (
        "layer2 key missing from the ctx dict returned by build_enriched_trading_context"
    )
    assert '"layer3": layer3' in src, (
        "layer3 key missing from the ctx dict returned by build_enriched_trading_context"
    )


# ---------------------------------------------------------------------------
# PCI-G02 — A路架构：observer tools in PI tool registry (L2a, missing)
# ---------------------------------------------------------------------------



@spec_claim(
    SpecClaim(
        "PCI-G02",
        "PI_OPERATOR_DESIGN_DECISIONS",
        "§A路架构：observer tool registry",
        "L2a",
        "market_observer 在 PI 的 agent_shell tool registry 中存在对应工具定义——A路架构结构证明："
        "observers 是 PI 的工具而非独立发言方。tool registry 中必须存在含 'observe_market'、"
        "'observe_chain' 或 'observe_macro' 命名（或等价命名）的工具注册条目。",
        status="implemented",
    )
)
def test_observer_tools_registered_in_pi_tool_registry():
    """A路架构结构检查：PI tool registry 必须包含 observe_market/observe_chain/observe_macro。

    失败状态（当前）：registry 只有 run_market_scan，没有 A路要求的 observe_* 命名条目。
    通过条件（合规态）：COMMANDS 字典中含有 observe_market / observe_chain / observe_macro
    任意一条（精确命名或等价命名）的 CommandDef 注册。
    """
    root = _project_root()
    src = (root / "aletheia/agent_shell/registry.py").read_text(encoding="utf-8")
    observe_names = ("observe_market", "observe_chain", "observe_macro")
    found = [name for name in observe_names if name in src]
    assert found, (
        f"PI tool registry 缺少 A路架构 observer 工具注册条目 {observe_names!r}；"
        "当前只有 run_market_scan，不满足 observer-as-tool 结构契约。"
    )


# ---------------------------------------------------------------------------
# PCI-G05 — B路 council 缺席 tripwire (L2a, missing_tripwire)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="missing_tripwire — B路 council 当前不应存在；若此测试意外通过，"
    "说明有人在 Phase 2 条件触发前引入了 council 机制（需立即 review）",
)
@spec_claim(
    SpecClaim(
        "PCI-G05",
        "PI_OPERATOR_DESIGN_DECISIONS",
        "§B路 council 实现边界",
        "L2a",
        "B路 council（多专家辩论 + 总编仲裁）在当前代码库中不存在任何实现。"
        "任何在 Phase 2 条件触发前引入 council 机制的 PR 都会触发此 tripwire。",
        status="missing",
    )
)
def test_b_path_council_not_yet_implemented():
    """Tripwire：扫描 agent_shell/ 和 agents/ 目录，断言 B路 council 类/函数不存在。

    预期失败（当前）：council / bull_bear / multi_expert / CouncilSession / BullBearCouncil
    不存在，所以 assert found 会 AssertionError → xfail strict=True → 正常。
    触发条件：若未来代码引入上述名称 → found 非空 → assert 通过 → XPASS → CI 报错。
    """
    root = _project_root()
    council_identifiers = (
        "council", "bull_bear", "multi_expert", "CouncilSession", "BullBearCouncil",
    )
    found_in: list[str] = []
    for search_dir in ("aletheia/agent_shell", "aletheia/agents"):
        dir_path = root / search_dir
        if not dir_path.exists():
            continue
        for py_file in dir_path.rglob("*.py"):
            src = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if any(ident in node.name for ident in council_identifiers):
                        found_in.append(f"{py_file.relative_to(root)}::{node.name}")
    assert found_in, (
        "B路 council 实现不存在（预期状态）；此 assert 故意失败以维持 xfail。"
        "若此断言通过，说明 council 代码已被引入，需评估是否满足 Phase 2 触发条件。"
        f"发现的 council 实现：{found_in}"
    )


# ---------------------------------------------------------------------------
# PCI-G06 — risk_engine.evaluate_proposal() 不依赖 operator.permission_mode (L2c)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PCI-G06",
        "live_unlock_contract",
        "§4b risk_engine 不豁免任何 permission_mode",
        "L2c",
        "evaluate_proposal() 的执行路径不依赖 operator.permission_mode——risk_engine 关卡"
        "在 Auto 模式下与任何其他模式完全相同地运行。risk_engine.py 不得读取"
        " operator.permission_mode 并做 skip/bypass 分支判断。L2c 系统不变式：任意"
        " permission_mode 下，违规 proposal 均返回 approved=False。",
        status="implemented",
    )
)
def test_risk_engine_does_not_read_operator_permission_mode(tmp_path, monkeypatch):
    """双重验证：(1) 源码无 operator.permission_mode 引用；(2) 行为：违规 proposal 在
    auto 和 manual 模式下均返回 approved=False。
    """
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    # --- Part 1: 源码扫描 ---
    src = _src("aletheia/risk/engine.py")
    # engine.py 可读 cfg.agent.permission_mode（Worker 面，用于 Observe 拦截）
    # 但不得读 operator.permission_mode（Operator 面），后者不是风控输入
    assert "operator.permission_mode" not in src, (
        "risk/engine.py 读取了 operator.permission_mode，Auto 模式可能豁免风控关卡 — "
        "违反 live_unlock_contract §4b 不变式"
    )

    # --- Part 2: 行为断言 ---
    # 构造最小可用环境
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    from aletheia.config.schema import AletheiaConfig
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage.models import TradeProposal

    # 构造一个违反 kill_switch 的 proposal（最简单的违规）
    def _make_violating_proposal(execution_mode: str = "paper_internal") -> TradeProposal:
        return TradeProposal(
            symbol="BTC/USDT",
            direction="long",
            quantity=0.001,
            price=1.0,
            leverage=1,
            execution_mode=execution_mode,
            market_type="usdt_futures",
        )

    # 激活 kill_switch（全局拒绝任何 proposal）
    cfg = AletheiaConfig()
    cfg.runtime.kill_switch_active = True

    def _patched_load():
        return cfg

    monkeypatch.setattr("aletheia.risk.engine.load_config", _patched_load)

    for perm_mode in ("Auto", "AllowEditing", "ReadOnly"):
        # 修改 operator permission_mode（Operator 面）
        cfg.operator.permission_mode = perm_mode
        result = evaluate_proposal(_make_violating_proposal())
        assert result.approved is False, (
            f"operator.permission_mode='{perm_mode}' 下，kill_switch 违规 proposal 却返回"
            f" approved=True — risk_engine 不变式被破坏（PCI-G06）"
        )
        assert any("kill_switch" in v for v in result.violations), (
            f"permission_mode='{perm_mode}' 下 kill_switch_active violation 消失 — "
            "risk_engine 可能在某些 permission_mode 下跳过了关卡"
        )


# ---------------------------------------------------------------------------
# PCI-G07 — 低风险写命令不接受 docs/ 路径（PCI-G03 互补断言，L2a, missing）
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PCI-G07",
        "PI_OPERATOR_DESIGN_DECISIONS",
        "§宪法人类专有：低风险写命令路径守卫",
        "L2a",
        "create_research_note、create_dreaming_log、create_daily_review 等低风险写命令的"
        " backend_handler 不接受以 'docs/' 开头的路径参数——PCI-G03 的互补断言，防止 PI 通过"
        "低风险命令绕过 file_artifacts.py 路径守卫直接覆盖宪法文档。",
        status="implemented",
    )
)
def test_low_risk_write_commands_reject_docs_prefix_path():
    """源码扫描：create_research_note/create_dreaming_log/create_daily_review 的 handler
    实现中必须含有 docs/ 前缀拒绝逻辑（assert / raise / if...return error）。

    预期失败（当前）：handlers 中无此守卫 → assert 断言失败 → xfail 正常。
    通过条件：handlers 中加入 docs/ 路径拒绝代码后，此 xfail 变 XPASS → CI 报错提醒升级为常规测试。
    """
    src = _src("aletheia/agent_shell/commands.py")
    # 只需要在文件中存在与 docs/ 路径拒绝相关的字符串即可作为结构证据
    docs_guard_present = (
        "docs/" in src
        and any(
            marker in src
            for marker in (
                "startswith('docs/')",
                'startswith("docs/")',
                "startswith(\"docs/\")",
                "docs/ prefix",
                "reject.*docs",
            )
        )
    )
    assert docs_guard_present, (
        "create_research_note/create_dreaming_log/create_daily_review handlers 中"
        "未找到 docs/ 前缀路径拒绝逻辑——PI 可能绕过 file_artifacts.py 守卫覆写宪法文档。"
        "请在 handler 中加入 if path.startswith('docs/'): return {'error': ...} 守卫。"
    )


@spec_claim(
    SpecClaim("PI-S05", "execution_contract", "§entry discipline by execution mode", "L2b",
              "Local paper entry uses an immediate market fill from a fresh exact mark; "
              "the autonomous wrapper and paper broker reject local limit entries. "
              "Exchange demo callers may still request a planned native limit entry, whose "
              "four entry fields remain protected by Risk."),
)
def test_entry_discipline_market_default_local_limit_rejection_and_native_gate(monkeypatch):
    """行为断言(非源码 grep):

    1. create_paper_proposal 默认下单类型 = market。
    2. paper_internal 显式 limit 在 wrapper 和 broker 两层都拒绝且无副作用。
    3. 风控对 exchange native limit 入场缺任一要素(入场价/止损/止盈/盈亏比)→ 拒单
       (incomplete_entry_plan)。
    4. 四要素齐全的 exchange native limit 入场 → 不再触发 incomplete_entry_plan。
    """
    import inspect
    from aletheia.execution import paper_tools
    from aletheia.storage.models import TradeProposal
    from aletheia.risk.engine import evaluate_proposal

    # 1. local paper is an immediate simulated fill, not a fake maker promise.
    sig = inspect.signature(paper_tools.create_paper_proposal)
    assert sig.parameters["order_type"].default == "market"

    monkeypatch.setattr(
        paper_tools, "submit_proposal", lambda **kwargs: kwargs,
    )
    with pytest.raises(ValueError, match="paper_internal_market_only"):
        paper_tools.create_paper_proposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=0.001, price=50000.0, execution_mode="paper_internal",
            order_type="limit",
        )

    common = dict(symbol="BTC/USDT", market_type="spot", direction="long",
                  quantity=0.001, price=50000.0, execution_mode="bitget_demo")

    # 2. native limit 缺 take_profit + risk_reward → incomplete_entry_plan
    incomplete = TradeProposal(order_type="limit", source="native",
                               stop_price=49000.0, **common)
    res = evaluate_proposal(incomplete)
    assert any("incomplete_entry_plan" in v for v in res.violations), (
        f"native limit 入场缺四要素应被拒,violations={res.violations}"
    )

    # 3. 四要素齐全 → 不再有 incomplete_entry_plan(其它无关违规不影响本断言)
    complete = TradeProposal(order_type="limit", source="native",
                             stop_price=49000.0, take_profit=52000.0,
                             risk_reward=2.0, **common)
    res2 = evaluate_proposal(complete)
    assert not any("incomplete_entry_plan" in v for v in res2.violations), (
        f"四要素齐全不应触发 incomplete_entry_plan,violations={res2.violations}"
    )

    # market entries/closes are outside the exchange-native limit-plan gate.
    market_close = TradeProposal(order_type="market", source="native",
                                 stop_price=None, **common)
    res3 = evaluate_proposal(market_close)
    assert not any("incomplete_entry_plan" in v for v in res3.violations), (
        f"market 单不应触发入场计划门槛,violations={res3.violations}"
    )


@spec_claim(
    SpecClaim(
        "PI-S06",
        "agent_contracts",
        "§System Operator daemon evaluation health",
        "L2b",
        "Each daemon evaluation timeout or exception increments the existing "
        "pi_trading_decision failure health and returns a retry sentinel; only a real "
        "subsequent evaluation success clears the consecutive failure count.",
        status="implemented",
    ),
)
def test_daemon_evaluation_failures_count_and_real_success_recovers(monkeypatch):
    import asyncio

    from aletheia.agent_runtime import pi_daemon, scheduler
    from aletheia.agent_shell import commands
    from aletheia.infra import errors as infra_errors
    from aletheia.infra import events as infra_events
    from aletheia.learning import control

    learning_failures: list[tuple[str, str]] = []
    emitted: list[dict] = []
    monkeypatch.setattr(
        control,
        "record_learning_job_failure",
        lambda job_id, error: learning_failures.append((job_id, error)),
    )
    monkeypatch.setattr(
        infra_events, "emit_infra_event", lambda **kwargs: emitted.append(kwargs)
    )
    monkeypatch.setattr(infra_errors, "record_error", lambda **_kwargs: None)

    failure_cases = (
        (asyncio.TimeoutError, "daemon_evaluation_timeout"),
        (RuntimeError, "daemon_evaluation_failed"),
    )
    for error_type, expected_code in failure_cases:
        scheduler._consecutive_failures.clear()
        learning_failures.clear()
        emitted.clear()

        async def fail_evaluation(_params, _error_type=error_type):
            raise _error_type("forced daemon failure")

        monkeypatch.setattr(
            commands, "evaluate_trading_opportunity", fail_evaluation
        )
        retry_results = [
            asyncio.run(pi_daemon._run_evaluation("signal")) for _ in range(3)
        ]

        assert retry_results == [pi_daemon._EVALUATION_RETRY_PENDING] * 3
        assert scheduler._consecutive_failures["pi_trading_decision"] == 3
        assert learning_failures == [
            ("pi_trading_decision", expected_code),
        ] * 3
        assert any(
            row.get("code") == "job_consecutive_failure:pi_trading_decision"
            for row in emitted
        )

        async def complete_evaluation(_params):
            return {"action": "skip", "pending_queue_remaining": 0}

        monkeypatch.setattr(
            commands, "evaluate_trading_opportunity", complete_evaluation
        )
        assert asyncio.run(pi_daemon._run_evaluation("signal")) == 0
        assert scheduler._consecutive_failures["pi_trading_decision"] == 0

    if hasattr(scheduler._JOB_RUN_STATE, "failure_recorded"):
        del scheduler._JOB_RUN_STATE.failure_recorded


@spec_claim(
    SpecClaim(
        "PI-S07",
        "agent_contracts",
        "§System Operator heartbeat health",
        "L2b",
        "The scheduled daemon heartbeat uses a delivery-only health id, so a successful "
        "notify cannot clear pi_trading_decision evaluation failures; failed delivery is "
        "recorded independently as daemon_notify_failed and immediately falls back to an "
        "inline evaluation.",
        status="implemented",
    ),
)
def test_daemon_heartbeat_health_is_separate_from_evaluation(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import pi_daemon, scheduler
    from aletheia.learning import control

    src = _src("aletheia/agent_runtime/scheduler.py")
    assert '_guarded("pi_trading_heartbeat", _run_pi_trading_decision)' in src

    monkeypatch.setattr(control, "is_autonomous_learning_job", lambda _job: False)
    monkeypatch.setattr(control, "record_learning_job_success", lambda _job: None)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False),
            agent=SimpleNamespace(permission_mode="Paper"),
            pi_daemon=SimpleNamespace(enabled=True),
        ),
    )
    monkeypatch.setattr(pi_daemon, "is_daemon_running", lambda: True)
    monkeypatch.setattr(pi_daemon, "notify_pi_daemon", lambda **_kwargs: True)

    scheduler._consecutive_failures.clear()
    scheduler._consecutive_failures["pi_trading_decision"] = 2
    scheduler._run_job_guarded(
        "pi_trading_heartbeat", scheduler._run_pi_trading_decision
    )

    assert scheduler._consecutive_failures["pi_trading_decision"] == 2
    assert scheduler._consecutive_failures["pi_trading_heartbeat"] == 0

    monkeypatch.setattr(pi_daemon, "notify_pi_daemon", lambda **_kwargs: False)
    inline_calls: list[str] = []

    def complete_inline(job_id, awaitable):
        inline_calls.append(job_id)
        awaitable.close()
        return {"action": "skip", "pending_queue_remaining": 0}

    monkeypatch.setattr(scheduler, "_run_async_with_timeout", complete_inline)
    monkeypatch.setattr(
        control, "record_learning_job_failure", lambda _job, _error: None
    )
    scheduler._run_pi_trading_decision()
    assert inline_calls == ["pi_trading_decision"]
    assert scheduler._consecutive_failures["pi_trading_decision"] == 0
    assert scheduler._consecutive_failures["pi_trading_heartbeat"] == 1

    if hasattr(scheduler._JOB_RUN_STATE, "failure_recorded"):
        del scheduler._JOB_RUN_STATE.failure_recorded


@spec_claim(
    SpecClaim(
        "PI-S08",
        "agent_contracts",
        "§PI three-layer context injection",
        "L2b",
        "evaluate_trading_opportunity feeds all three context layers into the LLM prompt "
        "before the model call; Layer 1 (environment snapshot), Layer 2 (short-term memory), "
        "and Layer 3 (long-term wisdom) headers and their payloads must appear in the captured "
        "user message sent to llm_structured.",
        status="implemented",
    ),
)
async def test_three_layer_context_injected_into_llm_prompt(monkeypatch):
    """PI §B: _assess_opportunity must forward all three layer section headers to llm_structured."""
    import aletheia.agents.helpers as helpers
    import aletheia.model_provider.router as router
    from aletheia.agent_shell.trading_decision import _assess_opportunity

    captured: list[str] = []

    async def _fake_llm(system, user, *, schema, provider=None, **_kw):
        captured.append(user)
        return {
            "action": "skip",
            "symbol": None,
            "direction": None,
            "conviction": 0,
            "reasoning": "fixture skip",
            "skip_kind": "no_pending_signal",
            "risk_factors": [],
            "supporting_signals": [],
            "opposing_signals": [],
            "bear_case": "",
            "key_assumptions": [],
            "parameter_rationale": {},
        }, None

    monkeypatch.setattr(helpers, "llm_structured", _fake_llm)
    monkeypatch.setattr(router, "get_provider_for_tier", lambda _tier: "mock_provider")

    ctx = {
        "layer1": {
            "positions": [],
            "balance": {"equity_usdt": 1000.0, "available_usdt": 950.0},
            "today_pnl": 0.0,
            "regime": {
                "state": "neutral",
                "confidence": 0.6,
                "long_bias": 0.5,
                "short_bias": 0.5,
            },
            "features": [],
            "risk": {
                "kill_switch": False,
                "max_daily_loss_pct": 5,
                "max_open_positions": 3,
            },
            "data_health": {},
            "opportunities": [],
        },
        "layer2": {
            "reflection": None,
            "outcome_stats": None,
            "per_symbol": [],
            "strategies": [],
            "pending_signals": [],
            "pending_queue": {"unresolved_total": 1, "overflow_count": 0},
        },
        "layer3": {
            "lessons": [
                {
                    "type": "general",
                    "symbol": None,
                    "content": "L3_SENTINEL_WISDOM: follow the trend",
                }
            ],
            "guard_candidates": [],
            "serenity_opportunities": [],
        },
        "skip_policy": {"unchanged_context_defer_window_minutes": 120},
    }

    result = await _assess_opportunity(ctx)

    assert captured, "_assess_opportunity never called llm_structured"
    user_msg = captured[0]

    assert "# Layer 1: Environment Snapshot" in user_msg, (
        "Layer 1 header missing from LLM prompt — three-layer context injection broken"
    )
    assert "# Layer 2: Short-term Memory" in user_msg, (
        "Layer 2 header missing from LLM prompt — three-layer context injection broken"
    )
    assert "# Layer 3: Long-term Wisdom" in user_msg, (
        "Layer 3 header missing from LLM prompt — three-layer context injection broken"
    )
    assert "L3_SENTINEL_WISDOM" in user_msg, (
        "Layer 3 payload missing from LLM prompt — lesson content not rendered into prompt"
    )


@pytest.mark.asyncio
async def test_operator_structured_calls_disable_provider_reasoning(monkeypatch):
    """Operator callers leave JSON schema rendering to the shared helper."""
    import aletheia.agents.helpers as helpers
    import aletheia.model_provider.router as router
    from aletheia.agent_shell import trading_decision

    captured: dict[str, tuple[str, dict, str | None]] = {}

    async def _fake_llm(
        _system,
        user,
        *,
        schema,
        caller,
        reasoning_effort=None,
        **_kwargs,
    ):
        captured[caller] = (user, schema.model_json_schema(), reasoning_effort)
        if caller == "system_operator.debate_bull":
            return {"confidence": 60, "thesis": "bull", "evidence": []}, None
        if caller == "system_operator.debate_bear":
            return {"confidence": 40, "thesis": "bear", "evidence": []}, None
        return {"action": "skip"}, None

    monkeypatch.setattr(helpers, "llm_structured", _fake_llm)
    monkeypatch.setattr(router, "get_provider_for_tier", lambda _tier: "mock_provider")
    monkeypatch.setattr(
        trading_decision, "_build_assessment_prompt", lambda _ctx: "assessment"
    )
    monkeypatch.setattr(
        trading_decision, "_build_bull_prompt", lambda *_args: "bull"
    )
    monkeypatch.setattr(
        trading_decision, "_build_bear_prompt", lambda *_args: "bear"
    )

    await trading_decision._assess_opportunity({})
    await trading_decision._reassess_direct_skip(
        {}, {"action": "skip"}, "counterfactual_confirmation_required"
    )
    await trading_decision._run_debate("BTC/USDT", "long", {})

    expected = {
        "system_operator.assess_opportunity": trading_decision._DECISION_SCHEMA,
        "system_operator.reassess_direct_skip": trading_decision._DECISION_SCHEMA,
        "system_operator.debate_bull": trading_decision._BULL_SCHEMA,
        "system_operator.debate_bear": trading_decision._BEAR_SCHEMA,
    }
    assert set(captured) == set(expected)
    for caller, schema_dict in expected.items():
        user, received_schema, reasoning_effort = captured[caller]
        assert received_schema == schema_dict
        assert json.dumps(schema_dict) not in user
        assert reasoning_effort == "none"


@spec_claim(
    SpecClaim(
        "PI-S09",
        "agent_contracts",
        "§entry plan geometry correction",
        "L2b",
        "Geometrically-inverted stop/tp values from the LLM (e.g. short with tp > entry, "
        "or long with stop > entry) are silently replaced by config-derived defaults before "
        "the proposal reaches the Risk Engine. The risk engine's geometry backstop is not "
        "triggered for these proposals. stop_source reflects 'default' for corrected stops.",
        status="implemented",
    ),
)
def test_inverted_stop_tp_replaced_by_config_defaults(tmp_path, monkeypatch):
    """Short with tp=110 and stop=80 at price=100 (both inverted) must be corrected
    to config-default values before the Risk Engine, and stop_source must be 'default'."""
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs",):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()

    from aletheia.agent_shell.trading_decision import TradingDecision, prepare_decision_as_proposal
    import aletheia.agent_shell.trading_decision as td_module
    import aletheia.trust.guard_chain as gc

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )
    monkeypatch.setattr(
        td_module, "_latest_exact_entry_snapshot", lambda *a: None
    )
    monkeypatch.setattr(gc, "run_guards", lambda ctx: [])
    monkeypatch.setattr(gc, "summarize", lambda verdicts: {"blocks": [], "details": ""})

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="short",
        conviction=70,
        reasoning="test",
        bear_case="test bear",
        stop_price=80.0,
        take_profit=110.0,
        parameter_rationale={
            "entry_price": "test", "stop_loss": "test",
            "take_profit": "test", "notional_usdt": "test", "leverage": "test",
        },
    )

    import asyncio
    result = asyncio.new_event_loop().run_until_complete(
        prepare_decision_as_proposal(decision, "paper_internal")
    )

    proposal = result["proposal"]
    assert proposal.stop_price > 100.0, (
        f"short stop must be > entry after correction, got stop={proposal.stop_price}"
    )
    assert proposal.take_profit < 100.0, (
        f"short tp must be < entry after correction, got tp={proposal.take_profit}"
    )
    assert proposal.stop_source == "default", (
        f"stop_source must be 'default' when LLM stop was discarded, got {proposal.stop_source!r}"
    )
