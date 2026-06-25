# 冻结期未做 / 待做事项备忘 (Frozen / Deferred Backlog)

> **本文件作用**:记录 Round 1 冻结期内**调研出来但当前不能做**的所有优化点,
> 包括判定理由、解冻条件、具体操作步骤。**目的:防止知识丢失**,任何后续会话都能
> 重新接续。**严禁删除**条目,只允许添加完成标记。
>
> **创建时间**:2026-06-24
> **关联文档**:`round2_week1_decision_chain_fixes.md`(Round 2 第一周必做)
> **冻结约束**:见 `CLAUDE.md` "EXPERIMENT FREEZE" 段 + `docs/product/EXPERIMENT_PROTOCOL.md`

---

## 索引(按解冻条件分类)

### A. 必须等 Round 1 判决后(触冻结路径)
- [A1] L-Bug-A 修复 Layer3 lessons 过滤值
- [A2] L-Bug-A' 修复 Layer3 guards 查询缺 item_type 参数
- [A3] L-Bug-B 接入 ps_lessons 到决策链
- [A4] S-Bug-C strategy_records 暴露 win_rate/total_pnl/trade_count
- [A5] CR-EX-03 bitget_broker Position 补 opportunity_id
- [A6] §13.1 paper_trader_ranker evidence 硬编码 paper_internal
- [A7] §13.2 trading_context today_pnl 语义不一致
- [A8] §16.3 KL-3 Layer 3 wisdom 过滤类型写错 (=A1)
- [A9] §18.2 OP-WINRATE operator win_rate 恒 0%
- [A10] PIPELINE_GAPS D1 calibration_feedback 未注入 LLM prompt
- [A11] PIPELINE_GAPS D5 Regime Detection 重启后 10min 用错误默认值
- [A12] PIPELINE_GAPS A1 dreaming_agent ps_lessons 永不进决策 (=A3)
- [A13] PT-2 / PL-PT-04 paper_trader 补 opportunity_id 给 PiDecision
- [A14] Self-Evolution Tier 2 EvolutionSynthesizerAgent

### B. 信任边界内(需用户显式授权)
- [B1] §14.1 kill-switch release 加 confirm_release 二次确认
- [B2] APP-HMAC `app.py:428` 改 `hmac.compare_digest`
- [B3] §16.2 CR-EX-03 bitget_broker (重复登记,见 A5)
- [B4] type:ignore workaround → PostSubmitDBError(binance/okx broker)

### C. 当前 indirect-risk(可能间接影响 PI 输入),等 Round 1 结束后 safe
- [C1] prune_system_actions TTL 7d(scheduler + repositories)
- [C2] prune backtest_runs best-per-strategy

### D. 安全可做但未列入"立即落地"(本会话排序后劣后,留作下次)
- [D1] Serenity 三表 TTL 剪枝(serenity_evidence/run_events/completed_empty)
- [D2] system_action 写入去重 guard
- [D3] dreaming_log 悬挂指针清理
- [D4] insight_synthesizer 静默失败排查
- [D5] strategy_experiments 表 DROP / 标 dead
- [D6] /api/analytics/strategy-performance 端点(JOIN backtest_runs)
- [D7] /api/analytics/serenity-pipeline 端点
- [D8] test_architecture_fitness.py 加 trading_context 守卫(xfail)
- [D9] CR-SCH-01 24 个 scheduler job 补 _record_job_failure
- [D10] Strategy Pool Stage 1+2 direction_affinity/regime_tags 字段 + Ranker
- [D11] Windows autostart schtasks 可靠性加固
- [D12] detect_contradictions() stub 实现
- [D13] G2 agents executor 线程数评估
- [D14] PIPELINE_GAPS §C2 RegimeConfig/GuardConfig 前端控制
- [D15] reconciler 批量同秒强平模式 — 需独立调研(2026-06-24 本会话新增)
- [D16] OKX universe 含不可交易标的 — opportunity 浪费(2026-06-24 本会话新增)
- [D17] 5bfa4a0 IOCP 修复的 3 个 follow-up(2026-06-24 本会话新增)
- [D18] trust.audit_chain hash mismatch — audit.jsonl 10 处断链(2026-06-25)
- [D19] data.signal 24h win_rate 35% — scanner 选品质量偏低(2026-06-25)
- [D20] 并行 Claude /loop 会话干扰 git history(2026-06-25)

