# 参考项目分析：OpenAlice & Prism → Aletheia 可取之处

> 状态：一次性分析报告（2026-06-10）。机器无关、长期有效的*意图/参考*，非运行态事实。
> 源码克隆位置：`C:\Users\ollama\Projects\_refs\{OpenAlice, prism}`（在项目仓库之外，不污染 git）。
> 结论先行：**OpenAlice 是理念同源、实现成熟的"成年版"，值得对照升级；Prism 架构同构但犯了和 Aletheia 同样的病（LLM agent 是死代码），是反面教材而非优点来源。**
>
> **当前取舍**：第四节的 **commit_hash 链**与 adopted-position 审计引用已实施，并由当前 data/audit/trust 契约维护。`state_after` 全账户快照会复制已有持久状态，reject 已有一等审计事件，Guard 责任链又只是高风险结构重排，三者均不进入当前 roadmap。深度研究见 `docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`，历史状态卡见 [`TAG_BORROW_BACKLOG`](../../archive/legacy/issues/backlog/TAG_BORROW_BACKLOG.md)。

---

## 一、两个项目的定位

| | OpenAlice | Prism | Aletheia |
|---|---|---|---|
| 体量 | 大型 TS/Electron monorepo | ~1770 行 Python/FastAPI MVP | Python MVP |
| 资产 | 股/币/商品/外汇/宏观，多 broker | 中国公募基金组合 | Bitget 现货/合约 |
| 定位 | "Trading-as-Git" 成熟实现 | 研究/决策支持，不下单 | "Trading-as-Git"，弱实现 |
| 与 Aletheia | **理念同源**（连 "Trading-as-Git" 名字都一样） | **架构同构**（多 agent + async task + 规则 kernel） | — |

---

## 二、OpenAlice — 理念可借（区分"理念" vs "实现别抄"）

### 1. ⭐ Trading-as-Git 真状态机（最高价值，直接对照 Aletheia 同名弱实现）

OpenAlice 把订单流做成了**真正的 git 语义**，而非比喻：
`add(op)` → `commit(msg)`（只算 hash、不执行）→ `push()`（真执行）/ `reject(reason)`。

证据 `_refs/OpenAlice/services/uta/src/domain/trading/git/TradingGit.ts`：
- `add` 入暂存区（`stagingArea`，`:56`）
- `commit` 生成 `pendingHash` 但**不执行**——`commit` 与 `push` 分离，给"人审批"留了天然窗口（`:65-85`）
- commit hash = `SHA-256(JSON{message, operations, timestamp, parentHash}).slice(0,8)`（`:34-39`）→ 内容寻址、可验证、形成链（`parentHash`）
- `push` 逐 op 执行，把成功/失败都记进 `OperationResult`，并在执行后拍一张 `stateAfter` 全量快照存入 commit（`:115-127`）→ **每个 commit 自带"执行后账户状态"，可重放、可审计、可 diff**
- `reject` 也写一条 commit（`[rejected] ...`），状态机里"拒绝"是一等事件而非沉默丢弃（`:145-187`）

类型定义 `_refs/OpenAlice/packages/uta-protocol/src/types/git.ts`：
- `Operation` 是**判别联合**（`placeOrder/modifyOrder/closePosition/cancelOrder/syncOrders/reconcileBalance`，`:22-42`），不是 `Record<string,unknown>` 类型擦除
- `GitCommit{hash,parentHash,message,operations,results,stateAfter,timestamp}`（`:77-86`）
- 所有金额字段都是 **string（Decimal-as-string）**，避免 IEEE754 浮点误差、保证 JSON round-trip（`:65-73`）

**Aletheia 现状差距**：`aletheia/execution/gateway.py` 只有 `submit_proposal` / `execute_existing_proposal` / `_execute_proposal`——有"提案→执行"，但**没有 staging、没有 commit hash、没有 parent 链、没有 commit 级 stateAfter 快照、没有 reject 作为一等记录**。

