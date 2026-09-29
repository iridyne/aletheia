"""Alpha cohort reachability — a cohort anchor must have a live producer.

## 这一刀查的是什么

`aletheia opportunity audit --view alpha` 的每一层都要先把「决策侧」和「结果侧」
连起来。2026-09-27 在 Fedora 实例上五层全部返回 `insufficient_data` / `sample_size=0`，
而库里并不缺数据（`filter_candidate_outcomes` 有 5,795 条合格的 24h cost-aware 结果）。
追下去发现两个层的 cohort **根本没有生产者**：

- `Ranker Alpha` 读 `shadow_trades`，而该表唯一的写入者 `persist_shadow_batch`
  (`extensions/counterfactual/trades.py:46`) 在生产侧的最后一个调用者
  (`agents/paper_trader.py`) 已在 `2e69baae9` 删除 —— 表变成了墓碑。
- `System Operator Alpha` 需要 `filter_decisions` 里
  `candidate_kind='pi_decision' AND stage='system_operator'` 的行，而这些行只由
  `record_operator_filter_trace` (`analytics/filter_evidence.py:1015`) 写入，
  该函数在生产侧**零调用者**。

单元测试看不见这一类缺陷，因为它们自己把 join 两侧都写进夹具：夹具自洽，缺生产者与
缺保留窗口都无法暴露（`tests/test_alpha_attribution.py` 30/30 全绿即是此故）。这正是
`SDD_METHODOLOGY.md:96-100` 说的「只证明函数、类、字符串或局部单元测试存在，不足以把
claim 标为 implemented……无法跨过生产入口的实现按 dead code 或 Partial 处理」。

## 检查项

1. **anchor 可达性**：每个未退役 cohort 的 anchor 表必须存在写入函数，且该函数在
   `tests/` 之外至少有一个调用者。
2. **保留期登记一致性**：`_RETENTION_DAYS` 必须与 `scheduler.py` 实际的
   `prune_*(...)` 参数一致（`r.<attr>` 按 `config/schema.py` 的默认值解析），且每个被
   join 的表都必须在册。2026-09-27 的 `filter_candidate_outcomes`(30d) 早于
   `filter_decisions`(45d) 被清理，就是这一类的隐患：join 的 anchor 先被删。

纯静态：只做 AST/正则，不 import 被测模块、不开数据库、不碰网络。
`test_*_self_check_*` 是负向自检，证明这两项检查真的会失败（照搬 R20
`tests/audit/test_circuit_breaker_twin_parity.py` 的自我否证模式）。

## 已知盲区（不要假装它覆盖了）

检查项 1 是**表级**的：它问「这张表有没有活着的行生产者」。2026-09-27 的两个真实缺陷里，
它只能抓住 Ranker Alpha（`shadow_trades` 的生产者 `insert_shadow_trade` 从生产入口不可达，
已用自检复现）。System Operator Alpha 的缺口在**行的子集**上——表有活的生产者，缺的是
`candidate_kind='pi_decision' AND stage='system_operator'` 这一批行——表级检查看不到它，
所以那一层只能靠 `_COHORTS` 里的显式豁免记录，豁免理由里写明了这一点。

判定的两个已知近似：
- 可达性按**函数名**建图，跨模块同名函数会带来假阳性（可能把不可达判成可达）。因此
  「可达」可能是误报，「不可达」可靠——这正是检查需要的方向。
- `_ENTRY_PREFIXES` 是手写的生产入口面。新增入口面（例如新的运行时宿主）需要同步维护，
  否则会误报不可达。

Refs:
- `docs/design/contracts/opportunity_outcome_contract.md:124`、
  `docs/design/contracts/filter_evidence_contract.md:60,315`
- #3569（本门禁的验收项）、#3572（`shadow_trades` 静默停写）
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import re

import pytest

from tests.audit._segment_guard import ROOT

_SCAN_ROOTS = ("core", "extensions", "scripts")


@dataclass(frozen=True)
class Cohort:
    """One alpha layer's cohort anchor and the tables joined against it."""

    anchor: str | None
    joined: tuple[str, ...] = ()
    retired: str = ""
    note: str = ""


