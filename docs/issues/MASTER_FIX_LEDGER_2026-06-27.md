# Master Fix Ledger — 2026-06-27(Round 1 后全量修复)

> **背景**:Round 1 已提前终止(见 `docs/product/EXPERIMENT_PROTOCOL.md` 终止判决),冻结解除。
> owner 指令:把"反幻觉修复 + 报告发现的所有 bug + 所有被冻结阻塞的 fix"一次性全修完、全测过,
> 然后再决定是否开 Round 2。
>
> **梳理方法**:9 组并行 subagent 扫所有 issues/plans/辅助文档,**逐条对 HEAD 代码 grep 核实真实状态**
> (不信文档标签——历史自评 25-62% 不准)。详细逐条数据见 **`_RAW_SWEEP_LEDGER.md`**(本批次自动产出)。
> 本文件是**指挥/分批计划**层,raw ledger 是**证据/file:line**层。
>
> **核实覆盖**:8 组完成(251 项),backend-A(BACKEND_ISSUES §1/§2/§3)单独补跑中,完成后并入。

## 实施进度(滚动更新)

**✅ 已完成 + 验证(本会话):**
- **R0 / L-Bug-C 完整版**:`trading_decision.py` 透传 opportunity_id+strategy_id+scanner_family;`trading_context.py` opportunities dict 补 id/linked_strategy_id;新增 `_attach_opportunity_context` 按 symbol+direction 回填。→ LP-S05 GREEN。
- **B1**:`paper_trader_ranker.py:412` retrieve_lessons 去 post_trade_review,只留 trade_case_study。
- **C1**:`post_trade_reviewer.py` 提示改问"信号质量"(非 execution quality 幻觉源)+ confidence 恒 medium(反转检索池让 trade_case_study 主导)。test_experience_loop 12 passed(更新 1 过时测试 → medium 契约)。
- **L-Bug-A**:`trading_context.py` lessons 段 → `list_knowledge_items(item_type='trade_case_study')`。LP-S01 GREEN。实测 lessons 0→7。
- **L-Bug-A'**:guards 段 → `item_type='guard_candidate'`。LP-S02 GREEN。实测 guards 0→3。
- **strategy_id 链**(backend-A 1.1-causal-chain):透传 linked_strategy_id 到 proposal。
- **B2 判断**:B1 去幻觉源后字面匹配只作用真实案例,污染主体已除;结构化重构(需 trade_case_study 暴露 realized_pnl)记为低优先增量,不过度工程化。
- **A10**:`trading_decision._build_assessment_prompt` 注入 trading_memo/calibration_feedback/reasoning_feedback(三者 layer2 已算但从未渲染)。验证三段都进 prompt。
- **S-Bug-C**:`trading_context` strategies 段 JOIN `build_strategy_kpis()` 填真实 win_rate/total_pnl/trade_count(替代恒 None)。验证 key 匹配。
- **equity/stock show_errors**:删 yfinance 已移除的 `show_errors=False`(两 observer 之前 100% completed_with_errors)。import OK。
- **A6**:`paper_trader_ranker.py:728` evidence SQL 从硬编码 `paper_internal` 改 `NOT LIKE '%live%'`(涵盖 bitget_demo/okx_demo 等真实 paper 模式)。
- **A9**:`operator_loop.py` 两处(:394 主评估 + :498 _get_strategy_performance)从 fills(无 realized_pnl/strategy_id 列,恒 0/报错)改用 closed positions 算 win_rate/PF/total_pnl。
- **final_conviction**:删 `DebateResult` 未读字段 + `_run_debate` 计算(dead reader-trap)。
- **spec 同步**:LP-S01/LP-S02/LP-S05 + SP-S03 全部移 xfail→GREEN(status implemented)。验证 spec_learning_pipeline 4passed/2xfail(LP-S03/S06)、spec_strategy_pool 3passed。

**P3a 剩余**:C2(risk_critic 输入扩容)/C4(dreaming 接真实 PnL)/C5(archive 历史幻觉 post_trade_review,运行态脚本)。
- **L-Bug-B**:`trading_context` 末尾拉 `LessonStore().list_all(status='proposed',limit=5)` → layer3.ps_lessons;`trading_decision` prompt 渲染 "Recent Post-trade Lessons" 段。验证 live 拉 5 条 + 渲染。LP-S03 GREEN。

