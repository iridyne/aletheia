# OpenAlice Trading-as-Git (TaG) 深度研究报告

> **日期**：2026-06-29
> **触发**：owner 让我研究 https://www.openalice.ai/blog/trading-as-git-intro
> **方法**：13 agent 工作流（三层 ground truth + 5 镜头分析 + 5 轮对抗核查）
> **三层地面真相**：博客原文 / 本地 `_refs/OpenAlice` 完整 TypeScript 源码（`services/uta/src/domain/trading/git/TradingGit.ts` 等）/ 本项目 Aletheia 自身的实现对照
> **核查纪律**：博客与源码冲突时一律以源码为准；任何无源码佐证的"代码事实"都被对抗 agent 标红剔除
> **关联文档**：`docs/references/external-systems/openalice_prism_review.md`（更详细的源码深拆）、`docs/references/INDEX.md §4`（红线/借鉴清单）

---

## 一句话定性

**TaG 是一个工程上扎实的"AI 交易日志系统"，被包装成"版本控制系统"对外宣传。日志系统本身值得借鉴；"Trading as Git" 这个等价比附经不起代码核对——核心 git 操作（revert / branch / merge / Merkle 链）在交易场景下要么物理上不可能、要么代码里压根没实现。**

---

## 一、TaG 的真实身份：三步操作 + 单链审计 + AI/人硬边界

### 1.1 普通人能懂的类比

外科医生做手术：① 写好今天要做的操作清单（暂存）② 在知情同意书上写清楚"为什么这么做"（提交说明 + 哈希）③ 真正动刀（推送执行，并自动拍一张"手术后病人状态"照片归档）。被取消的手术也归档——"曾经打算做但没做"这件事本身也是病历的一部分。

### 1.2 三步操作（以代码行为为准）

| 步骤 | 代码方法 | 实际行为 | 是否落盘 |
|---|---|---|---|
| **stage / add** | `TradingGit.add(op)` | 把意图推进内存 `stagingArea: Operation[]` | ❌ 纯内存，宕机即丢 |
| **commit** | `TradingGit.commit(message)` | 生成临时哈希 + 锁定 message；**不发单** | ❌ |
| **push** | `TradingGit.push()` | 依次调 `executeOperation(op)` → broker；抓 `stateAfter`；写链；持久化 | ✅ 全量覆盖写 `commit.json` |
| **reject** | `TradingGit.reject()` | 与 push 对称，但不调 broker；result 全部 `'user-rejected'`，照样入链 | ✅ |
| **sync** | `TradingGit.sync()` | 把 broker 报告的订单状态变化写入 `[sync]` commit（等价 `git pull`） | ✅ |
| **recordReconcile** | `TradingGit.recordReconcile()` | 余额漂移合成提交，**绕过 staging/push/guard** 直接入链 | ✅ |

> ⚠️ 博客 intro 帖（WebFetch 转述）用 "Add"，UTA 帖 + GitHub README 用 "Stage"——这个措辞差异**不能完全确认**是博客原文差异还是 WebFetch 转述不一致。代码方法名是 `add()`，但语义对应"Stage"。

### 1.3 信任边界（这是 TaG 设计中最有原创性的部分）

`routes-trading.ts` 注释明确：

> `"manual approval — the AI tool is hollowed out, only humans can push"`

即 Alice AI **只能调 `stage-*` 路由**，把意图放进购物车；`/push` 路由必须由人工触发。这是 API 路由层的设计约束（不是不可绕过的代码硬锁），但意图清晰：AI 提议、人工放行。

---

## 二、博客 vs 代码：四处营销超出代码事实

