# Development Log

## 2026-07-03 — discussion 批次开发经验沉淀

### 触发
owner 要求把本轮 20+ worktree、多分支、多模块修复过程里的经验和教训写成本地开发文档,
让后续 AI 继续开发时能直接看到,避免重复踩坑。

### 交付
- 新增 `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md`。
- `DEV_PROTOCOL.md` 加大批量修复经验入口。
- `AI_WORKFLOW.md` 的 Context Recovery Protocol 加该文档。
- `AI_HANDOFF.md` 和 `CURRENT_TASK.md` 加短入口。

### 内容要点
- 长文先拆成 Dxx 可验证清单,审计和修复分支分开。
- 性能先量化再修 top offender;本轮 recommendations 重复 artifact scan 6 -> 1。
- 前端源码更新后必须 build 静态包并重启 gateway。
- 合并后要清理 worktree、已合并分支、未登记旧目录。
- owner 决策项不能伪装成 bug fix。

---

## 2026-07-02 — 动态 sizing / persona / learning 系统 P0–P4

owner 方向落地:Operator 在风控硬边界∩persona区间内动态决仓位/杠杆/加仓/止损 + Learning 拟合 +
渐进解冻。全默认 OFF(零行为变化)。设计 `docs/design/dynamic_sizing_operator_persona_design.md`。