_COHORTS: dict[str, Cohort] = {
    "Universe Alpha": Cohort(anchor="filter_candidate_outcomes"),
    "Ranker Alpha": Cohort(
        anchor="shadow_trades",
        joined=("filter_candidate_outcomes",),
        retired=(
            "no producer: sole writer persist_shadow_batch's last production caller "
            "(agents/paper_trader.py) was deleted in 2e69baae9 (#2945)"
        ),
    ),
    "System Operator Alpha": Cohort(
        anchor="filter_decisions",
        joined=("filter_candidate_outcomes", "pi_decisions"),
        retired=(
            "PRODUCER GAP AT PREDICATE LEVEL, which this table-level gate cannot "
            "detect: the required rows come only from record_operator_filter_trace "
            "(analytics/filter_evidence.py:1015, zero production callers), and the "
            "predicate also needs the retired signal_source='market_observer'. The "
            "exemption is declared here precisely because the automatic check cannot "
            "reach a row-subset gap — see the module docstring's blind-spot note"
        ),
    ),
    "Learning Alpha": Cohort(
        anchor=None,
        note="reads persisted lab measurements through a helper; no SQL table of its own",
    ),
    "Serenity Alpha": Cohort(anchor="serenity_opportunities"),
}

# Retention the gate expects, in days. `None` = never pruned by the scheduler.
# Every table any cohort joins must appear here, so adding a join to an unregistered
# table is itself a red gate.
_RETENTION_DAYS: dict[str, int | None] = {
    "shadow_trades": 90,
    "filter_decisions": 45,
    "filter_candidate_outcomes": 45,
    "pi_decisions": 7,
    "serenity_opportunities": None,
}

_PRUNE_CALL = re.compile(r"repo\.prune_(?P<fn>[a-z_]+)\((?P<args>[^)]*)")
_SCHEMA_DEFAULT = re.compile(
    r"^\s+(?P<name>[a-z_]+_days):\s*int\s*=\s*(?P<value>\d+)", re.M
)
_LITERAL = re.compile(r"^\s*(\d+)\s*$")
_ATTR = re.compile(r"^\s*r\.([a-z_]+)\s*$")


def _resolve_retention(scheduler_src: str, schema_src: str) -> dict[str, int | None]:
    """Retention of each table the scheduler prunes.

    The table name is the prune function's own name (``prune_pi_decisions`` prunes
    ``pi_decisions`` even though its scheduler key is ``pi_decisions_transient``).
    A table absent from the result is never pruned by the scheduler.
    """
    defaults = {
        m.group("name"): int(m.group("value")) for m in _SCHEMA_DEFAULT.finditer(schema_src)
    }
    out: dict[str, int | None] = {}
    for match in _PRUNE_CALL.finditer(scheduler_src):
        first = match.group("args").split(",", 1)[0]
        literal = _LITERAL.match(first)
        attr = _ATTR.match(first)
        if literal:
            out[match.group("fn")] = int(literal.group(1))
        elif attr and (value := defaults.get(attr.group(1))) is not None:
            out[match.group("fn")] = value
    return out


