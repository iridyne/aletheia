> **⚠️ 已归档(2026-06-26)** — 本报告是 2026-06-19→06-23 的点对点快照。4-agent 对 HEAD 逐条核实:列举项绝大多数已修或已被 `FIX_PLAN_2026-06-26.md` / `frozen_optimization_backlog.md` 捕获;仍开放且未被别处记录的残余项已迁入 `../BACKEND_ISSUES.md` §19。当前开放 backlog 以 FIX_PLAN 为准。

# Pipeline Gaps — 断管道登记簿

> 审计日期: 2026-06-23
> 来源: 三维审计（数据管道 + 文档覆盖 + 前后端一致性）
> 用法: 每条标注当前状态 + 推荐方向 + 涉及文件。优先级 A>B>C。

---

## A 级：数据沉默堆积（写入无有效消费）

### A1. ps_lessons ↔ knowledge_fts 双轨脱钩

**现状**: 系统有两套独立的"经验记忆"：
- `ps_lessons` 表（LessonStore）← dreaming_agent 写入
- `knowledge_items` + `knowledge_fts`（FTS5 索引）← post_trade_reviewer/risk_critic/insight_synthesizer 写入

`retrieve_lessons()` 只从 `knowledge_fts` 读取。ps_lessons 的数据永远不会出现在任何 Agent 的 prompt context 中。

**影响**: dreaming_agent 产出的 lesson 进入了一个死胡同，不影响未来任何决策。

**涉及文件**:
- `aletheia/learning/lessons.py` — LessonStore CRUD
- `aletheia/knowledge/retrieval.py` — retrieve_lessons()（只读 knowledge_fts）
- `aletheia/agents/dreaming_agent.py` — 同时写两套

**推荐方向**: 废弃 ps_lessons，统一到 knowledge_items 路径。或将 LessonStore 改为 knowledge_items 的前端 wrapper。

---

### A2. ps_skills 无生产者（SkillStore 空壳）

**现状**: `SkillStore` 定义了完整的 create/activate/deprecate 生命周期，但：
- 无任何 Agent/Scheduler 调用 `SkillStore().create()`
- 唯一入口是 `server/routes/persistence.py` 的手工 REST API
- 这是 Self-Evolution Tier 2/3 的预留架构，当前完全惰性

**影响**: 技能学习系统有 schema 无内容。UI "Skills" 页面永远空。

**涉及文件**:
- `aletheia/learning/skills.py` — SkillStore
- `docs/plans/self_evolution_plan.md` — Tier 2 计划（2026-07-01）

**推荐方向**: 等 Tier 2 实施。当前状态可接受（预留架构），但 UI 应标注"coming soon"而非空白。

---

### A3. liquidation_events 采集但不进风控

**现状**: `data_collectors` 每 2min 从交易所拉取清算数据写入 `liquidation_events` 表。消费方仅有：
- `context_builder.py` → 注入 market_observer 的 LLM prompt（间接影响）
- CLI `inspect` 命令（手工查看）

**影响**: 大规模清算事件（市场极端波动信号）无法直接影响 risk_engine 的批准/拒绝决策。

**涉及文件**:
- `aletheia/agent_runtime/scheduler.py:_run_data_collectors`
- `aletheia/data/context_builder.py:build_market_context_from_db()`
- `aletheia/risk/engine.py`（不读取 liquidation_events）

**推荐方向**: 在 risk_engine 增加一个可选的"极端清算"check，当近 N 分钟清算量超阈值时提高 risk_score 或缩减 position sizing。优先级中（实验冻结期后）。

---

## B 级：功能壳存在但静默失效

### B1. detect_contradictions() 是 stub

**现状**: `knowledge/manager.py:detect_contradictions()` 明确标注 `IMPLEMENTATION_STATUS: stub — returns empty for MVP`，永远返回 `[]`。

**影响**: 知识库中可能存在矛盾的 guard/lesson/research_note（例如"做多 BTC"与"不做多 BTC"同时存在），系统无法检测。

