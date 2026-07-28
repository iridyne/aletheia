# Round 2 第一周 — AI 决策链修复 checklist

> **本文件作用**:把冻结期(到 Round 1 判决结束)亲核出的 AI 决策链 bug 固化下来,
> Round 2 解冻当天直接照表施工。每个 fix 都有对应的 RED spec 测试,
> 修改完代码即可 GREEN,不需重新分析根因。
>
> **创建时间**:2026-06-24
> **扩充时间**:2026-06-27(并入全生命周期审计 `docs/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md`
> 的总根因 R0 / guard 三重死锁 / 唯一活闭环喂幻觉 — 见下方 Fix L-Bug-C / L-Bug-D / C 类系统性重构)
> **解冻条件**:Round 1 判决完成(参见 `docs/product/experiments/EXPERIMENT_PROTOCOL.md`)
> **前置准备**:`tests/specs/spec_learning_pipeline.py`(6 个 RED 测试)+ `tests/specs/spec_strategy_pool.py`
> 在冻结期已写好 RED 测试,且已并入 `test_spec_conformance._import_all_specs()` 收集(修了 SDD 收集陷阱)
>
> **本会话(2026-06-27)已落地的非冻结部分**(无需等 Round 2):
> 1. SDD 收集陷阱修复 — `spec_learning_pipeline` / `spec_strategy_pool` 现已被 conformance 收集
> 2. 补 2 个 RED claim:LP-S05(opportunity_id 总根因)/ LP-S06(guard 升级死路)
> 3. doctor 新增只读探针 `loop.retrieval_pool` + `loop.guard_promotion`(`diagnostics/doctor.py`)
>    — 实测当前 review:case = 118.2:1、105 candidate / 0 promoted,两条都正确报 warn
> ~~下方所有 Fix 仍是 **Round 2 解冻当天** 才能动的代码(全部落在冻结路径)。~~
>
> **✅ 2026-07-04 收口:下方 6 个核心修复已全部完成。**
> 冻结已于 2026-06-27 解除,批量修复于同日落地(`b9ad9574`),L-Bug-D 于 `bd91eda8` 落地。
> 仅 C 类系统性重构(§末尾)仍 deferred。

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

### Fix L-Bug-A — Layer3 lessons 过滤值对齐 DB 实际 item_type ✅ 完成 (`b9ad9574`)

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

### Fix L-Bug-A' — Layer3 guards 查询加 item_type 参数 ✅ 完成 (`b9ad9574`)

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

### Fix L-Bug-B — ps_lessons 接入决策链 ✅ 完成 (`b9ad9574`)

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

### Fix S-Bug-C — strategy_records 暴露 win_rate / total_pnl / trade_count ✅ 完成 (`b9ad9574`)

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

## 全生命周期审计追加修复(2026-06-27 并入)

> 来源:`docs/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md`。上面 A/A'/B/S-C 解决的是
> **消费侧断路**(数据进不了 PI 视野);下面解决的是**归因链源头被切断**——
> 数据即便进得来也是 unknown 噪音。这两条比上面 4 个优先级更高:它们是总根因。

### Fix L-Bug-C — 透传真实 opportunity_id(总根因 R0,单一最高杠杆) ✅ 完成 (`b9ad9574`)

**位置**:`aletheia/agent_shell/trading_decision.py:250-251`(`create_paper_proposal(...)` 调用,冻结)

**当前代码(冻结)**:
```python
create_paper_proposal(
    ...,
    strategy_id=None,        # ← 硬编码
    opportunity_id=None,     # ← 硬编码,切断归因链的那一刀
)
```

**问题(全链连锁)**:`market_observer` 早已把 `scanner_family` / `universe` / `confidence`
写进 opportunity 行,但 PI Agent 在下单这一步把 `opportunity_id` 丢成 `None`,
之后**无任何回填**。一处 None 让下游 4 类 learning artifact 同时坏死:
- `trade_case_study` 的 entry-context 全 `unknown` → `has_full_chain=False` →
  永远升不到 high_confidence+active(实测干净案例仅 16 条 vs review 1891 条)
- `reasoning_feedback` 的 scanner 恒 `unknown`(`decision_meta` 里根本没有 `scanner_family` 键)
- guard 谓词恒空(无 factor_key 可锚定)
- `post_trade_review` 无法按策略/scanner 分组聚合

**修复**(Round 2 解冻当天):
1. 在 `_assess_opportunity` / 决策路径里把当前 opportunity 的真实 `id` 一路带到下单点
2. `create_paper_proposal(opportunity_id=opp["id"], strategy_id=<resolved or None>)`
3. 在 `decision_meta`(`trading_decision.py:225-233`)补 `scanner_family` 键,
   供 `reasoning_feedback` 归因

