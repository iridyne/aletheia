# Strategy 模块重构（Position B）实施计划

> 2026-07-02 · 源：多 agent 存废分析（证据 6-lens + 3 立场 + 红队 + 综合）
> 判决：**Position B — 重构为 Trading Thesis**，嫁接 Position C 的「regime 归位 scanner-family」洞见 + 保留 Position A 的 SP-S03 KPI 接线。
> 本批执行范围：**P0 → P1**（P2 拆量化引擎 / P3 信任边界 #14 闸 = 本批不动，须 owner 单独授权）。

---

## 0. 为什么是 B（推理链摘要）

- **PI 是唯一开仓决策者**（决策脊柱 §跳5 ★唯一开仓决策权★；owner Option A PI-central）。任何独立**做判断**的第二引擎与愿景冲突。
- **代码里 RuleSet 真在做判断且绕过 PI**：硬入场闸（`paper_trader_ranker.py:258-267`）、绕过 PI 的自动平仓（`paper_trader.py:262`、`position_manager.py:326-367`）、每日 backtest 退役。→ 范式冲突**代码上真实**。
- **但活体效果为零**：208 笔平仓中 `ruleset_exit_signal` = 0；84 策略 76 退役、0 曾到 paper_running。→ 冲突**行为上休眠**。**这是零回归拆弹窗口。**
- **backtest kill 在 5 笔样本上是统计噪音**（19 个退役策略 backtest PF≥2.0、零 review_notes）。删它不损失真信号。
- **真正缺的原语是 THESIS**（可证伪的「regime+scanner+方向→edge」+ 活体证据）。

### 完整性核查发现（比原始 workflow 立场更准的两点）

1. **THESIS 存储已部分存在**：`universe_forward_outcomes`（表）+ `outcome_recorder.py`（登记/结算）+ `repo.calibration_stats()`（按 scanner_family×horizon 聚合胜率）+ Tier-1 `_maybe_generate_guard_candidates`（按 family+direction 亏损自动产 guard 候选）**已经**是一个 scanner-family+direction 的活体结果+自进化闭环。**不要重建冗余表。** 真缺口只有一处：这套 per-family 证据**没有在决策时点喂给 PI**——`trading_context` 目前只把它压成一个 universe-wide 的 `signal_win_rate_24h`（`trading_context.py:706-737`），PI 看不到「momentum:long 这一族最近 N 单胜率」。
2. **owner 已明确否决低维硬拦截**（记忆 `aletheia-negfeedback-high-dimensional-evolve`）：要高维模式归因 + 可被后续证据推翻的 self-evolve，**不要** symbol/dir/scanner 低维硬 block。→ P1b 的「regime 过滤归位 scanner-family」**不能做成新的硬闸**，只能做成 **flag-gated 默认关 + 软性/证据性**。同时也避免污染 owner 正在跑的 Phase 2A 干净窗观察。

---

## 1. 执行范围与优先级

| 优先级 | 动作 | 行为风险 | 本批 |
|---|---|---|---|
| **P0** | 拆引信：移除 RuleSet 绕过 PI 的自动平仓 | 零（活体触发 0 次）| ✅ 执行 |
| **P1a** | 把已有 forward-outcome per-family 证据喂给 PI 决策上下文 | 纯增量 telemetry，不改决策 | ✅ 执行 |
| **P1b** | regime/方向 affinity 归位 scanner-family 配置（flag-gated 默认关，软性）| 默认关=零行为变化 | ✅ 执行（休眠）|
| P2 | 删 dsl/evaluator/backtest/orchestrator/ruleset_scanner + FSM | 大 | ❌ 本批不动 |
| P3 | 风控 #14 闸改 scanner-family 证据（信任边界）| live 路径 | ❌ 须 owner 授权 |

---

## 2. P0 — 拆引信（RuleSet 自动平仓）

### 目标
RuleSet 退出信号**不再直接 close_position**。退出权归 SL/TP/max_hold/PI。趁 `paper_running=0` 零回归拆弹。

### 改动
1. `aletheia/agents/paper_trader.py`
   - `run_paper_trader` Path A 循环（:159-174）：移除 `exit` 分支的 `_handle_ruleset_exit` 调用；exit 信号丢弃（记 log，不平仓）。entry→signal_pending 路径保留（已是 PI-central）。
   - 删/gut `_handle_ruleset_exit`（:262-275）。
2. `aletheia/portfolio/position_manager.py`
   - `evaluate_open_positions`（:323-331 + :366-367）：移除 `scan_active_rulesets()` 采集 + `("ruleset_exit_signal", price)` 决策分支。SL/TP/max_hold/market-aware/thesis_invalidated 全部保留。

### SDD
- `tests/specs/spec_pipeline_position_manager.py` 新增 **PL-PM-08**（L2b）：「有 open position + 该 symbol 的 RuleSet exit 触发时，`evaluate_open_positions` 不因 ruleset 平仓（退出权归 SL/TP/max_hold/PI）」。RED→实现→GREEN。

### 验证
- `uv run pytest tests/specs/spec_pipeline_position_manager.py -q`（PL-PM-01~08 全绿，尤其不破 SL/TP/max_hold）
- `uv run pytest tests/test_position_manager.py -q`
- `aletheia doctor`

---

## 3. P1a — 把 per-family thesis 证据喂给 PI（复用现有存储，不建新表）

### 目标
PI 决策时能看到「这一族+方向最近的活体 edge」——把 `calibration_stats()`（已存在）的 per-(scanner_family, direction[, regime]) 胜率/forward_return 注入决策上下文，按候选的 scanner_family 对齐。