### E. 战略决策类(需独立讨论)
- [E1] OKX 51155 compliance 错误的战略含义(换交易所?demo≠live 失效?)
- [E2] Round 1 G6 不可达的应对(11/60 笔 vs 12 天窗口)

---

## A. 触冻结路径条目(必须等 Round 1 判决后)

### [A1] L-Bug-A 修复 Layer3 lessons 过滤值

**位置**:`aletheia/agent_shell/trading_context.py:379-388` (函数 `_build_layer3_wisdom`)
**问题**:过滤集合 `('lesson','accepted_lesson','trading_guard')` 在 DB 中 0 命中,
真正 lesson 内容存在 `post_trade_review`/`trade_case_study` 类型中
**亲核数字 (2026-06-24)**:lesson|accepted_lesson|trading_guard 三类共 0 行;
trade_case_study 496 行 + post_trade_review 1679 行
**修复**:见 `round2_week1_decision_chain_fixes.md` Fix L-Bug-A 全文
**对应 spec**:`tests/specs/spec_learning_pipeline.py::test_layer3_lessons_filter_aligned`
**解冻条件**:Round 1 判决完成

---

### [A2] L-Bug-A' Layer3 guards 查询加 item_type 参数

**位置**:`aletheia/agent_shell/trading_context.py:398`
**问题**:`list_knowledge_items(limit=30)` 不带 item_type 参数,全表 rowid DESC 扫,
被高频 system_action 占满 → 81 条 guard_candidate 永远排不进窗口
**亲核数字 (2026-06-24)**:当前 limit=30 窗口里 29 个 system_action + 1 operator_assessment + 0 guard_candidate
**修复**:一行改 `list_knowledge_items(item_type='guard_candidate', limit=30)`
**对应 spec**:`tests/specs/spec_learning_pipeline.py::test_layer3_guards_filter_via_query`
**解冻条件**:Round 1 判决完成

---

### [A3] L-Bug-B ps_lessons 接入决策链

**位置**:`aletheia/agent_shell/trading_context.py` + `aletheia/agent_shell/trading_decision.py`
**问题**:整个 aletheia/ 中**零 SELECT FROM ps_lessons** 代码;184 条 status='proposed' 完全无消费
**修复**:见 `round2_week1_decision_chain_fixes.md` Fix L-Bug-B 全文(含 list_ps_lessons 仓库函数)
**对应 spec**:`tests/specs/spec_learning_pipeline.py::test_ps_lessons_consumed_in_prompt`
**解冻条件**:Round 1 判决完成 + 同时需在 `repositories.py` 新增 `list_ps_lessons()` 函数

---

### [A4] S-Bug-C strategy_records 暴露 win_rate / total_pnl / trade_count

**位置**:`aletheia/agent_shell/trading_context.py`(`_build_layer2_strategies` 或类似)
**问题**:DDL 17 列里无 win_rate/total_pnl/trade_count;PI prompt 拿到全是 None
**正确入口**:`aletheia/analytics/trade_stats.py:227 build_strategy_kpis()`
返回 `{strategy_id: {trade_count, win_rate, total_pnl}}`
**修复**:见 `round2_week1_decision_chain_fixes.md` Fix S-Bug-C 全文
**对应 spec**:`tests/specs/spec_strategy_pool.py::test_strategy_pool_exposes_kpi`
**解冻条件**:Round 1 判决完成
**坑**:综合 agent 之前写过 `per_strategy_kpi_summary` —— **不存在!** 真函数名是 `build_strategy_kpis`

---

### [A5] CR-EX-03 bitget_broker Position 补 opportunity_id

**位置**:`aletheia/execution/bitget_broker.py:141-149`
**问题**:bitget broker 构造 Position 时不传 opportunity_id,risk check #16 静默失效
**亲核状态**:截至 2026-06-24,**未实施**(BACKEND_ISSUES.md §16.2)
**修复**:在 Position 构造处补 `opportunity_id=order.get('opportunity_id')`
**解冻条件**:此项触信任边界 `aletheia/execution/`,**需用户显式授权**(同时也归 B 类)