> **冻结判定**:透传 id 会改变 `has_full_chain` → 改变哪些 item 升 high_confidence →
> 改变检索池 → 改变 ranker 排序 → **改变决策**,因此严格属于冻结范围,不能在 Round 1 内做。
> (审计报告 §7 曾讨论"A2 钉住 confidence=medium 让其变 safe"的折中,但仍改 has_full_chain
> 与归因输出,保守起见统一推到 Round 2。)

**对应 RED 测试**:`tests/specs/spec_learning_pipeline.py::test_proposal_carries_opportunity_id`
(AST 检查 `create_paper_proposal` 调用**不得**出现 `opportunity_id=None` 常量)

---

### Fix L-Bug-D — guard 升级链可达性(三重死锁 → 0 升级) ✅ 完成 (`bd91eda8`)

**位置**:`aletheia/knowledge/guards.py`(本身不在冻结列表,但 `load_trading_guards`
被冻结的 `paper_trader_ranker` 调用,故归 Round 2 统一处理)

**当前现状(三重死锁,全实测)**:
- **B0** `config.yaml` `auto_promote_enabled=false`(实测值;流转 agent 曾误判"开关开着",
  那是 schema 默认 True 的幻觉 —— 真实配置是关的)
- **B2** 升级器 `load_trading_guards(status='candidate')` 读 `data/knowledge/guards/`,
  但候选写在 `data/knowledge/guard_candidates/`,且前者目录根本不存在
  (`guards.py:97` 路径 vs `markdown.py:59` 写入路径不一致)
- **B3** 104~105 条候选的 markdown 缺 `predicates` / `status` / `guard_id` frontmatter,
  即便目录对上也无法解析成可激活 guard

**修复**(Round 2 解冻当天,三处都要动,缺一仍是 0 升级):
1. `guards.py` 的 `load_trading_guards` 读取目录改为 `guard_candidates/`(或两目录都扫)
2. 候选生成处补全 `predicates` / `status='candidate'` / `guard_id` frontmatter
3. 评估后再决定是否把 `auto_promote_enabled` 打开(开关是策略决定,不是 bug 修复)

**对应 RED 测试**:`tests/specs/spec_learning_pipeline.py::test_guard_candidates_reachable_by_promoter`
(检查 `guards.py` 源码引用 `guard_candidates` 目录;对应审计 断点 B2/B3)

---

### C 类 — 系统性重构(比一行修复大,需独立设计,Round 2 之后排期)

> 上面的 L-Bug-A..D 是"接好断掉的线";下面是"线接好后数据本身仍是噪音"的治本项。
> 不要在解冻当天顺手做——每一条都需要单独的设计 + spec。详见审计报告 §6 C 类。

1. **重构 post_trade_reviewer 的输入与提示**(幻觉发源地):
   当前 ctx 只有 `Fills:N; orders:M`(`post_trade_reviewer` 输入,无 PnL),提示却问
   "execution quality" → LLM 只能编 VWAP / 全球路由等股票台术语。
   修复 = 喂真实 PnL / 持仓时长 / 进出价,提示改问真实可答的问题。
   **唯一活闭环正是这条**(post_trade_review → FTS → ranker `_lesson_penalty`),
   所以它产的幻觉直接污染选单 —— 优先级仅次于 L-Bug-C。
2. **重构 `_lesson_penalty`**(`paper_trader_ranker.py:400`,冻结):
   当前字面匹配 `loss/failed/stopped/drawdown` 扣分(最高 -25),但这些词是幻觉模板的
   小节标题(Risk/drawdown metrics),BM25 切到哪段随机 → 不可控虚假惩罚。
   修复 = 要么从池里剔除 `post_trade_review`,要么改为基于结构化字段而非全文匹配。
3. **risk_critic 输入扩容**:当前只有 3 个计数标量 → 只能反复"批准率太高、橡皮图章"。
4. **dreaming `_extract_lessons` 接真实交易数据**:当前产 240 条 ps_lessons 全是幻觉源;
   且 `risk/engine.py:309` 可被 accepted ps_lesson block 交易,目前全 `proposed`/0 `accepted`
   是休眠炸弹 —— 人工 accept 即激活,需在接真实数据后才允许。
5. **存储治理**:把 1890 条幻觉 `post_trade_review` archive,避免长期污染检索池。

---

## 修复执行顺序

