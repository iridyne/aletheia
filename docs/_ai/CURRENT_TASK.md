# Current Task

> **本文件只写意图(计划 / 待办 / 阶段目标),不写运行态事实。**
> Gateway 是否在跑、凭证是否就位、观察期跑了多久、demo 单是否落交易所——这些是
> **每台机器本地的、随时间变化的事实**,写进 git 必然在另一台机器上过期。
> **要知道真实状态,运行 `aletheia doctor`**(只读、零副作用,是事实的唯一来源)。
> 双机切换标准流程见 `docs/_ai/DEV_PROTOCOL.md`。
> **2026-06-16 节奏调整**:取消"专门隔离 72h 不 commit 观察窗"的硬约束。改为
> 边持续开发边用 `aletheia doctor` 间歇性观察闭环健康指标。哪台机器在跑 / autostart
> 是否注册 / gateway 是否在线——仍是随时间变化的运行态事实,**不在此断言**;接手机器
> **第一步一律 `git pull --ff-only` + `aletheia doctor` 取真实事实**(事实唯一来源是机器
> 本身,不是本文叙事)。

---

## 当前任务:全项目 Bug/SDD 审查 — 修复阶段进行中

全项目 bug/SDD 审查**已完成**（`#1`–`#180`）；现处于**修复阶段**，分支 `fix/bug-review-fixes`（已推送 origin）。

接手入口：

- 详细问题台账 + 权威「Fix Progress」修复日志：`docs/_ai/FULL_BUG_REVIEW_2026-07-06.md`
- 短交接说明：`docs/_ai/BUG_REVIEW_HANDOFF_2026-07-06.md`

修复进度（2026-07-07）：**已修 42 findings / 11 提交（B1–B6 + B10a），9 个 HIGH 全部处置**；全 SDD spec 套件 588 passed / 0 failed + 15 个新回归测试文件。剩余按批续做：B7 组合/PnL · B8 前端（需 npm build）· B9 数据/分析/doctor/cli · B10 文档尾；另 ~15 个耦合/基建项暂缓（市场类型数据模型迁移 / operator 鉴权 #40,41 / #109,143,102-pending,17 / 运行时性能 #90,147,134），各记原因于台账。

审查进度（2026-07-06）：**全项目覆盖审查已完成**，台账范围 `#1`–`#180`。

- 三轮只读并行审 + 对抗性核验，叠加原 #1–#131 手工审：
  - `#132`–`#142`（9 路）：未覆盖后端（控制路由 / gateway 生命周期·边缘 / 自更新 / server app / 持仓退出 / 交易所同步·broker / 底层 client）。
  - `#143`–`#162`（16 路）：信任关键 + 决策链 + 数据/分析（risk 守卫链 / execution 咽喉 / permissions / self-heal / config·secrets / storage / agent_shell / agents / data / analytics 全 28 文件 / model_provider / universe·trading）。
  - `#163`–`#180`（12 路收尾）：前端逐页（13 页 + 5 图表 + 全部组件 + 共享 App/client/types/lib）+ knowledge / doctor 逐行 / cli / runtime_continuity·uta。
  - 合计覆盖前端端到端 + 全部后端子系统；台账 Coverage status 已标「full-project pass complete」。共 9 条候选被核验推翻，记在「Investigated and refuted」，勿再报。
- **下一步不再做广扫**（无剩余未扫描区）。要么开修，要么仅在「新增子系统 / 新数据源 / 新学习消费者 / 新前端页」时做定向补扫。
- 最高优先修复候选（HIGH）：`#159`（守卫晋升用污染数据可凭假证据晋升硬拦截守卫）、`#145`（掉线后自愈恢复任务挂错事件循环→永久暂停不自愈）、`#137`（tunnel 鉴权漏风控设置）。全部分诊见 `BUG_REVIEW_HANDOFF_2026-07-06.md` 的 Fix Triage Hints。
- 动手前须先确认：`#142`（Binance 绕代理）疑似已在 `iridite/system_proxy_support` 修复；`#156`（Coinalyze OI）须先核实线上 `/open-interest` 字段名。

当前意图：

- 修复阶段进行中。逐批选择问题，先声明边界，再写聚焦回归测试，再改代码；触
  `risk/`·`execution/`·secrets·live·gateway lifecycle 的改动带定向回归、需 owner 显式授权。
