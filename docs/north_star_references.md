# North-Star References — 方向地图，**非施工授权**

> ⚠️ **本文件是方向参考（北极星），不是 MVP 施工授权。**
>
> 它记录我们从同类系统里学到什么、以及我们真正缺什么——用于**未来**决策，
> 不构成"现在就去实现里面任何一项"的许可。遵循 `AGENTS.md`：实现指南是
> **目标 + 约束**文档，不是把每个未来想法都做出来的许可；优先补 MVP 现有缺口，
> 而非新增能力。本文件里的任何"可借鉴项"在动工前都需要单独的、显式的授权。
>
> **当前阶段红线**：系统尚未被证明能连续稳跑、也未被证明"学习在产出可验证假设
> 而非噪音"。在这两点被证明之前，本文件第 7 节列出的演进项一律**推迟**。

最后更新：2026-06-05 · 维护者：Aletheia Dev

---

## 0. 一句话结论

> 我们已经把交易侧的"形"（OpenAlice 那一类）基本做齐了——**本地优先、
> Trading-as-Git 式可追溯、多角色 agent、可见推理**。我们真正缺的、且 Hermes /
> ALMA / AlphaQuanter 共同指向的，是**同一个东西：经验闭环**——写进记忆的东西
> 要能反哺下一次决策。今天我们的学习机制是一本**只写不读的账本**。
>
> 这张地图把这一点钉死，但**明确推迟动工**，直到稳定性与学习质量被证明。

---

## 1. 六系统全景

| 系统 | 一句话定位 | 对我们的贡献 | 链接 |
|---|---|---|---|
| **OpenAlice** | 本地优先的通用 AI 交易 agent（"one-person Wall Street"） | 交易侧范式答案：local-first / Trading-as-Git / 凭证隔离 / guard pipeline | github.com/TraderAlice/OpenAlice |
| **Hermes Agent** | 自我改进的常驻 agent（skill from experience + 持久记忆） | 自学习侧范式答案：可检索记忆 + skill 注入 + 记忆反哺 | hermes-agent.nousresearch.com |
| **TradingAgents** | 多 agent 投研框架（分析师/交易员/风控 + 多空辩论） | 多角色 + 反方 + 交易前辩论 | github.com/TauricResearch/TradingAgents |
| **Sophia** | 持久数字生命体研究框架（叙事记忆/自我模型/长程适应） | 长程身份 + dreaming/consolidation + 自我解释 | arXiv:2512.18202 |
| **ALMA** | 让 agent 学习更好的记忆设计（meta-learning agentic memory） | 记忆不是死库；schema 可进化、影响未来检索 | arXiv:2602.07755 |
| **AlphaQuanter** | 工具编排式 agentic RL 交易框架 | 透明工具调用 + 从交易反馈优化策略 | arXiv:2510.14264 |
| **Nautilus-Practice** | 用户自有，基于 NautilusTrader 的"包装层"——回测/沙盒/实盘三模式 + 动态 Universe + LangGraph 多 agent 策略生成 | 工程侧范式答案：成熟撮合内核 + 三模式同心圆 + 资金费率熔断 + 用 agent **造策略**而非**做决策**（详见 §10） | github.com/iridite/nautilus-practice （本地克隆） |

> **诚实声明**：以下拆解全部基于各系统的**公开文档 / README / 论文摘要**。
> 营销表述与可核实机制分开标注；不替对方脑补未公开的内部实现。

---

## 2. 映射表 — 每个范式 vs Aletheia 现状

图例：✅ 已有 · 🟡 半成品 · ❌ 缺失。file 引用均来自本仓代码勘探，可 grep 复核。