**可落地建议（Python，smallest change）**：
- 给 proposal 引入 `commit_hash = sha256(canonical_json(proposal_core))[:8]`，proposal 落库时计算，执行结果(fill/reject)挂到同一 hash 下 → 立刻获得"一笔决策的内容指纹 + 可审计链"。
- 执行后把账户快照（持仓/现金/未实现盈亏）作为 `state_after` 存进同一条记录，而非散落各表 → 任意历史时刻可重建。
- 把"用户拒绝/风控拒绝"也写成一条带 hash 的记录（你已有 JSONL，天然适合 append-only commit log）。
- **别抄**：OpenAlice 的 Decimal-as-string 是 JS 无原生 Decimal 的妥协；Python 用 `decimal.Decimal` 即可，但"金额不落 float"这个纪律要学。

### 2. ⭐ Guard 责任链 + 可配置注册表（直接对照 risk/engine.py 巨函数）

证据 `_refs/OpenAlice/services/uta/src/domain/trading/guards/`：
- `guard-pipeline.ts:13-37`：pipeline 是**唯一碰账户的地方**，它组装好只读 `GuardContext{operation, positions, account}`，再把 op 顺序过每个 guard；**guard 自己从不接触账户**——只看组装好的 context。这是漂亮的关注点分离。
- `types.ts:12-15`：`OperationGuard { name; check(ctx) → string|null }`——返回 `null` 放行，返回字符串即拒绝原因。无状态、可单测、契约极简。
- `max-position-size.ts` / `cooldown.ts` / `symbol-whitelist.ts`：每个 guard 独立成文件，构造时吃 `options`（如 `maxPercentOfEquity`、`minIntervalMs`、`symbols[]`），逻辑各 30-50 行。
- `registry.ts:6-34`：guard 按 `type` 字符串注册，`resolveGuards(configs)` 从配置数组实例化 → **风控规则变成"配置 + 可插拔单元"，加一条规则不动主流程**。

**Aletheia 现状差距**：`aletheia/risk/engine.py:64` 的 `_evaluate_proposal_locked` 把 10+ 条检查（kill_switch / symbol / market / live_state / permission / position_size / open_positions / short / daily_loss / account_mode / leverage）**全部内联在一个巨函数里**，靠 `violations.append(...)` 累积。功能正确、且有 `_eval_lock` 处理 TOCTOU（这点比 OpenAlice 还严谨），但：加规则要改主函数、单测要构造整个 proposal、规则无法按账户/模式差异化配置。

**可落地建议**：
- 把每条 check 抽成 `Guard.check(ctx) -> Violation | None`，`evaluate_proposal` 变成"组装只读 ctx + 遍历 guard 列表"。`_eval_lock` 和 kill_switch 短路保留在 pipeline 层。
- **收益对 Aletheia 尤其大**：你的优先级是"信任硬化"，而 change-controlled 的 `risk/` 正是审查重点——拆成小 guard 后，每条规则可独立审查、独立单测、diff 更小，审查成本骤降。
- **别抄**：OpenAlice guard 是无状态进程内对象（`cooldown` 用内存 `Map` 存上次时间）——这对 Aletheia 不行，你的 memory 里就记着"限频须持久化"（`aletheia-self-heal-layer`）。Aletheia 的 guard 状态要落 SQLite/JSONL。

### 3. 凭证隔离（硬件钱包模型）——理念可借，完整实现是过度工程

OpenAlice 拆成两个长生命周期进程（README "Architecture" 节）：
- **Alice**（决策侧）：agent runtime + 研究 + UI，**不持有 broker 凭证、不直接连交易所**，只通过 HTTP SDK 调 UTA。
- **UTA**（执行侧）：持有 broker 连接、Trading-as-Git 状态机、guards、快照；**只绑 `127.0.0.1`**，仅同机 Alice 能访问。
- 比喻：像硬件钱包——"持凭证的那一半小、隔离、不动；富客户端那一半随便放哪"。UTA 设计成将来可下放到手机/家里常开盒子，Alice 跑 VPS。
- Guardian supervisor 负责按序拉起、用 `/__uta/health` gate Alice 启动（README）。

**对 Aletheia 的启发**：你已经有 "Execution Gateway 是订单唯一通道" 的概念，但它和决策在同进程。**理念可借**：凭证（secrets.enc/.secret.key）与决策逻辑的信任边界要清晰——这正是你 change-controlled 模块（secrets/auth/client/live）想守的东西。
**别抄**：单人自用 MVP 不需要真的拆双进程 + supervisor + 健康门控，那是为"凭证下放到独立设备"的远期目标服务的。Aletheia 当前用模块边界 + change-control 守住即可。