- 每批一次提交（显式 `git add`，不 `-A`）；修复期间不 push/restart/enable live，推送/重启由 owner 指令触发。
- 修学习/分析类消费者时套「合成平仓 + clean_window 过滤」自检（#159/#160 同类）。
- 下一批优先级建议：B10 文档尾（最快）→ B9 → B7 → B8（留给需 npm build 的专门轮次）→ 数据模型迁移/operator 鉴权（基建专门轮次）。

---

## 当前诊断入口:Opportunity Flow Ledger

近期排查“机会为什么没有变成交易”时，优先使用统一入口：

```powershell
uv run aletheia flow audit --hours 24
uv run aletheia flow trace --opportunity-id <id>
```

排查特定问题时优先加过滤：

```powershell
uv run aletheia flow audit --hours 24 --stage debate_downgraded
uv run aletheia flow audit --hours 24 --verdict missed_alpha_suspect
uv run aletheia flow audit --hours 24 --symbol TRX/USDT
```

它把 Trade Funnel、Debate Impact、Thesis Lineage、Sizing Trace 等只读审计摘要合到一个
机会生命周期视图里。旧专项命令仍保留，用于需要深挖单一环节时查看细节。

候选供给不足时，`trading_context` 会写 `candidate_supply_snapshot`，连续 2 个 cycle 的
`executable_count < 3` 会在 `/api/operator/recommendations` 中出现
`candidate_supply_drought`，并建议刷新机会池（`run_market_scan`）。

边界保持不变：flow 只读重建，不调用 LLM，不跑 ranker，不重跑 guard/debate/risk/execution，
不改交易行为；缺字段保留 `insufficient_data`。

---

## 当前阶段:Phase 0 — 边开发边观察闭环跑通

**目标(机器无关,长期有效)**:让闭环在 `bitget_demo`(真实模拟盘)形态下能稳定跑起来,
证明"闭环在运转"(不是盈利)。**不再要求专门隔离 72h 不 commit**——在持续开发过程中
间歇性观察这些指标即可。

**闭环健康参考指标(累计观察,不必同一窗口内同时满足):**
- 网关能在多日尺度连续运行(health 非 dead);`code_drift` 自愈重启是预期行为,代表代码
  改动后看门狗成功换上新代码,不是失败信号。
- market_observer 每 ~15min 产 opportunity(>70% 非 error)。
- paper_trader 在累计若干次开仓周期后产生 fill;5 个 fill 是经验性"足够说话"的样本量,
  不是 binary gate。
- position_manager 有平仓行为(至少 2 次,确认 close 路径不仅仅是开仓后僵死)。
- post_trade_reviewer + risk_critic 有产出(knowledge_items 累计增长)。
- demo 真单在 Bitget 侧可见(`get_open_orders`/`get_positions` 能查到)。

> **判据性质**:衡量"闭环在运转",**不衡量盈利**(盈利是 Phase 1+ 的独立问题)。
> **观察项(非判据)**:持仓集中在单一标的是当前阶段正常现象,多标的分散是 Phase 2A。

**开发节奏**:可以正常 commit / push / 改代码;每次较大改动后用 `aletheia doctor`
扫一眼真值,看上述指标是否仍在累积。改动让某项指标退化时再回头调。代码改动后记得
`uv run aletheia gateway restart --no-browser`,scheduler 跑的是内存中的旧代码。

**大批量修复前置阅读**:如果下一次任务类似 `discussion.md` 这种跨前端、后端、测试、
文档、运行验证的长问题,先读 `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md`。
该文档总结了本轮 20+ worktree 批量修复的拆解、量化、性能、pytest、worktree 收尾和
owner 决策边界经验。

**AI 默认测试入口（2026-07-03）**:修 bug 时默认先跑 `.\scripts\test_ai.ps1`
（上次失败 + `fast` 层）。需要更大信心时再跑相关 `integration`/`e2e`/`slow`，
最后才用 `pytest-xdist` 并行或全量测试。完整规则见
`docs/operations/PYTEST_OPTIMIZATION_GUIDE.md`。

**前后端通讯通路规则（2026-07-03）**:dashboard 不能把所有请求都当成同一类加载。
连接探活走 `/api/ready`，业务健康走 `/api/health`/`/api/status`，ticker/建议类请求
作为后台轮询，不驱动全局加载条。完整约束见
`docs/_ai/RUNTIME_COMMUNICATION_LANES.md`。