| # | 博客口径 | 代码事实 | 评级 |
|---|---|---|---|
| 1 | "版本控制系统" 暗示 git 全部能力 | 实际只有线性单链：`private commits: GitCommit[]` + `head` 单指针，**无 branch、无 merge、无 DAG** | **过度比附**（博客没明说 branches，但"version control"四字带来的联想严重 over-claim） |
| 2 | "records a commit with an 8-char hash" 暗示 git 同构 | `SHA-256({message,operations,timestamp,parentHash}).slice(0,8)`，是**内容哈希截断**，不是 Merkle 树；与真 `git` 完全不互通 | **基本真实，细节有差距**。8 字符 hex 碰撞空间 ≈ 43 亿（不是 4 亿）；高频场景下不是理论极限 |
| 3 | `"Better context leads to higher quality reasoning. That's why TaG works."` | **循环论证**：把推理写进 commit message 只解决"可见性"，不解决"推理质量"。没有对照组、没有数据 | **未证实假设** |
| 4 | `"Alice has shown remarkable ability to anticipate severe downturns in both the October 2025 and January 2026 crashes"` ＋ `"We ran a backtest covering January 1-31, 2026"` | **逻辑矛盾**：回测是事后用历史价格重跑；在已知崩盘前价格走势的情况下"预判"崩盘不构成预测能力。仓位变化 44.8% → ~40% 是常规波动幅度（~10.7%），无对照组、无统计检验、无原始数据 | **典型回测幸存者偏差叙事，零证据值** |

### 关于"1 月崩盘预判"的更深一层

博客在同一段里把 "Since deploying TaG" 与 "We ran a backtest" 并列，造成读者以为有实盘证据。如果 TaG 自身的 `MaxPositionSizeGuard` 在账户波动加大时机械触发减仓——这是机械行为，与 "AI 预判崩盘" 在语义上完全不同。博客把工程层面的机械保护，包装成了 AI 的"洞察力"。

---

## 三、设计真正兑现的（值得致敬的工程纪律）

代码层有实现，不是口号：

1. **AI / 人硬边界**（API 路由设计）：AI 工具只暴露 stage-* 端点，push 必须人工触发。
2. **reject 是一等公民**：被否的意图与执行成功用**同结构**（`GitCommit` + hash + parentHash）写同一条链。这比"只记录成功"的传统系统有架构优势。
3. **执行后账户全量快照（`stateAfter: GitState`）**绑到每条 commit 上：`{ netLiquidation, totalCashValue, unrealizedPnL, realizedPnL, positions, pendingOrders }`。任何历史时刻可直接读快照，不用从 PnL 曲线反推。
4. **Guard pipeline 在 dispatch 前实时执行**：每次 push 实时拉 `broker.getPositions()` 和 `broker.getAccount()`，不依赖缓存；三个内置 guard（`MaxPositionSizeGuard / CooldownGuard / SymbolWhitelistGuard`）独立可配置可注册。
5. **UTA 与 broker 解耦**：TaG 语义在 UTA 层，broker 是可替换适配器。博客原话 `"TaG doesn't need to be designed at the broker level"` 在代码中得到验证。

---

## 四、Git 隐喻的硬边界（用代码证明的失效点）

### 4.1 成交无 revert——最核心的比喻裂缝

`Operation` 联合类型里**没有** `undoCommit` / `rollback` 操作。代码无 `revert()` 方法。物理上：成交后只能反向平仓（再付手续费、再吃滑点、再面对新价格）。Git 最有价值的"撤销"在交易领域物理不可能。

### 4.2 链是线性单链，并发场景无锁

- `commits: GitCommit[]` 内存数组 + 单 `head` 指针——没有分支、没有合并、没有 DAG。
- 持久化是 `writeFile(commit.json, JSON.stringify(state, null, 2))`——全量覆盖写、**无文件锁、无 WAL、无 checksum、无原子写（rename + write）**。两个进程同时 `onCommit` 会安静覆盖前者。
- 对比 Aletheia：`files/jsonl.py` 有 `_exclusive_file_lock`（msvcrt / fcntl 跨进程锁）+ `verify_audit_chain()`，SQLite 有 WAL + busy_timeout——**Aletheia 在持久化/并发一致性上比 TaG 严**。

### 4.3 推理叙事 ≠ 推理质量

