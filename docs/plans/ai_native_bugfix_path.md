# AI-Native Bug-Fix Path — 让机器替人类发现、诊断、(分级)修复

**状态**: Phase 1+2 实施中(2026-06-28 起);Phase 3 研究中;Phase 4 已设计待排期。
**owner 决策来源**: 2026-06-28 会话(范围=先做 1+2,但 1→4 完整 plan 先落地;修复边界=选项1,选项3 作研究)。
**关联**: [`docs/_ai/DEV_PROTOCOL.md`] 意图/事实分离;[`docs/governance/trust_and_change_control.md`] 信任边界;CLAUDE.md「doctor 探针诚实模式」。

---

## 0. 问题陈述(owner 原话提炼)

最初战略是「一切基于 Web」,导致 CLI 长期被当作后端隐藏实现而功能羸弱。后果:当 AI 决策链产出**幻觉/杂乱数据**(最常出现在 Web UI 的 **Evolve** 模块下 Strategies / Learning / Serenity Lens 三个子区)时,**必须由人类肉眼进 Web UI 去发现**,再把问题带回给 AI。这种「human 负责 bug discovery」是不正常的、持续消耗 owner 时间与精力的。

目标:**让 AI agent 原生具备对运行态框架做 analysis + diagnostic 的能力**,自己通过 CLI 读取内部状态、检测异常、(分级)制定并执行修复,把 bug 修复尽量全自动化,减少 human-in-loop。

---

## 1. 关键纠偏:CLI 不是荒漠,已 80% 建好(实测,2026-06-28 七路并行调研)

| 现状 | 证据 |
|---|---|
| `aletheia inspect` **已有 22 个子命令**,覆盖全交易链(positions/proposals/risk/orders/fills/opportunities/universe/strategies/regime/microstructure/serenity/shadow/agent-runs/equity/calibration/factor-feedback/guards/scorecard/reflection/predictions/agent-status),**几乎全支持 `--json`** | `aletheia/cli/inspect_cmd.py`(`_output()` 统一 human/JSON 分发) |
| `aletheia doctor` **一次跑 23 个探针**(进程/配置/凭证/数据新鲜度/宏观/循环活性/利润停摆/集中度/学习健康/审计链/FTS/OHLCV) | `aletheia/diagnostics/doctor.py:build_report()` |
| 已有 3 层自主机制 | `health.py` 自愈;`agent_shell/operator_loop.py`(4h KPI 规则引擎);`operator-shell/` TS(RecommendationEngine+RepairEngine) |

**结论:这不是「重建 CLI」,而是补三个精准的洞。**

---

## 2. 三层缺口模型:看 / 诊断 / 修

### 第一层「看」(AI 的眼睛)—— 基本够,一个致命小洞
- **`doctor` 没有 `--json`**(头号瓶颈):`doctor_cmd.py` 只 `typer.echo` 人类格式 ASCII 表;AI agent 只能脆弱地刮文本抠数字。`Report`/`Check` 本来就是 dataclass,`dataclasses.asdict()` 一行即可序列化。→ **Phase 1**

### 第二层「诊断」(把"看到"变成"这里坏了")—— 系统完全瞎
- 23 探针 + 22 inspect **全部只查运行/新鲜/活着**,**零个**查「AI 产出的内容是不是幻觉/垃圾/语义崩」——而这恰是 owner 肉眼盯 Evolve 在找的东西。→ **Phase 2**

### 第三层「修」(plan + execute)—— 必须按性质分级(见 §4 边界原则)
- 3 层自主机制**全部只读 health 信号串 / KPI 聚合,无一读内容**。`memo_effectiveness`(在 `worktree-neg-feedback-loop-fix` 分支,**未合 master**)是唯一的内容-vs-结果比对种子,但其输出无人消费。→ **Phase 4**

---

## 3. 已覆盖 vs 净新(Phase 2 防重复造轮子的硬约束)

**已被现有探针覆盖,Phase 2 绝不重复:**
- `_probe_knowledge_flood` — PI window-30 组成(guards 是否进窗)
- `_probe_learning_retrieval` — 检索池 review:case 比 + guard 晋升停滞(candidate vs active)
- `_probe_reconciled_ratio` — reconciler 主导平仓
- `_probe_concentration` — 24h **成交(fills)** 集中度
- `_probe_profit_loop` — 停摆签名 + PI skip×N 归因
- `_probe_round1` / `_probe_signal_effectiveness` / `_probe_data_quality`