**诊断默认入口（2026-07-03）**:排障时先跑 `uv run aletheia doctor --verbose`
（或机器消费用 `--json --verbose`）。需要留给人/AI 复盘时用
`uv run aletheia doctor --bundle data/diagnostics/doctor.json` 生成脱敏诊断包。
普通日志按 50MB 分段并尽量保留多代，MVP/Round 1/Round 2 阶段优先保留问题现场；
doctor / gateway logs 的日志尾部输出必须走脱敏读取，不能把 token/secret/authorization
原样展示给人或 AI。

**交易诊断入口（2026-07-04）**:`uv run aletheia doctor --deep` 现在是交易问题的
第一诊断入口，会用 compact summary 指向 Alpha / Trade Funnel / Sizing / Thesis Lineage
哪一块最值得看。
详细报告走只读 standalone 命令：`uv run aletheia funnel audit --hours 24` 和
`uv run aletheia sizing trace --hours 24`。Guard 是否压制候选流走
`uv run aletheia inspect guard-impact --hours 24`（可加 `--json` 给机器消费）。Thesis 样本级复盘走
`uv run aletheia thesis audit --days 7` 和 `uv run aletheia thesis records --days 7 --json`；
Trades & Positions 详情 drawer 会在 Causal Chain 下方显示对应 Thesis Lineage。
生成 thesis 的上游可以用 LLM 或规则/模型；Thesis Lineage v1 只还原和评估已经留下的
thesis / reasoning / evidence，缺字段就标 missing/insufficient_data，不新增 LLM 调用，
不事后补写理由。这些 audit 只做诊断，不自动调仓位、风控、Kelly、杠杆、ranker、prompt
或 persona。

**Guard Impact Audit 边界（2026-07-04）**：`inspect guard-impact` 只读 DB、active guard
markdown/frontmatter 和已有 shadow/forward outcome。它用来判断 active blocking guards 是否疑似
过度压制候选流，verdict 只有 `protective`、`overbroad_suspect`、`insufficient_data`。它不会
禁用/修改 guard，不改 ranker/System Operator/risk/execution/live，不调用 LLM/HTTP/exchange。
如果结果是 `insufficient_data`，继续观察或转向 unsupported candidates / debate；不要用它自动改
交易行为。

**Thesis Lineage V1.1 观察窗口（2026-07-04）**:v1 合并运行后，不要马上升 v2。
接下来 7-14 天只回答一个问题：现有交易判断链有没有留下足够完整、可审判的数据。
计划见 `docs/plans/thesis_lineage_v1_1_observation_window.md`。V1.1 只做三件事：跑起来、
看 `universe -> opportunity -> decision -> proposal / skip -> shadow / position -> outcome`
是否能串起来、修断链和缺字段。禁止新增 thesis generator、LLM normalizer、prompt 优化、
自动调参、新 UI、交易行为改动、新 `ThesisRecord` 表或决策前历史 thesis 检索。观察 JSON
快照属于本机运行事实，写到 `data/diagnostics/` 这类 gitignored 目录，不写进 git 文档。
`aletheia thesis audit --days 7 --json` 已内建 `data_completeness` 和
`raw_table_completeness`，`aletheia thesis snapshot --days 7` 会把同一份 audit JSON
写到 `data/diagnostics/`，`aletheia thesis snapshots` 汇总最近保存的快照。下一轮观察优先
看这两个区块，再决定修哪个 provenance 写入点。节奏：部署后先跑 `doctor --deep --json`，
然后每天跑一次 `thesis snapshot --days 7`；如果 V1.1 期间修了 provenance，修复效果只看修复后
新写入的行，不用修复前历史行判断。

**Thesis Lineage 链路完整性修复（2026-07-04）**：已落地 `7df8889f`，计划见
`docs/plans/thesis_lineage_integrity_repair_plan.md`。后续观察不要只看 raw
`missing_opportunity_id` 总数，因为其中混有全局 skip/watch。优先看：
`link_required_missing_opportunity_id`（真正需要机会链的 PI 行缺父 ID）、
`global_no_target_count`（合法的全局 skip/watch）、`stale_missing_pnl_pct`
（超过 shadow 结算窗口仍缺结果）、`overdue_pending`（forward outcome 到期未结算）。
修复是否有效，只用修复后新增行判断；历史缺口中无法确定性回填的部分保留为历史债务，
不要用 LLM 或 symbol/time 猜测回写。

