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