AI 生成的 commit message 可以反向合理化、可以重复历史措辞、可以在数据不足时硬编叙事。TaG 把这些**忠实记录并打上 hash 印章**，反而给审计者制造"经过严密分析"的假安全感。Aletheia 已踩过同源坑（`aletheia-learning-loop-noise-not-signal`：`agent_rationale` 字段存在但学习闭环断路，rationale 质量无人验证）。

### 4.4 Stage 区纯内存——宕机即丢

AI stage 了 3 个止损意图，进程在 commit 前 OOM/重启——这 3 个意图永久消失，无任何痕迹。真 git 的 `.git/index` 是磁盘级的，TaG 没有等价保证。

### 4.5 reconcile 是打补丁不是消因

`recordReconcile()` 把"经纪商账户里有但 TaG 链不知道"的漂移直接合成 commit 入链——**绕过 guard pipeline**。这是诚实记账（漂移被承认而非吞掉），但漂移频繁意味着 git 状态与真实经纪商状态长期偏差，链上历史会被合成提交稀释。

### 4.6 运营负担：自托管 + AGPL-3.0

- **AGPL 传染性**：对外提供网络服务也触发开源义务。商业团队"内部部署 + 闭源对外服务"路径几乎被堵死。博客的 "Free as in freedom" 包装在商业语境下是限制而非自由。
- **进程间鉴权 deliberately deferred**（CHANGELOG 原话）：v1 仅绑定 `127.0.0.1`，同机器任何进程都能无鉴权调 `POST /uta/:id/wallet/push` 执行真实交易。
- **持久化是单 JSON 文件，无主备、无 checksum**：磁盘故障 = 全部历史丢失。

---

## 五、对 Aletheia 的可落地借鉴清单（已纠错）

> 所有借鉴必须先走 SDD：先在 `tests/specs/spec_*.py` 写 `SpecClaim(status="missing")` + RED 测试 → 改代码 → GREEN。
> 触 trust boundary（`risk/`, `execution/`, `config/secrets.py` 等）必须显式 owner 授权。

| 优先级 | 借鉴 | 落地位置（已纠错） | 期望收益 | 红线 |
|---|---|---|---|---|
| **P0-1** | `TradeProposal` 加 `commit_hash` + `parent_hash` | `storage/models.py:109-135`（加字段）+ `storage/repositories.py`（写入时计算）+ `tests/specs/spec_data_layer.py`（加 claim）；复用 `files/jsonl.py:_hash_record` 实现 | 提案层防篡改指纹；复盘可用 hash 而非 自增 id 定位 | 需数据库迁移；不触 `risk/engine.py` 决策逻辑 |
| **P0-2** | 执行后 `state_after` 账户全量快照 | `execution/gateway.py:~233-244` 已有的 audit 写入点**加字段**；数据来自 `get_cached_state()` | 复盘从"推算账户状态"变成"查快照"；直接喂 operator 自监管（`b4dba31` 已实战的自监管能力获得更丰富的历史状态） | trust boundary：最小改动，仅加字段不改执行逻辑；SDD 先行 |
| **P0-3** | Guard 责任链拆分 | 新建 `aletheia/risk/guards/`；每条 check 一个文件 + `Guard(Protocol)` 接口 + `GuardContext` 只读上下文 + `GUARDS: list[Guard]` 注册表；`_evaluate_proposal_locked`（实际 `engine.py:77-332`，约 255 行，**20 个编号节点 #0–#19**，非"19 条"）变成遍历注册表的 ~10 行 | ① 单 guard 可独立测试；② 新增/替换 guard 不动主函数；③ 可读性大幅提升 | trust boundary **红线最严**；行为**零变化**，前后 `uv run pytest tests/specs/ -q` 必须零回归；**严禁趁重构把 market-state 检查（funding rate / price level）塞进 engine.py**——这踩 INDEX.md §4 红线（来源：north_star.md §10.3，正确落位是 playbooks 层） |
| **P1-1** | reject 与 push 同结构（提案 commit log） | `execution/gateway.py:177/317` 已写 `proposal_rejected` audit event；`server/routes/data.py:653` 已写人工拒绝 audit——**补 `commit_hash + parent_hash + state_after` 字段，使三路拒绝与执行成功路径在 audit 层同结构** | 提案链完整（含被否的），audit 可解释性 | trust boundary，最小改动；SDD 先行 |
| **P1-2** | reconcile 持仓事件加 `commit_hash` | **核查纠错**：`portfolio/position_reconciler.py:361` **已经写了** `write_audit({event_type: 'position_adopted', ...})`——所以**不需要新增**，只需补 `commit_hash` 字段让其进入提案 hash 链 | 审计链无盲区（外部漂移持仓也有 hash） | 风险较低；SDD 先行 |