- P0 `3e8015f` active_broker_view_complete 视图完整性门(修 #1 权益基准 + #2 thrash 变体)
- P1 `ad580a1` persona 区间 schema + persona.py resolver seam(conservative==扁平配置零变化)
- P2 `9c27dc4` build_sizing_context→risk_appetite;operator_decidable 参数上下文决策
- P3 `3fbdd79` sizing_policy regime 干净窗奖励学习 + doctor loop.sizing_policy
- P4 `884e035` 渐进解冻 ladder(distress refreeze)+ 金字塔 pyramid_plan(硬顶 clamp)
- specs PS-S01..S12;conformance 522;full suite 1817/0
- 未接:金字塔实盘下单(owner-gated,会 place orders)
- 顺带:PR#35 费率漂移 6 用例对齐(d8aedeb+149774d,只改测试)

---

## 2026-07-01 — reconciler 假孤儿 thrash 根因修复 + Scorecard 去污

### 触发
监控抓到做空仓批量 reconciled_gone 平仓 + 相同价 adopted 重开 → thrash 循环。

### 根因
非 Bitget 账户上 classify_positions 在 unified 缓存过期时回退读空的 legacy Bitget 缓存 →
误判全部 OKX 持仓 orphaned → 本地平(不动交易所)→ 重认领 → 无限 thrash。41% 仓位
reconciled_gone、56% adopted、strategy 归因被毁(10/110 真实平仓带 strategy_id)。

### 修复(commit 5d6bb89,SDD)
- P0 classify_positions 回退 broker 感知(非 Bitget+unified 过期 → bail);PL-ADOPT-10/11/12
- P1 go_live_scorecard 排除 synthetic 平仓(新建 analytics/close_reasons.py);AN-S03
- 穷尽排查确认缓存错配只坑 reconciler 一处

### P2(owner 授权已实施,da38fbf)
- P2-1 OKX 交易所保护单(OKXClient.place_algo_order + stops 分发器 + adopt 调用;双门控
  exchange_tpsl_enabled 休眠 + demo-only);PL-ADOPT-13/14
- P2-2 adopt._recover_strategy_link 归因恢复(P0 已解根问题,此为防御性;紧配不误归因);PL-ADOPT-15/16
- P2-3 factor_feedback 过滤 synthetic 平仓;LP-S15
- conformance 505 全绿;full suite 1815/0;gateway restart live

### 验证
conformance 500 全绿;full suite 1815/0;真配置 stale 路径 0 false-orphan;doctor healthy。
报告 docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md。

---

## 2026-07-01 — Phase 2A+ 剩余四项收口（2A/2A+ 全 CLOSED）

### 触发
owner「尽量把 2a 完全完成,不要再遗留任何项目」。四项 PARTIAL/PENDING 一次做完。
commit `f093e36`（实现）+ `8cea488`（文档）,push master。

### 关键实现
- **2A+.2**：`_derive_regime_targeting(direction)` 推导 direction_affinity + applicable_regimes。
  **陷阱**：applicable_regimes 须写 **JSON 数组**——ranker 用 `json.loads`+`isinstance(list)` 解析,
  逗号串被静默丢弃使过滤失效。primary + sweep-variant 两调用点都传。SP-S04 升 L2b。
- **2A+.4**：`types.ts` + `StrategyLifecycle.tsx` Direction 列/drawer（`fmtRegimes` 解析 JSON）。
- **2A+.9**：`family_quota_by_regime` + `resolve_family_quota()` + allocator/service 消费 +
  Settings UI 每 regime 逐行 sum=1.0 双校验。UP-16 L2b。
- **2A+.11**：`score_volatility` 接 atr_4h/bb_width_4h 4h 多 TF 确认。UP-15 L2b。

### 不做（有理由）
2A+.10 端到端 short 验证 = 运行观察项（非代码）,需 bear/volatile + bull 真实出现,证据留 `data/observation/`。

### 验证
conformance 493→496 全绿；full suite 1815 passed/0 failed；build 通过；doctor healthy。

---

## 2026-07-01 — Scanner + Observer 审计修复 P0–P3

### 触发
两份审计报告（Universe Scanner + Market Observer）→ plan → 实施。核实 workflow 因 workspace rate-limit 全灭，改手动核实，查出报告 6 处根因偏差。分支 `fix/scanner-observer-audit-fixes`，spec `UP-01~13` GREEN。

### 关键修复
- **US-4（最高杠杆）**：`service.py:256` forward-outcome direction 用 `scanner_direction` 同源 + `_infer_direction` 加 event 分支 → 一处修复 momentum 54% None + event 0 条 + 胜率统计双重失真。
- **US-1**：`compute_total_score` 消费 `scanner_weights`（PI 手调此前无效）。
- **US-3/8**：perp market_type 传递 + feature 复合 key。**US-9**：short 硬禁不被高分绕过。
- **US-5/6**：BB squeeze O(n²)→O(n)；price_precision tick-size 保真。
- **MO-1**：key_observations 进 layer1。**MO-2**：per-symbol guard（闭合断口 F）。
- **MO-3/4/6/7/8/9/11/12/13/14**：F&G 填充 / scanner_family hint / RS rank 单symbol省略 / crowding n/a / fallback DEGRADED / news 48h / prediction 流动性 / load_config 移出循环。

### 不做（有理由）
US-7（读侧已 fallback）/ US-12·US-16（无消费者·增强 → ROADMAP）/ MO-5·10·15（已覆盖 / 收益微）。

---

## 2026-07-01 — 双层模型路由上线 + System Operator 重命名 + earendil-works/pi 研究

### 触发

接续上一会话：完成双层模型路由功能；研究 earendil-works/pi 是否可复用；owner 指示
全面统一"PI Agent"→"System Operator"术语，并完成文档同步。

### 双层模型路由（commit e5a8010）

**问题**：所有 agent 共用单一 `default_provider`，顶层决策与批量提取无法分流到不同模型。

**实现**：
- `ModelsConfig` 新增 `deep_provider` / `fast_provider`（`None` → fallback default）
- `get_provider_for_tier("deep"|"fast")` 翻译层（`model_provider/router.py`）
- `llm_complete` / `llm_structured` / `llm_stream` 加 `provider=` 参数
- deep 层 11 个 call site（trading_decision / strategy_designer / risk_critic / market_observer / dreaming_agent / lesson_reviewer）
- fast 层 5 个 call site（research_worker 全部批量 LLM 调用）
- 前端 Routing Role 下拉 + 路由摘要行
- SDD: 5 个 MR-* claims GREEN

### earendil-works/pi 研究结论

核查 `operator-shell/package.json`（无 `@earendil` 依赖）和 `pi_runtime_adapter.ts`（调
Aletheia Python API，不调外部 npm）确认：earendil-works/pi 是 coding CLI，与 Aletheia
operator 无关。operator-shell 完全自建。决策详见 `DECISIONS.md §2026-07-01`。

### 术语统一

活文档统一"System Operator"；`pi_daemon.py` 文件名保留；历史归档文档加头部标注。

---

## 2026-06-29 — OpenAlice TaG 深度研究 + P0-1 hash 基础设施 + P1-2 reconcile audit 链入

### 触发
owner 让研究 https://www.openalice.ai/blog/trading-as-git-intro，并把"能做、有必要做的"全部生成 plan 并实施。会话末追问 https://www.openalice.ai/blog/traderalice-cursor-for-vibe-trading 是否值得借鉴；最后让把文档全量同步。worktree 分支 `worktree-openalice-tag-research`。

### 研究（13-agent workflow，797k tokens / 8.5min）
三层 ground truth：博客全文 / 本地 `_refs/OpenAlice` 真实 TS 源码（`services/uta/src/domain/trading/git/TradingGit.ts`）/ Aletheia 自身对照。5 镜头分析（概念映射 / 架构机制 / 价值主张可信度 / 批判与边界 / Aletheia 对比借鉴）+ 5 轮对抗核查。

**核心发现**：TaG = "工程上扎实的 AI 交易日志系统 + 版本控制比喻的过度包装"。Git 比喻在 revert / branch / merge / Merkle 链上全面失效（成交无 revert、代码只有线性单链、hash 是 SHA-256 截断非 Merkle 树）。博客"1 月崩盘预判"自标注是 backtest，逻辑自相矛盾，零证据值。**真正值得借鉴的两个工程纪律**：① reject 是一等审计记录、② 执行后账户全量快照绑到决策记录。

对抗核查抓到 6 处脑补（含我自己的几条），全部并入正文。报告落 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`。

### P0-1（commit `b8ed990`）— TradeProposal hash chain（最小可行起手项）
不触 trust boundary 的 hash 基础设施。`models.py` TradeProposal 加 `commit_hash` + `parent_hash`（均 `Optional[str]`，default `None` 向后兼容）+ `migrations.py` `_ADDITIVE_COLUMNS` 注册两列 + `idx_proposals_commit_hash` 索引 + `repositories.py` 新增 `_compute_proposal_hash()`（SHA-256 of canonical JSON of immutable fields 截断 16-char hex）+ `insert_trade_proposal()` 在事务内自动填且链入（全局单链，按 rowid DESC 选 parent）。SDD：DM-S08/S09（L2a）+ DM-B06（L2b）+ DM-C02（L2c：re-compute 确定性 + 链完整性 + **status 免疫断言**——pending → executed 后 commit_hash 必须不变）。

**关键设计**：hash 输入排除 `status` + `commit_hash` 自身；16 字符（OpenAlice 用 8 / 43 亿空间，Aletheia 多 broker 多 strategy 全局单链加宽到 64-bit）；全局单链非按 broker 分链（简单 + 与 OpenAlice 一致）。

验证：4 个新 spec RED→GREEN；全量 1802 passed / 3 skipped / 0 failed 零回归；conformance 359→360 / 99% 覆盖维持；doctor `trust.audit_chain` 通过。

### Vibe Trading 帖（#4）交叉核查（commit `2359971`）
源码 grep 验证 "auto-learn effect" 命中**零**——纯营销话术。三个具体能力中 News Retrieval 已有等价物（`news_items` 表）、LLM 写 Pandas 踩 §4 红线（agent 生成可执行代码）、ReAct + chat-driven 与 trust boundary 冲突。**结论无新增可落地借鉴项**。研究报告加 §八点五交叉核查节避免未来会话重复研究。

### P1-2（commit `31eb1a9`）— Reconcile audit 链入
4-agent explore workflow 先核查 A=FORBIDDEN_INTENTS hardening（owner 让先做）—— 意外发现 `deactivate_kill_switch + modify_permissions` **早在 2026-06-23 就加进** `registry.py:415-426` 并配 Defense-in-depth 注释，OL-C03 spec 已 `status="implemented"` 无 xfail，BACKEND_ISSUES §15.1 也已标 ✅。**A 是 no-op**，无新代码。MASTER_FIX_LEDGER 没列、所以没"过时标签"要修（typical of memory `aletheia-backend-issues-archive-unreliable`）。

B 走 SDD：在 `spec_pipeline_adopted_position.py` 加 PL-ADOPT-09（L2b）；RED 信号清晰（`audit after_state.commit_hash None must equal proposal.commit_hash '4fe0133763e952bd'`）；在 `position_reconciler.py:372-379` 的 `write_audit` after dict 加 `commit_hash` + `parent_hash` 两键（直接读 insert_trade_proposal 事务内填充的 proposal 对象，无需重算）；强化测试也覆盖 `parent_hash` key 存在性（即使值为 None，区分"genesis 链首"和"忘了写"）。GREEN。

**价值闭合**：P0-1 给提案打了内容指纹，P1-2 让 reconcile audit 第一个用上这个指纹。审计消费者从此单看 `audit.jsonl` 行就能还原 hash 链关联，不用回 DB 查 `trade_proposals`。

验证：9/9 PL-ADOPT passed；邻近 57 spec passed；全量 1802 passed 零回归；conformance 360→361。

### 文档全量同步（本 commit + commit 67ed379）
5-agent parallel workflow 扫 5 层金字塔（L1 VISION / L2 design 契约 / L2.5 SDD / L4 _ai status / L4 issues ledger）+ 1 个 adversarial verifier。verifier 抓到 5 处问题（claim 计数误用 stale baseline、scanner 输出被 schema 截断、L2-data-model + L2-SDD 重复提议、PL-ADOPT-09 anchor 不清、漏掉 spec_pipeline_adopted_position / spec_audit_chain 没进 README spec 表）。亲自核 grep 验证 spec_data_model.py 实际 18 claims（scanner 误用 8），重写截断内容，并去重整合。

L2 同步（commit 67ed379）：data_model.md + audit_chain_contract.md（新增 §6.4 提案级 hash chain + §7.3 doctor 探针 gap + §6.3 表加 position_adopted 行 + 顺便修 stale 行号 :415-419 → :415-426）+ trust_and_change_control.md §1.1 末尾 + design/README.md spec 表纠正与补全。

L4 同步（本 commit）：DEVLOG + AI_HANDOFF + CURRENT_TASK + DECISIONS prepend；docs/references/INDEX.md §3 加新研究报告；docs/references/openalice_prism_review.md 标 P0-1 / P1-2 ✅；新建 docs/issues/TAG_BORROW_BACKLOG.md（4 项剩余借鉴 P0-2/P0-3/P1-1 + Decimal/risk_critic stats/state-driven orchestration 状态卡）。

### 当前状态
worktree `worktree-openalice-tag-research` 待 owner 审核 merge：5 commit（b8ed990 P0-1 + 2359971 Vibe 核查 + 31eb1a9 P1-2 + 67ed379 L2 docs + 本条 L4 docs）。memory `aletheia-tag-borrow-plan` 已同步落地状态。后续 P0-2（state_after 快照）/ P1-1（reject 同构）触 trust boundary，需 owner 显式批准；P0-3（Guard 责任链拆分）触 risk/engine.py，红线最严。

## 2026-06-25 — proposal rationale 双重截断刈断 meta JSON(没搞全 + 弱代码外泄)

### 现象
用户在 proposal detail 看 rationale "没搞全"(thesis 半句断)+ "夹杂弱代码"
(原始 `---{"conviction":64,...` JSON 串直接显示给人)。

### 真因(亲核)
`agent_shell/trading_decision.py` 拼 rationale = `f"{reasoning[:200]}\n---\n{json.dumps(meta)}"`
再 `[:500]`。两层截断:reasoning 砍 200(thesis 断句)+ 整串砍 500,而 meta JSON
~1.1KB → `[:500]` 把 JSON 刈成非法串。实测 5/5 条 agent_rationale 恒=500、JSON 全
`Unterminated string`。前端 parseDecisionMeta 的 JSON.parse 必失败 → catch 把整条
原始串(含碎 JSON)当 text 显示 = 弱代码外泄。两症状同一根因。

### 修复(冻结例外 #5,用户授权,双层防御)
- 后端 trading_decision.py:`_MAX_RATIONALE_TEXT=600` 只截 human thesis(带 `…`),
  **删整串 `[:500]`** → JSON 永远完整。agent_rationale 列是 TEXT 无限制。
- 前端 Proposals.tsx:parseDecisionMeta catch 返回分隔符前干净 text;
  ProposalRationaleDetail/ProposalRationale no-meta 分支渲染 text 非 raw → 坏值
  也永不向人倒 JSON(防御纵深,保证不复发)。
- `tests/test_proposal_rationale.py`(3):JSON 必可解析 / thesis 单独截断非整串 500 /
  短 thesis 不动。

### 判决影响
记 EXPERIMENT_PROTOCOL 例外 #5。**PI 输入信号变化**:agent_rationale 的 meta JSON
被 reasoning_feedback.py 解析出 scanner_family → 喂回 PI prompt。修复前 JSON 全损坏
→ reasoning_feedback 一直 "unknown";修复后首次成功提供真实 scanner_family = PI 多
一个此前缺失的输入维度,阶段评估区分修复前/后。

### 根治理念
根除"human text + machine JSON 拼单串再截断"这个脆弱模式本身(任何 char cap 都会
刈 JSON),而非调大数字。后端保证 JSON 不被切 + 前端保证坏值不外泄 = belt-and-suspenders。
全 risk/agent_shell 冻结链 128 测试通过。

## 2026-06-25 — 回撤刹车误用持仓 notional 当 equity → $0 cap 锁死 demo 开仓 (70a7b64)

### 现象
用户发现 OKX Demo 一整批期货 Short 被拒,拒因 `Position size over cap — $21.72 > $0 cap`。
$0 上限 = 任何仓位都开不了 → 整批无差别拒绝(22h 内 144 笔误拒)。

### 真因(亲核,非推理)
`risk/engine.py:_portfolio_drawdown_scale`(检查 #5 回撤缩放)按 `proposal.execution_mode`
(okx_demo)过滤 `portfolio_snapshots`,拿到**每个 mode 自己的行**,其 `total_value_usdt`
是持仓 **notional**(Σqty×price)而非账户 equity(`pnl.py:373` 写入)。13:53-14:03 五笔
止盈平仓(净赚 +12),持仓 notional 从 ~$272 collapse 到 $16.5 → drawdown 误算
`(277-16.5)/277=94%` → scale=0 → `effective_cap=26.84×0=$0` → 所有提案被拒。
**赚钱平仓反而把自己锁死。** 真实账户(`exchange_account` 行)全程稳定 ~$88,400,无回撤。

### 修复(70a7b64,trust boundary,用户授权,走 SDD)
engine.py:356 交易所模式(okx_demo/*_demo/live)改读 `exchange_account` 行真实 equity;
`paper_internal` 无交易所账户、mark-to-market `total_value_usdt` 即 equity,保持原路径。
- `spec_risk_engine.py`:+RE-B05a 回归(notional 272→16 但 equity 平稳 → scale 必须=1.0);
  RE-B05 分段数学保持绿。
- `risk_engine_contract.md`:关卡 #5 注明回撤数据源 = exchange_account equity。

### 判决影响
记入 `EXPERIMENT_PROTOCOL.md` 例外 #4。**关键**:此 bug(6-24 17:25→6-25 15:18 UTC,~22h)
人为压制了交易样本——Round 1 G6 样本量低(T+2.6 天仅 17 笔)有相当部分归因于此 infra
误拒,非 PI 不愿交易。该窗口"被拒"不计入"PI 决策质量"评估。

### 生效验证
重启后 okx_demo scale=1.0、cap 恢复 $26.84;risk_decisions 15:22/15:24/15:27 连续
approved=1 空 violations(此前 15:18 仍 `...>0.0` 误拒)。全 risk 测试 56 passed。
同属 notional/equity 混淆家族(余额读错=例外#1;回撤口径=本次)。

## 2026-06-24 — 进程级 SelectorEventLoop 根治 Windows IOCP 顽疾 §17/§23/§24 (5bfa4a0, fix 分支)

### 现象
体检发现系统半瘫:Web UI 卡死 3h45min(frontdoor HTTP 不响应但 TCP 仍 listen,
看门狗 TCP-only 检测误判"还活着"不重启)、trading loop 因 LLM provider 间歇性
不可用被 startup_gate 自动暂停。errors.log 持续刷 `ProactorEventLoop IOCP drain
teardown hung 255s`(§17)+ frontdoor `WinError 64` 周期性崩溃(§23/§24)。

### 真因
整个 `aletheia/` 未设 `WindowsSelectorEventLoopPolicy`,所有 `asyncio.run()` 与
uvicorn server loop 默认 ProactorEventLoop(IOCP)。Windows 上 ProactorEventLoop
关闭时 IOCP drain 挂起 + AcceptEx overlapped 操作产生 WinError 64。
`scheduler._run_async_with_timeout` 此前只局部用 SelectorEventLoop,覆盖不到
broker `_run_coro` / `exchange_sync._run_async` 在另一 worker 线程另起的裸
`asyncio.run()` —— 这才是 §17 线程泄漏的真正来源。

### 修复(5bfa4a0)
进程级设 policy(覆盖全部裸 asyncio.run,修 §17/§24)+ uvicorn 子进程加
`--loop none`(loop_factory=None → 回退标准 asyncio.run → 遵循进程 policy 而非
uvicorn 硬编码的 ProactorEventLoop 工厂,修 §23)。
- `async_utils.py`(新):`apply_windows_selector_policy()` 幂等 helper。
- `server/app.py` / `gateway/frontdoor.py` / `main.py`:import 顶部调用(三进程)。
- `_gateway_runner.py`:两处 uvicorn cmd 加 `--loop none`。
- `scheduler.py`:既有 workaround 加注释(逻辑不变)。
- `tests/test_event_loop_policy.py`(新):真实子进程断言 policy 生效 + `--loop none`
  契约回归守卫。

### 关键实证(纠正 agent 推断)
- uvicorn 0.48.0 `LOOP_FACTORIES` 含 `"none": null`;`--loop selector` 不存在
  (传了会崩);`--loop asyncio` 在 win32 仍返回 ProactorEventLoop。亲测
  `Config(loop='none').get_loop_factory()` 返回 None(exit 0)。
- SelectorEventLoop 唯一破坏路径 = claude_cli/codex_cli 的 8 处
  create_subprocess_exec,但二者 config `enabled=false`,当前安全。已在
  async_utils.py 留显著警告:启用前须改这 8 处为线程内同步 subprocess。

### 安全边界
未触碰任何实验冻结路径(AI 决策链);aiohttp/httpx/ccxt 均纯 socket I/O 兼容
Selector。改动隔离在 `fix/windows-selector-event-loop` 分支,未动 master。

### 待验证 / 遗留
- 修复运行时效果由后台监测确认(新进程 errors.log 不再出现 IOCP/WinError 64)。
- 顺带发现但未修(超本轮 scope):(1) backend 冷启动 ccxt load_markets 耗时
  20-30s > launcher 15s readiness 窗口,导致 LLM 故障期 `gateway start` 易报
  "readiness did not pass" 假失败(进程其实在后台继续起);(2) LLM provider =
  Azure Databricks AI Gateway 端点侧间歇性超时(host/网络层正常,401/可达性
  1.9s),非本机可控。

## 2026-06-24 — Equity Curve 误报 + 诊断代理陷阱(dbde229 commit body 勘误)

### 现象
用户报 Dashboard 显示 "Equity Curve / No trades closed yet — curve appears after
the first position closes",但 DB 里有 68 笔已平仓(realized -21.36 USDT)+ 3
笔持仓。空态文案与事实矛盾。

### 真因(两层叠加)
1. **前端 bug**:`PnLChart.tsx` 的 `useQuery` 只解构 `data`/`isLoading`,**未
   处理 `isError`**。请求失败时 `data===undefined → points=[] →
   chartData.length===0`,直接走"还没平仓"的空态分支 —— 违反"onError 必须处理"。
2. **触发外因**(诊断时归因被误判,见下方勘误):用户浏览器实测访问 localhost
   完全直连无碍。最初看到空态最可能是 gateway 处于 churn 期端点偶发失败 +
   PWA 缓存了无 isError 处理的旧 bundle。

### 修复(dbde229)
PnLChart 新增 `isError` 分支:红色 "Failed to load equity curve." + Retry 按钮
(`refetch`),与真零交易空态区分。后端 `/api/analytics/equity-curve` 完好
(days=1/7/30 均返回完整 points)。重建并提交 dashboard_static。

### 勘误 — dbde229 commit body 的代理归因错了
原 commit body 写"浏览器走系统代理被转坏返回 502"、建议用户"在代理软件
绕过列表加入 127.0.0.1/localhost"。**这两句都是错的**,用户当场指出后实测纠正:

- **WinINET 系统代理**(浏览器走的):`HKCU:...\Internet Settings\ProxyEnable=0`
  (关闭),且 `ProxyOverride` 已含 `localhost;127.*;<local>`(DIRECT)。浏览器
  访问 `127.0.0.1` 不经任何代理。
- **环境变量代理**(curl/CLI 走的):`HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:7897`,
  `NO_PROXY` 空。curl 默认会无脑把 localhost 也丢给代理 → 误返回 502/000。**我**
  诊断时中招后,把 curl 假象错误地外推成"浏览器/后端故障"。

记忆 [[aletheia-localhost-probe-proxy-trap]] 已写入,以后诊断:
- curl 探活 localhost **一律加 `--noproxy '*'`**;
- 验证浏览器体验用 `Invoke-WebRequest`(走 WinINET);
- 后端死活以 `aletheia gateway status` + `/api/health` 的 `status` 字段为准,
  不以"curl 是否 502"为准。

不 amend dbde229(避免改写历史),勘误以本条 DEVLOG 为准。

---

## 2026-06-23→24 — 最小赚钱循环全链路修复 + SDD 扩张 + 代码审计

### 背景
用户观察"系统不怎么开仓"。逐层排查发现最小赚钱循环(opportunities→paper_trader→
signal_pending→PI Agent→proposal→risk→execution)被**五层叠加阻碍**卡死,20h+ 零开仓。
每修一层暴露下一层——典型的"修复 A 暴露 B"连锁。

### Phase 1 — OKX contractSize 单位混淆 (650f608)
CCXT swap `amount`/`fetch_positions.contracts` 单位是合约张数非币数(TRX=1000/ETH=0.1/
BTC=0.01)。下单放大 contractSize 倍、绕过风控 notional;mark-to-market 算错。
okx/client.py 边界换算 coins↔contracts。清理 3 笔被放大的历史错误仓位。

### Phase 2 — PI 余额幻觉破产 (6c006cb, 冻结例外#1)
Phase 4a 后 PI Agent(LLM)是唯一 proposal 创建者。它被喂 8 个错误事实构建"账户濒临
破产"幻觉 → 100% skip:余额读 bitget 闲置 $167(真实 okx_demo $88k)/ 7d 统计被
units_bug_cleanup 污染(-13.46 vs +1.94)/ cfg.risk.kill_switch_active AttributeError →
risk block 空 / 策略过滤 status-vs-state typo → 0 活跃策略 / win_rate 46.6% 被 LLM
误读为 -45.8% drawdown / long_bias 读如方向否决 / debate 公式丢弃 initial conviction。
trading_context + trading_decision 逐项修(冻结例外,用户授权)。

### Phase 3 — OKX 跨 loop 回归 (d6fc427)
Phase 1 引入的 `_contract_size` 调 `load_markets` 在 `_run_coro` 新线程新 loop 跑,
但 OKXClient 在 __init__(同步上下文)eager 建 aiohttp session → 跨 loop Future 冲突 →
load_markets 失败 → contractSize 回退 1.0 → Phase 1 修复在 broker 路径静默破功。
OKXClient 改 lazy-init:`_ensure_exchange()` 在首次 async 调用所在 loop 建。
回归测试 TestLazyExchangeInit 钉死"__init__ 不建 exchange"。

### Phase 4 — 日损 $1 误熔断 (5831bd1, CR-RE-03, SDD 流程)
engine.py 检查 #8 日损门控对所有模式用 `max_live_limited_cap_usdt`($100)作基数 →
demo 日损限 $1。Phase 1 清账 -23.26 损失触发 demo 日损熔断,阻断当日 PI 全部交易。
SDD 流程:RE-B13 RED → engine.py:154 按 _is_live 分流(demo 用 starting_capital_usdt,
对齐 check #13)→ GREEN。config starting_capital 100→88000(日损限 $1→$880)。

### Phase 5 — signal 重复→"数据损坏"skip (edc15b2 例外#2 + 079bb9d)
paper_trader Path B 写 signal_pending 后只对被跳过的标 reviewed,eligible 漏标 →
同一批 open opportunity 每 5min 重复写入 → PI 读重复(ETH short 12 次)判
"pipeline data integrity compromised"。paper_trader.py:153 加
update_opportunity(reviewed);清理 1676 条历史重复;新增 prune_pi_decisions(7d,
action 白名单保护 trade/close/recommend_promote)防再堆积。PL-PT-05 xfail→pass。

### 结果
五层全清 + gateway 稳定后,PI 02:00+ 产出 5 trade/2 skip,开出 3 笔真实持仓
(ETH/SOL/BTC short),notional 规格正确,integrity 借口消失。最小赚钱循环打通。
doctor loop.profit ✓ healthy。全量 1651 tests passed。

### 附:SDD 扩张 + 审计 + code review
- SDD: spec claims 45→114,新建 4 spec+3 契约,修 12 契约漂移(DOC_CONTROL_ANALYSIS)
- 审计: 16 契约+7 管道+git 挖掘 → 74 findings(CODE_AUDIT_REPORT),已修 trust 项
- code review(11 角度): 修 2 个 get_cached_state 遗漏,否决 6 误报,新发现 OP-WINRATE/APP-HMAC
- 监测固化: doctor loop.profit 探针 + spec_pipeline_profit_loop.py
- 冻结例外 3 次均登记 EXPERIMENT_PROTOCOL;Round 1 起点保留 6/22,修复前数据不计入

---

## 2026-06-21 — 数据质量自检 + PI Agent 自愈闭环 (5e5f4fe)

### 背景
Universe signal 24h win_rate 只有 36.7%（低于40%阈值），但系统对此无感知。
Doctor 只检查进程/网络/配置，不检查数据积累和信号有效性。PI Agent 不了解自身数据环境。

### Phase A — Doctor OHLCV/Signal probes
- `_probe_ohlcv_coverage`: 检查所有 active members 是否满足 min_bars_for_backtest(200)；不足 → warn + hint backfill
- `_probe_signal_effectiveness`: 过去7天 24h horizon forward outcomes win_rate；<40% → warn signal degraded
- `aletheia audit data [--json]`: per-symbol×timeframe bar count 矩阵 + signal effectiveness
- `aletheia data backfill`: 批量下载所有 active members OHLCV

### Phase B — PI Agent 自检+自愈
- `trading_context._get_data_health_summary()`: 新函数，返回 {ohlcv_ready, signal_status, win_rate, coverage}
- Layer 1 自动注入 `data_health` 字段；PI Agent 每轮决策都感知数据环境
- `_build_assessment_prompt()`: signal_status=degraded → 注入 ⚠ SIGNAL DEGRADATION 警告；ohlcv not ready → 保守提示
- `commands.evaluate_trading_opportunity()`: 决策后触发自愈 — degraded → pause_low_conviction；缺数据 → request_backfill（写 system_action knowledge item）

### Phase C（上轮完成）
8 个 OKX active members 全部 ≥200 1h bars；backfill 直接绕过 incremental logic 完成

### 验证
- `aletheia doctor`: `data.ohlcv: ok`, `data.signal: warn(36.7%)`
- `aletheia audit data --json`: 结构化覆盖率 ✓
- `layer1["data_health"]`: `signal_status=degraded` 正确触发


## 2026-06-17 — Self-Evolution Tier 1 实施(worktree-self-evolution-tier1)

> 研究 → 三层设计 → Tier 1 全量实现 + 文档整理，一次会话完成。

### 背景
Universe forward outcomes（4h/15m/1h/24h 收益追踪）数据已有，但从未反哺 scanner 规则。
Tier 1 目标：无 LLM 成本地关闭这个反馈回路，将负面 4h outcome 自动转化为 guard_candidate。

### 实现内容
- `aletheia/universe/outcome_recorder.py`：`settle_due()` 后扫描 4h settled 行，方向感知
  阈值判断（long < -3%，short > +3%），调用 `create_guard_candidate()`，7 天内去重
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent(symbol, family, days=7)`
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试

### 文档
- `docs/plans/self_evolution_plan.md`（从 `docs/_ai/SELF_EVOLUTION_DESIGN.md` git mv）
- `docs/status/ROADMAP.md`：Self-Evolution 横切轨道
- `docs/_ai/CURRENT_TASK.md`：Tier 2（2026-07-01）、Tier 3（2026-08-17）日期

### 验证
1311 passed, 0 failed（master `.venv`，全量 suite）

### 状态
两个提交在 `worktree-self-evolution-tier1`（`1a75dc7`, `89df0be`），待合入 master。
Master 当前有未提交的 equity observer / futures integration 工作，需先处理再 merge。

---

## 2026-06-16 — 项目级 Code-Review 修复(A/B/C/D 设计级,uncommitted)

> 来源:`/code-review max` 全项目审查(契约对应 + 运行态正确性 + 潜在 bug)。
> 15 条确认 findings,7 条机械修复先行,4 条设计级本轮完整实现。

### 实现内容
- **A** `signal_emitter.py`: `_HIGH_CONF_THRESHOLD` 70→60 —— allocator 总分天花板 ≈68.5,
  原阈值令 "high" 永不可达。
- **B** `outcome_recorder.py`: `settle_due`/`_batch_fetch_prices` 改 async,共享 CCXT provider
  给全部 universe symbol 取 live ticker,snapshot 兜底 —— 此前只有静态 3 标的能定价,
  轮动成员全标 `failed`,校准攒不到数据。
- **C** 方向感知校准:`UniverseForwardOutcome` 加 `direction` 列(`models.py` + `migrations.py`
  CREATE TABLE 与 `_ADDITIVE_COLUMNS` 双路);`service.refresh` 经 `_infer_direction` 写方向,
  `register_pending` 落库;`repositories.calibration_stats` 胜率 SQL 改方向感知
  (`positive_count`→`win_count`,short 赢在负收益),`routes/universe.py` 同步。
- **D** `scheduler.py`: 新增 `reschedule_universe_refresh()`(锁内 `reschedule_job`);
  `routes/settings.py` 在 `refresh_interval_min` 变化时调用,使保存即时生效(原须重启)。

### 验证
- `tests/test_universe_calibration.py`(NEW,3 方向用例) + signal_emitter 阈值用例
- ABCD 受影响测试单独跑 36/36 全绿,ABCD 未新增 DB writer / scheduler job
- 顺带修 `test_startup_gate::test_empty_response_not_usable`(测试桩 `_FakeResp` 缺 `.raw`、与
  `startup_gate.py:64` 漂移;加 `raw=None` 对齐真实 `ModelResponse`,零生产改动;BACKEND_ISSUES §6.5 ✅),
  修复后全量基线恢复全绿
- 所有改动均非 trust-boundary 文件(risk/execution/auth/secrets/live 未触碰)

---

## 2026-06-14 — 全局代理配置实现(commit 28eb357)

> 根因:Windows Task Scheduler 启动的 watchdog 进程没有 HTTPS_PROXY env var → CCXT/Binance
> 连接超时 → market_data 写入失败 → market data 永远 stale。

### 实现内容
- `aletheia/net.py`(NEW): `httpx_client()` / `aiohttp_session()` / `apply_proxy_env()` 三个工厂函数
- `aletheia/config/schema.py`: 新增 `NetworkConfig(proxy_url, no_proxy)`
- `aletheia/server/app.py`: lifespan startup 调 `apply_proxy_env()`
- 13 个文件 30+ 处 httpx/aiohttp 调用点统一替换为 net.py 工厂
  (最关键:ccxt_provider.py 的 aiohttp.ClientSession → aiohttp_session())
- `aletheia/server/routes/settings.py`: `POST /api/settings/network` + `/test` 端点
- `frontend/src/pages/Settings.tsx`: NetworkCard 组件 (Infrastructure tab)
- `aletheia/diagnostics/doctor.py`: `_probe_network()` 诊断维度

### 验证
- `npm run build` 通过(187 modules, 803 kB)
- `uv run pytest -q` 通过(同预存失败数,无新增失败)
- 用户可通过 Settings → Infrastructure → Network Proxy 配置代理 URL 并点 Test 验证连通性

---

## 2026-06-14 — Edge Lab 全量落地(commits 9fe5c5f / 8532280 / c90d2eb / ab2ba5c / ab4a522)

> 来源:Clustr100 社区交易群知识提炼 → 用户批准 implementation plan → 两个并发 session 分工落地。

### 实现内容
- **P0**: `Opportunity` 新增 `valid_until / entry_zone / stop_loss_hint / tp_hint`(schema + migration)
- **P0.5**: `paper_broker` 可配置滑点(`slippage_bps=5bps`, 多头高填 / 空头低填) + `execution_fee_pct` 去硬编码
- **P1**: `ShadowTrade` 模型 + `shadow_trades` 表 + `persist_shadow_batch()` + `paper_trader` 每 tick 入库 Top-5
- **P1.5**: `Position` 新增 `btc_entry_price / btc_exit_price`；开仓时从 latest_snapshot 抓 BTC 价写入；平仓时 pnl.py 写 exit
- **P2**: `Position` 新增 `regime_tag`；开仓时从 `latest_macro_snapshot()` 读 `risk_regime`；risk_off 惩罚 -20→-35
- **P2.5**: `compute_family_scorecard()` + `GET /api/analytics/family-scorecard`
- **tests**: `test_edge_lab.py`(11 个新测试全绿) + 回归修复 2 处精确断言(`pytest.approx`)

### 关键修复(本 session 负责)
- `family_scorecard.py` SQL bug：`positions` 无 `scanner_family`，原查询空返回。改为 LEFT JOIN `trade_proposals→opportunities`。
- `models.py` 重复 `ShadowTrade` class：并发 session 各写一份，移除重复定义。
- `test_paper_broker_stops.py` + `test_risk_engine.py`：slippage 导致填价偏移，精确断言失败。改 `pytest.approx(rel=0.01)`。

### 测试状态
`uv run pytest -q`：仅 3 个预存失败留存(test_ac12_risk_blocks, test_self_heal x2)，均与本批次无关。1170+ 通过。

## 2026-06-13 — 执行路径语义诚实文档化(纯文档,不动代码)

> 用户要求学习吸收外部 Agentic Trading 框架经验。核实后发现外部框架研究已做完(三份参考文档齐备),
> 真正净增量是通读当前代码、把"LLM 在决策回路精确位置"的一手事实钉死。用户明确:补文档/认知,不改代码。

### 核实结论(一手,2026-06-13 通读代码)
- **下单机制确定性**:`paper_trader.py:124` 固定 notional/价、`:126-130` 固定止损%、Risk Engine 硬限。
- **但交易方向 = LLM**:`market_observer.py:125-152` LLM 输出 `direction_bias` → `paper_trader.py:104-108` 直接照做。
- **strategy rule 悬空**:`strategy_designer.py:102-113` 产出 entry_rule/exit_rule 文本存 `parameters` JSON,
  全仓无 DSL parser,`paper_trader.py:136-149` 从不读 rule——策略层对交易零影响。
- **验证仅 paper 一层**:`strategy_lifecycle.py:184-273` gates 严格但基于真实 paper 统计,无 backtest/walk-forward/PBO。
- **audit 无 hash 链**:`jsonl.py:25-69` append-only 但每条独立,无 parent_hash。

### Added
- `docs/EXECUTION_PATH_HONESTY.md`:三类拆解(确定性/LLM/悬空,file:line)+ 原始设计 v1 差距对照 +
  Prism"反向"对照 + 验证层/审计链/经验闭环现状 + 三条诚实化路径(命名/验证 gate/真 DSL,仅记录)。

### Changed
- `docs/north_star_references.md`:更正 §0/§2/§6——"market_observer 不读历史"已过时(现注入 memory block);
  经验闭环从"只写不读"更正为"软闭环已通,缺参数化反馈";交叉引用新文档。
- `docs/BACKEND_ISSUES.md`:新增 §4(2026-06-13 结构性发现,未修复待授权)——strategy rule 悬空、
  验证层仅 paper、audit 无 hash 链;header/阅读顺序标注 §4。

### 不做(留痕)
- 不改任何代码;不执行 openalice_prism 蓝图 P0/P1/P2(待授权);不引入 DSL 引擎/回测框架(红线)。

## 2026-06-13 — Gateway 静态文件启动可靠性(commit db2423f)

> 用户访问 WebUI 得到 JSON 404。根因:后台启动时 Vite watch 与 uvicorn 竞态——uvicorn 模块导入时
> 检查 `dashboard_static/index.html` 并 mount,但 Vite 首次构建未完成,文件缺失 → mount 跳过 → 404。

### Changed
- `aletheia/_gateway_runner.py`:禁用内嵌 Vite watch(后台启动依赖预构建静态文件;开发用 dev.ps1)。
- `aletheia/server/app.py`:静态 mount 从模块顶层移到 lifespan startup,新增 `_mount_static_dashboard()`。
- `aletheia/gateway/lifecycle.py`:`do_start` 集成 preflight(fail-fast)+ `_verify_webui_accessible` 启动后验证。
- `aletheia/diagnostics/doctor.py`:新增 `webui.static` 诊断维度。
- `CLAUDE.md`:补 WebUI 静态文件构建规则。

### Added
- `aletheia/gateway/preflight.py`:启动前预检(静态文件/数据库/配置),失败给修复建议并拒绝启动。

## 2026-06-13 — 外部框架综述参考分析(纯文档,不动代码)

> 用户提供外部《Agent Framework Research Report 2025–2026》并要求结合项目更新 roadmap。
> 判断:报告是验证镜非新需求源,其重型栈处方命中红线 → 按参考文件先例只做文档分析。

### Added
- `docs/REFERENCE_agent_framework_research_2025_2026.md`:报告摘要 + 收敛最佳实践 vs Aletheia
  逐条核对 + §7 重型栈(Postgres/向量库/Redis/LangGraph)逐条拒绝附红线 + 净增量 + 治理红线。

### Changed
- `docs/ROADMAP.md`:Sources 引该报告(validation only);Common Failure Modes 补 Lookahead bias +
  Execution/slippage mismatch 两行;File Relationships 加新参考文件。
- `docs/north_star_references.md` §5 末加外部综述独立佐证指针(Hermes ≡ §4、重型栈已拒绝)。

### 不做(留痕)
- 不引入报告推荐的任何依赖/框架(Postgres/vector/Redis/NATS/LangGraph 均违反红线)。
- 不新增 phase/能力,不改 Phase 0 优先级,不触 trust 边界。

## 2026-06-13 — DX 收口 + 能力批次(18 提交)+ 机器交接

> 本日批次紧跟 `1075c8e`(Persistence/Sync/Recovery 子系统,已在 origin/master)。会话结束时
> 在本机停机、准备切换到另一台开发机前推送。逐条根因见 `docs/AI_HANDOFF.md` 同日条目。

### Changed — Dev 体验收口到 single-port :8787
- dev.ps1 不再起独立 Vite dev server(:5173);gateway :8787 同时服务 API + 静态资源。
  `vite build --watch` + backend `uvicorn --reload`(+ `WATCHFILES_FORCE_POLLING` Windows 兼容)
  接管热重载;SSE 推 `frontend-built` 事件,`dev-reload.ts` 自动刷新浏览器。
- 所有 dev-reload 行为门控 `ALETHEIA_DEV_RELOAD=1` + `live_disabled`,生产恢复 supervisor 崩溃保护。
  sentinel 路径统一为 `RELOAD_SENTINEL` 常量。新增 `scripts/watch_backend.py`、`/api/version`。
- "Restart" → "Cold Restart"(明确冷启 vs 热重载)。状态浮层从扁平 table 改为四区 flex 布局。

### Added
- LLM startup gate 重试(默认 3 次,可配 `health.startup_gate_max_retries` /
  `startup_gate_retry_delay_sec`)+ 暂停时 Windows toast,治 Databricks 端点偶发误暂停。
- 原生 Anthropic + OpenAI provider(SSE 流式 + 富元数据 + `llm_stream()` + Settings UI),36 单测。
  本项目默认 LLM 仍是 Databricks AI-Gateway,native 为新增能力非默认切换。
- Universe:`quote_volume_24h` / `spread_bps` 列、enriched detail drawer(features + linked opportunities)。
- `docs/plans/`(5 篇 implementation plan / 设计报告)+ `frontend/public/favicon.svg` 入库。
- Clustr100 社区知识库 10 篇(`docs/research/clustr100_community_kb/`,direction-only 非施工授权)。

### Changed — 风控
- symbol 过滤统一为单个 `symbol_list` + `symbol_filter_mode`(whitelist/blacklist)开关决定语义;
  默认 blacklist + 空列表,移除旧的 BTC/ETH/SOL 硬编码白名单。

### Fixed — design-doc 合规(2026-06-11 评审 4 条)
- paper_trader 记录 skip 原因;prediction_observer 显式 `execution_eligible=False`(+ migration);
  event/catalyst scanner 接进 `run_all_scanners` 参与配额;`depth_10bps_usd` gap 注释澄清。

### Operator
- 模式 `Investigating` → `Working`;`current_intent` 现于浮层 + Command Center strip 显示。

## 2026-06-12 — 账户级仓位发现 + 纳管(Phase 1-2 落地,Phase 3-4 设计在册)

**背景**:所有者立场——Aletheia 启动接管账户后,人难以再与 AI 并行管仓,故全账户仓位
(含系统外开出的)应统一交由 AI 统一管理("接管即纳管")。这是把 ROADMAP Phase 6 的
"live-read reconciliation / position-mismatch"思想**提前、且以 demo 优先**落地。

**修复(顺带,先于一切)**:`total_value_usdt` 之前返回持仓名义市值(口径错,会随每笔
fill 跳变),现在交易所缓存可用时用真实账户权益覆盖,与同一 summary 里其它字段一致
(`pnl.py`)。另修 `client.get_positions()`:Bitget v2 mix all-position 的入场价字段是
`openPriceAvg`,旧代码读的 `averageOpenPrice` 根本不存在 → 一直静默为 0;现已读对,并
补 `markPrice`/`leverage`。无真实入场价就无法合成诚实因果链,这是纳管的前置。

**Phase 1 — 发现(只读,已完成)**:新增 `portfolio/position_reconciler.py`
`classify_positions()`,把交易所缓存仓位与本地 open 仓位按 `(canonical symbol, direction)`
配对(hedge 模式下同标的多空并存,故必须按"对"匹配),分类为 managed / external / orphaned。
新增 `GET /api/positions/reconciliation`;`compute_portfolio` 增 `exchange_external_summary`
(并把旧的、hedge 模式下会算错的 external 计数改为按对精确)。前端 `TradesPositions.tsx`
新增 "Account Reconciliation" 卡片(此前 drift 信号无任何前端消费者)。

**Phase 2 — 纳管(已完成,改动受控)**:新增 `source` 列(`native`/`adopted`)到
positions/orders/trade_proposals(CREATE TABLE + `_ADDITIVE_COLUMNS` 双覆盖)。
`adopt_position()` 在单事务内合成 proposal→order(filled)→position 完整链,全部
`source="adopted"`,入场价/数量取自交易所,零入场价/零数量则拒绝纳管(不伪造)。关键设计:
纳管仓位的 `execution_mode` 按账户模式映射(demo→`bitget_demo`,live→`bitget_live_limited`),
于是 `position_manager` 自动管理 demo、跳过 live——**demo 自动 / live 人工的拆分由
`execution_mode` 一处如实设置后被既有守卫免费强制**,无需特殊判断。

**治理(trust §1.1 修订)**:旧铁律"外部仓位永不入本地表"修订为"可入表,但必须带
`source=adopted` 的诚实合成链"。红线测试 `test_external_positions_not_written_to_local_table`
拆为 `test_sync_alone_never_writes_local_positions`(sync 仍只读)+
`test_adopted_positions_carry_synthetic_chain`(纳管必带诚实链)。决策记录见 DECISIONS.md。

**Phase 3-4(设计在册,未实现)**:P3 = 定时 reconcile 任务(demo 自动纳管 + 孤儿平仓,
**默认开启**,见 DECISIONS.md 行为警告)、operator 命令(`reconcile_positions`/`adopt_position`/
`close_position`,live 需人工确认)、手动触发 API。P4 = live 人工确认平仓路径
(`close_live_position`,过 Execution Gateway)+ Full 模式接缝(默认关闭)。

**测试**:`uv run pytest -q` → **777 passed**。前端 `tsc` 干净,bundle 已重建。

---

## 2026-06-10 — CLI 分层 + 运营命令软弃用（人类交互收口到 Web）

**背景**：评估是否完全去掉 CLI、人类交互全走 Web。结论：不去掉，分三层。

**硬约束（为什么不能全删 CLI）**：Web UI 由 gateway 进程伺服，进程没起就没 UI——无法用"还不存在的 UI"启动那个进程；且 Windows 看门狗任务写死 `aletheia.main gateway start`，`doctor` 最需要时正是 gateway 挂掉、UI 够不着时。

**三层划分**：
- **bootstrap 内核（保留）**：`gateway start/restart/enable-autostart`、`doctor`、`version`。
- **运维/调试工具（保留，无 Web 平价）**：`bitget sync`、`knowledge reindex`、`knowledge search`。
- **运营命令（软弃用，有 `/api/*` 平价）**：`permission`/`model`/`risk`/`strategy`/`update` 整组 + `secrets` + `bitget test`。

**软弃用机制（commit `16efd5b`）**：
- `cli/_deprecated.py`（新）：`warn_deprecated()` 往 **stderr** 打一行指向 Web 的黄色提示；**stdout 不变**。
- 整组弃用用 Typer `@callback`（子命令执行前触发）；`bitget test` 用内联提示，`sync` 保持干净。
- `main.py`：`add_typer` 重排为 bootstrap / kept-tools / `[deprecated]` 三档，help 文本加 `[deprecated → …]` 标记。
- `diagnostics/doctor.py`：kill_switch / demo-creds 的 fix_hint 改为 Web 首选、CLI 备选——避免"删命令后提示成谎话"。

**文档同步（commit `2217510`）**：RUNBOOK / README 标 dashboard 为 primary、弃用 CLI 为 deprecated；`implementation_guide`（冻结规格）与 AI_HANDOFF 历史条目不改。

**决策**：软弃用而非硬删——零回退、零功能损失、保留逃生口；物理删除留待将来 Web 路径用顺手后一次性收尾。架构决策见 `docs/DECISIONS.md`。

**测试**：`uv run pytest -q` → **538 passed**（软弃用零行为变更，无回退）。

---

## 2026-06-08 (later⁷) — Gateway 稳定性修复（ghost-socket 根治）

**问题**：前端状态栏显示 "Gateway Stopped" 但页面正常访问；`gateway stop` 无法释放端口。

**根因**：uvicorn multiprocessing worker 在 parent 被 kill 后变孤儿，继承 listening socket 并用旧代码运行。其 cmdline (`multiprocessing.spawn.spawn_main`) 不含 orphan detector 的标记字符串，逃脱所有清理。

**修复（commit `a90ea84`）**：
- `pidfile.py` — `find_port_holders(port)`：netstat/lsof 直接按端口查 PID（兜底）
- `lifecycle.py` — `do_start`/`do_stop` 三层清理：PID tree-kill → cmdline orphan sweep → port holder kill
- `health.py` — `/api/status` 自报 `gateway_running: True`（能响应 = 在运行）
- `app.py` — lifespan 启动补写 PID + ownership 追踪（只清自己写的）
- `gateway.py` — restart 拒绝无恢复机制时的自杀

**决策**：网关端口清理改为 "one stop all" 策略——最终兜底不看进程身份，只看端口占用。
**测试**：46 gateway/supervisor/pidfile tests 全过。

---

## 2026-06-08 (later⁶) — Markets K线动态加载（scroll-back history + live tail）

**目的**：Markets 页（`/charts`）的 K 线图原本只加载固定一段（最新 N 根），向左滚动看不到更早历史，右沿也不自动更新。改成主流交易所体验：向左滚到边缘 → 自动续加更早历史；右沿保持实时刷新。

**后端**（`aletheia/data/ccxt_provider.py` + `server/routes/data.py`）：
- 新增 `_TF_MS` 映射 + `timeframe_ms(tf)` helper（tf→毫秒的单一真源）
- `get_ohlcv`/`_fetch_ohlcv` 增加 `since: int|None` 参数，透传给 CCXT `fetch_ohlcv(since=)`
- `/api/market/ohlcv` 增加 `before`（unix 秒）参数：`since_ms = before*1000 - limit*tf_ms`，并把 `>= before` 的 candle 过滤掉，保证续加页与左沿不重叠
- 缓存策略收紧：只有默认最新视图（`before` 未设 且 `limit==100`）走 TTL 缓存；历史页与实时尾部一律 fetch fresh

**前端**（`api/client.ts` + `charts/PriceChart.tsx`）：
- `getOhlcv` 增加 `before` 参数（第 4 位）
- `PriceChart` 重写为命令式 candle store（refs 持久化，图表不随数据重建）：
  - 初次加载最新 `INITIAL_LIMIT=300` 根
  - 向左滚到 `SCROLL_TRIGGER_BARS=10` 内触发 `loadOlder()`：`before=<最左根>` 取下一页、去重、前插、`setData` 后用 `setVisibleLogicalRange` 平移逻辑区间保持视窗不跳
  - `LIVE_POLL_MS=10s` 轮询最新 2 根，`series.update()` 替换在成形/追加已收盘的 bar
  - 守卫：`loadingOlderRef`/`exhaustedRef`/`initializedRef`；markers 在独立 effect 重挂不重建图表

**验证**：488 passed（+2 新测试：`before` 过滤 + `since` 窗口数学、unsupported timeframe 干净报错）；后端 urllib E2E 确认续加页与最新视图 gap 恰为 1 个 tf、零重叠；Vite :5173 视觉 E2E 确认三种行为（初次 `limit=300`、续加 `before=...`、实时 `limit=2`）全部触发、零 console error。

---

## 2026-06-08 (later⁵ cont.) — Deep Research + ROADMAP v1

**目的**：博采众长制定按周迈步路线图。

**Deep Research 执行**：102 agent / 20 sources / 25 claims → 19 verified (6 killed)

**核心发现（adversarially verified）**：
- 经验闭环有 4 层：无学习 → 记忆累积 → walk-forward 策略修改 → RL 端到端（我们在 1→2）
- 策略必须结构化（Pydantic schema / signal+confidence 数值对），不能 free-text
- Agent 协作主流：fan-out/fan-in + 硬风控门控；对抗辩论是加分非必须
- walk-forward validation 是防噪音固化的唯一可靠方法（SHARP 论文 2026.05）
- 单 agent RL vs 多 agent 辩论之争未定——我们选多 agent + 可解释性
- TradingAgents 明确说"treat as research scaffold"——这是诚实定位

**产出**：`docs/ROADMAP.md`（12 周路线图 + 4 个观测门 + 红线 + 失败模式表）

**来源（核实度最高的）**：
- TradingAgents (github.com/TauricResearch) — structured debate + Pydantic output
- ai-hedge-fund (github.com/virattt) — fan-out/fan-in + signal/confidence
- SHARP (arXiv 2605.06822) — walk-forward validated policy edits
- TradingGroup (arXiv 2508.17565) — chain-based + 20-day outcome labeling
- AlphaQuanter (ACL Findings 2026) — single RL agent, tool-augmented
- FinRL (github.com/ai4finance-foundation) — ensemble DRL baseline
- AutoGen/Magentic-One — actor model + stall detection

---

## 2026-06-08 (later⁵) — Pipeline gap analysis + 文档统一

**目的**：对照"全市场扫描→分析→策略→开仓→平仓→评估→学习→总结"完整预期，
逐环节代码核实当前实现与预期的差距。

**核心发现**：
- 管道骨架 80% 接通：5 crypto + 4 xStocks + 4 stocks + prediction markets 自动扫描；
  market_observer→paper_trader→position_manager 全链路每 15min 自动运行
- 三大结构性差距明确定位：
  1. **经验闭环只闭 30%**：6 agent 写 knowledge，只 2 agent（observer/designer）读回
  2. **Agent 无协作流**：13 个 scheduler job 各自 cron，无方向性数据传递
  3. **策略文字非规则**：designer 产出 entry_rule 自然语言，trader 不读
- 两个 agent 代码完整但未排入 scheduler：`post_trade_reviewer` / `risk_critic`

**产出文档**：
- `docs/PIPELINE_STATUS.md`（新建）— 逐环节现状表 + 差距分析 + scheduler 全景 + 北极星阶段图
- `docs/AI_HANDOFF.md` — 更新 current state + next steps
- `docs/CURRENT_TASK.md` — 更新状态为 PLANNING（stage 0.5 措施待确认）
- Memory `aletheia-mvp-status.md` — 更新 pipeline completion 表

**无代码变更**。纯分析+文档整理 session。

---

## 2026-06-08 (later⁴) — xStocks 接入 + 管道 bug fix 三项

**本次 session 两阶段工作：**

### 阶段一：Bitget xStocks（ON-series 代币化股票）接入

- `aletheia/config/schema.py` — 新增 `observed_xstocks: list[str]`（默认 AAPLON/TSLAON/NVDAON/SPYON）
- `aletheia/trading/instruments.py` — 新增 `xstocks_instruments_from_config()`，返回 `exchange="bitget"` 的 `InstrumentId` 列表
- `aletheia/agents/market_observer.py` — 新增 xStocks 分析 loop：独立 Bitget provider，`asset_class="equity"`，Opportunity 写入但 paper_trader gate 门控跳过（observation-only）
- `aletheia/trading/market_data.py` — 新增 xStocks ticker 采集 loop，写入 `market_snapshots`（position_manager 定价所需）；提取 `_fetch_and_store()` helper 复用
- `tests/test_xstocks_observer.py` — 4 个新测试覆盖 xStocks 路径

### 阶段二：管道 bug fix（全链路审计发现）

1. **`Opportunity.confidence` 默认值** (`aletheia/storage/models.py:51`)
   - `"medium_confidence"` → `"speculative"`
   - 原因：paper_trader `_CONFIDENCE_RANK = {"speculative":0, "medium":1, "high":2}` 不含 `"medium_confidence"`，任何默认值 opportunity 被 rank KeyError 当 0 静默跳过

2. **`paper_broker.py` datetime 时区不一致**
   - 移除 `from datetime import datetime` 和 `filled_at=datetime.utcnow().isoformat()`（naive）
   - `Order.filled_at` 默认 None；`Fill.filled_at` 用 `_utcnow()`（带 tzinfo）

3. **xStocks ticker 未写入 market_snapshots**（顺带修复）
   - position_manager 调 `_latest_price(symbol)` 时因无 snapshot 返回 None → 持仓永不触发 stop/TP
   - 修复：`market_data.py` xStocks loop 调 `_fetch_and_store()` 同等写入

**测试结果**：474 passed，1 flaky（`test_write_audit_jsonl` 全量并发路径泄漏，单跑通过，既存问题）

**commit**: `0303907 fix(pipeline): confidence default, datetime tz, xStocks ticker snapshots`
**commit**: `4f59d70 feat(xstocks): Bitget tokenized equity observation via market_observer`

## 2026-06-08 (later³) — Chart blackout root-cause: stale gateway process + provider consolidation

**Symptom**: Every lightweight-charts component (equity curve + the 3 markets
K-line charts) rendered blank. `/api/market/ohlcv` returned
`{"error":"market_data_unavailable","candles":[]}` and the frontend's
`Array.isArray(data) ? data : []` guard silently turned that into an empty array.

**Root cause was NOT missing abstraction** — the data layer (`MarketDataProvider`
protocol, `CCXTProvider`, `OHLCVCache`, `symbols.py`) was already implemented and
correct on disk. Two real causes:

1. **Stale process (the actual blackout).** The gateway booted at 11:16; the commit
   that switched `data.py` to `CCXTProvider` landed at 13:26. The running process
   held the *old* `data.py` in memory — which called legacy `BitgetClient.get_ohlcv()`
   with canonical `BTC/USDT`, a symbol Bitget REST v2 rejects (`Parameter BTC/USDT
   does not exist`). **Completely invisible**: `/api/health` still reported healthy.
   Fixed immediately by `aletheia gateway restart`.
2. **Construction drift.** `CCXTProvider(...)` was hand-constructed from the same
   "read config → build" template in 3 places, inviting copy-paste in new components.
   A dead, uncalled singleton `get_default_provider()` also lingered — exactly the
   cross-event-loop singleton trap we want to forbid.

**Hardening landed (so this class of bug surfaces honestly next time):**

- **Version-drift health check.** New `aletheia/agent_runtime/build_info.py` records
  the source commit at boot (`record_boot_commit()` in `lifespan()`, before any
  scheduler thread starts). `code_drift_issue()` compares disk HEAD to the booted
  commit; `compute_health()` now appends `code_updated_pending_restart:<old>..<new>`
  when they diverge → `/api/health` goes degraded, telling us to restart. Reuses
  `update/git_ops.current_commit()`. Non-repo / git-unavailable → stores None → never
  false-alarms. Known limit (accepted): uncommitted working-tree edits don't move
  HEAD so aren't flagged — but the real failure here was "committed, not restarted",
  which is caught.
- **Single provider factory.** Deleted the dead singleton; added
  `get_market_data_provider(exchange=None)` as the one market-data entry point
  (per-call, never cached — documented why: CCXT binds aiohttp to the creating loop,
  scheduler uses a fresh loop per job). Routed 3 sites through it: `routes/data.py`,
  `trading/market_context.py`, `trading/market_data.py`. Left the 2 Bitget-only
  xStocks sites (`market_data.py`, `agents/market_observer.py`) constructing
  `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly — both now
  carry a comment marking the deliberate exception (factory would force the config
  fallback `bybit`, which doesn't list ON-series tokens).
- **CLAUDE.md** Development Rules gained a market-data rule pointing at the factory
  and forbidding `BitgetClient` for market data.

Note: the equity curve being flat at 0.0 is *honest*, not this bug — `PnLChart`
reads `/api/market/portfolio-history` (DB), which has snapshots but no positions yet.
Out of scope.

## 2026-06-08 (later²) — Market context polish (thesis fallback + score persisted)

After the market context layer landed, real-run inspection surfaced two
rough edges:

1. **codex_cli's structured output is not 100% reliable.** On ~1 in 3 runs
   it returns a valid JSON shape but with empty `rationale` and empty
   `key_observations` (the JSON parse drops fields, or the model returns a
   minimal stub). Old code then fell back to `thesis = context[:500]`,
   dumping the raw markdown snapshot (`## Verified market data snapshot`,
   table headers etc.) into the dashboard thesis cell — looked broken.
2. **`confidence_score` only lived inside the thesis text** (`score=65/100`
   at the end). The dashboard couldn't surface it as a real column.

Both fixed:

- **`opportunities.confidence_score INTEGER`** column added (additive
  migration; nullable so older rows stay valid). `Opportunity` Pydantic
  model + frontend `Opportunity` type updated.
- **Thesis fallback rewritten**: when rationale + observations are both
  empty, thesis is a one-line stub citing the failure mode
  (`"No structured rationale produced (direction=X, confidence=Y). Likely
  cause: LLM unavailable or structured-output parse failure — see
  agent_runs/errors."`). Raw snapshot never leaks through.
- **Charts page Markets table** — added a `Score` column showing
  `confidence_score/100` with a tooltip explaining what it means and why
  it can be empty. Side-by-side with categorical confidence so the model's
  full calibration is visible at a glance.

Real e2e (3 fresh runs after migration applied):
- `long / medium / 64/100` — full structured shape ✅
- `neutral / speculative / 50/100` — model genuinely on the fence,
  honestly committed a midpoint ✅
- `neutral / speculative / —` + thesis "No structured rationale..." —
  Codex CLI timed out, fallback stub kicked in cleanly ✅

New test `test_observer_thesis_fallback_avoids_raw_snapshot`. Full suite
**423 passed** (the 2 failures in `test_exchange_sync.py` predate this
work — verified by stashing other working-tree mods and re-running).
Zero change-controlled module touched.

## 2026-06-08 (later) — Market context layer (fixes "all neutral / speculative")

The dashboard Markets page had been showing every market_observer opportunity
as `direction=neutral, confidence=speculative` — symptom that read like "AI is
not running". Diagnosis: AI is running fine (codex_cli healthy), but the LLM
was being fed exactly **one line per symbol** ("BTCUSDT: price=X 24h_change=
0.05%"). With a 1-D input the model can only reply at the base rate; the
system prompt's "be conservative — default to neutral" hardened that into
permanence.

Fix is the data-supply layer, not the prompt. Borrowed the **TradingAgents**
pattern (multi-timeframe pre-computed indicators + short close series +
markdown verified-snapshot tables + structured output schema) using stdlib
only — no numpy/pandas/ta-lib. Research memo and design rationale in
`docs/research/market_context_eval.md` (covers OpenAlice tool-as-DSL pattern,
TradingAgents predigest pattern, why VergeX is unavailable as a reference).

What got built:

- **`aletheia/trading/indicators.py`** (new, stdlib-only) — SMA / EMA / RSI
  (Wilder's smoothing) / ATR / Bollinger / momentum / volume z-score / cross-
  asset relative-strength rank. Pure functions, oldest→newest series, None on
  too-short windows.
- **`aletheia/trading/market_context.py`** (new) — `build_market_context()`
  pulls 1h × 60, 4h × 50, 1day × 30 OHLCV per symbol via the existing
  `BitgetClient.get_ohlcv` (no creds, no new endpoints), computes the
  indicator pack per timeframe, ranks the symbols by 24h return, and renders
  a ~2.5kb markdown block: per-symbol indicator table + last-10 1h closes +
  cross-asset RS table + indicator glossary. Falls back to the legacy 1-line
  context on any failure (red line: agents must never crash).
- **`aletheia/agents/market_observer.py`** — feeds the new context block in,
  upgrades `MarketRead` schema to add `confidence_score` (0-100, forces the
  model to commit a number) and `key_observations` (list of specific data
  citations — empty list signals "no concrete evidence"). System prompt
  rewritten: removed the "default to neutral" hard nudge, replaced with
  calibration ladders (high/medium/speculative criteria, when long vs short
  vs neutral is justified). Adds two `record_step()` breadcrumbs
  (`market_read_input` with the full snapshot, `market_read_output` with the
  structured read) so the dashboard step trace shows the model actually saw
  data and reasoned over it.

Real e2e: first run after the change produced
`direction=long, confidence=medium` with the thesis citing
"all three above 1h SMA20 and SMA50, positive 1h+4h Mom10, ETH leads cross-
asset RS" — vs the previous N runs which were unanimously
`neutral / speculative`. The ground truth (BTC/ETH/SOL all up 2–6% intraday
with multi-timeframe agreement) finally reaches the model.

Red lines kept: no funding/OI/orderbook (would extend client.py surface), no
news/sentiment (AGENTS.md ban), no numpy/pandas (dependency discipline), no
change-controlled module touched. New `tests/test_market_context.py` (17
tests covering indicators, builder render, fallback path, observer integration
and structured-output extraction). Full suite **423 passed**.

What this *doesn't* do: deepen the perception layer (funding regime / OI /
orderbook / news) — those remain north-star-deferred until stage-0 evidence
accumulates. This pass just ensures the data we already collect actually
reaches the model. The Learning Ledger's `activation_ratio` now has a real
input signal (vs being permanently 0% because no opportunity ever advanced).

## 2026-06-08 (latest) — Three remaining real gaps closed

After WEBUI_BACKEND_HANDOFF was done, three real gaps remained that I hadn't yet
addressed (everything else was either already fixed, recorded as a future-scope
north-star item, or banned by AGENTS.md). All three closed in one pass — they're
small once the §1.1 causal chain and §6 experience-loop scaffolding are in place:

- **Gap 1 — agent_run_steps now actually populated.** The `/api/agent-runs/{id}/steps`
  endpoint shipped in the prior pass returned an empty array because nothing was
  writing to the table. Hooked the write in `agents/helpers.py::log_tool` (every
  tool call now produces a step) plus a new `record_step()` for non-tool reasoning
  nodes. Per-run step counters live in-process and are released by the orchestrator
  at end-of-run so the dict stays bounded. **Zero changes to the 8 agents** — they
  already use `log_tool` everywhere, so the trace fills in automatically.

- **Gap 2 — Stability monitor + `GET /api/stability`.** Bridges the gap between
  "process alive" (existing `health.py`) and "data internally consistent"
  (north-star §7 stage-0 evidence). New `agent_runtime/stability_monitor.py`
  reports orphan counts (fills with no order, executed proposals with no order,
  etc.), strategy_id drift (paper proposals without a strategy — direct §1.1
  causal-chain signal), 24h agent failure ratio, uptime hours, and a 0-100
  composite score with explicit reason codes. Read-only — inconsistencies are
  REPORTED, never silently repaired. Operator can poll the endpoint through the
  7-14 day stage-0 run and watch the score numerically.

- **Gap 3 — Learning Ledger gets a quality block.** `dreaming_agent` was already
  emitting a Learning Ledger but it only counted volume ("research_note: 12,
  strategy_memory: 3"). Volume can be noise. Added stage-1 quality signals:
  activation ratio (% strategies that reached paper_running or beyond),
  retire/deprecate ratio (noise being filtered — non-zero is healthy),
  post-trade-review coverage (%), and lessons-consulted count over 24h
  (`retrieve_lessons` + `load_playbooks` calls — non-zero means the experience
  loop is actually closing). This is the evidence north-star §7 stage 1 wants.

Wiring is intentionally minimal: no new tables, no schema migration, no agent
behaviour changes. Just three new read paths that surface signals already implied
by data we already record. New `tests/test_three_gaps.py` (10 tests) including a
red-line test that orphan fills get DETECTED, never silently fixed. Full suite
406 passed.

What this leaves on the gap list: none of the *immediate* "real" gaps. Remaining
items are all north-star architectural work (Sophia-class long-term identity,
RL feedback loop, cross-agent debate workflow, deeper market perception) that
the project's stage sequencing explicitly defers until 7-14 days of clean stage-0
evidence accumulates. The stability + ledger numbers above are the receipts that
let "stage 0 done" become a yes/no rather than a vibe.

## 2026-06-08 — Exchange Account Sync (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap)

The Portfolio dashboard was off by an entire reality: it only saw locally-managed
positions, so `total_value_usdt = 0` even when the demo account held real balance.
Now closed:

- **`portfolio/exchange_sync.py`** (new) — `sync_exchange_account()` calls the existing
  read-only Bitget client methods (`get_balances`/`get_positions`/`get_open_orders`)
  and caches the result as a JSON blob in the `settings` table (no schema change). Status
  classifier `ok | stale | unconfigured | error`. Never raises; degrades to None and the
  UI shows the existing caveat. **Note (turned out client.py was already exposing
  everything — no change-controlled module touched).**
- **`compute_portfolio()`** — extended with `exchange_equity_usdt`,
  `exchange_state_age_sec`, `exchange_external_positions` (drift signal), and
  `exchange_sync_status`. The local managed `total_value_usdt` field is preserved so the
  UI can show both side-by-side and reconcile drift.
- **`GET /api/exchange/account`** — read-only cached view; HTTP path never blocks on
  Bitget. Scheduler refreshes the cache every 5 min via a new `_run_exchange_sync` job.
- **`aletheia bitget sync`** CLI — manual one-shot for debugging.

Red lines kept: read-only against the exchange; external positions stay out of the
local `positions` table (they have no proposal back-link → would violate trust §1.1);
`exchanges/bitget/client.py` untouched. New `tests/test_exchange_sync.py` (9 tests
including a red-line test that asserts the local positions table stays empty after a
sync). Full suite 396 passed. Real e2e: CLI sync against the live demo account
returned `status: ok` and the cached view flowed into `/api/exchange/account` and
`/api/portfolio` after a gateway restart.

Cross-process visibility caveat: SQLite WAL means a CLI write doesn't immediately
appear to a long-running gateway process holding its own connection. In practice the
scheduler refreshes the cache from inside the gateway process every 5 min, so the API
sees the truth on its own timeline — no extra wiring needed for the production path.

## 2026-06-07 — WebUI backend handoff: position FK + steps endpoint

Implemented the two actionable, in-scope gaps from `WEBUI_BACKEND_HANDOFF.md`:

- **#1 position → proposal/order causal link** (⭐ highest value): `positions` gained
  nullable `proposal_id`/`order_id` FK columns (CREATE TABLE + additive migration).
  Both brokers populate them when a fill opens a position; averaging into an existing
  position preserves the *original* opener. Makes the position→upstream direction of the
  trust §1.1 causal chain real (CausalChain.tsx no longer needs "not recorded"). Touched
  change-controlled `execution/bitget_broker.py` — additive only, focused regression tests.
- **#2 `GET /api/agent-runs/{id}/steps`**: read-only endpoint exposing `agent_run_steps`
  (new `list_run_steps`/`insert_run_step` repo fns). Honest caveat: the table is not yet
  written by any agent, so it returns `[]` today — populating it is a separate, larger
  change (every agent) beyond the handoff's "expose the table" ask.

Not done (by design): **#3** exchange equity touches change-controlled `client.py` → needs
explicit owner authorization; **#4** news ingestion is banned MVP scope (AGENTS.md).

`tests/test_webui_backend_handoff.py` (6 tests); full suite 387 passed.

## 2026-06-07 — Experience loop (north-star stage 2, minimal closed loop)

### Added — knowledge that reads back into decisions (§6 gap closed, minimally)

The knowledge base was write-only (the central north-star gap). It now feeds prior
lessons back into decisions, using frontier-but-constrained patterns (Generative-Agents
retrieval scoring, Hermes-style memory block, RAG provenance) on **SQLite FTS5 — no
vectors, no new deps**:

- `knowledge/retrieval.py` (new): FTS5 index + `retrieve_lessons()` scored by
  BM25(relevance) × recency-decay × importance. **Quality gate: only `high_confidence`
  + `active` items are retrievable** — speculative/archived noise can't become "experience".
  `format_memory_block()` renders results as an **explicitly advisory, provenance-tagged**
  block. Degrades to empty on any failure (FTS absent / no match) so agents never break.
- `migrations.py`: `knowledge_fts` virtual table, created out-of-band so a SQLite build
  without FTS5 can't break other migrations.
- `knowledge/manager.py`: `create_*` index high-confidence items; archive/deprecate
  remove them from the index (archived ⇒ not recalled).
- `trading/playbooks.py`: `load_playbooks()` / `format_playbook_block()` — playbooks are
  now **read** into the designer prompt (were seed-only, 0 readers). Read-only; agents
  never write playbooks.
- `agents/market_observer.py` + `agents/strategy_designer.py`: inject the memory block
  (and playbooks for the designer) into the LLM prompt; retrieval is logged as a
  `retrieve_lessons` tool call (observable). Injection is pure prompt text — it cannot
  touch risk/execution/config.
- `cli/knowledge_cmd.py` (new): `aletheia knowledge reindex` (backfill) + `search` (preview).

Governance held: read-only, quality-gated, advisory-not-instruction, no change-controlled
module touched, no live gate weakened. New `tests/test_experience_loop.py` (8 tests);
full suite 381 passed. End-to-end smoke confirmed a high-confidence lesson reaches the
observer's decision prompt with its provenance tag.

## 2026-06-07 — WebUI structural redesign (frontend-only)

### Changed — 14 dashboard pages → 10 role-organized destinations

Complete structural redesign of the React frontend to fix the owner's pain points
(density / layout / transparency / workflow). Diagnosis: UI was a flat page-per-DB-table;
the framework wants §23.1's 6 roles around the causal chain + a workflow flow. **Zero
backend / API changes** — pure frontend composition over existing endpoints.

5-phase IA (Observe / Decide / Execute / Evolve / Operate). Merges:
- Positions + Paper Trades → **Trades & Positions** (`TradesPositions.tsx`) + new
  **`CausalChain.tsx`** inspector (proposal→risk→order→fill→position; position-anchored
  upstream shown as "not recorded" — honest re trust §1.1, never symbol-matched).
- Agent Runs + Opportunities → **Agent Activity** (`AgentActivity.tsx`, tabs via new
  `Tabs.tsx`); run drawer "Reasoning Steps" block = mount point for the unbuilt
  `GET /api/agent-runs/{id}/steps`.
- Dreaming + Knowledge Base + Reports → **Learning** (`Learning.tsx`, 3 tabs; §4 KB
  taxonomy preserved, no type laundering).
- Proposals → **Approval Desk** (decision cards + inline risk verdict + confirm-to-approve).

New `PageHeader.tsx` (title + role descriptor) on all 10 pages — directly answers the
"transparency insufficient" complaint. News Intel got the §17.3 honesty banner
("manual entry / ingestion pipeline not configured"). RiskControls grid → responsive.

Deleted 7 merged page files. Legacy routes redirect (no dead links). CommandPalette +
cross-links updated. Verified live (preview MCP): nav structure, tab switching, honest
empty states, legacy redirects, zero console errors. tsc + vite build green.

Docs: `WEBUI_PAGE_MAP.md`, `OVERVIEW_REDESIGN.md`, `WEBUI_BACKEND_HANDOFF.md` (parked
backend gaps), `BACKEND_ISSUES.md §12`. Branch: feat/strategy-causal-chain (not merged).

## 2026-06-07 (later)

### Fixed — re-audit of BACKEND_ISSUES §10 + 8 falsely-archived issues

Re-verified the §10 "all fixed 2026-06-06" archive against current code (40+ items):
~75% true, ~25% claimed-fixed-but-not. Eight confirmed false/half-true, now really
fixed (each with a focused regression test; full suite 373 passed):

- §1.2 live-order: insert a `pending` order row BEFORE `place_order` (was place-then-
  insert → exchange order with no local trace if DB write fails). `bitget_broker.py`
- §1.3 health alert: run the Telegram coroutine to completion in a worker thread (was
  `loop.create_task` fire-and-forget → alert silently dropped from a sync caller). `health.py`
- §2.3 paper/demo risk: added `max_demo_position_notional_usdt` + `max_demo_single_trade_risk_pct`
  with distinct violation tokens (was unbounded → promotion evidence built from unbounded
  trades). `risk/engine.py`, `config/schema.py`
- §2.3 clientOid: `place_order` now sends a proposal-derived `clientOid` (idempotency on
  retry). `exchanges/bitget/client.py`
- §2.3 kill-switch TOCTOU: `evaluate_proposal` serialized via `_eval_lock` so a daily-loss
  trip is visible to the next proposal. `risk/engine.py`
- §2.5 paper_trader: autonomous-OFF gate duplicated into the agent body (CLI/API trigger
  bypassed the scheduler gate). `agents/paper_trader.py`
- §3 commit_all: dreaming agent calls `commit_all` after consolidation (Trading-as-Git was
  wired but never invoked). `agents/dreaming_agent.py`
- §3 gateway logs: size-cap rotation (`rotate_if_needed`) + tail-only reads (was unbounded
  growth + whole-file read → OOM). `gateway/logs.py`, `_gateway_runner.py`

Also added `starting_capital_usdt` as a real config field (was read via getattr fallback).
The §10 archive now carries a correction banner; lesson recorded: never trust a "fixed"
claim without grepping current code.

### Fixed — strategy↔trade causal chain (gap #2, north-star stage 0/2 prerequisite)

The `strategy_id` causal chain was broken in **four** places, not one (the
2026-06-06 §1.1 "fix" only wired paper_trader's fallback, leaving the upstream
producer side dead). All four now connected:

- `strategy_lifecycle.create_strategy()` accepts `parameters` → persisted to
  `StrategyRecord.parameters` (was always `"{}"`; trust §4.2 audit snapshot)
- `strategy_designer` produces a structured parameter spec and auto-advances the
  strategy through the **ungated** lifecycle states to `paper_running` (stops there;
  live gates untouched), so a tradable strategy actually exists
- `strategy_designer` registered as a daily scheduler job (was never scheduled →
  no strategies were ever produced autonomously)
- `market_observer` binds new opportunities to an active strategy via
  `linked_strategy_id` (honest None if none active; was never set, zero coverage)

Result: promotion-gate `paper_trades` count is no longer structurally stuck at 0.
New `tests/test_strategy_causal_chain.py` (5 tests) locks the chain end-to-end.
Suite: 357 passed.

### Changed

- Documentation housekeeping: archived all fixed items in BACKEND_ISSUES.md (§10) and FRONTEND_ISSUES.md (§10.1–10.24); removed root-level IMPLEMENTATION_GUIDE.md duplicate (canonical copy in docs/)
- Gateway: replaced all Windows subprocess calls (wmic/tasklist/powershell/taskkill) with pure ctypes Win32 API — fully invisible operation, zero console window flashes

### Decision

- Process introspection on Windows uses kernel32/ntdll ctypes instead of spawning external executables (see DECISIONS.md)

## 2026-06-06

### Added

- CLAUDE.md (project overview for Claude Code)
- .claude/ directory (settings, commands: start-session, end-session, update-handoff, project-audit)
- .codex/config.toml (Codex project config)
- docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md, docs/DECISIONS.md, docs/DEVLOG.md, docs/AI_WORKFLOW.md

### Fixed (major pass)

- **Backend (80+ fixes)**: All items in BACKEND_ISSUES.md §1-§3 — trust invariants (strategy_id causal chain, execution gateway isolation, git ff-only, promotion gates), data integrity (PnL fees, KB status filtering, CORS, audit symmetry, risk evaluation, scheduler error handling, telegram hardening), dead code cleanup
- **Frontend (24 fixes)**: All items in FRONTEND_ISSUES.md §1-§3 — shared types/DataTable/DetailDrawer components, risk_decision display, KB markdown reading, autonomous_paper_trading toggle, effective config card, promotion gates UI, chart-trade linking, dreaming 6-section layout, overview sparkline, cross-page navigation, agent run tool calls

### Notes

AI workspace initialization. Existing AGENTS.md preserved as-is (already comprehensive).
