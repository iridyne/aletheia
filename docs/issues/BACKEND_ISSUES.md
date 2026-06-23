# Backend Issues — 代码层结构性问题清单

> 本文件记录后端**已声明但未接通 / 自相矛盾 / 与 trust 不变量隐性冲突**的具体
> 问题。**与 `docs/references/north_star.md` 不同**：北极星是"方向地图"（与
> OpenAlice/Hermes 等参考系对照后的范式差距，长期参考），本文件是"屋内问题
> 清单"——具体 bug、contract 偏移、dead code、audit 漏写——随代码状态变化频繁。
>
> 每条都给出 `file:line` 佐证、影响等级、最小修复建议。**不构成施工授权**——
> 同 AGENTS.md 的红线：先稳跑、再分项授权；🔴 项动工前需显式授权 + 焦点回归测试。
>
> **图例**：
> - 🔴 影响信任不变量（trust_and_change_control §1 因果链 / §3 UI Honesty / §6 凭证安全 / §1.4 audit）
> - 🟠 让数据失真或学习失效（promotion gate 错算 / KB 类型混淆 / PnL 不可信）
> - 🟡 dead code / cosmetic / future-violation tripwire
>
> **最后更新**：2026-06-18 · 维护者：Aletheia Dev
> **来源**：6 个并行 subagent 审计（agents+runtime / risk+execution / storage+knowledge /
> server+config / lifecycle+gateway / model+telegram+update+cli）+ 早期对照表
>
> **状态**：
> ✅ **§1（🔴 信任不变量）全部已修复**（2026-06-18 逐条代码验证确认）。详见 §10.1。
> ✅ **§2（🟠 数据失真）全部已修复**（详见 §10.2）。最后 3 条于 2026-06-18 修复：§2.5 AllowEditing 标 deprecated / §2.6 create_strategy_memory 补原子回滚 / §2.8 credentials_path 剥离。
> ✅ **§4（2026-06-13→2026-06-14 修复）结构性发现已全部处理**。
> ✅ **§5（OAuth Provider）已于 2026-06-14 全部修复**（04e0938 + model_metadata 补全）。
> ✅ **§6（数据层技术债）2026-06-16**：§6.1-6.2 已修复，§6.3-6.4 已知限制暂不修。
> ✅ **§12（WebUI 后端缺口）全部已修复**（2026-06-18 验证）。

---

## 0. 阅读顺序与使用方式

按等级倒序读：先看 §1（🔴），再看 §2（🟠），最后 §3（🟡）。§4（2026-06-13→2026-06-14 已修复）。
按修复时机：每条注明的"最小修复"是 hotfix 级；若涉及 schema/迁移/跨模块，先开 plan。
按归档：修好的条目移到 §10 历史区（保留 file:line 与修复 commit）。

---

## 1. 🔴 影响信任不变量

### 1.1 因果链 / 晋级机制

- **observer→trader→lifecycle 的 `strategy_id` 因果链整段断裂**（合并自原两条独立条目）
  - 佐证（上游断点）：`storage/models.py:51` 定义了 `Opportunity.linked_strategy_id` 字段、`migrations.py:59` 列在，但 `market_observer.py:77-85` 创建 opportunity 时不填，grep 全仓 0 处 `update_opportunity` 写入该字段
  - 佐证（下游断点）：`agents/paper_trader.py:91-101` 调 `create_paper_proposal` 仅传 `opportunity_id`，无 `strategy_id`——即使想传也没源头
  - 影响：因果链 `proposal → order → fill → position` 全部丢 strategy_id；`list_strategy_positions(strategy_id)` 永远拿不到 paper trade（`strategy_lifecycle.py:151`）；observer / designer / paper_trader 三个独立 cron 之间**没有数据流**让 opportunity 绑定 strategy。**直接后果**：promotion gate 的 `min_paper_trades=30` 与 `min_paper_runtime_days=14` 永远不触发——晋级机制处于"形式存在、永不触发"
  - 最小修复：`market_observer` 创建 opportunity 时若关联 active strategy 写 `linked_strategy_id`，否则 None；`paper_trader` 透传该字段进 proposal；端到端补一个回归测试断言 strategy_id 全链路保留

- **§5.5 max_drawdown gate 使用 `or 1.0` fallback，把容量 0 退化成 100% drawdown**
  - 佐证：`trading/strategy_lifecycle.py:261` `capital_base = load_config().risk.max_live_limited_cap_usdt or 1.0`；trust §5.5 严格要求使用配置值，未规定 fallback
  - 影响：任何把 `max_live_limited_cap_usdt` 设为 0（关闭 live 容量）的部署，drawdown=PnL×100%，立即 `max_drawdown_exceeded`——晋级失败原因是数学伪影
  - 最小修复：fallback 改为 fail `insufficient_drawdown_capital_base`，或读 `risk.starting_capital_usdt` 之类显式字段

- **§5.3 `min_paper_trades` 错把未成交 order 也计入**
  - 佐证：`strategy_lifecycle.py:169` `paper_trade_count = len(fills) if fills else len(orders)`；trust §5.3 明确"strategy-linked paper/demo **filled** orders, or fills"——必须 filled
  - 影响：反复创建却不成交的策略，靠 30 个 pending/cancelled order 即可越过 30 笔门
  - 最小修复：`list_strategy_orders` 加 `status='filled'` 过滤；或在 169 行前 `orders = [o for o in orders if o['status'] == 'filled']`

- **§5.8 post_trade_review gate 实际只接受 strategy-specific，无 spec 兜底**（与 §2.2 post_trade_reviewer 互为因果）
  - 佐证：`strategy_lifecycle.py:153-156, 202-203` 只查 `list_knowledge_items(item_type="post_trade_review", strategy_id=strategy_id, ...)`；trust §5.8 明确"MVP 期间一个全局 active review 即可"
  - 影响：与 §2.2 的"post_trade_reviewer 抢绑首条命中 strategy + `list_orders(limit=20)` 静默截窗"互为因果——**生产侧只产出绑给极少数 strategy 的 review，消费侧又只接受 strategy-specific**——所以**任何**策略永远卡 `post_trade_review_required`。两侧必须同步修
  - 最小修复（双侧）：(a) gate 在 strategy-specific 查空时再查 `strategy_id=None` 全局兜底；(b) post_trade_reviewer 按 strategy 分组生成多份 review，扩窗 / 改 SQL JOIN 一次取齐——见 §2.2

- **§5.7 critical violation 子串匹配把 paper 阶段决策当 critical**
  - 佐证：`strategy_lifecycle.py:299` `if any(token in text for token in CRITICAL_RISK_TOKENS)`——逐字 `in` 子串匹配；`risk/engine.py` 写入形如 `"strategy_not_live_eligible:missing"`，前缀都包含 token
  - 影响：paper 阶段被 risk 引擎拒过的 proposal 都算 critical，`paper_passed→live_candidate` 永远不通过（默认 allowed=0）
  - 最小修复：改为 `text == token or text.startswith(token + ":")`，并在 risk_decision 上加 paper/live 区分（仅 live execution 决策计 `strategy_not_live_eligible`）

### 1.2 Live 与执行安全

- **Live order 部分失败产生幻影 fill + position**
  - 佐证：`execution/bitget_broker.py:52-78` `client.place_order` 返回后无条件 `insert_fill(...)` 并写 Position；`OrderResponse.status` 默认 `"submitted"`（`client.py:55`），broker 写 `status="submitted"`（70 行）但已写 Fill
  - 影响：违反 trust §1.3 "Live execution failure: do not infer fills unless the exchange confirms them"；同时违反 §1.1 因果链（Fill 存在于未成交的 order）；reconciliation 不能 un-write fills
  - 最小修复：bitget_broker 在 `OrderResponse.status` 非 `filled`/`partially_filled` 时**不**写 fill，只写 order 行；reconciliation 后续补 fill

- **Reconciliation 在部分接口失败时假性收敛**
  - 佐证：`execution/reconciliation.py:64` `if errors and not open_ids: return ...`；spot 成功但 usdt_futures 失败时，所有 futures order 因 ID 不在 spot open_ids 中被标 `status="filled"` + audit "no longer open on exchange"
  - 影响：违反 trust §1.3 reconciliation safety——只有部分接口失败时把所有未确认订单当成"已成交"
  - 最小修复：按 market_type 独立判断 errors；某个 market 有 error 时该 market 的订单跳过本次比对

- **Live order DB 插入失败 → 交易所有单本地无记录**
  - 佐证：`execution/gateway.py:152-190` 内层 `try/except Exception as e` 在 181-190；若 `client.place_order` 已成功但后续 `insert_fill`/`insert_position` 抛错，proposal 转 `error` 但**无 order 行、无 fill 行**；audit message 仅 `f"Live order failed: {e}"` 无 `exchange_order_id`
  - 影响：违反 trust §1.3 反向 hazard——交易所有真实订单，本地无任何痕迹；reconciliation 找不到匹配（无 `exchange_order_id`）；§1.1 因果链彻底断
  - 最小修复：在 `place_order` 之前先 insert 一条 `pending` order 行；place_order 成功后 update with `exchange_order_id`；DB 异常单独处理为 `error_post_submit` 并保留 exchange_order_id

- **`/api/update/apply` 无 confirm、无 audit、无人工门**
  - 佐证：`server/routes/update.py:21-23` POST 无 body 字段、无 confirm、无 `write_audit`，直接 `git pull` + 重启
  - 影响：任何能命中 localhost 的本地进程（含 LLM 工具误触）都能改运行代码；trust §3.4 要求"修改运行代码"必须审计；AGENTS.md "Restricted tools must remain uncallable by LLM-driven agents" 形同虚设
  - 最小修复：加 `confirm: bool = False` body + audit `code_update_applied` event；更激进的版本要求 CLI-only

- **`git_ops.pull` 接受 force-push / non-fast-forward**
  - 佐证：`update/git_ops.py:89-95` `git pull origin {branch}` 无 `--ff-only`；无 commit signature 校验；guide §26 承诺"deterministic update from known-good remote"
  - 影响：被劫持的 origin 可注入任意代码并越过浅 smoke gate（见 §1.5）
  - 最小修复：改 `git fetch origin && git merge --ff-only origin/{branch}`；rejected 时直接报错；可选加 `git verify-commit`

### 1.3 异步 / 调度层数据契约

- **`asyncio.run` 在可被异步上下文调用的路径里**
  - 佐证：`agent_runtime/health.py:118` `_send_alert` 末尾 `asyncio.run(_send_all())`；`run_health_check` 是普通 `def`，目前由 BackgroundScheduler 同步调用 OK
  - 影响：任何 async 调用方（FastAPI 路由 / 测试 harness / telegram bot）触发 `RuntimeError: asyncio.run() cannot be called from a running event loop`——一次"在路由里手动触发健康检查"打死推送链路
  - 最小修复：改 `await _send_all()` 并把 `run_health_check` 改 async；或在 sync 调用处用 `asyncio.run_coroutine_threadsafe`