def _functions(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _called_names(source: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


# Production entry surfaces: anything these drive is reachable in a running
# system. A one-hop "has a non-test caller" check is not enough — it accepts an
# internal wrapper (`persist_shadow_batch` calls `insert_shadow_trade`) whose own
# last production caller was deleted, which is exactly the Ranker defect.
_ENTRY_PREFIXES = (
    "core/aletheia/agent_runtime/",
    "core/aletheia/server/",
    "core/aletheia/cli/",
    "core/aletheia/gateway/",
    "core/aletheia/main.py",
    "scripts/",
)


def _function_calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if isinstance(sub.func, ast.Name):
            names.add(sub.func.id)
        elif isinstance(sub.func, ast.Attribute):
            names.add(sub.func.attr)
    return names


def _reachable_from_entrypoints(sources: dict[str, str]) -> set[str]:
    """Function names callable, transitively, from a production entry surface.

    Edges are per *function body*, not per file: a file-wide call set over-connects
    the graph so badly that every name becomes "reachable" (measured: 6,922 names).

    Name-based, therefore conservative in one direction only: a name collision can
    make the set larger, never smaller, so "reachable" may be a false positive while
    "unreachable" is reliable.
    """
    callees: dict[str, set[str]] = {}
    frontier: set[str] = set()
    for rel, src in sources.items():
        is_entry = rel.startswith(_ENTRY_PREFIXES)
        for node in _functions(src):
            callees.setdefault(node.name, set()).update(_function_calls(node))
            if is_entry:
                frontier.add(node.name)
    reachable = set(frontier)
    while frontier:
        next_frontier: set[str] = set()
        for name in frontier:
            for callee in callees.get(name, ()):
                if callee not in reachable:
                    next_frontier.add(callee)
        reachable |= next_frontier
        frontier = next_frontier
    return reachable


def _writer_functions(source: str, table: str) -> set[str]:
    """Names of functions whose own body *produces rows* in ``table``.

    Only row-creating writes count. ``UPDATE``-only helpers cannot grow a cohort,
    and a pruner is called on a schedule while writing nothing — accepting either
    would let a tombstone table pass the reachability check. The invariant is "has
    a producer", not "is touched".
    """
    patterns = (
        re.compile(rf"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+{table}\b", re.I),
        re.compile(rf"\bREPLACE\s+INTO\s+{table}\b", re.I),
        re.compile(rf"\b_insert(?:_many)?\(\s*[\"']{table}[\"']"),
    )
    names: set[str] = set()
    for node in _functions(source):
        if node.name.startswith("prune_") or node.name.startswith("_delete"):
            continue
        segment = ast.get_source_segment(source, node) or ""
        if any(p.search(segment) for p in patterns):
            names.add(node.name)
    return names


def iter_repo_sources(roots: tuple[str, ...] = _SCAN_ROOTS) -> dict[str, str]:
    sources: dict[str, str] = {}
    for base in roots:
        for path in (ROOT / base).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                sources[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return sources


def unreachable_anchors(cohorts: dict[str, Cohort], sources: dict[str, str]) -> list[str]:
    """Cohorts whose anchor table has no writer reachable from production."""
    storage = {
        rel: src for rel, src in sources.items() if "/storage/" in rel and rel.startswith("core/")
    }
    reachable = _reachable_from_entrypoints(sources)
    failures: list[str] = []
    for layer, cohort in cohorts.items():
        if cohort.anchor is None or cohort.retired:
            continue
        writers: set[str] = set()
        for src in storage.values():
            writers |= _writer_functions(src, cohort.anchor)
        if not writers:
            failures.append(
                f"{layer}: no function anywhere in core storage writes anchor table "
                f"{cohort.anchor!r}"
            )
        elif not (writers & reachable):
            failures.append(
                f"{layer}: anchor table {cohort.anchor!r} is written by {sorted(writers)} "
                "but none of those is reachable from a production entry surface — the "
                "cohort is structurally unmeasurable"
            )
    return failures


def retention_failures(
    cohorts: dict[str, Cohort], retention: dict[str, int | None]
) -> list[str]:
    failures: list[str] = []
    for layer, cohort in cohorts.items():
        tables = ((cohort.anchor,) if cohort.anchor else ()) + cohort.joined
        for table in tables:
            if table not in retention:
                failures.append(
                    f"{layer}: table {table!r} is joined but absent from "
                    "_RETENTION_DAYS — state its retention (or None) before joining it"
                )
    return failures


def retention_registry_failures(
    declared: dict[str, int | None], observed: dict[str, int | None]
) -> list[str]:
    """``None`` means "never pruned", so it is only consistent with absence."""
    failures: list[str] = []
    for table, days in declared.items():
        actual = observed.get(table)
        if days is None:
            if actual is not None:
                failures.append(
                    f"{table!r}: registry says never pruned, scheduler prunes at "
                    f"{actual} days"
                )
        elif actual is None:
            failures.append(
                f"_RETENTION_DAYS lists {table!r} but scheduler.py has no prune call for "
                "it — the registry has gone stale"
            )
        elif actual != days:
            failures.append(
                f"{table!r}: registry says {days} days, scheduler prunes at {actual} days"
            )
    return failures


@pytest.mark.fast
def test_every_cohort_anchor_has_a_live_producer() -> None:
    failures = unreachable_anchors(_COHORTS, iter_repo_sources())
    assert not failures, (
        "alpha cohort reachability: a layer joins an anchor table that nothing in "
        "production produces, so its verdict can never be anything but "
        "insufficient_data. Give the cohort a producer, or declare it retired in "
        "_COHORTS with the reason (see #3569).\n  " + "\n  ".join(failures)
    )


@pytest.mark.fast
def test_retention_registry_covers_every_joined_table() -> None:
    failures = retention_failures(_COHORTS, _RETENTION_DAYS)
    assert not failures, "\n  " + "\n  ".join(failures)


@pytest.mark.fast
def test_retention_registry_matches_the_scheduler() -> None:
    observed = _resolve_retention(
        (ROOT / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8"),
        (ROOT / "core/aletheia/config/schema.py").read_text(encoding="utf-8"),
    )
    failures = retention_registry_failures(_RETENTION_DAYS, observed)
    assert not failures, (
        "retention in scheduler.py and the alpha-cohort registry have diverged; a "
        "silently shortened window empties live cohorts.\n  " + "\n  ".join(failures)
    )


def test_self_check_a_writer_called_only_from_tests_is_reported() -> None:
    """Negative control: an anchor written only for tests must be reported."""
    writer = 'def insert_orphan(row):\n    _insert("orphan", row)\n'
    probe = {"Probe Alpha": Cohort(anchor="orphan")}
    reachable = {
        "core/aletheia/storage/repositories.py": writer,
        "core/aletheia/cli/ops.py": "def run():\n    insert_orphan({})\n",
    }
    assert unreachable_anchors(probe, reachable) == []
    orphaned = {
        "core/aletheia/storage/repositories.py": writer,
        "tests/test_probe.py": "def test_x():\n    insert_orphan({})\n",
    }
    failures = unreachable_anchors(probe, orphaned)
    assert failures, "self-check: an anchor written only for tests must be reported"
    assert "structurally unmeasurable" in failures[0]


def test_self_check_an_internal_wrapper_does_not_count_as_reachability() -> None:
    """Negative control for the real Ranker shape.

    ``insert_shadow_trade`` did have a non-test caller — the internal wrapper
    ``persist_shadow_batch`` — whose own last production caller had been deleted.
    A one-hop caller check accepts that; forward reachability must not.
    """
    probe = {"Probe Alpha": Cohort(anchor="anchor_x")}
    sources = {
        "core/aletheia/storage/repositories.py": (
            'def insert_row(row):\n    _insert("anchor_x", row)\n'
        ),
        "extensions/wrapper.py": "def persist_batch(rows):\n    insert_row(rows)\n",
        "core/aletheia/cli/ops.py": "def unrelated():\n    return 1\n",
    }
    failures = unreachable_anchors(probe, sources)
    assert failures, "self-check: an unwired internal wrapper must not satisfy reachability"
    assert "structurally unmeasurable" in failures[0]
    # Wire the wrapper into a production surface and it becomes reachable.
    sources["core/aletheia/cli/ops.py"] = "def run():\n    persist_batch([])\n"
    assert unreachable_anchors(probe, sources) == []


def test_self_check_a_no_writer_at_all_is_reported() -> None:
    probe = {"Probe Alpha": Cohort(anchor="missing_table")}
    failures = unreachable_anchors(
        probe, {"core/aletheia/storage/repositories.py": "def other():\n    pass\n"}
    )
    assert failures == [
        "Probe Alpha: no function anywhere in core storage writes anchor table "
        "'missing_table'"
    ]


def test_self_check_flags_a_shortened_retention_window() -> None:
    """Negative control: a retention change in scheduler.py must be reported."""
    scheduler = (
        '"filter_candidate_outcomes": (\n'
        "    repo.prune_filter_candidate_outcomes(\n"
        "        r.filter_candidate_outcomes_days,\n"
        "    )\n"
        "),\n"
    )
    observed = _resolve_retention(scheduler, "    filter_candidate_outcomes_days: int = 12\n")
    assert observed == {"filter_candidate_outcomes": 12}
    failures = retention_registry_failures({"filter_candidate_outcomes": 45}, observed)
    assert failures and "12 days" in failures[0]


def test_self_check_flags_an_unregistered_join() -> None:
    probe = {"Probe Alpha": Cohort(anchor="anchor_x", joined=("unregistered_y",))}
    failures = retention_failures(probe, {"anchor_x": 30})
    assert failures and "unregistered_y" in failures[0]