| 范式贡献 | Aletheia 现状 | 依据 |
|---|---|---|
| local-first / 文件优先 | ✅ | SQLite + JSONL + Markdown，单进程 |
| Trading-as-Git 式可追溯 | ✅（形态不同） | audit 因果链 + `files/git.py` 版本化 `knowledge/playbooks/reports` |
| 凭证隔离 | ✅ | `config/secrets.py`（change-controlled），keyring，永不入 GET/日志/prompt |
| guard pipeline / 执行前检查 | ✅ | Risk Engine（`risk/`）+ 执行网关（`execution/`），硬限不可被 agent 改 |
| 进程 supervisor / 自愈 | ✅ | `_gateway_runner.py` supervisor + PR #7 的 OS watchdog |
| 多角色 agent | ✅ | **8 个已注册 agent**（见 `aletheia/agents/*.py`，`@register_agent`） |
| 反方 / 风险批评 | 🟡 | `risk_critic` **会写**风险教训，但**没人在决策前读**（见 §6） |
| 多空辩论 / 跨 agent 批评 | ❌ | agent 是独立定时任务，仅经共享 DB 单向写，无 inter-agent 数据流 |
| dreaming / 整理 | ✅ | `dreaming_agent` 每天 2am + daily_review + Learning Ledger（PR #8） |
| skill from experience | 🟡→❌ | playbooks 仅 `seed_playbooks` 种子写入，**0 个 agent 读**、不从经验生成 |
| 持久记忆**反哺决策** | ❌ | **核心缺口**：5 类知识只写、几乎不读回 LLM 决策（见 §6） |
| 可检索记忆（FTS / 向量 / 关键词） | ❌ | 仅 `list_knowledge_items(type, limit)`，无 FTS5、无 embedding |
| 记忆 schema 进化 | ❌（固定） | Pydantic 模型 + 加列式迁移，无运行时进化 |
| 从交易反馈优化策略（RL 式） | ❌ | 晋级门槛会算指标，但不回流改策略 |
| 长程身份 / 自我模型 | ❌ | 无（属研究级，远期） |

---

## 3. 深度拆解：OpenAlice

### 3.1 机制（公开材料可核实）

- **双进程 + supervisor 架构**：
  - **Alice**（agent 运行时）：AI provider 路由、ToolCenter 工具注册、append-only JSONL inbox、cron/heartbeat 调度、市场数据/分析/新闻模块。
  - **UTA**（持凭证的执行载体）：实现各 broker（CCXT / Alpaca / IBKR / Longbridge / Mock），内含 Trading-as-Git 状态机、guard pipeline、快照调度；**仅绑 `127.0.0.1`**。
  - **Guardian**（supervisor）：编排启动、健康门控、重生。
- **Trading-as-Git（TaG）**：`stage 订单 → commit（带 reasoning 消息）→ push 执行`；push 时跑 guard → 派发 broker → 抓账户快照 → 记一条带 8 位 hash 的 commit；历史可像 `git log/show` 回看。commit 消息含真实推理（例："Defensive trim from 44.8% to ~40% as BTC tested SMA20…"）。**注意**：博客与 README**未公开**订单对象模型 / 序列化格式 / diff 表示；博客自承"概念清晰、机制欠详"。
- **存储**：`data/config/*.json`（engine/accounts/ai-provider，Zod 校验）、`data/brain/persona|heartbeat.md`（用户覆盖层，gitignore）、`~/.openalice/workspaces/<id>/`（每工作区一个目录 + git repo）、inbox 为 append-only JSONL。
- **Guard pipeline**："ESLint for trading"——执行前在 UTA 内跑：max position、trade cooldown、symbol whitelist。
- **凭证隔离**：broker 凭证只在 UTA 进程，Alice 永不接触。
- **栈**：Node/TS 为主（~81%）、Python 辅（~17%）；pnpm monorepo + Turborepo；委托 Claude Code / Codex CLI 做 workspace 执行。

### 3.2 我们的现状对应

| OpenAlice | Aletheia 等价物 | 评价 |
|---|---|---|
| Trading-as-Git commit/push | audit 因果链（proposal→risk→order→fill→position，保留原始因果 ID）+ `files/git.py` 版本化 | 我们的可追溯**更强**（结构化、不可篡改因果），但没有 OpenAlice 那种"commit message = 人类可读推理"的单一叙事载体 |
| Guard pipeline | Risk Engine（`risk/`，change-controlled）+ 执行网关 | 我们的**更硬**（agent 不可改硬限） |
| UTA 凭证隔离 | `config/secrets.py` + keyring | 等价；我们是单进程内的边界，OpenAlice 是独立进程（v1 同机） |
| Guardian supervisor | `_gateway_runner.py` + PR #7 watchdog | 等价 |
| persona/heartbeat 双层 | 无显式 persona 层（我们用 permission_mode + 配置） | 差异，非缺口 |

### 3.3 差距

- **没有"commit message 即推理叙事"的单一可读载体**：我们的推理散在 agent_runs / rationale / 知识条目里，缺一个像 OpenAlice commit 那样"一眼看懂这步为什么做"的线性叙事。
- **没有进程级凭证物理隔离**（我们是同进程逻辑边界）——对单用户本地系统**不是真问题**。

### 3.4 可借鉴项（每条标注治理红线）