### 4. reconcileBalance — 外部余额漂移作为一等事件（你踩过的坑）

`TradingGit.recordReconcile`（`git/TradingGit.ts:201-257`）+ `Operation` 的 `reconcileBalance` 变体（`types/git.ts:28-42`）：当 broker 报告的余额与订单 log 推算不符（首次启动 bootstrap、外部转账、staking 奖励、平台外交易），**记一条"虚拟市价成交 @ 观察价"的 commit**，让 cost-basis 流水线自然吸收这个 delta，而不是沉默覆盖或丢弃。

**对 Aletheia 的对照**：你的 memory `aletheia-portfolio-sync-gap` 记着这个痛点已 CLOSED（Exchange State Provider 的架构教训）。OpenAlice 的做法值得对照——它把"漂移"做成**可审计的 reconcile commit**而非静默 sync，符合你"State consistency > Auditability"的优先级。如果将来 portfolio sync 还要演进，这是参考答案。

### 5. Workspace + MCP — 真 agent 协作/自主的载体（方向，非近期）

`_refs/OpenAlice/src/workspaces/`：per-task 目录 + git repo + 持久 terminal session 跑原生 agent CLI（`adapters/{claude,codex,shell}.ts`），通过 `.mcp.json` 把 OpenAlice 工具喂进去。README 称这是"任何非平凡 AI 工作的推荐路径——原生 prompt cache、原生渲染、无协议垫片"。

**对 Aletheia "无 agent 协作"差距的启发**：OpenAlice 跑"真 agent"不是自己写编排循环，而是**把活儿丢进一个装好工具的 workspace，让成熟 agent CLI 去跑**。这是个值得记住的方向——但对 Aletheia 是中长期，不是当前"信任硬化"阶段该碰的。

### 6. 文档治理纪律（轻量可借）

`_refs/OpenAlice/CLAUDE.md` 把 `TODO.md`（前瞻 backlog，完成即删，git log 才是历史）和 `README.md`（大改后才审计的定位文档）的维护规则写进 AI 协作约定。Aletheia 现已采用更明确的分层：GitHub Issues 跟踪当前缺口，契约写当前事实，Archive 保存历史审计；不再维护 Backend/Frontend 本地 issue ledger。

---

## 三、Prism — 架构同构，但同病相怜（主要是反面教材）

### ⚠️ 核心发现：Prism 的 LLM 多 agent 是未接通的死代码

Prism 表面有一整套 LLM 多 agent 协作，实际后台任务跑的是**纯规则算术**——和 Aletheia "策略是纯规则非 LLM" 是**同一个病**：

- 有 `Coordinator`（LangGraph 并行跑 3 个 analyst + 聚合，`agents/coordinator.py:23-55`）、3 个 LLM analyst（`fundamental/technical/risk_analyst.py`，各自 `self.llm.ainvoke(prompt)`）、`COTEngine`（`reasoning/cot_engine.py` 用 LLM 把 agent 结论串成推理链）、`RecommendationEngine`（`analysis/recommendation_engine.py`）。
- **但实际后台分析任务调用的是 `PrismKernel`**（`services/analysis_service.py:137` → `core/prism_kernel.py`），而 `PrismKernel` 里的 `fundamental_agent/technical_agent/risk_agent`（`prism_kernel.py:34-87`）**全是写死的算术公式**：`score = _clamp_score(55 + avg_change * 5)`、`confidence: 0.72` 硬编码、`analysis` 是 f-string 拼的中文句子。**完全不调 LLM。**
- 即所谓 "MAS debate via LangGraph"（`prism_kernel.py:114` 的进度文案）是营销话术——三个节点串行跑算术，互不读对方输出，没有 debate。

**对 Aletheia 的价值**：这是面镜子。Prism 证明了"先把 LLM agent 脚手架搭起来、实际跑规则占位"是一条**会留下死代码 + 语义不诚实**的歧路。Aletheia 若要升级策略，应当**让 LLM 真正进入决策回路**（哪怕只在一个节点），而不是再搭一套漂亮的、不接通的 agent 类。这直接呼应你的优先级"语义诚实 > 能力"。

### 可借的小模式（少数真优点）

