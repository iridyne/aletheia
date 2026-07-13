"""Spec: Operator Loop Contract (operator_layer.md + PI_OPERATOR_DESIGN_DECISIONS.md)

Executable claims covering the PI Operator Loop permission model,
rate limits, PARAM_REGISTRY, and forbidden actions.

Contract authority:
  docs/design/operator_layer.md
  docs/design/PI_OPERATOR_DESIGN_DECISIONS.md
"""

import ast
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_OPERATOR_LOOP_SRC = (
    Path(__file__).parent.parent.parent
    / "aletheia" / "agent_shell" / "operator_loop.py"
)
_SCHEDULER_SRC = (
    Path(__file__).parent.parent.parent
    / "aletheia" / "agent_runtime" / "scheduler.py"
)

# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-S01", "operator_layer", "§permission_model level_sets", "L2a",
        "_LEVEL_1_ACTIONS, _LEVEL_2_ACTIONS, _LEVEL_3_ACTIONS sets exist in operator_loop.py",
    ),
)
def test_level_action_sets_exist():
    """operator_loop.py must export the three permission-level sets."""
    import aletheia.agent_shell.operator_loop as ol

    assert hasattr(ol, "_LEVEL_1_ACTIONS"), "_LEVEL_1_ACTIONS missing"
    assert hasattr(ol, "_LEVEL_2_ACTIONS"), "_LEVEL_2_ACTIONS missing"
    assert hasattr(ol, "_LEVEL_3_ACTIONS"), "_LEVEL_3_ACTIONS missing"

    assert isinstance(ol._LEVEL_1_ACTIONS, (set, frozenset)), "_LEVEL_1_ACTIONS must be a set"
    assert isinstance(ol._LEVEL_2_ACTIONS, (set, frozenset)), "_LEVEL_2_ACTIONS must be a set"
    assert isinstance(ol._LEVEL_3_ACTIONS, (set, frozenset)), "_LEVEL_3_ACTIONS must be a set"

    # Sanity: each set non-empty
    assert len(ol._LEVEL_1_ACTIONS) > 0
    assert len(ol._LEVEL_2_ACTIONS) > 0
    assert len(ol._LEVEL_3_ACTIONS) > 0


@spec_claim(
    SpecClaim(
        "OL-S02", "operator_layer", "§param_registry keys", "L2a",
        "PARAM_REGISTRY dict exists with required keys: min_confidence_to_trade, "
        "default_stop_loss_pct, max_demo_position_notional_usdt",
    ),
)
def test_param_registry_required_keys():
    """PARAM_REGISTRY must contain at minimum the three tunable params from the design."""
    import aletheia.agent_shell.operator_loop as ol

    assert hasattr(ol, "PARAM_REGISTRY"), "PARAM_REGISTRY missing from operator_loop"
    assert isinstance(ol.PARAM_REGISTRY, dict), "PARAM_REGISTRY must be a dict"

    required_keys = {
        "min_confidence_to_trade",
        "default_stop_loss_pct",
        "max_demo_position_notional_usdt",
    }
    missing = required_keys - set(ol.PARAM_REGISTRY.keys())
    assert not missing, f"PARAM_REGISTRY missing required keys: {missing}"


@spec_claim(
    SpecClaim(
        "OL-S06", "operator_layer", "§scheduler cron", "L2a",
        "scheduler.py registers pi_operator_loop job with CronTrigger "
        "at hour='0,4,8,12,16,20', minute=15",
    ),
)
def test_scheduler_operator_loop_cron():
    """Read scheduler.py source and verify the pi_operator_loop cron arguments."""
    src = _SCHEDULER_SRC.read_text(encoding="utf-8")

    assert "pi_operator_loop" in src, "pi_operator_loop job not found in scheduler.py"

    tree = ast.parse(src)
    cron_jobs: list[dict] = []

    for node in ast.walk(tree):
        # Look for add_job(..., "cron", ..., id="pi_operator_loop", ...)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_job"):
            continue
        # Collect keyword arguments
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        id_node = kwargs.get("id")
        if id_node is None:
            continue
        if not (isinstance(id_node, ast.Constant) and id_node.value == "pi_operator_loop"):
            continue
        # Confirmed this is the pi_operator_loop add_job call
        hour_node = kwargs.get("hour")
        minute_node = kwargs.get("minute")
        hour_val = hour_node.value if isinstance(hour_node, ast.Constant) else None
        minute_val = minute_node.value if isinstance(minute_node, ast.Constant) else None
        cron_jobs.append({"hour": hour_val, "minute": minute_val})

    assert cron_jobs, "No add_job(id='pi_operator_loop') call found in scheduler.py"
    job = cron_jobs[0]
    assert job["hour"] == "0,4,8,12,16,20", (
        f"pi_operator_loop hour expected '0,4,8,12,16,20', got {job['hour']!r}"
    )
    assert job["minute"] == 15, (
        f"pi_operator_loop minute expected 15, got {job['minute']!r}"
    )