**涉及文件**:
- `aletheia/knowledge/manager.py:302-310`

**推荐方向**: 实现基于 embedding 或关键词的基础矛盾检测（Tier 2 级别工作）。短期内 UI 不应展示"0 contradictions"为正面信号。

---

### B2. serenity 4表只写不消费（dreaming 产出无决策价值）

**现状**: dreaming_agent 写入 `serenity_risk_gates`、`serenity_replay_snapshots`、`serenity_post_mortems`、`serenity_profile_snapshots`，但：
- 无任何决策 Agent 读取这些表
- 仅被 `server/routes/data.py` 暴露给 UI 展示
- 设计意图是"离线回顾"，不参与实时决策

**影响**: dreaming_agent 的 serenity 产出对交易改善无直接贡献，仅供人类浏览。

**涉及文件**:
- `aletheia/agents/dreaming_agent.py`
- `aletheia/server/routes/data.py`（只读展示）

**推荐方向**: 这是设计意图（离线回顾层），非 bug。但若 serenity_risk_gates 中的"风险门"能转化为 trading_guards，则可闭环。低优先级。

---

## C 级：已有能力但不可达（前端未接入）

### C1. 6 个 analytics 端点无前端 UI

**现状**: 后端 `analytics.py` + `data.py` 实现了以下高价值分析端点，但无对应前端页面：

| 端点 | 功能 | 价值 |
|------|------|------|
| `/api/analytics/strategy-evidence` | 每策略证据快照 | 晋级决策依据 |
| `/api/analytics/backtest-paper-drift` | 回测 vs 实纸偏差 | 策略退化检测 |
| `/api/analytics/exit-policy-advice` | 出场策略诊断 | 持仓管理改善 |
| `/api/analytics/trade-paths` | MFE/MAE 路径分析 | 止损/止盈优化 |
| `/api/analytics/family-scorecard` | 策略族评分卡 | 族级资源分配 |
| `/api/analytics/shadow-trades` | 影子交易 | 未执行机会回顾 |

**推荐方向**: 在 StrategyLifecycle 或 Charts 页面嵌入这些 analytics 组件。

---

### C2. 3 个 Config 类无前端控制

| Config | 字段数 | 影响 |
|--------|-------|------|
| `RegimeConfig` | 4 | enabled/bias_threshold/short_enabled — 市场状态机开关无法通过UI控制 |
| `GuardConfig` | 5 | enforce_in_paper_ranker/auto_promote — guard系统行为无法调整 |
| `MarketAwareExitsConfig` | 9 | 所有市场感知出场参数 — 后端schema有但settings.py也未接入 |

**推荐方向**: 在 Settings.tsx 中新增对应段落。MarketAwareExitsConfig 需先在 `settings.py` 的 TradingConfigUpdate 中新增字段。

---

### C3. persistence 全套数据管理无 UI

**现状**: `/api/persistence/` 提供 backup/export/import/lessons/skills/policies 共 16 个端点，全部无前端页面。

**推荐方向**: 低优先级。这些是运维型功能，可通过 CLI 或 API 工具完成。若需 UI，放在 Settings 的"Data Management"子页面。

---

---

## D 级：功能代码完整但默认关闭/未接通决策路径（深度审计 2026-06-23）

### D1. Confidence Calibration 报告未注入 LLM prompt（SHELL）

**现状**: scheduler 每 6h 运行 `build_calibration_report()`，写入 knowledge_items。`trading_context.py:310` 读取报告放入 `layer2["calibration_feedback"]`。但 `trading_decision.py` 的 `_build_assessment_prompt()` **从未读取或渲染该字段**。

**影响**: 系统花 LLM tokens 生成校准报告，但这些数据对 PI Agent 的 conviction 评估完全无影响。