1. **进度回调 + WebSocket 实时推送**（干净，~150 行，Aletheia 没有）：
   - `progress_cb(progress:int, message:str)` 贯穿长任务（`prism_kernel.py:107/111/114/125/160`），由 `AnalysisService._update_state` 同时更新 `TaskStore` 和广播（`analysis_service.py:129-135`）。
   - `ProgressHub`（`services/progress_hub.py`）：`{task_id: set[WebSocket]}`，`publish` 时遍历推送、自动清理断开的连接（`:30-50`）——50 行实现一个够用的进度广播。
   - `TaskStore`（`services/task_store.py`）：`asyncio.Lock` 守的 `{task_id: TaskState}`，`queued/running/completed/failed` 状态机。
   - **对 Aletheia 有用**：agent 分析/回测是长任务，前端干等无反馈。这套是低成本的"实时进度条"参考。**别抄**：`TaskStore` 是纯内存（重启即丢，`task_store.py:21`）——Aletheia 要落 SQLite。

2. **LangGraph StateGraph 编排骨架**（升级"纯规则链"的标准路径）：
   - `coordinator.py:48-55` 的模式——`add_node` 注册步骤、`add_edge` 连边、`StateGraph(AgentState)` 共享状态、`asyncio.gather` 并行跑 agent 后 `aggregate`——是把"一根写死的规则链"升级成"可加节点/可加条件分支/可插 LLM 节点"的标准做法。Aletheia 若要从纯规则策略演进，StateGraph 是比手写 if-else 编排更可扩展的骨架（即便 Prism 自己用得浅）。

3. **base_agent 统一契约**（`agents/base_agent.py:22-29`）：`analyze(data) -> {analysis, confidence, reasoning}` 的统一返回契约简单清晰。Aletheia 若做 agent 协作，统一 agent I/O 契约值得参考。

### Prism 明确不值得借

- ❌ **纯规则冒充 LLM 分析**（见上，语义不诚实）。
- ❌ `message_bus.py` 是空壳——`MessageBus` 类只有 `add_agent_node/build`（`:14-27`），所谓"inter-agent communication"根本没实现，agent 间零通信。**别被名字骗到**：它和 coordinator 的并行-聚合一样，是"假协作"。
- ❌ 技术栈反方向：SQLAlchemy async + Alembic 迁移 + docker-compose + Postgres-ready——对 Aletheia 坚持的 SQLite + JSONL、无 Docker/Postgres 是反向扩张，违反你 CLAUDE.md 的"no uncontrolled expansion"。

---

## 四、给 Aletheia 的优先级建议（按"信任硬化"主线排序）

| 优先级 | 借鉴点 | 来源 | 工作量 | 为何契合当前阶段 |
|---|---|---|---|---|
| **P0** | risk/engine.py 巨函数 → 可插拔 Guard 责任链 + 注册表 | OpenAlice guards/ | 中（重构，行为不变） | change-controlled 模块审查成本骤降，每条规则可独立单测 |
| **P0** | proposal 引入 commit_hash + commit 级 state_after 快照 + reject 一等记录 | OpenAlice TradingGit | 中 | ✅ commit_hash 部分已实施（`b8ed990` P0-1，2026-06-29）；reconcile audit 已链入（`31eb1a9` P1-2）；state_after 快照 + reject 同构待 owner 批准（触 `execution/`） |
| **P1** | 金额不落 float（统一 Decimal） | OpenAlice 全线 | 小-中 | State consistency；现在用 float 算 notional（engine.py:108）有隐患 |
| **P2** | 长任务进度回调 + WebSocket 推送（落 SQLite 版） | Prism ProgressHub/TaskStore | 小 | 体验提升，非信任项，有空再做 |
| **方向** | 策略升级让 LLM 真进决策回路（StateGraph 骨架），**勿重蹈 Prism 死代码覆辙** | Prism 反面 + LangGraph | 大 | 三大差距之一，但不是当前阶段重点 |
| **远期** | 凭证-决策双进程隔离 / Workspace+MCP 跑真 agent | OpenAlice | 很大 | 单人 MVP 暂不需要，记录为方向 |

**一句话**：OpenAlice 的 **Guard 责任链** 和 **commit-hash 审计链** 是当前"信任硬化"阶段性价比最高的两笔借鉴，且都不违反"最小改动/不扩张"原则；Prism 的主要价值是警示——别再搭一套不接通的 LLM agent 脚手架。

---

## 五、落地蓝图（可直接参考施工，非授权）