**Learning 闭环当前边界（2026-07-03）**:交易历史 → case study → lesson → policy →
draft skill 的代码链路已打通，旧 trading memo 会按 7 天半衰期降权。draft skill 仍需
人工审核后才可启用；后续不要把 draft skill 自动接入 risk/execution/live/credential/order
路径。

**SDD 文档对齐工作线（2026-07-03）**:Learning 范围已按 `discussion.md` 金标准完成
契约/API/spec 注释对齐。下一步才扩展到全项目 SDD 文档漂移扫描；不要把历史 plans/issues
里的旧问题描述当成当前事实，先对照代码和现行 L2 契约。

全项目第一批已处理规则引擎删除后的当前契约漂移、API 端点漏登记和 System Operator
术语漂移。后续继续按 `docs/_ai/SDD_GLOBAL_ALIGNMENT_AUDIT_2026-07-03.md` 的清单推进：
route-vs-api_reference、config-to-UI、spec missing/xfail、以及历史/当前文档边界。

> **结构性阻碍已清除(意图记录,2026-06-24)**:最小赚钱循环
> (opportunities→paper_trader→signal_pending→PI→proposal→risk→execution)曾被
> **五层叠加阻碍**卡死(OKX contractSize 单位 / PI 余额幻觉 8 bug / OKX 跨 loop /
> demo 日损 $1 误熔断 / signal 重复→"数据损坏"skip)。这些是**代码层面的结构缺陷**,
> 现已逐层修复并补回归测试(详见 `DEVLOG.md` 2026-06-23→24 条 + `DECISIONS.md`)。
> 此后 Phase 0 健康指标应能正常累积——**但"现在是否真在累积"仍是运行态事实,以
> `aletheia doctor` 的 `loop.profit` 探针为准,本文不断言**。实验判决记账见
> `EXPERIMENT_PROTOCOL.md`:修复生效前(污染期)的 skip/proposal 数据不计入策略评估。

## 最新完成：Phase 2A+ 剩余四项收口 — 2A/2A+ 全 CLOSED（2026-07-01）

owner「尽量把 2a 完全完成,不要再遗留任何项目」。四项 PARTIAL/PENDING 一次做完
（`f093e36` 实现 + `8cea488` 文档,push master）：
- **2A+.2** designer 写 applicable_regimes（`_derive_regime_targeting`；**须写 JSON 数组**,
  否则 ranker `json.loads`+`isinstance(list)` 解析失败静默丢弃使过滤失效；SP-S04 升 L2b）。
- **2A+.4** 前端 Direction 列 + drawer（`fmtRegimes` 解析 JSON）。
- **2A+.9** `family_quota_by_regime` regime 感知配额 + `resolve_family_quota()` + Settings UI（UP-16）。
- **2A+.11** `score_volatility` 接 atr_4h/bb_width_4h 4h 多 TF 确认（UP-15，反向验证防死码）。
- 验证：conformance 493→496 全绿；full suite 1815 passed / 0 failed；build 通过；doctor healthy。

**唯一剩余 = 2A+.10 端到端 short 验证（非代码,运行观察项）**：需 bear/volatile regime + bull
翻转真实出现才能观察完整链路 + 短过滤。**阶段进入 owner 定「先跑攒干净窗」——不再堆 phase,
观察集中做空有无 edge。运行态证据（regime/持仓/胜率）留 `data/observation/`（gitignored),
以 `aletheia doctor` 为准,本文不断言。**

## 上一批完成：Scanner/Observer 审计修复 P0–P3（2026-07-01）

- 两份审计报告 → plan（`docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md`）→ 实施。
  宽 fan-out 核实 workflow 因 workspace rate-limit 全灭，改手动核实，查出报告 **6 处根因偏差**（见报告 §0）。
  分支 `fix/scanner-observer-audit-fixes`。
- **最高杠杆**：`service.py:256` forward-outcome direction 同源修复（一处消解 momentum 54% direction=None
  + event 0 条 forward outcome + 胜率统计双重失真）。其余 P0-P3 见 `DEVLOG.md` / `AI_HANDOFF.md`。