### 改动
1. `aletheia/agent_shell/trading_context.py`
   - Layer 1/2 候选块（每个 ranker 候选携带 `scanner_family`）旁，附 `family_edge`：`{n_settled, win_rate, avg_forward_return}`，来源 `repo.calibration_stats()`（若无 regime 维度则先用全局；见下）。
   - 保留现有 `signal_win_rate_24h` 不动。
2. `aletheia/agent_shell/trading_decision.py`
   - prompt builder 渲染候选时追加 family_edge 一行（人类可读），供 PI 加权 conviction。纯上下文，不加硬门。
3.（可选，若成本低）regime 维度：`universe_forward_outcomes` 加 `regime_state` 列（addcol）+ `outcome_recorder.register_pending` 写入当时 regime；`calibration_stats` 支持按 regime 分组。**若改动过大则本批只做 family+direction 维度，regime 维度记入后续。**

### SDD
- 新增 **TH-S01**（L2b，contract `trading_thesis_evidence`）：「trading_context 为携带 scanner_family 的候选附 family_edge（n/win_rate/avg_return），来源 calibration_stats」。
- 注册进 `tests/test_spec_conformance.py` 硬编码导入（避免收集陷阱，见记忆 `aletheia-sdd-spec-collection-trap`）。

### 验证
- 新 spec 绿；`trading_context` 单测；`aletheia doctor` 的 content.* 探针不退化。

---

## 4. P1b — regime/方向 affinity 归位 scanner-family（flag-gated 默认关，软性）

### 目标
把 regime/方向 affinity 从（90% 已死的）strategy record 解耦，做成 **scanner-family 属性**；ranker 对 `opportunity.scanner_family` 查表得 regime 适配度。**默认关**（`cfg.universe.scanner_regime_filter_enabled=False`）；开启时**软性**（评分惩罚/证据标注），不做低维硬 block（遵 owner 高维可推翻指令 + 不污染干净窗）。

### 改动
1. `aletheia/config/schema.py`
   - 新增 `scanner_regime_affinity: dict[str, dict]`（family → {direction_bias, applicable_regimes}）默认合理值；`scanner_regime_filter_enabled: bool=False`（dormant flag）。
   - 前端 Settings 补对应控件（CLAUDE.md Config↔UI rule）。
2. `aletheia/agents/paper_trader_ranker.py`
   - 新增 `_check_scanner_regime_fit(scanner_family, direction, regime)`（查 config 表，不依赖 strategy record）。
   - 仅当 flag 开：作为**评分调整**接入 `_compute_trade_score`（软），不 early-return skip。
   - 现有 `_check_strategy_regime_fit`（strategy-linked，SP-S04）**保持不变**（P2 才移除）。
3. 前端 Settings 页新增控件。

### SDD
- 新增 **SP-S06**（L2b）：「flag 关时 scanner_regime_fit 零行为影响；flag 开时对逆 regime 方向施加评分惩罚而非硬 skip」。
- 不破 SP-S04（strategy 路径）/ SP-S05（short_bias 可达）。

### 验证
- 新 spec 绿；SP-S04/S05 不回归；flag 默认关 → ranker 输出与改前一致（回归对照）。

---

## 5. 完整性缺口核查表（goal step 3a）

| # | 缺口 | 核查动作 | 状态 |
|---|---|---|---|
| 1 | spot-parity 未合并分支交互 | 本批改动文件：paper_trader/position_manager(exit)、trading_context/trading_decision(telemetry)、ranker(regime 评分)、schema/repositories/Settings/spec。**未碰** okx/spot adapter、`market_type` 默认、决策链 direction 逻辑 → 与 spot-parity 分支无函数级冲突 | ✅ 无冲突 |
| 2 | Serenity 归因链 | serenity 退出走 SL/TP/max_hold/thesis_invalidated（P0 均保留），`_close_serenity_feedback` 未动；`test_serenity_p0_p3_e2e.py` 全绿 | ✅ 已核 |
| 3 | Kelly sizing 用 strategy_id 计数 | `_get_strategy_paper_trade_count` 未改；P1b 未改 strategy_id 归因（只加 scanner-family 评分）| ✅ 未动 |
| 4 | 历史数据保留 | 本批零 DROP/ALTER 删列；仅新增 1 config 字段 + 1 只读 repo 查询 | ✅ 设计即保留 |
| 5 | doctor 探针查 strategy | `aletheia doctor` 实跑：content.strategy_health(84 策略无 zombie)/content.attribution(0 缺失)/content.signal_settlement(115k 结算 current) 全绿；仅 2 个 WARN=reconciled_ratio + proposal_bias，均为改前既存的 Phase 2A 观察对象，**无新增假警** | ✅ 已核 |

## 6. 验证（goal step 3b）

- 定向 spec：`uv run pytest tests/specs/spec_pipeline_position_manager.py tests/specs/spec_strategy_pool.py tests/specs/spec_data_utilization.py -q`
- e2e：`uv run pytest tests/e2e/test_pipeline_position_to_close.py -q`
- 全量后台：`uv run pytest tests/ -q --tb=line`
- `aletheia doctor`（改后必跑）

## 7. 留给 owner 的判断题（证据决定不了）

1. 节奏：Phase 2A 干净窗观察中，P1b 是否保持默认关直到观察出结论？（本计划默认：关）
2. P3 #14 闸信任边界改动是否放行？（本批不动）
3. backtest equity 曲线（P2 才删）是否可接受永久失去？
4. B/C 最后一厘米：是否给「确定性规则退出」留未来位置？留→B（本计划）；不留→C（更激进删除）。