| 借鉴点 | 价值 | 治理红线 |
|---|---|---|
| **"推理叙事"载体**：让每个交易/调仓动作附一段人类可读的"为什么"，可线性回看 | 提升可复盘性（用户最看重的） | ✅ 安全——纯记录，不触碰执行/风控 |
| **TaG 的 stage→commit→push 三段心智模型** | 给 paper/live 一个统一的"先暂存、再确认、才执行"语义 | 🟡 我们已有 proposal→approve→execute，**勿重复造轮子**；最多借它的命名/心智 |
| **Evolution mode（给 agent bash 自改代码权）** | —— | 🔴 **绝不引入**。直接违反 AGENTS.md："agent 不可编辑项目指令给自己加权"、不可改执行/风控/凭证 |

### 3.5 为什么现在先别做

OpenAlice 的交易侧"形"我们**基本已经做齐**，再投入只是"确认我们做对了"，边际价值低。
唯一值得的"推理叙事载体"也属锦上添花，应排在"稳跑验证"之后。

---

## 4. 深度拆解：Hermes Agent

### 4.1 机制（公开材料可核实）

- **核心循环 `AIAgent`**（`run_agent.py`）：`输入 → prompt_builder 构造三层 prompt → API（chat_completions / codex_responses / anthropic_messages 三选一）→ 工具分发 → 循环至完成`。
- **三层 prompt 组装**（`agent/prompt_builder.py`）：
  - **stable 层**：身份、工具 schema、**skill 指引**（走 Anthropic cache 断点）。
  - **context 层**：文件附件、**skill context**。
  - **volatile 层**：**memory 块**、profile、时间戳。
- **记忆**：SQLite + **FTS5 全文检索**（`hermes_state.py`）；会话血缘（压缩后 parent/child）；`context_compressor.py` 超阈值做**有损摘要**并存压缩会话。
- **Skills**：遵循 **agentskills.io 开放标准**（与 Claude Code skills 同族——带 frontmatter 的 markdown），经 Skills Hub 存取/分享/社区贡献；作为 context 注入 prompt。cron 任务起"**全新无历史的 AIAgent + 注入 skill**"。
- **插件**：memory providers（`plugins/memory/`）与 context engines（`plugins/context_engine/`）均为**单选**插件；Honcho 做对话式（dialectic）用户建模。
- **6 种终端后端**（local/Docker/SSH/Daytona/Modal/Singularity）+ 20+ 消息平台。

> **声称 vs 已证实**：官方称其有"closed learning loop——agent 自管记忆 + 周期 nudge +
> 自主造 skill + 用中自我改进"。**架构页未公开**触发条件、nudge 频率、记忆筛选算法、
> skill 自生成的具体实现。FTS5 记忆 / 三层注入 / skill=markdown 是**已证实**的；
> "自我改进闭环的算法"目前属**营销层**，要借鉴必须我们自己设计并验证。

### 4.2 我们的现状对应

| Hermes | Aletheia 等价物 | 评价 |
|---|---|---|
| skills = 注入的 markdown，从经验生成 | **playbooks**（`trading/playbooks.py`） | 🟡 我们有 markdown playbooks，但**仅种子、0 agent 读、不从经验生成**——形似神不至 |
| FTS5 可检索记忆 | `knowledge_items`（SQLite + Markdown） | ❌ 我们**无任何检索原语**（无 FTS5/向量/关键词），只有 `list(type, limit)` |
| volatile 层注入 memory 块到决策 prompt | agent 的 LLM prompt | ❌ **核心缺口**：我们 agent 决策时几乎不注入历史记忆（见 §6） |
| context 压缩 + 血缘 | 无（我们靠 retention 裁剪 + JSONL 轮转） | 差异，非当务之急 |
| Honcho 用户建模 | 无 | 单用户本地系统，价值有限 |

### 4.3 差距

- **无可检索记忆层**：要让记忆反哺，先得"能按相关性找到该读哪条"。我们连关键词搜索都没有。
- **skill/playbook 不闭环**：playbooks 是死资产；Hermes 的 skill 至少会被注入决策。
- **决策 prompt 不带历史**：这是与 Hermes 最本质的差距，也正是 §6 的主题。

### 4.4 可借鉴项（每条标注治理红线）