- spec `UP-01~13` 全 GREEN；全量 **1815 passed**；无新增 doctor WARN。
- **defer 到 ROADMAP**（非遗漏，有理由）：US-16（真 4h 特征接入 scorer，属增强）、
  US-12（all_scores 入库，待有消费者，否则违反 data_utilization 契约）。

## 更早完成：双层模型路由 + System Operator 重命名（2026-07-01）

- **双层模型路由**（commit `e5a8010`）：`deep_provider` / `fast_provider` 双通道，
  11 个 deep call site + 5 个 fast call site 已注入，前端 Settings 加 Routing Role 下拉。
  SDD: 5 个 MR-* claims 全 GREEN。
- **术语统一**：文档层"PI Agent"→"System Operator"。代码层文件名不变（触 trust boundary）。
- **earendil-works/pi 研究结论**：外部 npm，coding CLI，与本项目无代码关联，不引入。
  详见 `docs/_ai/DECISIONS.md §2026-07-01`。

---

## 当前活动工作线:OpenAlice TaG 借鉴系列(2026-06-29 起)

研究报告 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md` + 借鉴清单状态卡 `docs/issues/TAG_BORROW_BACKLOG.md`。分支 `worktree-openalice-tag-research` 待 owner 审核 merge(5 commit:P0-1 + Vibe Trading 核查 + P1-2 + L2 docs + L4 docs)。

- **P0-1**(已实施 `b8ed990`)— TradeProposal commit_hash + parent_hash 全局单链 hash 基础设施(不触 trust boundary)。SDD:DM-S08/S09/B06/C02。
- **P1-2**(已实施 `31eb1a9`)— position_adopted audit event 链入 hash chain。SDD:PL-ADOPT-09。
- **P0-2 待 owner 批准** — 执行后 state_after 账户全量快照(`gateway.py` audit 写入点加字段;触 `execution/` trust boundary;预计 ROI 最高的剩余项)。
- **P0-3 待 owner 批准** — Guard 责任链拆分(`risk/engine.py:77-332` 20 个 check 拆 `risk/guards/`;trust boundary 红线最严;**严禁趁机塞 market-state 检查**,见 north_star.md §10.3)。
- **P1-1 待 owner 批准** — reject 与 push 同结构(三路拒绝 audit 补 hash 字段)。

明确拒绝的借鉴项见 `docs/_ai/DECISIONS.md` 2026-06-29 条目。

## 并行开发线:账户级仓位发现 + 纳管("接管即纳管")

> 所有者立场决策:Aletheia 接管账户后全账户仓位(含系统外仓位)统一交由 AI 管理。
> 设计与治理见 `docs/_ai/DECISIONS.md`(2026-06-12 两条),进度叙事见 `docs/_ai/DEVLOG.md`。
> 这是把 ROADMAP Phase 6 的 reconciliation 思想提前、以 demo 优先落地。

- **P1 发现(只读)** — 完成:`position_reconciler.classify_positions()` +
  `GET /api/positions/reconciliation` + 前端 Account Reconciliation 卡片。
- **P2 纳管(合成诚实因果链)** — 完成:`source` 列 + `adopt_position()`;trust §1.1 已修订。
- **P3 定时任务 + operator 命令 + 手动触发** — 待实现。demo 自动纳管任务**默认开启**
  (`auto_adopt_enabled` 默认 true,见 DECISIONS.md 行为警告);live 纳管/平仓需人工确认。
- **P4 live 人工确认平仓 + Full 模式接缝** — 待实现(最敏感,触 `execution/`,最后做)。

> **合并边界**:与 Operator Layer 一致——可以正常合并到 `master`;P3/P4 因触及
> `execution/` / 凭证 / live 路径,改动需用户显式授权(trust 边界,不依赖观察门)。

## 下一步可做：Strategy Pool（已完成前期调研，2026-06-17）

> 调研文档：`docs/research/RESEARCH_SHORT_AND_STRATEGY.md`（现状分析）+
> `docs/plans/PLAN_REGIME_STRATEGY_SHORT.md`（实施方案）

**调研结论摘要**：
- `StrategyRecord.parameters`（JSON blob）不含 `direction_affinity` / `regime_tag`，需扩展
- 10 态状态机已完善，扩展不需要改状态机本身
- ranker 对策略的方向性过滤是空白，`direction_bias` 从 opportunity 独立推断

**实施方案已写在 `PLAN_REGIME_STRATEGY_SHORT.md`**，分 3 阶段：
- Stage 1：给 StrategyRecord 加 `direction_affinity` + `regime_tags` 字段（`_ADDITIVE_COLUMNS` 路径，安全）
- Stage 2：Ranker 中引入 regime_match 得分项（扩展 `_compute_trade_score`）
- Stage 3：strategy_designer 生成时写入方向偏好（需探索 strategy_designer agent）

**前提**：上述 Stage 1-2 不触及 trust 边界（`risk/`、`execution/`），可直接实施。

## 接手机器第一步(机器交接后)

0. **接手机器开机第一步**（机器无关流程，2026-06-17 更新）:
   1. `git pull --ff-only`（或处理 diverge：`git pull --rebase origin master`）
   2. `uv run aletheia doctor`（本机 GBK 控制台需 `PYTHONIOENCODING=utf-8`）—— 取真实事实
   3. 需要本地跑闭环时：`uv run aletheia gateway start`；
      若需无人值守自动复活：`aletheia gateway enable-autostart`
   4. `uv run pytest -q` 确认 1338+ 测试基线绿

## Edge Lab — 完成 (2026-06-14, commits 9fe5c5f / 8532280 / c90d2eb / ab2ba5c / ab4a522)

信号→PnL 可量化消除层全量落地：P0–P2.5 六项全部完成。
- Shadow Trades 持久化(Top-5 反事实候选入库)
- 可配置滑点(5bps，adversarial 填价)
- BTC benchmark(开仓/平仓时抓 BTC 价，alpha/beta 分离用)
- Regime tag(每笔交易的宏观环境标签)
- Family Scorecard API(`GET /api/analytics/family-scorecard`)
- 11 个新测试全绿；3 个预存失败与本批次无关。
- 待办:P3(虚拟部分 TP、家族自动杀闸、Edge Lab 仪表盘 UI)——留待后续授权。

## 9域数据收集层 P0 — 完成 (84a66fd, 2026-06-14)

**已实现**: microstructure/derivatives/macro 3类 collector + normalizer + context_builder +
scheduler job (2min tick) + 5张新 DB 表 + repositories + features.py 占位符填空 +
market_observer 追加 enriched context block + 16个离线单元测试。

**效果**: score_derivatives() scanner 数据 non-None 后自动激活；LLM prompt 追加
Microstructure/Derivatives/Macro 三表段，平均每个 symbol 多 ~200 token 高密度信号。

**下一步 (P1, 可选)**:
- OI delta 跨快照计算 (现在 oi_change_1h_pct 恒为 None，需用前后两个 OI 快照算)
- 跨交易所 premium tracking
- 新闻 RSS + LLM 摘要 (Domain 6)

---

## 代理配置(已落地,2026-06-14 commit 28eb357)

全局 HTTP 代理已接通。要让 market_data 恢复正常:
1. Settings → Infrastructure → Network Proxy → 输入代理 URL(如 `http://127.0.0.1:7897`)→ 点 Test 验证连通 → Save。
2. 重启 gateway(proxy 已在 lifespan startup 自动 apply;watchdog 重启后同样生效)。
3. `aletheia doctor` 应显示 `✓ network.proxy  proxy configured, reachable (XXms)`。