---

### [A6] §13.1 paper_trader_ranker evidence 硬编码 paper_internal

**位置**:`aletheia/agents/paper_trader_ranker.py:723`
**问题**:evidence 收集时执行模式被硬编码为 `paper_internal`,但实际 paper_execution_mode 可能是 okx_demo / bitget_demo
**亲核状态**:截至 2026-06-24,**未修**(BACKEND_ISSUES.md §13.1)
**修复**:从 `cfg.exchange.paper_execution_mode` 读真实值
**解冻条件**:paper_trader_ranker.py 在冻结清单,需 Round 1 判决后

---

### [A7] §13.2 trading_context today_pnl 语义不一致

**位置**:`aletheia/agent_shell/trading_context.py:75` 附近
**问题**:today_pnl 字段语义在不同分支不一致(单 broker vs 全模式)
**亲核状态**:BACKEND_ISSUES.md §13.2,未修
**解冻条件**:冻结路径,Round 1 判决后

---

### [A8] = [A1](去重,源自 BACKEND_ISSUES.md §16.3 KL-3)

---

### [A9] §18.2 OP-WINRATE operator win_rate 恒 0%

**位置**:`aletheia/agent_shell/operator_loop.py:395-399`
**问题**:operator_loop 计算 win_rate 时用 fills(无 realized_pnl),win_rate 永 0
**亲核状态**:BACKEND_ISSUES.md §18.2,未修
**修复**:改用 positions(status='closed').realized_pnl > 0 的比例
**解冻条件**:operator_loop.py 在冻结路径(agent_shell/),Round 1 判决后

---

### [A10] PIPELINE_GAPS D1 calibration_feedback 未注入 LLM prompt

**位置**:`aletheia/agent_shell/trading_decision.py`
**问题**:历史预测准确度未喂回 PI 提示词,calibration_feedback 永远是 prompt 缺失字段
**解冻条件**:冻结路径,Round 1 判决后

---

### [A11] PIPELINE_GAPS D5 Regime Detection 重启后默认值问题

**位置**:`aletheia/regime/detector.py:261`(已部分修)
**现状**:上会话提交 80f944c 已加 DB 恢复逻辑(`get_current_regime` 在 cache 空时从 DB 回填),
**但 detector 主路径在冻结清单**,完整修复(从 DB 恢复并触发 publish)需等 Round 1
**解冻条件**:Round 1 判决后

---

### [A12] = [A3](去重,源自 PIPELINE_GAPS.md A1)

---

### [A13] PT-2 / PL-PT-04 paper_trader 补 opportunity_id 给 PiDecision

**位置**:`aletheia/agents/paper_trader.py:138` 附近
**问题**:PiDecision 缺 opportunity_id 关联,审计链断裂
**亲核状态**:PL-PT-04 spec claim status="drift",未修
**解冻条件**:paper_trader.py 在冻结清单,Round 1 判决后

---

### [A14] Self-Evolution Tier 2 EvolutionSynthesizerAgent

**位置**:设计文档 `docs/plans/self_evolution_plan.md`(Tier 1 已实施 1a75dc7)
**状态**:Tier 2 是 LLM-driven 知识合成,**待 Tier 1 数据积累后实施**
**解冻条件**:Round 1 数据 + Tier 1 guard_candidate 数据(当前 81 条)累积足够

---

## B. 信任边界(需用户显式授权)

### [B1] kill-switch release 加 confirm_release 二次确认

**位置**:`aletheia/server/routes/risk.py:85-92`
**意图**:防止误触发解锁(目前 POST 一下就解了)
**修复**:加 `confirm_release: bool = False` 参数,False 时返回 422 + 提示需要二次确认
**为何需授权**:触 `aletheia/server/routes/risk.py`(信任边界),非冻结但需用户批准

---

### [B2] APP-HMAC `app.py:428` 改 hmac.compare_digest

**位置**:`aletheia/server/app.py:428`(`_tunnel_auth_guard` 中间件)
**问题**:当前 `if token != expected` 是普通字符串比较,有时序侧信道风险
**修复**:`import hmac` + `if not hmac.compare_digest(token, expected):`
**为何需授权**:触信任边界 + 改鉴权逻辑,需用户批准

