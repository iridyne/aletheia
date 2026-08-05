# Architecture Decision Log

## 2026-07-11 — 回放只负责提出候选，独立前瞻样本才负责验证

### Context

有界历史回放能发现某个过滤阈值可能过紧，但同一批数据既用于挑选候选值、又用于证明候选值，会产生选择偏差。直接把 `ready_for_controlled_experiment` 变成配置变更，还会绕过 ranker、组合冲突、风险和执行等未回放环节。

### Decisions

1. 回放达到门槛后，只登记 observe-only 实验；生产阈值和候选放行保持不变。
2. 实验锁定 filter stage/version/family/direction/market/horizon、control、treatment 和停止门，重复控制器运行不能改写计划。
3. 只使用严格晚于登记时刻的 first-exposure 候选；挑选 treatment 的历史样本永久排除在验证外。
4. 生产 pass 是 control；生产拒绝但 shadow 阈值会放行的增量候选是 treatment。两边各需 20 个 settled net 结果和 70% 覆盖。
5. treatment 收缩净结果及其相对 control 改善均达到 5bp，只进入 `ready_for_review`；不支持则结束，生产版本变化则作废。
6. 数据库固定 `production_mutation_allowed=0`；实验代码不得导入或调用配置写入、风险、执行或 live 路径。

### Consequences

- 当前候选值需要重新积累未来样本，历史回放结果不会被冒充为独立证明。
- `ready_for_review` 不是自动应用许可；任何生产变化仍需人工决定及下游影响评估。
- 实验计划和每次评估快照保留为小型分析审计历史，便于解释为什么某个候选被继续、否定或作废。

---

## 2026-07-11 — macOS 用周期 LaunchAgent 托管 gated start，而不另造 supervisor

### Context

现有 gateway 已有 supervisor 管理 frontdoor/backend，`gateway start` 又是幂等、带启动验证的短命令。若 launchd 直接用 `KeepAlive` 反复运行这个短命令，正常退出会触发重启风暴；若让 launchd 分别托管内部两个进程，则会产生第二套生命周期真相源。

### Decisions

1. macOS 安装用户级 LaunchAgent，不用 root/LaunchDaemon；登录时运行，并用 `StartInterval=60` 周期执行同一个 `gateway start --no-browser`。
2. 不使用 `KeepAlive`。plist 使用 `AbandonProcessGroup=true`，让 gated start 生成的独立 supervisor 在检查进程退出后继续存活。
3. plist 只保存绝对 venv Python、项目工作目录和日志路径，不保存环境变量、API key、token 或凭证引用。
4. 安装采用校验后原子替换，再用 `launchctl bootstrap/kickstart`；删除使用 `bootout`。状态以 `launchctl print` 为准，不以文件存在冒充已加载。
5. Windows 继续使用原 Task Scheduler 实现；两个平台共享 enable/disable/status API、CLI、Settings 和 doctor 语义。

### Consequences

- supervisor 仍是唯一内部进程管理者；launchd 只负责 supervisor 整体死亡或用户重新登录后的恢复。
- 周期任务显示 `state=not running` 可以是正常状态，必须结合 runs 与 last exit code 判断是否成功。
- 项目或 venv 路径变化会被标为配置漂移，需要重新 enable，而不是显示虚假绿色。

---

## 2026-07-10 — 筛选负反馈只使用事前候选与同口径净结果

### Context

旧 filter controller 只看整个 scanner family 的表现，但管道没有保存某个具体门槛放过了谁、拒绝了谁。这无法区分家族本身好坏与某个筛选器的因果作用，更不能用事后涨跌为无方向候选选一个更赚的方向。

### Decisions

1. 每个可评价筛选关必须保存事前候选身份、最小输入、pass/reject/not-applicable/error、原因、参数版本和代码提交。
2. 高频无方向门可以按扫描批次压缩，但 `candidate_facts` 必须包含全部候选，不得抽样。
3. 只有事前已有 long/short、exact venue/symbol/market 和有效入场价的候选才创建可学习净结果。同一候选经过多关时共享一份结果。
4. 筛选评价至少按 filter version + family + direction + market + horizon 分层，使用收缩净期望。pass/reject 两边各少于 20 个完整 net 样本或覆盖低于 70% 时只报证据不足。
5. 观察数据只能输出 `overrestrictive_suspect`，不直接改参数。Risk/live/credential/exchange-capability 不因错过收益自动放宽。
6. 为稳定长期运行，高频批次保留 30 天，逐候选/结果保留 90 天。这些是分析镜像；权威交易、风控和审计原件仍永久保留。

### Consequences

- 新代码上线后需要重新积累样本，旧的 family 总胜率不能补写成某个 filter 的历史对照。
- 方向刹车、ranker 权重、family 生产校准和 sizing 仍需独立受控实验。
- 证据写入失败不改变原始交易决定，但 `doctor` 必须暴露覆盖和计数不一致。

---

## 2026-07-10 — Opportunity 结果以固定时点净收益为学习事实

### Context

原有 `universe_forward_outcomes` 只保存入场后的原始价格涨跌，结算器在任务真正运行时查当前价。它无法区分现货/永续合约，也没有手续费、价差、滑点和资金费。调度器晚到会改变所谓的“1h 收益”，不同方向和不同市场的样本也会混在一起。

### Decisions

1. 可用于学习的结果必须锁定 `venue + symbol + market_type + direction + horizon`，并优先用目标时点已完成的 OHLCV。
2. “胜出”不再等于方向正确；必须在手续费、价差/滑点和资金费后，净收益仍超过最低可交易优势。
3. 历史 gross-only 行可用于说明价格发生过什么，但不允许猜测成本并补写净收益，不允许进入新校准和规则生成。
4. 现货后备数据不允许满足永续合约的行情请求。存储主键不能表达完整标的时，宁可不写缓存，也不覆盖另一市场的数据。
5. 校准和风控读取必须按策略族+方向+市场类型分组。这个净收益口径仍不能代替某个前置筛选器自己的放行/拒绝对照证据。

### Consequences

- 旧数据不会立即填充新学习样本；系统将从新写入的完整结果逐步积累证据。
- 永续合约缺入场时资金费时，会显示证据不完整，而不是把资金费当成 0。
- 前置筛选器的自动调整继续保持关闭，直到每个筛选器的对照数据落库。

---

## 2026-07-04 — Agentic Trading 数据源采购与集成顺序

### Context

本轮梳理外部数据源后，候选项覆盖交易所原生数据、市场聚合、衍生品/微观结构、链上、宏观、新闻社媒、预测市场、中国市场和 Agent 搜索工具。当前系统的瓶颈不是“再接一个昂贵数据商”，而是让已经接入或低成本可接入的数据变成可信、可观测、会被 agent 正确使用的信号。

高价机构源（Kaiko、CoinGlass 高阶套餐、CoinAPI 全量、RavenPack、Factiva、Wind/iFinD 等）能提供更完整的历史、盘口、新闻和合规覆盖，但在单人交易 MVP 阶段，如果没有明确消费者、评估指标和已证明的信号缺口，会迅速变成成本和维护负担。

### Decisions

1. **先修可观测性，再买数据**
   - 任何新增数据源必须先能回答三件事：最近一次成功拉取是什么时候、写进了哪张表、哪个 agent 或页面实际使用它。
   - `doctor` 必须优先暴露“表为空/太旧/开关关闭”的状态，避免数据源静默失效。

2. **优先低成本且能直接进入决策上下文的数据**
   - 第一梯队：交易所原生数据、订单簿、Coinalyze 免费/低价额度、官方公告 RSS、SEC/CFTC/Fed 这类官方源、DefiLlama/Hyperliquid/CoinGecko 等已接入源。
   - 原因：这些源成本低、语义清楚、能直接服务 agent 判断“行情是否可交易、杠杆是否拥挤、宏观/监管是否有突发变化”。

