# Aletheia 文档系统深度分析 — 通过文档控制代码的完整地图

---

## 一、总览 — 文档控制代码的世界观

在 Aletheia 项目中，文档不是代码的注释，而是代码的法律文本：`docs/design/*_contract.md` 是黄金标准（Gold Standard），代码的任何改动必须先修改契约文档，代码随文档收敛，而非反向。这套体系之所以能够运作，依赖两个技术支撑：其一是 Spec-as-Code，每份 L2 契约文档的关键断言被编码为 `tests/specs/spec_*.py` 中带 `@spec_claim` 装饰器的 pytest 函数，可独立、自动化地运行验证；其二是架构 Fitness 测试（`tests/test_architecture_fitness.py`），用 AST/源码扫描在秒级内验证模块间的依赖方向和关键常量的存在性。本报告对全部 15 份设计文档逐一解剖，绘制其控制边界、载荷断言、规范与代码的漂移点，以及 Spec 覆盖率，最终形成一张"文档控制代码"的完整地图，供开发者在触碰任何模块前定位其主控文档、已知风险和 Spec 缺口。

---

## 二、按层级深入每份文档

### 2.1 risk_engine_contract.md

**角色**: L2 金标准契约，定义 16 关 Risk Engine 硬墙、Kill Switch 行为、仓位计算路径和绕过预防不变量，是所有风控代码变更的主控文档。

**关键章节**:
- **§1 入口与序列化**: 声明 `evaluate_proposal()` 是唯一公开入口点，模块级 `threading.Lock(_eval_lock)` 序列化所有提案评估，防止 APScheduler 线程池并发导致的 Kill Switch TOCTOU 竞态。
- **§2 检查关卡（16关）**: 定义 #0（kill_switch）至 #16（portfolio_correlation）的规范表，包含每关名称、判断条件、配置字段、失败令牌和代码行锚点。明确声明本契约覆盖 SYSTEM_ARCHITECTURE_GUIDE 的"5关"简化叙述。
- **§3 返回结构**: 规定 `RiskCheckResult` 字段：`approved`、`risk_score`（violations×0.2 上限1.0）、`violations`、`allowed_execution_mode`（拒绝时固定为 `paper_internal`）、`message`、`decision_id`。每次调用必须持久化一条 `RiskDecision` 行。
- **§4 Kill Switch**: 四类行为——手动激活、check #8 自动触发、重启后状态持久、仅 `actor='human'` 可停用。
- **§5 仓位计算两条路径**: `paper_trader` 固定 $25/价格名义值；PI Agent 使用 Kelly 缩放的 conviction 仓位。两条路径的输出均须通过 check #5 上限。
- **§6 硬限不可绕过**: 五项旁路防护机制，包括 `create_paper_proposal()` 硬编码 `_PAPER_MODES`、`_eval_lock` 序列化、网关二次检查、Kill Switch 磁盘持久化、全决策入库。
- **§7 已知缺陷**: 声称 `engine.py:324,481` 存在 logger NameError——此条已失效，`logger` 在 `engine.py:16` 正确导入。
- **§8 目标态差距**: direction_bias 方向性不在 Risk Engine 验证范围内（属策略层）；市场状态护栏（如资金费率熔断）不得添加至 Risk Engine。

**载荷断言**:
- `evaluate_proposal()` 是唯一公开入口；所有提案必须经其获得许可 (`engine.py:67`)
- `_eval_lock = threading.Lock()` 在模块级防止 TOCTOU (`engine.py:30`)
- Check #0：`if cfg.runtime.kill_switch_active: violations.append('kill_switch_active')` 先于所有其他检查 (`engine.py:83`)
- Check #8 日损超限时自动触发 Kill Switch (`engine.py:158-163`)
- 每次 `evaluate_proposal` 调用必须持久化一条 `RiskDecision` 行 (`engine.py:271-280`)
- `RiskCheckResult.allowed_execution_mode` 拒绝时固定为 `paper_internal` (`engine.py:255-256`)
- `deactivate()` 限制 `actor='human'`，无代理可达路径 (`kill_switch.py:39`)
- Check #5 drawdown 缩放：0-5% DD 系数1.0，线性降至10% DD时0.5，20% DD时0.0 (`engine.py:362-371`)

**偏差与漂移**:
- **中等**: §2 failure-token 表缺少三个未文档化令牌：`exceeds_demo_position_notional`（`engine.py:128`）、`max_single_trade_risk_missing_stop_price`（`engine.py:207`）、`stale_market_data_demo`（`engine.py:238-241`）
- **轻微**: §7 的 logger NameError 警告是失效的过时标注，`logger` 已在 `engine.py:16` 正确导入
- **轻微**: §1 中行号锚点漂移：`evaluate_proposal` 实为 line 67（文档写64）、`_eval_lock` 实为 line 30（文档写27）

**Spec 覆盖**: `tests/specs/spec_risk_engine.py`，9 个 claims（RE-S01～RE-B05）。覆盖入口点、锁类型、结果字段、最低检查数量、RiskDecision 持久化、Kill Switch 阻断、lesson/correlation 安全拒绝、drawdown 分段缩放。**未覆盖**：check #1-#4、#6-#14 的 13 个关卡均无 L2b 行为测试；Kill Switch 重启后持久化行为无 spec_claim。

**控制的代码模块**: `aletheia/risk/engine.py`、`aletheia/risk/kill_switch.py`

**Takeaway**: 控制表面扎实，所有 16 关和序列化锁均完整实现，但 §2 failure-token 表与代码存在 3 个未文档化令牌，13 个关卡缺乏行为级测试，是最高优先级 spec 补充区域。

---

### 2.2 execution_contract.md

**角色**: L2 金标准，定义提案→风控决策→订单→成交→仓位因果链、原子性/幂等性保证、对账红线和审计要求，是执行路径代码变更的主控文档。

**关键章节**:
- **§1 入口与因果链**: `submit_proposal()`（`gateway.py:16`）为新提案入口；`execute_existing_proposal()`（`gateway.py:52`）重执行已存储提案，不重复创建行。因果链：trade_proposals→risk_decisions→orders→fills→positions。
- **§2 链路数据结构**: 映射每个环节至 Pydantic 模型；提案状态机：pending→approved→executed/rejected/error。
- **§3 执行模式与 Broker**: 5种模式映射至对应 broker（paper/bitget/okx/binance）；`_is_live()` 在 `engine.py:35`（文档称32，3行漂移）。
- **§4 TPSL 挂单**: 首次成交后挂单，非致命（仅警告），持久化至 `position.exchange_tpsl_order_id`，平仓前自动取消。
- **§5 原子性/幂等**: `Order(pending)` 先于交易所调用插入数据库；`client_oid=proposal_id` 作幂等键；失败时提案设 `status=error`，禁止删除部分行。
- **§6 对账红线**: 对账仅读取交易所状态、更新本地 `order.status`、写审计记录——绝不下单、绝不取消订单、绝不伪造 Fill 行。
- **§7 审计要求**: 每个重要状态转换写入 `data/logs/audit.jsonl`，必填字段：timestamp/actor/source/event_type/entity_type/entity_id/message。
- **§8 目标态差距**: `strategy.entry_rule/exit_rule` 是死字符串从未解析；审计链 `_prev_hash` 已实现（`jsonl.py:185-200`），§8 中的 ⚠️ 标注已失效。

**载荷断言**:
- `submit_proposal()` 是新提案唯一入口，一个提案创建且仅创建一条 `trade_proposals` 行 (`gateway.py:16`)
- 所有订单必须通过 Execution Gateway，代理/控制台不得直接调用交易所
- `Order(pending)` 先于交易所调用插入数据库 (`bitget_broker.py:87-99`)
- `client_oid=proposal_id` 作幂等键去重 (`bitget_broker.py:106`)
- 对账绝不下单或取消订单，仅更新 `order.status` (`reconciliation.py:38-85`)
- TPSL 挂单失败仅记录警告，不影响主流程 (`bitget_broker.py:321-322`)
- 审计链使用 SHA-256 `_prev_hash` 跨轮转链接 (`jsonl.py:185-200`)

**偏差与漂移**:
- **中等**: §8 的 `_prev_hash` ⚠️ 标注是过时标注——代码已完整实现哈希链，标注未清理
- **轻微**: `_is_live()` 实际在 `engine.py:35`，文档称 32（3行漂移）
- **轻微**: TPSL 锚点称 `bitget_broker.py:152`，实际调用在 153-163
- **轻微**: `binance_broker` 存在于代码但 `spec_execution.py` 未覆盖

