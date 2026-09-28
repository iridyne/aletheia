# 19 · Batch × Executable Plans（屎山修复批处理）

> **历史方案，已停止按此派发（2026-09-28）**：Owner 要求由主 Agent 独立 review，并产出完整修复方案。下文的外派、立卡及 Wave 安排保留作历史背景，不是当前执行授权；具体问题必须按当前源码和 Contract 重核。当前进度见 [分析状态](analysis_status.md)。

> 日期：2026-09-28
> 前置文档：[17_shitmap_task_cards.md](17_shitmap_task_cards.md)（任务卡索引）+ 18a/18b/18c/18d（Phase 1 复核 brief）
> **性质：可执行计划文档。每个 Batch 是一份可直接分派的 executable plan；不含代码改动。**
> 使用方式：owner 拍板某个 Batch → 复制该 Batch 章节下发给指定档次的外部 Agent。

---

## 总览

| Batch | 主题 | 成员 issue | 分派档次 | 并行度 | 依赖 |
|---|---|---|---|---|---|
| **1** | 存储层泄露 + retention | #3642 + #3643 | deepseek | 单 PR 合做 | 无 |
| **2** | 跨段工具函数集中化 | #3523 | deepseek | 单 PR | 无 |
| **3** | 归因链 + 执行滑价三合一 | #3644 + #3618 + #3619 | codex+luna（#3644/#3618）· codex+astra（#3619） | 3 PR 分序 | 无 |
| **4** | 运行时兜底两合一 | #3645 + #3612 | codex+astra | 2 PR 分序 | 无 |
| **P1** | Phase 1 复核派发 | 18a/18b/18c/18d 4 份 brief | luna×3 · deepseek×1 | 4 份并行 | 无 |

**并行策略**：Batch 1/2/3/4 触及不同文件（存储投影 vs 跨段工具 vs 归因/execution vs scheduler/systemd），互不干扰，**可四条同时开跑**。P1 是纯读，与任何 Batch 无冲突。

**收口顺序建议**：Batch 2 最快（纯 mechanical）→ Batch 1（照 #3631 范式）→ Batch 4（scheduler 改动）→ Batch 3 分序合入 → P1 报告回收 → 决定 Batch 5+。

---

## Batch 1 · 存储层泄露 + retention

