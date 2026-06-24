# Round 2 第一周 — AI 决策链修复 checklist

> **本文件作用**:把冻结期(到 Round 1 判决结束)亲核出的 4 个 AI 决策链 bug 固化下来,
> Round 2 解冻当天直接照表施工。每个 fix 都有对应的 RED spec 测试,
> 修改完代码即可 GREEN,不需重新分析根因。
>
> **创建时间**:2026-06-24
> **解冻条件**:Round 1 判决完成(参见 `docs/product/EXPERIMENT_PROTOCOL.md`)
> **前置准备**:`tests/specs/spec_learning_pipeline.py` + `tests/specs/spec_strategy_pool.py`
> 在冻结期已写好 RED 测试(本会话 commit)

---

## 背景

冻结期内对 3 个子系统(Serenity/Learning/Strategies)的影响审计发现:
**写入活跃,消费断路** — 数据每天在产,但喂给 PI 的过滤把数据全挡在门外。

亲核数字(2026-06-24):
- knowledge_items 总 3819 行,24h 写入 797 行(含 system_action 1204 / post_trade_review 1679 /
  trade_case_study 496 / research_note 240 / guard_candidate 81 / dreaming_log 62 /
  strategy_memory 20 / operator_assessment 19 / daily_review 14 / reasoning_feedback 3 /
  trading_memo 3)
- PI 实际看到的 lessons: **0 条**(过滤集合 `('lesson','accepted_lesson','trading_guard')`
  在 DB 中 0 命中)
- PI 实际看到的 guards: **0 条**(limit=30 窗口被 system_action 占满,28 个 system_action +
  1 operator_assessment + 0 guard_candidate)
- ps_lessons: 184 条,**完全没有消费代码路径**
- strategy_records: PI 拿到 win_rate/total_pnl/trade_count **全是 None/`?`**
  (DDL 17 列里没这三列)

这是 Round 1 期间 AI 决策质量的天花板。修复后,Round 2 应能产出"学习增益"的 A/B 对比。

---

## 4 个核心修复

### Fix L-Bug-A — Layer3 lessons 过滤值对齐 DB 实际 item_type

**位置**:`aletheia/agent_shell/trading_context.py:379` (函数 `_build_layer3_wisdom()`)

**当前代码(冻结)**:
```python
items = list_knowledge_items(limit=20)
lessons: list[dict] = []
for item in items:
    if item.get("item_type") in ("lesson", "accepted_lesson", "trading_guard"):
        lessons.append({...})
```

**问题**:DB 中 `lesson` / `accepted_lesson` / `trading_guard` 三类都是 0 行(从未有过生产者写入)。
真正的 lesson 内容存在 `post_trade_review`(1679) / `trade_case_study`(496) 类型中。
过滤值与生产者完全不重叠,等于这条路径**永空**。

**修复**(Round 2 解冻当天):
```python
items = list_knowledge_items(item_type='trade_case_study', limit=20)
# 或者改为多次 query 拿 post_trade_review + trade_case_study 都取
```

注意 `list_knowledge_items(item_type=...)` 已支持参数(`repositories.py:516`),只需传过去。

**对应 RED 测试**:`tests/specs/spec_learning_pipeline.py::test_layer3_lessons_filter_aligned`
(AST 解析 `_build_layer3_wisdom`,确认过滤集合与 DB 真实 item_type 至少 1 个重叠)

---

### Fix L-Bug-A' — Layer3 guards 查询加 item_type 参数

**位置**:`aletheia/agent_shell/trading_context.py:398`(同一函数 `_build_layer3_wisdom`)

**当前代码(冻结)**:
```python
items = list_knowledge_items(limit=30)   # ← 全表扫,limit=30 被 system_action 占满
guards: list[dict] = []
for item in items:
    if item.get("item_type") == "guard_candidate":
        guards.append({...})
```

**问题**:`_select_all` 默认 `ORDER BY rowid DESC LIMIT 30`,取最新 30 条。
24h 内 system_action 写入 ~1200 条 + post_trade_review ~218 条,
**limit=30 窗口里几乎全是这两类**,81 条 guard_candidate 完全排不进。

**修复**(Round 2 解冻当天,**一行**):
```python
items = list_knowledge_items(item_type='guard_candidate', limit=30)
```

**对应 RED 测试**:`tests/specs/spec_learning_pipeline.py::test_layer3_guards_filter_via_query`
(AST 检查这一行的 call 必须包含 keyword `item_type='guard_candidate'`)

---

### Fix L-Bug-B — ps_lessons 接入决策链

**位置**:`aletheia/agent_shell/trading_context.py` + `aletheia/agent_shell/trading_decision.py`(都冻结)

**当前现状**:
- `ps_lessons` 表 184 条 `status='proposed'`,最新写入 `2026-06-23T05:54 UTC`
- 整个 `aletheia/` 中**没有任何 SELECT FROM ps_lessons** 的代码
- dreaming_agent 在产 lesson 但 PI 完全看不见

**修复**(Round 2 解冻当天):
1. 在 `_build_layer3_wisdom()` 新增一段 ps_lessons 拉取(类似 serenity_opportunities 那段):
   ```python
   try:
       from aletheia.storage.repositories import list_ps_lessons  # 若不存在则先在 repositories.py 加
       lessons = list_ps_lessons(status='proposed', limit=5)
       layer["ps_lessons"] = [
           {"summary": l.get("summary"), "severity": l.get("severity"),
            "category": l.get("category")} for l in lessons
       ]
   except Exception:
       layer["ps_lessons"] = []
   ```