**Spec 覆盖**: `tests/specs/spec_execution.py`，7 个 claims（EX-S01～EX-C01）。**未覆盖**：提案状态机全转换路径、broker 路由 L2b 测试、TPSL 行为、原子性/幂等性保证、对账只读不变量的行为级验证、审计轨迹写入。

**控制的代码模块**: `aletheia/execution/gateway.py`、`aletheia/execution/paper_broker.py`、`aletheia/execution/bitget_broker.py`、`aletheia/execution/okx_broker.py`、`aletheia/execution/binance_broker.py`、`aletheia/execution/reconciliation.py`、`aletheia/storage/models.py`（交易相关模型）、`aletheia/files/jsonl.py`

**Takeaway**: 因果链、原子插入模式、幂等键和对账红线均与代码精确匹配，但执行路径中影响最大的 §4（TPSL）和 §5（原子性）完全无 spec 覆盖，是资金安全的最高风险 spec 空白。

---

### 2.3 live_unlock_contract.md

**角色**: L2 金标准信任边界契约，定义实盘解锁路径、默认锁定状态、审计要求和代理永久禁止清单（FORBIDDEN_INTENTS + RESTRICTED_TOOLS）。

**关键章节**:
- **§1 Live 状态标志**: `LiveConfig.state` 默认 `live_disabled`；系统出厂锁定，任何实盘执行被 risk_engine check #3 拒绝 (`schema.py:112`)。
- **§2 解锁路径与人工确认门**: 唯一入口 `live_unlock()`（`live.py:17`）；需 `confirm=true`，首次还需 `acknowledge_first_unlock=true`；单向 `live_disabled→live_limited`；锁定是独立路由。
- **§3 解锁审计格式**: 规定 JSON 精确字段：`actor=human, source=dashboard, event_type=live_unlocked`；追加写入 `audit.jsonl`。
- **§4 不可逆操作清单**: 实盘解锁和真实交易所订单，两者均不可逆。
- **§5 权限模式（双面）**: 工人端（Observe/Paper/Full）和操作员端（ReadOnly/Research/PaperAuto/AllowEditing/Auto），默认分别为 Paper 和 ReadOnly。
- **§6 代理永久禁止清单**: `FORBIDDEN_INTENTS` frozenset 7项 (`registry.py:415-419`)；任何权限模式均不可访问；`RESTRICTED_TOOLS` 5项，`TOOL_CLASSES['restricted']=set()` 使其永久不可达。

**载荷断言**:
- `LiveConfig.state` 默认 `live_disabled`，系统启动时绝不为实盘 (`schema.py:112`)
- `live_unlock()` 强制 `confirm=true` 且首次需 `acknowledge_first_unlock=true` (`live.py:17-45`)
- 状态转换单向：`live_disabled→live_limited`，无更高权限路径 (`live.py:31`)
- `FORBIDDEN_INTENTS` 恰好 7 项：`enable_live, modify_credentials, bypass_risk, place_live_order, modify_execution_gateway, delete_knowledge, change_risk_limits` (`registry.py:415-419`)
- `check_permission()` 对任何 `FORBIDDEN_INTENTS` 中的意图返回永久拒绝 (`permissions.py:52-53`)
- `RESTRICTED_TOOLS` 恰好 5 项，均注册为 `restricted` 类 (`tool_registry.py:39-45`)
- `TOOL_CLASSES['restricted']=set()` 使任何权限模式均无法满足受限工具访问 (`agent_runtime/permissions.py:7`)

**偏差与漂移**:
- **中等**: 解锁路由没有操作员 `permission_mode` 代码层检查，访问控制依赖 HTTP 层隔离——§2 标记为 batch 5 待解决
- **中等**: `trust_and_change_control.md §6.1` 将受控模块列为 `aletheia/agent_runtime/permissions.py`，但 `FORBIDDEN_INTENTS` 实际在 `aletheia/agent_shell/permissions.py`——模块路径偏差
- **轻微**: `spec_live_unlock.py LU-B02` 引用"§3 restricted tools"，但§3是审计格式，非受限工具——spec 注解错误

**Spec 覆盖**: `tests/specs/spec_live_unlock.py`，5 个 claims（LU-S01～LU-C01）。**未覆盖**：实际解锁行为（前置条件验证、审计事件写入、单向转换）无 spec_claim；§3 审计格式无测试；§4 不可逆操作清单无测试；操作员权限面无覆盖。

**控制的代码模块**: `aletheia/agent_shell/registry.py`、`aletheia/agent_shell/permissions.py`、`aletheia/agent_runtime/tool_registry.py`、`aletheia/agent_runtime/permissions.py`、`aletheia/server/routes/live.py`、`aletheia/config/schema.py`

**Takeaway**: 所有 7 项 FORBIDDEN_INTENTS 和 5 项 RESTRICTED_TOOLS 均存在且正确执行，零代码漂移；但两个已记录的 ⚠️ 缺口（解锁路由缺少操作员权限检查、模块路径偏差）待 batch 5 修复，且最关键的解锁行为路径无任何行为级 spec 测试。

---

### 2.4 promotion_gates_contract.md

**角色**: L2 金标准契约，定义策略生命周期状态机、量化晋级门和数据源约束，是策略从模拟到实盘晋升的主控文档。

**关键章节**:
- **§1 生命周期状态机**: `LIFECYCLE_STATES` 10个状态（draft→deprecated），`FORWARD_TRANSITIONS` 单向有向图，自动晋级止步于 `paper_candidate`。
- **§2 晋级门（默认阈值）**: `DEFAULT_PROMOTION_GATES` 7键精确数值表，覆盖运行时长、交易次数、正期望值、最大回撤、最大连续亏损、关键风控违规数、交易后复盘要求。
- **§3 数据源表**: 7个仓库表，计算必须来自存储记录，不得来自状态标签；不可计算指标须以 `insufficient_*` 标签明确失败，禁止静默通过。
- **§4 关键偏差**: 偏差1——高频放松规则（30次交易→3天最低运行时长替代14天）；偏差2——契约声明 `paper_candidate→paper_running` 的 PI 审批门"未实现"，但代码 `strategy_lifecycle.py:167-172` 已实现 `_has_pi_approval()`——**这是一个关键的失效标注**。
- **§5 实盘晋级（更严）**: `live_candidate→live_limited` 额外需要 `live.state==live_limited`、`first_live_confirmation_done==true`、权限模式和 Risk Engine 许可。
- **§6 退役/降级**: 三条退役路径，`operator_loop.py` 行号大幅漂移（文档称 299/330，实际为 564/595）。

**载荷断言**:
- `LIFECYCLE_STATES` 精确 10 个命名状态，顺序固定 (`strategy_lifecycle.py:27`)
- `DEFAULT_PROMOTION_GATES` 精确 7 键及数值：`min_paper_runtime_days=14, min_paper_trades=30, positive_expectancy_required=True, max_drawdown_pct_allowed=5.0, max_consecutive_losses_allowed=5, critical_risk_violations_allowed=0, post_trade_reviews_required=True` (`strategy_lifecycle.py:55`)
- 门控必须从存储记录计算，不可计算时须以 `insufficient_*` 失败，禁止静默通过
- 高频放松：若 `paper_trade_count>=30`，运行时长门限从14天放松至3天 (`strategy_lifecycle.py:247-258`)
- `paper_candidate→paper_running` 须 PI Agent `recommend_promote` 记录 (`strategy_lifecycle.py:167-172`)
- `live_candidate→live_limited` 额外需要 `live.state==live_limited` + `first_live_confirmation_done==true` (`strategy_lifecycle.py:411-423`)

**偏差与漂移**:
- **严重**: §4 偏差2 声称 PI 审批门"未实现"——实际代码 `strategy_lifecycle.py:167-172` 已实现 `_has_pi_approval()`，契约描述为虚假的失效标注，必须修正
- **轻微**: `operator_loop.py` 行号漂移：recommend_retire 在 line 564（文档称299），recommend_demote 在 595（文档称330）
- **轻微**: `check_promotion_gates()` 在 `strategy_lifecycle.py:209`（文档称192）

**Spec 覆盖**: `tests/specs/spec_promotion_gates.py`，7 个 claims（PG-S01～PG-C01）。**未覆盖**：高频运行时长放松分支无行为测试；实盘晋级附加守卫无 spec_claim；三条退役路径无 spec_claim。

**控制的代码模块**: `aletheia/trading/strategy_lifecycle.py`、`aletheia/strategy/orchestrator.py`、`aletheia/agent_shell/operator_loop.py`

**Takeaway**: 结构不变量（10个状态、7个门值）与代码精确匹配，但包含一个严重失效标注（PI 审批门在契约中声明"未实现"而代码已实现），以及多处行号漂移，须立即修正契约文本。