---

## 数据质量自检 + PI Agent 自愈 — 完成 (5e5f4fe, 2026-06-21)

- `aletheia doctor`: 新增 `data.ohlcv` + `data.signal` probe
- `aletheia audit data [--json]`: per-symbol bar count + signal win_rate
- `aletheia data backfill`: 批量 OHLCV 回灌
- PI Agent Layer 1 注入 `data_health`; signal_status=degraded 触发自愈 action
- 当前状态: data.ohlcv: ok (8/8 ≥200), data.signal: warn (win_rate 36.7%)

---

## 待办(下一步可做的事 — 不含"现在状态如何")

0. **Self-Evolution Tier 2（计划 2026-07-01）**：
   - 前置检查：`calibration_stats()` 可见 ≥50 条 settled outcomes
   - 实施内容：新增 `EvolutionSynthesizerAgent`，APScheduler UTC 03:00 每日触发，
     按 scanner_family 分组调用 LLM 归纳量能/动量规律，写入 research_note / guard_candidate
   - 详细设计：`docs/plans/self_evolution_plan.md` § Tier 2
   - Tier 3（2026-08-17）：确定性 scanner rule 提炼，见同文件 § Tier 3

1. **持续闭环观察**:每次较大改动后用 `aletheia doctor` 扫一眼,看 fills/positions/
   knowledge_items 是否仍在累积;退化才回头查根因。