| 借鉴点 | 价值 | 治理红线 |
|---|---|---|
| **可检索记忆（先上 SQLite FTS5，不必向量）** | 让"读回相关经验"成为可能；轻量、本地、无新重依赖 | ✅ 安全——只读检索层。**勿过早上 embedding/向量库**（违反 #1 不做重） |
| **决策 prompt 注入相关知识**（market_observer/strategy_designer 读回 risk lesson、过往复盘） | 真正闭合经验闭环 | 🟡 注入内容**必须先经"学习账本证明是可验证假设而非噪音"**；注入的是"建议/教训"，**不得**让其改变硬限或解锁 live |
| **playbook 激活**：让 design/observer agent 真正读 playbook | 复活死资产 | ✅ 安全（playbook 是观察框架，非硬规则） |
| **skill 自生成 / 自我改进** | Hermes 的招牌 | 🔴 **交易系统里高危**：skill 自生成绝不能长出"解锁 live / 改风控 / 改凭证"的能力；且"closed loop"是营销、需我们自设计验证门。**最多**做"人审后才生效的 playbook 草案" |
| context 压缩 + 血缘 | 长程运行省 token | 🟡 远期；当前 retention 够用 |

### 4.5 为什么现在先别做

Hermes 指向的闭环是我们的真缺口，但**正因为它会放大一切**——把还没验证的噪音
固化成"经验"——所以必须先有"学习账本证明产出的是可验证假设"，再动。且 Hermes 的
"自我改进"算法本身未公开、属营销层，照搬无据。**先稳跑、先证学习质量，再闭环。**

---

## 5. 其余四系统（点到，深拆见未来）

- **TradingAgents**（多角色 + 多空辩论 + 风险团队）：我们已有 8 角色含 risk_critic（反方），
  **但缺"交易前辩论 / 跨 agent 批评"的结构化数据流**。可借鉴：让 risk_critic 的输出在
  paper_trader 决策前被读到（这其实又回到经验闭环）。红线：辩论不得绕过 Risk Engine。
- **Sophia**（叙事记忆 / 自我模型 / 长程适应 / System-3）：研究级，**远期**。dreaming 已是雏形。
- **ALMA**（记忆 schema 可进化）：研究级。与我们"固定 Pydantic schema"正相反——
  **明确不在近期**（schema 自进化在交易系统里是稳定性与可审计性的高风险项）。
- **AlphaQuanter**（工具编排 RL + 从反馈优化）：透明工具调用我们已有（`log_tool`）；
  "从反馈优化策略"= 经验闭环的强化学习版，属远期。

---

## 6. 核心综合 — 经验闭环：现在是"开环"

这是整张地图的脊梁，用代码事实说话：

**我们写得很勤：**
- `risk_critic` 写"风险教训"（`agents/risk_critic.py` → `create_research_note`）。
- `post_trade_reviewer` 写复盘（`post_trade_review`）。
- `dreaming_agent` 写每日整理 + Learning Ledger（PR #8）。
- 5 类知识双存储（SQLite `knowledge_items` + Markdown），齐全。

**但几乎不读回决策：**
- `paper_trader`：纯规则，`llm_complete` 是 **dead import**（`agents/paper_trader.py`）。
- `market_observer`：LLM 只看 `latest_snapshot`，**不读**任何历史/教训（`agents/market_observer.py`）。
- `strategy_designer` / `research_agent`：通用 prompt，无过往模式注入。
- `dreaming_agent`：只 **数** 知识条数（`list_knowledge_items(t, limit=1000)` 取 `len`），**不读内容**。
- `create_strategy_memory`（`knowledge/manager.py:75`）**从未被任何 agent 调用**——定义了却不用。
- playbooks：仅 `seed_playbooks` 种子，**0 个 agent 读**（`grep playbook aletheia/agents/` 空）。
- **无任何检索原语**：无 FTS5 / 向量 / 关键词搜索。

**一个诚实的例外（不能overclaim）**：`strategy_lifecycle.py:154` 在晋级门槛里**会读**
`post_trade_review` 的**条数**作为 gate。所以严格说不是 100% 开环——而是
**"读来计数/门控，从不读进 LLM 决策 prompt"**。经验影响不了下一笔交易的判断。

→ 这正是 **Hermes（记忆注入决策）/ ALMA（记忆影响检索）/ AlphaQuanter（从反馈优化）**
共同指向的同一缺口。**它是我们与"会学习的生命体"之间最本质、也是唯一的结构性差距。**

---

## 7. 排序（北极星，**非施工图**）