**涉及文件**:
- `aletheia/agent_shell/trading_context.py:310-314` — 写入 context dict
- `aletheia/agent_shell/trading_decision.py` — prompt 构建函数（缺失消费）
- `aletheia/analytics/confidence_calibration.py` — 报告生成

**修复方向**: 在 `_build_assessment_prompt()` 中添加 calibration_feedback 段落渲染。

---

### D2. MarketAwareExits 默认关闭，三条规则从不执行（PARTIAL）

**现状**: `MarketAwareExitsConfig.enabled` 默认 `False`（schema.py:307）。`position_manager.py:255,282` 先检查 enabled，为 False 直接跳过。`exit_rules.py` 中 urgent_exit/tighten_stop/confidence_collapse 代码完整但从不被调用。

**影响**: 市场急跌时系统不会主动收紧止损或紧急平仓，只依赖机械止损价。

**涉及文件**:
- `aletheia/config/schema.py:307` — `enabled: bool = False`
- `aletheia/portfolio/position_manager.py:255,282` — 条件检查
- `aletheia/portfolio/exit_rules.py` — 规则实现
- `aletheia/server/routes/settings.py` — TradingConfigUpdate 未包含 market_aware_exits

**修复方向**: (1) settings.py 增加 MarketAwareExitsConfig 字段 (2) 前端 Settings 增加控制段 (3) 将默认值改为 enabled=True（实验冻结后）

---

### D3. Strategy paper_candidate → paper_running 需 PI Daemon 消费信号（PARTIAL）

**现状**: strategy_designer 自动推进策略到 `paper_candidate`，但进入 `paper_running` 需要 PI Daemon 消费 `recommend_promote` 信号。如果 PI Daemon 未运行（permission_mode 为 Observe），策略永远卡在 paper_candidate。

**影响**: 在 Observe 模式下（实验初期），策略自动生成但永远不会开始 paper trading。

**涉及文件**:
- `aletheia/agents/strategy_designer.py:607-630` — 写 recommend_promote 并 break
- `aletheia/trading/strategy_lifecycle.py:167-172` — `_has_pi_approval()` 校验
- `aletheia/agent_runtime/pi_daemon.py` — 消费信号

**修复方向**: 考虑在 Paper 模式下允许自动晋升（无需 PI 确认），仅 live 晋级需要人工确认。

---

### D4. Knowledge/Lessons 对决策无结构化约束力（PARTIAL）

**现状**: Lessons 通过 `trading_context.py` 注入 LLM prompt Layer 3。但 `_DECISION_SCHEMA` 无 lesson 引用字段，无后处理代码基于 lesson 修改 conviction。LLM 可以完全忽略 lessons。

**影响**: "经验学习影响未来决策"的承诺是软性的——依赖 LLM 自觉遵循，无法审计或强制。

**涉及文件**:
- `aletheia/agent_shell/trading_decision.py` — schema 定义 + prompt 构建
- `aletheia/agent_shell/trading_context.py:362-378` — lessons 注入

**修复方向**: (1) 在 _DECISION_SCHEMA 中增加 `lessons_considered: list[str]` 字段 (2) 后处理：若 trading_guard 明确 block 了 symbol/direction，无论 LLM 输出什么都强制 conviction=0（已有 guard 机制走这条路）

---

### D5. Regime Detection 无独立 job，重启后内存态丢失（PARTIAL）

**现状**: `detect_regime()` 嵌入 `universe_refresh` job 内部执行。结果存于进程内存 `_published_regime`。重启后 `get_current_regime()` 返回默认 "sideways 0.5"。`market_regime` 表有持久化但读取路径只走内存。

**影响**: gateway restart 后的第一个 10 分钟窗口内，paper_trader_ranker 用错误的 regime 评分。

**涉及文件**:
- `aletheia/regime/detector.py:261` — 默认返回值
- `aletheia/universe/service.py:104-136` — regime 执行位置
- `aletheia/agents/paper_trader_ranker.py:207,359` — 消费 regime