3. **预测市场保持默认关闭，只有新鲜数据才进入上下文**
   - Prediction market 是情绪/叙事补充，不是交易真相源。
   - 默认关闭保留；开启前必须确认采集新鲜、问题类别匹配、旧数据不会进入 prompt。

4. **暂缓高价机构数据源集成**
   - 暂不接 Kaiko / RavenPack / Factiva / Wind / iFinD 等高价源。
   - 触发重新评估的条件：现有免费/低价源已经跑稳；agent 决策日志能指出明确缺失；有可回测或可对照的收益/风控指标；月度成本能被预期收益覆盖。

5. **中国/A 股数据先作为研究支线，不进入交易主链**
   - AKShare/Tushare/BaoStock 等可用于宏观和主题研究，但在当前主交易链仍是 crypto/crypto-perp 优先。
   - 未建立证券交易执行、合规和风控边界前，不把 A 股信号直接接入自动交易决策。

### Consequences

- 下一步集成顺序应是：先让现有表和低成本源有健康检查，再补官方公告源，然后评估是否手动启用 prediction market；高价数据商只做采购备忘和试用评估，不进入 MVP 默认依赖。
- 新增数据源必须同步登记数据来源、消费者和状态；没有消费者的数据不落库。

---

## 2026-07-02 — Universe registry whitelist dict 格式类型不一致致 pipeline 静默全杀

### Context

`OKXClient.load_demo_markets()` 被改为返回 `{"swap": frozenset, "spot": frozenset}` dict，目的是让 gateway 的 pre-flight 检查能按 market_type 区分（F1 修复：`WLD` 等仅 spot 挂单的币种不能被路由到 perp 订单）。但 `universe/registry.py:_load_demo_whitelist()` 仍按旧接口签名返回 `frozenset | None`，直接 return dict。

`sync_registry` 用 `canonical not in demo_whitelist` 过滤。Python `in` 在 dict 上查的是 **key**（"swap"/"spot"），不是 frozenset 里的 symbol → 所有真实 symbol 检查都失败 → `sync_registry` 注册 0 个 asset → snapshot selected=0 → universe 永久 fallback 到 3 个静态币。

DB 铁证：`universe_registry` 1434 行，active=0；scanner runs 5826 次+，最近数百次全部 scanned=0。

### Root Cause Pattern：interface 转换边界断裂

helper 改变了返回类型（`frozenset` → `dict[str, frozenset]`），所有 caller 必须同步适配。本次仅 gateway 路径（`okx_broker._get_demo_symbols`）做了适配，registry 路径遗漏。静默失败——没有 exception、没有 error log（`logger.debug` 级别），只能通过 DB 全零或用户报"只有 3 个币"才能发现。

### Decisions

1. **Fix in helper, not caller（保持向后兼容）**
   - `_load_demo_whitelist()` 内部 collapse dict → frozenset 并集。保持签名 `frozenset | None` 不变。
   - Rationale：最小改动（1 个函数内 5 行），registry.py 的 `if canonical not in whitelist` 判断逻辑不动，gateway 路径也不受影响。

2. **追加回归测试 `test_registry_whitelist.py`**
   - 4 个 case：dict collapse 成 frozenset、签名不变、symbol 可测试、non-okx 返回 None、空 markets。
   - 覆巢之下：成员 `test_dict_vs_frozenset_membership` 直接演示 bug 模式，确保未来不能「无感」破回去。

3. **教训：调用方多样化场景下的 interface 变更必须清查所有 site**
   - 规则：当 helper 返回结构从 scalar/flat 改成 nested dict/list 时，**grep 所有 caller 并逐一对齐**。
   - 规则：当 caller 用 `in`/`not in` 操作符时，如果返回类型在 dict/frozenset/list 之间切换，这是高崩风险——dict `in` 查 key、frozenset `in` 查 value。
   - 规则：静默失败（debug-log + continue）是高危模式——考虑在 registry sync count=0 时 `logger.warning`，让运维可观测。
   - 规则：核心数据路径（registry sync、opportunity emission、order placement）的 count=0 状态应该记录到 stability_monitor 或 health endpoint，不能靠日志 grep。

### Files
- `aletheia/universe/registry.py:58` — `_load_demo_whitelist` collapse dict → frozenset
- `tests/test_registry_whitelist.py` — 新增回归测试（4 case）
- DB: `data/app.db universe_registry` 1434 行将随下次 refresh 逐步激活（okx venue 377 行重新变为 active）

---

## 2026-07-01 — Scanner/Observer 审计修复的架构决策

### Context
基于两份审计报告修复 universe scanner + market observer。手动核实（宽 fan-out workflow 因 workspace rate-limit 全灭）查出报告 6 处根因偏差，若干项据此改变处置。

### Decisions
1. **forward-outcome direction 必须与 opportunity 同源（single source of truth）**
   - 问题：`service.py:256` 用 `_infer_direction` 重算方向，opportunity 用 `scanner_direction`；分裂造成 momentum 54% direction=None + event 0 条 + 胜率统计污染。
   - 决定：inserted_members direction 改用 `item.get("scanner_direction") or _infer_direction(...)`，与 signal_emitter 完全对齐。方向只有一个权威来源（scanner 内在方向），`_infer_direction` 仅作 fallback。spec UP-01/UP-03 锁定。

2. **`all_scores` 不落库（避免无消费者数据）**
   - 报告建议持久化 5-family 分数供 ensemble 分析。
   - 决定：**不加列**。当前无任何消费者，落库违反 data_utilization 契约「无消费者数据=负债」。有明确消费需求时再加，记入 ROADMAP。

3. **scanner_weights 与 calibration 相乘叠加，而非替换**
   - `scanner_weights`（PI 手调）与 calibration（自动胜率）都是 family 级乘数。
   - 决定：`opportunity *= calibration × scanner_weights`。空 dict → 全 1.0，不改变默认行为。关联 OL-C09。

4. **guard advisory 用 scanner_family（LLM 前可得）做 per-symbol 匹配，不用 direction**
   - direction 是 LLM 输出（LLM 前不可得）；scanner_family 来自 universe member（LLM 前已知）。
   - 决定：MO-2 用 scanner_family 做 per-symbol guard 匹配，闭合断口 F 核心（scanner_family 缺失致恒不匹配）。direction 维度作为后续增强。

---

## 2026-06-29 — OpenAlice TaG 借鉴策略的 5 个决策

### Context
研究 OpenAlice Trading-as-Git（TaG）的工程实现后，识别出可借鉴清单 5 项（P0-1 commit_hash + P0-2 state_after 快照 + P0-3 Guard 责任链拆分 + P1-1 reject 同构 + P1-2 reconcile hash）。落地前需要拍板若干设计取舍。

### Decisions

1. **从 P0-1（TradeProposal 加 `commit_hash` + `parent_hash`）起手，而非更大重构**
   - 候选：先做 P0-3 Guard 责任链拆分（结构最大改动，但风险最高）；先做 P0-2 state_after 快照（价值最直接，但触 `execution/gateway.py` trust boundary）。
   - **决定**：从 P0-1 起手。它**不触任何 trust boundary**（纯 `storage/` 改动 + 加 spec），且为后续 P0-2 / P1-1 / P1-2 提供 hash 基础设施——没有 commit_hash 就无法链入 audit event。
   - 否决"一次做完所有 P0"备选：本会话已实施 P0-1 + P1-2 两项，剩余 P0-2 / P0-3 / P1-1 仍需 owner 显式批准（trust boundary）。