```
阶段 0（现在）：稳跑验证
  └─ PR #7（开机自启+watchdog）+ PR #8（假活检测）真实跑 7–14 天，不崩/不乱单/不丢状态。

阶段 1：证明学习质量
  └─ 用 Learning Ledger + 复盘，证明产出的是"可验证假设"而非噪音。
     若产出是噪音 → 先修产出质量，绝不进入闭环（否则把噪音固化成"经验"）。

阶段 2（仅在 0、1 都通过后，且需显式授权）：最小经验闭环
  ├─ 先上轻量可检索记忆（SQLite FTS5，不上向量）。
  ├─ 决策 prompt 注入"相关教训/复盘"（market_observer / strategy_designer 先行）。
  ├─ 激活 playbooks（design/observer 真正读）。
  └─ 硬约束：只读回、不自改 schema、注入内容不得改硬限/解锁 live；
     任何"自生成 skill"最多做成"人审后生效的草案"。

阶段 3+（远期研究）：context 压缩血缘 / 跨 agent 辩论 / RL 反馈 / schema 进化 / 自我模型。
```

---

## 8. 治理红线（贯穿全文）

- 本文件不构成实现授权；每个借鉴项动工前需单独显式授权。
- 🔴 永不引入：agent 自改代码权（OpenAlice Evolution mode）、agent 自生成可解锁 live /
  改风控 / 改凭证的 skill、运行时自进化的知识 schema。
- 🟡 闭环类改动必须：先证学习质量、只读回、过 hard-control、注入"建议"而非"指令"。
- 区分"声称 vs 已证实"——Hermes 的"closed learning loop 算法"等属营销层，照搬无据。
- 最终目标（用户原话）：**一个本地运行、会交易、会复盘、会学习、会积累自我经验、
  但被 hard control 牢牢限制住的个人交易数字孪生**——先当稳定的实习生，再当交易员。

> **代码层结构性问题清单**：与本文件的"范式差距"性质不同，已迁出至
> `docs/BACKEND_ISSUES.md`。本文件保持为"方向地图"，不承载具体 bug/contract
> 偏移条目；后者随代码状态变化频繁，应单独维护。

---

## 10. 深度拆解：Nautilus-Practice（用户自有参考系）

> 与 §3–§5 不同：本节参考系是**用户本人的另一个仓库**（基于 NautilusTrader 1.223
> 的包装层），所以它不是"我们能参考它的什么"，而是"我们能从**自己另一处的工程
> 选择**里学到什么"。本地克隆于 `C:\Users\Administrator\Projects\nautilus-practice`，
> 最后查看：2026-06-06。
>
> 本节同样**不构成施工授权**——任何借鉴动工前需显式授权。

### 10.1 它是什么（公开材料 + 代码可核实）

**定位**：一个**严肃的量化包装层**——把 NautilusTrader（Rust + Python 撮合内核）
封装成"配置驱动 + 三模式同心圆 + 多 agent 策略生成"的开发平台。代码量级远超
Aletheia：keltner_rs_breakout 单策略 1071 行，engine_high.py 2060 行，langgraph 子目录
完整 DDD 分层（application/domain/infrastructure/presentation）。

**核心组件**（按目录）：

| 目录 | 职责 | 关键代码 |
|---|---|---|
| `strategy/` | 4 个完整策略 + 模块化 indicators/signals/universe 复用层 | `core/base.py:82` `BaseStrategy(Strategy)` 继承 NautilusTrader `Strategy` |
| `backtest/` | 高/低双引擎；高级引擎用 Parquet catalog + `BacktestNode`，低级用 `add_data()` 直 API | `engine_high.py:2060 行`、`engine_low.py:38.9K` |
| `sandbox/` | 测试网/模拟盘连接器（OKX 为主），有完整 preflight 检查 | `sandbox/engine.py:1067 行`、`sandbox/preflight.py:401 行` |
| `live/` | 实盘 OKX + Binance；`circuit_breaker.py` 是**资金费率熔断**——年化 >100% 拒、>50% 切现货 | `live/engine.py:408 行`、`live/circuit_breaker.py:229 行` |
| `core/` | 配置三层架构：YAML → Pydantic → ConfigAdapter；`adapter.py` 单文件 31KB | `core/adapter.py`、`core/schemas.py:28KB`、`core/exceptions.py:15KB` |
| `langgraph/` | **多 agent 策略生成系统**——LangGraph 编排 Coordinator/Researcher/Validator/Optimizer | `infrastructure/graph/research_graph.py:250 行`、`optimize_graph.py:328 行` |
| `config/` | active.yaml 选择器 + strategies/ + environments/（dev/sandbox/live） | YAML 驱动一切 |
| `tests/` | 127 个测试（langgraph 子模块）+ 项目级 unittest | 100% 通过 |

**关键工程选择**：

- **NautilusTrader 作内核**：撮合/订单/风控数据结构都来自上游；自己写的是
  *策略 + 适配 + 配置 + agent 编排*。**没有自己写撮合或 risk engine**。