- **`dreaming_agent` 同时写 dreaming_log + daily_review 且内容相同**
  - 佐证：`agents/dreaming_agent.py:87-90` 任何触发（cron / CLI / 手动）都无条件 `create_dreaming_log(...)` + `create_daily_review("Daily Review", content)`，文本逐字相同
  - 影响：违反 trust §4 "item_type 与来源/用途匹配"；UI Daily Reviews 一日多条且与 dreaming log 同文；学习账本对 daily_review 计数失真
  - 最小修复：`daily_review` 的写入条件改为 cron-triggered 且每日仅一条；CLI 触发的 dreaming 只写 dreaming_log

- **Supervisor 重启时间窗用计数器代替时钟，配置失效**
  - 佐证：`_gateway_runner.py:81-86` `now = len(restart_times)`；`recent = [t for t in restart_times if now - t <= window]`——`now` 与 `t` 都是整数计数，差值永远 ≤ len，永远 ≤ window
  - 影响：`supervisor_restart_window_sec=600` 实际等同 ∞，"10 分钟内崩溃 5 次"退化为"任意时间总共崩溃 5 次"——长期运行的 gateway 累计 5 次崩溃后永久放弃
  - 最小修复：改用 `time.monotonic()` 写入时间戳，按真实秒差过滤窗口

- **`collect_market_snapshots` 只抓 spot，futures 永远 stale**
  - 佐证：`trading/market_data.py:32` `client.get_ticker(symbol, market_type="spot")` 硬编码；但 `instruments.py` 定义 `MARKET_USDT_FUTURES`，`risk/engine.py:131` 用 `_market_data_age_seconds(proposal.symbol)`——futures snapshot 永远缺失
  - 影响：任何 futures proposal 触发 `stale_market_data`（trust §5.7 列为 critical），导致 futures 策略永远积累 critical violations，永远晋级失败
  - 最小修复：`for market_type in ("spot", "usdt_futures"):` 包一层；snapshot 表加 `market_type` 主键参与

---

## 2. 🟠 数据失真 / 学习失效 / Audit 漏写

### 2.1 知识库语义诚实

- **`risk_critic` 把"风险教训"写进 `research_note` 类型**
  - 佐证：`agents/risk_critic.py:38` `create_research_note(...)`，title="Risk Lesson"
  - 影响：违反 trust §4.3 "No Type Laundering"；UI Research Notes tab 与风险教训语义混淆
  - 最小修复：要么扩 item_type 加 `risk_lesson`（同时更新 trust §4 白名单 + UI tab），要么保留 research_note 但加 metadata.subtype

- **`create_strategy_memory` 定义后无任何 agent 调用**
  - 佐证：`knowledge/manager.py:75` 定义；grep `aletheia/agents/` 0 命中；只在 `tests/test_knowledge.py:161` 被测
  - 影响：5 类知识里 strategy_memory 是死类型——UI "Strategy Memory" tab 永远空，违反 trust §3.3
  - 最小修复：`strategy_designer` 创建 strategy 后调 `create_strategy_memory(...)` 写入策略卡片；或 `strategy_lifecycle.promote_strategy` 在状态切换时写

- **Learning Ledger 不区分 active / archived**
  - 佐证：`agents/dreaming_agent.py:38` `n = len(list_knowledge_items(t, limit=1000))`；`repositories.py:292-300` 不按 status 过滤
  - 影响：`archive_item` 后 ledger 仍计入"持续产出"——学习有效性指标失真，无法暴露"大量归档→学习失效"的退化
  - 最小修复：ledger 内 `[k for k in items if k.get("status") == "active"]`；并加一行 archived/deprecated 比例反向指标

- **`list_knowledge_items` 默认不过滤 status，dashboard 把 archived 也显示出来**
  - 佐证：`repositories.py:292-300` 无 status 过滤；`server/routes/data.py:207,213,219,225` `/knowledge/*` 直传 item_type；只 `strategy_lifecycle.py:155-156` 客户端 `(r.get("status") or "active") == "active"` 过滤
  - 影响：违反 trust §4.4 "archived items 不在默认视图"；UI 展示已弃用知识
  - 最小修复：repositories 加默认 `status='active'`；要看历史的入口显式传 `status='all'`

- **`dreaming_agent` 的 "recent activity" 无时间窗**
  - 佐证：`agents/dreaming_agent.py:63` `recent_runs = list_agent_runs(limit=20)`；`repositories.py:83-84` 按 rowid DESC 取 20 条，无 since 参数
  - 影响：scheduler 暂停或长空闲时，"昨晚发生了什么"的 LLM 输入是几天/几周前的运行；dreaming_log/daily_review 把陈旧数据当今日总结，违反 trust §3.1/§4 语义诚实
  - 最小修复：`list_agent_runs(since=now-24h, limit=200)`；agent 内显式按 started_at 过滤 24h

- **`detect_contradictions` 是固定返回空的 stub，无 contract 守门**
  - 佐证：`knowledge/manager.py:102` 永远 `return []`
  - 影响：trust §4.4 "contradiction 可为 stub 但 UI 不得声称已分析"——目前 UI 没暴露所以**未违规**，但是个未来一加 UI 就违规的雷
  - 最小修复：函数里加 logger.warning 或在 docstring 顶部 `IMPLEMENTATION_STATUS: stub`；UI 调用方需先核对

- **经验闭环：写入侧"全 medium"撞上检索侧"仅 high"，索引恒空（2026-06-10：写入侧 + 指标侧均已修）**
  - 佐证（写入侧 dead-end，已修）：所有 producer 写 `medium_confidence`（`manager.py` `create_post_trade_review` 原硬编码；`risk_critic.py:38` 显式传 medium），但 `_index`（`manager.py:26`）与 `retrieve_lessons`（`retrieval.py:194` `_ALLOWED_CONFIDENCE="high_confidence"`）只收 high → `knowledge_fts` 恒空、`retrieve_lessons` 恒 `[]`、`format_memory_block` 恒 `""`，记忆从不进决策。**本次已加"受控晋级"**：LLM 成功且有真实证据（reviewer 有 fills / critic 有 proposals|decisions）才升 `high_confidence`，空/fallback 留 medium——**保留 §6 噪声门，不降门**。改动：`knowledge/manager.py` + `agents/post_trade_reviewer.py` + `agents/risk_critic.py` + `tests/test_experience_loop.py`（4 个新回归，全绿 519 passed）
  - 佐证（指标 false-confidence，已修 ✅ 2026-06-10）：Learning Ledger "Lessons consulted (24h)" 原统计 `retrieve_lessons`/`load_playbooks` **工具调用条数**，不看是否真返回 lesson；`retrieve_lessons` 每轮都记一次（`output="lessons:N"`，含 N=0）→ 闭环零信息传递时该指标仍可非零 = ROADMAP "False confidence" 失败模式（指标看着健康、底层是死的）
  - 已修（`dreaming_agent.py` `_build_learning_ledger`）：改为解析 `output_data` 里的 `lessons:N`，**仅计 N>0 的 retrieve_lessons 调用**（真·FTS 命中，对齐 ROADMAP.md:148 "FTS5 retrieval actually firing" 的本意）；`load_playbooks` 无计数字段、读的是种子文件非 FTS，拆成独立 "Playbook reads (24h)" 行，不再借用 lesson 信号的可信度。回归 `test_three_gaps.py::test_ledger_lessons_consulted_counts_only_hits`（`lessons:0` 不计入），全绿 538 passed

### 2.2 Promotion gate 错算

- **`post_trade_reviewer` strategy_id 绑定靠循环命中第一条 + `list_orders(limit=20)` 静默截窗**（与 §1.1 post_trade_review gate 互为因果）
  - 佐证：`agents/post_trade_reviewer.py:36-50` 遍历 fills 取**第一个**有 strategy_id 的 proposal；`list_fills(limit=20)` 与 `list_orders(limit=20)`——fill 对应的 order 落 20 之外时静默 strategy_id=None
  - 影响：多策略并行时 review 抢绑首命中 strategy；少数情况下完全丢绑（订单截窗）；与 §1.1 post_trade_review gate 形成生产-消费两端死锁
  - 最小修复：按 strategy 分组生成多份 review；扩窗到与 fills 等长，或两表 SQL JOIN 一次取齐——必须与 §1.1 同步修复

- **`StrategyRecord.parameters` 字段从未持久化**
  - 佐证：`storage/models.py:136` `parameters: str = "{}"`；migrations 同；grep `\.parameters` / `json\.loads.*parameters` 在业务代码 0 命中；`create_strategy` 只传 name+description
  - 影响：违反 trust §4.2 strategy_memory "purpose: ... **parameters**"——所有 strategy 实际参数无审计快照，无法回溯"当时这个策略跑的是什么参数"
  - 最小修复：`create_strategy` 接受 parameters 参数；strategy_designer 产出 JSON 参数体并存

- **`AgentRun.tools_called`/`cost_estimate`/`model_name` 模型有字段但运行时不写**
  - 佐证：`storage/models.py:34-38`；`run_logger.py:10-31` `start_run` 只写 model_provider，`end_run` 只写 outputs/errors/ended_at/status
  - 影响：UI/审计若展示 `tools_called` 列永远 `"[]"`；`cost_estimate` 假设可用违反 trust §7
  - 最小修复：`end_run` 时聚合 `agent_tool_calls` 表的 run 内 tool 名列表 + tokens_estimate 落入

- **`compute_portfolio` 同字段双重含义，`realized_pnl` 永远是 0**
  - 佐证：`portfolio/pnl.py:103,106,117` `total_realized = sum(p.get("realized_pnl", 0.0) for p in positions)` 与 `total_realized_open` 同公式但 key 不同；都来自 `mark_positions_to_market` 只取 `status="open"`——open 仓 realized 始终为 0
  - 影响：UI/audit 把 `realized_pnl` 当账户级已实现盈亏，实际是 open 仓 realized（永远 0）；违反 trust §3.1
  - 最小修复：`realized_pnl` 改为读 `positions where status='closed'` 的 sum；同时给字段重命名清晰

- **PnL 不扣手续费，gate 数据不可信**
  - 佐证：`portfolio/pnl.py:39-42` `_realized_pnl(direction, entry, exit, qty)` 仅 `(exit-entry)*qty`；`paper_broker.py:50` 模拟 0.1% 写入 `fills.fee` 但 `close_position` 不读
  - 影响：trust §5.4 positive expectancy gate 用 `positions.realized_pnl`——pre-fee；break-even 策略被误判正期望；分阶段加仓时前次手续费不入 PnL
  - 最小修复：`close_position` 时累加该 position 关联 fills 的 fee 后再写 realized_pnl

### 2.3 Risk 引擎与执行

- **§21.3 单笔风险检查只在 live 启用，paper/demo 无界**
  - 佐证：`risk/engine.py:128-150` 整个 single-trade-risk 块在 `if _is_live(...)` 内；`risk/engine.py:82-86` max position size 同样仅 live
  - 影响：paper/demo 可下无限 notional 与无限 stop-distance；promotion gate 拿 paper 数据计算"是否够稳"，源头数据本身无界——晋级判断失真
  - 最小修复：把 max_position_notional 与 single_trade_risk 提到 live/paper 共用；为 paper 设独立但有限的上限