---

### 2.5 data_model.md

**角色**: L2 金标准数据层契约，定义约80张 SQLite 表、52个 Pydantic 模型、WAL 并发模型、仅追加迁移策略、保留白名单和因果链永久性不变量。

**关键章节**:
- **§1 架构概述**: SQLite3 + WAL，`busy_timeout=30000ms`，`synchronous=NORMAL`，`isolation_level=None`（真正自动提交），每线程独立连接（`threading.local`）。
- **§2 核心交易管道（因果链）**: ASCII 图展示完整链路：market_snapshots→opportunities→trade_proposals→risk_decisions→orders→fills→positions→portfolio_snapshots/knowledge_items。
- **§3 表分组**: 12组约80张表（A-L），含交易核心10张、策略4张、知识/学习3张、市场数据5+1张、代理运行时10张、代理Shell 7张等。
- **§4 Pydantic 模型索引**: 52个模型，全部在 `aletheia/storage/models.py`；含无持久表的瞬态模型（PSArtifactRef等）。
- **§5 Schema 惯例**: UUID4 字符串 ID、ISO 8601 UTC 时间戳、JSON 列存为 TEXT、FTS5 虚表 knowledge_items_fts（名称错误，见漂移）。
- **§6 迁移策略**: 全新DB用完整 DDL；现有DB用 `_ADDITIVE_COLUMNS` 幂等追加列。硬规则：禁止 DROP TABLE/DROP COLUMN/RENAME。
- **§7 保留与清理**: 每日运行清理任务；因果链表（7张）永不裁剪；仅 `_PRUNABLE` 白名单可裁剪，越界抛 ValueError。
- **§8 JSON 列处理**: 写入无 SQLite 边界 schema 验证；读取容错（JSON 解析失败静默跳过）。
- **§9 并发恢复**: 依赖 `busy_timeout=30000` 自动重试；`isolation_level=None` 防止隐式 BEGIN 锁泄漏。

**载荷断言**:
- 每条新连接执行 `PRAGMA journal_mode=WAL` (`sqlite.py:37`)
- `busy_timeout=30000ms` 在每条连接上设置 (`sqlite.py:43`)
- `isolation_level=None`（真正自动提交）防止后台线程持有 WAL 写锁 (`sqlite.py:34`)
- `threading.local(_local)` 保证每线程一连接，禁止跨线程缓存连接 (`sqlite.py:11`)
- `was_in_tx` 在每次写操作**之前**捕获；仅当 `not was_in_tx` 时提交 (`repositories.py:1018-1020`)
- `_PRUNABLE` 白名单上限并不含任何因果链表 (`repositories.py:1000-1012`)
- `audit.jsonl` 使用 SHA-256 `_prev_hash` 跨轮转防篡改链接 (`jsonl.py:185-319`)

**偏差与漂移**:
- **中等**: §3E 将 `runtime_state` 列为独立表（代理运行时10张），但 `migrations.py` 中无 `CREATE TABLE IF NOT EXISTS runtime_state`——此表名只是列名，Agent Runtime 实际为9张独立表
- **中等**: §5 FTS5 虚表名称为 `knowledge_items_fts`，`migrations.py:1453` 实际创建为 `knowledge_fts`——表名不匹配
- **轻微**: §7.1 行号锚点 `repositories.py:959-971` 已漂移，`_PRUNABLE` 实际在 `repositories.py:1000-1006`
- **轻微**: §8 行号锚点 `repositories.py:1666` 已漂移，JSON 容错实际在 `repositories.py:1701-1703`

**Spec 覆盖**: `tests/specs/spec_data_model.py`，7 个 claims（DM-S01～DM-S07）。覆盖每线程连接模型、WAL 模式、自动提交、`_PRUNABLE` 有界性、7张因果表存在性、3张运行时表、capital_events 表。**未覆盖**：`was_in_tx` 写前捕获模式无 spec_claim；JSON 列处理无测试；并发恢复协议无测试。

**控制的代码模块**: `aletheia/storage/migrations.py`、`aletheia/storage/sqlite.py`、`aletheia/storage/repositories.py`、`aletheia/storage/models.py`、`aletheia/files/jsonl.py`

**Takeaway**: WAL/并发/保留不变量均实现且可通过 spec 验证，但 FTS5 虚表名称是实际功能性名称不匹配（会导致全文检索路径出错），`runtime_state` 表的不实计数虽不影响运行时但使文档的表数量统计不可信。

---

### 2.6 agent_contracts.md

**角色**: L2 金标准契约，定义 19 个注册代理的 IO 契约、30个调度任务表（频率/超时/LLM 标志）和横切不变量，是调度配置和代理隔离规则的主控文档。

**关键章节**:
- **§1 调度任务概览（30个）**: 全部30个注册任务，五个线程池（default 2工人, io/fast_io/observer/agents 各1工人），1个条件注册任务（`ohlcv_nightly_backfill`）。
- **§2 代理契约（按功能）**: 按角色分组的 per-agent IO 契约（触发器、输入、LLM 用法、输出、副作用、关键不变量）。`~~stock_observer~~` 标记为 DEPRECATED。
- **§3 横切关注点**: SQLite 并发不变量、每任务硬超时表、错误处理（每任务 catch-all、连续3次失败告警）、LLM 降级 `complete_with_fallback()`、权限模式。
- **§4 数据流摘要**: ASCII 图从 market_data(5min) 追踪至 knowledge_items → dreaming_daily → strategy_designer。

**载荷断言**:
- 所有命名池的 `ThreadPoolExecutor` 工人总数不超过 3；`_EXECUTOR_WORKERS` 最大值为 2（default 池）(`scheduler.py:96-107`)
- 调度器注册恰好 30 个 `add_job` 调用（29 个无条件+1个条件）；spec 要求 ≥28 (`scheduler.py:324-482`)
- `paper_trader` 绝不直接调用 LLM（纯规则评估），`paper_trader.py` 无 llm/openai 导入
- PI Agent 入口点为 `evaluate_trading_opportunity()` 而非 `evaluate_trading_decision` (`trading_decision.py:113`)
- `FORBIDDEN_INTENTS` 在任何权限模式下均不可调用 (`registry.py:415`)
- 连续 ≥3次任务失败触发 `infra_event` (`scheduler.py:67-79`)
- 过期机会经 `expire_stale_opportunities(ttl_hours=48)` 处理（实际在 `repositories.py:1632`，文档称1597）

**偏差与漂移**:
- **中等**: 三个调度器任务频率与代码不符：`position_manager` 代码每5分钟（文档称10分钟）、`post_trade_reviewer` 代码每4小时（文档称2小时）、`risk_critic` 代码每6小时（文档称1小时）——文档高估系统响应频率
- **中等**: `insight_synthesizer` 触发时间：§1 说 CST 02:30，§2.D 说 UTC 04:00，两者含义不同——代码与§1 一致（CST 02:30）
- **轻微**: `stock_observer` timeout 入口仍存在于 `scheduler.py:33`，对应任务已无 `add_job` 调用

**Spec 覆盖**: `tests/specs/spec_agent_contracts.py`，8 个 claims（AC-S01～AC-S08）。注意：AC-S01 用正则 `r'max_workers\s*=\s*(\d+)'` 仅匹配两处明确 `max_workers=1` 的辅助线程池，未验证 `_EXECUTOR_WORKERS` dict 中的 APScheduler 池大小——spec 通过但未真正验证约束目标。**未覆盖**：每任务超时机制、LLM 降级行为、每任务 catch-all 隔离、PI Agent 安全（绝不执行订单）。

**控制的代码模块**: `aletheia/agent_runtime/scheduler.py`、`aletheia/agents/paper_trader.py`、`aletheia/agents/paper_trader_ranker.py`、`aletheia/agents/equity_observer.py`、`aletheia/agent_shell/trading_decision.py`、`aletheia/knowledge/manager.py`、`aletheia/knowledge/guards.py`

**Takeaway**: 结构不变量大体正确，但三个任务的调度频率在文档和代码之间差异显著（2-6倍），意味着文档描述的系统响应能力被高估，以此为依据的运维决策可能有误。

---

### 2.7 api_reference.md

**角色**: Aletheia 后端 REST API 的权威端点索引，定义路径/方法身份和信任边界义务，字段级 schema 真相在代码中。