- **三模式同心圆**：backtest（本地）→ sandbox（交易所测试网）→ live（实盘），
  共享 90% 配置（`extends: "base.yaml"`）。这与 OpenAlice "v1 同机双进程"是另一种
  解法——**用配置层抽象代替进程隔离**。
- **资金费率熔断**：`live/circuit_breaker.py:96` 入场前查资金费率，>100% 年化直接
  拒、50%–100% 切现货、<50% 走永续；典型的"在执行前过 guard"模式，但**触发条件
  是市场指标而非账户指标**——值得对照我们的 risk engine。
- **LangGraph 多 agent 策略生成**：`langgraph/infrastructure/graph/research_graph.py`
  跑 `Coordinator → Researcher（生成代码）→ Validator（评分）→ 回环`，**输出是一段
  Python 策略源码字符串**，落到 SQLAlchemy DB 里。
- **DDD 分层**：langgraph 子项目里 application（用例）/ domain（模型 + 仓储接口）/
  infrastructure（实现：LLM、graph、agents、code_gen、cache、checkpoint）/
  presentation（CLI），interfaces 通过抽象类强解耦。

### 10.2 与 Aletheia 的工程选择对照

图例：✅ 我们更优 · 🟰 等价不同形 · 🟡 它更成熟 · ❌ 我们没做

| 维度 | Nautilus-Practice 做法 | Aletheia 做法 | 评价 |
|---|---|---|---|
| **撮合内核** | NautilusTrader 1.223 (Rust+Python) — 业界级 | 自写 paper_broker + Bitget 适配 | 🟡 它更成熟，但我们规模小到自写没事；上 NautilusTrader 等于换轮子 |
| **回测严肃度** | Parquet catalog + 13 项性能指标 + Sharpe/Sortino/Calmar/Drawdown 自动计算 | 暂无显式回测；仅 paper trading 模拟 | ❌ 我们没"严肃回测"——promotion gate 是用 paper 实盘累积的（trust §5），周期长 |
| **环境同心圆** | base.yaml + dev/sandbox/live 三 extends 文件，配置 90% 共用 | live_state + exchange_mode + permission_mode 三个标志位组合 | 🟰 等价不同形；他们靠配置文件继承，我们靠运行时门控 |
| **入场前 guard** | `circuit_breaker.py:96` 在策略 `on_bar` 内调用 → 资金费率检查 → 切现货/拒/走永续 | Risk Engine 在 gateway 层；Engine 检查的是账户/订单约束，**不查市场指标** | 🟡 他们多一层"市场状态熔断"；我们的 Risk Engine 偏账户安全。借鉴价值真实但**不属于 trust 边界扩展**——属于策略层（playbooks 的活） |
| **策略产出方式** | LangGraph 编排 4 个 agent → 输出 Python 源码字符串 → 落 SQLAlchemy → 用回测引擎跑分 | 7 个 agent 各自跑 cron，写 markdown/research_note；**不产出代码**，只产出"hypothesis 文字" | 🟰 完全不同的设计哲学。他们是"AI 写策略代码"，我们是"AI 写交易决策"。各有风险 |
| **agent 角色拆分** | Coordinator → Researcher → Validator → Optimizer，路由由 `_route_after_coordinator` 状态机决定（`research_graph.py:166`） | 7 agent 都是独立 cron job，单向写库，**无 inter-agent 数据流** | 🟡 他们有真正的 agent 协作（state-driven 路由）；我们 §6 已点出这是缺口 |
| **凭据隔离** | `.env` + `dotenv` + 长度校验（`sandbox/engine.py:362`）；OKX 凭据 passphrase 单独字段 | keyring → fernet fallback；写入只读，永不出现在 GET/log/prompt | ✅ 我们更硬 |
| **配置驱动度** | YAML active 选择器 + Pydantic schema 校验 + adapter.py 31KB 适配 | TOML/YAML + Pydantic，但适配代码薄（`config/loader.py:12KB`） | 🟡 他们的配置抽象层更厚——好处是策略热替换，坏处是 31KB 单文件维护成本 |
| **依赖外部 LLM** | LangGraph + Anthropic API（claude_client.py） + LLMCache（pickle） + Checkpoint 恢复 | 多 provider router（OpenAI/Anthropic/Codex CLI），无 cache 无 checkpoint | 🟡 他们做了 LLM 工程化（cache/checkpoint）；我们 §9 BACKEND_ISSUES 也点过 healthcheck 真实 authed call 等问题 |
| **可观测性** | TUI 进度条 + 13 项指标自动报告 + 过滤器诊断系统（统计每过滤器拦截率） | dashboard 显示状态 + audit JSONL；无策略级"过滤器命中率" | 🟡 他们的"过滤器命中率"诊断对**Aletheia 的 risk_critic 可借鉴**（risk_critic 现在写文字教训，可以改成统计 risk 拒绝原因分布） |
| **测试** | 127 个 langgraph 测试 + 项目级 unittest | 272 个 pytest（trust + 安全 + 接受度） | ✅ 我们覆盖度更广，他们 langgraph 模块覆盖率仅 12% |
| **撞名 / 包冲突** | langgraph 子目录与外部 langgraph 包冲突，需 sys.path 黑魔法绕开 | 无此类问题 | ✅ 我们没踩这个坑 |