@spec_claim(
    SpecClaim(
        "OL-S07", "operator_layer", "§scheduler startup continuity", "L2a",
        "pi_operator_loop keeps its four-hour cron and receives a startup "
        "next_run_time within two minutes, guarded by persisted assessment freshness.",
        status="implemented",
    ),
)
def test_scheduler_operator_loop_has_deduplicated_startup_catchup():
    src = _SCHEDULER_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = kwargs.get("id")
        if isinstance(job_id, ast.Constant) and job_id.value == "pi_operator_loop":
            target = node
            break

    assert target is not None
    kwargs = {kw.arg: kw.value for kw in target.keywords if kw.arg}
    rendered = ast.unparse(kwargs["next_run_time"])
    assert "datetime.now()" in rendered and "timedelta" in rendered
    assert "minutes=2" in rendered.replace(" ", "")
    assert "_operator_assessment_due" in src


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-B05", "operator_layer", "§rate_limits config_patches", "L2b",
        "_MAX_CONFIG_PATCHES_PER_DAY constant <= 3",
    ),
)
def test_max_config_patches_per_day():
    """Rate-limit for daily config patches must be conservative (<= 3)."""
    import aletheia.agent_shell.operator_loop as ol

    assert hasattr(ol, "_MAX_CONFIG_PATCHES_PER_DAY"), "_MAX_CONFIG_PATCHES_PER_DAY missing"
    limit = ol._MAX_CONFIG_PATCHES_PER_DAY
    assert isinstance(limit, int), "_MAX_CONFIG_PATCHES_PER_DAY must be int"
    assert limit <= 3, (
        f"_MAX_CONFIG_PATCHES_PER_DAY={limit} exceeds the safe ceiling of 3; "
        "raising this value risks runaway autonomous config changes"
    )


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-C03", "operator_layer", "§defense_in_depth level3_coverage", "L2c",
        "Every _LEVEL_3_ACTIONS member is in FORBIDDEN_INTENTS or maps to a "
        "RESTRICTED_TOOLS entry — defense-in-depth double-lock",
        status="implemented",
    ),
)
def test_level3_defense_in_depth():
    """Each Level 3 action must be covered by at least one of:
      - FORBIDDEN_INTENTS (blocks via check_permission)
      - RESTRICTED_TOOLS  (blocks at tool-registry level)

    The mapping accounts for naming differences between operator action names
    and the intent/tool names used in the registry.
    """
    import aletheia.agent_shell.operator_loop as ol
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS

    restricted_set = set(RESTRICTED_TOOLS)

    # Canonical mapping: L3 action name → (forbidden_intent, restricted_tool)
    # Either entry being present counts as covered.
    coverage_map: dict[str, tuple[str | None, str | None]] = {
        "deactivate_kill_switch":  ("deactivate_kill_switch", None),  # now in FORBIDDEN_INTENTS
        "enable_live_mode":        ("enable_live", "unlock_live"),
        "modify_risk_limits":      ("change_risk_limits", "change_hard_risk_limits"),
        "modify_credentials":      ("modify_credentials", "modify_credentials"),
        "modify_permissions":      ("modify_permissions", None),      # now in FORBIDDEN_INTENTS
    }

    uncovered: list[str] = []
    for action in ol._LEVEL_3_ACTIONS:
        fi, rt = coverage_map.get(action, (None, None))
        covered_by_fi = fi is not None and fi in FORBIDDEN_INTENTS
        covered_by_rt = rt is not None and rt in restricted_set
        if not (covered_by_fi or covered_by_rt):
            uncovered.append(action)

    assert not uncovered, (
        f"Level 3 actions without defense-in-depth coverage: {uncovered}. "
        "Each L3 action must appear in FORBIDDEN_INTENTS or RESTRICTED_TOOLS. "
        "Add the missing entries to registry.py or tool_registry.py."
    )


@spec_claim(
    SpecClaim(
        "OL-C04", "operator_layer", "§permission_model disjoint_sets", "L2c",
        "No action appears in more than one level set — _LEVEL_1, _LEVEL_2, _LEVEL_3 are disjoint",
    ),
)
def test_level_sets_are_disjoint():
    """Actions must belong to exactly one level; overlap is a safety invariant violation."""
    import aletheia.agent_shell.operator_loop as ol

    l1 = ol._LEVEL_1_ACTIONS
    l2 = ol._LEVEL_2_ACTIONS
    l3 = ol._LEVEL_3_ACTIONS

    l1_l2 = l1 & l2
    l1_l3 = l1 & l3
    l2_l3 = l2 & l3

    assert not l1_l2, f"INVARIANT VIOLATION: actions in both L1 and L2: {l1_l2}"
    assert not l1_l3, f"INVARIANT VIOLATION: actions in both L1 and L3: {l1_l3}"
    assert not l2_l3, f"INVARIANT VIOLATION: actions in both L2 and L3: {l2_l3}"


