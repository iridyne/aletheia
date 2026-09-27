"""Lightweight documentation-governance checks.

This intentionally checks only cheap, durable invariants. It is not a
documentation database, status tracker, or generated-site pipeline.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import date
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
# Zero known violations as of 2026-09-26 (R20 harvest): all seven historical
# entries were resolved — pointer fixes for moved files, retirement notes for
# deleted tests/agents, and successor executable claims for converged specs.
BASELINE_MISSING_CONTRACT_PATHS: set[str] = {}


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
# Zero known violations as of 2026-09-26 (R20 harvest): all 49 historical
# entries now carry real assertions — boundary scans assert their scan count,
# delegation wrappers assert the delegated observable, and pure no-op tests
# assert the observable consequence of the operation.
BASELINE_VACUOUS_TESTS: set[tuple[str, str]] = {}


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


def _vacuous_failures_in_file(path: Path) -> list[str]:
    """Vacuous-test findings for one test module. Shared by the whole-repo gate
    and the changed-file gate so the two can never disagree."""
    relative = path.relative_to(ROOT)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{relative}:{exc.lineno}: vacuous test check: syntax error"]
    failures: list[str] = []
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


def vacuous_test_failures(paths: list[str] | None = None) -> list[str]:
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

    ``paths`` (repository-relative test modules) narrows the scan to those files
    for the changed-file gate; ``None`` scans every test module.

    Non-goals (deliberately not detected here, need semantic analysis):
      - tautological assertions like ``assert True`` / ``assert x == x``;
      - assertions on the wrong object;
      - assertions made unreachable by an early ``return``.
    This check answers only "is there any assertion at all". Anything
    unrecognized is ignored, never flagged. Files that fail to parse are
    reported (not silently skipped).
    """
    if paths is None:
        targets = sorted((ROOT / "tests").rglob("*.py"))
    else:
        targets = [ROOT / path for path in paths]
    failures: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        failures.extend(_vacuous_failures_in_file(path))
    return failures