### 10.3 三个值得诚实学习的点

**① 资金费率熔断（market-state guard）—— 借鉴价值真实但要分层落位**

Nautilus-Practice 的 `circuit_breaker.py` 在策略层做"入场前查市场状态"，决策结构：

```python
if funding_rate_annual > 100%:  return REJECT
elif funding_rate_annual > 50%:  return SPOT  # 切现货
else:                            return PERP
```

**对 Aletheia 的启示**：我们的 `risk/engine.py` 是**账户/订单**层风控（trust §6.3
"do not weaken or remove a risk check"），**不该**在这里加市场状态依赖
（违反单一职责 + 增加外部数据依赖让 risk engine 变脆弱）。

正确落位应该是：**playbooks 层 + market_observer 的 thesis**——用 playbook 描述
"funding 极端时回避永续"，让 market_observer / strategy_designer 在产出 opportunity
前考虑这个状态。这与 §6 已点的"playbooks 0 reader"问题正面相关。

**治理红线**：✅ 安全——只要不进 risk engine、只在策略层做。🔴 红线：绝不能让
agent **绕过** risk engine 自行加入"funding 太高所以下单/撤单"的捷径。

**② LangGraph 编排作为"策略代码自动化生产线"—— 红线最重的一项**

Nautilus-Practice 的 `research_graph.py:62-89` 用 StateGraph 真实跑：

```text
coordinator (entry) -> researcher 写代码 -> validator 评分 -> 回 coordinator -> ...
                       ↓ 验证通过则 -> END
```

研究员调用 `StrategyGenerationPrompt` 让 LLM 输出 JSON，含 `code` 字段（Python 源码），
再由 validator 跑 `ast.parse` + 关键字检查（`strategy_generator.py:136`），
通过则保存到 SQLAlchemy。

**对 Aletheia 的启示**：

- 🟢 **可借鉴的形**：state-driven agent 编排让多 agent 真有"协作流"——这正是 §6
  "agent 是独立 cron 单向写库"的反例。**但不必上 LangGraph**：我们的 `orchestrator.py`
  扩展一个"工作流定义"小层即可（`coordinator -> researcher -> validator` 三个 agent
  按状态轮转），不需要引重依赖。
- 🔴 **绝不借鉴的实**：Nautilus-Practice 让 agent **生成可执行 Python 源码**
  落 DB 然后回测——这在交易系统里是 AGENTS.md "agent 自改代码权"的同位违规：
  - LangGraph 那边的"安全"是因为生成的代码只跑回测、不接 live；
  - 我们的 trust §6.1 把 risk/、execution/、credentials 列为 change-controlled——
    任何 agent 产出的可执行内容**不得**到达这些路径；
  - 即使是隔离的回测沙盒，"agent 生成代码 → 自动评分 → 自动晋级 → 接 live"是 Sophia/AlphaQuanter 警告的远期闭环风险，**比 Hermes "skill 自生成"更激进**。

**最大可借鉴**：把策略**参数（数值/枚举）**让 agent 优化（Optimizer 角色，
受限搜索空间），**绝不**让 agent 写"代码"。这与 §7 阶段 2 "playbook 草案需人审"
精神一致。

**③ DDD 分层与抽象——可借鉴但要克制**

`langgraph/application/interfaces/llm_service.py` 这种"接口与实现分离"的写法
对**测试性**有帮助（`tests/` 里 mock LLMService 即可），但 Aletheia 当前
代码量级（aletheia/ 目录约 50 个 Python 文件）远未到需要 DDD 的规模——
trust §2.4 "Make the smallest change that satisfies"。

**最大可借鉴**：只在 `model_provider/` 加 `interfaces.py`（已经隐性有 base.py），
让 agent 单元测试不依赖真实 LLM 网络调用——这与 BACKEND_ISSUES §2.7
"provider 不一致的 error 形状"正面相关。