**P3b 剩余**:A7 today_pnl(低优先,prompt 已标 realized,纯命名一致性)/A11 regime publish(detector.py:257-273)/A13 PiDecision opportunity_id 字段+迁移(models.py+paper_trader.py)/**L-Bug-D guard 升级链推迟**(B2 目录不一致 guards.py:97 vs guard_candidates/ + B3 候选缺 status/guard_id/predicates frontmatter outcome_recorder.py:262 + B0 auto_promote_enabled 开关属 needs_human_decision;机制完整实现且依赖用户开关,LP-S06 保持 xfail 不破坏)。
**spec 状态**:LP-S01/S02/S03/S05 + SP-S03 共 5 个 xfail→GREEN;仅 LP-S06(guard 链)保持 xfail。
**P3c 未开始**:CUM-ADD per-symbol cap(risk/engine)/broker opp_id(bitget+okx+binance+paper)/kill-switch confirm_release/risk_critic 类型/fee reconcile/PostSubmitDBError/fail-open guards/error-leak(2.4/2.7 provider auth header)。
**P3d 未开始**:见 `_RAW_SWEEP_LEDGER.md`。**P4/P5 未开始**。
**已完成 14 修复全部 import+spec 验证通过。全量回归:1700 passed / 4 failed / 1 skipped。**
**本会话 14 修复零回归**(git stash 验证:stash 后 baseline 同样 4 failed → 这 4 个是 master pre-existing,非本会话引入)。

### Pre-existing 测试失败(master 基线,P5 全绿前须修,新窗口处理)
1. `test_entry_logic.py::test_observer_writes_structured_direction` — observer 写 2 条 opportunity 而非 1(`len(opps)==2`)
2. `test_entry_logic.py::test_observer_stores_canonical_format_symbol` — 同源(2 条)
3. `test_entry_logic.py::test_full_observer_to_paper_trader_loop` — 同源
4. `test_universe_settings_ui.py::test_p3_pages_use_shared_primitives` — RiskControls.tsx 有动态 `style={{width}}`(FE 3.4 inline style,测试可能过严)
→ 1-3 同源(疑 observer opportunity 重复写入/spot+perp 双写),需读 test_entry_logic + market_observer 写入路径定根因。
**P3c 剩余**:CUM-ADD/broker opportunity_id/kill-switch confirm/risk_critic 类型/fee reconcile/PostSubmitDBError/fail-open guards/error-leak(2.4/2.7)。
**P3d 剩余**:见 `_RAW_SWEEP_LEDGER.md` general_bug/dead_code/ui_honesty/test_debt/perf 段。
**接续验证**:`uv run pytest tests/specs/spec_learning_pipeline.py tests/test_experience_loop.py -q`

---

## 状态总览(9 组,336 项;backend-A §1 中 67 项已 fixed)

| real_status | 数量 | 处理 |
|---|---|---|
| fixed(已真修,核实确认) | 93 | 关闭,不再动 |
| not_a_bug(误报/非bug) | 32 | 关闭 |
| duplicate(跨文档重复) | 6 | 合并到主条 |
| **not_fixed(真 bug 仍在)** | **75** | **必修** |
| **partial(部分修)** | **32** | **补全** |
| needs_human_decision | 13 | 列给 owner 决策 |

→ 需施工(not_fixed+partial)= **107 项**;needs_human_decision = 13 项交还 owner。

---

## 批次 1 — 反幻觉核心(P3a)· 最高价值,亲自实施 + spec 验证

| ID | 状态 | 文件 | 备注 |
|---|---|---|---|
| **R0 / L-Bug-C** 透传 opportunity_id + scanner_family | ✅ **已修(本会话)** | trading_context.py:148 + trading_decision.py(dataclass/helper/meta/proposal) | LP-S05 翻 GREEN。总根因。 |
| **B1** ranker 检索去 post_trade_review 幻觉 | 待修 | paper_trader_ranker.py:412 | item_types 去掉 'post_trade_review' 只留 'trade_case_study'(一行) |
| **B2 / R3** 重构 _lesson_penalty(字面匹配→结构化) | 待修 | paper_trader_ranker.py:400-422 | 改排序逻辑,需新 spec |
| **C1 / R2** post_trade_reviewer 提示改 + confidence 降 medium | partial(PnL 已注入) | post_trade_reviewer.py:71-72,85 | 挡幻觉出检索池 |
| **C2 / R2b** risk_critic 输入扩容 | 待修 | risk_critic.py:23-24,41 | 喂逐条拒因,停自动升 high |
| **C4** dreaming _extract_lessons 接真实 PnL | 待修 | dreaming_agent.py:176-224 | 依赖 C1 |
| **C5** archive 历史幻觉 post_trade_review | 待修 | manager.py:288(脚本) | 须在 B1 之后 |
| **R1** retrieval 质量门(high_confidence) | 下游 | retrieval.py:31 | R0+C1 修后自然收敛,无独立改 |

## 批次 2 — 决策链其余(P3b)· 亲自实施 + spec

| ID | 状态 | 文件 | spec |
|---|---|---|---|
| **L-Bug-A** Layer3 lessons 死过滤器 | 待修 | trading_context.py:382 | LP-S01 |
| **L-Bug-A'** Layer3 guards 加 item_type | 待修 | trading_context.py:398 | LP-S02 |
| **L-Bug-B** ps_lessons 接入决策链 | 待修 | trading_context.py + trading_decision.py + lessons.py:71 | LP-S03 |
| **S-Bug-C** strategy KPI 暴露 | 待修 | trading_context.py:276 + trade_stats.py:227 build_strategy_kpis | SP-S03 |
| **A10** calibration/memo/reasoning_feedback 注入 prompt | 待修 | trading_decision.py _build_assessment_prompt | 三者 layer2 已建但 prompt 未渲染 |
| **A6 / §13.1** ranker evidence 硬编码 paper_internal | 待修 | paper_trader_ranker.py:723 | |
| **A7 / §13.2** today_pnl 跨模式语义 | 待修 | trading_context.py:75 + pnl.py:184 | rename today_realized_pnl |
| **A9 / §18.2** operator win_rate 用 fills 恒 0 | 待修 | operator_loop.py:385-403,495-505 | 改读 closed positions realized_pnl |
| **A11** regime 重启后 bias 未恢复+无 publish | partial | detector.py:257-273 | |
| **A13 / PT-2** PiDecision 缺 opportunity_id | 待修 | models.py:823 + paper_trader.py:140 | 加字段+迁移 |
| **L-Bug-D B2** guard 升级器读错目录 | 待修 | guards.py:96 vs files/markdown.py:57 | LP-S06 |
| **L-Bug-D B3** guard 候选缺 frontmatter | 待修 | outcome_recorder.py:262-277 | LP-S06 |
| **final_conviction** dead code(算了不读) | 待修 | trading_decision.py:300 | 删 or 用 |
| **equity/stock show_errors=** yfinance 移除致 100% 失败 | 待修 | equity_observer.py:128 + stock_observer.py:111 | 真 bug,删参数 |
| **§15.4** equity_observer KB write-back 缺失 | 待修 | equity_observer.py | conf>0.7 写 research_note |

## 批次 3 — 信任边界 + 安全不变量(P3c)· 逐项谨慎 + spec-first + 汇报高亮

> ⚠️ 全部触 risk/ execution/ routes/ secrets/。CLAUDE.md Safety Rule:改 risk/execution 需 owner 批准。
> owner 本 goal「报告所有 bug 都修」覆盖真 bug,但每项实施前按 C-1 教训核实下游 + 补 SDD spec,汇报时逐项高亮。

| ID | 状态 | 文件 | 风险 |
|---|---|---|---|
| **CUM-ADD** per-symbol 累积加仓无上限 | 待修(真 gap) | risk/engine.py + paper_broker.py:69 | 加 per-symbol notional cap + spec |
| **A5/CR-EX-03** bitget Position 缺 opportunity_id | 待修 | bitget_broker.py:158(+paper/okx/binance 克隆同步) | risk check #16 失效 |
| **B1** kill-switch confirm_release 二次确认 | 待修 | routes/risk.py:80-92 | 防误解锁 |
| **B-P1-2** risk_critic 类型洗白(research_note) | 待修 | risk_critic.py:42 | 新 item_type 或文档化 |
| **B-P1-5** broker fee reconcile 真实回填 | partial(estimated marker 已修) | reconciliation.py | 仅 live 有意义 |
| **B4** PostSubmitDBError 替代 type:ignore | 待修 | binance_broker.py:163 + okx_broker.py:225 | |
| **EH-1 §17.2** risk engine fail-open guards | needs spec-first | risk/engine.py | 异常应 fail-closed(拒) |
| **CR-EX-01** _LIVE_MODE 硬编码 bitget | partial(告警在) | reconciliation.py:21-68 | OKX/Binance live reconcile |

## 批次 4 — 一般 bug + dead code + UI honesty + 测试债(P3d)· 可 Workflow 并行(独立低风险)

详见 `_RAW_SWEEP_LEDGER.md` general_bug / dead_code / ui_honesty / test_debt / perf 段。要点:
- **一般 bug**:F2 universe POST /policy 缺失 · E3 volume_scanner 内存态丢失 · E5 operator retire 无超时 · F6 symbol_map KeyError · F5 score_event 空壳 · BinanceClient eager-init · symbol_map · prune FK CASCADE · dreaming_log 16 孤儿指针 · CR-PI-01 scanner weight 不持久化 · E2 UTA connected_brokers · I1 price_feed docstring 谎言 · CR-SCH-01 job failure(partial)
- **端点缺失**:D6 strategy-performance · D7 serenity-pipeline(+ api_reference 同步)
- **dead code**:D5 strategy_experiments 空表 DROP · D12 detect_contradictions stub · F5 score_event · BrokerBreakdown(PnLZone.tsx:67) · BITGET_GRANULARITY
- **UI honesty**:chart 硬编码色(3.7) · text slice 截断(Learning.tsx:242) · P3.11 timer · P3.12 price line · P3.14 WebSocket · P3.15 stop viz · D14 Regime/Guard config UI(Config↔UI rule) · inline styles/as any(typing 债)
- **测试债**:Tier1/2/3 mock-only 清理 · conftest selector policy(D17-1) · architecture-fitness guard(D8)
- **perf/运维**:prune_system_actions TTL(D2/C1) · Serenity 三表 TTL(D1) · net.py httpx connect timeout(D21)

## 交还 owner 决策(needs_human_decision · 不擅改)

1. **B0 auto_promote_enabled** — guard 链 B2/B3 修通 dry-run 验证后,是否把 config 开关置 true(会产生 active guard 改 ranker)
2. **A14 Self-Evolution Tier 2** — 新功能,是否 Round 2 开工
3. **E1 OKX 51155 换交易所** — demo↔live 代表性,是否切 bitget_demo/其他
4. **D11** Windows autostart 加固 · **D13** executor 线程数(SQLite 约束) · **D15** reconciler 同秒批量强平调研 · **D19** scanner 选品质量调参 · **D20** 并行 Claude 会话干扰 git
5. **G7** Fernet key 与密文同目录(local-first MVP 可接受?) · **G10** Prometheus /metrics(no uncontrolled expansion?)

## 执行顺序

1. **P3a 反幻觉核心**(R0✅ → B1 → C1 → B2 → C2 → C4 → C5)— 有依赖,R0 先(已done)
2. **P3b 决策链**(L-Bug-A/A'/B/S-Bug-C/A10 注入 → A6/A7/A9/A11/A13 → L-Bug-D)
3. **P3c 信任边界**(逐项 spec-first,汇报高亮)
4. **P3d 一般/UI/测试**(Workflow 并行)
5. **P4 E2E 管道测试 + 单测** → **P5 全量回归 + doctor + spec + 前端 build**
6. 汇报 + Round 2 决策

每批改完即跑对应 spec/单测验证;全量后台跑。