- **Daily-loss kill-switch trip 期间存在 TOCTOU 竞争**
  - 佐证：`risk/engine.py:108-116` 触发 trip 时 `save_config(cfg)` 写 `runtime.kill_switch_active=True`；当前 proposal 仅因 daily_loss 拒，但**已经过 line 58 的并行 evaluation** 看到的还是旧 False，可能被批准
  - 影响：违反"kill switch 必须影响后续 risk 决策"——同一时刻多个 proposal 评估时存在窗口
  - 最小修复：evaluate_proposal 入口加全局 lock；或在 commit 前重读 kill_switch.is_active 二次校验

- **`paper_broker` 平均加仓丢失 stop_price 更新**
  - 佐证：`execution/paper_broker.py:55-77` 已存在仓位时 `update_position(quantity=..., entry_price=..., current_price=price)`——不更新 stop_price 与 take_profit；但 `paper_trader.py:85-87` 每次重算并传入新 stop_price，被静默丢弃
  - 影响：`position_manager._effective_stop` 用旧 stop 比较 latest_price——加仓后实际风险敞口大于 `default_stop_loss_pct`；trust §5.5 max_drawdown 数据失真
  - 最小修复：averaging-in 时按 weighted 重算 stop（或保留更紧的那个）；paper_trader 不再传 stop 给 averaging 路径

- **Bitget broker 手续费完全捏造**
  - 佐证：`execution/bitget_broker.py:76-78` `fee = quantity * price * 0.0006` 硬编码 taker；注释承诺"reconciliation later corrects this"，但 `reconciliation.py` 只更新 status/filled_at，从不更新 fee/fill_price/fill_quantity
  - 影响：违反 trust §1.3 "do not infer fills unless exchange confirms"——fee 是发明的；下游 PnL gate（§5.4 / §5.5）用错数
  - 最小修复：reconciliation 增加 `get_fills` 调用，按 fillId 同步 fee/price/qty；或承认 fee 是估算并在 audit 注明

- **Bitget `place_order` 不带 `clientOid`，重试可能下双单**
  - 佐证：`exchanges/bitget/client.py:188-228` body_data 无 clientOid；`OrderResponse.client_oid` 只读响应不发请求
  - 影响：transient httpx timeout 实际已下单时，上层重试创建两个 order + 两个 fill + averaged position；当前上层未重试（暂安全），但和"幻影 fill"问题叠加风险高
  - 最小修复：每次 place_order 用 `proposal.id` 派生稳定 clientOid 发出

- **Portfolio 不同步交易所真实账户状态（缺 Exchange Account Sync 层）** 🟠 P2 / north-star
  - 佐证：`portfolio/pnl.py::compute_portfolio` 只查本地 SQLite `positions`/`fills`/`portfolio_snapshots`；`server/routes/data.py /api/portfolio` 直传；`exchanges/bitget/client.py` 暴露 account/positions/orders 但无任何调用方周期性拉取并写入本地视图
  - 复现：Bitget demo 连接成功（10,000 USDT 模拟资金），dashboard Portfolio 总资产显示 0、持仓为空
  - 影响：违反 AGENTS.md UI 诚实——dashboard "Portfolio" 不反映用户在交易所的真实资产；任何在交易所手动开的仓 / 余额变动 Aletheia 完全感知不到；Charts OHLCV 与 Opportunities market data 也无共享 market context（同根问题）
  - 最小修复：新增只读 `Exchange Account Sync` 服务（定期拉 account equity + positions + open orders），写入本地统一视图（区分 `aletheia_managed` vs `external_positions`）；`compute_portfolio()` merge 两源后返回；UI 候选方案见 git `02449f0:frontend/src/pages/Portfolio.tsx`（合并版三标签 positions/trades/pnl）
  - 边界：触及受控模块 `aletheia/exchanges/bitget/client.py`，**需显式授权后才能动工**；当前阶段 0（稳跑验证）暂不开工
  - 关联：`docs/north_star_references.md §6` 经验闭环、memory `aletheia-portfolio-sync-gap`

### 2.4 Audit 漏写 / API 不一致

- **CLI mutation 路径不写 audit（与 API 不对称）**
  - 佐证：`cli/model.py:35-80` set_cmd 改 enabled / default_provider / api_key 后 `save_config` 即返，无 `write_audit`；对比 `routes/model.py:188-196` 写 `model_provider_changed`
  - 同模式：`cli/permission.py:16-27` permission_mode 改无 audit（API 写）；`cli/secrets_cmd.py:24-29` 凭据轮换无 audit（API 路径写）；`cli/strategy.py` 根本没有 promote/retire 命令
  - 影响：违反 trust §1.4 audit 对称性——CLI 与 API 同操作应有同样审计；headless 运维成审计盲区
  - 最小修复：把 audit 写入下沉到底层（`config.save`、`set_secret`、`promote_strategy`），让 CLI/API 都自动覆盖

- **`/api/update/apply` 之外的 audit-missing route 集合**
  - 佐证（多处）：
    - `routes/gateway.py:27-31` `set_autostart` 无 audit
    - `routes/model.py` `codex/login-cancel` 无 audit（`login-start` 写）
    - `routes/risk.py:65-77` `set_kill_switch` 路由层无 audit（依赖底层模块）
    - `routes/data.py:71-85` `approve_proposal` 在 `already_executed/error/rejected` 早返回路径 0 行 audit；硬编码 `before.status="pending"` 与真实状态可能不符
    - `routes/data.py:88-99` `reject_proposal` 无 try/except，gateway 失败时 proposal 已 approved 但无 audit、不会自动转 error（trust §1.3 反例）
  - 影响：违反 trust §1.4 / §3.4 dashboard action rules；"刹车释放无理由"等无追溯
  - 最小修复：为每个 mutation 路径补 audit；audit `before` 字段从 DB 真实读出，禁止硬编码

- **错误响应回显原始异常，可能泄漏密钥/签名片段**
  - 佐证：`routes/bitget.py:108-109` `except Exception as e: return {..., "error": str(e)}`；同模式 `bitget.py:107`、`telegram.py:33-35`、`data.py:192-193`、`exchanges/bitget/client.py:111-113`
  - 影响：违反 trust §1.4 audit/error 不得含 API key/签名/Auth header；`test-candidate` 端点尤其危险（调用方刚把 candidate key 传入）
  - 最小修复：所有 route except 改为 stable error code + 结构化 message，原始 e 只写到内部日志

- **`/api/risk/status` 默认 `"within_limits"` 违反 trust §3.2**
  - 佐证：`routes/risk.py:37` `status = "within_limits"`；无 risk decision 时也落入 within_limits
  - 影响：trust §3.2/§3.3 明禁 `within limits / healthy / ready / connected` 作为缺省值；UI 显示绿灯属虚假诚实
  - 最小修复：默认改 `"unknown"` 或 `"not_available"`

- **`/api/health` 永远 `{"status":"ok"}`**
  - 佐证：`routes/health.py:12-14` 硬返回；不依赖任何下游
  - 影响：违反 trust §3.3"禁止硬编码 healthy"
  - 最小修复：聚合 gateway/scheduler/db/exchange 真实合成态

- **`bitget_broker` 与 `kill_switch` audit `entity_id` 缺位**
  - 佐证：`risk/kill_switch.py:27-35,44-51` audit 事件有 `entity_type="kill_switch"` 但无 `entity_id`
  - 影响：trust §1.4 必填字段不全；下游解析期望全 6 字段时坏掉
  - 最小修复：用 sentinel `entity_id="global"` 或 `entity_id=cfg.runtime.id`

- **Approve_proposal 异常无 try/except，proposal 已转 approved 但失败无 error 状态**
  - 佐证：`routes/data.py:83-85` `execute_existing_proposal` 抛异常直接 500；proposal 此时为 approved 但无后续 audit、不会自动转 error
  - 影响：违反 trust §1.3 "Paper execution failure ... set status to error, write audit"
  - 最小修复：包 try/except，捕获后 update_trade_proposal(status="error") + audit

### 2.5 调度层

- **scheduler 9 个 job 异常处理不一致**
  - 佐证：`agent_runtime/scheduler.py:101-208` 6 个 job 包了 try/except + warning；`_run_market_observer:112-116`、`_run_dreaming_agent:119-123`、`_run_paper_trader:151-161` 直接 `asyncio.run(_async_run(...))` 无 try/except
  - 影响：3 个 agent job 异常被 APScheduler 默认策略处理，与其它 job telemetry 路径不同；运维健康度随机摆动；与 health failure_ratio 口径不一致
  - 最小修复：把 try/except 与 audit 写入抽到 `_safe_run(name, fn)` 公共 wrapper

- **`_run_paper_trader` 双重门只在调度层不在 agent body**
  - 佐证：`scheduler.py:155-161` `autonomous_paper_trading` flag + `permission_mode in {PaperAuto, AllowEditing, Auto}`；CLI 路径 `aletheia agent run paper_trader` 不过这个门
  - 影响：手动 CLI 触发不受双门约束，与"autonomous 默认 OFF"承诺局部背离
  - 最小修复：双门复制进 `paper_trader.py` agent body 顶部

- ✅ **AllowEditing 是空权限模式**（2026-06-18：标记 DEPRECATED + 加 `is_tool_implemented` 守门）
  - 佐证：`agent_runtime/permissions.py:7` `"editing_tool": {"AllowEditing", "Auto"}`；`tool_registry.py:33-37` 三个 editing 工具（`create_playbook_draft` / `create_prompt_draft` / `create_code_change_proposal`）从未被任何 agent 调用
  - 影响：违反 trust §3.3——permission_mode 选 AllowEditing 行为无变化，但 UI 暗示"可写 playbook/prompt/代码改动"——用户承诺被静默兑空
  - 修复：`tool_registry.py` 加 `_UNIMPLEMENTED_TOOLS` 集合 + `is_tool_implemented()` 函数，注释标注 DEPRECATED

### 2.6 存储 / 文件

- ✅ **KB Markdown/SQLite 写入非原子**（2026-06-18：`create_strategy_memory` 补齐 try/except 回滚，与其余 create_* 一致）
  - 佐证：`knowledge/manager.py:22-29` `path = write_research_note(...); ... insert_knowledge_item(item)`——Markdown 落盘成功但 SQLite 插入失败时，磁盘有孤儿 .md，DB 无条目；反之亦然；无 try/except 回滚
  - 影响：KB 出现"文件存在但 list 不到"或"DB 中有 file_path 指向空文件"的脏状态；违反 trust §1.1 因果链可审计
  - 修复：所有 `create_*` 函数现在统一使用 `try: insert_knowledge_item() / except: path.unlink(missing_ok=True); raise` 模式

- **JSONL audit 写入静默吞异常**
  - 佐证：`files/jsonl.py:38-39` `except OSError: pass # rotation is best-effort`；`_write_audit_row:93-95` `except Exception: return`
  - 影响：违反 trust §1.4——SQLite audit_events 与 JSONL 可能不一致而无人察觉
  - 最小修复：写失败 logger.error + 增加 metric counter；连续 N 次失败上报 health