@spec_claim(
    SpecClaim(
        "OL-C07", "operator_layer", "§permission_model bounded_confidence_recovery", "L2c",
        "loosen_confidence is a Level 1 drought-liveness exception bounded at neutral; "
        "increase_sizing/widen_stop_loss remain Level 2 human proposals",
        status="implemented",
    ),
)
def test_bounded_confidence_recovery_is_the_only_level1_loosen_exception():
    """Confidence may auto-restore only to neutral; other loosening stays L2."""
    import aletheia.agent_shell.operator_loop as ol

    assert "loosen_confidence" in ol._LEVEL_1_ACTIONS
    assert "loosen_confidence" not in ol._LEVEL_2_ACTIONS

    proposal_only_actions = {"increase_sizing", "widen_stop_loss"}
    missing_from_l2 = proposal_only_actions - ol._LEVEL_2_ACTIONS
    assert not missing_from_l2, (
        f"Loosening actions missing from _LEVEL_2_ACTIONS: {missing_from_l2}"
    )

    # The full invariant: they must NOT be reachable via _execute_level1_action.
    # Check by inspecting the AST of _execute_level1_action for elif branches matching
    # these action names — the legacy branches violate the Level 2 boundary.
    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    reachable_in_l1_exec: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_execute_level1_action"):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Compare):
                continue
            # Look for: action.action_type == "<name>"
            left = child.left
            if not (isinstance(left, ast.Attribute) and left.attr == "action_type"):
                continue
            for comp in child.comparators:
                if isinstance(comp, ast.Constant) and comp.value in (
                    proposal_only_actions | {"loosen_confidence"}
                ):
                    reachable_in_l1_exec.add(comp.value)

    assert "loosen_confidence" in reachable_in_l1_exec
    assert not (reachable_in_l1_exec & proposal_only_actions), (
        "Level 2 proposal-only loosening actions became reachable in the Level 1 executor"
    )

    spec = ol.PARAM_REGISTRY["min_confidence_to_trade"]
    assert spec.neutral == "medium"
    assert spec.ladder == ["speculative", "medium", "high"]


# ---------------------------------------------------------------------------
# Content-health self-supervision (2026-06-28) — the manager ingests doctor's
# content.* diagnostics and surfaces them as Level-1 notify only (no auto-fix).
# Contract: operator_self_supervision.md + PI_OPERATOR_DESIGN_DECISIONS.md §problem4#1
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-S10", "operator_layer", "§health_monitor content ingestion", "L2a",
        "SystemAssessment exposes a content_health field and doctor.build_content_report() "
        "exists, so the Operator can ingest content-quality diagnostics into its assessment.",
    ),
)
def test_content_health_ingestion_surface_exists():
    import dataclasses
    import aletheia.agent_shell.operator_loop as ol
    from aletheia.diagnostics import doctor as dx

    fields = {f.name for f in dataclasses.fields(ol.SystemAssessment)}
    assert "content_health" in fields, "SystemAssessment missing content_health field"

    assert hasattr(dx, "build_content_report"), "doctor.build_content_report missing"
    report = dx.build_content_report()  # pure read; must run without raising
    assert hasattr(report, "checks")
    # build_content_report runs ONLY content probes — never the operational ones.
    names = {c.name for c in report.checks}
    assert all(n.startswith("content.") for n in names), \
        f"build_content_report leaked non-content probes: {names - {n for n in names if n.startswith('content.')}}"


@spec_claim(
    SpecClaim(
        "OL-S11", "operator_layer", "§permission_model content response is Level 1", "L2a",
        "decide_actions responds to content_health with a Level-1 notify_operator ONLY — "
        "it never emits a Level 2/3 action from content anomalies (manager flags, never auto-fixes).",
    ),
)
def test_content_health_response_is_level1_notify_only():
    import ast
    import aletheia.agent_shell.operator_loop as ol

    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    decide = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "decide_actions"),
        None,
    )
    assert decide is not None, "decide_actions not found"

    # Find the `if assessment.content_health:` block and collect the OperatorAction
    # action_type / level literals constructed inside it.
    forbidden = ol._LEVEL_2_ACTIONS | ol._LEVEL_3_ACTIONS
    found_notify_level1 = False
    for node in ast.walk(decide):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Attribute)
                and node.test.attr == "content_health"):
            continue
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "OperatorAction"):
                continue
            kw = {k.arg: k.value for k in call.keywords}
            atype = kw.get("action_type")
            level = kw.get("level")
            if isinstance(atype, ast.Constant):
                assert atype.value not in forbidden, \
                    f"content_health branch emits a Level 2/3 action: {atype.value}"
                if atype.value == "notify_operator" and isinstance(level, ast.Constant):
                    assert level.value == 1, "content notify must be Level 1"
                    found_notify_level1 = True
    assert found_notify_level1, \
        "content_health branch must emit a Level-1 notify_operator action"


# ---------------------------------------------------------------------------
# OL-S12 — two-loop separation: supervision loop owns the recommend_* namespace
# in the shared pi_decisions bus (PI_OPERATOR_DESIGN_DECISIONS §问题2, INV-OPS-1)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-S12", "operator_layer", "§问题2 pi_decisions action namespace", "L2c",
        "Supervision loop (operator_loop) writes ONLY recommend_* actions into the shared "
        "pi_decisions bus — never trading-env actions (signal_pending/trade/close/reduce). "
        "The two control loops are disambiguated by the action namespace, NOT signal_source "
        "(INV-OPS-1). Structural root-fix for the 'three components, don't conflate' problem.",
        status="implemented",
    ),
)
def test_operator_loop_writes_only_recommend_actions():
    """AST-scan operator_loop.py for PiDecision(action="...") literals. The supervision
    loop must only emit recommend_* (e.g. recommend_retire/recommend_demote); writing a
    trading-env action here would re-entangle the two control loops."""
    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    trading_actions = {"signal_pending", "trade", "close", "reduce", "scale_in", "flip", "skip"}
    written: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "PiDecision"):
            continue
        for kw in node.keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                written.add(kw.value.value)

    assert written, "No PiDecision(action=...) literal found in operator_loop.py"
    leaked = written & trading_actions
    assert not leaked, (
        f"operator_loop (supervision loop) writes trading-env action(s) {leaked} into "
        "pi_decisions — it must only write recommend_* (INV-OPS-1). Trading actions belong "
        "to the trading loop (paper_trader / PI), never the supervision loop."
    )
    assert all(a.startswith("recommend_") for a in written), (
        "operator_loop wrote non-recommend action(s): "
        f"{ {a for a in written if not a.startswith('recommend_')} }"
    )