---

### [B4] type:ignore workaround → PostSubmitDBError

**位置**:`aletheia/execution/binance_broker.py` + `aletheia/execution/okx_broker.py` 两处 type:ignore
**意图**:用专门的异常类代替 type:ignore 注释
**为何需授权**:触 execution/ 信任边界

---

## C. 当前 indirect-risk(Round 1 后变 safe)

### [C1] prune_system_actions TTL 7d

**位置**:新增 `_PRUNABLE` 条目 + `_run_cleanup` 注册(`aletheia/storage/repositories.py` + `aletheia/agent_runtime/scheduler.py`)
**亲核数字 (2026-06-24)**:system_action 1211 条全部在 7d 内,TTL=7d 当前**不会清理任何行**
**为何 indirect-risk**:理论上一旦 TTL 设错时区或配置,可能误清新数据 → 改 PI 窗口
**解冻条件**:Round 1 判决后,或 Fix [A2] 完成后(PI 不再依赖该窗口)
**操作**:
```python
# repositories.py 加
_PRUNABLE['system_action'] = ('knowledge_items', 'item_type', 'system_action', 'created_at')
def prune_system_actions(days: int = 7) -> int: ...
# scheduler.py _run_cleanup 注册
prune_system_actions(7)
```

---

### [C2] prune backtest_runs best-per-strategy

**位置**:概念性 - 每策略只保留最优 N 条 backtest
**为何 indirect-risk**:`paper_trader_ranker.py`(冻结)读 backtest_runs 时按 `created_at DESC LIMIT 1`
取最新,prune 后"最新" ≠ "最优",ranker 选不同策略 → 改决策
**解冻条件**:必须等 ranker 改为按真实绩效排序后才能 prune

---

## D. 安全但本轮排序后劣后(下次可做)

### [D1] Serenity 三表 TTL 剪枝

**表**:serenity_evidence (30d) / serenity_run_events (14d) / serenity_research_runs WHERE status='completed_empty' (7d)
**理由**:这三表不进任何 agent context,纯 UI 消费
**注意**:需先确认 SerenityLens.tsx 是否从这些表拿数据,以及 UI 是否依赖较老的 evidence
**操作模板**:仿 `prune_serenity_claims` (`repositories.py:1050`)

### [D2] system_action 写入去重 guard

**位置**:`aletheia/learning/knowledge_writer.py`(若不存在,查 INSERT INTO knowledge_items 的所有调用方)
**意图**:同 type+title 60s 内去重,缓解表膨胀
**注意**:learning/ 在冻结清单 → 改写入路径需谨慎,如果改的是 caller 而不是 learning/ 内部,可能 safe;但因为它改写入节奏,**也算 indirect-risk**,降到本类劣后

### [D3] dreaming_log 悬挂指针清理

**问题**:62 条 dreaming_log 记录的 file_path 指向 `data/knowledge/dreaming_logs/202606/` —— 该目录**不存在**
**位置**:`aletheia/agents/dreaming_agent.py`(若不在冻结清单则可改写入逻辑,否则只清孤儿行)
**注意**:agent 写入到 knowledge_items 也属"PI 输入"边缘,先评估

### [D4] insight_synthesizer 静默失败排查

**位置**:`aletheia/analytics/insight_synthesizer.py`
**现状**:7d 内 agent_runs=0,可能从未运行
**任务**:查 scheduler 注册 / 触发条件 / 是否被 enqueue

### [D5] strategy_experiments 表 DROP

**现状**:total=0,从未写入过,空表
**操作**:`migrations.py` 加 DROP + 把表名从 SDD 模块映射移除
**风险**:确认没有任何代码引用此表(包括动态字符串)

### [D6] /api/analytics/strategy-performance 端点

**意图**:JOIN backtest_runs + strategy_records,前端展示真实 PF/Sharpe/Equity
**为何劣后**:已有 Top 10 中 round1-progress / pipeline-funnel / learning-funnel 三个端点先做

### [D7] /api/analytics/serenity-pipeline 端点

**意图**:research_runs status 分布 + 24h opportunities 活跃数
**为何劣后**:同 D6

### [D8] architecture-fitness 加 trading_context 守卫(xfail)