**修复方向**: (1) 启动时从 `market_regime` 表恢复最近一条记录 (2) 或独立 regime detection job（与 universe_refresh 解耦）

---

## 功能完整度总览（两轮审计综合）

| 层 | 完整度 | WORKING | PARTIAL | SHELL | 关键断点 |
|----|--------|---------|---------|-------|---------|
| 安全层（risk/execution/kill_switch/因果链/审计链） | ~95% | risk 16关、因果链、hashchain、kill_switch | reconciliation(仅live) | — | demo 对账缺失 |
| 基础设施层（health/通知/采集/sync） | ~75% | health自愈、Telegram、audit | UTA(credential静默)、data_collectors(6非9域) | — | volume 内存丢失 |
| 决策层（PI Agent + knowledge + calibration） | ~50% | PI决策链+guards | lessons(软影响)、regime(重启恢复✅已修) | calibration→决策 | calibration 未注入 prompt |
| 自适应层（regime/exits/self-evolution/lifecycle） | ~40% | backtest闭环、guard生成 | exits(已接入config✅)、lifecycle(需PI确认) | — | PI不跑则全卡 |
| Operator/命令层 | ~70% | 43命令、tasks系统 | Level 2 proposal(无前端review) | — | retire 依赖 PI 确认 |
| 可观测层（analytics + UI） | ~45% | prediction(intentional观察) | 6 analytics无UI、3 config无UI | — | 能力存在但不可达 |

---

---

## E 级：运维/可观测性弱点（第二轮审计 2026-06-23）

### E1. Reconciliation 在 demo 模式下不核对 exchange orders（PARTIAL）

**现状**: `reconciliation.py` 仅在 `cfg.exchange.mode == "live"` 时运行。bitget_demo 的订单理论上有 exchange order ID，但 reconciliation 过滤条件 `execution_mode == _LIVE_MODE` 排除了 demo 单。

**影响**: demo 模式下本地订单状态可能与 exchange 不一致（如 demo 单在 exchange 侧被部分成交），但本地永远不会发现。

**推荐方向**: 扩展 reconciliation 条件为 `mode in ("live", "demo")`，或增加独立的 demo reconciliation job。低优先级（demo 偏差不影响真实资金）。

### E2. UTA adapter 无法区分"0 余额"和"无 credential"（PARTIAL）

**现状**: `create_all_configured()` 初始化失败时 except-pass 静默跳过，`get_unified_state()` 返回全零 `UnifiedState`。

**影响**: UI 展示 "0 USDT equity" 时，用户无法判断是真的空仓还是 API key 配置错误。

**推荐方向**: 在 `UnifiedState` 中增加 `connected_brokers: list[str]` 字段，前端根据长度展示连接状态。

### E3. volume_scanner 结果仅存内存，重启丢失（PARTIAL）

**现状**: `_latest_volume_ranking` 是 module global dict，不写 DB。重启后 paper_trader_ranker 的 volume rank scoring 回落默认值。

**影响**: 10min 窗口内 volume 维度评分不准确。

**推荐方向**: 可写入 `market_snapshots` 附属列或独立表。低优先级。

### E4. data_collectors 文档称 "9域" 实际 6 域（文档不准确）

**现状**: `_async_data_collectors` 只覆盖 orderbook/funding/OI/liquidation/macro/volume 共 6 个域。

**推荐方向**: 修正 agent_contracts.md 的 "9-domain" 描述为 "6-domain (4×2min + 2×10min)"。

### E5. Operator retire_strategy 写 pi_decision 而非直接执行（设计但有隐患）

**现状**: `_action_retire_strategy()` 不直接 `promote_strategy(sid, "retired")`，而是写 `pi_decision` recommendation 等 PI Agent 确认。

**影响**: 如果 PI Agent 未运行，应退策略永远 pending。这是双重确认设计，但缺少超时降级。

**推荐方向**: 增加超时机制：recommendation 超过 24h 未消费 → 自动执行 retirement。