2. 在 `trading_decision.py` 拼 PI prompt 时新增章节(类似 "Research-sourced Opportunities (from Serenity agent)"):
   ```python
   ps_lessons = layer3.get("ps_lessons", [])
   if ps_lessons:
       parts.append("## Recent Post-trade Lessons")
       for l in ps_lessons:
           parts.append(f"  [{l['severity']}] {l['category']}: {l['summary']}")
   ```

**对应 RED 测试**:`tests/specs/spec_learning_pipeline.py::test_ps_lessons_consumed_in_prompt`
(AST 检查 `trading_decision.py` 出现 ps_lessons 引用,且 prompt 拼接里有相应章节)

---

### Fix S-Bug-C — strategy_records 暴露 win_rate / total_pnl / trade_count

**位置**:`aletheia/agent_shell/trading_context.py`(冻结)

**当前现状**:
- `strategy_records` DDL 17 列:`id, name, description, state, parameters, created_at, updated_at,
  under_review, review_notes, review_updated_at, paper_trading_paused, paper_pause_reason,
  paper_paused_at, direction_affinity, applicable_regimes, strategy_type, execution_venue`
- **没有 win_rate / total_pnl / trade_count**
- PI prompt 拿到的策略池里这三个字段全是 None / `?`
- 真实绩效在 `backtest_runs`(7d 41 条,含 profit_factor / sharpe / equity_curve_json)

**正确的修复入口**:`aletheia/analytics/trade_stats.py:227 build_strategy_kpis()` 已经返回
`{strategy_id: {trade_count, win_rate, total_pnl}}`(来自 fills + positions 实时计算,
不是 backtest_runs)。Round 2 只需在 `_build_layer2_strategies()`(在 `trading_context.py` 里读
`list_strategies()` 那段)JOIN 一下:

```python
from aletheia.analytics.trade_stats import build_strategy_kpis
kpis = build_strategy_kpis()
for s in strategies:
    sid = s.get("id")
    if sid and sid in kpis:
        s["win_rate"] = kpis[sid].get("win_rate")
        s["total_pnl"] = kpis[sid].get("total_pnl")
        s["trade_count"] = kpis[sid].get("trade_count")
```

> **重要**:综合 agent 之前提过 `per_strategy_kpi_summary()` —— **不存在**。
> 实际函数名是 `build_strategy_kpis()`,在 `aletheia/analytics/trade_stats.py:227`。
> 这是亲核结果,记在这里以免 Round 2 解冻时再踩坑。

**对应 RED 测试**:`tests/specs/spec_strategy_pool.py::test_strategy_pool_exposes_kpi`
(DB 验证:从 `trading_context._build_layer2_strategies()` 路径返回的策略 dict 必须含
非 None 的 win_rate / total_pnl / trade_count)

---

## 修复执行顺序

1. **Fix L-Bug-A'**(guards 加 item_type 参数,**一行**)— 最容易,先验证 spec 能 GREEN
2. **Fix L-Bug-A**(lessons 过滤值对齐)— 同一函数,顺手做
3. **Fix S-Bug-C**(strategy JOIN kpi)— 跨 file 改动,需运行测试确认 PI prompt 真拿到数字
4. **Fix L-Bug-B**(ps_lessons 接入)— 跨两 file + 需新增 `list_ps_lessons()` 仓库函数,最复杂

每修一个,跑对应 spec 应当从 RED 变 GREEN。

---

## 解冻后立即可做(冻结期内是 indirect-risk,Fix L-Bug-A' 完成后才 safe)

- `prune_system_actions` TTL 7d:加进 `_run_cleanup` 白名单(`scheduler.py`)
  - **冻结期为何不能做**:虽然 7d TTL 不动新数据(我们亲测当前 system_action 1211/1211 都在 7d 内),
    现在做不会改 PI 窗口,理论上 safe。**但**风险大于收益:一旦 prune 误清新数据(配置错、时区错),
    会改 PI 输入。Round 2 等 A' 修复后,PI 已不依赖这个窗口,prune 才彻底无风险。

---

## 验收标准(Round 2 修复完后)

1. `tests/specs/spec_learning_pipeline.py` 全 GREEN(3 个测试)
2. `tests/specs/spec_strategy_pool.py` 全 GREEN(1 个测试)
3. `/api/analytics/learning-funnel` 端点显示:PI limit=30 窗口中 guard_candidate 数 >= 5
4. doctor 探针 `loop.knowledge_flood`(若已实现)从 warn 变 ok
5. PI prompt 抽样检查:出现 `## Recent Post-trade Lessons` + `## Strategy Pool` 内含真实数字

---

## 反思 — 这份 plan 的可信度

本 plan 中**所有 file:line 与函数名都已在 2026-06-24 会话中亲自 grep 验证**:
- `_build_layer3_wisdom()` 内 `list_knowledge_items` 两处调用确在 L379/L398 ✓
- `build_strategy_kpis()` 确在 `trade_stats.py:227`,返回 dict[strategy_id] ✓
- `list_strategies()` 确在 `repositories.py:502` ✓
- `list_knowledge_items(item_type=..., limit=N)` 函数签名支持 item_type 参数 ✓

如果 Round 2 解冻时发现 file:line 偏移,先 grep 确认真位置再改 —— 不要凭这份 plan 的字面行号。