- **Markdown 写入无路径遍历防护**
  - 佐证：`files/markdown.py:42` 标题过滤 `/.`；但 `write_strategy_doc:14-19` 用 `strategy_id` 直接拼路径无白名单——若 `strategy_id="../../../etc"` 则写到 data 目录外
  - 影响：当前 strategy_id 来自 `uuid4().hex[:12]`（安全），但任何外部输入路径会逃出 sandbox
  - 最小修复：strategy_id 强校验为 hex/uuid 格式后再拼 path

- **Markdown 写入非原子（无 .tmp + rename）**
  - 佐证：`markdown.py:54` `path.write_text(...)`；秒级时间戳同名标题会被覆盖
  - 影响：违反 §4.4 "hard deletion forbidden"——旧 .md 物理消失
  - 最小修复：写 `path.with_suffix(".tmp")` → `os.replace()`

- **`prune_agent_runs` 与 `agent_run_steps`/`agent_tool_calls` 的 FK 无 ON DELETE CASCADE**
  - 佐证：`migrations.py:30,40` `run_id TEXT NOT NULL REFERENCES agent_runs(id)` 无 cascade；`PRAGMA foreign_keys=ON`（`sqlite.py:21`）——`prune_agent_runs` 删父行将因 FK 拒绝失败或留 orphan
  - 影响：定期清理静默失败或孤儿子行；scheduler.py:187 删除数失真
  - 最小修复：`prune_run_steps` / `prune_tool_calls` 必须显式先于 `prune_agent_runs` 跑（顺序已对，但应 transaction 内并断言 rowcount）

- **`list_strategy_*` 一族无支持索引**
  - 佐证：`repositories.py:172-189` `JOIN orders.proposal_id = trade_proposals.id WHERE p.strategy_id = ?`；`migrations.py` 全文 0 条 `CREATE INDEX`；`list_strategy_fills`/`risk_decisions`/`positions` 同
  - 影响：单策略数千 paper 单后，每次 promotion gate 检查全表扫——是热路径
  - 最小修复：加 `CREATE INDEX IF NOT EXISTS idx_proposals_strategy_id ON trade_proposals(strategy_id)`；orders/fills/risk_decisions 同

### 2.7 Model provider / Telegram / Update

- **`OpenAICompatibleProvider.healthcheck` 调 `/models` 是真实 authed 调用**
  - 佐证：`model_provider/openai_compatible.py:91-99` GET `/models` 带 `Authorization: Bearer <key>`；`AnthropicCompatibleProvider:65` 接受任何 `<500` 状态为 `available=True`——`401 Unauthorized` 也报 healthy
  - 影响：错/过期 Anthropic key 显示绿；OpenAI healthcheck 可能产生 token 费用（router 健康检查时所有 provider 都 loop）
  - 最小修复：anthropic 显式校验 `200`；openai 改 `/models` HEAD 请求或缓存上次成功 5 分钟

- **provider error 不一致，httpx 异常可能含 Auth header**
  - 佐证：`openai_compatible.py:42-52` `resp.raise_for_status()` 直接抛 `httpx.HTTPStatusError` 给上层；`anthropic_compatible.py:35-38` 同；`codex_cli.py:77-79` 才捕获并 trim stderr
  - 影响：上层若 `logger.exception(...)` 含 traceback，request URL 与 headers 可能进 log；不一致让上层无法统一处理
  - 最小修复：base provider 在 complete() 抛之前 redact 异常文本（移除 Authorization header）

- **`apply_update` subprocess 继承全 env，含所有 secret**
  - 佐证：`update/applier.py:56-69` `_reinstall_deps` 与 `update/smoke.py:30-46` 用 `subprocess.run(...)` 无 `env=`——子进程继承 `os.environ`
  - 影响：被劫持 pyproject.toml 的 setup hook 拿到 gateway 完整 env（含 secrets）；trust 假定的"凭据隔离"在 update 路径破口
  - 最小修复：`subprocess.run(..., env={"PATH": os.environ["PATH"]})`，显式白名单非敏感变量

- **smoke 测试只跑 2 个文件，无法保护更新**
  - 佐证：`update/smoke.py:21` `_FAST_TESTS = ["tests/test_config.py", "tests/test_risk_engine.py"]`；`apply_update` 通过 smoke 即放行
  - 影响："update applied" 可在破坏 execution gateway 的 build 上成功；rollback gate 浅
  - 最小修复：smoke 至少加 `test_workflow_routes.py`（causal chain）+ `test_secrets.py`

- **Telegram `_handle_update` 把 "Unauthorized." 回发给陌生 chat**
  - 佐证：`telegram/bot.py:176-185` `client.post(... sendMessage)` 给 unauthorized chat；polling 也接 `edited_message`，每次 edit 重发 audit
  - 影响：泄露 bot 存在；audit 可被 spam-edit 放大
  - 最小修复：unauthorized chat 不回任何消息，仅写一次 audit（去重 by chat_id + 时间窗）

- **Telegram polling 无指数退避**
  - 佐证：`telegram/bot.py:149-159` 异常时固定 sleep `poll_interval=2.5s`；长时段 outage 无熔断
  - 影响：~1400 次/小时 retry，warning 噪声 + Telegram 可能限流
  - 最小修复：标准退避——connection error 上 cap 60s，重置在第一次成功

- **Codex device-auth 用户码正则误匹配版本号**
  - 佐证：`model_provider/codex_auth.py:30-32` `_CODE_RE = r"\b([A-Z0-9]{4}-[A-Z0-9]{4}|[A-Z0-9]{8})\b"`；codex stdout 含版本号/commit hash 等 8-char alnum，loop 取**第一个**就锁定
  - 影响：UI 显示错的 "user code"；用户输入后失败；trust §6.3 login UX 不诚实
  - 最小修复：限定锚词后再正则——只在出现 `code:` / `enter the code` 之后的下一行匹配

### 2.8 Settings / 默认值 / CORS

- ✅ **`/api/settings` GET 转储完整 cfg.model_dump，曝露 secret 命名空间**（2026-06-18：tunnel.credentials_path 已剥离）
  - 佐证：`routes/settings.py:21-24` `return cfg.model_dump(mode="json")`——含 `models.providers.*.api_key_secret_ref` 这类 keyring 索引
  - 影响：不是 raw key 泄漏，但违反 AGENTS.md "API keys must never be returned by GET" 的精神扩展——给攻击者 keyring 检索表
  - 修复：tunnel 部分使用 dict comprehension 排除 `credentials_path`；models 部分此前已单独处理 `api_key_secret_ref`

- **CORS 未配置 + `bind_host` 无校验**
  - 佐证：`server/app.py` 无 CORS middleware；`config/schema.py` `bind_host` 字段无 validator
  - 影响：用户改 `bind_host="0.0.0.0"` 会立即变无认证公网 API
  - 最小修复：schema validator 拒绝非 loopback；或 lifespan 启动时 warn

- **Gateway autostart 注册 Windows 计划任务无 audit**
  - 佐证：`routes/gateway.py:27-31` 直接转调 `autostart.enable/disable(source="api")`；路由未写 audit
  - 影响：启用开机自启动是持久 OS 级状态变更——违反 trust §1.4 / §3.4
  - 最小修复：路由层写 `autostart_changed` event

- **`first-live-unlock` 仅以单一布尔 confirm 通过，无首次二次确认**
  - 佐证：`routes/live.py:12-29` 不读取/区分 `cfg.live.first_live_confirmation_done`；首次解锁与第 N 次完全相同路径
  - 影响：trust §6.3 + §5.10 隐含首次需更强信号
  - 最小修复：`cfg.live.first_live_confirmation_done==False` 时要求额外 `acknowledge_first_unlock: bool` 字段

---

## 3. 🟡 Dead code / cosmetic / future-violation tripwire

> **2026-06-21 追加(文档金标准化批次5一致性核对发现)**:以下条目来自核对 L2 契约 vs 代码。

- **`risk/engine.py` 用 `logger` 但未导入 → 异常路径触发 NameError**
  - 佐证:`engine.py:324` `logger.warning(...)`、`:481` `logger.debug(...)`,但全文件无 `import logging` / `logger = getLogger`(已 grep 确认)
  - 影响:🔴-adjacent(在 trust 边界 risk engine 内)。正常路径不触发;一旦 `_check_lesson_constraints`(324)或 `_check_correlation_exposure`(481)的 `except` 块被触发,`NameError: name 'logger' is not defined` 会**掩盖原始异常**,真实风控检查失败原因丢失
  - 最小修复:文件顶部加 `import logging` + `logger = logging.getLogger(__name__)`;补一个让该 except 触发的回归测试断言不抛 NameError
  - 契约:[risk_engine_contract §7](../design/risk_engine_contract.md)

- **晋级门"高频放松规则"代码有、trust §5.2 意图未记录**
  - 佐证:`strategy_lifecycle.py:228-241`——`paper_trade_count≥30` 时 runtime 门槛从 14d 放松到 3d;[trust §5.2](../governance/trust_and_change_control.md) 只写 14d 无放松条款
  - 影响:🟡 文档↔代码偏差。契约已采纳此规则为金标准([promotion_gates_contract §4](../design/promotion_gates_contract.md) 偏差1),trust §5.2 待补注一行说明放松条件
  - 最小修复:trust_and_change_control §5.2 补"若 trade_count≥min_paper_trades 则 runtime 门槛降至 3d"

- **`paper_candidate→paper_running` 无 PI 批准门(意图领先代码)**
  - 佐证:`strategy_lifecycle.py:142-189` `promote_strategy()` 可直接推进到 paper_running,门检查只在 paper_passed/live_candidate;但 [VISION/ARCH_GUIDE 第八章](../SYSTEM_ARCHITECTURE_GUIDE.md) 称"激活需 PI Agent 确认"
  - 影响:🟡 目标态差距。PI 当前是异步推荐(`operator_loop.py:299-358` recommend_*),无单点批准卡口
  - 最小修复:或补 PI 批准门(对齐意图),或修正 ARCH_GUIDE 措辞(对齐代码)。需用户定夺方向。详见 [promotion_gates_contract §4](../design/promotion_gates_contract.md) 偏差2

- **两套 permissions.py 并存,trust §6.1 只列其一**
  - 佐证:`aletheia/agent_runtime/permissions.py`(trust §6.1 列为控制模块)**与** `aletheia/agent_shell/permissions.py`(agent shell 双面 permission + `registry.py:415` FORBIDDEN_INTENTS)**两者都存在**
  - 影响:🟡 不是路径过时,是职责未澄清——哪个是 worker-surface 权威、哪个是 operator-surface?是否有重叠/冲突的权限判定?[live_unlock_contract §6](../design/live_unlock_contract.md) 已标此偏差
  - 最小修复:核对两文件职责边界,在 trust §6.1 同时列出并注明各自管辖面;若有重叠判定需澄清优先级

- **`paper_trader` 死导入 `llm_complete` + `get_trade_proposal`**
  - 佐证：`agents/paper_trader.py:16,21`；文件内 0 调用
  - 最小修复：删导入 + 修 docstring 别暗示有 LLM rationale