2. **commit_hash 长度选 16 字符（64-bit 空间），而非照搬 OpenAlice 的 8 字符**
   - OpenAlice 用 8 字符 SHA-256 截断（43 亿空间），对单账户单进程足够。
   - **决定**：Aletheia 多 broker / 多 strategy / 多 execution_mode 共享一条全局单链，节点密度远超 OpenAlice 设计假设；加宽到 16 字符（64-bit ≈ 1.8e19 空间），把误碰撞概率压到实际不可能；存储成本无差异。
   - 否决"全 64 字符"备选：与 git short-hash 习惯偏离过远，且非数据库主键，64-bit 已远超实用强度。

3. **哈希输入排除 `status` 与 `commit_hash` 自身（而非全字段哈希）**
   - 全字段哈希简单但有致命问题：`pending → executed/rejected` 是正常状态流转，会破链。
   - **决定**：`p.model_dump(exclude={"status", "commit_hash"})` 输入 canonical JSON SHA-256。
   - L2c spec（DM-C02）显式断言"status 变化不影响 hash 重算"，防止未来误改哈希函数引入回归。

4. **全局单链（按 `rowid DESC` 选 parent），而非按 broker / strategy 分链**
   - 候选：按 broker 分链（4 条链，bitget / okx / binance / paper）；按 strategy 分链（数十条链）；按 execution_mode 分链。
   - **决定**：全局单链。多 broker 间提案串成一条决策时间线，任何篡改都破链；查询简单（`SELECT commit_hash FROM trade_proposals WHERE commit_hash IS NOT NULL ORDER BY rowid DESC LIMIT 1`）；与 OpenAlice 设计一致。
   - 否决分链备选：分链增加复杂度，对审计价值无实质提升。

5. **拒绝若干 OpenAlice / Vibe Trading 借鉴项**
   - **拒绝 LLM 写 Pandas 直接算指标**（Vibe Trading 帖宣传）：踩 INDEX.md §4 红线（agent 生成可执行代码）；Aletheia 预算好指标喂 LLM 是更稳健设计。
   - **拒绝 Trade Primitive auto-learn effect**：源码 grep 命中零，纯营销话术；且 Aletheia learning loop 现产噪音（memory `aletheia-learning-loop-noise-not-signal`），加新闭环雪上加霜。
   - **拒绝 ReAct 自由 tool 循环 + chat-driven UI + context compaction**：与 Aletheia trust boundary 设计哲学冲突（agent 不可自由 tool-use；dashboard + scheduler 模式不需要长会话压缩）。
   - **拒绝 JSON 文件取代 SQLite**：OpenAlice 持久化是 `commit.json` 全量覆盖写，无文件锁；Aletheia 三进程并发会立即翻车（`database is locked` 根因刚修好）。
   - **拒绝 "AI 不能 push" 信任边界搬过来**：Aletheia 是全自动 paper trading（人工在 dashboard 审核），已用 `bypass_distress_gate` + `kill_switch` + risk engine 作等价机制；不需要 OpenAlice 那种 stage/push 人工放行模式。

### Reasoning
共同原则：**借鉴工程纪律不抄设计哲学**（reject-as-first-class + state_after 快照是纪律，stage/push/chat 是哲学差异）；**给基础设施铺路再做下游**（P0-1 先于 P0-2/P1-1/P1-2）；**spec 先行锁定不变量**（每条 P0 借鉴必有对应 DM-* / PL-ADOPT-* spec claim 锁住，未来误改会被立即捕获）；**营销话术经源码 grep 才采信**（"auto-learn effect" / "1 月崩盘预判" 经核查全部祛魅）。

### References
- 研究报告：`docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`
- 历史借鉴清单 + 当时状态：`docs/archive/legacy/issues/backlog/TAG_BORROW_BACKLOG.md`
- L2 契约同步：`docs/design/data/data_model.md` + `docs/design/contracts/audit_chain_contract.md §6.4` + `docs/governance/policies/trust_and_change_control.md §1.1`
- Memory：`aletheia-tag-borrow-plan`、`aletheia-refs-openalice-prism`

---

## 2026-06-24 - 最小赚钱循环修复期的 4 个架构决策

### Context
排查"PI 不开仓"暴露五层阻碍,修复中遇到几个需要定调的架构选择。

### Decisions

1. **OKXClient 改 lazy-init(不用静态 contractSize 表)**
   - CCXT exchange 的 aiohttp session 绑定创建它的 event loop。OKXClient 在 __init__
     (同步上下文)建 exchange,但经 okx_broker._run_coro 在新线程新 loop 使用 → 跨 loop。
   - **决定**:`__init__` 只存凭证,`_ensure_exchange()` 在首次 async 调用所在 loop 懒建。
   - 否决"静态 contractSize 表"备选:OKX swap 有 76 个 contractSize≠1 的币、11 种值、
     新币持续加入,默认 1.0 绝不安全,必须修真正根因(loop 绑定)而非绕过。