2. **并行开发**:可以同时推进 Operator Layer Track B、Persistence/Recovery 集成、
   或本节列出的任何能力线;不再受"Phase 0 门未绿"约束阻塞。
3. **Windows 无人值守加固**:autostart 看门狗 schtasks 注册可靠性、gateway 重启 / 注销 /
   整机重启后的自动复活。这是真无人值守的前提,但不是闭环验证的前提。
4. **经验闭环质量门**:post_trade_reviewer / risk_critic 仅在 LLM 真产出 + 有真实活动时
   升 `high_confidence`,防噪音进检索。
5. **Persistence/Sync/Recovery 集成**:子系统代码已完整实现,下一步 = 将
   `GatewayRecoveryService` 接入现有 `gateway/lifecycle.py` 启动流程、为 paper_trader
   的 fill/position 流程接 `GatewayStateStore` 双写、为 agents 接入 `CheckpointStore`
   实现可恢复长任务。专用集成测试待编写。

## 下一阶段(可并行启动)

见 `docs/status/ROADMAP.md`。Phase 1 = 学习质量诊断;Phase 2A = 结构化策略信号;
Phase 2B = Agent 协调链。Phase 6 (Live) 仍需用户显式授权才启动(trust 边界)。

---

## Pi Agent (Operator Layer Track B) — 实现状态摘要

> 设计源:
> 1. `docs/status/ROADMAP.md` § Cross-Cutting Track: Operator Layer
> 2. `docs/design/specs/2026-06-10-pi-operator-agent-dual-process-design.md` (详细蓝图)
> 3. `docs/design/specs/2026-06-10-pi-operator-agent-design-crosswalk.md` (覆盖矩阵)
> 可直接在 `master` 推进;触 trust 边界(execution/凭证/live)的改动需显式授权。

### 架构决定

- **双进程**:Python gateway = 交易真相层 + 命令执行;Node `Operator Shell` = PI-style session runtime + operator loop。
- **权限双层**:worker/trading 保留;独立 `operator.permission_mode`(ReadOnly/Research/PaperAuto/AllowEditing/Auto)。
- **Command Center** 为主操作面;`Ctrl+K` 仅 launcher。

### Stage 1 已完成的核心能力(2026-06-11 截止)

- **37 typed commands** + 9 proposal-only specialists(strategy promotion, prompt/schedule/risk/behavior change, extension enablement)
- **Recovery contract**:budgeted multi-branch folding, heartbeat intent fallback, in-flight task re-entry, persisted active branch truth
- **Supervision tiering**:10s heartbeat → 30s session/task scan → 5min subsystem review → 15-60min operational control(12 条自动接线)
- **6 specialist delegations**:dreaming, post_trade_review, strategy_evaluation, strategy_design, risk_critique, portfolio_snapshot
- **Session tree**:nested branches, session-kind context profiles, parent trace folding, user-created workspace branches
- **Extension sandbox**:manifest draft → sandbox validation → enablement proposal → enabled slash dispatch
- **Command Center**:SSE event feed, authoritative command preview, structured trace/result cards, context inspector, cross-session search
- **Inspectable working memory**:session JSONL + task/todo/scratch/extension/report Markdown artifacts

### 剩余缺口

- 更广的 scheduled scan/review opportunities(cadence 覆盖率)
- 更深的 PI-style multi-turn branch runtime(long conversation within branches)
- Node shell 与 gateway 进程编排(当前 Node shell 尚未作为独立进程与 gateway 协同运行)

---

## 历史交接

详细的逐次变更记录见 `docs/_ai/AI_HANDOFF.md` 与 `docs/_ai/DEVLOG.md`。本文件只保留
**当前阶段意图**;已完成的诊断/修复叙事不在此堆积。