# ---------------------------------------------------------------------------
# PI Constitution Invariants (PCI-G01~G04) — PI_OPERATOR_DESIGN_DECISIONS.md
# "宪法专有" section: what PI can never do autonomously.
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PCI-G01", "operator_layer",
        "§pi_constitution modify_own_prompt blocked",
        "L2c",
        "FORBIDDEN_INTENTS contains 'modify_own_prompt' — PI can never overwrite its "
        "own system prompt base layer, even via low-risk commands. "
        "Contract: PI_OPERATOR_DESIGN_DECISIONS §产品定义 (2026-06-30).",
        status="implemented",
    ),
)
def test_modify_own_prompt_is_forbidden():
    """modify_own_prompt must be in FORBIDDEN_INTENTS (hard block, not just command gate).

    propose_prompt_change is proposal_only (human reviews), but that command gate
    only applies to named commands. A low-risk command (create_research_note, etc.)
    that assembles a prompt-overwrite write operation bypasses command validation.
    FORBIDDEN_INTENTS is the last hard wall.
    """
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    assert "modify_own_prompt" in FORBIDDEN_INTENTS, (
        "PCI-G01 VIOLATED: 'modify_own_prompt' missing from FORBIDDEN_INTENTS. "
        "PI could overwrite its own system prompt base layer via low-risk commands. "
        "Add 'modify_own_prompt' to FORBIDDEN_INTENTS in registry.py."
    )


@spec_claim(
    SpecClaim(
        "PCI-G04", "operator_layer",
        "§pi_constitution observers_no_pi_decision",
        "L2a",
        "market_observer.py does not call insert_pi_decision or construct PiDecision() — "
        "observers are tools, not independent signal issuers (A-path architecture). "
        "Contract: PI_OPERATOR_DESIGN_DECISIONS §A路架构 (2026-06-30).",
        status="implemented",
    ),
)
def test_observers_do_not_insert_pi_decisions():
    """AST-scan observer files: none must call insert_pi_decision or construct PiDecision.

    The A-path architecture mandates: lower-layer observers write Opportunity records,
    PI (the top decision-maker) reads them and writes PiDecision. Observers that write
    PiDecision directly re-entangle the two-loop architecture.
    """
    import ast as _ast

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    # chain_observer.py and macro_observer.py are not yet implemented —
    # guard only the files that exist to avoid false failures on missing files.
    _OBSERVER_FILES = [
        _PROJECT_ROOT / "aletheia" / "agents" / "market_observer.py",
    ]
    # Optionally guard additional observers if they exist
    for optional_name in ("chain_observer.py", "macro_observer.py"):
        candidate = _PROJECT_ROOT / "aletheia" / "agents" / optional_name
        if candidate.exists():
            _OBSERVER_FILES.append(candidate)

    forbidden_names = {"insert_pi_decision", "PiDecision"}
    violations: list[str] = []

    for obs_path in _OBSERVER_FILES:
        src = obs_path.read_text(encoding="utf-8")
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            # Function calls: insert_pi_decision(...) or PiDecision(...)
            if isinstance(node, _ast.Call):
                func = node.func
                name = None
                if isinstance(func, _ast.Name):
                    name = func.id
                elif isinstance(func, _ast.Attribute):
                    name = func.attr
                if name in forbidden_names:
                    violations.append(f"{obs_path.name}: {name}()")

    assert not violations, (
        f"PCI-G04 VIOLATED: observer(s) call insert_pi_decision or construct PiDecision — "
        f"observers must only write Opportunity, never PiDecision: {violations}"
    )


# ---------------------------------------------------------------------------
# Self-heal contract: PI approval gate + structured audit + action taxonomy
# (operator_layer.md §[产品定义] 监管/自愈契约, 不变式 1-4)
# 2026-06-30 SDD alignment (GAP-01..07)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-C08", "operator_layer", "§selfheal pi_approval_gate invariant", "L2c",
        "Self-heal L1 actions (clean_stale_heartbeats / restart_collector / "
        "rebuild_sqlite_index) must not execute without a prior PI approval record "
        "— operator_layer.md §不变式1.",
        status="partial",
    ),
)
def test_l1_selfheal_requires_pi_approval():
    """RED tripwire: fires while the PI-approval gate is absent.

    Goes GREEN once _execute_level1_action (or the self-heal stubs) checks
    pi_decisions for an 'approve_l1_action' token before executing
    clean_stale_heartbeats / restart_collector / rebuild_sqlite_index.
    """
    import ast as _ast

    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = _ast.parse(src)

    # Gate function must exist
    assert "_request_pi_approval" in src or "approve_l1" in src, (
        "No PI-approval gate found in operator_loop.py — "
        "_request_pi_approval_for_selfheal() or equivalent must exist "
        "before this tripwire can be satisfied."
    )

    # The gate must be called inside _execute_level1_action or the self-heal handlers
    execute_fn = next(
        (n for n in _ast.walk(tree)
         if isinstance(n, _ast.FunctionDef) and n.name == "_execute_level1_action"),
        None,
    )
    assert execute_fn is not None, "_execute_level1_action not found"
    gate_calls = [
        n for n in _ast.walk(execute_fn)
        if isinstance(n, _ast.Call)
        and isinstance(n.func, _ast.Name)
        and "pi_approval" in n.func.id
    ]
    assert gate_calls, (
        "_execute_level1_action does not call a PI-approval gate before "
        "dispatching self-heal actions."
    )