**关键章节**:
- **惯例**: 基础 URL `localhost:8787/api/`，无鉴权本地策略，503保留给后端不可用（UI 诚实义务），响应 schema 不在此文件规范。
- **信任边界端点**: 5组跨信任边界端点，每组须写审计事件。
- **核心/健康、交易管道、投资组合、策略、分析、市场数据、知识**: 标准业务端点，共约80条。
- **风控/Kill Switch**: 声明两个独立端点 `POST /kill-switch/activate` 和 `POST /kill-switch/release`——**这与代码严重不符**。
- **实盘交易（信任边界）**: 声明 `POST /live-unlock` 和 `POST /live-lock`——**前缀漏标 `/settings/`**。
- **设置、凭证、模型/LLM、宇宙管道、预测市场、Serenity**: 覆盖全部配置和业务端点。

**载荷断言**:
- `POST /live-unlock` 须 `confirm=true`；首次须 `acknowledge_first_unlock=true` (`live.py:17-28`)
- Kill Switch 发布须确认；人工专属操作
- 信任边界端点每次调用必须写审计事件
- 凭证端点为只写模式，GET 不返回原始密钥
- `POST /settings/permission-mode` 不得授予 `FORBIDDEN_INTENTS`
- 隧道激活时信任边界路径须 `X-Aletheia-Token` 头 (`app.py:410-433`)

**偏差与漂移**:
- **严重**: Kill Switch 路径分裂：文档声明 `POST /kill-switch/activate` + `POST /kill-switch/release` 两个独立端点；代码实际是单一 `POST /api/risk/kill-switch`（`risk.py:85`），通过 `body {active: bool}` 切换——文档声明的发布确认义务在代码中完全未执行
- **严重**: 实盘端点前缀错误：文档列为 `/live-unlock`、`/live-lock`；实际路由在 `/api/settings/live-unlock`、`/api/settings/live-lock`（`live.py:9`前缀）
- **中等**: `GET /risk/boundaries`（`risk.py:17`）和 `GET /risk/summary`（`risk.py:74`）在文档中完全缺失
- **中等**: 无任何 `tests/specs/spec_api_reference.py`——所有信任边界端点义务（审计写入、confirm 门、无原始密钥、FORBIDDEN_INTENTS 守卫）无 spec 覆盖

**Spec 覆盖**: 无（`spec_api_reference.py` 不存在）——这是最高风险的 spec 空白，所有面向用户的信任边界操作完全无自动化验证。

**控制的代码模块**: `aletheia/server/app.py`、全部 `aletheia/server/routes/*.py` 文件

**Takeaway**: 文档是有用的端点索引，但存在三个具体漂移点影响信任：Kill Switch 是单一合并端点非两个分离端点（发布确认义务在代码中缺失）、实盘解锁路径前缀错误、两个风险端点未文档化——加之零 spec 覆盖，此契约目前不能作为可靠的执行面。

---

### 2.8 data_layer.md

**角色**: 市场数据层重构计划和架构契约，定义 CCXT 抽象、`BTC/USDT` 规范符号约定、每调用工厂模式和分阶段迁移。

**关键章节**:
- **§0 参考系深读**: OpenBB 三层架构（Provider/Fetcher/OBBject）和 OpenAlice CCXT 执行专用的架构借鉴。
- **§1 现状诊断**: 五个问题（交易所耦合、无OHLCV缓存、符号硬编码、配置混淆、无持久化），七个消费点迁移计划。
- **§2 目标架构**: `MarketDataProvider` Protocol；CCXTProvider（Binance主+Bybit回退）；BitgetClient 仅限执行操作。
- **§3 变更清单（分阶段）**: Phase 0（接口+symbols.py）→Phase 1（CCXTProvider+缓存+路由迁移）→Phase 2（yFinance 宏观）→Phase 3（清理：BITGET_GRANULARITY替换、tradable_symbols规范化）。
- **§9 已锁定决策**: ccxt 库、OHLCV SQLite 每（symbol,timeframe）对500行滚动窗口、BitgetClient get_ticker/get_ohlcv 保留但注释。

**载荷断言**:
- `get_market_data_provider()` 是每调用工厂（绝不缓存），CCXT aiohttp 绑定创建它的事件循环 (`ccxt_provider.py:3-7`)
- 所有市场数据消费者须用 `get_market_data_provider()`，禁止为市场数据导入 `BitgetClient`
- 规范符号在系统内部全程为 `BTC/USDT`；`BTCUSDT` 仅在执行适配器边界通过 `to_bitget()` 转换 (`symbols.py:42-88`)
- `MarketDataProvider` 是 `@runtime_checkable Protocol`，具有 `async get_ticker()` 和 `async get_ohlcv()` (`providers.py:45-56`)
- xStocks（ON系列）必须直接构建 `CCXTProvider(exchange='bitget', fallback_exchange='bitget')` (`ccxt_provider.py:59-118`)

**偏差与漂移**:
- **中等**: Phase 3.1 未完成——`BITGET_GRANULARITY` 仍存在于 `instruments.py:21`，`TIMEFRAME_MAP` 从未引入
- **中等**: Phase 3.6 未完成——`tradable_symbols_from_config()` 文档注释明确称"Bitget format"，返回原始符号列表，未转换为规范格式——在 `paper_trader` 过滤器处存在潜在符号格式不匹配风险
- **中等**: `get_market_data_provider()` 实现了三提供商 `FallbackChainProvider([CCXTProvider, CoinGeckoProvider, BinancePublicProvider])`，此三层链和 `enable_crypto_fallback` 标志在契约中完全未描述
- **中等**: §7 规划的四个测试文件只创建了 `test_ccxt_provider_factory.py`，其余三个（`test_symbol_mapper.py`、`test_market_data_provider.py`、`test_ohlcv_cache.py`）未存在

**Spec 覆盖**: 无（无 `spec_data_layer.py`）——载荷断言（每调用工厂、规范符号格式、CCXT专用数据路径）均无可执行 spec 测试。

**控制的代码模块**: `aletheia/data/ccxt_provider.py`、`aletheia/data/providers.py`、`aletheia/data/symbols.py`、`aletheia/data/cache.py`、`aletheia/trading/instruments.py`、`aletheia/server/routes/data.py`

**Takeaway**: Phase 0/1/2 完整实现，核心工厂和符号规范契约成立；Phase 3 两项清理工作未完成，存在潜在的符号格式静默不匹配风险，且代码中未文档化的三提供商链是超越契约的隐性功能扩展。

---

### 2.9 SDD_METHODOLOGY.md

**角色**: L2 方法论元契约，规定所有契约文档的编写、验证、更新方式，是整个 SDD 体系的元规范。

**关键章节**:
- **核心命题**: AI 降低写代码成本后，工程瓶颈从"写语法"转向"定义意图"；SDD 通过前置精确 spec 消除歧义税。
- **六阶段工作流**: Constitution → Specify → Clarify（7项检查清单）→ Plan → Tasks（1-4h/PR）→ Implement（双重验证后提交）。**关键规则**：实现中发现需改动→先改 spec→再改代码。
- **11条原则**: Spec as Truth、Upfront Clarity、Persistent Context、Test-Driven Guardrails、System Integration、Living Specs、Structured Organization、Systematic Decomposition、Human Orchestration、Explicitness、Executable Intent。
- **SDD vs Vibe Coding**: 对比表——trust 边界（risk/execution/live）必须走 SDD 全流程；周边工具代码可适度 Vibe Coding。
- **工具机制**: `@spec_claim` 装饰器→pytest 运行→Conformance Matrix→失败记入 BACKEND_ISSUES。
- **Clarify 阶段检查清单**: 7项（错误路径/空状态/边界值/并发/幂等性/安全/可观测性），全部勾选才进入实现。
- **每阶段 Checkpoint 制度**: 6个 Phase 各有对应 Checkpoint，Implement 阶段须 `uv run pytest tests/specs/ -q` 全绿 + Conformance Matrix 无新 gap。

**控制的代码模块**: `docs/design/*_contract.md`、`tests/specs/spec_*.py`、`tests/test_architecture_fitness.py`、`tests/test_spec_conformance.py`、`aletheia/risk/`、`aletheia/execution/`、`server/routes/live.py`

**Takeaway**: 这是 Aletheia SDD 的元契约，其价值在于建立了"spec→code，不可逆序"的强制约束和六阶段 Checkpoint 机制；一旦这个约束在实践中被绕过（如先改代码再补文档），整个 Conformance Matrix 退化为无意义的事后记录。

---

### 2.10 SYSTEM_MAP.md

**角色**: Aletheia 全模块概念地图，展示每个活跃组件的数据流方向、管道健康状态和已确认的断裂链路，是触碰任何数据路径前的飞行前检查清单。