### 最小可行第一步

**P0-1（TradeProposal 加 commit_hash）** 是改动面最小、风险最低、价值最直接的起点。它**不触 trust boundary**，纯粹是 `models.py` + `repositories.py` + `spec_data_layer.py` 三处改动，可以在一个会话内完成 RED→GREEN，为后续 P0-2 / P1-1 / P1-2 提供 hash 基础设施。

---

## 六、明确拒绝的借鉴项（红线 + 项目实际约束）

| 拒绝项 | 理由 |
|---|---|
| ❌ 用 JSON 文件取代 SQLite | TaG 的 `commit.json` 全量覆盖写、无文件锁，在 Aletheia 三进程架构下立即翻车。`database is locked` 根因（`aletheia-db-lock-root-cause`）是花大价钱修好的，不能为"像 TaG"倒退 |
| ❌ 把 "AI 不能 push" 这条信任边界搬过来 | Aletheia 是全自动 paper trading（人工在 dashboard 审核），已用 `bypass_distress_gate` + `kill_switch` + risk engine 作为等价机制 |
| ❌ 引入 "branches" 概念 | 博客没明确提、代码也没实现、对交易场景无业务价值 |
| ❌ 用线性提交链取代关系表 | Aletheia 多 broker / 多 agent 并发写需要 SQLite 的 ACID，TaG 单账户单进程持久化模型不适用 |
| ❌ market-state 检查进 `risk/engine.py` | INDEX.md §4 红线 4（来源 north_star.md §10.3）。Aletheia 已有先例：资金费率熔断、funding rate 检查的正确落位是 playbooks 层，不是 risk engine。`engine.py` 已有的 `_market_data_age_seconds()`（数据新鲜度检查）是边界灰色地带，不应在其基础上扩展 |

---

## 七、对照表：Aletheia 当前状态 vs OpenAlice TaG

| TaG 能力 | Aletheia 现状 | file:symbol 证据 | 差距判定 |
|---|---|---|---|
| commit hash（提案指纹） | **无** | `storage/models.py:109-135`（无字段） | **P0 未做** |
| parent_hash 链（提案链） | **无** | 同上 | **P0 未做** |
| audit hash chain（系统事件层） | ✅ 已实现且**比 TaG 严** | `files/jsonl.py:133/193/265`；`doctor.py:1373` 全量校验 | Aletheia 领先 |
| 执行后账户全量快照（commit 级） | **无** | `execution/gateway.py:~233-244` 仅写 `status diff` | **P0 未做** |
| reject 一等审计记录 | ✅ 功能等价但结构不同 | `gateway.py:177/317`；`routes/data.py:653`；spec PL-RG-02 | 功能有，缺 hash 链入 |
| 决策推理持久化 | ✅ 已实现 | `models.py:123`（`agent_rationale`）；`position_enrichment.py:101` 回填到 `Position.decision_reasoning` | 字段存在但无 hash 绑定 |
| Guard 责任链（可插拔） | **未拆分** | `risk/engine.py:77-332`（单函数 ~255 行，20 个编号节点 #0–#19） | **P0 未做** |
| 因果链（ID 串联） | ✅ 已实现，比 TaG 更结构化 | `models.py:141/157/176`（`proposal_id` → `risk_decision_id` → `order_id`）；`repositories.py:996` retention 保护 | Aletheia 领先 |
| 知识文件 git 版本化 | ✅ 已实现（可选） | `files/git.py:62` | 范围是 knowledge/playbooks，不是 proposal |
| Decimal 金额（不落 float） | **未实现** | `models.py:117-118/164-165`（全 float）；`risk/engine.py:120` | **P1 未做**（INDEX.md §4 P1 项） |
| reconcile commit（漂移入账） | ✅ audit 事件已写 | `position_reconciler.py:361` `position_adopted` event | **缺 hash 字段** |

