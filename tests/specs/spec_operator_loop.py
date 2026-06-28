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
        "OL-C07", "operator_layer", "§permission_model loosen_in_level2_only", "L2c",
        "loosen_confidence/increase_sizing/widen_stop_loss belong to _LEVEL_2_ACTIONS, "
        "not _LEVEL_1_ACTIONS — these loosening actions require human proposal",
        status="implemented",
    ),
)
def test_loosening_actions_in_level2_not_level1():
    """Loosening actions must be Level 2 (proposal) only, never Level 1 (auto).

    Fixed 2026-06-23 (OL-C07 / §15.2): the dead elif branches for
    loosen_confidence/increase_sizing/widen_stop_loss were removed from
    _execute_level1_action, so the Level-1 executor no longer has any code path
    that treats a loosening action as auto-executable. The set classification
    (_LEVEL_2_ACTIONS) and the executor are now consistent.
    """
    import aletheia.agent_shell.operator_loop as ol

    loosening_actions = {"loosen_confidence", "increase_sizing", "widen_stop_loss"}

    # Half of the claim: they ARE in _LEVEL_2_ACTIONS (set classification is correct)
    missing_from_l2 = loosening_actions - ol._LEVEL_2_ACTIONS
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
                if isinstance(comp, ast.Constant) and comp.value in loosening_actions:
                    reachable_in_l1_exec.add(comp.value)

    assert not reachable_in_l1_exec, (
        f"DRIFT: _execute_level1_action contains handler branches for Level 2 actions: "
        f"{reachable_in_l1_exec}.  These branches must be removed to restore "
        f"Level 1 / Level 2 boundary integrity."
    )


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