@spec_claim(
    SpecClaim(
        "OL-C09", "operator_layer", "§selfheal l2_human_confirm invariant", "L2c",
        "L2 proposals must not auto-execute even after PI approval — they must "
        "carry a human_confirm_required marker and wait for explicit human approval "
        "(operator_layer.md §不变式3).",
        status="implemented",
    ),
)
def test_l2_proposal_requires_human_confirmation():
    """RED tripwire: _create_proposal must include a human_confirm_required flag.

    Goes GREEN when the artifact content carries human_confirm_required=True
    and the operator loop respects it before execution.
    """
    import inspect
    import aletheia.agent_shell.operator_loop as _ol

    src = inspect.getsource(_ol._create_proposal)
    assert "human_confirm" in src or "pending_human" in src, (
        "_create_proposal does not mark the proposal as requiring human "
        "confirmation (human_confirm_required). Add this field to the artifact "
        "content to satisfy operator_layer.md §不变式3."
    )


@spec_claim(
    SpecClaim(
        "OL-S13", "operator_layer", "§selfheal l1_action_types exist", "L2a",
        "_LEVEL_1_ACTIONS must include data-maintenance self-heal types: "
        "clean_stale_heartbeats, restart_collector, rebuild_sqlite_index "
        "(operator_layer.md §自愈档位 L1行).",
        status="partial",
    ),
)
def test_l1_selfheal_action_types_exist():
    """The three L1 data-maintenance self-heal action types must be registered."""
    import aletheia.agent_shell.operator_loop as _ol

    required = {"clean_stale_heartbeats", "restart_collector", "rebuild_sqlite_index"}
    missing = required - _ol._LEVEL_1_ACTIONS
    assert not missing, (
        f"Self-heal action types missing from _LEVEL_1_ACTIONS: {missing}. "
        "Add them to align with operator_layer.md §自愈档位 L1."
    )


@spec_claim(
    SpecClaim(
        "OL-B10", "operator_layer", "§selfheal structured_audit fields", "L2b",
        "_emit_self_heal_action must persist all 5 required audit fields in "
        "KnowledgeItem.content (JSON): operation_type / trigger_reason / result "
        "/ before_state / after_state (operator_layer.md §不变式2).",
        status="implemented",
    ),
)
def test_selfheal_emit_stores_structured_fields():
    """_emit_self_heal_action must write all 5 structured fields to KnowledgeItem.content."""
    import json as _json
    from unittest.mock import patch

    captured: list = []

    def fake_insert(item):
        captured.append(item)

    with patch("aletheia.storage.repositories.insert_knowledge_item", side_effect=fake_insert):
        from aletheia.agent_shell import commands as _cmds
        _cmds._emit_self_heal_action(
            action="clean_stale_heartbeats",
            reason="heartbeat table >10k rows",
            operation_type="data_cleanup",
            trigger_reason="heartbeat table >10k rows",
            result="deleted 9823 rows",
            before_state="10021 rows",
            after_state="198 rows",
        )

    assert captured, "_emit_self_heal_action did not call insert_knowledge_item"
    item = captured[0]
    content_raw = item.content
    assert content_raw is not None, (
        "KnowledgeItem.content must not be None for self-heal actions — "
        "it must carry the 5 structured audit fields."
    )
    try:
        content = _json.loads(content_raw)
    except Exception:
        pytest.fail(f"KnowledgeItem.content is not valid JSON: {content_raw!r}")

    required_fields = {"operation_type", "trigger_reason", "result", "before_state", "after_state"}
    missing_fields = required_fields - set(content.keys())
    assert not missing_fields, (
        f"_emit_self_heal_action KnowledgeItem.content missing required fields: {missing_fields}. "
        "All 5 structured audit fields are mandatory per operator_layer.md §不变式2."
    )


@spec_claim(
    SpecClaim(
        "PCI-G03", "operator_layer",
        "§pi_constitution constitution_files_not_in_write_whitelist",
        "L2a",
        "write_operator_report (file_artifacts.py) roots all output under "
        "get_data_dir()/'reports' — never under docs/design/ or docs/governance/. "
        "Constitution files (risk_engine_contract.md, live_unlock_contract.md, "
        "PI_OPERATOR_DESIGN_DECISIONS.md, etc.) are human-owned and must not appear "
        "in any write-command output path. Contract: PI_OPERATOR_DESIGN_DECISIONS "
        "§宪法专有 (2026-06-30).",
        status="implemented",
    ),
)
def test_write_operator_report_root_is_data_dir():
    """write_operator_report must produce paths under data/reports/, never docs/.

    We call report_note_path() (the path-builder, no disk write) and verify the
    returned path does not escape the data directory into the source tree.
    The data dir during tests is a tmp_path; we check that the path is anchored
    under a 'reports' stem and contains no 'docs' segment.
    """
    import ast as _ast

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    fa_src = (
        _PROJECT_ROOT / "aletheia" / "agent_shell" / "file_artifacts.py"
    ).read_text(encoding="utf-8")

    # 1. The path-builder must reference get_data_dir(), not a hardcoded docs/ path.
    assert "get_data_dir()" in fa_src, (
        "file_artifacts.py does not call get_data_dir() — "
        "report paths may escape the data directory"
    )

    # 2. No string literal in file_artifacts.py contains 'docs/design' or 'docs/governance'.
    tree = _ast.parse(fa_src)
    forbidden_paths = ("docs/design", "docs/governance", "docs\\design", "docs\\governance")
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            for fp in forbidden_paths:
                assert fp not in node.value, (
                    f"PCI-G03 VIOLATED: file_artifacts.py contains hardcoded path "
                    f"{node.value!r} pointing to constitution files under docs/. "
                    "All PI write paths must stay under data/reports/."
                )


