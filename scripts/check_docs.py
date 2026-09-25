"""Lightweight documentation-governance checks.

This intentionally checks only cheap, durable invariants. It is not a
documentation database, status tracker, or generated-site pipeline.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PROPOSAL_STATUSES = {
    "draft",
    "accepted",
    "rejected",
    "implemented",
    "superseded",
}
FROZEN_MARKERS = {
    Path("docs/archive/legacy/issues/README.md"): "FORENSIC-FROZEN",
    Path("docs/archive/legacy/plans/README.md"): "LEGACY-FROZEN",
    Path("docs/archive/legacy/status/README.md"): "SNAPSHOT-FROZEN",
    Path("docs/archive/legacy/ai-context/README.md"): "HISTORICAL-NO-TRACKING",
}
REQUIRED_CURRENT_FILES = {
    Path("docs/governance/policies/documentation_governance.md"),
    Path("docs/design/contracts/README.md"),
    Path("docs/design/proposals/README.md"),
    Path("docs/product/CAPABILITY_GROUND_TRUTH.md"),
    Path("docs/product/ACTIVE_RUNTIME_SCOPE.md"),
    Path(".github/ISSUE_TEMPLATE/change_contract.md"),
    Path(".github/PULL_REQUEST_TEMPLATE.md"),
}
RETIRED_CONTRACT_PATHS = {
    Path("docs/design/contracts/learning_evolution_contract.md"),
    Path("docs/design/contracts/learning_observability_contract.md"),
    Path("docs/design/contracts/promotion_gates_contract.md"),
}
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
CURRENT_DOC_PREFIXES = (
    "docs/governance/",
    "docs/product/definition/",
    "docs/product/gates/",
    "docs/product/experiments/",
    "docs/product/capability-integrity/",
    "docs/design/contracts/",
    "docs/design/decisions/",
    "docs/design/proposals/",
    "docs/design/data/",
    "docs/design/api/",
    "docs/design/architecture/",
    "docs/design/operator/",
    "docs/design/frontend/",
    "docs/design/market-systems/",
    "docs/operations/",
)
CURRENT_DOC_FILES = {
    "AGENTS.md",
    "docs/README.md",
    "docs/product/README.md",
    "docs/product/SCOPE.md",
    "docs/product/ACTIVE_RUNTIME_SCOPE.md",
    "docs/product/CAPABILITY_GROUND_TRUTH.md",
    "docs/product/capability-integrity/README.md",
    "docs/design/README.md",
    "docs/design/process/SDD_METHODOLOGY.md",
}


def repository_markdown_files() -> list[Path]:
    """Return repository Markdown, including new files, but not ignored files."""
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "*.md",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode("utf-8"))
        if path.parts[:2] == ("docs", "monitoring"):
            continue
        paths.append(path)
    return sorted(set(paths))


def current_authority_markdown_files() -> list[Path]:
    return [
        path
        for path in repository_markdown_files()
        if path.as_posix() in CURRENT_DOC_FILES
        or path.as_posix().startswith(CURRENT_DOC_PREFIXES)
    ]


def _link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    return target.split(maxsplit=1)[0]


def broken_relative_links(markdown_files: list[Path]) -> list[str]:
    failures: list[str] = []
    for relative in markdown_files:
        source = ROOT / relative
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = unquote(_link_target(raw_target))
            if (
                not target
                or target.startswith(("#", "/", "//"))
                or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)
            ):
                continue
            path_text = target.split("#", 1)[0].split("?", 1)[0]
            if not path_text:
                continue
            resolved = (source.parent / path_text).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                failures.append(f"{relative}: link escapes repository: {target}")
                continue
            if not resolved.exists():
                failures.append(f"{relative}: missing link target: {target}")
    return failures


def proposal_metadata_failures() -> list[str]:
    failures: list[str] = []
    proposal_dir = ROOT / "docs/design/proposals"
    for path in sorted(proposal_dir.glob("*.md")):
        if path.name in {"README.md", "template.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            failures.append(f"{path.relative_to(ROOT)}: missing YAML frontmatter")
            continue
        try:
            frontmatter = text.split("---\n", 2)[1]
        except IndexError:
            failures.append(f"{path.relative_to(ROOT)}: unclosed YAML frontmatter")
            continue
        proposal_id = re.search(r"^id:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        status = re.search(r"^status:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        if not proposal_id:
            failures.append(f"{path.relative_to(ROOT)}: missing proposal id")
        if not status or status.group(1) not in ALLOWED_PROPOSAL_STATUSES:
            failures.append(
                f"{path.relative_to(ROOT)}: invalid proposal status"
            )
    return failures


def governance_failures() -> list[str]:
    failures: list[str] = []
    for relative in REQUIRED_CURRENT_FILES:
        if not (ROOT / relative).is_file():
            failures.append(f"missing current authority: {relative}")
    for relative, marker in FROZEN_MARKERS.items():
        path = ROOT / relative
        if not path.is_file() or marker not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative}: missing frozen-boundary marker {marker}")
    duplicate_scope = ROOT / "docs/product/definition/ACTIVE_RUNTIME_SCOPE.md"
    if duplicate_scope.exists():
        failures.append(
            "docs/product/definition/ACTIVE_RUNTIME_SCOPE.md: duplicate authority"
        )
    for relative in RETIRED_CONTRACT_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"{relative}: future or retired design in contracts/")
    return failures


TOP_LEVEL_DIRS = {
    path.name
    for path in ROOT.iterdir()
    if path.is_dir() and not path.name.startswith(".")
}
# Drift baseline: contract refs to root-relative `.py` paths that no longer
# exist on disk. Kept as a visible, dated set (never a silent skip) so the
# check is green on master but still catches NEW drift. 2026-09-26.
BASELINE_MISSING_CONTRACT_PATHS = {
    "data/derivatives_facts.py",
    "extensions/research/market_observer.py",
    "tests/e2e/test_learning_paper_lifecycle.py",
    "tests/specs/spec_learning_authority.py",
    "tests/specs/spec_learning_lifecycle.py",
    "tests/specs/spec_trade_decision_rationale.py",
    "tests/test_uta.py",
}


def contract_code_path_failures() -> list[str]:
    """Contract docs must reference real repo file paths in backticked `.py` spans.

    Scopes to `docs/design/contracts/*.md`. Finds backticked inline spans that
    look like a repo-relative `.py` path: they must contain a `/`, end in `.py`,
    contain no spaces/backticks, and their first segment must be a real top-level
    repo directory (e.g. ``core/…``, ``tests/…``, ``scripts/…``) so the reference
    is genuinely root-relative. Each such path must exist on disk relative to the
    repository root.

    Non-goals (deliberately not validated here, need a symbol classifier):
      - dotted symbols, ``foo()`` calls, constants, ``Class.method()`` refs;
      - ``.md`` targets (covered by ``broken_relative_links`` link syntax);
      - package-relative shorthand like ``learning/registry.py`` (a top-level
        segment missing) and globs/placeholders like ``agents/*.py`` -- these are
        prose abbreviations or patterns, not root-relative paths, and are ignored
        rather than reported;
      - fenced code blocks are skipped: they are illustrative samples, not live
        path references, and would churn on intentionally abbreviated paths.
    Anything unrecognized is ignored, never failed.
    """
    failures: list[str] = []
    pattern = re.compile(r"`([^`\s]+?/[^`\s]*\.py)`")
    for relative in sorted(Path("docs/design/contracts").glob("*.md")):
        if relative.name == "README.md":
            continue
        text = (ROOT / relative).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for path in pattern.findall(line):
                if (
                    path in BASELINE_MISSING_CONTRACT_PATHS
                    or path.split("/", 1)[0] not in TOP_LEVEL_DIRS
                    or "*" in path
                    or "{" in path
                ):
                    continue
                if not (ROOT / path).exists():
                    failures.append(
                        f"{relative}:{lineno}: missing contract code path: {path}"
                    )
    return failures


# Timestamp-unit baseline: pre-existing assignments whose ``*_ms`` name is fed
# by a ``/ 1000`` (seconds) expression or whose ``*_sec``/``*_seconds`` name is
# fed by a ``* 1000`` (milliseconds) expression. Visible, dated (2026-09-26),
# never a silent skip; keep green on master but still catch NEW violations.
BASELINE_TIMESTAMP_UNIT_VIOLATIONS = {
    # (relative_path, lineno) tuples of audited violations.
}


def timestamp_unit_suffix_failures() -> list[str]:
    """Variable names ending in ``_ms`` must carry milliseconds; ``_sec``/
    ``_seconds`` must carry seconds.

    Scopes to Python source under ``core/aletheia/``. Flags only the two most
    dangerous, mechanically-obvious unit contradictions, detected with ``ast``:
      1. a target named ``*_ms`` whose value divides by 1000 (``/`` or ``//``);
      2. a target named ``*_sec``/``*_seconds`` whose value multiplies by 1000.

    Non-goals (deliberately not validated here, need dataflow or type info):
      - cross-function dataflow and function return-type units;
      - dict-key units and database column units;
      - comparisons against ``time.time()`` vs ``time.time_ns()``;
      - any inference requiring type information.
    Anything not matching the two literal patterns above is ignored, never
    flagged. Files that fail to parse are reported (not silently skipped).
    """
    failures: list[str] = []
    source_root = ROOT / "core/aletheia"
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno}: timestamp unit check: syntax error")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.BinOp) or not isinstance(value.right, ast.Constant):
                continue
            if value.right.value != 1000:
                continue
            div_op = isinstance(value.op, (ast.Div, ast.FloorDiv))
            mul_op = isinstance(value.op, ast.Mult)
            for target in targets:
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                else:
                    continue
                bad = (
                    (name.endswith("_ms") and div_op)
                    or (name.endswith(("_sec", "_seconds")) and mul_op)
                )
                if not bad:
                    continue
                if (str(relative), node.lineno) in BASELINE_TIMESTAMP_UNIT_VIOLATIONS:
                    continue
                op = "/" if isinstance(value.op, ast.Div) else (
                    "//" if isinstance(value.op, ast.FloorDiv) else "*"
                )
                failures.append(
                    f"{relative}:{node.lineno}: timestamp unit contradiction: "
                    f"{name} = ... {op} 1000"
                )
    return failures


# Vacuous-test baseline: pre-existing test functions whose body contains no
# assertion construct at all, so they can never fail. Visible, dated
# (2026-09-26), never a silent skip; keep green on master but still catch NEW
# vacuous tests. (relative_path, func_name) tuples, not line numbers, so the
# baseline does not churn on edits.
BASELINE_VACUOUS_TESTS = {
    # (relative_path, func_name)
    ("tests/specs/spec_broker_funding_fairness.py", "test_broker_funding_recovery_is_fail_closed_and_fair"),
    ("tests/specs/spec_candidate_selection_utility.py", "test_current_candidate_bootstrap_without_slow_cache"),
    ("tests/specs/spec_candidate_selection_utility.py", "test_perp_funding_history_cutover"),
    ("tests/specs/spec_data_model.py", "test_run_migrations_idempotent"),
    ("tests/specs/spec_data_utilization.py", "test_fred_research_cutoff_and_isolation"),
    ("tests/specs/spec_data_utilization.py", "test_positioning_evidence_cutoff_and_replay"),
    ("tests/specs/spec_data_utilization.py", "test_options_volatility_observation_export"),
    ("tests/specs/spec_data_utilization.py", "test_current_derivatives_have_one_economic_owner"),
    ("tests/specs/spec_data_utilization.py", "test_derivatives_comparison_is_observation_only"),
    ("tests/specs/spec_entry_plan_contract.py", "test_market_quality_retry_before_input_binding"),
    ("tests/specs/spec_entry_plan_contract.py", "test_static_entry_feasibility_and_birth_owner"),
    ("tests/specs/spec_macro_signals.py", "test_macro_source_time_consumer_contract"),
    ("tests/specs/spec_observation_experiment_ownership.py", "test_legacy_experiment_reference_inventory_cannot_grow"),
    ("tests/specs/spec_observation_experiment_ownership.py", "test_no_new_legacy_experiment_writer"),
    ("tests/specs/spec_pipeline_bitget_preset_protection.py", "test_bitget_spot_and_futures_close_never_send_entry_presets"),
    ("tests/specs/spec_update_observation_settlement.py", "test_caught_up_activation_cut_is_a_true_no_op"),
    ("tests/specs/spec_update_observation_settlement.py", "test_activation_cut_contains_only_delivery_and_checkpoint_work"),
    ("tests/specs/spec_update_observation_settlement.py", "test_activation_cut_lock_covers_backup_and_source_switch"),
    ("tests/specs/spec_update_observation_settlement.py", "test_bounded_applied_delivery_recovery"),
    ("tests/test_architecture_fitness.py", "test_risk_decisions_have_one_application_writer"),
    ("tests/test_architecture_fitness.py", "test_risk_writer_guard_accepts_canonical_storage_and_risk_import"),
    ("tests/test_architecture_fitness.py", "test_fill_writes_stay_with_exchange_materialization"),
    ("tests/test_architecture_fitness.py", "test_fill_writer_guard_preserves_exact_provenance_update"),
    ("tests/test_architecture_fitness.py", "test_candidate_writer_guard_accepts_publication_and_distinct_maintenance"),
    ("tests/test_config_schema_bounds.py", "test_execution_cost_config_accepts_documented_boundaries"),
    ("tests/test_gateway_firewall_cli.py", "test_noninteractive_gateway_start_never_attempts_hidden_uac"),
    ("tests/test_gateway_firewall_cli.py", "test_loopback_binding_never_requests_firewall_change"),
    ("tests/test_main_install_hint.py", "test_hint_eof_on_stdin_does_not_crash"),
    ("tests/test_notifications.py", "test_webhook_respects_notify_alerts_false"),
    ("tests/test_notifications.py", "test_webhook_respects_notify_trade_fills_false"),
    ("tests/test_notifications.py", "test_webhook_respects_notify_daily_summary_false"),
    ("tests/test_observation_execution_owner.py", "test_worker_cycle_exclusion_is_reentrant_only_in_the_holding_thread"),
    ("tests/test_observation_execution_owner.py", "test_worker_lock_releases_and_can_be_reacquired"),
    ("tests/test_okx_broker.py", "test_close_is_noop_when_never_used"),
    ("tests/test_order_writer_boundary.py", "test_order_writer_ownership"),
    ("tests/test_order_writer_boundary.py", "test_order_guard_accepts_canonical_owner_and_delegation"),
    ("tests/test_planning_writer_boundary.py", "test_planning_guard_accepts_declared_writers"),
    ("tests/test_planning_writer_boundary.py", "test_planning_writer_ownership"),
    ("tests/test_proposal_writer_boundary.py", "test_proposal_writer_owners"),
    ("tests/test_regime_boundary.py", "test_universe_keeps_publishing_without_optional_regime"),
    ("tests/test_self_evolution_tier1.py", "test_create_failure_does_not_raise"),
    ("tests/test_spec_conformance.py", "test_print_conformance_matrix"),
    ("tests/test_tokenomics_phase345.py", "test_budget_enforcement_is_dormant_by_default"),
    ("tests/test_tokenomics_phase345.py", "test_budget_warn_only_never_denies"),
    ("tests/test_tokenomics_phase345.py", "test_safety_callers_remain_warn_only_in_every_mode"),
    ("tests/test_venue_listing_cache.py", "test_invalidate_nonexistent_is_noop"),
    ("tests/test_windows_dogfood.py", "test_assert_trust_root_matches_passes_when_keys_equal"),
    ("tests/test_windows_dogfood.py", "test_require_windows_passes_on_windows"),
    ("tests/test_ws_realtime.py", "test_fingerprint_queries_match_the_migrated_schema"),
}


def _call_name(call: ast.Call) -> str | None:
    """Return the dotted tail name of a call target (``assert_frame_equal``,
    ``self.assertEqual`` -> ``assertEqual``), or ``None`` for dynamic targets."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_placeholder(body: list[ast.stmt]) -> bool:
    """True if the body is just a docstring and/or ``pass``/``...``."""
    stmts = [
        stmt
        for stmt in body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]
    return all(
        isinstance(stmt, ast.Pass)
        or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        )
        for stmt in stmts
    )


def _has_assertion(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body contains any verification construct."""
    for dec in func.decorator_list:
        name = dec.id if isinstance(dec, ast.Name) else (
            dec.attr if isinstance(dec, ast.Attribute) else None
        )
        if name and ("skip" in name or "xfail" in name):
            return True
    if _is_placeholder(func.body):
        return True
    for node in ast.walk(func):
        if isinstance(node, (ast.Assert, ast.Raise)):
            return True
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name and (name.startswith("assert") or name in ("fail", "skip", "xfail")):
                return True
        if isinstance(node, ast.With):
            ctx = node.items[0].context_expr
            if isinstance(ctx, ast.Call) and _call_name(ctx) in (
                "raises", "warns", "deprecated_call",
            ):
                return True
    return False


def vacuous_test_failures() -> list[str]:
    """Test functions must actually assert something.

    Scopes to Python sources under ``tests/``. Flags any function whose name
    starts with ``test_`` (at module level or inside classes, sync or async)
    whose body contains NO verification construct at all: no ``assert``, no
    call named ``assert*`` / ``*assert_*`` (covers ``self.assertEqual``,
    ``assert_frame_equal``, ``mock.assert_called_once()``), no
    ``with pytest.raises/warns/deprecated_call``, no ``pytest.fail/skip/xfail``
    or ``fail``, and no bare ``raise``. Such a test can never fail, so it offers
    false confidence while costing runtime.

    Skipped (not flagged): functions decorated with a name containing ``skip``
    or ``xfail`` (already excluded from meaningful execution), and empty
    placeholders whose body is only a docstring and/or ``pass``/``...`` (flagged
    anyway they would be pure noise).

    Non-goals (deliberately not detected here, need semantic analysis):
      - tautological assertions like ``assert True`` / ``assert x == x``;
      - assertions on the wrong object;
      - assertions made unreachable by an early ``return``.
    This check answers only "is there any assertion at all". Anything
    unrecognized is ignored, never flagged. Files that fail to parse are
    reported (not silently skipped).
    """
    failures: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        relative = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno}: vacuous test check: syntax error")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if _has_assertion(node):
                continue
            if (str(relative), node.name) in BASELINE_VACUOUS_TESTS:
                continue
            failures.append(f"{relative}:{node.lineno}: vacuous test (no assertion): {node.name}")
    return failures


def semantic_truth_failures() -> list[str]:
    """Guard a few cross-document truths that structural link checks cannot see."""
    failures: list[str] = []

    registry_path = ROOT / "extensions/learning/registry.py"
    registry_tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    measured_paths: list[str] = []
    for node in ast.walk(registry_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "LearningPath":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        measurement_supported = next(
            (
                keyword.value
                for keyword in node.keywords
                if keyword.arg == "measurement_supported"
            ),
            None,
        )
        if (
            isinstance(measurement_supported, ast.Constant)
            and measurement_supported.value is True
        ):
            measured_paths.append(str(node.args[0].value))
    expected_measured_paths = [
        "universe_family_calibration",
        "momentum_entry_geometry",
        "mechanism_entry_learning",
        "ranker_case_score",
    ]
    if measured_paths != expected_measured_paths:
        failures.append(
            "extensions/learning/registry.py: measured-path contract mismatch; "
            f"expected {expected_measured_paths}, found {measured_paths}"
        )

    current_truth = {
        Path("docs/design/contracts/learning_contract.md"):
            "and mechanism Entry Learning are Paper-authority-capable;",
        Path("docs/product/experiments/EXPERIMENT_PROTOCOL.md"):
            "Mechanism Entry Learning uses Planning Generation as its ITT unit",
        Path("docs/product/CAPABILITY_GROUND_TRUTH.md"):
            "Retired simulator | Archived; no current execution",
        Path("docs/product/capability-integrity/CAPABILITY_INVENTORY.md"):
            "LN-08 | Learned guard generation and observation",
        Path("docs/product/capability-integrity/FACT_CARDS.md"):
            "no learned block/penalty",
        Path("docs/product/experiments/EXPERIMENT_MVP_GATE.md"):
            "retired historical compatibility pointer",
        Path("docs/product/experiments/EXPERIMENT_BACKLOG.md"):
            "retired historical compatibility pointer",
    }
    for relative, required in current_truth.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        if required not in text:
            failures.append(f"{relative}: missing semantic truth marker: {required}")

    legacy_script = (ROOT / "scripts/mvp_gate_check.py").read_text(encoding="utf-8")
    if "LEGACY-ONLY" not in legacy_script:
        failures.append(
            "scripts/mvp_gate_check.py: historical Round 1 gate is not marked LEGACY-ONLY"
        )

    stale_phrases = {
        "Learned guard generation and enforcement",
        "the two supported paths",
        "GitHub issue #489",
        "[VISION.md](VISION.md)",
        "The current surface does **not** expose the measured A1 cluster",
        "Its `LCO-*` claims remain `missing`",
        "(`LCO-*`, target/missing)",
        "the missing `LCO-*` owner projection",
        "owner summary, default Contribution tab",
        "| Learning UI | Contribution is the default tab",
        "suitable for the default\nContribution tab",
        "The Contribution tab exposes every measurement-supported path",
    }
    truth_files = [
        ROOT / "docs/product/SCOPE.md",
        ROOT / "docs/product/CAPABILITY_GROUND_TRUTH.md",
        ROOT / "docs/product/gates/METRICS.md",
        ROOT / "docs/product/gates/PHASE_GATES.md",
        ROOT / "docs/product/experiments/EXPERIMENT_PROTOCOL.md",
        ROOT / "docs/design/contracts/learning_contract.md",
        ROOT / "docs/design/proposals/learning_observability.md",
        ROOT / "docs/design/process/DOC_ATLAS_BY_DOMAIN.md",
        ROOT / "docs/design/data/data_utilization.md",
    ]
    for path in truth_files:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            if phrase in text:
                failures.append(
                    f"{path.relative_to(ROOT)}: stale semantic authority: {phrase}"
                )

    retired_gate_phrases = {
        "Phase 2A - 2B",
        "direction_affinity=\"short\"",
        "SELECT COUNT(*) FROM strategy_edits",
    }
    phase_gates = (ROOT / "docs/product/gates/PHASE_GATES.md").read_text(
        encoding="utf-8"
    )
    for phrase in retired_gate_phrases:
        if phrase in phase_gates:
            failures.append(
                "docs/product/gates/PHASE_GATES.md: retired strategy gate remains: "
                + phrase
            )

    return failures


def main() -> int:
    failures = []
    failures.extend(governance_failures())
    failures.extend(proposal_metadata_failures())
    failures.extend(broken_relative_links(current_authority_markdown_files()))
    failures.extend(contract_code_path_failures())
    failures.extend(timestamp_unit_suffix_failures())
    failures.extend(vacuous_test_failures())
    failures.extend(semantic_truth_failures())
    if failures:
        print("Documentation governance check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Documentation governance check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