---

## 八点五、Vibe Trading 帖（#4）交叉核查（2026-06-29 同会话）

owner 追问让我也参考 https://www.openalice.ai/blog/traderalice-cursor-for-vibe-trading（2025-12-01 帖、TaG 预告帖）。这是 OpenAlice 整体产品定位帖，比 TaG intro 帖更广。

**披露的额外内容**：
- **Vibe Trading 哲学**：用户自然语言表达意图，AI agent 处理结构性复杂度（数据源 / 撮合 / 风控 / 策略设计），类比 Cursor 的 Vibe Coding。
- **Agentic Quant 定位**：保留量化能力，让 AI 决定何时与如何使用（区别于"预定义策略 + 信号触发"的传统量化）。
- **三个具体能力声明**：
  - AI-Native News Retrieval（"grep for financial news"）
  - DataFrame + LLM 写 Pandas / Excel 公式直接算指标
  - Trade Primitive Generator + "auto-learn its effect"（声称构造复合订单 + 自动学习效果）
- **Sandbox 双模式**：live（连真实市场） + backtest（虚拟世界，历史数据回放）。
- **ReAct framework** 作为底层 reasoning 范式（链接 arxiv.org/abs/2210.03629）。

**源码交叉核查（grep `_refs/OpenAlice/src services`）**：

| 博客声明 | 源码证据 | 结论 |
|---|---|---|
| Trade Primitive "auto-learn effect" | `learn.*effect` / `effect.*memo` grep 命中**零** | **营销话术，无实质代码**——`primitive` 命中的几个文件是 broker/CCXT 通用模板 |
| chat-driven 自然语言指令 | `src/core/{agent-work,session,compaction}.ts` + `ui/src/components/workspace/Chat*` 一整套 | 真实——OpenAlice 是 chat-driven LLM agent |
| Context compaction | `src/core/compaction.ts` 308 行，注释 `"modeled after Claude Code CLI's internal compact mechanism"` | 真实——200k token 满了召唤 LLM 总结 |
| ReAct loop | 源码用 `agent-work-listener.ts` + `tool/*` 工具调用循环 | 真实 |
| Backtest 虚拟世界 | 源码有完整 backtest 模块 | 真实 |
| AI-Native News Retrieval | 源码无独立 news collector 模块（声称指 tool 抽象） | **能力存在但话术夸张** |

**对 Aletheia 的借鉴评估**：