2. **demo 日损用 starting_capital_usdt 基数(CR-RE-03)**
   - engine.py 检查 #8 原对所有模式用 max_live_limited_cap_usdt → demo 日损限 $1。
   - **决定**:按 `_is_live()` 分流,demo 用 starting_capital_usdt,对齐检查 #13 既有范式。
   - 这是 engine 内部一致性修复(check #13 已用 demo 基数,#8 漏了),非新逻辑。

3. **ID 格式:文档对齐代码(12-char hex),不迁移 UUID4**
   - 契约说 UUID4 36-char,代码用 `uuid4().hex[:12]`(48bit)。
   - **决定**:改文档对齐代码。碰撞 P≈2e-7@1e4 行可忽略,short 形式利于日志可读;
     full-UUID4 迁移是无谓的破坏性改动。

4. **冻结例外的判决记账:Round 1 起点保留 6/22,修复前数据不计入**
   - 3 次冻结/trust 例外(profit-loop 8 bug / PT-1 / CR-RE-03)都在 Round 1 观察期内修。
   - **决定(用户拍板)**:不重置 Round 1 起点。判决时区分修复前/后——所有修复生效前
     (6/22→6/24)在错误事实下产生的 skip/proposal 数据**不计入策略评估**,6/24 起为
     干净观察窗口。所有例外登记 EXPERIMENT_PROTOCOL 冻结例外记录。

### Reasoning
共同原则:**修真正的根因,不绕过**(lazy-init vs 静态表);**对齐既有范式,不发明新逻辑**
(日损基数分流跟随 check #13);**文档追代码当代码是既成合理约定**(ID 格式);
**诚实记账,预承诺不事后改**(实验判决区分污染前后数据)。

---

## 2026-06-17 - Self-Evolution Tier 1：4h 作为触发窗口，medium_confidence 不进 FTS

### Context

Universe forward outcomes 有 4 个时间窗口（15m / 1h / 4h / 24h）。需要选一个作为
自动生成 guard_candidate 的触发窗口。同时，guard_candidate 的 confidence 级别决定
它是否会被 LLM 在市场分析时检索到。

### Decision

1. **触发窗口选 4h**：15m/1h 噪声过大，24h 延迟过长，4h 是信噪比最好的窗口。
2. **生成 `medium_confidence`（不进 FTS）**：guard_candidate 是机器自动生成的候选，
   未经人工审核，不应自动进入 LLM 检索池（防止低质量噪声干扰分析）。
   需人工确认后用 `create_trading_guard()` 升级为 `high_confidence` 才可检索。
3. **Tier 1 零 LLM**：所有内容由 `_build_guard_candidate_content()` 程序化生成，
   零额外 token 成本，不依赖 LLM 可用性。

### Reasoning

- 4h 窗口与 paper_trader 的持仓周期匹配，是判断"入场是否合理"的最有意义时间段。
- `medium_confidence` 确保人工是质量关的最终把控者；FTS 只服务高置信知识。
- Tier 1 无 LLM 依赖意味着即使 LLM 提供商不可用，反馈闭环仍然运转。

---

## 2026-06-13 - Dev 栈统一到 single-port :8787,移除独立 Vite dev server

### Context

此前本地开发跑两个端点:Vite dev server(:5173,HMR)+ gateway(:8787,API + 生产静态)。
两端口导致 dev 与生产形态分叉:dev 下前端走 Vite proxy、生产下走 gateway 的 StaticFiles,
路径/CORS/资源解析行为不一致,且 SSE 触发的 `static 404` bug(`ba06f5c`)正源于此分叉。

### Decision

dev 与生产**统一为单端点 :8787**。dev.ps1 不再起 Vite dev server;改用
`vite build --watch`(产物落 `aletheia/dashboard_static/`)+ gateway StaticFiles 服务,
backend `uvicorn --reload` 提供后端热重载,SSE(`frontend-built` 事件)驱动浏览器自动刷新。

### Reasoning

- dev 形态 ≡ 生产形态:同一个 gateway 服务 API + 静态,消除 proxy/StaticFiles 行为分叉。
- 单端点 = 单一可信地址,符合项目 "localhost 单控制面" 的定位(CLAUDE.md frontend 段)。
- Starlette StaticFiles 每请求 `os.stat()`,无目录缓存,故前端 rebuild 无需重启 worker——
  SSE 事件单独触发浏览器刷新即可(`cbbab00` 据此移除了多余的 worker restart)。

### Consequences

- 所有 dev-reload 能力门控 `ALETHEIA_DEV_RELOAD=1`(仅 dev.ps1 设)+ `live_disabled`;
  生产模式 supervisor 崩溃保护不受影响。
- UI 迭代统一用 `scripts/dev.ps1`,**不要**在紧循环里手动 rebuild `dashboard_static/`
  (已写入 CLAUDE.md frontend 规则)。

### Alternatives Considered

- 保留双端口 + 修 proxy 配置:治标,dev/prod 分叉根因仍在。
- 纯 HMR(不落静态):dev 与生产形态进一步分叉,且 gateway 需感知 Vite 内部协议,耦合加重。

---

## 2026-06-12 - 修订 trust §1.1:外部仓位可经"合成诚实因果链"纳入本地账本(adopted)

### Context

Aletheia 原先只管理**自己开的**仓位(proposal → risk → order → position 完整因果链)。
交易所账户上**先于 Aletheia 存在**或在系统外开出的仓位,对管理层完全不可见:`exchange_sync`
只读地把它们拉进缓存,仅向 UI 暴露一个 drift *计数*。当前 demo 账户就坐实了这个缺口——
2 个外部仓位(BTC 空、SOL 空 0.2)持续产生浮亏,却没有止损、没有策略、无人管理。

项目所有者(2026-06-12)做出明确立场决策:**Aletheia 一旦启动接管账户,人就很难再与 AI
并行管理仓位,因此账户上的全部仓位(含外部仓位)都应统一交由 AI agent 管理**("接管即纳管")。

这与旧的 trust §1.1 铁律直接冲突——原铁律规定"外部仓位**永不**写入本地 positions 表,因为
它们没有 proposal 因果链回链,会破坏因果链诚实性",并有一条红线测试
`test_external_positions_not_written_to_local_table` 强制执行。

### Decision

修订 trust §1.1:外部仓位**可以**进入本地 `positions` 表,但**必须**经由一条
**合成但诚实**的因果链被"纳管(adopt)":

1. 新增 `source` 列(`positions` / `orders` / `trade_proposals`),取值 `native`(Aletheia
   正常开出)或 `adopted`(reconciler 纳管外部仓位时合成)。已有行一律 `native`。
2. `position_reconciler.adopt_position()` 在单个 `transaction()` 内合成
   proposal → order(filled) → position,全部 `source="adopted"`,position 的
   `proposal_id`/`order_id` 回链非空,入场价/数量取自交易所(真相),不伪造。
3. 合成的 proposal/order 语义上断言的是**"Aletheia 纳管了一个既存仓位"**,**不是**
   "Aletheia 决定开这笔交易"——`source=adopted` 与 rationale 文案显式说明这一点。
4. 纳管仓位的 `execution_mode` 由账户模式映射:demo → `bitget_demo`,live →
   `bitget_live_limited`。这一个映射点同时决定了管理策略:`position_manager` 自动管理
   `bitget_demo`、**跳过** `bitget_live_limited`——**live 仍是人工专属,由架构强制保证**。
5. 红线测试 `test_external_positions_not_written_to_local_table` 改写为
   `test_adopted_positions_carry_synthetic_chain`:断言纳管行带 `source="adopted"` 且
   回链非空(从"禁止入表"改为"入表必须带诚实链")。

### Reasoning

- 并行的人+AI 仓位管理在实践中不可行;单一权威比双头管理更安全(符合优先级
  State consistency > Safety)。
- 旧铁律的**目的**是保持因果链诚实——而非永久禁止纳管。合成诚实因果链同时满足两者:
  外部仓位进入管理,且每个仓位仍有完整、如实标注的链,可审计。
- demo 自动 / live 人工 的拆分不靠散落的 if 判断,而由 `execution_mode` 一处如实设置
  后被既有的 `position_manager` 守卫(`_PAPER_MODES`)免费强制执行,改动面最小。
- live 真实资金的纳管与平仓**始终需人工确认**,当前绝不自动;但架构预留了清晰的、
  默认关闭的接缝,未来 `Full` 模式 + live-unlock 下可由顶层 LLM agents 接管 live(P4)。

### 补充决策(2026-06-12):demo 自动纳管任务 **默认开启**

项目所有者明确要求:Phase 3 的 demo 自动纳管定时任务 **默认开启**(`auto_adopt_enabled`
默认 `true`),而非默认关闭后手动启用。理由:所有者的立场是"接管即纳管,全账户统一交由
AI 管理";若默认关闭,外部仓位会被静默地继续无人管理,正好违背初衷。该开关只作为
**关闭行为的 kill-switch 存在**,不是"选择性加入"的开关。

> ⚠️ **行为警告(务必知晓)**:此任务默认开启意味着——一旦 gateway 在 demo 模式下启动,
> 账户上**任何系统外的 demo 仓位都会被自动纳管**,随即 `position_manager` 会按
> `cfg.trading.default_stop_loss_pct` / `default_take_profit_pct` 给它们加默认止损/止盈,
> 并可能据此**自动平仓**(平仓会下真实 demo 单)。这是"接管即纳管"的预期行为,但它是系统
> 第一次对真实 demo 仓位**主动下手**而非仅观察。Live 仓位不受影响(始终人工确认)。
> 要临时停用,把 `auto_adopt_enabled` 设为 `false`。



### Context

用户在 `worktree-dev` 上明确要求继续推进 **Pi Agent / Operator Layer** 的完整设计,并要求
不要漏掉外部参考文件 `C:\Users\Administrator\Downloads\aletheia_pi_agent_implementation_plan.md`
里的任何关键内容。与此同时,Phase 0 的 `72h` 观察窗继续在另一台观测机上运行,本 checkout 的职责
是**并行设计与原型准备**,不是拿 branch 进展冒充 observation gate 达标。

repo 现状也决定了不能继续含糊:

- 现有 `aletheia/agent_shell/` 已存在,但它只是**单回合 request-response runtime**,不是常驻
  PI-style operator runtime
- `frontend/src/components/CommandPalette.tsx` 已有 `Ctrl+K` 入口,但它只是弹窗,不是完整
  Command Center 工作面
- 当前全局权限仍是 `Observe / Paper / Full`,与参考设计要求的
  `ReadOnly / Research / PaperAuto / AllowEditing / Auto` 不兼容
- 安全边界要求任何顶层 operator 都不能直接触碰 live、credentials、hard risk、execution gateway

因此必须先把最终设计方向钉死,避免后续实现沿着两条互斥路线摇摆:

1. 继续把现有 `/api/command` 与 Python `agent_shell` 堆成单进程大壳
2. 采用双进程,把 PI-style 常驻 runtime 与 trading truth layer 分开

### Decision

Operator Layer 采用 **dual-process** 架构:

1. **Python gateway 保持权威真相层**
   - 继续拥有交易状态真相、命令执行权、审计写入权、配置真相、SQLite 持久化权
   - 一切会改动系统状态的 Operator 命令,仍由 Python backend 校验并执行

2. **新增 Node/TypeScript `Operator Shell`**
   - 负责 PI-style 常驻 session runtime、operator loop、任务编排、修复建议、推荐生成
   - 通过 loopback API 与 Python gateway 通信
   - 不直接写 `app.db`,不直接调交易执行,不直接碰 secrets / live / hard risk

3. **交互形态固定**
   - `Ctrl+K` 仅保留为 launcher / quick ask
   - 真正的主产品面是独立的 **Command Center 页面**

4. **权限拆层固定**
   - 保留现有 worker/trading 权限语义
   - 新增独立 `operator.permission_mode`
   - Canonical operator modes: `ReadOnly / Research / PaperAuto / AllowEditing / Auto`

5. **现有 `aletheia/agent_shell` 不推倒重来**
   - 保留并重构定位为 Python 侧 operator substrate / compatibility layer
   - 它不再被视为最终的 Apex Agent Shell

### Reasoning

- 这条路线是外部参考设计与当前 repo 真实结构之间唯一不自相矛盾的落点:
  - PI-style 常驻 runtime 需要自己的服务边界
  - Aletheia 的 trading truth、risk、execution、audit 又必须继续集中在 Python backend
- 若继续走 Python 单进程大壳:
  - 会把 reasoning runtime、command execution、state truth 混进同一层
  - 更难保持安全边界清晰
  - 也更偏离参考 PI 设计的本质
- 若让 Node shell 直接写 DB 或直接执行交易:
  - 会破坏现有后端对交易/风控/审计的权威性
  - 也会把 Windows 本地多进程 SQLite 写入风险放大
- Command Center 必须独立成页,因为参考设计要求持久会话、trace、inspector、recommendation
  等完整 operator workspace,这不是 `Ctrl+K` 弹窗能承载的

### Consequences

- 本地设计 source of truth 收口为:
  - `docs/design/operator/operator_layer.md` = 概念层
  - `docs/design/history/operator-specs/2026-06-10-pi-operator-agent-dual-process-design.md` = repo-specific 双进程蓝图
  - `docs/design/history/operator-specs/2026-06-10-pi-operator-agent-design-crosswalk.md` = 参考文件覆盖矩阵
- 后续 implementation plan 必须按 dual-process 拆 Track 0-9,不能回退成单进程 quick fix
- `CURRENT_TASK.md` / `ROADMAP.md` / `AI_HANDOFF.md` / `PIPELINE_STATUS.md` 都应引用上述本地设计源
- 这条决策**不等于**允许立即合并或上线;上线仍受 `Phase 0 门全绿 + 显式授权` 约束

### Alternatives Considered

- **继续扩写 Python-only `aletheia/agent_shell`**
  - rejected: 会把 operator runtime 与 trading truth layer 混在一处,且偏离参考 PI 设计

- **完全外置独立 daemon,前端直接对接**
  - rejected: 会削弱 gateway 作为本地产品控制平面的地位,也更难保证 dashboard honesty

- **把 `Ctrl+K` 弹窗继续升级成主表面**
  - rejected: 只能承载 quick command / quick ask,承载不了 operator workspace

### Supersedes / Clarifies

- **Clarifies** `2026-06-07 - WebUI: lightweight cockpit IA fix, defer 5-zone shell`
  - 该决策针对当时主分支 UI 范围控制,仍然成立
  - 但其“global Command Center deferred”现在被限定为:
    - 主线合并/上线仍 gated
    - `worktree-dev` 上的 Operator Layer **设计与实现准备**已获明确授权,可以继续推进

---

## 2026-06-10 - CLI 分层 + 运营命令软弃用(人类交互收口到 Web)

### Context

人类交互散落在两个面:CLI(Typer)与 Web dashboard。多数 CLI 子命令已有等价的
`/api/*` 端点。提出"是否完全去掉 CLI、把人类交互全部走 Web UI"。

排雷发现一个**鸡生蛋的硬约束**:Web UI 由 gateway **进程**伺服,进程没起就没有 UI
——无法用"还不存在的 UI"去启动那个进程。且 Windows 看门狗任务的命令体写死
`aletheia.main gateway start --no-browser`,`doctor` 最需要的时刻恰是 gateway 挂掉、
UI 够不着时。这两类命令天然属于进程外的命令行。

另一处雷:`doctor` 的 blocker/warn 修复提示(fix_hint)写死了 `aletheia risk resume`、
`aletheia secrets set`、`aletheia model set`——若删命令而不改提示,提示即成谎话
(正是本项目反复对抗的"记录-事实不符")。

### Decision

CLI 不去掉,而是**分三层**,人类日常交互收口到 Web:

1. **Bootstrap 内核(保留,Web 无法替代)**:`gateway start/restart/enable-autostart`、
   `doctor`、`version`。"让机器活起来 + 看它活没活"的最小集。
2. **运维/调试工具(保留,无 Web 平价)**:`bitget sync`、`knowledge reindex`、
   `knowledge search`。
3. **运营命令(软弃用,已有 Web 平价)**:`permission`/`model`/`risk`/`strategy`/
   `update` 整组 + `secrets`(set/list/test)+ `bitget test`。

**软弃用 = 命令照常工作,仅在运行时往 stderr 打一行指向 Web 位置的提示**
(`cli/_deprecated.py::warn_deprecated`);stdout 不变,脚本与现有测试零影响。

### Reasoning

- 选软弃用而非硬删:零回退风险、零功能损失、保留逃生口。硬删要同步删测试 +
  清文档 + 改 doctor 提示,改动面大且与"逐步去掉"的渐进意图相悖。
- 提示走 stderr 而非 stdout:保证 538 测试(断言 stdout/exit_code)原样通过,
  且管道/脚本不被污染。
- doctor fix_hint 同步改为"Web 首选、CLI 备选",维持指引指向被维护的面。

### Consequences

- 将来某运营命令的 Web 路径用顺手、确认无依赖后,物理删除只剩"删代码 + 删对应
  测试"两步,无连带风险。
- 新增运营功能应优先(或仅)做 Web 端;CLI 侧不再扩充运营命令。
- Bootstrap 内核(`gateway start` + `doctor`)是**不可删**的——删它会切断刚稳定的
  无人值守复活链(看门狗依赖 `gateway start`)。

### Alternatives Considered

- **完全去掉 CLI**:被鸡生蛋约束否决——UI 架在 gateway 进程上,无进程即无 UI;
  看门狗也依赖 `gateway start`。
- **立即硬删运营命令**:回退成本高、改动面大,与渐进意图相悖;留作将来一次性收尾。

---

## 2026-06-09 - Bitget demo wiring: empirical probe results (STEP 0)

### Context

Connecting paper trading to Bitget's real demo/simulated API (instead of local paper_broker). Web docs were unreachable (403/Cloudflare), so the v2 demo mechanism was determined by placing real minimal demo orders and inspecting raw responses (`scripts/_probe_*.py`, since deleted).

### Findings (all empirically verified against the live demo account)

1. **Outcome A confirmed**: demo uses `productType=USDT-FUTURES` + `paptrading:1` header. The S-prefixed `SUSDT-FUTURES` is **rejected** (`40778: does not support SUSDT currency as margin`). → No productType/symbol mapping needed; the planned STEP 5 is dropped.
2. **Demo account is `hedge_mode`** (two-way positions), margin `crossed`, ~198 USDT available.
3. **Mix v2 order body requires fields the current `client.py:211-218` omits** — this is a latent bug in `execute_live_order`'s path that was never caught because every live test mocks `place_order`:
   - `marginCoin: "USDT"` (omitting → `400172 Margin Coin cannot be empty`)
   - **Open**: `tradeSide: "open"` + `side: "buy"|"sell"` (omitting tradeSide in hedge mode → `40774 ... unilateral position type`)
   - **Close**: `tradeSide: "close"` + `holdSide: "long"|"short"` + `side` SAME as the open side (close a long = `side:"buy", tradeSide:"close", holdSide:"long"`). `reduceOnly` is NOT the hedge-mode close mechanism; `tradeSide:close`+`holdSide` is. Omitting holdSide → `22002 No position to close`.
4. **Market orders fill synchronously**: immediately after place-order, `GET /mix/order/detail?orderId=...` returns `state=filled, baseVolume, priceAvg`. → STEP 4 can parse fill status synchronously from a follow-up detail query; STEP 7 reconciliation does NOT need to be extended for demo.

### Decision

- `client.place_order` (mix branch) must add `marginCoin` + `tradeSide:"open"`, and after submit, query order/detail to resolve `filled` + `priceAvg` synchronously (don't fabricate).
- A hedge-mode close needs `tradeSide:"close"` + `holdSide` — the demo close helper (STEP 8) and any close path must pass these.
- STEP 5 (S-prefix) is dropped. STEP 7 reconciliation change is dropped (sync fill).
- The hedge-mode order-shape fix lives in change-controlled `client.py`; it also fixes the never-exercised live path.

### Implementation outcome (2026-06-09, all 9 steps, 514 tests green — UNCOMMITTED per observation no-commit rule)

- `client.place_order` (mix): adds `marginCoin:"USDT"` + `tradeSide:"open"` (or `tradeSide:"close"`+`holdSide` when `OrderRequest.hold_side` is set), and after submit queries `/mix/order/detail` to resolve real `fill_price`/`filled_quantity` synchronously. Never fabricates a fill (stays `submitted` if detail unavailable).
- `bitget_broker.execute_live_order` generalized with `execution_mode`/`fee_rate` params; `_find_open_position` parameterized by mode. New `close_demo_position()` places a real hedge-mode close then realizes PnL via `pnl.close_position` using the exchange's confirmed exit price; refuses to close locally if the exchange call fails (books stay consistent).
- `gateway._execute_proposal`: split `bitget_demo` into its own branch → broker (no `live_disabled` guard), audit `demo_order_executed`/`demo_order_error`.
- `risk/engine`: added symmetric `account_mode_mismatch:order=demo` (demo order requires `exchange.mode==demo`) + a looser demo data-freshness gate (`stale_market_data_demo`, 1800s vs live 120s, since data collects every 5 min). All other live-only checks stay live-only; `_is_live` untouched.
- `position_manager`: `bitget_demo` exits route to `close_demo_position` (real close); `paper_internal` still local.
- **Three latent bugs found + fixed**, all masked because every live test mocks `place_order`: (1) the mix body omitted `marginCoin`/`tradeSide`; (2) the live path never converted canonical `BTC/USDT` → Bitget `BTCUSDT` (now done at the exchange boundary, local rows stay canonical); (3) the broker recorded the *requested* price as the fill price instead of the exchange's confirmed `priceAvg`.
- Safety: `tests/conftest.py` autouse guard makes `place_order` raise in any unmocked test (keyring creds are NOT sandboxed by the `tmp_path` DATA_DIR patch, so an unmocked demo order would otherwise hit the real API during pytest). Existing paper_trader tests pinned to `paper_internal` (they validate the fallback path); demo path covered by `tests/test_demo_trading.py` (10 tests, all mocked).

### Live end-to-end verification on real Bitget demo (2026-06-09, user-authorized one round-trip)

Called the broker directly (NOT the gateway — kill switch / autonomous loop untouched), min size, opened+closed in one run (net-flat), local rows in a throwaway DB:
- **Open**: real demo order `1448153278595629057`, filled @ **62846.5** — the price came from the exchange's confirmed `priceAvg` (order/detail), not the request. Local order tagged `execution_mode=bitget_demo`, symbol stored canonical `BTC/USDT`, exchange call used `BTCUSDT`.
- Exchange `get_positions()` confirmed `BTCUSDT long 0.0001` appeared.
- **Close**: real hedge-mode close `1448153285251989505` (`tradeSide:close`+`holdSide:long`, `side:buy`), exit 62841.7, realized −0.008 USDT.
- Exchange went **flat** (no dangling position); local round-trip recorded `status=closed`.

Confirms the full path works against the real demo exchange and the three latent-bug fixes are correct end-to-end. Demo account ~198 USDT, BTCUSDT minTradeNum 0.0001.

---

## 2026-06-08 - InstrumentId: three-part unique instrument identity

### Context

Multiple bugs traced back to symbol ambiguity: Binance BTC/USDT spot and Bybit BTC/USDT spot are different data sources but were stored with the same key. Spot and perpetual for the same symbol have different price series but shared the same canonical string. `paper_trader` silently dropped all opportunities because config stored Bitget format (`BTCUSDT`) while opportunities stored canonical (`BTC/USDT`).

Reference systems (OpenAlice, Nautilus Trader) all use a three-part instrument identity: venue/exchange + symbol + market type.

### Decision

Introduce `InstrumentId` frozen dataclass in `aletheia/data/symbols.py`:
```
exchange : str   # CCXT exchange id: "binance", "bybit", "bitget"
symbol   : str   # canonical CCXT: "BTC/USDT"
market_type: str # "spot" | "perp" | "futures"
```
String form: `"binance:BTC/USDT:spot"`. Parsed via `InstrumentId.parse()`.

Config gains `market_data.observed_instruments: list[str]` (e.g. `["binance:BTC/USDT:spot"]`). Legacy `observed_symbols` still works as fallback when the new field is empty.

Storage: `market_snapshots` and `market_ohlcv` tables gain `exchange TEXT` column (additive migration, zero downtime).

The entire pipeline — `CCXTProvider` → `collect_market_snapshots` → `market_observer` → `Opportunity` → `paper_trader` — now uses canonical `BTC/USDT` throughout. Only the risk engine and order router convert to Bitget format (`BTCUSDT`) at the execution boundary.

### Rationale for `exchange:symbol:market_type` vs `exchange:symbol` alone

Spot and perpetual of the same coin have different price series, funding rates, and risk profiles. A perp needs CCXT symbol `BTC/USDT:USDT` (settle currency suffix) while spot needs `BTC/USDT`. Encoding market_type in the identifier makes the CCXT call unambiguous without any inference.

### Consequences

- `aletheia/data/symbols.py` — `InstrumentId`, `to_ccxt_perp()`, `to_canonical()` strips perp suffix
- `aletheia/config/schema.py` — `MarketDataConfig.observed_instruments`
- `aletheia/trading/instruments.py` — `observed_instruments_from_config()`, `observed_symbols_from_config()`
- `aletheia/data/ccxt_provider.py` — accepts `InstrumentId` or `str`; `_resolve_ccxt_symbol()` returns `(ccxt_sym, canonical)` tuple
- `aletheia/storage/models.py` + `migrations.py` — `exchange` column on `market_snapshots`, `market_ohlcv`
- `aletheia/storage/repositories.py` — `exchange=` param on snapshot/OHLCV queries
- `aletheia/server/routes/data.py` — `/market/ohlcv` and `/market/snapshots` accept `exchange=` and `market_type=`
- `aletheia/server/routes/settings.py` — `/settings/symbols` syncs `observed_instruments`; `GET /settings` exposes `market_data` block
- `frontend/src/api/client.ts` — `getOhlcv()` uses `URLSearchParams` to encode `BTC/USDT`
- `frontend/src/pages/Settings.tsx` — read-only "Observed instruments" chips in SymbolsCard
- `tests/test_instrument_id.py` — 45 new tests
- `tests/test_entry_logic.py` — fixtures changed to canonical format

---

## 2026-06-08 - Post-MVP scope expansion: multi-asset observation + Polymarket

### Context

Implementation guide §3.3 originally listed Polymarket as "roadmap only" under MVP constraints. During the full-market-analyzer session (2026-06-08), the user confirmed the system is no longer in MVP state and explicitly authorized expanding scope to include:

- Additional crypto symbols beyond BTC/ETH/SOL (observation + trading)
- US equity observation (yFinance, paper-observation only)
- Polymarket prediction market observation (Gamma API, observation only)
- Roadmap: additional exchanges (xStocks, etc.)

### Decision

**Option A selected by user.** Retain `prediction_observer.py` and `polymarket_provider.py` as shipped. The user's explicit authorization in this turn satisfies the AGENTS.md exception clause ("unless the user explicitly asks for that exact capability in the current turn"). The §3.3 constraint belongs to MVP, which is now superseded.

### Boundaries preserved

- `asset_class="prediction"` opportunities are observation-only — paper_trader gate confirmed to skip them.
- No Polymarket order placement, no live trading on prediction markets, no credentials stored.
- All new agents remain subject to full AGENTS.md safety invariants (audit logs, permission mode, kill switch).

### Consequences

- `aletheia/agents/prediction_observer.py` — new agent, registered in scheduler (every 30 min)
- `aletheia/data/polymarket_provider.py` — new Gamma API provider
- `aletheia/agents/stock_observer.py` — new US equity observer (Mon–Fri market hours)
- Config expanded: `observed_stocks`, `observed_prediction_markets`
- §3.3 is superseded; `docs/governance/policies/implementation_guide.md` remains as historical MVP reference, not as a current constraint.

---

## 2026-06-07 - WebUI: lightweight cockpit IA fix, defer 5-zone shell

### Context

User raised two concrete UI pains: (1) sidebar `.brand` and content `.topbar` baselines don't align — visually jarring; (2) account total value / today PnL hidden inside Paper Trades subtab while Overview's 4 panels are all system-health (Risk/Live/Model/Exchange) — operator's most-wanted data is missing from the homepage. User additionally provided `aletheia_web_ui_implementation_plan.md` (the design doc behind the discarded local 5-zone cockpit), asking for a redesign referencing OpenAlice / TradingView / Bloomberg.

### Decision

Take the lightweight slice: adopt the implementation plan's §6.1 information-architecture goals (Cockpit answers operator's first-glance questions, including PnL) without adopting its §3 five-zone shell, §4 global Command Center, or §5 News Intelligence elevation. Specifically:

- Align `.brand` to `.topbar` (52px shared baseline) — pure CSS, ~5 lines.
- Add `<AccountSnapshot/>` (Total Value / Today PnL / Open Positions / Pending Proposals) at the top of Overview, Paper Trades, and Positions pages. Total Value carries an honest "aletheia-managed only · exchange sync pending" caveat per the portfolio-sync-gap (BACKEND_ISSUES §2.3).
- Introduce minimal `MetricTile` shared component (no `EntityCard`/`EvidenceStack`/`ActionBar` etc — those tie to the 5-zone Inspector, deferred).
- Plan archived at `~/.claude/plans/open-alice-tradingview-bloomberg-ai-ui-temporal-sifakis.md`.

### Reasoning

- AGENTS.md priority #4 (UI honesty) is genuinely violated by Overview omitting account state — fixing that is on-charter.
- AGENTS.md "No Uncontrolled Expansion" forbids importing the 5-zone shell wholesale (Right Inspector + Bottom Timeline + global Command Center are exactly the 18 local files this same day's earlier merge decision discarded).
- north_star §7 places us in stage 0 (stable run); large UI shell rework belongs to stage 1+, not now.
- The implementation plan's IA chapter and the shell chapter are separable. Taking IA without shell gives the user the actual win they asked for at ~1% of the work.

### Consequences

- 4 source files touched (`index.css`, `Overview.tsx`, `PaperTrades.tsx`, `Positions.tsx`) + 2 new components (`MetricTile.tsx`, `AccountSnapshot.tsx`); rebuild of `aletheia/dashboard_static/`.
- Total Value caveat permanently documents the data-source gap in the UI itself, in lockstep with BACKEND_ISSUES §2.3 — the caveat removes only after Exchange Account Sync ships.
- Five-zone shell / Right Inspector / global Command Center / News upgrade explicitly deferred to "stage 0 stable run + stage 1 learning quality validated", whichever later.
- 352 tests still passing.

### Alternatives Considered

- Full implementation per `aletheia_web_ui_implementation_plan.md` Milestones 1-7 — rejected: equivalent to reverting today's earlier merge decision and doing what AGENTS.md forbids at the current stage.
- Inspector-only (medium scope) — rejected: still requires reorganizing all 14 pages to feed the inspector; not justified by the two pains user named.

---

## 2026-06-07 - Merge: adopt remote cockpit, discard local 5-zone shell

### Context

Local branch (`9d91b15`/`edb791b`/`02449f0`) and origin (`ab29932`..`eb50f7f`) independently built an "Agentic Trading Cockpit" touching the same frontend files (`App.tsx`, `index.css`, all `pages/*`). Three-way merge was structurally impossible; one side had to win wholesale. Merge `ace780a` chose origin. This entry is the post-hoc review and ratification.

### Decision

Keep `ace780a` as-is. None of the 18 local-only files (`LeftNav`, `RightInspector`, `BottomTimeline`, `CommandCenter`, `InspectorContext`, `shared/*`, consolidated `Portfolio.tsx`, beefier `NewsIntelligence`, `statusColor.ts`, build artefacts) get cherry-picked back.

### Reasoning

- AGENTS.md priority order ranks user-facing capability at #6; UI shell rework belongs there. State consistency, safety, audit, UI honesty (#1–#4) are not improved by porting any of the 18 files.
- AGENTS.md "No Uncontrolled Expansion" forbids large refactors / styling rewrites — porting a 5-zone shell qualifies.
- north_star_references.md §7 places the project in stage 0 (prove stable run); UI revamp is not a stage 0 task.
- The single local file that maps to a real north-star (`pages/Portfolio.tsx`, consolidated three-tab view) does not actually fix `aletheia-portfolio-sync-gap` — its data still comes from local SQLite only. The real fix is a backend Exchange Account Sync layer; building the storefront before the merchandise inverts the order. Filed as a backend issue (BACKEND_ISSUES.md §2.3) with the consolidated page noted as a candidate UI for after the backend lands.
- News Intelligence local variant conflicts with `issue_register.md::ISSUE-008` (news ingestion should be marked experimental, not expanded).

### Consequences

- `frontend/src/` and `aletheia/dashboard_static/` stay byte-identical to origin.
- Discarded local commits remain reachable through `02449f0` for archaeology.
- The then-current AI handoff (now archived at `docs/archive/legacy/ai-context/session/AI_HANDOFF.md`) was updated to surface the two unfixed north-star gaps that the previous "no remaining open items" line obscured.
- Full audit and 18-file judgement table archived at `docs/design/history/operator-specs/2026-06-07-merge-postmortem-design.md`.

### Alternatives Considered

- Cherry-pick the consolidated Portfolio page only — rejected: storefront-before-merchandise; data-source gap is in backend.
- Revert the merge and rebuild on local cockpit — rejected: would discard ~60 backend hardening files for a UI preference, inverting AGENTS.md priority order.

---

## 2026-06-07 - Gateway: Pure ctypes for Windows process management

### Context

The gateway watchdog (every 5 min via Task Scheduler) and crash-recovery path spawned external processes (wmic.exe, tasklist.exe, powershell.exe, taskkill.exe) for liveness checks and orphan cleanup. Despite CREATE_NO_WINDOW flags, these console-subsystem executables still occasionally flashed Windows Terminal windows on Win11.

### Decision

Replace all subprocess-based process queries with pure ctypes Win32 API calls (kernel32 + ntdll):
- `_pid_alive`: OpenProcess + NtQueryInformationProcess(ProcessCommandLineInformation=60)
- `_list_processes`: CreateToolhelp32Snapshot + Process32First/Next
- `kill_pid_tree`: TerminateProcess + BFS child traversal

### Reasoning

1. Zero subprocess = zero chance of console window allocation
2. Faster: in-process ctypes calls vs. spawning + waiting on external .exe
3. NtQueryInformationProcess(60) is stable API available since Windows 10 1511
4. Keeps POSIX paths unchanged (no regression risk for future Linux support)

### Consequences

- Windows-specific code isolated in `aletheia/gateway/_win32_proc.py`
- Tests must be Windows-only for ctypes paths (pytest.mark.skipif)
- If targeting Windows < 10 1511 in future (unlikely), would need fallback

### Alternatives Considered

- Keep subprocess with CREATE_NO_WINDOW: still occasionally flashes on Win11
- Windows Service (pywin32/nssm): adds external dependency, overkill for current scope
- Named pipe heartbeat: more complex, doesn't solve orphan discovery

---

## 2026-06-06 - AI Workspace Initialized

### Context

Project needs cross-device AI collaboration support for seamless handoff between machines.

### Decision

Adopted standard AI workspace structure with context recovery and handoff protocols.

### Reasoning

Enables seamless project handoff between devices and AI tools (Claude Code, Codex, etc.).

### Consequences

All AI sessions should follow start/end protocols documented in AGENTS.md.

### Alternatives Considered

None — process improvement, not technical tradeoff.

---

## 2026-07-01 — System Operator 命名统一 + earendil-works/pi 研究结论

### Context

项目内长期混用"PI Agent"/"Pi Agent"称呼底层的交易决策+系统监管 agent 组件群。同期研究了
`earendil-works/pi`（GitHub npm 包）是否可复用/借鉴——因名称"pi"与项目历史命名重合，导致
7 次审计误报（A2/A4 误归因到外部 npm）。

### Decisions

1. **统一术语为"System Operator"，废弃"PI Agent"**
   - 原"PI Agent"在代码层从未命名为"pi"——三个组件分别叫 `operator-shell`、`operator_loop`、
     `pi_daemon`，只有最底层沿用了历史残留的 `pi_` 前缀。
   - **决定**：所有活文档（ROADMAP、AI_HANDOFF、DECISIONS、CURRENT_TASK、设计契约等）
     统一使用"System Operator"或"Operator Layer"；历史归档文档（`docs/design/specs/2026-06-10-pi-*.md`、
     `docs/archive/legacy/plans/pi_agent_*.md`）保留原名、只在文件头部加历史标注，不做全量改写（归档文件
     不是维护对象）。
   - `pi_daemon.py` 文件名保留（改名是破坏性重构，触 trust boundary），但文档一律描述为
     "trading decision daemon"（其职责名）而非"PI Agent"。

2. **earendil-works/pi 与 Aletheia 无代码层关联，不引入**
   - earendil-works/pi（`@earendil/pi` npm 包）是终端编程助手 CLI：RPC/SDK/Skills 框架，
     用于让 AI agent 辅助编码，与 Aletheia 的交易决策/系统监管职责完全不同。
   - `operator-shell/src/pi_runtime_adapter.ts` 命名为"pi_runtime_adapter"是历史命名——
     它调用的是 Aletheia Python `/api/operator/internal/llm-session`，**不调用** earendil npm 包
     的任何 API。源码 grep 确认：`operator-shell/package.json` 无 `@earendil` 依赖。
   - **决定**：earendil-works/pi 不引入，不再作为借鉴参考对象。operator-shell 是完全自建
     (~6k 行 TypeScript + ~6k 行 Python），无需外部 agent 运行时。
   - 过去的"crosswalk 文档"（`docs/design/history/operator-specs/2026-06-10-pi-operator-agent-design-crosswalk.md`）
     对照的是内部计划文档 `aletheia_pi_agent_implementation_plan.md` 与双进程设计规格，
     **不是** earendil-works/pi 代码的映射——crosswalk 名称造成误读，已记录为历史文档。

### Reasoning

**名称是认知负债**：7 路审计误报（A2/A4 混淆 TS operator-shell 与 Python pi_daemon）的
直接根因是"PI Agent"一词指向不清楚，修名是降低维护认知成本的最低成本修复。

**不引入 earendil-works/pi 的核心原因**：职责错位（coding harness ≠ trading operator）+
Aletheia 已完整自建（operator-shell 自身已是 LLM agent runtime）+ 引入外部 agent 运行时
与 trust boundary 设计哲学冲突（agent 不可自由 tool-use，所有命令走 Command Registry）。

### References

- 三组件区分图：`docs/design/operator/operator_layer.md` §[必看] 警告框
- memory：`aletheia-pi-agent-full-build-decision`（含 A2/A4 误报根因详述）
- 历史设计归档：`docs/design/history/operator-specs/2026-06-10-pi-operator-agent-design-crosswalk.md`

## 2026-07-11 — Verification evidence must be tool-produced

### Context

The first repository-agent workflow could mark a task passed without executing
the registered command, and completed tasks retained stale implementation next
actions. A fresh session could therefore recover structurally valid but false
or misleading state.

### Decision

Only an explicit `agent workflow task verify` (or `task complete
--command-key`) may produce versioned success evidence. It executes the selected
catalogued command without a shell, records the exact implementation commit and
command digest, and records nothing on failure. Completion requires current-HEAD
tool evidence and a structured handoff, then resets the next action to workflow
bootstrap. CI enforces task and handoff adoption over the full changed range.

### Consequences

Repository continuity is no longer based solely on Agent prose. Historical
evidence remains useful history but is visibly labelled and cannot complete new
work. Trading and runtime behavior are unchanged.

---

## 2026-07-16 — Remove repository-local development task management

**Decision:** Aletheia no longer implements a development-task database,
task packets, worker leases, handoff ledgers, verification evidence records,
or task-management CLI. Agent/session/worktree coordination belongs to the
external orchestrator (currently ORCA or an equivalent tool). The repository
owns only durable project rules, code, architecture contracts, tests, PRs,
decisions, and CI.

**Reason:** The internal mechanism duplicated the external orchestration layer
and expanded until Git and CI had to maintain its metadata. This added a second
source of truth, generated task-only commits, and made squash/rebase behavior a
workflow concern without improving trading-system correctness.

**Guardrail:** Do not reintroduce a repository-local development task store.
A new coordination need must first be solved through the external orchestrator,
a clear agent prompt, isolated worktrees, GitHub issues/PRs, or a static project
instruction/decision document.

---

## 2026-08-05 — Update CLI is a first-class operator workflow

**Decision:** `aletheia update` is not deprecated. It remains the supported
human-operated source-checkout update path, including its verified dashboard
rebuild and gateway restart. The dashboard may offer the same workflow, but it
is not the sole maintained surface.

**Reason:** An update crosses the dashboard process boundary. The CLI can
perform the checked update and controlled restart in one explicit command,
including when the dashboard is unavailable or needs replacing.