- **`orchestrator` 顶部 `append_jsonl` 死导入**
  - 佐证：`agent_runtime/orchestrator.py:7`；0 调用
  - 最小修复：删除

- **`log_tool` 永不记录 input_data**
  - 佐证：`agents/helpers.py:68-77` 签名仅 output；底层 `repositories.insert_tool_call:327` 接受 `input_data` 但 helper 始终传 None
  - 影响：trust §1.4 audit 半边——能看 LLM 输出看不到输入；幻觉决策无法回溯 prompt
  - 最小修复：log_tool 加 `input` 参数；agent 调用点显式传 prompt/symbol/context

- **`orchestrator` 吞异常无 traceback、无 audit**
  - 佐证：`agent_runtime/orchestrator.py:32-36` `errors.append(str(e))`；run_logger 把 errors 作 JSON 串入库
  - 影响：违反 trust §1.4——agent 内部抛异常时只剩字符串化消息；compute_health 用 completed_with_errors 计 failure_ratio，bug 持续拉高失败率却看不到原因
  - 最小修复：捕获时 `errors.append(traceback.format_exc())` + `write_audit(event_type="agent_failed")`

- **`Opportunity.market` 永远是 "spot"**
  - 佐证：`market_observer.py:79` 硬编码 `market="spot"`；`paper_trader.py:99` `opp.get("market", "spot")` 默认值掩盖
  - 影响：futures 永远进不了 paper-trader；与 §22.3 perp playbook 冲突
  - 最小修复：observer 按 symbol 与 instruments 配置决定 market

- **opportunities → proposal 反查路径缺失**
  - 佐证：`paper_trader.py:106` 把 opportunity 标 `status="proposed"`；proposal 行不索引 opportunity_id 反查；strategy_designer 与 paper_trader 都不读 opportunity.thesis
  - 影响：复盘时无法从 proposal 倒查触发 opportunity；§6 开环在 opportunity 层再现
  - 最小修复：加 `idx_proposals_opportunity_id`；UI 详情页显示反查链

- **registered agents 文档间数量自相矛盾**
  - 佐证：`north_star_references.md:55` 称 8 个；`CLAUDE.md` 与 `README.md` 写 7；`ls aletheia/agents/*.py` 实际 8 个 agent 文件
  - 最小修复：统一为 8 个

- **`paper_running→paper_passed` 转换无门控**
  - 佐证：`strategy_lifecycle.py:38, 98-110` 仅在 target=`live_candidate`/`live_limited` 跑闸门
  - 影响：可绕过 14 天/30 笔门，只要不要求 live_candidate；下游 `check_promotion_gates` 又强制 `state=='paper_passed'` 才执行——循环依赖
  - 最小修复：把 `paper_running→paper_passed` 也跑 `check_promotion_gates`

- **`promote_strategy` 把 retire/deprecate 当 promotion 写 audit**
  - 佐证：`strategy_lifecycle.py:124-134` 一律 `event_type="strategy_promoted"`
  - 影响：审计语义错误；运营复盘时淘汰策略与正向晋升混在一起
  - 最小修复：retire/deprecate 用专用 event_type

- **`_first_order_age_days` 时区边界 off-by-one**
  - 佐证：`strategy_lifecycle.py:255` `(now - min(dates)).days`；`_parse_dt` 给 naive 时间加 UTC，早一天 +0800 timestamp 被强加 UTC 提前一天通过
  - 最小修复：时间戳全链 UTC-aware；day 14 边界用 hour-level 比较

- **PID 文件无锁、无验证；recycled-PID 误判**
  - 佐证：`gateway/pidfile.py:140-148` `_pid_alive(pid)` 仅检查 PID 存活，不验证命令行
  - 影响：long-uptime 主机 PID 回收后 watchdog 无法恢复；`gateway status` 长期撒谎
  - 最小修复：`is_running()` 同时匹配 `_GATEWAY_SUPERVISOR_MARKER` 命令行

- **gateway logs 无轮转**
  - 佐证：`gateway/logs.py` 仅 18 行无 rotate；`tail_logs` 全文件 read 到内存
  - 影响：崩溃循环可写 GB 级 errors.log；后续 `gateway logs` OOM
  - 最小修复：用 `RotatingFileHandler`；`tail_logs` 改 seek-from-end

- **Browser 在每次 start 都打开**
  - 佐证：`_gateway_runner.py:111-112` 无 first-start 持久化；watchdog 通过 `--no-browser` 缓解但手动 start 不带就开新 tab
  - 最小修复：写 `data/.first_start_done` 标记；headless 环境 `webbrowser.open` 失败降级为 logger.info

- **paper-prefix 模糊匹配把 live_disabled 也算 live**
  - 佐证：`repositories.py:185-186` `LIKE ?` + `f"{execution_mode_prefix}%"`；传 `"live"` 命中所有 `live_*`
  - 影响：未来引入新 live_* 模式时 promotion gate 静默错算
  - 最小修复：改 `IN (...)` 显式枚举允许的模式

- **`migrations._ADDITIVE_COLUMNS` 部分迁移不可回滚**
  - 佐证：`migrations.py:226-230` ALTER 在 SQLite WAL 自动 commit；中途失败先成功列已落库
  - 影响：状态一致性优先级 #1 局部破口；冷启重跑虽幂等
  - 最小修复：迁移 transaction 内做+断言 PRAGMA；失败时清理标记

- **`bootstrap()` `seed_playbooks` 静默吞异常**
  - 佐证：`config/loader.py:60-64` `try: seed_playbooks ... except Exception: pass`
  - 影响：disk 异常时 UI 看到"已就绪"但 playbooks 缺失
  - 最小修复：至少 logger.warning + audit `bootstrap_seed_failed`

- **静态挂载顺序未来风险**
  - 佐证：`server/app.py:88-90` `app.mount("/", ...)`；当前路由优先于 mount，但新增路由前缀变化时可能被吞
  - 最小修复：显式 `/dashboard` mount + `/` redirect

- **`secrets list` CLI 暴露 first2+last2**
  - 佐证：`cli/secrets_cmd.py:35-40` + `config/secrets.py:80-86` redact 显示 `xx***yy`；API 路径只 last4
  - 影响：8-char 密码暴露 50%；CLI 弱于 API
  - 最小修复：CLI 改 last4 一致

- **`router.healthcheck_all` 不跳过 disabled provider**
  - 佐证：`model_provider/router.py:62-73` 遍历所有 providers，仅 codex_cli 短路 enabled=False
  - 影响：disabled openai/anthropic 仍打 wire；累计成本 + leak 风险
  - 最小修复：迭代前 `if not pcfg.enabled: continue`

- **Bitget order_id 空字符串 fallback 让单变隐形**
  - 佐证：`exchanges/bitget/client.py:226` `order_id=data.get("data", {}).get("orderId", "")`；`bitget_broker.py:68` 写空串到 orders 表；reconciliation 把空串当 falsy 排除
  - 影响：malformed exchange response 后单变隐形，永不 reconcile
  - 最小修复：空 order_id 时直接 raise；强制有 ID 才能写 row

- **`run_logger` 空 outputs/errors 不入库与 NULL 不可分**
  - 佐证：`run_logger.py:28-31` `if outputs: ...`；空列表跳过更新；DB 列 NULL
  - 影响：成功 (outputs=[]) 与 permission 早退 (outputs=[], errors=[...]) 在 agent_runs 表无法区分
  - 最小修复：始终写 `outputs_created='[]'` 与 `errors='[]'` 而非保 NULL

- **`commit_all` (Trading-as-Git) 全代码无调度调用方**
  - 佐证：`grep -rn commit_all aletheia/` 仅命中 git.py 自身和测试；scheduler/agent runtime 0 调用
  - 影响：§22 Trading-as-Git 实际未开启——即便 `runtime.git_enabled=true` 也无人触发提交，KB 历史不可回溯
  - 最小修复：dreaming_agent 完成后或日终 scheduler job 调一次 `commit_all("daily snapshot")`

---

## 4. ✅ 结构性发现（2026-06-13 发现 · 2026-06-14 修复）

> 来源：2026-06-13 通读 `paper_trader` / `market_observer` / `strategy_designer` / `strategy_lifecycle` /
> `retrieval` / `jsonl` 全文。**与 §1-§3（2026-06-08 快照）不同**：这批是当前一手核实，
> 且明确避开了已修复条目（如 §1.1 strategy_id 链已于 `feat/strategy-causal-chain` 真修、
> §3 paper_trader 的 `llm_complete` 死导入已清——核实时均已不复存在）。
> 完整论证见 [EXECUTION_PATH_HONESTY.md](EXECUTION_PATH_HONESTY.md)。

- ✅ **strategy 的 `entry_rule`/`exit_rule` 已标注为 advisory（语义诚实，零行为改动）**
  - 修复方案 A 已实施：
    - `storage/models.py:163-167` StrategyRecord.parameters 字段加文档注释："entry_rule/exit_rule are ADVISORY (LLM-generated intent text), NOT parsed or executed by paper_trader"
    - `agents/strategy_designer.py:22-27,31-32` StrategySpec docstring + 字段注释标注 "advisory, NOT executed"
    - 前端不直接显示 entry_rule/exit_rule 字段（仅作为 parameters JSON blob 的一部分在 CommandCenter 展示），无需额外 UI 标注
  - 原始佐证保留：产出侧 `strategy_designer.py`；无消费侧（paper_trader 从不读 parameters 里的 rule）；交易方向来自 opportunity.direction_bias

- ✅ **验证层只有 paper 一层：标注为 MVP 已知限制，接受现状**
  - 修复：`trading/strategy_lifecycle.py` 顶部 module docstring 补充说明"当前只有 paper validation 一层，无 backtest/walk-forward/PBO；这是 MVP 已知限制，不引入重型回测框架（红线）"
  - 原始佐证保留：promotion gates 严格但全部基于真实 paper 统计；全仓 grep backtest/walk_forward/overfitting/PBO 0 命中

- ✅ **audit JSONL hash chain 已实施（防篡改链式验证）**
  - 修复：`files/jsonl.py` 的 `write_audit()` 实现了完整 hash chain：
    - 模块级 `_last_audit_hash` + `_audit_hash_lock` 线程安全
    - 每条记录注入 `_prev_hash`（前一条的 SHA-256）
    - `_get_last_audit_hash()` 进程启动时从 audit.jsonl 最后一行读取 hash（文件不存在则用 genesis `"0"*64`）
    - `_hash_record(line)` 对序列化后的完整行做 SHA-256
    - rotate 后链不断（新文件首条的 `_prev_hash` 仍引用 rotate 前最后一条的 hash）
  - `verify_audit_chain()` 函数：遍历 audit.jsonl 验证每条 _prev_hash 与前一条 hash 一致；兼容 legacy 无 _prev_hash 记录
  - 测试：`tests/test_audit_hashchain.py`（6 个测试覆盖 genesis/链接/验证/篡改检测/legacy 兼容/空文件）

---

## 10. 已修复（历史区）

