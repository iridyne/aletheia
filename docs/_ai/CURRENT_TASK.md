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
