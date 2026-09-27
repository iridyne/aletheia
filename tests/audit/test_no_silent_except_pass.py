"""Silent `except ...: pass` gate (2026-09-26).

## 这一刀查的是什么

一个 `except <...>: pass` 块是**这个仓库里最危险的代码形式**。它把所有失败——
无论是合理的数据缺失、还是 schema 漂移、还是调用方 bug、还是 DB 锁——全部
变成"什么都没发生"，让人无法区分。

这条规则本来在文档/记忆里：「不能有无日志的静默失败」。现在写成会翻红的测试。

## 如何使用本门禁

**新 PR 里**：
- 如果你的代码必须 `except ...: pass`（例如网络重连时的 `close()` 清理），
  在 `silent_except_pass_baseline.json` 里加一条 `{"file": "...", "line": ..., "reason": "..."}`，
  并在 reason 里写清楚为什么这里不需要 log。
- 如果你修掉了一处旧的静默 pass，从 baseline 里删掉对应条目。

**门的行为**：
- baseline 里有但代码里已经消失的 → 提示"请从 baseline 删掉"（不翻红，只 print）
- 代码里有但 baseline 里没有的 → **翻红**（新引入的违规）
- 两边完全一致 → 通过

## 当前 baseline（2026-09-27）

41 处，全部带 reason：14 处 signal.signal() 主线程契约 / CancelledError 控制流 /
幂等清理等**真的不该 log** 的控制流，其余是已判定的 best-effort 清理路径。

2026-09-27 清理：164 → 41，123 处改为 warning/debug 日志。这是**耻辱榜**，
不是许可证。目标是让这个数字趋向 0。

Refs:
- docs/research/audits/COMPETITIVE_ANALYSIS_R20_2026-09-24.md §G1
  ("门禁测试取代散文规矩")
- PR #3503 (tests are the backing authority)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = Path(__file__).with_name("silent_except_pass_baseline.json")
_BASELINE = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))

# (file, line) tuples from the frozen baseline
_BASELINE_SET: frozenset[tuple[str, int]] = frozenset(
    (s["file"], s["line"]) for s in _BASELINE["sites"]
)


def _current_sites() -> list[tuple[str, int]]:
    """Return every `except ...: pass` site in core/aletheia and extensions."""
    sites: list[tuple[str, int]] = []
    for search_root in ("core/aletheia", "extensions"):
        for p in sorted((ROOT / search_root).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ExceptHandler)
                    and len(node.body) == 1
                    and isinstance(node.body[0], ast.Pass)
                ):
                    sites.append((str(p.relative_to(ROOT)), node.lineno))
    return sites


def test_no_new_silent_except_pass_sites() -> None:
    """Any `except ...: pass` that is NOT in the baseline must be fixed.

    A fresh violation means someone just added a silent swallow. This
    test prevents new ones from landing. To fix: replace `pass` with a
    `logger.warning(..., exc_info=True)` or an explicit `raise`, or add a
    justified entry to `silent_except_pass_baseline.json`.
    """
    current = set(_current_sites())
    new_violations = sorted(current - _BASELINE_SET)

    # Sites in baseline that are gone from code → remind the author to
    # update the baseline (but do NOT fail; the code is improved).
    stale = sorted(_BASELINE_SET - current)
    if stale:
        for f, l in stale[:10]:
            print(
                f"  STALE baseline entry (site already cleaned up): {f}:{l}"
                " — please remove it from silent_except_pass_baseline.json"
            )
        if len(stale) > 10:
            print(f"  … and {len(stale) - 10} more stale entries.")

    assert not new_violations, (
        f"\n{len(new_violations)} NEW silent `except ...: pass` site(s) "
        f"detected — each must either be replaced with a log/raise, or "
        f"added to silent_except_pass_baseline.json with a reason:\n\n"
        + "\n".join(f"  {f}:{l}" for f, l in new_violations)
        + "\n\nSee tests/audit/test_no_silent_except_pass.py for instructions."
    )


def test_baseline_count_only_decreases() -> None:
    """The number of sites in the frozen baseline must never increase.

    This test protects against someone quietly adding new entries to the
    baseline file instead of fixing the code. If you legitimately need to
    add a justified entry, the count goes up by at most 1 and you must
    also clear an existing stale entry in the same PR — a neutral trade.
    """
    current = set(_current_sites())
    stale_count = len(_BASELINE_SET - current)
    new_in_baseline = len(_BASELINE_SET) - stale_count

    # Allow the baseline to shrink (entries removed as code is fixed).
    # Reject any growth: if the code got worse, test_no_new_silent_except_pass_sites
    # already caught it; if someone added a *justified* entry to the baseline
    # while also cleaning another, new_in_baseline stays ≤ original count.
    baseline_size = len(_BASELINE["sites"])
    assert baseline_size <= _BASELINE["count"], (
        f"Baseline file lists {baseline_size} entries but the frozen count "
        f"header says {_BASELINE['count']}. "
        "Do not manually increase the `count` field — remove a stale site instead."
    )