# ---------------------------------------------------------------------------
# GAP-07: dual audit-path field inconsistency (_persist_action vs _emit_self_heal_action)
# Both paths must carry the 5 required structured fields queryable per contract.
# (operator_layer.md §不变式2, GAP-07 moderate)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-B11", "operator_layer", "§selfheal dual_audit_path consistency", "L2b",
        "_persist_action (AuditEvent) and _emit_self_heal_action (KnowledgeItem) "
        "are the two write paths for self-heal operations. The KnowledgeItem path "
        "must carry all 5 structured fields (§不变式2) in content JSON. "
        "_persist_action carries before_state/after_state via AuditEvent model; "
        "operation_type and trigger_reason are implied by event_type+message. "
        "Claim locks the KnowledgeItem path as the authoritative structured store.",
        status="implemented",
    ),
)
def test_knowledge_item_is_structured_selfheal_store():
    """KnowledgeItem.content (via _emit_self_heal_action) is the structured audit store.

    _persist_action uses AuditEvent (before_state/after_state present but operation_type
    and trigger_reason are embedded in event_type+message — sufficient for operational
    log but not for structured query). The KnowledgeItem path is the contract-mandated
    structured store.

    Verifies:
    1. KnowledgeItem model has a content field (can store JSON).
    2. The self-heal stubs call _emit_self_heal_action (not only _persist_action).
    """
    import ast as _ast

    # 1. KnowledgeItem has content field
    import dataclasses
    from aletheia.storage.models import KnowledgeItem

    # Pydantic model — check field names
    field_names = set(KnowledgeItem.model_fields.keys())
    assert "content" in field_names, (
        "KnowledgeItem model missing 'content' field — cannot store structured JSON audit."
    )

    # 2. Self-heal handler stubs reference _emit_self_heal_action
    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    for action_name in ("_action_clean_stale_heartbeats",
                        "_action_restart_collector",
                        "_action_rebuild_sqlite_index"):
        tree = _ast.parse(src)
        fn = next(
            (n for n in _ast.walk(tree)
             if isinstance(n, _ast.FunctionDef) and n.name == action_name),
            None,
        )
        assert fn is not None, f"{action_name} not found in operator_loop.py"
        calls = [
            n for n in _ast.walk(fn)
            if isinstance(n, _ast.Call)
            and (
                (isinstance(n.func, _ast.Attribute) and n.func.attr == "_emit_self_heal_action")
                or (isinstance(n.func, _ast.Name) and n.func.id == "_emit_self_heal_action")
            )
        ]
        assert calls, (
            f"{action_name} does not call _emit_self_heal_action — "
            "the structured KnowledgeItem audit write is required by §不变式2."
        )


# ---------------------------------------------------------------------------
# Self-heal L2 action taxonomy + PI approval gate + human-confirm marker
# (operator_layer.md [产品定义] §L2人工确认行 + §不变式1/§不变式3)
# SH-S01 / SH-C02 / SH-B03  2026-06-30 SDD alignment
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SH-S01", "operator_layer",
        "§selfheal l2_action_types exist",
        "L2a",
        "_LEVEL_2_ACTIONS 必须包含自愈专属类型 clear_signal_pending、"
        "modify_prompt_fragment、modify_config_fragment——对应产品定义 D6 §L2人工确认行。"
        "三个动作须在集合中注册，operator layer dispatcher 才能路由到正确 handler，"
        "人工确认门控才能接管。",
        status="partial",
    ),
)
def test_l2_selfheal_action_types_registered():
    """_LEVEL_2_ACTIONS must include the three L2 self-heal action types.

    The product definition (operator_layer.md §自愈档位) mandates that
    clear_signal_pending / modify_prompt_fragment / modify_config_fragment
    are L2 (human-confirm required). Without registration in _LEVEL_2_ACTIONS
    the dispatcher cannot route them to _create_proposal, and the human
    confirmation gate is bypassed.
    """
    import aletheia.agent_shell.operator_loop as ol

    required_l2_selfheal = {
        "clear_signal_pending",
        "modify_prompt_fragment",
        "modify_config_fragment",
    }
    missing = required_l2_selfheal - ol._LEVEL_2_ACTIONS
    assert not missing, (
        f"SH-S01 VIOLATED: _LEVEL_2_ACTIONS is missing L2 self-heal action types: {missing}. "
        "Add them to align with operator_layer.md §自愈档位 L2人工确认行."
    )