**成员**：[#3642](https://github.com/iridyne/aletheia/issues/3642)（`/api/universe/planning-history` 20.4MB / `planner_input_json` 92%）+ [#3643](https://github.com/iridyne/aletheia/issues/3643)（`candidate_planning_generations` 零 retention · 1.2MB/天）

**为什么合成一个 batch**：同一张表（`candidate_planning_generations`）、同一次 review、修法互补 —— #3642 治标（不让明细列返回），#3643 治本（不让表无界增长）。分开做 PR 是双倍 review 成本。

**分派档次**：deepseek（照 #3631 的 `PositionSummaryView` 范式复用，纯 mechanical）

**预算**：1 个 PR / ≤2 小时

### 序列

| Step | 做什么 | 依赖 | 产出 |
|---|---|---|---|
| 1 | 在 `core/aletheia/server/routes/` 里定位服务 `/api/universe/planning-history` 的路由函数，抄 #3631 范式声明 `PlanningHistorySummaryView`（对 `planner_input_json` / `requalification_source_json` / `result_json` / `validated_hypothesis_json` 打 `Field(default=None, exclude=True)`） | 无 | 服务响应从 20.4MB → 约 1MB（预估） |
| 2 | 把 `candidate_planning_generations` 加进 retention registry（找 registry 位置：`grep -rn "retention" core/aletheia/storage/`；模仿现有表的注册方式） | 无 | 表纳入 retention 循环 |
| 3 | 一并加验收测试：`tests/audit/` 下检查该表在 retention registry 有对应 policy | Step 2 | 未来加表漏 registry 会红灯 |
| 4 | PR body 里量化：before/after 响应字节数、retention policy 值 | 1-3 | 可 review |

### 给 Agent 的 prompt（可直接复制）

```
你要完成 GitHub issue #3642 + #3643（合并为一个 PR）。

前置阅读：
1. gh issue view 3642 && gh issue view 3643
2. #3631 的实现范式：core/aletheia/server/routes/data.py 附近的 PositionSummaryView（用于 /api/positions）
3. docs/research/audits/rival-2026-09-28/09_next_targets.md §1（#3631 的两个设计判断）
4. 记忆 [[feedback-dev-check-isnt-full-coverage-2026-09-26]]（改字符串前 grep tests/）

工作：
Step 1 · 定位 /api/universe/planning-history 路由，声明 PlanningHistorySummaryView
  对下列列打 Field(default=None, exclude=True)：
    - planner_input_json（19MB / 92%，主犯）
    - requalification_source_json
    - result_json
    - validated_hypothesis_json
  保留 planning_key、candidate_family 等轻量列。

Step 2 · 把 candidate_planning_generations 加进 retention registry
  先 grep "retention" core/aletheia/storage/ 找 registry 位置
  模仿一张已注册表的 policy 值（建议：保留 30 天，与其他明细表对齐）

Step 3 · tests/audit/ 加断言：candidate_planning_generations 必须在 retention registry
  参考已有 audit 门：tests/audit/test_alpha_cohort_reachability.py 的写法

Step 4 · PR body 附实测：
  before：curl -s <local>/api/universe/planning-history?limit=200 | wc -c
  after：同命令
  retention policy：<值>

纪律：
- 单 PR 承载两个 issue，body 用 "Fixes #3642, Fixes #3643"
- fast 测试全绿
- 不改 openapi_schema.py 之外的现有响应模型（新 SummaryView 是加，不是改）
- PR body 里量词（"the only"/"the last"）先 grep 验证

Attribution 按 CLAUDE.md 规则。
```

### 验收

- [ ] `/api/universe/planning-history?limit=200` 响应字节数下降至少一个数量级
- [ ] `candidate_planning_generations` 在 retention 循环里能被找到（grep 命中）
- [ ] `tests/audit/` 新增门跑通
- [ ] fast 全绿

### 阻拦 / Rollback

- 阻拦：无
- Rollback：revert PR；两条改动互不依赖，可分开 revert

---

## Batch 2 · 跨段工具函数集中化

**成员**：[#3523](https://github.com/iridyne/aletheia/issues/3523)（`_utcnow` ×18 / `_canonical_json` ×9 / `_table_exists` ×6 → 单一实现）

**为什么单独**：纯 mechanical refactor，不触及任何交易语义；但涉及 30+ 文件的 import 换名，最大风险是**改字符串打破测试断言**（记忆里有一次翻车教训）。

**分派档次**：deepseek

**预算**：1 个 PR / ≤3 小时

### 序列

| Step | 做什么 | 依赖 | 产出 |
|---|---|---|---|
| 1 | 新建 `core/aletheia/utils/time.py`：`utcnow() -> datetime` + `utcnow_iso() -> str`。**注意** `startup_behavior_gate.py:60` 用 `timespec='seconds'` 有独特精度需求，**不迁**（在报告里记录） | 无 | 单一 time 模块 |
| 2 | 新建 `core/aletheia/utils/json.py`：`canonical_json(payload) -> str`（sorted keys + compact separators） | 无 | 单一 canonical_json |
| 3 | 新建 `core/aletheia/storage/table_utils.py`：`table_exists(conn, name) -> bool`（带 None guard，见 #3541 hotfix） | 无 | 单一 table_exists |
| 4 | 18 处 `_utcnow` 换 import；9 处 `_canonical_json` 换 import；6 处 `_table_exists` 换 import | 1-3 | 33 处调用点收敛 |
| 5 | **关键**：`grep -rn "_utcnow\|_canonical_json\|_table_exists" tests/` 找齐所有 monkeypatch/mock，改成新 import 路径 | 4 | 测试不炸 |
| 6 | fast 全绿 | 5 | 可 review |

### 给 Agent 的 prompt

```
你要完成 GitHub issue #3523。

前置阅读：
1. gh issue view 3523（issue body 里 18 处 _utcnow 的精确 file:line 已列全）
2. 记忆 [[project-shit-cleanup-batch1-2026-09-26]]（#3541 hotfix 教训：table_exists(None) 要加 guard）
3. 记忆 [[feedback-dev-check-isnt-full-coverage-2026-09-26]]（改字符串前必须 grep tests/）

工作：
Step 1 · 建 core/aletheia/utils/time.py
  utcnow() -> datetime      # datetime.now(timezone.utc)
  utcnow_iso() -> str       # utcnow().isoformat()

Step 2 · 建 core/aletheia/utils/json.py
  canonical_json(payload: Any) -> str
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))

Step 3 · 建 core/aletheia/storage/table_utils.py
  table_exists(conn: sqlite3.Connection | None, name: str) -> bool
    if conn is None: return False   ← #3541 hotfix guard 必须带
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None

Step 4 · 逐个换 import
  18 × _utcnow：body 里列了所有 file:line，逐个改
  9 × _canonical_json：同
  6 × _table_exists：同
  例外：agent_runtime/startup_behavior_gate.py:60 用 timespec='seconds' 不迁
    在 PR body 说明理由

Step 5 · grep -rn "_utcnow\|_canonical_json\|_table_exists" tests/
  找齐所有 monkeypatch 和 mock，改成 patch 新路径
  例如：monkeypatch.setattr("aletheia.storage.models._utcnow", ...) → patch utils.time.utcnow_iso

Step 6 · 跑 fast 全套，全绿再交

纪律：
- 单 PR 一次做完（这是 mechanical 拆多 PR 反而增加 review 成本）
- 保留旧函数名不导出，避免 backward-compat 蔓延
- PR body 附：33 处调用点全部列出（before → after 一对一映射表）

Attribution 按 CLAUDE.md 规则。
```

### 验收

- [ ] `grep -rn "def _utcnow\|def _canonical_json\|def _table_exists" core extensions` 只剩 `startup_behavior_gate.py` 一处
- [ ] fast 全绿
- [ ] `tests/audit/test_no_silent_except_pass.py` baseline 不变

### 阻拦 / Rollback

- 阻拦：无
- Rollback：因为是 mechanical，revert 一次即可

---

## Batch 3 · 归因链 + 执行滑价三合一

**成员**：[#3644](https://github.com/iridyne/aletheia/issues/3644)（`opportunity_id` 恒 None → family=unknown）+ [#3618](https://github.com/iridyne/aletheia/issues/3618)（`slippage_cost` 恒 None）+ [#3619](https://github.com/iridyne/aletheia/issues/3619)（Bitget demo 止损无原生挂单，+0.82% 平均逆滑）

**为什么合成一个 batch**：这三个共同回答**同一个问题**——"亏损里多少是方向、多少是执行、方向侧又在哪一族"。单修任何一个信息量不完整：

- 只修 #3644 → 知道亏损归到哪个 mechanism 族了，**但不知道该族亏损有多少是执行拖累**
- 只修 #3618 → 能算出执行拖累的绝对值，**但不知道该族是不是本来就该亏**
- 只修 #3619 → 减少了执行拖累的绝对值，**但#3618 不修就无法度量减了多少**

**三者一起才能得出"该改哪个 mechanism 的哪个参数"这一有效结论。**

**分派档次 & 顺序**：
- **PR-A**（#3644）→ codex+luna，先做，因为它是"归因层"，其它两个 PR 的度量都要靠它归族
- **PR-B**（#3618）→ codex+luna，与 PR-A 并行（不同文件），补齐 slippage_cost 记录
- **PR-C**（#3619）→ codex+astra，PR-B 合入后做（否则无法度量收益）

**预算**：3 个 PR / ≤6 小时

### 序列

| Step | 做什么 | 谁 | 依赖 |
|---|---|---|---|
| A1 | 按 #3644 §6.2 的路线 B：在 proposal/position 上引入指回 `EntryPlan` 的键，让 `family_scorecard.py` 直读已有的 `candidate_family` | luna | 无 |
| A2 | 历史 24 笔样本回填 `candidate_family`（若成本可控；否则报告说明不做） | luna | A1 |
| A3 | PR-A 合入 | luna | A1-A2 |
| B1 | `pnl.py:997` 的 `slippage_cost = None` 改为**计算** slippage：`abs(realized_exit - expected_exit) / expected_exit`（具体口径按现有 fees_cost 的记账风格） | luna | 无（可与 A 并行） |
| B2 | 补测试：闭仓写入的行必须有非 None 的 `slippage_cost`（除非 `exit_price_source` 明示"无参考价"） | luna | B1 |
| B3 | PR-B 合入 | luna | B1-B2 |
| C1 | 在 Bitget demo 走单路径上，开仓成功后**同时**下 exchange-native TP/SL 挂单（用 CCXT 的 `create_order` params 或 Bitget 专属 endpoint，参照 #3619 body 里的证据锚点） | astra | 无 |
| C2 | 落 `exchange_tpsl_order_id` 到 position 行；平仓时如果是 TP/SL 触发的，`exit_price_source` 记 `exchange_stop` | astra | C1 |
| C3 | 与 PR-B 度量搭配：跑一遍 demo 冷启动 → 观察 `mean adverse slip` 是否从 +0.82% 降下来 | astra | B3+C1-C2 |
| C4 | PR-C 合入 | astra | C1-C3 |

### 给 Agent 的 prompt（三份分别复制）

#### PR-A prompt（luna，做 #3644）

```
你要完成 GitHub issue #3644。

前置阅读：
1. gh issue view 3644（body 里 §6.2 有精确的修复路线 B 分析）
2. docs/research/audits/rival-2026-09-28/09_next_targets.md §2.1 + §6.2
3. core/aletheia/execution/entry_plan_execution.py:360（症状点）
4. core/aletheia/analytics/family_scorecard.py:70-82（消费点）
5. core/aletheia/trading/entry_plan.py:182 + storage/models.py:1334（candidate_family 已有）

路线选择：body §6.2 已给出「路线 B」推荐 —— 引入指回 EntryPlan 的键，
让 family_scorecard 直读 candidate_family。**不要放宽 entry_plan_execution.py:360
的分支**，那条路会把问题从"恒 None"变成"恒查不中"，埋得更深。

工作要点：
- proposal/position 表加一个键（entry_plan_id 或 planning_generation_id，看哪个更省 migration）
- 迁移写 additive-only（记忆 [[master 被并发 rebase]]）
- family_scorecard.py 消费侧改成读新键
- 历史 24 笔样本能否回填 —— 若可，写一个一次性回填 script；若成本高，PR body 说明

验收：
- /api/analytics/trade-stats 的 by_family 不再是 100% unknown
- /api/analytics/alpha-attribution 五层至少 universe / ranker / system_operator 三层
  status 不再是 not_measured（若样本量仍不足，degrade 到 insufficient_sample 也算过）

纪律：
- 单 PR
- fast 全绿
- PR body 附 before/after 的 /api/analytics/trade-stats 输出对比

Attribution 按 CLAUDE.md 规则。
```

#### PR-B prompt（luna，做 #3618）

```
你要完成 GitHub issue #3618。

前置阅读：
1. gh issue view 3618（symptom：pnl.py:997 slippage_cost = None）
2. core/aletheia/portfolio/pnl.py 附近查 slippage_cost 的定义与消费者
3. docs/design/contracts/ 里 outcome / cost 相关契约
4. 已有 fees_cost / funding_cost 的记账风格（同文件里的邻居字段）

工作：
- pnl.py:997 计算 slippage：abs(realized_exit - expected_exit) / expected_exit
  expected_exit 来源：stop_price（stop_loss 平）/ take_profit_price（take_profit 平）/
    entry_price（其它情形，回落到"无法判定"→ 保持 None 并记 reason）
- 记 reason：新增 slippage_source 字段或复用 exit_price_source
- 补 tests/：闭仓写入必须有非 None slippage_cost，除非 reason 明示"无参考价"

验收：
- /api/analytics/cost-summary 的 missing_cost_counts.slippage_cost 从 24 降到接近 0
- 24 笔 demo 样本重跑 total_slippage 应有非零值

纪律同上。

Attribution 按 CLAUDE.md 规则。
```

#### PR-C prompt（astra，做 #3619，PR-B 合入后开始）

```
你要完成 GitHub issue #3619。前置：PR-B（#3618）已合入。

前置阅读：
1. gh issue view 3619（symptom：0/30 demo positions 有 exchange_tpsl_order_id）
2. Bitget demo CCXT adapter：extensions/exchanges/ 下找 bitget profile
3. 已有 exchange_order_id 的下单路径（作为对照）
4. docs/design/contracts/execution_ 相关契约

工作：
- 开仓成功后（不是并入开仓订单），追加下一笔 exchange-native TP/SL 挂单
  用 CCXT create_order 的 params={'stopLossPrice': ..., 'takeProfitPrice': ...}
  或 Bitget 专属 endpoint（参照文档确认 demo 环境支持）
- 落 exchange_tpsl_order_id 到 position 行
- 平仓时判定：如果是 TP/SL 触发的，exit_price_source = "exchange_stop"
- 兼容失败：交易所拒挂 TP/SL 时不能阻塞开仓；记 warning，回落到 local-poll

验收：
- demo 冷启动一段时间后 /api/positions 里 exchange_tpsl_order_id 非空比例 > 90%
- 与 PR-B 联动：新样本的 mean adverse slip 从 +0.82% 显著下降
- 已有 stop_loss 路径的 caplog 兜底继续存在（不是替换是补充）

纪律同上。

Attribution 按 CLAUDE.md 规则。
```

### Batch 3 整体验收

- [ ] `/api/analytics/trade-stats` `by_family` 分布不再 100% `unknown`
- [ ] `/api/analytics/cost-summary` `total_slippage` 非 null
- [ ] `/api/positions` `exchange_tpsl_order_id` 非空比例 > 90%（新开仓）
- [ ] `/api/analytics/alpha-attribution` 至少 3 层 `status ≠ not_measured`

### 阻拦 / Rollback

- 阻拦：PR-A 与 PR-B 无相互阻拦，可并行；PR-C 必须等 PR-B（否则无法度量收益）
- Rollback：三个 PR 各自可 revert；PR-A 的 migration 是 additive-only（记忆 [[master 被并发 rebase]] 约束），revert 时不删列

---

## Batch 4 · 运行时兜底两合一

**成员**：[#3645](https://github.com/iridyne/aletheia/issues/3645)（同步 job 无墙钟上界）+ [#3612](https://github.com/iridyne/aletheia/issues/3612)（systemd 无 `WatchdogSec`）

**为什么合成一个 batch**：#3645 body 自己写明"与 #3612 同一主题的内层" —— #3612 是"整个进程卡死没人重启"，#3645 是"单车道卡死"。两个合起来才是"进程与车道任一卡住都能被发现"的完整闭环。

**分派档次**：codex+astra（涉及 scheduler + systemd unit + 资金安全组合风险）

**预算**：2 个 PR / ≤4 小时

### 序列

| Step | 做什么 | 依赖 | 产出 |
|---|---|---|---|
| A1 | `scheduler.py:718` 的 `result = fn()` 加墙钟上界包装（参考同文件 `_run_async_with_timeout` 的做法，用 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=...)`） | 无 | 同步 job 有超时 |
| A2 | 各车道 job 注册时声明超时值；`protective` 车道更严（60s？），`maintenance_io` 更宽（600s？）。具体值 owner 拍或参照现有异步路径的 grace | A1 | 差异化超时 |
| A3 | 超时触发后 log + 加入 `tests/audit/test_scheduler_*`（如有）的门禁；不 kill worker（避免脏状态），只让 APScheduler 下轮 `misfire_grace_time` 接管 | A1-A2 | 可观测 |
| A4 | PR-A 合入 | A1-A3 | — |
| B1 | `session_service.py:591-602` `_build_systemd_unit()` 加 `WatchdogSec=<值>`（建议 60s） | 无（可与 A 并行） | unit 有 watchdog |
| B2 | `_gateway_runner.py:791` 的 ready-beacon 之外，加一个定期心跳循环调 `_emit_sd_notify("WATCHDOG=1")`（间隔 = `WatchdogSec / 3`，即 20s） | B1 | 循环发心跳 |
| B3 | 心跳循环失败时的行为：不假装 healthy，让 watchdog 触发 systemd `Restart=on-failure` | B2 | fail-closed |
| B4 | PR-B 合入 | B1-B3 | — |

### 给 Agent 的 prompt（两份）

#### PR-A prompt（做 #3645）

```
你要完成 GitHub issue #3645。

前置阅读：
1. gh issue view 3645
2. core/aletheia/agent_runtime/scheduler.py:655-672（车道定义 + 注释）
3. core/aletheia/agent_runtime/scheduler.py:718（症状点）
4. core/aletheia/agent_runtime/scheduler.py:800（_run_async_with_timeout 已有的异步范式）
5. core/aletheia/agent_runtime/scheduler.py:868-896（#3432 遗留的 async grace）

工作：
- 把 :718 的 result = fn() 包装成有墙钟上界的执行
  用 ThreadPoolExecutor + future.result(timeout=<lane_wall_clock_sec>)
- 各车道声明各自的上界（在 :655-672 附近的车道定义扩一个字段 wall_clock_sec）
  protective: 60
  fast_io: 30
  io: 120
  maintenance_io: 600
  audit: 300
  observer: 300
  agents: 300
  （具体值 owner 说了算，PR body 请他确认或提议）
- 超时不 kill worker（防脏状态），只 log critical + 让 APScheduler misfire_grace_time 接管
- tests/：造一个 sleep(10^6) 的 fake job，验证 60s 内被超时接住

纪律：
- 单 PR
- 与 #3612（PR-B）互不阻拦，可并行做
- fast 全绿

Attribution 按 CLAUDE.md 规则。
```

#### PR-B prompt（做 #3612）

```
你要完成 GitHub issue #3612。

前置阅读：
1. gh issue view 3612
2. core/aletheia/gateway/session_service.py:591-602（_build_systemd_unit）
3. core/aletheia/_gateway_runner.py:791（ready-beacon）
4. core/aletheia/_gateway_runner.py:770-778（_emit_sd_notify docstring — "written once per lifetime"）
5. core/aletheia/_gateway_runner.py:802-827（_emit_sd_notify 当前实现，只发 READY=1）

工作：
Step 1 · session_service.py:591-602 的 unit 加 WatchdogSec=60（建议值）

Step 2 · _gateway_runner.py 加一个循环任务：
  - 间隔 20 秒（WatchdogSec / 3）
  - 每次调用 _emit_sd_notify("WATCHDOG=1")
  - 循环体本身可以简单，但必须由主事件循环调度（不能在被卡的车道上）

Step 3 · _emit_sd_notify 扩接受参数：READY=1 / WATCHDOG=1 / STATUS=<msg>
  - 现在只发 READY=1，扩成 dispatch 逻辑
  - grep 全仓 WATCHDOG=1 / WatchdogSec 应该从 0 命中 → 若干命中

Step 4 · 失败语义：
  - 心跳循环任何异常都不吞
  - 让 watchdog 超时触发 systemd Restart=on-failure（这是本 issue 要修的最终行为）

验收：
- 在 Linux 上用 systemd-analyze verify 通过
- 手动 kill -STOP <pid> 模拟卡死后 systemd 能在 <2 min 内重启进程（本机可能没法测；PR body 给出验证步骤即可）
- fast 全绿

纪律：单 PR，与 PR-A 无冲突，可并行。

Attribution 按 CLAUDE.md 规则。
```

### Batch 4 整体验收

- [ ] `grep -rn "WATCHDOG=1" core/` 有命中
- [ ] `systemd-analyze verify` 通过
- [ ] scheduler 单元测试造的"死循环 job"在 60s 内被超时接住
- [ ] fast 全绿

### 阻拦 / Rollback

- 阻拦：无
- Rollback：两个 PR 独立 revert；PR-B 的 unit 改动是 additive，revert 后旧 unit 继续能启动

---

## P1 · Phase 1 复核派发

**成员**：18a/18b/18c/18d 四份 audit-only brief

**为什么单列**：这不是 batch（batch 是"合并做"），是"并行派"。四份 brief 各自独立、互不干扰，可四条同时开跑。

**分派**：

| Brief | 主题 | 档次 | 预算 |
|---|---|---|---|
| [18a](18a_brief_learning_20gap_revalidation.md) | Learning 20-gap 存活性 | codex+luna | ≤2 hour |
| [18b](18b_brief_admission_gap345_revalidation.md) | Admission GAP-3/4/5 | deepseek | ≤1 hour |
| [18c](18c_brief_segment6_top5_scan.md) | 段 6 top-5 扫描 | codex+luna | ≤3 hour |
| [18d](18d_brief_run_coro_repro.md) | `_run_coro` 复现 | codex+luna | ≤2 hour |

### 派发指令（复制到每份 brief）

```
你要按照 docs/research/audits/rival-2026-09-28/<brief 文件名>.md 完成一次
audit-only 复核。

硬约束（brief 里已写，再强调一次）：
- 不改任何代码
- 不 gh issue create（只把 issue 骨架写在报告里）
- 不改任何已有 issue（不 edit body / 不 close / 不 comment）
- 产出物是一份 markdown 报告，放到 brief 指定的输出路径

回收：完成后把报告路径回传 owner。
```

### P1 回收后的动作（不属于 P1 本身）

- 收齐 4 份报告 → owner review → 一次性 `gh issue create` 批量开 STILL_HOLDS 的卡
- 若某份报告发现存量 issue 需要更新 body（比如 #157 已过时）→ owner 决定是否 `gh issue edit`
- 报告本身也 commit 进仓（放 `docs/research/audits/`）

---

## 分派矩阵（一眼看谁做什么）

| Agent | 承接 |
|---|---|
| **deepseek** | Batch 1 · Batch 2 · 18b brief |
| **codex+luna** | Batch 3 PR-A · Batch 3 PR-B · 18a brief · 18c brief · 18d brief |
| **codex+astra** | Batch 3 PR-C · Batch 4 PR-A · Batch 4 PR-B |

**并发上限**：owner 之前实测过一次 Wave 3 并行（[[feedback-orchestrator-wave-pattern]]）；本次一次最多同时启动 3-4 个不同文件的任务，超过就等第一波合入。

**推荐 Wave 编排**：

- **Wave 1**（并行 4 条）：Batch 1（deepseek）+ Batch 2（deepseek）+ Batch 3 PR-A（luna）+ Batch 3 PR-B（luna）
- **Wave 2**（Wave 1 合入后）：Batch 4 PR-A（astra）+ Batch 4 PR-B（astra）+ Batch 3 PR-C（astra）
- **P1 派发**：任何 Wave 期间并行进行（纯读，与 code 改动无冲突）

---

## 不在本文档范围

- **表 B1** 未拆巨型函数（7 个）→ 等 [#3561](https://github.com/iridyne/aletheia/issues/3561) 解锁 spec 断言，再单独开卡
- **表 B2** 段 3 D1 敏感位点（reconciliation / coordinator / gateway 权威路径 6 处）→ 每一处需要 owner 单独批 D1，不能聚 batch
- **Batch 5/6/7/8** → P1 复核完成后再决定形态

---

## 关联

- 任务卡索引：[17_shitmap_task_cards.md](17_shitmap_task_cards.md)
- Phase 1 brief：18a/18b/18c/18d
- 主线：[16_alpha_mechanism_comparison.md](16_alpha_mechanism_comparison.md)
- 记忆：[[project-shit-cleanup-batch1-2026-09-26]]（上一轮 batch1 的 12 PR 经验）· [[feedback-orchestrator-wave-pattern]]（Wave 分工）