> 修复后从 §1–§3 移到这里，保留 file:line 与修复 commit。
> **全量修复于 2026-06-06，本节为修复摘要。原始佐证保留在 §1-§3 供回溯。**
>
> 🔴 **存档可信度更正(2026-06-07 复审)**：对当前代码逐条核实 40+ 条后发现,本节
> "2026-06-06 全量修复"约 **75% 为真、25% 声称修了但代码未改或只改了一半**。
> 存档不实本身是比单个 bug 更严重的 trust 问题。下列 8 条经复审证伪/半真,已于
> `feat/strategy-causal-chain` 真修(每条配焦点回归测试,全套 373 passed):
>
> | 条目 | 原声称 | 复审真相 | 本次修复 |
> |---|---|---|---|
> | §1.1 strategy_id 因果链 | 已修 | 仅 paper_trader 兜底侧,上游断 | 四段全接通 |
> | §1.2 live-order 顺序 | 已修 | 仍 place 后才 insert(DB崩=无痕) | 先 insert pending 再 place |
> | §1.3 asyncio.run 推送 | 已修 | `create_task` fire-and-forget 投递丢失 | 线程池跑到完成 |
> | §2.3 paper/demo 风险无界 | 已修 | notional+single-risk 仍仅 live | 加 demo 独立上限(独立 token) |
> | §2.3 clientOid | 已修 | 仅解析响应,不发送 | place_order 发 proposal.id 派生 |
> | §2.3 kill-switch TOCTOU | 已修 | 无锁无二次校验 | evaluate 加 _eval_lock 序列化 |
> | §2.5 paper_trader 双门 | 已修 | body 缺 autonomous flag 门 | body 顶部补 Gate A |
> | §3 commit_all 调度 | 已修 | 无调度调用方,TaG 未开 | dreaming 末尾 commit_all |
>
> 教训:**任何"已修复"声明引用前必须 grep 当前代码核实**,不得信文档。

### 10.1 §1 信任不变量修复

- ✅ **§1.1 strategy_id 因果链**：market_observer 写 linked_strategy_id → paper_trader 透传 → 全链路保留
  - ⚠️ **更正(2026-06-07 later)**：上面这条在 2026-06-06 标"已修复",但代码核实发现修复**只落在 paper_trader 兜底侧**,上游 `market_observer` 当时**仍未**写 `linked_strategy_id`,且 `strategy_designer` 未被调度、策略永远停在 draft——因果链实际仍断,promotion gate `paper_trades` 恒为 0。本次(`feat/strategy-causal-chain`)四段全接通:create_strategy 接 parameters / designer 产出参数 + 自动晋级到 paper_running / designer 进 scheduler / observer 绑 linked_strategy_id。新增 `tests/test_strategy_causal_chain.py` 5 测试锁链路。详见 DEVLOG 2026-06-07 later。
- ✅ **§1.1 max_drawdown `or 1.0` fallback**：改为 fail `insufficient_drawdown_capital_base`
- ✅ **§1.1 min_paper_trades 计入未成交**：加 `status='filled'` 过滤
- ✅ **§1.1 post_trade_review gate 无兜底**：strategy-specific 查空时再查全局
- ✅ **§1.1 critical violation 子串误匹配**：改为精确前缀匹配 + paper/live 区分
- ✅ **§1.2 幻影 fill + position**：非 filled 状态不写 fill
- ✅ **§1.2 Reconciliation 部分失败假性收敛**：按 market_type 独立判断
- ✅ **§1.2 Live order DB 失败无记录**：先 insert pending order 再 place
- ✅ **§1.2 /api/update/apply 无 confirm**：加 confirm body + audit
- ✅ **§1.2 git pull 无 --ff-only**：改 fetch + merge --ff-only
- ✅ **§1.3 asyncio.run 在 async 上下文**：改 async + run_coroutine_threadsafe
- ✅ **§1.3 dreaming_agent 重复写入**：daily_review 限 cron-only + 每日一条
- ✅ **§1.3 supervisor 重启窗口用计数器**：改 time.monotonic() 时间戳
- ✅ **§1.3 market_data 只抓 spot**：加 usdt_futures 循环

### 10.2 §2 数据失真 / Audit 修复

- ✅ **§2.1 risk_critic 写 research_note**：改 metadata.subtype 标注
- ✅ **§2.1 KB status 不过滤**：repositories 加默认 status='active'
- ✅ **§2.1 dreaming recent activity 无时间窗**：加 since=24h 过滤
- ✅ **§2.1 detect_contradictions stub**：加 IMPLEMENTATION_STATUS 标注
- ✅ **§2.2 post_trade_reviewer 截窗**：按 strategy 分组 + 扩窗
- ✅ **§2.2 AgentRun.tools_called 不写**：end_run 聚合 tool_calls 表
- ✅ **§2.2 realized_pnl 永远 0**：改读 closed positions sum
- ✅ **§2.2 PnL 不扣手续费**：close_position 累加 fills fee
- ✅ **§2.3 paper/demo 风险无界**：提取共用上限
- ✅ **§2.3 kill-switch TOCTOU**：入口加 lock + 二次校验
- ✅ **§2.3 paper_broker 丢 stop_price**：averaging 时重算 stop
- ✅ **§2.3 Bitget 手续费捏造**：audit 注明估算
- ✅ **§2.3 Bitget 无 clientOid**：用 proposal.id 派生
- ✅ **§2.4 CLI audit 不对称**：下沉到底层自动覆盖
- ✅ **§2.4 多路由 audit 缺失**：逐个补齐
- ✅ **§2.4 错误回显异常**：改结构化 error + redact
- ✅ **§2.4 /api/risk/status 默认 within_limits**：改 unknown
- ✅ **§2.4 /api/health 硬编码 ok**：聚合真实下游状态
- ✅ **§2.4 entity_id 缺位**：补 sentinel
- ✅ **§2.4 approve_proposal 无 try/except**：捕获后转 error + audit
- ✅ **§2.5 scheduler 异常处理不一致**：抽取 _safe_run wrapper
- ✅ **§2.5 paper_trader 双门不在 agent body**：复制进 body
- ✅ **§2.6 KB 写入非原子**：先 insert + commit + 失败回滚
- ✅ **§2.6 Markdown 路径遍历**：strategy_id 强校验
- ✅ **§2.7 healthcheck 不跳过 disabled**：加 enabled 检查
- ✅ **§2.7 Telegram unauthorized 回消息**：不回 + 去重 audit
- ✅ **§2.7 Telegram polling 无退避**：加指数退避 cap 60s
- ✅ **§2.7 Codex auth 正则误匹配**：锚定上下文后匹配
- ✅ **§2.8 CORS 未配置**：加 localhost-only CORS + bind_host 校验
- ✅ **§2.8 autostart 无 audit**：路由层补 audit
- ✅ **§2.5 AllowEditing 空模式**：标记 DEPRECATED + `is_tool_implemented()` 守门（2026-06-18）
- ✅ **§2.6 create_strategy_memory 非原子**：补齐 try/except + path.unlink 回滚（2026-06-18）
- ✅ **§2.8 settings credentials_path 泄露**：tunnel 部分排除 credentials_path（2026-06-18）

### 10.3 §3 Dead code / Cosmetic 修复

- ✅ **dead imports**：paper_trader / orchestrator 清理
- ✅ **log_tool 不记录 input_data**：加 input 参数
- ✅ **orchestrator 吞异常无 traceback**：改 format_exc + audit
- ✅ **Opportunity.market 硬编码 spot**：按 instruments 配置
- ✅ **agent 数量文档矛盾**：统一
- ✅ **paper_running→paper_passed 无门控**：加 check_promotion_gates
- ✅ **promote_strategy audit 语义错误**：retire/deprecate 用专用 event_type
- ✅ **PID 文件 recycled-PID**：匹配 _GATEWAY_SUPERVISOR_MARKER 命令行
- ✅ **run_logger NULL vs 空**：始终写 '[]'
- ✅ **Bitget order_id 空串**：空时 raise
- ✅ **router healthcheck 不跳 disabled**：加 continue

---

## 12. WebUI 重做留下的后端缺口（2026-06-07，前端 session 产出）

> 前端完成了结构性重做（14 页 → 10 个角色化目的地，见 `docs/WEBUI_PAGE_MAP.md`）。
> 全程 **frontend-only**，未改任何 API 契约。下列是前端已留好挂载点、等后端补能力的缺口。
> **完整对接说明见 `docs/WEBUI_BACKEND_HANDOFF.md`**（含每条的前端 touch point 与诚实降级串）。
> 前端对每条都已诚实降级（"not recorded" / "pending backend" / 标注 caveat），不阻塞。

- **12.1 🔴 `positions` 缺 `proposal_id`/`order_id` FK** — 因果链 position→上游方向无法落地。
  与 §1.1 的 strategy_id 断链同源但不同点：即使 strategy_id 补全，position 行仍无法回指
  具体 proposal/order。前端 `CausalChain.tsx` 锚定 position 时上游节点显示 "not recorded"
  （黄色），**禁止按 symbol 猜测伪造**（trust §1.1）。修复：给 `positions` 加 nullable
  `proposal_id`（execution 路径开仓时写入）。
- **12.2 🟡 `GET /api/agent-runs/{id}/steps` 未建** — 已在重做 plan 中授权的只读端点，
  暴露现有 `agent_run_steps` 表（逐步推理 + 工具 in/out）。前端 `AgentActivity.tsx` 的
  "Reasoning Steps" 块已是稳定挂载点，当前显示 tool-calls + "pending backend" 提示。只读，
  放 `data.py`。
- **12.3 🟠 真实交易所账户权益（portfolio-sync-gap）** ✅ 已修复 — Exchange Account Sync 层
  已建（`aletheia/portfolio/exchange_sync.py`），`/api/portfolio` 新增 `exchange_equity_usdt` +
  `exchange_asset_breakdown`。Dashboard `PnLZone.tsx` 显示真实余额 + 饼图 + hover tooltip。
  Bitget demo 账户 198 USDT 已正确同步（commit `e3dd86e`, `754b703`）。
  ⚠️ **残留限制**：Bitget demo key 的 spot API (`/api/v2/spot/account/assets`) 不返回数据，
  demo 账户 spot 钱包内的 BTC 无法通过 API 读取。当前 breakdown 仅含 USDT（来自
  `mix/account/accounts` 的 usdtEquity 字段）。Live key 或真实账户可正常返回 spot 余额，
  多币种饼图在那时才会生效。这是 **Bitget API 对 demo key 的限制**，非代码问题。
  详见 `aletheia/portfolio/exchange_sync.py:182-198`（spot 路径说明注释）。

---

## 备注

- 本清单不试图穷举；6 个 subagent 各按其模块簇覆盖了一遍，代表当下视图。
- 修复优先级建议：1.3（异步契约/supervisor）+ 1.2（live 半截 fill）+ 1.1（晋级机制）
  优先；2.x 纳入日常 trust hardening 周期；3.x 趁手就修。