| 候选 | Aletheia 现状 | 判定 |
|---|---|---|
| Vibe Trading 哲学 | 用户在 dashboard 设定 strategy 参数 + 自然语言备注，AI 决策；已经是 Vibe-Trading 的一种形态 | **概念已对齐**，无需引入新话术 |
| Agentic Quant 定位 | risk engine（量化约束）+ PI agent（LLM 决策）+ Strategy Pool（量化筛选）= 教科书 Agentic Quant | **Aletheia 已经是**，只是没用这个词 |
| AI-Native News Retrieval | `news_items` 表 + dedup_key + sentiment + impact_summary + analyzed_at | **已有等价物** |
| LLM 写 Pandas 直接算指标 | 指标预算好（RSI/ATR/funding rate/OI 等）喂给 LLM | **拒绝借鉴**——让 LLM 写可执行 pandas 代码踩 INDEX.md §4 红线（agent 生成可执行代码） |
| Trade Primitive + effect learning | broker SL/TP 已有（最近修过 `adopt_position` 缺失）；effect-learning 闭环不存在 | **拒绝借鉴**——博客是营销话术，无实质代码；且 Aletheia learning loop 现产噪音（memory `aletheia-learning-loop-noise-not-signal`），加新闭环雪上加霜 |
| Sandbox 双模式 | paper / live + `backtest_runs` / `backtest_trades` + Strategy Pool 历史回测 | **已有等价物** |
| ReAct 自由 tool loop | PI agent stateless → JSON 输出（不调工具） | **拒绝借鉴**——与 trust boundary 设计冲突（agent 不可自由 tool-use） |
| chat-driven UI + compaction | dashboard-driven + scheduler；PI agent 无聊天会话 | **拒绝借鉴**——架构哲学不同；compaction 是 chat-LLM 的标配，stateless 决策不需要 |

**结论**：**这篇博客的实质工程能力对 Aletheia 没有新可落地的借鉴项**。它的价值在哲学/定位层——"Vibe Trading"和"Agentic Quant"两个定位话术，恰好命中 Aletheia 已经在做的事。

研究无新增实施工作。P0-1 已是这轮研究的唯一可落地产出。

---

## 八、给 owner 的一句话总结

**OpenAlice TaG 真正给 Aletheia 的，不是 git 比喻这层包装，而是两个工程纪律：**

1. **reject 是一等审计记录**（不是沉默的状态翻转）
2. **每笔执行后的账户全量快照绑定到决策记录上**（不是散落各表跨表拼接）

这两点是 Aletheia 现有 audit_chain 的下一步自然延伸，工作量小、价值清晰，但每一步都触 trust boundary，必须 SDD 先行。建议从 **P0-1（TradeProposal 加 `commit_hash`）** 起手——这是不触 trust boundary 的 hash 基础设施，为后续所有借鉴铺路。

**博客那段 "1 月崩盘预判" 的营销叙事不构成任何 TaG 效力证据，不应作为借鉴或对标的依据。**

---

## 附录：方法学与核查记录

**13 agent workflow 拆解**：
- 3 个 ground truth ingest agent：博客 / 真实 TS 源码 / Aletheia 对照
- 5 个分析镜头：概念映射 / 架构机制 / 价值主张可信度 / 批判与边界 / Aletheia 对比借鉴
- 5 个对抗核查 agent：每个镜头都被独立 agent 拿 ground truth 反向核查

**核查发现的关键纠错**（已并入正文）：
1. ❌ "博客暗示 branches" → ✅ 博客从未明确提及 branch/merge/diff/revert，是读者从"version control"四字自行联想（concept lens 误判 + critique lens 误判，两轮独立核查抓出来）
2. ❌ 碰撞概率 1/4 亿 → ✅ 1/43 亿（16^8 ≈ 4.3×10⁹）
3. ❌ "19 条 check" / `engine.py:77-296` → ✅ 20 个编号节点 #0–#19 / 实际 `:77-332` 约 255 行（GT-C 的快照已过期，分析者抄了过期数字）
4. ❌ "Aletheia 没写 reconcile audit" → ✅ `position_reconciler.py:361` 已写 `position_adopted` event（compare lens 据 memory 笔记推断，但未核源码，P1-B 整条前提被推翻并改写）
5. ❌ "_eval_lock 是 TOCTOU 防护" → ✅ `_eval_lock` 只是 `threading.Lock()` 防多线程并发评估，与严格 TOCTOU 是不同问题维度
6. ❌ "AI 不能 push 是代码硬约束" → ✅ 是 API 路由层设计约束（AI 客户端只暴露 stage-* 端点），不是不可绕过的技术硬锁

**Workflow run ID**：`wf_6cf9cb78-a51`（13 agent / 797k 输出 token / 8.5 分钟）