**意图**:AST 断言 `_build_layer3_wisdom` 内每个 list_knowledge_items 调用必须传 item_type 参数。
xfail 标记,Round 2 Fix 完成后移除 xfail
**位置**:`tests/test_architecture_fitness.py`
**为何劣后**:虽然只是测试,但当前 spec_learning_pipeline.py 的 RED 测试已经覆盖类似断言

### [D9] CR-SCH-01 24 个 scheduler job 补 _record_job_failure

**位置**:`aletheia/agent_runtime/scheduler.py:771` 附近
**意图**:job 失败时统一记录,而非 24 处散落 try/except
**劳动量**:m,改动多但每处简单

### [D10] Strategy Pool Stage 1+2(direction_affinity / regime_tags / Ranker)

**位置**:CURRENT_TASK.md §64-79
**为何劣后**:Stage 1+2 直接影响策略选择,**虽然在 strategy/ 冻结目录外**(在 trading_context 间接读),触碰算 indirect-risk

### [D11] Windows autostart schtasks 可靠性加固

**意图**:gateway 看门狗在 logon trigger 缺陷情形下的可靠性
**位置**:看 `aletheia-watchdog-venv-bug.md` memory 已记
**操作**:补 schtasks 注册的 verbose log + fallback 触发条件

### [D12] detect_contradictions() stub 实现

**位置**:`aletheia/knowledge/manager.py:305`
**意图**:知识库矛盾检测,服务 dreaming_agent → ps_lessons → 决策注入链
**为何劣后**:整条服务链都冻结(ps_lessons → PI 是 [A3]),Round 1 期间做了也没人消费

### [D13] G2 agents executor 线程数评估

**意图**:目前 max_workers=3,LLM 卡住可能阻塞全队列。是否能调成有界异步队列?
**为何劣后**:CLAUDE.md 明示 max_workers 不可超过 3(SQLite 并发约束),调整需重设计

### [D14] PIPELINE_GAPS §C2 RegimeConfig/GuardConfig 前端控制

**问题**:18 个 config 字段在后端 schema 但前端 Settings 页缺控件(违反"Config↔UI rule")
**为何劣后**:18 字段拆分处理,部分冻结字段不能动,工作量大且分散

### [D15] reconciler 批量同秒强平模式 — 需独立调研

**现象**(2026-06-24 亲核):Round 1 起点至今 25 笔 closed 里 13 笔 `reconciled_gone`,**且全部聚集在 3 个时间窗**:
- 2026-06-22 15:32 同秒 ×3
- 2026-06-24 04:14-19 同秒 ×5
- 2026-06-24 12:07 同秒 ×5

**疑点**:`position_reconciler.py:435` 试图从交易所 TPSL 历史拿 fill price,失败才 fallback;
本会话亲核 12/13 用了真实 current_price(非 entry_price fallback)→ 看似真实平仓但**同秒批量** = 强模式。
要么 (a) TPSL 同时触发(可能多个 stop loss 价位接近)、要么 (b) reconciler 周期跑发现一批 orphan 一起处理。

**为何劣后(且不能改)**:`portfolio/position_reconciler.py` 触信任边界(close 路径),冻结期不动。
**Round 1 期间能做**:doctor 的 `loop.reconciled_ratio` 已落地(2026-06-24 本会话),可观察占比变化。
**Round 2 后调研**:加 reconciler 日志记录 `_try_get_tpsl_fill_price` 命中率 + trigger 来源(scheduler 周期 vs 事件触发)。

### [D16] OKX universe 含不可交易标的 — opportunity 浪费 + 订单失败

**现象**(2026-06-24 亲核):24h opportunities 表里 **ZEC/USDT=144 次、TRX/USDT=144 次**,但 OKX 实际不交易这些(`okx 51155 local compliance`)。24h 内 10 个 error orders 中 4 个是 ZEC/USDT。

**根因猜测**:universe 来源(`aletheia/data/universe.py` 或类似)从某个全 universe 源拉标的,没按 OKX 实际可交易列表过滤。