---

## 第二轮审计：功能健康全景

| 区域 | 状态 | 一句话 |
|------|------|--------|
| 通知系统（Telegram+Webhook） | WORKING | 完整，但 DND + 三层开关可能静默吞通知 |
| Reconciliation | PARTIAL | live 正常；demo 模式下不核对 exchange orders |
| Health/自愈 | WORKING | 8 项检查 + restart 机制完整；paused 时不检查 |
| Operator/Command Center | PARTIAL | 43 命令全有 handler；Level 2 proposal 无前端 review UI |
| 审计链（Hashchain） | WORKING | SHA-256 链完整，跨进程锁，CLI 验证入口 |
| Exchange Sync/UTA | PARTIAL | 3 个 demo adapter；无法区分 0 余额和无 credential |
| Prediction Market | WORKING | 设计即 observation-only，不进交易路径（正确） |
| 数据采集 | PARTIAL | 6 域（非 9 域）；volume 仅内存；Phase B 重启后立即触发 |

---

---

## F 级：第三轮全面扫描发现（前端+路由+子系统）

### F1. news.py `/analyze` 端点 ImportError（BUG）

**现状**: `news.py:92` 写 `from aletheia.agents.llm import llm_complete`，但 `aletheia/agents/llm.py` 不存在。正确路径应为 `aletheia.agents.helpers`。当前 `try/except` 吞掉错误，返回 "Analysis unavailable"。

**影响**: 新闻分析功能完全不可用，用户看到"分析不可用"但不知道是 bug。

**修复**: 改为 `from aletheia.agents.helpers import llm_complete`。

**涉及文件**: `aletheia/server/routes/news.py:92`

### F2. Universe Policy 无写入端点

**现状**: `GET /universe/policy` 存在（读取），但无 `POST /policy` 端点。`repositories.insert_universe_policy()` 已实现但路由未暴露。

**影响**: Universe 筛选策略只能通过代码/DB 手动修改，前端无法编辑。

**修复**: 在 `routes/universe.py` 增加 `POST /policy` 端点。

### F3. AgentActivity 页 reasoning steps 后端未建

**现状**: 前端 `AgentActivity.tsx` 的 ReasoningStepsSection 已声明需要 `GET /api/agent-runs/{id}/steps`，后端不存在。前端已诚实展示 "Step-by-step reasoning trace pending backend"。

**影响**: Agent 执行过程不可回溯。

### F4. Universe 页无 policy 编辑 UI

**现状**: Universe.tsx 只有 "Refresh" 按钮和只读展示，无 policy/threshold 编辑表单。

**影响**: 即使后端加了 POST /policy，前端也没有对应 UI。

### F5. scanner `score_event()` 是 SHELL

**现状**: `universe/scanners.py` 的 `score_event()` 直接 `return (0.0, [], None)`。其余 4 个 scanner 完整。

**影响**: event-driven 策略族永远得分 0，事件型机会无法被系统发现。

### F6. symbol_map.py 仅 30 个硬编码 CoinGecko slug

**现状**: `data/sources/symbol_map.py` 硬编码约 30 个 symbol → CoinGecko ID 映射，未知 symbol 抛 `KeyError`。

**影响**: 当 universe 纳入新上线 altcoin 且 CCXT 不可用时，CoinGecko fallback 会 crash。

---

## 第三轮全面扫描总结

### 前端页面（11个）

| 状态 | 数量 | 页面 |
|------|------|------|
| WORKING | 9 | Overview, TradesPositions, StrategyLifecycle, RiskControls, CommandCenter, Learning, Charts, SerenityLens, PredictionLens |
| PARTIAL | 2 | AgentActivity(reasoning steps缺), Universe(无policy编辑) |
| SHELL | 0 | — |

### 后端路由（8个文件，90个端点）

| 状态 | 数量 |
|------|------|
| 完整实现 | 89/90 |
| 有 bug | 1 (news.py /analyze ImportError) |
| 缺失端点 | 1 (universe POST /policy) |