def _find_test_verification_nodes(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    """Return all verification constructs inside one test function body.

    Reuses the same recognition as ``_has_assertion``: ``assert``, bare
    ``raise``, calls named ``assert*`` / ``fail`` / ``skip`` / ``xfail``,
    and ``with pytest.raises/warns/deprecated_call``.
    """
    nodes: list[ast.AST] = []
    for node in ast.walk(func):
        if isinstance(node, (ast.Assert, ast.Raise)):
            nodes.append(node)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name and (name.startswith("assert") or name in ("fail", "skip", "xfail")):
                nodes.append(node)
        elif isinstance(node, ast.With):
            ctx = node.items[0].context_expr
            if isinstance(ctx, ast.Call) and _call_name(ctx) in (
                "raises", "warns", "deprecated_call",
            ):
                nodes.append(node)
    return nodes


def _is_guarded_data_check(node: ast.AST) -> bool:
    """True if the AST node sits inside an ``if`` whose condition is an
    existence/data check and that ``if`` has no ``else`` branch."""
    return isinstance(node, ast.If) and not node.orelse and isinstance(
        node.test, (ast.Call, ast.BoolOp, ast.UnaryOp)
    )


def _if_existence_condition(test: ast.AST) -> bool:
    """Whether an ``if`` test looks like a data/environment existence probe."""
    calls = list(ast.walk(test))
    for call in calls:
        if not isinstance(call, ast.Call):
            continue
        name = _call_name(call)
        if name in ("exists", "getenv", "get", "isfile", "isdir"):
            return True
        if name == "get" and isinstance(call.func, ast.Attribute) and call.func.attr == "get":
            return True
        if isinstance(call.func, ast.Attribute) and call.func.attr in (
            "exists", "getenv", "get", "isfile", "isdir",
        ):
            return True
    for node in ast.walk(test):
        if isinstance(node, ast.Attribute) and node.attr in (
            "exists", "getenv", "get", "isfile", "isdir",
        ):
            return True
    return False


# Skip/xfail hygiene + data-guarded silent-pass baseline (2026-09-26).
# Visible, dated, never a silent skip; keep green on master but still catch
# NEW violations. See skip_xfail_and_data_guard_failures() for the detector.
BASELINE_SKIP_XFAIL_DATA_GUARD: set[tuple[str, int, str]] = set()


def _reason_text(reason: ast.AST | None) -> str | None:
    """Extract literal reason text from an ast node; None if not a plain
    string or a single-variable f-string (treat dynamic interpolations as
    non-empty)."""
    if reason is None:
        return None
    if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
        return reason.value
    if isinstance(reason, ast.JoinedStr):
        return " ".join(
            part.value for part in reason.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ).strip() or "f"
    return "f"


def skip_xfail_and_data_guard_failures() -> list[str]:
    """Skip/xfail hygiene and data-guarded silent-pass detection.

    Part A — skip/xfail hygiene: ``pytest.skip(...)`` calls,
    ``@pytest.mark.skip`` / ``@pytest.mark.skipif`` / ``@pytest.mark.xfail``
    decorators and ``pytest.importorskip(...)`` calls must carry a non-empty
    ``reason``; ``xfail`` must be explicitly ``strict=True`` (non-strict xfail
    silently passes when the test unexpectedly succeeds); unconditional
    ``@pytest.mark.skip`` is flagged because such a test should be deleted or
    moved to history rather than left permanently skipped.

    Part B — data-guard silent pass: a ``test_*`` function whose every
    verification construct sits inside an ``if`` whose condition is a
    data/existence probe (``os.path.exists``, ``Path(...).exists()``,
    ``os.environ.get``, ``os.getenv``, ``.isfile``, ``.isdir``, ``.get``)
    and that ``if`` has no ``else`` branch. When the guard is false the test
    performs zero assertions and passes — a false green.

    Non-goals (deliberately not validated here, need dataflow or intent):
      - reason text quality, skipif-condition validity, and conditional skips
        inside fixtures/helpers (cross-function dataflow);
      - ``else`` branches that disclose with an explicit
        ``pytest.skip(reason=...)`` (correct disclosure, not flagged);
      - loops that assert over a scanned corpus (not a guard gate);
      - assertion sufficiency (same boundary as ``vacuous_test_failures``).
    """
    failures: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        relative = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno}: skip/xfail check: syntax error")
            continue

        # Part A: decorators
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                mark = None
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Attribute)
                    and dec.func.value.attr == "mark"
                ):
                    mark = dec.func.attr
                elif (
                    isinstance(dec, ast.Attribute)
                    and isinstance(dec.value, ast.Attribute)
                    and dec.value.attr == "mark"
                ):
                    mark = dec.attr
                if mark not in ("skip", "skipif", "xfail"):
                    continue
                if not isinstance(dec, ast.Call):
                    # bare @pytest.mark.skip without a call -> unconditional skip
                    failures.append(
                        f"{relative}:{dec.lineno}: unconditional @pytest.mark.skip "
                        f"on {node.name} (no reason accepted)"
                    )
                    continue
                if mark in ("skip", "skipif"):
                    reason = next(
                        (kw.value for kw in dec.keywords if kw.arg == "reason"),
                        None,
                    )
                    reason_text = _reason_text(reason)
                    if mark == "skip":
                        failures.append(
                            f"{relative}:{dec.lineno}: unconditional @pytest.mark.skip "
                            f"on {node.name} (no reason accepted)"
                        )
                    elif not reason_text or not reason_text.strip():
                        failures.append(
                            f"{relative}:{dec.lineno}: @pytest.mark.skipif on "
                            f"{node.name} requires a non-empty reason"
                        )
                else:  # xfail
                    reason = next(
                        (kw.value for kw in dec.keywords if kw.arg == "reason"),
                        None,
                    )
                    strict = next(
                        (kw.value for kw in dec.keywords if kw.arg == "strict"),
                        None,
                    )
                    reason_text = _reason_text(reason)
                    if not reason_text or not reason_text.strip():
                        failures.append(
                            f"{relative}:{dec.lineno}: @pytest.mark.xfail on "
                            f"{node.name} requires a non-empty reason"
                        )
                    if strict is None or not isinstance(strict, ast.Constant) or strict.value is not True:
                        failures.append(
                            f"{relative}:{dec.lineno}: @pytest.mark.xfail on "
                            f"{node.name} must be strict=True"
                        )

        # Part A: pytest.skip / importorskip calls without reason
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in ("skip", "importorskip"):
                continue
            if name == "skip":
                reason = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "reason"),
                    None,
                )
                reason_text = _reason_text(reason)
                if not reason_text or not reason_text.strip():
                    failures.append(
                        f"{relative}:{node.lineno}: pytest.skip requires a non-empty reason"
                    )
            else:  # importorskip
                reason = node.args[1] if len(node.args) > 1 else next(
                    (kw.value for kw in node.keywords if kw.arg == "reason"),
                    None,
                )
                reason_text = _reason_text(reason)
                if not reason_text or not reason_text.strip():
                    failures.append(
                        f"{relative}:{node.lineno}: pytest.importorskip requires a non-empty reason"
                    )

        # Part B: data-guarded silent pass
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            verifications = _find_test_verification_nodes(node)
            if not verifications:
                continue
            all_guarded = True
            for vnode in verifications:
                guard = None
                parent_map = {}
                for p in ast.walk(node):
                    for child in ast.iter_child_nodes(p):
                        parent_map[id(child)] = p
                current = vnode
                found = False
                while current is not None:
                    parent = parent_map.get(id(current))
                    if parent is None:
                        break
                    if isinstance(parent, ast.If) and not parent.orelse:
                        if _if_existence_condition(parent.test):
                            guard = parent
                            found = True
                            break
                    current = parent
                if not found:
                    all_guarded = False
                    break
            if all_guarded and guard is not None:
                failures.append(
                    f"{relative}:{node.lineno}: data-guarded test (no assertions "
                    f"when guard is false): {node.name}"
                )
    return failures