**先治本(归因链源头),再接消费侧** —— 否则消费侧接好的仍是 unknown 噪音:

1. **Fix L-Bug-C**(透传 opportunity_id,总根因)— 最高杠杆,先做;做完 `trade_case_study`
   才会开始积累干净的 high_confidence 案例,后续所有 spec 的 DB 断言才有真数据
2. **Fix L-Bug-D**(guard 升级链三处)— 与 C 独立,可并行
3. **Fix L-Bug-A'**(guards 加 item_type 参数,**一行**)— 最容易,先验证 spec 能 GREEN
4. **Fix L-Bug-A**(lessons 过滤值对齐)— 同一函数,顺手做
5. **Fix S-Bug-C**(strategy JOIN kpi)— 跨 file 改动,需运行测试确认 PI prompt 真拿到数字
6. **Fix L-Bug-B**(ps_lessons 接入)— 跨两 file + 需新增 `list_ps_lessons()` 仓库函数,最复杂
7. **C 类系统性重构**(reviewer/ranker/risk_critic/dreaming/存储)— 单独设计,不在解冻当天

每修一个,跑对应 spec 应当从 RED 变 GREEN。**注意**:L-Bug-C 之前,A/A'/B 即便修了,
DB 里仍是 unknown 数据 —— 所以 C 的优先级在最前。

---

## 解冻后立即可做(冻结期内是 indirect-risk,Fix L-Bug-A' 完成后才 safe)

- `prune_system_actions` TTL 7d:加进 `_run_cleanup` 白名单(`scheduler.py`)
  - **冻结期为何不能做**:虽然 7d TTL 不动新数据(我们亲测当前 system_action 1211/1211 都在 7d 内),
    现在做不会改 PI 窗口,理论上 safe。**但**风险大于收益:一旦 prune 误清新数据(配置错、时区错),
    会改 PI 输入。Round 2 等 A' 修复后,PI 已不依赖这个窗口,prune 才彻底无风险。

---

## 验收标准(Round 2 修复完后)

1. `tests/specs/spec_learning_pipeline.py` 全 GREEN(6 个测试:LP-S01..S06;
   注意 LP-S04 是结构锚点,本就 GREEN,其余 5 个从 xfail 翻 pass)
2. `tests/specs/spec_strategy_pool.py` 全 GREEN
3. `create_paper_proposal` 后抽查 `trade_case_study`:entry-context 不再 `unknown`,
   且开始有 `has_full_chain=True` 的新行升到 high_confidence
4. `/api/analytics/learning-funnel` 端点显示:PI limit=30 窗口中 guard_candidate 数 >= 5
5. doctor 探针从 warn 变 ok:
   - `loop.knowledge_flood`(L-Bug-A' 后)
   - `loop.retrieval_pool` review:case 比从 118:1 收敛(L-Bug-C 后 + C 类 archive 后)
   - `loop.guard_promotion` 出现 active trading_guard(L-Bug-D 后)
6. PI prompt 抽样检查:出现 `## Recent Post-trade Lessons` + `## Strategy Pool` 内含真实数字

---

## 反思 — 这份 plan 的可信度

本 plan 中**所有 file:line 与函数名都已亲自 grep 验证**:

2026-06-24 验证:
- `_build_layer3_wisdom()` 内 `list_knowledge_items` 两处调用确在 L379/L398 ✓
- `build_strategy_kpis()` 确在 `trade_stats.py:227`,返回 dict[strategy_id] ✓
- `list_strategies()` 确在 `repositories.py:502` ✓
- `list_knowledge_items(item_type=..., limit=N)` 函数签名支持 item_type 参数 ✓

2026-06-27 追加验证(全生命周期审计):
- `create_paper_proposal(strategy_id=None, opportunity_id=None)` 确在 `trading_decision.py:250-251` ✓
- `decision_meta`(`trading_decision.py:225-233`)确实不含 `scanner_family` 键 ✓
- 实测 DB:high_confidence+active 池 = `post_trade_review` 1891 / `trade_case_study` 16(118:1)✓
- 实测 DB:`guard_candidate` 105 条 / active `trading_guard` 0 条 ✓
- `auto_promote_enabled=false`(实测 config,非 schema 默认)✓
- 流转 agent 曾误判"开关开着"——**已更正**,真值是关的(见审计报告 §7 更正记录)

如果 Round 2 解冻时发现 file:line 偏移,先 grep 确认真位置再改 —— 不要凭这份 plan 的字面行号。
归因链总根因是 L-Bug-C 那**一行**(opportunity_id=None),其余都是它的下游症状。