**为何劣后**:
- universe 模块**可能不在冻结清单**(还要 grep 验)。但**改了它会改 PI prompt 看到的 opportunities** → 间接触冻结
- 当前每个不可交易的 opportunity 浪费 paper_trader_ranker LLM tokens,**有真实成本**但不阻塞 Round 1
- **Round 2 解冻后**:在 opportunity 生成处加 OKX 可交易 symbol 集合过滤,或单独 prune `error_reason LIKE '%51155%'` 的 stale opportunities

### [D18] trust.audit_chain hash mismatch(2026-06-25 doctor 新增)

**现象**:doctor `trust.audit_chain` 报 chain BROKEN — `audit.jsonl` 中 10 处 `_prev_hash` mismatch(行号 1783/1790/1874 等)。
**影响**:审计链完整性破坏,无法证明 audit log 未被篡改(SDD spec `spec_operator_loop.py` 有对应 claim)。
**根因猜测**:
- 多进程并发写 audit.jsonl(backend + operator_loop)时 race condition
- 或 monitor commit 进程在 backend 还在写的时候 checkout/stash 造成行截断
**为何劣后**:
- audit.jsonl 是 append-only,已断链无法回溯修复(只能 truncate 重建或标记 gap)
- 当前 Round 1 冻结期内不能改 audit 写入逻辑(触信任边界 `agent_runtime/permissions.py`)
- **Round 2 后**:加 file lock 或改 audit 写入为 SQLite WAL 表,消除竞争
**冻结期能做**:只诊断不修;可以 `--reset-chain` 重建 hash(但需用户授权因为改审计文件)

### [D19] data.signal 24h win_rate 持续偏低(35%,2026-06-25)

**现象**:doctor `data.signal` 报 24h signal win_rate=35%(10264 settled, avg=-0.902%)
**影响**:universe/scanner 选品质量偏差;PI 拿到的 opportunities 质量不高 → fill 率低 → Round 1 样本不足
**关联**:[D16] OKX universe 含不可交易标的 + 整体选品算法质量
**为何劣后**:scanner 权重/Optuna 参数冻结期不能改(影响 AI 决策)
**Round 2 后**:Optuna re-tune scanner 权重 + 加 symbol 可交易性预过滤

### [D20] 并行 Claude /loop 会话干扰 git history(2026-06-25)

**现象**:git log 中每小时 :02 出现 `monitor(rNNN)` commit,来源非 Task Scheduler 非 scheduled_tasks.json,
是同 repo 另一个 Claude Code 会话(可能 CronCreate /loop)在定时巡检并 commit
**影响**:
- git history 被大量 monitor commits 淹没(r196 表示已 commit 196 次)
- 偶尔覆盖当前会话未提交的 working tree 修改(git add -A 风险)
- RUNTIME_BEHAVIOR_ISSUES.md 被反复编辑/提交
**缓解**:
- 用户可手动终止该 Claude 会话(找到对应 session)
- 或在该 session 中 CronDelete 相关 job
- 本会话已确认:不会 kill 它(可能用户有意保留),但记录干扰事实
**冻结期能做**:纯文档记录;不改代码

### [D17] 5bfa4a0 IOCP 修复的 3 个 follow-up(低优先,但记下来)

5bfa4a0 进程级 `WindowsSelectorEventLoopPolicy` 三个 follow-up:

1. **`pytest` 测试进程无 selector policy**:`tests/conftest.py` 没 `apply_windows_selector_policy()`,Windows 测试机跑 `test_okx_broker.py` 等调用 broker `asyncio.run()` 的测试可能 IOCP teardown hang ~255s。生产无影响(三个 entry-point 已设),只影响开发测试体验。
   修复:`tests/conftest.py` 顶部加 `from aletheia.async_utils import apply_windows_selector_policy; apply_windows_selector_policy()`。
   **冻结期能做**(纯测试基础设施),但本会话排序后劣后。

2. **uvicorn `--loop none` 版本兼容性**:`_gateway_runner.py:132,200` 给 uvicorn 子进程传 `--loop none`,**uvicorn 0.30+ 才有这个 key**。生产部署若装的旧版 → frontdoor/backend 直接 crash。**已被** `tests/test_event_loop_policy.py::test_uvicorn_loop_none_defers_to_process_policy` 守住,但**只在测试机的当前 uvicorn 版本生效**。
   建议:在 `pyproject.toml`pin `uvicorn>=0.30`,或在 `_spawn_*` 之前用 `uvicorn.config.LOOP_FACTORIES` 探测一次,不支持就降级到不传 `--loop`(但那会回到 Proactor 路径,§23 复发)。