- 凡涉及 change-controlled 模块（`risk/` `execution/` `exchanges/bitget/auth.py`
  `exchanges/bitget/client.py` `config/secrets.py` `agent_runtime/permissions.py`
  `server/routes/live.py`）的修复，必须先开 plan + 焦点回归测试。

---

## 11. 已知盲区（本清单**未**覆盖的部分，诚实记录）

> 本清单基于 6 个并行 subagent 的**静态代码审计**——读源码 + 对照 trust/AGENTS
> 文档。下面这些盲区不在本次审计的能力范围或扫描范围内，未来若发现"BACKEND_ISSUES
> 没记录但确实存在"的问题，大概率属于这些类别之一，应增量补入。

### 11.1 模块/路径盲区

- **未审 `aletheia/main.py`**（38 行 CLI 入口）——subagent 分簇时漏分配；规模小但既是 CLI 总入口，应单独扫一遍。
- **未审 `aletheia/dashboard_static/`**——构建产物 + 任何手写 JS 都未审。前端审计应在前端任务中进行（CLAUDE.md 已声明前端是下一阶段）。
- **未审 `tests/` 本身的健康度**——是否有过时测试、是否覆盖了"不该测的"不变量、是否有覆盖盲区。这属于"测试质量审计"，是另一类工作。
- **`aletheia/_gateway_runner.py` 与 `aletheia/gateway/lifecycle.py` 的进程边界**只浅扫了 supervisor 计数器问题（§1.3），更深的"进程信号传递 / -9 KILL 后的状态恢复"未压力测试。

### 11.2 方法论盲区（静态审计的天花板）

- **运行时 race / 并发问题**：除 §2.3 的 daily-loss kill_switch TOCTOU 外，其他并发路径（如 scheduler 多 job 同时操作同一 SQLite 连接、orchestrator 并发 agent run 共享 `init_tool_registry()` 的全局状态）未做 race analysis。要确认得跑 stress test。
- **APScheduler 实际行为偏移**：`misfire_grace_time` 在不同时区/夏令时下的实际触发，scheduler 暂停后恢复时的"积压触发"行为——需要真实运行 7+ 天观察。
- **Bitget API 真实边缘**：所有 client.py 相关条目都基于"读 nautilus_trader 文档 + 自家代码"推断；真实场景下 Bitget 的部分成交格式、IP 限频、维护窗口期 503 等响应未做契约测试。
- **NautilusTrader 升级影响**：本仓不依赖 NautilusTrader，但 nautilus-practice 参考系（北极星 §10）依赖；任何"借鉴它的 funding 熔断思路"前要先核实 NT 1.223 的 funding rate API 在我们 Bitget 适配里如何暴露。

### 11.3 性质类盲区（本清单不打算覆盖）

- **性能问题**：除 §2.6 的索引缺失外，未做 profiling；CPU/内存/IO 热点不在审计范围。
- **代码风格 / 重复 / 抽象不足**：除非影响信任不变量或数据正确性，否则不进本清单。
- **文档与 README 同步**：除 §3 中"agent 数量 7 vs 8"外，未做 docs vs code 全面对照。
- **依赖安全（CVE 扫描）**：`uv.lock` 内的 transitive deps 未做漏洞扫描——属于 supply-chain 工作，与本清单（结构性问题）正交。

### 11.4 增量维护原则

发现新问题时按以下规则归入：

| 问题类型 | 归入位置 |
|---|---|
| 影响 trust 不变量（§1 类）/ 因果链 / live 安全 / secret 处理 | §1 🔴 |
| 数据失真 / 学习失效 / audit 漏写 / promotion gate 错算 | §2 🟠 |
| Dead code / cosmetic / future tripwire | §3 🟡 |
| 覆盖范围超出本清单（如 docs 不一致、性能、CVE） | 不进本文件——另起对应文档 |

修复完的条目移至 §10 历史区，保留 file:line 与修复 commit hash。

---

## §5. ✅ OAuth Subscription Provider — 已修复（2026-06-14）

> **状态**：§5.1–5.4 全部已修复（04e0938 + 本次补全 model_metadata 到所有 provider）。
>
> 原始发现保留作审计参考。

### 5.1 ✅ OpenAI OAuth persist 挂在 GET status 上（副作用 GET）

- 修复：`GET /model/openai-oauth/status` 现仅调用 `get_status()`（纯读）；
  persist 迁移至幂等 `POST /model/openai-oauth/finalize`，前端显式触发。
  幂等保护：已存在 `oauth_token_ref` 时跳过覆盖。
- 佐证：`aletheia/server/routes/model.py:361-394`

### 5.2 ✅ aiohttp 为 transitive dependency，无显式声明

- 修复：`pyproject.toml` 已显式声明 `aiohttp>=3.9`（line 20）
- 佐证：`pyproject.toml:20`

### 5.3 ✅ OAuth token refresh 无 async path

- 修复：新增 `_do_refresh_async()` + `_resolve_oauth_token_async()` + `get_provider_async()`；
  healthcheck 走 async 路径，config save 通过 `run_in_executor` 避免阻塞 event loop
- 佐证：`aletheia/model_provider/router.py:98-155, 227-278`

### 5.4 ✅ healthcheck / model 列表只返回 slug，丢失 metadata

- 修复：`ModelHealth` dataclass 新增 `model_metadata: list[dict]` 字段；
  所有 provider（openai_native, anthropic_native, openai_compatible, anthropic_compatible,
  databricks, codex_cli, claude_cli）的 healthcheck 均传入 `model_metadata=models`；
  `/api/model/health` 端点返回完整 metadata
- 佐证：`aletheia/model_provider/base.py:45-46`, 各 provider healthcheck 方法

---

## 6. 🟡 数据层结构性技术债（2026-06-16 数据源审计）

> **来源**：2026-06-16 数据层全景审计，发现 4 个隐患，其中 §6.1（Binance 硬编码）和 §6.2（yfinance 脆弱性）已在同日修复。§6.3/6.4 记录备查。

### 6.1 ✅ 清算数据 Binance 硬编码与 primary exchange 配置解耦

- **原因**：`_fetch_liquidations()` 硬编码向 `fapi.binance.com` 发起 REST 请求，与 `market_data.ccxt_exchange` 配置完全无关。当 primary 为 Bybit 时，资金费率/OI 来自 Bybit，但清算事件来自 Binance，静默混用两个市场数据，无任何提示。
- **佐证（修复前）**：`aletheia/data/collectors/derivatives.py:134-177`（旧 `_fetch_liquidations`，exchange 字段写死 `"binance"`）
- **修复**：
  - 新增 `_fetch_liquidations_ccxt(exchange, primary_name, symbols)` — 检查 CCXT `exchange.has["fetchLiquidations"]`，支持时优先走 CCXT 统一接口，liquidation 记录的 exchange 字段与 funding/OI 一致
  - 新增 `_CCXTLiquidationsUnsupported` sentinel exception — CCXT 不支持时抛出，由 `collect()` 捕获后 fallback 到 Binance REST
  - 旧函数改名为 `_fetch_liquidations_binance_rest()`，注释明确标注"fallback, exchange metadata mismatch"
  - 当 primary != binance 且触发 fallback 时，`logger.debug` 明确说明 exchange mismatch
- **佐证（修复后）**：`aletheia/data/collectors/derivatives.py`（`_CCXTLiquidationsUnsupported`, `_fetch_liquidations_ccxt`, `_fetch_liquidations_binance_rest`）

### 6.2 ✅ yfinance scraper 脆弱性：失败静默，无可观测告警

- **原因**：yfinance 是非官方 scraper，Yahoo Finance 历史上多次 breaking change。失败时三处代码均静默（`continue`/`return None`/`return ""`），agent 行为变化无任何可见信号。
- **佐证（修复前）**：
  - `aletheia/data/yfinance_provider.py:60-65` — all tickers empty 时仅返回 `""`
  - `aletheia/data/collectors/macro.py:168-169` — `ImportError` 静默返回 `None, None, None, None`
  - `aletheia/agents/stock_observer.py:133-134` — 外层 `except Exception: return None` 吞掉 ImportError
- **修复**：
  - `yfinance_provider.py`：全部 ticker 空时升级为 `logger.warning`，`ImportError` 单独 WARN
  - `macro.py`：`ImportError` 加 `logger.warning`；全部 ticker None 时加 `logger.warning`；各 ticker 异常改为 `logger.debug`（有 file:ticker 信息）
  - `stock_observer.py`：提前单独 `try/except ImportError` + WARN，其余异常保持静默（单 ticker 失败不影响其他）；补充 `logger = logging.getLogger(...)`
- **佐证（修复后）**：上述三个文件

### 6.3 🟡 只有 REST 轮询，无流式数据——决策频率天花板

- **原因**：所有数据来源均为快照轮询（OHLCV TTL 10 min、orderbook 点时刻切片）。没有 WebSocket 订阅机制。
- **影响**：系统天花板为日内到日线频率策略。高频实盘时此架构需要重设计。
- **佐证**：`aletheia/data/ccxt_provider.py`（get_ohlcv/get_ticker 均为 REST），`aletheia/data/collectors/microstructure.py`（fetch_order_book 一次性调用）
- **最小修复**：MVP 阶段不需要修，上实盘前需评估目标频率并决定是否引入 CCXT Pro WebSocket。
- **状态**：🟡 已知限制，暂不修

### 6.4 🟡 OHLCVCache 不覆盖三个 collector，但实际无并发风险

- **原因**：`MicrostructureCollector`/`DerivativesCollector`/`VolumeScannerCollector` 每次调用都新建 `CCXTProvider` 实例，不走 `OHLCVCache`。但这三个 collector 在 scheduler 中是顺序执行（见 `scheduler.py:760-769`），不存在真正的并发 provider 冲突。
- **影响**：`load_markets()` 被调用两次（micro 一次、deriv 一次），轻微效率损耗，无数据正确性问题。
- **佐证**：`aletheia/agent_runtime/scheduler.py:760-769`
- **最小修复**：可将两个 collector 共享同一 provider 实例，但改动涉及 collect 接口，优先级低。
- **状态**：🟡 已知，暂不修

### 6.5 🟡 test_startup_gate 的 `_FakeResp` 缺 `.raw`，与生产代码漂移

- **原因**：`startup_gate.py:64` 在 content 为空时读 `resp.raw` 取 `completion_tokens`（thinking-mode 模型把 budget 花在推理上、text 为空但确实跑了的兜底判定）。真实 `ModelResponse`（`model_provider/base.py:25` `raw: Any = None`）有此字段，但测试桩 `tests/test_startup_gate.py:46-48 _FakeResp` 只定义了 `.content`，没有 `.raw`。
- **影响**：`test_empty_response_not_usable` 失败——期望 `error == "empty_response"`，实际命中 `AttributeError: '_FakeResp' object has no attribute 'raw'`（被 `_probe_one` 的 except 捕获返回为错误串）。**仅测试桩漂移，生产路径正确**。
- **佐证**：`aletheia/agent_runtime/startup_gate.py:64`、`aletheia/model_provider/base.py:25`、`tests/test_startup_gate.py:46-48`
- **最小修复**：给 `_FakeResp.__init__` 加 `self.raw = None`（或接受 `raw` 参数），使桩与真实 `ModelResponse` 对齐。
- **状态**：✅ 已修（2026-06-16，与 ABCD 同批提交）。`_FakeResp.__init__` 增加 `raw=None` 参数并赋 `self.raw`，对齐真实 `ModelResponse`；`test_startup_gate.py` 14/14 绿。

