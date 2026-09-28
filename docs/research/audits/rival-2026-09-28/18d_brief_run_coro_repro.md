# 18d · Audit Brief · `_run_coro` 跨 loop bug 复现（表 B3）

> **待核历史 brief（2026-09-28）**：尚无本项完成报告；不再外派，不按下文 Agent 档次或 Wave 自动执行。后续由主 Agent 核对当前源码与契约，产出问题解决方案。当前状态见 [分析状态](analysis_status.md)。

> 分类：**audit-only + failing-test，不修 bug**
> 建议 Agent 档次：**codex+5.6-luna**（历史 grep + 异步复现）
> 预算：≤2 小时
> 产出位置：`docs/research/audits/RUN_CORO_CROSS_LOOP_REPRO_2026-09-28.md` + 一个 xfail 或 skip 标注的失败测试
> 结束前**不修 bug**，只交付复现证据 + fix 方向建议

## 背景

记忆 `project_minimum_profit_loop_shit_map_2026-09-26.md` 记录：
> 段 3 的 `_run_coro` ×3 里，历史 `DECISIONS.md §2026-06-24 §1` 明确记过它跨 loop 的 bug，
> 是**已知的技术债**。清理有明确背景。

**问题**：记忆只写"已知"，没写复现步骤。直接立"修 bug"的卡，外部 Agent 会无从下手。
所以要先**做出复现**，再谈修法。

## 你要做的

### Step 1 · 精确定位 3 个 `_run_coro` 出现位点

在段 3（`core/aletheia/execution/`、`core/aletheia/risk/`）搜索 `_run_coro`，
产出 file:line × 3 + 每处的调用上下文（caller + 传入的 coroutine 类型）。

### Step 2 · 查 DECISIONS 历史

读 `docs/governance/decisions/DECISIONS.md` 找 §2026-06-24 §1（或附近日期），
提取当时对这个 bug 的原文描述——bug 触发条件、影响面、当时的处置理由。

### Step 3 · 构造复现

给出**能确定性触发**跨 loop 问题的最小场景：

- 是"两个 asyncio loop 并发存活时"？
- 是"loop 被 close 后又被引用"？
- 是"`asyncio.run()` 嵌套调用"？
- 是"跨线程 loop 通信"？

复现产物二选一：
- **首选**：一个 `tests/` 下的 `test_run_coro_cross_loop_repro.py`，标 `@pytest.mark.xfail(reason="known cross-loop bug, see <report>")` 或 `@pytest.mark.skip(reason="repro only, do not run in CI")`
- **次选**：一个独立 `scripts/repro_run_coro_cross_loop.py`，跑起来能稳定抛出跨 loop 相关 exception（`RuntimeError: <coroutine> attached to a different loop` 之类）

### Step 4 · 修法方向（不实施）

列 2-3 个可能的 fix 路线，每路线给：
- 一句话方案
- Blast radius（要改哪几处）
- D 级（D0/D1/D2）
- 已知阻拦（有没有既有测试锁住当前行为）

### Step 5 · 判定是否立卡

- 若复现成功 → 建议立卡（附 title/labels/body 骨架）
- 若复现失败（"我做不出稳定复现"）→ 报告说明，建议归档 DECISIONS 那条为"已淡出"

## 产出结构

```markdown
# `_run_coro` Cross-Loop Bug Reproduction · 2026-09-28

## 一句话结论
COMPLETE | INCOMPLETE_BUT_LOCATED | REFUTED_ON_REPRO

## §1 位点定位
| # | file:line | caller | coroutine 类型 |
|---|---|---|---|
| 1 | ... | ... | ... |

## §2 DECISIONS §2026-06-24 §1 原文摘录
<原文引用 + link>

## §3 复现路径
<步骤 + 环境 + 触发条件>

## §4 交付物
- 测试文件：`tests/test_run_coro_cross_loop_repro.py`（xfail/skip 标记）
- 或脚本：`scripts/repro_run_coro_cross_loop.py`
- 实际输出（异常 stack trace 附前后 10 行）

## §5 修法方向（不实施）
### 路线 A · <方案>
- Blast radius
- D 级
- 阻拦
### 路线 B / C
（同）

## §6 建议 issue 骨架（若复现成功）
- title: `[execution][async] _run_coro cross-loop bug — 3 sites, DECISIONS §2026-06-24 §1`
- labels: `bug`, `area/execution`, `severity/<按复现严重度>`
- body 3-5 段：现象、复现命令、影响、修法路线摘要、依赖

## §7 若复现失败
- 说明为什么复现不出（loop 语义已改？caller 已经不用这条路径？）
- 建议在 DECISIONS 加一条 "SUPERSEDED / could not reproduce on <SHA>" 注释
```

## 硬约束

- ❌ **不修 bug**（哪怕看出来了）——本 brief 只到复现 + 建议
- ❌ 不 `gh issue create`——只给骨架
- ❌ 不改 DECISIONS.md（只读）
- ✅ 测试标 `xfail` 或 `skip`，绝不能让复现测试进 CI 变成红灯
- ✅ 复现失败也算完成——诚实说做不出比强行造复现好

## 参考

- 记忆：[[project-minimum-profit-loop-shit-map-2026-09-26]]（§单个 PR 屎山信号密度）
- DECISIONS.md §2026-06-24 §1（历史记录）
- 段 3 主入口：`execution/coordinator.py`、`execution/gateway.py`、`execution/reconciliation.py`