**Phase 2 净新内容探针(`content.*` 命名空间,7 个,均经确认未覆盖):**

| 探针 | 检测的异常 | SQL/不变量 | 复用 |
|---|---|---|---|
| `content.strategy_health` | 僵尸策略(`paper_running` 久但 ~0 成交)、被阻塞候选(`paper_candidate` 无 PI 批准) | `strategy_records` JOIN `positions` + `_has_pi_approval` | `build_strategy_kpis()`、`strategy_lifecycle._has_pi_approval()` |
| `content.memo_quality` | trading_memo 缺失 / 过期(`valid_until` 已过)/(字段存在时)`accuracy_score<0.4` | `get_latest_memo()` | `insight_synthesizer.get_latest_memo()` |
| `content.lesson_chain` | `ps_lessons` 全卡 `proposed`、`applied_policy_version` 全 NULL(lesson→policy 链死) | `SELECT status,COUNT(*) FROM ps_lessons` | 直接 `get_db()` |
| `content.attribution` | 干净窗内 ranker 来源 `pi_decisions.opportunity_id` 空链率 >10% | 见下;A_ruleset 诚实 NULL 不计 | `learning_window.clean_window_cutoff` |
| `content.shadow_trades` | 过期未结算影子单(`resolved_at IS NULL` 且 >24h) | `SELECT COUNT(*) FROM shadow_trades WHERE resolved_at IS NULL AND created_at<...` | 直接 `get_db()` |
| `content.signal_settlement` | `universe_forward_outcomes` pending 且 >48h(结算 job 坏/暂停) | `SELECT COUNT(*) ... WHERE status='pending' AND created_at<...` | 直接 `get_db()` |
| `content.proposal_bias` | **提案层**(风控前)方向偏置(concentration 只看风控后成交) | `SELECT direction,COUNT(*) FROM trade_proposals WHERE created_at>=...-24h` | 直接 `get_db()` |

**探针诚实模式(强制,CLAUDE.md 既定)**: owner 已关闭/已结案/历史已停的状态报 `info`(数字全留)而非 `warn`;`warn` 只留真缺陷。例:`content.attribution` 的 A_ruleset 来源 NULL 是诚实设计,不报警;`content.lesson_chain` 在 `auto_promote_enabled=false` 时报 info 说明「按配置预期」。

---

## 4. 修复边界原则(owner 拍板=选项1;选项3 研究)

| 类型 | 例子 | 自动化策略 |
|---|---|---|
| **数据/状态修复**(可逆、不碰源码) | 作废幻觉 memo→触发重新合成;跑 guard 晋升 dry-run→晋升;清理过期影子单;给僵尸策略标 `under_review` | **机器自动执行 + `write_audit`**,可回滚 |
| **代码 bug 修复**(改源码) | reflection 静默吞错;`trading_decision.py` 的 `opportunity_id=None` | **不自动改**:自动诊断出根因 + 草拟修复计划(Level-2 提案/diagnostic todo),交 owner(或 Claude Code 会话)批准 |

理由:系统拿真金白银交易,整套治理(SDD 先行、change-control、信任边界、「不经批准不改 risk/execution」)的存在意义就是挡住 agent 自动改决策链代码。**owner 被解放的是"找 bug"体力活,保留的是"批准改动"判断活。**

> **选项3(研究方向,不在本批实现)**:对**低风险、非信任边界**的代码 bug,允许 agent 自动改+跑测试+提 PR 等 owner review。前置条件:先明确划出「非决策链白名单」(信任边界清单见 trust_and_change_control.md §6.1 的**补集**),且必须过 SDD + 全量测试 + 人工 merge。研究产出另立文档。

---

## 5. 路线图

### Phase 1 — AI 的眼睛机器可读(本批,~半天)
1. `aletheia/cli/doctor_cmd.py` 加 `--json`:`print(json.dumps(dataclasses.asdict(report), ...))`,输出 `{generated_at, checks:[{name,severity,detail,fix_hint}], summary:{blockers,warns}}`。退出码沿用(0 无 blocker / 1 有)。
2. (可选,本批做)`GET /_infra/doctor` 端点复用 `build_report()`,让运行中的 gateway 不必 spawn 子进程即可被查。
3. 测试:`--json` 输出可被 `json.loads` 解析、字段齐全、severity 枚举正确。