**关键章节**:
- **第1层 数据采集层**: 8个调度任务，均写入命名 SQLite 表；管道缺口：`liquidation_events` 每2分钟写入但 `risk_engine` 未消费（⚠️A3）。
- **第2层 代理决策层**: 16个调度任务；核心链（paper_trader）标记 ✅；`confidence_calibration` ⚠️——`calibration_feedback` 字段从未在 `trading_decision.py` 提示词中渲染（D1）。
- **第3层 风控与执行层**: 4个模块，均 ✅ 闭环；对账在 demo 模式不运行（E1 缺口）。
- **第4层 知识与学习层**: `knowledge_items+knowledge_fts` ✅；`trading_guards` ✅；`ps_lessons`❌（A1：dreaming_agent 写入但 `retrieve_lessons()` 只读 `knowledge_fts`）；`ps_skills`❌（A2：无代理调用 `SkillStore().create()`）。
- **第5层 配置层**: 10个 Config 类的前端覆盖率——`RegimeConfig`(4字段)/`GuardConfig`(5字段)/`MarketAwareExitsConfig`(9字段) 均为0%。
- **第6层 已确认管道断裂**: 7条（A1-C2），含A级3条（数据积累无消费）、B级2条（shell存在但静默失败）、C级2条（能力存在但不可达）。

**控制的代码模块**: `aletheia/agent_runtime/scheduler.py`、`aletheia/agents/*`、`aletheia/knowledge/retrieval.py`、`aletheia/knowledge/manager.py`、`aletheia/config/schema.py`、`aletheia/server/routes/settings.py`、`frontend/src/`

**Takeaway**: 这是触碰任何数据路径前必读的单页参考文档；其核心价值是管道状态列（✅/⚠️/❌），7条已确认断裂（A1-C2）代表不需要新数据采集即可修复的廉价提升点。

---

### 2.11 trust_and_change_control.md

**角色**: Aletheia 信任边界的变更控制章程，定义因果链不变量、代理变更协议、UI 诚实规则、知识分类、晋级门计算规范和变更受控模块清单，所有四份信任边界 L2 契约均遵从本文档。

**关键章节**:
- **§1 信任不变量**: 因果链（trade_proposals→risk_decisions→orders→fills→positions）不可破坏或伪造；提案 FSM 4条允许转换；失败必须留下可检查行；审计必须写入规定字段集。
- **§2 代理变更协议**: 编辑前声明边界/不变量/非目标/验证命令；5条停止条件；10类需要先写回归测试的变更类别。
- **§3 UI 诚实规则**: 每个显示的运营状态须来自真实后端值；5个规范化的"未知状态"标签（not configured/not available/no data/blocked/warning）；明确禁止硬编码"健康"/"已连接"等正面状态。
- **§4 知识条目分类**: 恰好5种允许类型（research_note/strategy_memory/dreaming_log/daily_review/post_trade_review）；添加新类型须5处同步更新；禁止类型洗白。
- **§5 晋级门计算规范**: 8个门的精确默认阈值和计算规则，须来自存储记录。
- **§6 变更受控模块**: 7条路径，每次变更须5项声明；模块专属不变量（风控检查不可弱化、执行须过网关等）。
- **§7 模糊情况默认值**: 6条保守默认（缺失数据="not available"，空白名单拒绝所有等）。

**偏差与漂移**:
- **中等**: §4.1 声称恰好5种 `item_type`，但代码库实际使用至少9种额外未文档化类型（`trade_case_study`、`system_action`、`operator_assessment`、`guard_candidate`、`trading_guard`、`trading_memo`、`lesson/accepted_lesson`、`calibration_report`、`reasoning_feedback`）——schema 已在未更新章程的情况下扩展
- **中等**: §6.1 将 `aletheia/agent_runtime/permissions.py` 列为变更受控模块，但 `FORBIDDEN_INTENTS` 在 `agent_shell/registry.py:415`、`RESTRICTED_TOOLS` 在 `agent_runtime/tool_registry.py:39`——两者均未被列为受控模块，形成变更控制边界定义缺口

**控制的代码模块**: `aletheia/risk/`、`aletheia/execution/`、`aletheia/exchanges/bitget/auth.py`、`aletheia/exchanges/bitget/client.py`、`aletheia/config/secrets.py`、`aletheia/agent_runtime/permissions.py`、`aletheia/server/routes/live.py`、`aletheia/agent_shell/registry.py`（FORBIDDEN_INTENTS）、知识条目 schema、因果链表 schema、`data/logs/audit.jsonl`、`frontend/src/`（UI 诚实规则）

**Takeaway**: 这是 Aletheia 信任边界的总章程，因果链不变量、代理变更协议、UI 诚实规则均在代码中得到执行，但知识 item_type 枚举已在无章程更新的情况下扩展至少9个额外类型，且变更受控模块清单遗漏了实际持有关键安全常量的两个文件。

---

### 2.12 operator_layer.md

**角色**: Operator 层（Pi 风格顶级系统代理 Shell）的概念设计参考，定义四层架构、安全禁止模型、权限模式、集成阶段和会话/工作区模型。

**关键章节（精选）**:
- **§落锚说明**: 两条铁律——确定性自愈 Supervisor（已交付 8a6e008/abd7773）是稳定基础；LLM Apex Agent Shell 尚未启动且需等待 Phase 0 观察期通过才能开始。两套编号（Operator Stage 1-6 和 ROADMAP Phase 0-7）禁止混淆。
- **§6 核心 Pi 安全问题**: Pi 无内置权限系统，必须由 Aletheia 安全 Shell 包装；禁止直接访问 API 密钥/实盘/凭证/执行网关/硬风险限制/钱包材料。
- **§10 命令注册表**: 5类命令（读取/低风险写入/受控操作/提案命令/永久禁止）；5种权限模式；Forbidden 类别含 `modify_api_key`、`enable_live_directly`、`bypass_risk_engine`、`place_live_order_directly`、`modify_execution_gateway`、`hard_delete_knowledge`。
- **§12 应用上下文提供者**: `AppContextProvider` 返回7个结构化上下文节目；仅加载摘要上下文，按需检索详情——禁止把整个数据库倒入提示词。
- **§22 失败处理**: Agent Shell 自身失败时系统必须继续——Agent Shell 不得成为安全单点故障。

**偏差与漂移**:
- **严重**: §落锚说明引用 `superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md` 和 crosswalk 文档为权威实施蓝图——两个文件均不存在于磁盘，`docs/design/superpowers/` 目录不存在
- **中等**: §10/§11 将 `retire_strategy` 和 `demote_strategy` 描述为直接 Level 1 自动操作；代码实际通过 Phase 4d 间接层写入 `pi_decisions` 表（`operator_loop.py:553-578`）——架构变更未记录在契约中
- **轻微**: `loosen_confidence`、`increase_sizing`、`widen_stop_loss` 同时出现在 `_LEVEL_2_ACTIONS` 和 `_execute_level1_action` 分派中（dispatch 表不一致）
- **中等**: 无 `spec_operator_loop.py`——三级权限执行、速率限制不变量、升级阈值无可执行 spec 测试

**控制的代码模块**: `aletheia/agent_shell/operator_loop.py`、`aletheia/agent_shell/context.py`、`aletheia/agent_shell/registry.py`、`aletheia/agent_shell/permissions.py`、`aletheia/agent_shell/sessions.py`、`aletheia/agent_runtime/pi_daemon.py`、`aletheia/agent_runtime/scheduler.py`

**Takeaway**: 安全禁止模型（Level 1/2/3 分类、永久禁止命令、Pi 包装要求）是中等强度的概念控制面，但是权威实施蓝图缺失于磁盘、Phase 4d 间接层未文档化，且无任何可执行 spec claims。

---

### 2.13 PI_OPERATOR_DESIGN_DECISIONS.md

**角色**: PI Operator 代理的设计决策记录，定义权限级别、触发节奏、范围边界、速率限制和冷却规则，是 `operator_loop.py` 行为的权威意图文档。

**关键章节（精选）**:
- **问题3**: 三级权限模型——Level 1 AUTO（收紧操作）、Level 2 PROPOSAL（放松操作）、Level 3 FORBIDDEN（Kill Switch 停用/实盘切换/凭证修改）；每日最多3次补丁、同参数2小时冷却、连续3次同方向升级至 Level 2。
- **问题5**: 确定4h cron（hour='0,4,8,12,16,20', minute=15）。
- **问题6**: 完整的允许/提案/禁止能力矩阵，含每类操作的每日次数上限。

**偏差与漂移**:
- **中等**: `min_confidence_to_trade` 在文档中描述为数值 ±10（范围40-90）；代码 `PARAM_REGISTRY` 实现为分类阶梯 `['speculative','medium','high']`，步长1——数值框架与实现不符
- **中等**: `default_stop_loss_pct` 上限文档称8.0，代码 `PARAM_REGISTRY` 为5.0
- **中等**: 连续3次同方向升级规则在文档中明确规定，但 `operator_loop.py` 中不存在此逻辑（grep 确认无此方向计数代码）