> 以下把第四节的 P0/P1 落到 Aletheia 现有代码结构上，给出贴合的施工路径 + file:line 锚点 + 验证方式。
> **不构成施工授权**——`risk/`、`execution/` 是 change-controlled 模块，动工前需显式授权 + 焦点回归测试（同 AGENTS.md 红线）。
> 现状核对基线（2026-06-10）：`TradeProposal` 定义在 `storage/models.py:64`，已有 `id`/`status`/`created_at`；风控决策落 `RiskDecision` 表（`models.py` 同区）；订单落 `Order` 表；金额字段全为 `float`；提案入口 `execution/gateway.py:16 submit_proposal` → `_execute_proposal`。

### 蓝图 A（P0）— risk/engine.py 巨函数 → 可插拔 Guard 责任链

**目标**：把 `_evaluate_proposal_locked`（`risk/engine.py:74`）里 10+ 条内联 check 拆成独立 Guard 单元，主函数退化为"组装只读 ctx + 遍历 guard 列表"。**行为完全不变**（纯重构），收益是审查/单测粒度。

**现有 10 条 check 的边界**（来自本次通读 `risk/engine.py:74-160+`）：
`kill_switch` / `symbol_allowed` / `market_allowed` / `live_state` / `permission_mode` / `max_position_size` / `max_open_positions` / `short_allowed` / `max_daily_loss`(带 kill_switch 副作用) / `account_mode_match` / `max_leverage`。

**施工路径**：
1. 新增 `aletheia/risk/guards/` 目录。定义契约（对照 OpenAlice `guards/types.ts:12`）：
   ```python
   # guards/base.py
   class GuardContext:  # 只读，由 pipeline 一次性组装
       proposal: TradeProposal
       cfg: Config
       open_positions: list[dict]     # 预取，避免每个 guard 重复查库
       realized_pnl_today: float
   class Guard(Protocol):
       name: str
       def check(self, ctx: GuardContext) -> str | None: ...  # None=放行，str=violation token
   ```
2. 每条 check 抽成一个文件（`symbol_allowed.py` / `max_position_size.py` …），逻辑原样搬，返回原 violation token 字符串（**token 字面量必须逐字保留**——`strategy_lifecycle.py:299` 的 `CRITICAL_RISK_TOKENS` 子串匹配依赖它们，见 BACKEND_ISSUES §5.7）。
3. `guards/registry.py`：`GUARDS: list[Guard]`（有序，kill_switch 在首位短路）。
4. `evaluate_proposal` 保留 `_eval_lock`（`engine.py:64-72`，OpenAlice 没有这层、Aletheia 更严谨，必须留），内部改为：组装 ctx → `for g in GUARDS: if (v := g.check(ctx)): violations.append(v)`。
5. **保留两处副作用不动**：`max_daily_loss` guard 命中后 trip kill_switch（`engine.py` §8）、daily-loss 的 UTC 日重算逻辑（见 memory `aletheia-daily-loss-killswitch-utc`）——这些是有状态副作用，放在对应 guard 内或 pipeline 收尾，别丢。

**与 OpenAlice 的关键差异（别抄的点）**：
- OpenAlice cooldown guard 用进程内 `Map` 存上次时间（`cooldown.ts:9`）——Aletheia **限频/状态必须落 SQLite/JSONL**（memory `aletheia-self-heal-layer` 明确"限频须持久化"）。Aletheia 当前无 cooldown guard，若将来加，状态走持久层。
- 不引入"按账户动态配置 guard 列表"那套（OpenAlice 多账户才需要）——单人单账户，一个静态有序列表足矣，不过度工程。

**验证**：重构前后对同一批 proposal 跑 `evaluate_proposal`，断言 `violations` 集合逐字一致（snapshot 测试）；`tests/` 现有 risk 测试全绿。

### 蓝图 B（P0）— proposal commit_hash + state_after 快照 + reject 一等记录

**目标**：兑现 "Trading-as-Git" 名实相符——让每笔决策有内容指纹、可重放、拒绝可审计。对照 OpenAlice `TradingGit.ts:34-39/115-127/145-187`。