### 子系统模块

| 模块 | 状态 | 关键发现 |
|------|------|---------|
| analytics/ (19文件) | WORKING | 所有模块有调用方和消费者 |
| universe/ (9文件) | WORKING | allocator 真实影响 sizing；score_event 是唯一 shell |
| gateway/ (20文件) | WORKING | 三层架构完整，preflight 3 项检查 |
| files/ (5文件) | WORKING | jsonl hashchain + atomic markdown writes |
| net.py | WORKING | proxy fallback + TCP probe + 60s TTL cache |
| cli/ (19文件) | WORKING | doctor 16 probes 全部实现 |
| data/sources/ (5文件) | WORKING | 四层降级链完整；symbol_map 是脆弱点 |

---

---

## G 级：架构设计缺陷（第四轮深度分析 2026-06-23）

### G1. save_config() 并发写无锁 — 配置静默丢失 [HIGH]

**现状**: `config/loader.py:save_config()` 直接 `write_text()` 覆盖 config.yaml，无文件锁、无原子写。90处调用点分布在 settings API + operator_loop + model_provider + kill_switch 等。

**影响**: 两个 HTTP API 同时修改不同字段时，后写者覆盖前者全部修改。operator_loop 的 Level 1 auto-action（如 tighten_confidence）与用户手动设置并发时，用户的改动可能被静默丢弃。

**涉及文件**: `aletheia/config/loader.py:119-124`

**修复方向**: 加 `threading.Lock` + 原子写（写临时文件 → os.replace）。

---

### G2. agents executor 单线程 — LLM 卡住阻塞所有决策 [HIGH]

**现状**: `_EXECUTOR_WORKERS = {"agents": 1}`，所有 LLM agent jobs（~12个）共享 1 个线程。LLM 超时设为 240-600s。

**影响**: 一个 LLM 调用卡 5 分钟 → 所有 agent jobs 排队等待 → paper_trader/pi_trading_decision 错过交易窗口。

**涉及文件**: `aletheia/agent_runtime/scheduler.py:96-107`

**修复方向**: 将 agents executor 提升到 2-3 个 worker；或将关键决策路径（pi_trading_decision）独立到专属 executor。

---

### G3. Cloudflare tunnel 无鉴权暴露 kill-switch/live-unlock [HIGH]

**现状**: tunnel 开启后，`/api/risk/kill-switch`、`/api/settings/live-unlock`、`/api/gateway/restart` 全部公网可达，无任何身份验证。

**影响**: 攻击者可远程释放 kill switch、开启 live 交易、或反复重启服务。

**涉及文件**: `aletheia/server/routes/risk.py`, `live.py`, `gateway.py`; `aletheia/gateway/tunnel.py`

**修复方向**: 在 trust-boundary 端点加 shared-secret header 检查或 IP allowlist middleware。

---

### G4. LLM prompt 无输入边界隔离 [MEDIUM]

**现状**: `trading_decision.py` 把 market data（thesis/content/lessons）直接 f-string 嵌入 prompt，无分隔标签、无长度限制、无字符消毒。

**影响**: 交易所公告或 Polymarket 事件描述中的恶意文本可能注入 LLM 决策。当前防护：JSON schema 枚举约束 + Risk Engine 事后过滤。

**涉及文件**: `aletheia/agent_shell/trading_decision.py:357-609`

**修复方向**: 用 XML 标签包裹外部数据（`<market_data>...</market_data>`）；截断单字段到 200 chars。

---

### G5. 无 circuit breaker — agent 连续失败不熔断 [MEDIUM]

**现状**: `_record_job_failure` 仅覆盖 6 个 IO jobs，核心 agent jobs（dreaming/strategy_designer/market_observer 等）连续失败无计数、无告警升级、无自动暂停。

**影响**: LLM provider 挂掉后，agents 每个周期都尝试调用 → 白耗 tokens + 占用 executor 线程。