**控制的代码模块**: `aletheia/agent_shell/operator_loop.py`、`aletheia/agent_runtime/scheduler.py`（pi_operator_loop 任务注册）

**Takeaway**: 核心安全不变量（Level 3 阻断、单调升级、速率限制、FORBIDDEN_INTENTS）在代码中忠实实现，但三个具体漂移项（数值框架错误、止损上限差异、3次同向升级规则缺失）降低了契约作为精确操作参考的可信度。

---

### 2.14 EQUITY_INTEGRATION_DESIGN.md

**角色**: 美股观察统一架构契约，将 yfinance（日线）和 Bitget xStocks（ON系列代币化股票）合并为单一 `equity_observer` 代理，覆盖符号统一、交叉验证、知识库路由和排名器影子轨道。

**关键章节（精选）**:
- **§2 统一架构**: `_XSTOCK_TO_TICKER` 反向映射表（实际名为 `_XSTOCK_BASE_TO_TICKER`）；yfinance 使用 `{TICKER}/USD`（代码实际为 `{TICKER}/USDT`）；xStocks 保持 `AAPLON/USDT`。
- **§3 数据流**: `equity_observer` 每30分钟运行（美股交易时段），yfinance 90天日线→股票特征→`market_ohlcv`；可选 xstock 抓取；交叉验证偏差率；LLM 接收合并快照。
- **§4 Opportunity 扩展**: `asset_subclass` 和 `canonical_ticker` 通过 `_ADDITIVE_COLUMNS` 迁移添加（无破坏性 schema 变更）；始终设 `execution_eligible=False`。
- **§8 xStocks 数据真实性**: 偏差>2%设 `premium_alert=True`；偏差>5%将 xstock 数据从 LLM 上下文完全排除。

**偏差与漂移**:
- **中等**: 文档§2.2 规定 yfinance 路径符号为 `{ticker}/USD`；代码 `equity_observer.py:399` 创建 Opportunity 用 `{ticker}/USDT`——`/USD` vs `/USDT` 功能性不匹配
- **中等**: xStocks 启用文档称"自动从映射表派生"（deprecated `observed_xstocks` 作为配置驱动）；代码 `equity_observer.py:285` 仍 gate-check `xstock_symbol in observed_xstocks`——手动配置仍然必须
- **中等**: §6.3 规定置信度>0.7时写 `research_note` 回 KB；`equity_observer.py` 无此写回路径——Phase 3 未实现
- **轻微**: `skip_reason` 文档称 `equity_observation_only`，代码使用 `equity_shadow_track`

**控制的代码模块**: `aletheia/agents/equity_observer.py`、`aletheia/data/symbols.py`、`aletheia/storage/models.py`、`aletheia/storage/migrations.py`、`aletheia/knowledge/router.py`、`aletheia/agents/paper_trader_ranker.py`、`aletheia/agent_runtime/scheduler.py`、`aletheia/config/schema.py`

**Takeaway**: Phase 1+2 完整实现，但三个具体漂移（`/USDT` vs `/USD`、xStocks 仍需手动配置、Phase 3 知识库写回缺失）加之无任何 spec 覆盖，意味着漂移可能随时间进一步积累而无自动检测。

---

### 2.15 PREDICTION_MARKET_DESIGN.md

**角色**: L2 预测市场观察模块设计契约，文档化当前仅观察状态、持久化 schema、serenity_evidence 集成，以及三条潜在盈利路径（MVP 期间明确不追求）。

**关键章节（精选）**:
- **§1 当前状态**: Polymarket Gamma API 只读摄取；LLM 概率动量分析；持久化4张表；`execution_eligible=False`；`asset_class='prediction'`；Paper Trader 无条件跳过。
- **§2-4 盈利路径与决策**: 三条路径均未实现；MVP 期间保持仅观察模式；类别回退 bug 已修复（commit 831599c）。
- **§5 架构图**: 数据流从 Polymarket Gamma API 至 prediction_observer 至5条分支（持久化、LLM 分析、Opportunity 插入、启发式声明、serenity evidence）。

**载荷断言**:
- `execution_eligible=False` 在所有预测 Opportunity 上——预测信号绝不进入执行通道 (`prediction_observer.py:359`)
- `asset_class='prediction'` 使 Paper Trader 无条件跳过 (`prediction_observer.py:358`)
- 持久化写入4张表：`prediction_events, prediction_markets, prediction_probability_snapshots, prediction_market_claims`
- `serenity_evidence` 仅当 `|Δ24h| > 10pp` AND `volume > $50k` AND 加密/宏观关键词时写入 (`prediction_observer.py:396-430`)

**偏差与漂移**:
- 任务描述称写入 `prediction_market_runs`；实际表名为 `prediction_observer_runs`（`repositories.py:180`）——文档本身未命名此表，是文档遗漏非代码漂移

**控制的代码模块**: `aletheia/agents/prediction_observer.py`、`aletheia/data/polymarket_provider.py`

**Takeaway**: 仅观察不变量（`execution_eligible=False`、`asset_class=prediction`）在代码中正确执行，文档整体忠实，但无 spec 覆盖且文档遗漏运行追踪表名，轻微降低文档完整性。

---

### 2.16 冻结文档扫描（Frozen Sweep）

以下文档被标记为"已不具有当前相关性"的冻结历史记录：

| 文档 | 状态 | 核心价值 |
|------|------|----------|
| `overview_redesign.md` | 已过时（2026-06-07会话记录） | Cockpit 设计语言锁定（Bloomberg/TradingView/OpenAlice）；两个后端任务已完成 |
| `webui_page_map.md` | 已过时 | 14页→10目的地合并决策；Proposals 提升为"审批台"仍有效 |
| `webui_backend_handoff.md` | **仍相关** | #4新闻摄取永久禁止（MVP范围扩展）；外部仓位禁止写入本地positions表；§13指数行情待建 |
| `2026-06-07-merge-postmortem-design.md` | 已过时 | 合并决策记录；portfolio-sync-gap 根因已记录 |
| `2026-06-10-pi-operator-agent-design-audit.md` | 已过时 | 设计阶段完成证明；2026-06-11注记称已由 ROADMAP/CURRENT_TASK/AI_HANDOFF 接管 |
| `2026-06-10-pi-operator-agent-dual-process-design.md` | 已过时 | Node/TS 双进程架构决策；已被 operator_layer.md + PI_OPERATOR_DESIGN_DECISIONS.md 提炼 |
| `2026-06-10-pi-operator-agent-design-crosswalk.md` | 已过时 | 参考实现23节完整覆盖证明；已被提炼文档取代 |

注意：`webui_backend_handoff.md` 包含一个仍有效的活跃待办项（§13 指数行情端点 `GET /api/market/indices` 待建）和一个永久禁止决策（新闻摄取），不可将其视为纯历史文档。

---

## 三、Spec-as-Code 闭环映射表

| Contract | Spec file | Claims | L2a | L2b | L2c | Coverage gap |
|----------|-----------|--------|-----|-----|-----|--------------|
| risk_engine_contract | spec_risk_engine.py | 9 | 4 | 4 | 1 | Check #1-4, #6-14 无行为测试；kill switch 持久化无测试；两条仓位计算路径无测试 |
| execution_contract | spec_execution.py | 7 | 4 | 2 | 1 | TPSL行为、原子性/幂等性、审计写入、reconcile只读不变量无L2b/L2c |
| live_unlock_contract | spec_live_unlock.py | 5 | 2 | 2 | 1 | 实际解锁行为（前置条件+审计+单向性）无spec_claim；操作员权限面无覆盖 |
| promotion_gates_contract | spec_promotion_gates.py | 7 | 4 | 2 | 1 | 高频运行时长放松分支无行为测试；实盘晋级附加守卫无测试；退役路径无测试 |
| data_model | spec_data_model.py | 7+1 | 7 | 1 | 0 | was_in_tx模式无测试；JSON列处理无测试；并发恢复协议无测试；无L2c |
| agent_contracts | spec_agent_contracts.py | 8 | 8 | 0 | 0 | 每任务超时机制、LLM降级、catch-all隔离、PI Agent安全不变量均无L2b/L2c |
| api_reference | 无 | 0 | 0 | 0 | 0 | **全部信任边界端点义务无覆盖**（confirm门、审计写入、无原始密钥、FORBIDDEN_INTENTS守卫） |
| data_layer | 无 | 0 | 0 | 0 | 0 | 每调用工厂、规范符号格式、CCXT专用数据路径无测试 |
| operator_layer / PI_OPERATOR | 无 | 0 | 0 | 0 | 0 | 三级权限执行、速率限制、升级阈值、永久禁止动作无可执行spec |
| equity_integration | 无 | 0 | 0 | 0 | 0 | Phase 1+2实现无L2a/L2b覆盖 |
| prediction_market | 无 | 0 | 0 | 0 | 0 | 仅观察不变量无spec覆盖 |
| ui_design_system | 无 | 0 | 0 | 0 | 0 | 纯约定文档，无L2a/L2b/L2c |