**施工路径（三步，可独立合入）**：
1. **commit_hash**（最小、先做）：`TradeProposal`（`models.py:64`）加字段 `commit_hash: str | None = None`。在 `submit_proposal` 构造 proposal 后、`insert_trade_proposal` 前计算：
   ```python
   commit_hash = sha256(canonical_json({
       "symbol","market_type","direction","order_type",
       "quantity","price","leverage","execution_mode","strategy_id","opportunity_id"
   })).hexdigest()[:8]
   ```
   注意：用 `decimal`/排序后的 canonical JSON，确保同一决策稳定出同一 hash。需配套 `migrations.py` 加列（参考现有迁移风格）。
2. **state_after 快照**：执行后（`_execute_proposal` 落 fill 处）把账户快照（持仓/现金/未实现盈亏）连同 `commit_hash` 写一条记录。**复用现有 JSONL audit 通道**（`gateway.py` 已是 audit 写入点之一），新增一个 `commit_log.jsonl`，每行 `{commit_hash, parent_hash, proposal_id, operations, result, state_after, ts}`——append-only，天然是 commit log。`parent_hash` = 上一条 commit 的 hash，形成链。
3. **reject 一等记录**：当前 risk 拒绝/用户拒绝可能只更新 `proposal.status`。改为：拒绝也往 `commit_log.jsonl` 写一条 `result: "rejected"` / `"user-rejected"` 的 commit（对照 `TradingGit.reject` `:145-187`）——拒绝不再是沉默的状态翻转，而是审计链上的一等事件。

**为何契合**：你的优先级 "State consistency > Safety > Auditability"——B 直接打 Auditability，且 state_after 快照让"任意历史时刻重建账户"成为可能（State consistency）。**不新增数据库/不引入 Postgres**，纯用已有 SQLite 列 + JSONL，符合"不扩张"。

**与 OpenAlice 别抄的点**：OpenAlice 有完整 staging area（`add` 入栈、`commit` 算 hash、`push` 才执行）——Aletheia 当前是"提案即可执行"的直流，**是否引入 staging（即把 commit 与 push 分离、给人审批留窗口）是更大的决策**，建议先只做 hash+快照+reject 三件（审计向，低风险），staging 分离留作后续单独评估（它会改执行时序，属 change-controlled 重活）。

### 蓝图 C（P1）— 金额统一 Decimal，不落 float

**现状风险**：`risk/engine.py:108` `notional = proposal.quantity * proposal.price` 用 float 算并与 `max_*_notional_usdt` 比较；`models.py` 的 `quantity/price/...` 全 `float`。浮点误差在边界值（刚好等于上限）可能导致 guard 误判。

**施工路径（渐进，别一次全改）**：
1. 先在**计算热点**收口：notional、drawdown、PnL 比较处用 `decimal.Decimal` 计算（入口 `Decimal(str(x))`，避免 `Decimal(float)` 引入二进制误差）。
2. 存储层暂可保留 float（SQLite 无原生 Decimal），但**比较/累加在 Decimal 域完成**，只在落库时转回。
3. 长期：金额字段统一走 `Decimal`-as-`str` 存储（对照 OpenAlice `git.ts:65-73` 的纪律），但这是大改，非 P1 范围。

**验证**：构造"刚好等于 notional 上限"的边界 proposal，断言 float 路径与 Decimal 路径的放行/拒绝判定一致或修正了已知偏差。

### 蓝图 D（P2）— 长任务进度回调 + WebSocket（参考 Prism，落 SQLite 版）

非信任项，有空再做。参考 Prism `services/{progress_hub,task_store}.py` 的 ~150 行实现，但 `TaskStore` 必须落 SQLite（Prism 是纯内存，重启即丢，`task_store.py:21`）。Aletheia agent 分析/回测是长任务，前端目前干等无反馈，这套能低成本补上实时进度条。

---

## 六、施工状态跟踪

| 蓝图 | 级别 | 模块 | change-controlled | 状态 |
|---|---|---|---|---|
| A — Guard 责任链 | P0 | risk/ | 是 ⚠️ | 📋 待授权 |
| B — commit_hash+快照+reject | P0 | execution/, storage/ | 是 ⚠️ | 📋 待授权 |
| C — Decimal 收口 | P1 | risk/, storage/ | 部分 | 📋 待授权 |
| D — 进度推送 | P2 | server/, agent_runtime/ | 否 | 💤 暂缓 |

> 动工任一 change-controlled 蓝图前：显式授权 → explore→plan → 焦点回归测试。完成后此表更新状态并记 commit。