**涉及文件**: `aletheia/agent_runtime/scheduler.py:67-79`

**修复方向**: 统一所有 jobs 到 failure counter + 可配置 circuit breaker（连续失败 N 次 → 跳过 M 个周期）。

---

### G6. BitgetClient 无重试 — 订单提交网络抖动时幂等性风险 [MEDIUM]

**现状**: `place_order()` 单次调用 httpx，15s timeout，无 retry。网络在响应返回前断开时，调用方无法确认是否成功。

**影响**: 极端情况下可能重复下单（虽然 client_oid 做了交易所层去重，但本地 order 状态可能不一致）。

**涉及文件**: `aletheia/exchanges/bitget/client.py`

**修复方向**: 加 1-2 次带指数退避的重试（仅限 ConnectError/Timeout，不重试 4xx）。

---

### G7. Fernet 密钥与密文同目录 [MEDIUM]

**现状**: `data/.secret.key`（明文加密密钥）和 `data/secrets.enc`（密文）在同一目录，仅靠 gitignore 保护。

**影响**: 任何能读 `data/` 目录的人可解密全部 secrets（API keys、exchange credentials）。

**涉及文件**: `aletheia/config/secrets.py`

**修复方向**: 密钥迁移到 `%APPDATA%/aletheia/` 或使用机器 ID 派生密钥。

---

### G8. datetime.now() naive datetime 13处混用 [MEDIUM]

**现状**: scheduler.py 中 11 处 `datetime.now()`（naive）用于 `next_run_time` 计算，与 cron jobs 的 `timezone="Asia/Shanghai"` 语义不一致。

**影响**: 夏令时切换（非中国场景）或跨时区部署时，初始延迟计算偏差最多 1 小时。当前部署（Windows + 中国时区）无实际影响。

**涉及文件**: `aletheia/agent_runtime/scheduler.py`（11处）, `gateway/autostart.py:148`

**修复方向**: 统一使用 `datetime.now(timezone.utc)` 或 `datetime.now(ZoneInfo("Asia/Shanghai"))`。

---

### G9. 权限模型仅在 agent_shell 层 — 底层函数可绕过 [MEDIUM]

**现状**: `FORBIDDEN_INTENTS` 和 `is_tool_permitted` 仅在 agent_shell 调用路径检查。直接调用 `execution/gateway.submit_proposal()` 或 `kill_switch.deactivate()` 不经过任何权限验证。

**影响**: 代码级攻击者（如恶意 plugin/hook）可绕过 FORBIDDEN_INTENTS。当前唯一的 defense-in-depth 是 Risk Engine 的参数检查。

**涉及文件**: `aletheia/execution/gateway.py`, `aletheia/agent_runtime/permissions.py`

**修复方向**: 在 `submit_proposal` 入口加 `assert permission_mode != "Observe"` 或等效检查。

---

### G10. 无 Prometheus metrics — 可观测性依赖定时 health check [MEDIUM]

**现状**: 无 `/metrics` endpoint，无 prometheus_client 引入。告警依赖 10 分钟一次的 health check 采样，`logger.error()` 不直接触发通知。

**影响**: 异常事件（如单次大额亏损）最多 10 分钟后才被发现。无法接入外部监控（Grafana/Alertmanager）。

**修复方向**: 暴露 `/metrics` 端点 + 关键 counters（trade_count, risk_rejections, agent_failures, LLM_latency）。中长期工作。

---

## 风险总览

| 级别 | 数量 | 编号 |
|------|------|------|
| HIGH | 3 | G1(config竞写), G2(agents单线程), G3(tunnel无auth) |
| MEDIUM | 7 | G4-G10 |
| LOW | 多 | 详见各审计报告（进程管理/WAL/启动顺序等已确认安全） |

---

---

## H 级：测试盲区（第五轮分析 2026-06-23）

### H1. Risk Engine check #15/#16 零测试 [HIGH]