---

## 四、架构 Fitness 函数清单

| Test function | Purpose | Contract anchor |
|---------------|---------|-----------------|
| test_risk_engine_does_not_import_agents | 验证 risk/engine.py 不导入 aletheia.agents/* （单向数据流） | risk_engine_contract §1（隐含不变量，无明确章节） |
| test_risk_engine_does_not_import_agent_shell | risk/engine.py 不依赖 agent_shell/（信任边界隔离） | live_unlock_contract §6 信任边界 |
| test_execution_gateway_does_not_import_agent_shell | Execution Gateway 不依赖 agent_shell/（信任层分离） | execution_contract §1 / trust_and_change_control §6.3 |
| test_agent_shell_does_not_import_exchange_auth | agent_shell 三个模块不导入交易所鉴权或 secrets | live_unlock_contract §6（代理永久不可达凭证路径） |
| test_forbidden_intents_complete | FORBIDDEN_INTENTS 包含契约规定的全部 7 个条目 | live_unlock_contract §6 |
| test_restricted_tools_complete | RESTRICTED_TOOLS 包含全部 5 个工具名 | live_unlock_contract §6（与 LU-B02 重叠但验证完整性） |
| test_risk_engine_eval_lock_exists | _eval_lock threading.Lock 存在于 risk/engine 模块 | risk_engine_contract §1（与 RE-S02 重叠） |
| test_causal_tables_not_in_prunable_whitelist | 7张信任因果表不在 _PRUNABLE 中 | data_model §7.2（验证排除性，DM-S04 验证有界性） |
| test_migration_schema_is_additive_only | SCHEMA_SQL 不含 DROP TABLE/DROP COLUMN/RENAME | data_model §6 迁移策略（无对应 spec_claim） |
| test_live_config_default_is_disabled | LiveConfig.state 默认值为 live_disabled | live_unlock_contract §1（与 LU-S01 重叠） |
| test_kill_switch_module_has_required_interface | kill_switch.py 暴露 is_active/activate/deactivate | risk_engine_contract §4 Kill Switch 接口 |

---

## 五、未契约化的隐含约束 + 契约未覆盖章节

### 未契约化的隐含约束（Fitness 测试覆盖但无对应契约章节）

- `test_risk_engine_does_not_import_agents`：依赖方向检查（risk→agents 禁止），risk_engine_contract 无导入分层章节
- `test_risk_engine_does_not_import_agent_shell`：同上（risk→agent_shell 禁止），无契约章节引脚
- `test_execution_gateway_does_not_import_agent_shell`：同上（execution→agent_shell 禁止），execution_contract 无导入分层章节
- `test_agent_shell_does_not_import_exchange_auth`：agent_shell→exchanges/bitget/auth 和 config/secrets 禁止，live_unlock_contract §6 表达了意图但无结构性导入方向断言
- `test_migration_schema_is_additive_only`：SCHEMA_SQL 不含破坏性 DDL，这是 data_model §6 的核心加载断言，但无对应 `@spec_claim`

### 契约已定义但无 Spec 覆盖的章节

- risk_engine_contract §2：check #1-#4 和 #6-#14（13个关卡）无行为测试
- risk_engine_contract §4：Kill Switch 激活/停用/持久化/审计写入无 spec_claim
- risk_engine_contract §5：两条仓位计算路径（paper_trader 固定 $25 vs PI Kelly 缩放）无 spec_claim
- execution_contract §2：提案状态机全转换路径（pending→approved→executed/rejected/error）的行为验证缺失
- execution_contract §3：broker 路由 L2b 测试（paper_internal→paper_broker, live→live_broker）缺失
- execution_contract §4：TPSL 挂单时机、持久化、平仓前取消——完全无 spec 覆盖
- execution_contract §5：原子性/幂等性/失败语义——完全无 spec 覆盖
- execution_contract §6：对账只读不变量（绝不下单/取消/伪造Fill）无行为级测试
- execution_contract §7：审计轨迹写入无 spec_claim
- live_unlock_contract §2：实际解锁行为（confirm前置条件、审计事件发出、单向方向）无 spec_claim
- live_unlock_contract §3：审计格式精确 JSON 字段无测试
- live_unlock_contract §5：操作员权限面双梯度（ReadOnly→Auto）及默认 ReadOnly 无 spec_claim
- promotion_gates_contract §4 偏差1：高频运行时长放松分支（30次交易→3天）无行为测试
- promotion_gates_contract §5：实盘晋级附加守卫无 spec_claim
- promotion_gates_contract §6：三条退役路径无 spec_claim
- data_model §1：`was_in_tx` 写前捕获模式——历史 DB 锁根本原因修复，无 spec_claim
- data_model §8：JSON 列容错读取无测试
- data_model §9：并发恢复协议（`rollback_if_in_transaction` 调度器包装）无测试
- agent_contracts §1：每任务超时机制无 spec_claim
- agent_contracts §3：LLM 降级（`complete_with_fallback`、瞬态错误检测、300s 冷却）无 spec_claim
- agent_contracts §3：每任务 catch-all 隔离、连续3次失败告警无 spec_claim
- agent_contracts §4：PI Agent 安全不变量（绝不执行订单）无 spec_claim

---

## 六、代码控制矩阵

| Code module | Primary contract | Secondary contract | Spec file | Coverage strength |
|-------------|-----------------|-------------------|-----------|-------------------|
| aletheia/risk/engine.py | risk_engine_contract | trust_and_change_control | spec_risk_engine.py | 中（结构强，行为弱——13关无L2b） |
| aletheia/risk/kill_switch.py | risk_engine_contract §4 | trust_and_change_control | spec_risk_engine.py（间接） | 弱（仅接口存在性测试） |
| aletheia/execution/gateway.py | execution_contract | trust_and_change_control | spec_execution.py | 中（入口点有测试，原子性/幂等性无） |
| aletheia/execution/bitget_broker.py | execution_contract §4,§5 | trust_and_change_control | spec_execution.py | 弱（broker存在性，无行为测试） |
| aletheia/execution/reconciliation.py | execution_contract §6 | trust_and_change_control | spec_execution.py（仅EX-S04） | 弱（仅可调用性，只读不变量无测试） |
| aletheia/server/routes/live.py | live_unlock_contract | trust_and_change_control | spec_live_unlock.py（间接） | 弱（实际解锁行为无spec_claim） |
| aletheia/agent_shell/registry.py | live_unlock_contract §6 | agent_contracts §3 | spec_live_unlock.py | 强（FORBIDDEN_INTENTS结构和值完整覆盖） |
| aletheia/agent_shell/permissions.py | live_unlock_contract §6 | operator_layer §10 | spec_live_unlock.py | 强（check_permission行为有L2b测试） |
| aletheia/agent_runtime/tool_registry.py | live_unlock_contract §6 | — | spec_live_unlock.py | 强（RESTRICTED_TOOLS权限类别有测试） |
| aletheia/trading/strategy_lifecycle.py | promotion_gates_contract | trust_and_change_control | spec_promotion_gates.py | 中（状态机和门值强，实盘晋级守卫弱） |
| aletheia/strategy/orchestrator.py | promotion_gates_contract §6 | — | spec_promotion_gates.py（间接） | 弱（自动退役逻辑无直接spec） |
| aletheia/agent_runtime/scheduler.py | agent_contracts | PI_OPERATOR_DESIGN_DECISIONS | spec_agent_contracts.py | 中（任务数有测试，频率/超时无） |
| aletheia/storage/sqlite.py | data_model §1,§9 | agent_contracts §3 | spec_data_model.py | 强（WAL/isolation_level/threading.local覆盖） |
| aletheia/storage/repositories.py | data_model §7 | execution_contract §5 | spec_data_model.py | 中（_PRUNABLE有测试，was_in_tx模式无） |
| aletheia/storage/migrations.py | data_model §3,§6 | execution_contract（因果表） | spec_data_model.py | 强（因果表/运行时表/capital_events均有测试） |
| aletheia/storage/models.py | execution_contract §2 | data_model §4 | spec_execution.py（间接） | 弱（模型存在性，状态机无测试） |
| aletheia/files/jsonl.py | data_model §7 / execution_contract §7 | trust_and_change_control §1.4 | — | 弱（无直接spec） |
| aletheia/config/schema.py | live_unlock_contract §1,§5 | data_layer §7 | spec_live_unlock.py（LU-S01） | 中（LiveConfig默认值有测试，其他字段无） |
| aletheia/data/ccxt_provider.py | data_layer §2,§3,§9 | — | 无 | 弱（无spec） |
| aletheia/data/symbols.py | data_layer §0 | — | 无 | 弱（无spec） |
| aletheia/data/providers.py | data_layer §2 | — | 无 | 弱（无spec） |
| aletheia/trading/instruments.py | data_layer Phase 3 | — | 无 | 弱（Phase 3.1/3.6 未完成，无spec） |
| aletheia/agent_shell/operator_loop.py | PI_OPERATOR_DESIGN_DECISIONS | operator_layer | 无 | 弱（无spec，三个参数漂移） |
| aletheia/agents/equity_observer.py | EQUITY_INTEGRATION_DESIGN | agent_contracts §2.A | 无 | 弱（无spec，三个功能性漂移） |
| aletheia/agents/prediction_observer.py | PREDICTION_MARKET_DESIGN | agent_contracts §2.A | 无 | 弱（无spec） |
| aletheia/knowledge/manager.py | trust_and_change_control §4 | agent_contracts §4 | spec_agent_contracts.py（AC-S04） | 中（CRUD存在性有测试，detect_contradictions stub无测试） |
| aletheia/knowledge/retrieval.py | SYSTEM_MAP §4（A1缺口锚点） | — | 无 | 弱（A1 ps_lessons vs knowledge_fts解耦无测试） |
| aletheia/agent_shell/context.py | operator_layer §12 | — | 无 | 弱（无spec） |
| aletheia/server/app.py | api_reference | trust_and_change_control | 无 | 弱（_PROTECTED_PATHS存在但无spec测试信任边界义务） |
| aletheia/server/routes/risk.py | api_reference（偏差）| risk_engine_contract | 无 | 弱（kill switch合并端点未文档化，无spec） |
| frontend/src/ | ui_design_system | trust_and_change_control §3 | 无 | 弱（纯约定，无可执行spec） |

---

## 七、综合判断 — 这套文档体系能多大程度地控制代码?

### 强项

核心信任边界的控制面是真实有效的。四份信任边界契约（risk_engine、execution、live_unlock、promotion_gates）的主要结构不变量均在代码中得到精确实现：16个 Risk Engine 关卡存在、序列化锁存在、FORBIDDEN_INTENTS 7项完整、RESTRICTED_TOOLS 5项完整、`ORDER(pending)` 先于交易所调用插入、对账文件无 `place_order` 调用、`live_disabled` 默认状态、`LIFECYCLE_STATES` 10个状态和 `DEFAULT_PROMOTION_GATES` 7键精确值——这些都经过 spec_*.py 的自动化验证，Fitness 测试在秒级内检测依赖方向违规。SQLite 并发三支柱（WAL + threading.local + was_in_tx 前置捕获）经过充分文档化并有 spec_data_model.py 的结构级验证。对于一个处于 MVP 阶段的系统，这个覆盖水平已经为资金安全提供了有意义的工程保障。

### 弱项

控制面在中间层和子系统层显著变薄。在契约层面，`api_reference.md` 存在三个影响信任的具体漂移（Kill Switch 合并端点、实盘解锁路径前缀、两个风险端点未记录），且完全无 spec 覆盖——这是用户最能触达的信任边界操作，却是整个文档体系中覆盖最弱的部分。在 spec 层面，L2b 行为测试仅占 35 个 claims 的 26%（9个），L2c 不变量测试仅 3 个（9%）；最高爆炸半径的路径（execution §4 TPSL、§5 原子性/幂等性、§6 对账只读）完全无 spec 覆盖。6个子系统文档（operator_layer、PI_OPERATOR、equity_integration、prediction_market、data_layer、ui_design_system）均无任何 spec_*.py 文件，其漂移只能通过人工代码审查发现。`trust_and_change_control.md §4` 声称知识系统恰好 5 种 item_type，但代码已静默扩展至至少 14 种——这是"文档是真相"原则在实践中被侵蚀的典型案例。

### 与第一性原理的契合度

VISION 的核心赌注是"学习装置 + 失败可生还"——系统在失败后必须留下可检查的记录，并能从失败中学习。SDD 文档体系对"失败可生还"的支持是真实的：因果链永久性、Kill Switch 持久化、对账只读、审计链防篡改这些不变量都有文档化并部分有测试。对"学习装置"的支持则较弱：A1（ps_lessons 与 knowledge_fts 解耦）、A2（ps_skills 无生产者）、D1（calibration_feedback 未注入提示词）这三个管道断裂点直接损害了系统从历史中学习的能力，而这些断裂点在 SYSTEM_MAP.md 中有清晰记录但在 spec 层面缺乏可执行的断言。换言之，文档体系能够防止系统"灾难性失败"，但还不能确保系统"学习循环闭合"。

### 加固建议（按优先级）

1. **立即修正 promotion_gates_contract §4 偏差2**：将 PI 审批门从"未实现"改为"已实现，锚定 `strategy_lifecycle.py:167-172`"——这是文档声称代码有缺陷而代码实际运作正常的严重失效标注，会误导所有后续开发者。
2. **为 execution §4/§5/§6 添加 L2b spec claims**（优先级最高）：TPSL 挂单的非致命行为、`Order(pending)` 先于交易所调用的原子性、对账只读不变量——这三个路径的回归直接影响实盘资金，但目前完全无自动化捕获。
3. **为 api_reference Kill Switch 路径修正文档**：将 `POST /kill-switch/activate` + `POST /kill-switch/release` 更正为单一 `POST /api/risk/kill-switch {active: bool}`，并明确"发布时缺少确认要求"是已知代码缺陷待修，而非已实现的安全保证。
4. **为 operator_loop 添加 spec_operator_loop.py**：至少覆盖 Level 3 永久阻断（`_LEVEL_3_ACTIONS` 中的所有动作）、单调升级（`max(base_level, action.level)`）和速率限制（`_MAX_CONFIG_PATCHES_PER_DAY=3`）三个 L2c claims——这是运行中影响资金风险参数的代理，缺乏可执行规范是一个漏洞。
5. **为 data_layer.md Phase 3 未完成项创建具体 issue**：`tradable_symbols_from_config()` 仍返回 Bitget 格式而非规范格式，在 `paper_trader` 过滤器处存在静默的符号格式不匹配风险；添加一个 L2b spec_claim 用 `xStocks` 机会的符号匹配来捕获此回归。

---

## 八、给开发者的快速行动卡

**规则1：触碰任何代码文件前，先找其主控文档**
打开`docs/design/`，对照六、代码控制矩阵定位 Primary contract。若该文件在"信任边界"（`aletheia/risk/`、`aletheia/execution/`、`aletheia/exchanges/bitget/auth.py`、`config/secrets.py`、`agent_runtime/permissions.py`、`server/routes/live.py`），则必须执行 SDD 全流程：先改契约文档→声明 `SpecClaim(status="missing")`→写 RED 测试→实现代码→GREEN→提交。如果你无法找到主控文档，先停下来问——"没有文档的代码改动"在此项目中是明确违规。

**规则2：改完代码后立即运行三条命令，任何一条红就不得提交**
```
uv run pytest tests/test_architecture_fitness.py -q   # <1s，依赖方向
uv run pytest tests/specs/ -q                          # <2s，全部spec claims
uv run pytest tests/test_spec_conformance.py -v -s    # 打印Conformance Matrix，确认无新gap
```
这三条命令是 Implement 阶段 Checkpoint 的全部验证要求。特别注意：Conformance Matrix 中出现新的 `gap` 意味着你修改了某个有 spec_claim 的模块但没有同步更新 spec——这是文档与代码分叉的早期信号。

**规则3：发现文档与代码不符时，永远先改文档（除非文档是过时失效标注）**
本报告记录了若干"文档声称代码有问题，代码实际正确"的失效标注（如 risk_engine §7 的 logger NameError、promotion_gates §4 偏差2 的 PI 审批门"未实现"），以及"文档声称功能存在，代码确实有漏洞"的真实缺口（如 api_reference Kill Switch 路径分裂）。判断规则：用 grep 核实代码，如果代码已正确实现，改文档标注为"已解决"；如果代码确实缺失安全保证（如 Kill Switch 发布缺少确认），将其记录在 `docs/issues/domain/BACKEND_ISSUES.md` 并创建 `SpecClaim(status="missing")` 以追踪修复——绝不以"文档写了就当实现了"蒙混过关。