# Contract names that live outside ``docs/design/contracts/<name>.md``. Every
# other name resolves automatically from that directory's file stems.
# ``docs/design/contracts/`` always wins over a same-stem Proposal: an executable
# claim answers to approved behavior, never to a design that does not exist yet.
CONTRACT_DOC_ALIASES = {
    "PI_OPERATOR_DESIGN_DECISIONS": "docs/design/operator/PI_OPERATOR_DESIGN_DECISIONS.md",
    "active_runtime_scope": "docs/product/ACTIVE_RUNTIME_SCOPE.md",
    "api_reference": "docs/design/api/api_reference.md",
    "candidate_selection_utility": "docs/design/contracts/candidate_selection_utility_contract.md",
    "data_layer_contract": "docs/design/data/data_layer.md",
    "data_model": "docs/design/data/data_model.md",
    "data_utilization_contract": "docs/design/data/data_utilization.md",
    "entry_plan_contract": "docs/design/contracts/entry-plan-contract.md",
    "insight_feedback_loop": "docs/archive/legacy/plans/learning/insight_feedback_loop.md",
    "learning": "docs/design/contracts/learning_contract.md",
    "learning_evolution": "docs/design/proposals/learning_evolution.md",
    "learning_observability": "docs/design/proposals/learning_observability.md",
    "operator_layer": "docs/design/operator/operator_layer.md",
    "risk_engine": "docs/design/contracts/risk_engine_contract.md",
}
# Contract names with no document anywhere in the repository: the section
# references in their claims are the spec file's own numbering, not a pointer
# into approved behavior. Registering them turns "half the executable claims
# answer to no contract" into a table you can read, and makes any NEW invented
# namespace fail. Values are the claim count when registered (2026-09-26) --
# documentation, not a budget; nothing asserts on them.
UNANCHORED_CONTRACT_NAMESPACES = {
    "analytics_contract": 7,
    "behavior_authority": 3,
    "factor_feedback": 3,
    "futures_leverage": 10,
    "gateway_stability_contract": 14,
    "guard_authority": 20,
    "knowledge_fts_integrity": 5,
    "learning_pipeline": 21,
    "legacy_promotion_gates": 1,
    "network_egress_contract": 12,
    "opportunity_information_integrity": 4,
    "outcome_integrity": 12,
    "persona_sizing": 14,
    "pipeline_adopted_position": 28,
    "pipeline_bitget_preset_protection": 5,
    "pipeline_entry_enrichment": 4,
    "pipeline_exchange_account_routing": 1,
    "pipeline_exchange_commit": 7,
    "pipeline_exchange_materialization": 17,
    "pipeline_historical_chain_recovery": 7,
    "pipeline_live_disappearance_settlement": 5,
    "pipeline_live_unlock": 6,
    "pipeline_market_data": 11,
    "pipeline_middle_state_integrity": 17,
    "pipeline_multibroker_reconciliation": 12,
    "pipeline_opportunity_expiry": 2,
    "pipeline_outcome_feedback": 22,
    "pipeline_position_manager": 14,
    "pipeline_profit_loop": 2,
    "pipeline_risk_execution_integrity": 25,
    "pipeline_risk_gate": 10,
    "pipeline_stop_propagation": 4,
    "pipeline_tpsl_private_materialization": 10,
    "pipeline_unresolved_entry_exposure": 16,
    "serenity_attribution": 2,
    "serenity_evidence_quality": 14,
    "serenity_holding": 3,
    "serenity_live_readiness": 1,
    "serenity_multi_lens": 6,
    "serenity_promote": 11,
    "serenity_researchability": 5,
    "serenity_verdict": 3,
    "serenity_xstock_execution": 2,
    "spot_parity": 3,
    "strategy_pool": 3,
    "trust_boundary_policy": 6,
    "universe_pipeline": 20,
    "uta_boundary": 6,
}
# Contract names whose only document is a Proposal. A Proposal describes behavior
# that does not exist yet, so nothing in it is assertable -- an executable claim
# pointing there has no approved behavior behind it. Clearing an entry means
# moving the referenced sections into a Contract.
BASELINE_PROPOSAL_ANCHORED_CONTRACTS = {
    "learning_evolution",
    "learning_observability",
}
# Claims whose ``section`` finds no heading in the resolved document. A visible,
# dated set (2026-09-26), never a silent skip. Two shapes hide in here: a section
# the claim invented, and a document that never numbered its headings.
BASELINE_UNRESOLVED_CLAIM_SECTIONS = {
    ("tests/specs/spec_active_runtime_scope.py", "AR-S03"),
    ("tests/specs/spec_active_runtime_scope.py", "AR-S04"),
    ("tests/specs/spec_active_runtime_scope.py", "AR-S07"),
    ("tests/specs/spec_agent_contracts.py", "AC-S05"),
    ("tests/specs/spec_api_reference.py", "API-D6"),
    ("tests/specs/spec_api_reference.py", "API-D7"),
    ("tests/specs/spec_api_reference.py", "API-S08"),
    ("tests/specs/spec_candidate_selection_utility.py", "CSU-S01"),
    ("tests/specs/spec_candidate_selection_utility.py", "CSU-S02"),
    ("tests/specs/spec_candidate_selection_utility.py", "CSU-S04"),
    ("tests/specs/spec_candidate_selection_utility.py", "CSU-S05"),
    ("tests/specs/spec_candidate_selection_utility.py", "CSU-S06"),
    ("tests/specs/spec_codex_shepherd.py", "AG-CODEX-SHEPHERD-01"),
    ("tests/specs/spec_configuration.py", "CFG-S01"),
    ("tests/specs/spec_configuration.py", "CFG-S02"),
    ("tests/specs/spec_data_layer.py", "DL-B04"),
    ("tests/specs/spec_data_layer.py", "DL-B05"),
    ("tests/specs/spec_data_layer.py", "DL-B14"),
    ("tests/specs/spec_data_layer.py", "DL-C01"),
    ("tests/specs/spec_data_layer.py", "DL-C02"),
    ("tests/specs/spec_data_layer.py", "DL-C03"),
    ("tests/specs/spec_data_layer.py", "DL-S01"),
    ("tests/specs/spec_data_layer.py", "DL-S02"),
    ("tests/specs/spec_data_layer.py", "DL-S03"),
    ("tests/specs/spec_data_layer.py", "DL-S06"),
    ("tests/specs/spec_data_layer.py", "DL-S07"),
    ("tests/specs/spec_data_layer.py", "DL-S08"),
    ("tests/specs/spec_data_layer.py", "DL-S09"),
    ("tests/specs/spec_data_layer.py", "DL-S10"),
    ("tests/specs/spec_data_model.py", "DM-A14"),
    ("tests/specs/spec_data_model.py", "DM-S10"),
    ("tests/specs/spec_data_utilization.py", "DU-B01"),
    ("tests/specs/spec_data_utilization.py", "DU-B02"),
    ("tests/specs/spec_data_utilization.py", "DU-C01"),
    ("tests/specs/spec_data_utilization.py", "DU-C02"),
    ("tests/specs/spec_data_utilization.py", "DU-S01"),
    ("tests/specs/spec_data_utilization.py", "DU-S04"),
    ("tests/specs/spec_data_utilization.py", "DU-S05"),
    ("tests/specs/spec_data_utilization.py", "DU-S06"),
    ("tests/specs/spec_data_utilization.py", "DU-S07"),
    ("tests/specs/spec_data_utilization.py", "DU-S08"),
    ("tests/specs/spec_data_utilization.py", "DU-S09"),
    ("tests/specs/spec_data_utilization.py", "DU-S10"),
    ("tests/specs/spec_data_utilization.py", "DU-S12"),
    ("tests/specs/spec_data_utilization.py", "DU-S13"),
    ("tests/specs/spec_data_utilization.py", "DU-S15"),
    ("tests/specs/spec_data_utilization.py", "DU-S16"),
    ("tests/specs/spec_data_utilization.py", "DU-S17"),
    ("tests/specs/spec_data_utilization.py", "DU-S18"),
    ("tests/specs/spec_data_utilization.py", "DU-S19"),
    ("tests/specs/spec_data_utilization.py", "DU-S22"),
    ("tests/specs/spec_data_utilization.py", "DU-S30"),
    ("tests/specs/spec_data_utilization.py", "DU-S33"),
    ("tests/specs/spec_data_utilization.py", "DU-T01"),
    ("tests/specs/spec_decision_lifecycle.py", "DL-S64"),
    ("tests/specs/spec_defillama.py", "DFL-C01"),
    ("tests/specs/spec_defillama.py", "DFL-E2E"),
    ("tests/specs/spec_defillama.py", "DFL-S01"),
    ("tests/specs/spec_defillama.py", "DFL-S02"),
    ("tests/specs/spec_defillama.py", "DFL-S03"),
    ("tests/specs/spec_defillama.py", "DFL-S04"),
    ("tests/specs/spec_defillama.py", "DFL-S05"),
    ("tests/specs/spec_defillama.py", "DFL-S06"),
    ("tests/specs/spec_defillama.py", "DFL-S07"),
    ("tests/specs/spec_entry_plan_contract.py", "EP-S09"),
    ("tests/specs/spec_entry_plan_contract.py", "EP-S12"),
    ("tests/specs/spec_equity_observer.py", "EQ-R01"),
    ("tests/specs/spec_equity_observer.py", "EQ-R02"),
    ("tests/specs/spec_equity_observer.py", "EQ-R03"),
    ("tests/specs/spec_equity_observer.py", "EQ-R04"),
    ("tests/specs/spec_equity_observer.py", "EQ-R05"),
    ("tests/specs/spec_equity_observer.py", "EQ-R06"),
    ("tests/specs/spec_equity_observer.py", "EQ-R07"),
    ("tests/specs/spec_hyperliquid.py", "HL-C01"),
    ("tests/specs/spec_hyperliquid.py", "HL-C02"),
    ("tests/specs/spec_hyperliquid.py", "HL-E2E"),
    ("tests/specs/spec_hyperliquid.py", "HL-S01"),
    ("tests/specs/spec_hyperliquid.py", "HL-S02"),
    ("tests/specs/spec_hyperliquid.py", "HL-S03"),
    ("tests/specs/spec_hyperliquid.py", "HL-S04"),
    ("tests/specs/spec_hyperliquid.py", "HL-S05"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-B01"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-B03"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-B04"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-B05"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-B06"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-S01"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-S02"),
    ("tests/specs/spec_insight_feedback_loop.py", "IFL-S03"),
    ("tests/specs/spec_insight_feedback_loop.py", "PCI-G02"),
    ("tests/specs/spec_learning_contract.py", "LC-S04"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S01"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S02"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S03"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S04"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S05"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S06"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S07"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S09"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S11"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S12"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S13"),
    ("tests/specs/spec_learning_t0_foundation.py", "LT0-S15"),
    ("tests/specs/spec_lesson_guards.py", "LG-S01"),
    ("tests/specs/spec_lesson_guards.py", "LG-S02"),
    ("tests/specs/spec_lesson_guards.py", "LG-S03"),
    ("tests/specs/spec_lesson_guards.py", "LG-S04"),
    ("tests/specs/spec_lesson_guards.py", "LG-S05"),
    ("tests/specs/spec_lesson_guards.py", "LG-S06"),
    ("tests/specs/spec_macro_signals.py", "MS-C02"),
    ("tests/specs/spec_macro_signals.py", "MS-C03"),
    ("tests/specs/spec_macro_signals.py", "MS-C04"),
    ("tests/specs/spec_macro_signals.py", "MS-C05"),
    ("tests/specs/spec_macro_signals.py", "MS-C06"),
    ("tests/specs/spec_macro_signals.py", "MS-C08"),
    ("tests/specs/spec_macro_signals.py", "MS-C09"),
    ("tests/specs/spec_macro_signals.py", "MS-S03"),
    ("tests/specs/spec_macro_signals.py", "MS-S04"),
    ("tests/specs/spec_macro_signals.py", "MS-S05"),
    ("tests/specs/spec_macro_signals.py", "MS-S06"),
    ("tests/specs/spec_macro_signals.py", "MS-S07"),
    ("tests/specs/spec_macro_signals.py", "MS-S08"),
    ("tests/specs/spec_macro_signals.py", "MS-S09"),
    ("tests/specs/spec_macro_signals.py", "MS-S10"),
    ("tests/specs/spec_macro_signals.py", "MS-S11"),
    ("tests/specs/spec_macro_signals.py", "MS-S12"),
    ("tests/specs/spec_macro_signals.py", "MS-S14"),
    ("tests/specs/spec_macro_signals.py", "MS-S14b"),
    ("tests/specs/spec_macro_signals.py", "MS-S14c"),
    ("tests/specs/spec_macro_signals.py", "MS-S14d"),
    ("tests/specs/spec_market_type_dedup.py", "OD-S01"),
    ("tests/specs/spec_market_type_dedup.py", "OD-S01b"),
    ("tests/specs/spec_market_type_dedup.py", "OD-S02"),
    ("tests/specs/spec_market_type_dedup.py", "OD-S02d"),
    ("tests/specs/spec_market_type_dedup.py", "OD-S02e"),
    ("tests/specs/spec_observation_experiment_ownership.py", "OEO-S01"),
    ("tests/specs/spec_observation_experiment_ownership.py", "OEO-S04"),
    ("tests/specs/spec_observation_experiment_ownership.py", "OEO-S06"),
    ("tests/specs/spec_observation_experiment_ownership.py", "OEO-S07"),
    ("tests/specs/spec_operator_contract_parity.py", "PI-S05"),
    ("tests/specs/spec_operator_loop.py", "PCI-G01"),
    ("tests/specs/spec_operator_loop.py", "PCI-G03"),
    ("tests/specs/spec_operator_single_source.py", "PI-S06"),
    ("tests/specs/spec_pi_decision_chain.py", "PCI-G02"),
    ("tests/specs/spec_pi_decision_chain.py", "PCI-G05"),
    ("tests/specs/spec_pi_decision_chain.py", "PCI-G07"),
    ("tests/specs/spec_pi_decision_chain.py", "PI-S01"),
    ("tests/specs/spec_pi_decision_chain.py", "PI-S07"),
    ("tests/specs/spec_risk_engine.py", "RE-B15"),
    ("tests/specs/spec_risk_engine.py", "RE-B15b"),
    ("tests/specs/spec_risk_engine.py", "RE-B16"),
    ("tests/specs/spec_risk_engine.py", "RE-B19"),
    ("tests/specs/spec_risk_engine.py", "RE-S09"),
    ("tests/specs/spec_trading_mandate.py", "TM-S01"),
    ("tests/specs/spec_update_observation_settlement.py", "OBC-UPD-04"),
}
# Both baselines above expire. ``tests/test_spec_conformance.py`` records what
# happens without an expiry: a count budget nobody lowered stayed red for two
# months and stopped protecting anything. An expired non-empty baseline fails, so
# the choice is to clear entries or to move the date deliberately.
CLAIM_ANCHOR_BASELINE_EXPIRY = date(2026, 12, 1)
_CLAIM_SECTION_NUMBER = re.compile(r"^§?\s*([0-9]+(?:\.[0-9]+)*[a-z]?)\b")


def _contract_doc_paths() -> dict[str, str]:
    """Resolve every registered contract name to exactly one document path."""
    paths = {
        path.stem: f"docs/design/contracts/{path.name}"
        for path in (ROOT / "docs/design/contracts").glob("*.md")
        if path.name not in {"README.md", "template.md"}
    }
    paths.update(CONTRACT_DOC_ALIASES)
    return paths


def _claim_literal(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    """A SpecClaim argument's string value, following module-level constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _spec_claim_anchors() -> list[tuple[str, int, str, str, str]]:
    """Return ``(relative, lineno, claim_id, contract, section)`` per SpecClaim."""
    anchors: list[tuple[str, int, str, str, str]] = []
    for path in sorted((ROOT / "tests/specs").glob("spec_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # A third of the claims pass the contract as a module-level constant
        # (``_CONTRACT = "..."``), so literal-only extraction would miss them.
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "SpecClaim"):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            fields = []
            for index, name in enumerate(("claim_id", "contract", "section")):
                argument = node.args[index] if index < len(node.args) else keywords.get(name)
                fields.append(_claim_literal(argument, constants))
            claim_id, contract, section = fields
            if contract is None or section is None:
                continue
            anchors.append(
                (str(path.relative_to(ROOT)), node.lineno, claim_id or "?", contract, section)
            )
    return anchors


def _claim_section_present(section: str, document: str) -> bool:
    """Whether a claim's ``section`` names something the document actually has."""
    headings = [line for line in document.splitlines() if line.startswith("#")]
    match = _CLAIM_SECTION_NUMBER.match(section.strip().lstrip("§ "))
    if match:
        # Headings are written both as ``## 2 Foo`` and ``## §1 file location``.
        numbered = re.compile(rf"^#+\s*§?\s*{re.escape(match.group(1))}[.)\s:]")
        return any(numbered.match(heading) for heading in headings)
    word = section.strip().lstrip("§ ").split()[0].lower() if section.strip() else ""
    return bool(word) and any(word in heading.lower() for heading in headings)


def spec_claim_anchor_failures() -> list[str]:
    """Executable claims must name a contract and section that exist.

    ``docs/design/contracts/README.md`` already states this as a human duty --
    open the named Contract and the named section, and if the section does not
    exist you invented it -- and the duty has already been missed (claim
    ``LU-C07`` pointed at a ``§4b`` that never existed). Nothing read
    ``SpecClaim.section`` before this check, so a claim could name any string.

    Three mechanical rules:
      1. the ``contract`` name must be registered -- resolved from
         ``docs/design/contracts/``, aliased, or listed as a document-less
         namespace -- so no new invented name appears silently.
      2. a name must not resolve into ``docs/design/proposals/``. A Proposal
         describes behavior that does not exist yet; nothing in it is assertable.
      3. for a name that resolves to a document, ``section`` must find a heading.

    Non-goals (deliberately not validated here): whether the claim's assertion
    actually verifies what the section says, and whether every contract section
    has a claim. The second needs the section baseline at zero first, or it
    would emit hundreds of entries at once.
    """
    failures: list[str] = []
    if BASELINE_UNRESOLVED_CLAIM_SECTIONS or BASELINE_PROPOSAL_ANCHORED_CONTRACTS:
        if date.today() > CLAIM_ANCHOR_BASELINE_EXPIRY:
            failures.append(
                f"scripts/check_docs.py: claim-anchor baselines expired on "
                f"{CLAIM_ANCHOR_BASELINE_EXPIRY.isoformat()} and are not empty; "
                f"clear them or move the date deliberately"
            )
    paths = _contract_doc_paths()
    documents: dict[str, str] = {}
    for relative, lineno, claim_id, contract, section in _spec_claim_anchors():
        if contract in UNANCHORED_CONTRACT_NAMESPACES:
            continue
        path = paths.get(contract)
        if path is None:
            failures.append(
                f"{relative}:{lineno}: claim {claim_id} names unregistered contract "
                f"{contract!r}; add it to CONTRACT_DOC_ALIASES or "
                f"UNANCHORED_CONTRACT_NAMESPACES in scripts/check_docs.py"
            )
            continue
        if path.startswith("docs/design/proposals/"):
            if contract not in BASELINE_PROPOSAL_ANCHORED_CONTRACTS:
                failures.append(
                    f"{relative}:{lineno}: claim {claim_id} anchors {contract!r} on a "
                    f"Proposal ({path}); a Proposal describes behavior that does not "
                    f"exist yet, so nothing in it can be asserted"
                )
            continue
        if (relative, claim_id) in BASELINE_UNRESOLVED_CLAIM_SECTIONS:
            continue
        if path not in documents:
            documents[path] = (ROOT / path).read_text(encoding="utf-8")
        if not _claim_section_present(section, documents[path]):
            failures.append(
                f"{relative}:{lineno}: claim {claim_id} points at {contract} "
                f"{section!r}: no such heading in {path}"
            )
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


# Silent-failure-fallback baseline: pre-existing broad exception handlers
# whose entire body is a single falsy return/assignment, so a swallowed error
# is silently rendered as a plausible zero/empty value instead of "unavailable".
# Visible, dated (2026-09-26), never a silent skip; keep green on master but
# still catch NEW handlers. (relative_path, lineno) tuples of audited handlers.
# Zero known violations as of 2026-09-26 (R20 harvest): all 98 historical
# entries now return None / typed-unavailable / logged multi-statement bodies
# instead of a bare falsy literal.
#
# Narrow exemptions (kept per-site with reason):
# - (core/aletheia/storage/migrations.py, 2489) fts5_available(): the whole
#   file before "# MIGRATION_LEDGER_START" is checksum-frozen by
#   _legacy_baseline_implementation_checksum(), so ANY source edit — including
#   adding a log line — breaks the frozen migration ledger. The handler
#   returns False (FTS5 unsupported -> caller degrades) which is the correct
#   fail-closed probe result; the failure is intentionally silent because the
#   source line itself is the audit record. Fixing this would require a
#   migration-ledger exception, which is a larger change than the gate repair.
BASELINE_SILENT_FAILURE_FALLBACKS: set[tuple[str, int]] = {
    ("core/aletheia/storage/migrations.py", 2489),
}


def _falsy_literal(node: ast.AST) -> str | None:
    """Return a canonical repr of ``node`` if it is a falsy literal, else None."""
    if isinstance(node, ast.Constant):
        if node.value in (0, 0.0, False, "", (), b""):
            return repr(node.value)
        return None
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    if isinstance(node, ast.Tuple) and not node.elts:
        return "()"
    if isinstance(node, ast.Set) and not node.elts:
        return "set()"
    return None


def silent_failure_fallback_failures() -> list[str]:
    """Broad ``except`` handlers must not swallow an error into a falsy value.

    Scopes to Python source under ``core/aletheia/``. The bug class this guards:
    "couldn't read it" silently rendered as "it doesn't exist" -- a bare/``Exception``
    handler whose entire body is a single ``return 0`` (or other falsy literal) or
    ``value = 0``, converting a genuine failure into a plausible-looking value.
    Detected with ``ast``; a handler is flagged only when BOTH:
      1. it is broad -- ``except:``, ``except Exception``, or ``except BaseException``;
      2. ignoring any leading docstring, its body is exactly ONE statement that
         returns/assigns a falsy literal (``0``, ``0.0``, ``False``, ``""``, ``[]``,
         ``{}``, ``()``).

    Non-goals (deliberately not validated here, need dataflow or more context):
      - ``return None`` / ``value = None`` -- None is this repo's sanctioned
        "unavailable" signal, the opposite of the bug, and is never flagged;
      - ``pass``-only handlers -- a different smell (ignored error), out of scope;
      - ``raise`` / ``continue`` / ``break`` -- propagation / loop control;
      - multi-statement bodies that log or set a reason code before returning
        falsy -- deliberately skipped, they surface a signal;
      - narrow handlers like ``except (TypeError, ValueError):`` -- a targeted
        catch of an expected failure is idiomatic and would produce heavy noise;
      - any case needing dataflow to know whether the falsy value reaches a caller
        that treats it as real data.
    This check answers only the narrow literal pattern above. Files that fail to
    parse are reported (not silently skipped).
    """
    failures: list[str] = []
    source_root = ROOT / "core/aletheia"
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno}: silent failure fallback check: syntax error")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is not None:
                if not (
                    isinstance(node.type, ast.Name)
                    and node.type.id in ("Exception", "BaseException")
                ):
                    continue
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            if len(body) != 1:
                continue
            stmt = body[0]
            if isinstance(stmt, ast.Return):
                literal = _falsy_literal(stmt.value) if stmt.value is not None else None
            elif isinstance(stmt, ast.Assign):
                literal = (
                    _falsy_literal(stmt.value)
                    if len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    else None
                )
            else:
                literal = None
            if literal is None:
                continue
            if (str(relative), node.lineno) in BASELINE_SILENT_FAILURE_FALLBACKS:
                continue
            failures.append(
                f"{relative}:{node.lineno}: silent failure fallback: "
                f"broad except returns falsy {literal!r}"
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
    failures.extend(silent_failure_fallback_failures())
    failures.extend(skip_xfail_and_data_guard_failures())
    failures.extend(spec_claim_anchor_failures())
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