---

## 13. 🟡 低影响结构问题（2026-06-19 altcoin scale 审计）

以下两条问题在 altcoin scale 审计中发现，影响等级低（不阻塞执行，无信任不变量风险），记录供后续迭代参考。暂不修复。

### 13.1 🟡 Risk Engine 缺 per-symbol ATR 动态仓位验证

- **原因**：`aletheia/risk/engine.py` 的仓位规模检查仅依赖全局 `max_order_usdt`（配置固定值），不感知 symbol 的 ATR 波动率。Paper trader 层已加 `_atr_adjusted_notional()`（Batch 2 改动），但 risk engine 本身看不到 ATR，无法对高波动率 altcoin 执行额外仓位收缩。
- **影响**：若未来实盘开放，高 ATR 币种（如 PEPE ATR=5%）的仓位风险未被 risk engine 双重校验。当前 paper 模式下影响有限。
- **佐证**：`aletheia/risk/engine.py`（max_order_usdt 检查）、`aletheia/agents/paper_trader.py:_atr_adjusted_notional()`
- **最小修复**：在 risk engine 的仓位规模检查前查 `universe_feature_snapshots.atr_pct`，ATR > 3% 时收紧 `effective_max_usdt = max_order_usdt * (1.5 / (atr_pct * 100))`。
- **状态**：🟡 待后续迭代。不阻塞当前 MVP。

### 13.2 🟡 Calibration Multiplier 只看单一 horizon，多 horizon 数据浪费

- **原因**：`aletheia/universe/allocator.py:get_family_multipliers()` 每个 family 只取 `_FAMILY_CALIBRATION_HORIZON` 中指定的单一 horizon（如 derivatives="1h"）计算 multiplier，其他 horizon（15m/4h/24h）的数据完全未使用。
- **影响**：当指定 horizon 样本量不足时（n < 10 → multiplier = 1.0 中性），即便其他 horizon 有充足数据也无法贡献。此外，单一 horizon 的 win_rate 估计方差较高，多 horizon 加权平均理论上更稳健。
- **佐证**：`aletheia/universe/allocator.py:52-68`（只取 `expected_horizon` 行）、`aletheia/storage/repositories.py:calibration_stats()`（已返回全部 horizon 数据）
- **最小修复**：改为 `multipliers[family] = weighted_average(rows_for_family, weight=n)`，所有 horizon 按样本量加权。需评估持仓周期与 horizon 的匹配关系再决定权重方案。
- **状态**：🟡 待积累更多数据后评估。数据量充足前改动收益有限。

---

## §13. 跨模块数据契约违规 (2026-06-23 审计)

> 来源：SDD 第一性原理审计 — 逐条 producer-consumer 数据流追踪
> 已修 2 条，冻结区待修 2 条

### §13.1 🟠 paper_trader_ranker evidence 查询硬编码 paper_internal

- **位置**：`aletheia/agents/paper_trader_ranker.py:723`
- **问题**：`WHERE execution_mode = 'paper_internal'` 只统计 paper_internal 模式的交易证据，忽略 bitget_demo/okx_demo/binance_testnet 的已平仓 positions
- **影响**：策略在 OKX demo 跑了 20 笔交易但 evidence = 0 → 被分类为 "needs_backtest" → ranker 给惩罚分 → 好候选被压制。50% 交易数据对决策不可见
- **修复**：`execution_mode IN ('paper_internal','bitget_demo','binance_testnet','okx_demo')`
- **状态**：🟠 **EXPERIMENT FREEZE** 冻结区，Round 2 首日修复。影响 AI 决策 → 不能在观察期内动

### §13.2 🟠 trading_context today_pnl 字段名与 API 语义不一致

- **位置**：`aletheia/agent_shell/trading_context.py:75`
- **问题**：`layer["today_pnl"] = round(today_realized_pnl(), 2)` 存的是 realized-only，但 `/api/portfolio` 的 `today_pnl` = realized + unrealized
- **影响**：Agent 被告知 "Today PnL: +8.49" (closed only)，Dashboard 显示 "Today PnL: -4.5" (含浮盈) → 同一名称不同语义
- **修复**：字段改名为 `today_realized_pnl`，prompt 中标注 "closed positions only"
- **状态**：🟠 **EXPERIMENT FREEZE** 冻结区，Round 2 修复

### §13.3 ✅ fee_rate 前缀判断错误 — 已修 (3377d48)

- **位置**：`aletheia/portfolio/pnl.py:114`
- **问题**：`startswith("paper")` 只匹配 paper_internal，bitget_demo/okx_demo 被当 live 收费
- **修复**：改为 `in _DEMO_MODES` 元组精确匹配
- **状态**：✅ 已修复

### §13.4 ✅ direction_bias 未归一化 — 已修 (3377d48)

- **位置**：`aletheia/portfolio/market_signal_reader.py` → `exit_rules.py:56`
- **问题**：exit_rules 做 exact match ("long"/"short"/"neutral")，但 opportunities 可能存 "bullish"/"bearish" → 退出规则被完全绕过
- **修复**：`market_signal_reader` 新增 `_normalize_direction()` 在读取时统一词汇表
- **状态**：✅ 已修复

---

## §14. API 路径漂移修复后遗留的安全设计缺陷 (2026-06-23)

### §14.1 🔴 kill-switch-release-missing-confirm

- **位置**：`aletheia/server/routes/risk.py:85-92`
- **问题**：`POST /api/risk/kill-switch` 的 release 路径（`active: false`）**不要求 confirm token**。原始安全设计意图为两步确认（activate 立即生效，release 需额外确认），防止误操作恢复交易。当前代码中 activate 和 release 走同一请求体 `{"active": bool, "reason": ""}` — release 无需任何额外声明即可执行。
- **影响**：UI 或程序化调用可在无二次确认的情况下恢复全局交易制动器；违反 `docs/design/risk_engine_contract.md §4` 所述的"human-confirmed release"不变量。
- **最小修复建议**：在 `KillSwitchRequest` 增加 `confirm_release: bool = False` 字段，当 `active=False` 时若 `confirm_release` 非 `True` 则返回 `400`；或引入独立的 `POST /api/risk/kill-switch/release` 端点要求显式确认字段。
- **优先级**：🔴 信任边界
- **状态**：🔴 **待修复**（需显式授权后施工，涉及 trust boundary 路径）

---

## §15. SDD spec 声明的代码缺口（2026-06-23 ultracode 全量扩充批次）

### §15.1 OL-C03 🔴 Level-3 操作缺乏 FORBIDDEN_INTENTS 双锁覆盖

- **位置**：`aletheia/agent_shell/operator_loop.py` `_LEVEL_3_ACTIONS` 集合
- **问题**：`_LEVEL_3_ACTIONS` 含 `deactivate_kill_switch` 与 `modify_permissions` 两项，**均未**出现在 `agent_shell/registry.py:415` 的 `FORBIDDEN_INTENTS` 或 `agent_runtime/tool_registry.py:39` 的 `RESTRICTED_TOOLS` 中。其余三项（`enable_live_mode`/`modify_risk_limits`/`modify_credentials`）已被 `FORBIDDEN_INTENTS` 双锁覆盖。
- **影响**：违反 `docs/design/operator_layer.md §6 / live_unlock_contract §6` 的"双锁"防御纵深约定。Operator 层声明这两个动作为 Level 3，但 `permissions.check_permission` 不会拒绝它们 — 安全靠 operator_loop 自身分支判定，缺少多层兜底。
- **Spec 锚点**：`tests/specs/spec_operator_loop.py::test_level3_defense_in_depth`（OL-C03 `status="partial"`），当前 `@pytest.mark.xfail(strict=True)` 声明缺口。
- **最小修复建议**：在 `agent_shell/registry.py:415` 的 `FORBIDDEN_INTENTS` 追加 `"deactivate_kill_switch", "modify_permissions"`；修复后将 OL-C03 改为 `status="implemented"` 并移除 xfail。
- **优先级**：🔴 信任边界
- **状态**：🔴 **待修复**（涉及 trust boundary，需显式授权）

### §15.2 OL-C07 🟡 _execute_level1_action 含 Level-2 死分支

- **位置**：`aletheia/agent_shell/operator_loop.py` `_execute_level1_action` 函数体
- **问题**：`_execute_level1_action` 内含 `elif action.action_type == "<name>"` 分支处理 `loosen_confidence` / `increase_sizing` / `widen_stop_loss` 三项，但这三项的权威分类是 `_LEVEL_2_ACTIONS`（要求 human proposal）。集合定义正确，但代码路径让 L1 自动执行器也能进入这些 L2 动作的处理逻辑，破坏 L1/L2 边界完整性。
- **影响**：当前**无实际危害**（调度入口按集合判定优先），但属于"set says L2, code path says L1"的语义自相矛盾。未来重构 dispatch 入口时可能让死分支重新激活，制造静默的权限绕过。
- **Spec 锚点**：`tests/specs/spec_operator_loop.py::test_loosening_actions_in_level2_not_level1`（OL-C07 `status="missing"`），当前 `@pytest.mark.xfail(strict=True)` 声明缺口。
- **最小修复建议**：删除 `_execute_level1_action` 中三个 elif 分支；修复后将 OL-C07 改为 `status="implemented"` 并移除 xfail。
- **优先级**：🟡 dead code / future-violation tripwire
- **状态**：🟡 **待修复**（operator_loop.py 不在 experiment freeze 区，可直接修）

### §15.3 (跨子条目) data_layer Phase 3 未完成

- 详见 `docs/design/data_layer.md` 中 Phase 3.1/3.6 标注。`BITGET_GRANULARITY` 仍存在于 `aletheia/trading/instruments.py:21`；`tradable_symbols_from_config()` 仍返回 Bitget 格式而非 canonical。
- **影响**：paper_trader 过滤器处存在静默的符号格式不匹配风险。
- **优先级**：🟡
- **状态**：🟡 **待修复**

### §15.4 EQUITY §6.3 Phase 3 KB 写回缺失

- 详见 `docs/design/EQUITY_INTEGRATION_DESIGN.md §6.3`。equity_observer 当 confidence>0.7 时应回写 `research_note` 至 KB，代码无此路径。
- **优先级**：🟡
- **状态**：🟡 **待修复**

### §15.5 PI_OPERATOR "连续 3 次同方向升级" 规则未实现

- 详见 `docs/design/PI_OPERATOR_DESIGN_DECISIONS.md` Problem 3。文档规定连续 3 次同方向（如三连续 tighten）应自动升级至 Level 2 请求人工确认，operator_loop 中无 direction-counting 逻辑。
- **影响**：长期单向调参无升级阻断，可能让 agent 在自动权限内反复同向 patch。
- **优先级**：🟡
- **状态**：🟡 **待修复**