`_check_lesson_constraints` 和 `_check_correlation_exposure` 在 tests/ 中出现 **0 次**。`_portfolio_drawdown_scale` 同样零测试。这三个函数直接影响仓位 sizing 和交易阻断——如果它们悄悄失效，系统会在亏损期间仍允许大仓位，且不会有任何测试发现。

涉及文件: `aletheia/risk/engine.py:285-328, 331-372, 390-485`

### H2. Promotion Gates 无边界值测试 [HIGH]

现有测试只验证"远离阈值"的 fail/pass 案例。29 vs 30 trades、4.99% vs 5.01% drawdown、5 vs 6 consecutive losses 的边界从未被测试。off-by-one 错误会让不合格策略进入 live。

涉及文件: `aletheia/trading/strategy_lifecycle.py:check_promotion_gates`

### H3. 前端 100% 零测试覆盖 [MEDIUM]

无 vitest/jest/playwright。80+ React 组件（含 kill switch 控制、live unlock 按钮、config 设置面板）任何改动无回归保护。

---

## I 级：性能瓶颈（第五轮分析 2026-06-23）

### I1. price_feed 逐 symbol 串行 ticker — 50 symbols = 15s [HIGH]

代码注释说"batch fetch_tickers"，但实现是 `for sym in symbols` 循环串行调用。50 个 symbol × 300ms RTT = 15s，接近 45s timeout。

涉及文件: `aletheia/data/price_feed.py:129-152`

### I2. 启动 sync_all_exchanges 同步阻塞 — 最坏 50s [MEDIUM]

5 次串行 Bitget HTTP 调用在启动时阻塞 uvicorn 主线程。API 超时时最坏 50s，supervisor 可能误判为启动失败。

涉及文件: `aletheia/server/app.py:305`

### I3. position_manager N+1 查询 — 50 持仓 = 260 DB reads/轮 [MEDIUM]

`_get_atr_pct` 对每个持仓独立查 `universe_feature_snapshots`，且查询条件 `symbol=?` 不命中现有索引（索引是 `asset_id, ts DESC`）。

涉及文件: `aletheia/portfolio/position_manager.py:64-81`

---

## J 级：代码质量债（第五轮分析 2026-06-23）

### J1. risk/engine.py 中 4 处 except Exception 吞掉风控错误 [HIGH]

风控引擎的 `_check_lesson_constraints` 和 `_check_correlation_exposure` 内部异常被 `except Exception: return []` 吞掉，导致检查静默跳过而非安全拒绝。

涉及文件: `aletheia/risk/engine.py`

### J2. 344 处 except Exception（168 处纯 pass）[MEDIUM]

分布在 104 个文件。最高危位置：`_gateway_runner.py`(9处)、`lifecycle.py`(11处)。这些是启动/生命周期路径，错误被吞掉会导致诊断盲区。

### J3. 无 mypy/pyright — 116 处 Any 无验证 [MEDIUM]

整个代码库有类型注解但没有类型检查器运行。`# type: ignore` 9 处在无类型检查器的情况下是失效注释。

### J4. 7 个文件超 1000 行（最大 2063 行）[LOW]

`operator_recommendations.py`(2063), `commands.py`(2034), `routes/data.py`(1827), `repositories.py`(1722), `migrations.py`(1691), `scheduler.py`(1531), `routes/operator.py`(1471)

---

## 文档覆盖缺口（已修正 2026-06-23）

| 类别 | 缺口 | 状态 |
|------|------|------|
| ~~agent_contracts.md~~ | ~~14/30 job 频率错误~~ | ✅ 已修正 |
| ~~agent_contracts.md~~ | ~~5 agent 无条目~~ | ✅ 已补充 |
| ~~data_model.md~~ | ~~8 张表未记录~~ | ✅ 已补充 |
| ~~api_reference.md~~ | ~~~30 端点未记录~~ | ✅ 已补充 |