### Phase 2 — doctor 内容质量探针族(本批,核心,~1–2 天)
1. 在 `doctor.py` 实现 §3 的 7 个 `content.*` 探针,注册进 `build_report()`。
2. 全部遵守探针诚实模式;每个探针 `try/except` 包裹(坏探针不拖垮整份报告,现有约定)。
3. 单测:每个探针在「有异常 / 无异常 / 空表」三态下的 severity 正确;复用 `tests/test_doctor.py` 的临时 DB 夹具风格(真实 DB,不 mock)。

**完成 1+2 的验收**:`aletheia doctor --json` 一次调用即可让 AI agent 拿到「运行健康 + 内容异常」全量机器可读清单——**bug 发现脱离人肉**。

### Phase 3 — 面向 agent 的统一诊断入口(研究 + 后续实施)
- `aletheia diagnose --json`(或复用 `doctor --json` + 一个 agent-facing wrapper):一次调用返回全量诊断,每条带 `evidence`(查询结果片段)、`severity`、可执行 `fix_hint`、以及 `fix_class`(`data` / `code` / `config`)分类——为 Phase 4 的分级路由打标。
- 研究点:是否需要把 inspect 的关键内容(strategy KPI、learning-funnel、serenity 评分)也并入这一份,做成 AI 的「单次全景体检」。

### Phase 4 — 分级自动修复(已设计,待排期)
- 把 `content.*` 异常接进**现有** `agent_shell/operator_loop.py:decide_actions()` 规则引擎(不新建框架)。
- **数据态异常** → 新增可逆 action(如 `invalidate_stale_memo`→`synthesize_trading_memo()`;`run_guard_promotion`;`resolve_orphan_shadow_trades`;`mark_strategy_under_review`),走现有 `_execute_level1_action` + `write_audit`。
- **代码态异常** → 走现有 `_create_proposal` / `create_diagnostic_todo`(operator-shell `RepairEngine`),产出根因 + 计划草案,等 owner 批。
- 复用:`memo_effectiveness.evaluate_prior_memo()`(待其从 neg-feedback 分支合并 master)、`operator_store.create_artifact()`、`insight_synthesizer.synthesize_trading_memo()`。
- 护栏:kill_switch / live 模式下不自动执行任何写动作;所有 action 审计可回滚。

---

## 6. 风险与不做什么
- **不**为追求自动化而绕过 SDD / 信任边界 / kill_switch。
- **不**在 Phase 2 重复 §3「已覆盖」的探针。
- **不**让任何探针变成写操作(doctor `build_report()` 纯只读契约不可破;写动作只在 `apply_fixes()` 与 Phase 4 的 operator action)。
- worktree 上线铁律:本批在 worktree 开发,合并走主仓 FF merge + restart + `doctor` 验证(空 data worktree 不 restart gateway)。

---

## 7. 实施进度(本文件随实施更新)
- [x] Phase 1.1 `doctor --json` — `Report.to_dict()` + CLI `--json`(`doctor_cmd.py`);契约 `{generated_at,healthy,checks[],summary{blockers,warns}}`
- [x] Phase 1.2 `GET /_infra/doctor` — sync def 复用 `build_report().to_dict()`(`routes/health.py`),运行中 gateway 免子进程可查
- [x] Phase 2 七个 `content.*` 探针 + 单测 — `content.strategy_health/memo_quality/lesson_chain/attribution/shadow_trades/signal_settlement/proposal_bias`;真实临时 DB 23 单测;时间戳比较走 Python isoformat cutoff(避开 SQLite `datetime('now')` 空格分隔同日比较反转陷阱)
- [ ] Phase 3 设计细化(`aletheia diagnose` + `fix_class` 打标)— **deferred,移入 ROADMAP「待观察」**(owner 2026-06-28:先看 Phase1+2 真实运行价值再评估)
- [ ] Phase 4 排期(数据态 action 先行)— **deferred,移入 ROADMAP「待观察」**
- [ ] 选项3「非决策链白名单」研究文档 — 研究方向,未排期

> **实施备注(2026-06-28,分支 `worktree-auto-restart-git-hooks`)**:Phase 1+2 已落地。`content.*` 探针对缺表/缺数据优雅降级为 `info`(不崩报告)。在 worktree 空 `data/` 下跑 `doctor --json` 会看到 7 条 `probe error: no such table`——这是空 data 目录,非 bug;主仓已迁移 DB 上返回真实 ok/warn(单测已证)。激活:FF merge master + restart + `aletheia doctor --json` 实测。