@spec_claim(
    SpecClaim(
        "SH-C02", "operator_layer",
        "§selfheal pi_approval_gate in _execute_level1_action",
        "L2c",
        "PI 批准前置不变式（实现层）——_execute_level1_action 对 "
        "clean_stale_heartbeats / restart_collector / rebuild_sqlite_index 三个自愈动作，"
        "必须在 dispatch 前调用含 'pi_approval' 或 'approve_l1' 字样的门控函数。"
        "无批准记录则不执行（operator_layer.md §不变式1）。",
        status="partial",
    ),
)
def test_l1_selfheal_pi_approval_gate_in_execute():
    """RED tripwire: _execute_level1_action must call a PI-approval gate before
    dispatching clean_stale_heartbeats / restart_collector / rebuild_sqlite_index.

    Goes GREEN once _execute_level1_action (or the self-heal handler stubs)
    contains a call to a function whose name contains 'pi_approval' or 'approve_l1'.
    """
    import ast as _ast

    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = _ast.parse(src)

    execute_fn = next(
        (n for n in _ast.walk(tree)
         if isinstance(n, _ast.FunctionDef) and n.name == "_execute_level1_action"),
        None,
    )
    assert execute_fn is not None, "_execute_level1_action not found"

    # Gate: any call whose function name contains 'pi_approval' or 'approve_l1'
    gate_calls = [
        n for n in _ast.walk(execute_fn)
        if isinstance(n, _ast.Call)
        and (
            (isinstance(n.func, _ast.Name)
             and ("pi_approval" in n.func.id or "approve_l1" in n.func.id))
            or (isinstance(n.func, _ast.Attribute)
                and ("pi_approval" in n.func.attr or "approve_l1" in n.func.attr))
        )
    ]
    assert gate_calls, (
        "SH-C02 NOT IMPLEMENTED: _execute_level1_action does not call a PI-approval gate "
        "('pi_approval' or 'approve_l1') before dispatching self-heal actions. "
        "Implement the gate per operator_layer.md §不变式1."
    )


@spec_claim(
    SpecClaim(
        "SH-B03", "operator_layer",
        "§selfheal l2_proposal_human_confirm_required field",
        "L2b",
        "L2 proposal artifact 必须携带 human_confirm_required=True 字段：_create_proposal "
        "写入 create_artifact 的 content dict 必须含 human_confirm_required 键且值为 True。"
        "operator_layer.md §不变式3 要求 L2 操作即使 PI 批准也必须等人确认；"
        "该字段是前端/通知路径的识别依据。",
        status="partial",
    ),
)
def test_l2_proposal_artifact_carries_human_confirm_required():
    """RED tripwire: _create_proposal must embed human_confirm_required=True
    in the artifact content dict.

    Goes GREEN when the content dict passed to create_artifact contains
    the key 'human_confirm_required' set to True.
    """
    import inspect
    import aletheia.agent_shell.operator_loop as _ol

    src = inspect.getsource(_ol._create_proposal)
    assert "human_confirm_required" in src, (
        "SH-B03 NOT IMPLEMENTED: _create_proposal does not write 'human_confirm_required' "
        "into the artifact content dict. Add human_confirm_required=True to satisfy "
        "operator_layer.md §不变式3 (L2 operations require human confirmation even after "
        "PI approval)."
    )


# ---------------------------------------------------------------------------
# Round 2 Ranker Signal-Quality Claims (Coriva impl plan 2026-06-30)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-SCORE-NORM", "operator_layer",
        "§ranker confidence_score normalization",
        "L2b",
        "confidence_score 贡献上限为 50 分（经 clip_norm 到 [0,1] 再乘 50）"
        "——防止 0-100 原始值直接主导 regime/factor 信号（Round 1 方向集中根因）。"
        "paper_trader_ranker._compute_trade_score 中 conf_score 直接加分路径必须被替换。",
        status="implemented",
    ),
)
def test_score_norm_confidence_capped():
    """confidence_score=100 时贡献不超过 75 分（clip_norm 后上限 50 + default offsets）。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime
    import unittest.mock as mock

    neutral_regime = MarketRegime(state="sideways", confidence=0.3)
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=neutral_regime):
        high_conf_opp = {"confidence_score": 100, "symbol": "BTC/USDT",
                         "scanner_family": "test"}
        score = _compute_trade_score(high_conf_opp, None, {"status": "watch"}, "high")
    assert score <= 75.0, (
        f"confidence_score=100 贡献超出预期上限 75（实际={score}），"
        "regime/factor 信号将被压制 — 检查 clip_norm 是否正确"
    )


@spec_claim(
    SpecClaim(
        "OL-SIDEWAYS-TREND-TAX", "operator_layer",
        "§ranker sideways regime trend tax",
        "L2b",
        "sideways regime (confidence>0.6) + trend-family scanner → score -= 25。"
        "当前 sideways 分支完全缺失，CTA 文献实证其为趋势跟踪最差 regime。",
        status="implemented",
    ),
)
def test_sideways_trend_tax_applied():
    """sideways + breakout scanner 应比 reversion 低至少 15 分。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime
    import unittest.mock as mock

    sideways = MarketRegime(state="sideways", confidence=0.75)
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=sideways):
        base_opp = {"confidence_score": 50, "symbol": "ETH/USDT", "scanner_family": "breakout"}
        score_trend = _compute_trade_score(base_opp, None, {"status": "watch"}, "medium",
                                           direction="long")
        opp_rev = dict(base_opp, scanner_family="reversion")
        score_rev = _compute_trade_score(opp_rev, None, {"status": "watch"}, "medium",
                                          direction="long")
    assert score_trend < score_rev - 15, (
        f"sideways + breakout({score_trend}) 应比 reversion({score_rev}) 低至少 15 分（trend_tax 未触发）"
    )