3. **Python 3.14 `asyncio.get_event_loop_policy()` 移除**:当前 cpython 3.12 venv 工作正常,但 Python 3.13 弃用、3.14 计划移除 `get_event_loop_policy` / `set_event_loop_policy`。
   修复:升级到 Python 3.13+ 时改成 `asyncio.Runner(loop_factory=...)` 模式。
   **当前可忽略**(venv 锁定 3.12)。

---

## E. 战略决策类(需独立讨论)

### [E1] OKX 51155 compliance 错误的战略含义

**现象**:OKX demo 频繁报 `code:1 sCode:51155 "local compliance restrictions"`,某些标的开仓失败
**亲核状态**:2026-06-24 仍在出现,影响 PI 决策→ fill 转化率
**战略问题**:
1. 这是 OKX demo 的固有限制,还是配置/合约错误?
2. demo 行为与 live 是否一致?若不一致,Round 1 的 PF/ECE 对真实 live 表现的代表性存疑
3. 是否需要切回 bitget_demo / 评估其他交易所?
**为何独立**:这不是 bug fix,是产品决策

### [E2] Round 1 G6 不可达的应对

**亲核数字 (2026-06-24)**:Round 1 起点 6/22 之后实际 closed_trades=**11 笔**,
realized_pnl=-15.43,WR=45.5%,PF=0.362;距 G6(≥60 笔)还差 49 笔,
剩余约 7-8 天,需要 ~6.5 笔/天速率,远高于当前 ~1.6 笔/天
**战略选项**:
- (a) 延 Round 1 窗口到 60 笔达成 — 但 PF/WR/ECE 三个指标可能本身就告诉我们要 Kill
- (b) 接受当前数据作为 Round 1 终值,即便不到 60 笔(放宽 G6)
- (c) 提前 Kill,Round 2 改方向
- (d) 调研 PI trade=193 / fill=14 转化率 7% 的根因,若能快速提速则保留 Round 1
**为何独立**:涉及实验协议(EXPERIMENT_PROTOCOL.md)的修订,不是代码改动

---

## 维护规则

1. **添加新条目**:任何新发现的"现在不能做但以后要做"的事,必须登记进对应 A/B/C/D/E 段
2. **完成标记**:条目完成后**改标题加 `[完成 commit-hash]` 前缀**,**不要删除条目**
   (保留历史归因)
3. **改判定**:若发现某条目的 freeze_safe 标签需要纠正(本会话已纠正过 prune_system_actions
   indirect-risk → safe-but-deferred),在条目下加 "**判定纠正**:[日期] 从 X 改为 Y,理由 Z"
4. **不要重复**:同一问题在 BACKEND_ISSUES.md / PIPELINE_GAPS.md / 本文件多处出现时,
   本文件用 "= [Ax]" 互相 link,避免改动同步漂移
5. **会话开头扫读**:任何"我能做什么"的会话,**先扫一遍 D 段**(劣后 safe 项),
   挑当下 value/effort 比最高的做,而不是凭印象决策

---

## 引用

- 调研依据:本会话 (2026-06-24) 三次并行 workflow 调研
  + Serenity Lens (`wdi1vahdk` task)
  + Learning + Strategies (`wlv2i92fc` task)
  + Round 2 优化清单 (`w3wm6vgsr` task)
- 亲核纪律:综合 agent 给的事实陈述中曾出现函数名错(per_strategy_kpi_summary)、
  数字差 6.6 倍(Round 1 closed_trades 73 vs 真实 11)等错误。本文件中所有
  file:line / 函数名 / SQL 数字都已在 2026-06-24 会话亲核 grep 验证。
  下次会话使用前若发现偏差,以 grep 现状为准,**不要凭本文件字面行号**修改代码。
- 关联 memory:
  - [[aletheia-experiment-protocol]] — 冻结期判决标准
  - [[aletheia-backend-issues-archive-unreliable]] — 不轻信 "已修" 标签
  - [[workflow-failed-agent-not-zero-evidence]] — 综合 agent 转述失真
