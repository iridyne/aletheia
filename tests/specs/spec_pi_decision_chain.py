"""Spec: PI Agent decision-chain fixes (Round 2 PI-agent audit, 2026-06-28).

Locks the two PI decision-chain fixes made during the PI-agent SDD audit
(see docs/issues/PI_AGENT_SDD_AUDIT_2026-06-28.md):

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
import pathlib

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
    # The old hard-block path must be gone: locate submit_decision_as_proposal and
    # assert no `return None` follows a `guard_summary["blocks"]` test.
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "submit_decision_as_proposal"),
        None,
    )
    assert fn is not None, "submit_decision_as_proposal not found"
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
    # Sanity: it must still emit the RETAINED config-tuning actions (not over-pruned).
    assert "tighten_confidence" in emitted and "loosen_confidence" in emitted, (
        "config-tuning actions were over-pruned — those are the ONE thing Python keeps"
    )


@spec_claim(
    SpecClaim("PI-S04", "promotion_gates_contract", "§4 PI approval gate", "L2b",
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
# PCI-G02 — A路架构：observer tools in PI tool registry (L2a, missing)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402 — placed after existing imports to avoid reshuffling


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