@spec_claim(
    SpecClaim(
        "OL-FACTOR-SMOOTH", "operator_layer",
        "§ranker factor IC smooth scoring",
        "L2b",
        "factor retrospective win_rate has no direct score authority. The ranker consumes only "
        "the bounded prospective score_delta emitted by the shared experiment adapter.",
        status="implemented",
    ),
)
def test_factor_scoring_is_continuous():
    """Unapproved history is neutral; authorized bounded deltas remain continuous."""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime
    import unittest.mock as mock

    flat = MarketRegime(state="sideways", confidence=0.1)
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=flat):
        base = {"confidence_score": 50, "symbol": "BTC/USDT", "scanner_family": "test"}
        sig_low  = {"status": "active", "win_rate": 0.52, "trade_count": 10,
                    "authority_applied": False, "score_delta": 99.0}
        sig_high = {"status": "active", "win_rate": 0.60, "trade_count": 10,
                    "authority_applied": False, "score_delta": -99.0}
        s_low  = _compute_trade_score(base, None, sig_low, "medium", direction="long")
        s_high = _compute_trade_score(base, None, sig_high, "medium", direction="long")
        assert s_low == s_high
        authorized_low = {"authority_applied": True, "score_delta": 1.0}
        authorized_high = {"authority_applied": True, "score_delta": 4.0}
        a_low = _compute_trade_score(base, None, authorized_low, "medium", direction="long")
        a_high = _compute_trade_score(base, None, authorized_high, "medium", direction="long")
    assert a_high - a_low == 3.0


@spec_claim(
    SpecClaim(
        "OL-REGIME-SMOOTH", "operator_layer",
        "§ranker regime confidence smooth weighting",
        "L2b",
        "regime 加分乘以 confidence^1.5（连续），消除 >0.5 断崖。"
        "confidence=0.49 vs 0.51 的分差应 < 5（原差距 ±20）。",
        status="implemented",
    ),
)
def test_regime_scoring_no_cliff():
    """regime.confidence=0.49 vs 0.51 分差应 < 5（无断崖）。"""
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime
    import unittest.mock as mock

    base_opp = {"confidence_score": 50, "symbol": "BTC/USDT", "scanner_family": "test"}

    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=MarketRegime(state="bear", confidence=0.49)):
        s_below = _compute_trade_score(base_opp, None, {"status": "watch"}, "medium",
                                        direction="short")
    with mock.patch("aletheia.agents.paper_trader_ranker.get_current_regime",
                    return_value=MarketRegime(state="bear", confidence=0.51)):
        s_above = _compute_trade_score(base_opp, None, {"status": "watch"}, "medium",
                                        direction="short")
    assert abs(s_above - s_below) < 5, (
        f"confidence 0.49→0.51 分差 {abs(s_above-s_below):.1f} 超出 5 分，"
        "regime 评分仍有断崖效应"
    )


# ---------------------------------------------------------------------------
# CR-PI-01: scanner weight persistence
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OL-C11", "operator_layer", "§scanner_weight_persistence", "L2a",
        "_action_adjust_scanner_weight MUST persist weight to config via save_config so "
        "adjustments survive restarts. UniverseConfig MUST have scanner_weights field.",
        status="implemented",
    ),
)
def test_scanner_weight_persistence():
    """Verify scanner_weight adjustments persist to config (CR-PI-01 fix)."""
    from aletheia.config.schema import UniverseConfig, AletheiaConfig

    # Schema: UniverseConfig must have scanner_weights field
    u = UniverseConfig()
    assert hasattr(u, "scanner_weights"), "UniverseConfig missing scanner_weights field"
    assert isinstance(u.scanner_weights, dict), "scanner_weights must be a dict"

    # operator_loop source: _action_adjust_scanner_weight must call save_config
    import ast
    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Find the _action_adjust_scanner_weight function body
    fn_body_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_action_adjust_scanner_weight":
            fn_body_src = ast.unparse(node)
            break
    assert fn_body_src, "_action_adjust_scanner_weight not found in operator_loop.py"
    assert "save_config" in fn_body_src, (
        "_action_adjust_scanner_weight does not call save_config — "
        "scanner weight adjustments are in-memory only and will be lost on restart (CR-PI-01)"
    )
    assert "scanner_weights" in fn_body_src, (
        "_action_adjust_scanner_weight does not write scanner_weights field"
    )


@spec_claim(
    SpecClaim(
        "OL-C12", "operator_layer", "§stale_recommendation_escalation", "L2a",
        "_check_stale_recommendations must exist and escalate recommend_retire/demote "
        "pi_decisions pending >24h into notify_operator calls (E5 fix).",
        status="implemented",
    ),
)
def test_stale_recommendation_escalation_exists():
    """Verify _check_stale_recommendations function exists and has correct behavior."""
    import ast
    src = _OPERATOR_LOOP_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn_found = False
    fn_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_stale_recommendations":
            fn_found = True
            fn_src = ast.unparse(node)
            break
    assert fn_found, "_check_stale_recommendations not found in operator_loop.py (E5 fix missing)"
    assert "recommend_retire" in fn_src or "recommend_demote" in fn_src, (
        "_check_stale_recommendations does not handle recommend_retire/recommend_demote"
    )
    assert "notify_operator" in fn_src or "_action_notify" in fn_src, (
        "_check_stale_recommendations does not escalate to notify_operator"
    )
    # Must be called from run_operator_loop
    loop_fn_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_operator_loop":
            loop_fn_src = ast.unparse(node)
            break
    assert "_check_stale_recommendations" in loop_fn_src, (
        "_check_stale_recommendations is defined but never called from run_operator_loop"
    )