### 10.4 同时也是反面教材的几点

诚实说法：Nautilus-Practice 也有问题，避免我们照搬。

| 反面教训 | 代码事实 | 对 Aletheia 的警示 |
|---|---|---|
| **包名冲突 hack** | `langgraph/IMPLEMENTATION_SUMMARY.md:131-148` 描述 `sys.path` 黑魔法绕开包冲突；至今未重命名目录 | 不要用与 PyPI 包同名的子目录命名（我们目前安全） |
| **31KB 单文件 adapter** | `core/adapter.py:31416 字节` 单文件——配置驱动度高 = 适配代码巨型化 | 我们 `config/loader.py` 才 12KB，别因"配置抽象"膨胀到不可维护 |
| **strategy_generator 是占位** | `langgraph/.../strategy_generator.py:42-50` 拼字符串生成 backtesting.py 风格代码（不是 NautilusTrader），看起来像未完成的 demo | "agent 写代码"在他们那里也只是雏形——别以为它"已经成熟可借鉴" |
| **CLAUDE.md 自陈"AI 两次直接在 main 分支提交"** | `CLAUDE.md:149-155` 工程纪律失败案例 | 我们的 AGENTS.md 红线（`Do not amend commits unless explicitly asked`）必须严守 |
| **测试覆盖率 12%（langgraph 模块）** | `IMPLEMENTATION_SUMMARY.md:117` "代码覆盖率: 12.02%" | 我们 272 测试覆盖广得多——别为了"加 agent 编排"牺牲覆盖度 |

### 10.5 排序更新（接 §7 北极星阶段图）

```
阶段 0（不变）：稳跑验证
阶段 1（不变）：证明学习质量
阶段 2（更新）：最小经验闭环
  └─ 沿用 §7，但额外参考 Nautilus-Practice 的：
     ├─ playbook 激活策略：把 funding/regime 等市场状态做成 playbook 输入
     │  （而非进 risk engine）
     ├─ agent state-driven 编排：让 market_observer → strategy_designer →
     │  risk_critic 形成有方向的工作流，而非各自 cron。**不引入 LangGraph**，
     │  在 orchestrator.py 里加个 100 行的 workflow 状态机即可
     └─ 把 risk_critic 改成"统计型"：参考 Nautilus-Practice 过滤器诊断系统，
        让它输出"过去 N 天 risk 拒绝原因 top-5"等量化报告，而非纯文字教训
阶段 3+（更新）：远期研究（已添加）
  └─ "agent 调参（数值搜索空间）" — 可借鉴 OptimizerAgent 形式但**绝不**到代码生成
  └─ "回测严肃化" — 远期可考虑引入 vectorbt / NautilusTrader backtest，
     但这是大改、属于推迟队列；当前 paper trading 路径继续是真值来源
```

### 10.6 治理红线（Nautilus-Practice 专项）

- 🔴 **永不**：让 agent 生成可执行 Python 代码并落到 strategy/ 目录或 DB（即使有
  validator 评分，trust §6.1 change-controlled 边界禁止）。
- 🔴 **永不**：把 `circuit_breaker.py` 这类 market-state 检查放进 `risk/engine.py`——
  增加 risk engine 的外部数据依赖等于削弱它的可审计性。
- 🟡 **慎重**：DDD 抽象只在测试性收益明显的边界引入（model_provider），不要扩散
  到 storage/ 或 risk/。
- 🟡 **慎重**：LangGraph 的"workflow 编排"范式可借鉴，但**不引入它本身**——
  我们已有 orchestrator + scheduler 两层，再加一层重依赖违反 AGENTS.md
  "No Docker/Postgres/Redis/queues" 的精神延伸（不引入复杂 runtime）。

### 10.7 一句话结论

> Nautilus-Practice 在**工程严肃性**（撮合内核、回测引擎、配置抽象）上比 Aletheia
> 走得更远；在**自动化策略生产**（agent 写代码）上比 Aletheia 更激进，且这部分恰是
> Aletheia 的红线区。**形可借（agent 工作流编排、playbook 状态依赖、统计型 risk 复盘）**；
> **实不可借（agent 生成可执行代码、market-state 进 risk engine、DDD 全局扩散）**。
> 它最大的价值是**自我对照**——证明同一个开发者在另一个项目里选了不同抽象路径，
> 让我们更清楚 Aletheia 的"local-first + change-controlled + 不写撮合"是有意识的克制。
