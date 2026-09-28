# 问题盘点与交接材料 (2026-09-24)

> 从 Claude Code 转向 Oh my Pi 的完整状态交接。本文档记录已完成的修复、遗留的结构性问题、以及建议的修复路线图。

---

## 1. 核心问题诊断（来自 R20 竞品分析）

**文档路径**: `docs/research/audits/COMPETITIVE_ANALYSIS_R20_2026-09-24.md`

### 根本性差距

| 维度 | Aletheia | R20 | 差距根因 |
|------|----------|-----|---------|
| 测试/生产行数比 | **1.02** | **2.60** | 规则写在文档里而不是测试里 |
| 治理文档总量 | **143,808 行** | 近乎 0 行 | 同上 |
| 门禁测试 | 零专门的治理门禁 | 48 个 audit 门 + 76 个 AST 对拍门 | R20 把规则编译成会翻红的测试 |
| 交付速度 | 慢（#3134 吸收 113 PR 才敢合） | 快（25 天 v8.3.0 可部署） | 缺验收定义，改动没有运行证据 |

### 为什么会这样

**Aletheia 把工程纪律写成了需要人去遵守的文字；R20 把工程纪律编译成了会翻红的测试。**

举例：
- **Aletheia**: AGENTS.md 写着"一个能力只留一份实现，converge 后 delete 输家"——这需要 AI 自觉遵守、人 review 发现违反
- **R20**: `test_circuit_breaker_twin_parity.py` 门禁——两处实现的决策序列逐项对拍，不等价就翻红

前者的边际成本随代码量增长而上升（每个 PR 都要人肉核对规则），后者的边际成本随代码量增长而下降（写一次门禁，永久拦截）。

---

## 2. 最近完成的修复（2026-09-20 至今）

### 2.1 Issue 簇 #3443–#3447（已全部关闭）

五个紧密相关的缺口，全部修复并带回归测试：

| Issue | 根因 | 修复 commit | 新增测试 |
|-------|------|-------------|----------|
| #3447 | settings 路由未调用 `to_canonical()` | c3d1da230 | +62 行（4 个新测试） |
| #3443 | allowlist 未纳入 `candidate_selection_policy_identity()` | 254daa14e | +53 行（2 个新测试） |
| #3444 | `_listing_view()` profile 解析失败静默回退到 live | 34e4c9dbc | +35 行（1 个新测试） |
| #3445 | order-constraints cache key 不含 profile generation | 2dac6c656 | +102 行（2 个新测试） |
| #3446 | `modify_request()` 丢失 `quantity_precision` | 6c6e33cdd | +104 行（2 个新测试） |

**共性模式**：
- 都是 **fail-closed 语义未被测试保护** 导致的
- 都是**等价变体处理不一致**（符号格式、profile 切换、venue 数量步长）
- 都在修复时补上了回归测试，但没有建通用门禁防止同类问题复发

### 2.2 其他近期修复（177 个 commit）

统计结果：
- **11 个** 生产代码修复 + 配套新测试（100+ 行级别）
- **6 个** 生产代码修复 + 小量新测试（20–50 行）
- **11 个** `fix(tests)` —— 修复已有测试的 drift（时间戳锚点、fixture 过时、断言过时）
- **0 个** 生产代码修复完全无测试

**结论**：方向正确（每个 fix 都带测试），但量上没追平（测试/生产比从 1.01 → 1.02，基本没变）。

---

## 3. 持续踩坑的反模式

### 3.1 时间戳单位混用

**踩过的坑**：
- derivatives 恒为 0 根因：OI delta 跨 venue 单位混用（记忆：`project_derivatives_zero_rootcause.md`）
- 最近 11 个 `fix(tests)` 有大半是修时间戳锚点 drift

**为什么会反复踩**：
- 没有命名即语义约定（`_ms` / `_ts` 后缀）
- 没有单位一致性门禁测试

**R20 的对策**：`test_timestamp_unit_convention.py` —— 扫描所有时间戳字段，例外表必须为空。

### 3.2 "读不到 ≠ 没有" 语义混淆

**踩过的坑**：
- `all_realized_pnl_unavailable`：paper funding settler 永久排除终态行，数据补齐后不回访（记忆：`project_overview_partial_close_rootcause.md`）
- derivatives 恒为 0：OI 读取失败当作"OI 是 0"处理

**为什么会反复踩**：
- 缺失失败语义表：哪些字段允许 None，None 代表什么（缺数据 vs 真的是零）
- 没有门禁测试检查失败路径的文档覆盖

**R20 的对策**：`FAILURE_SEMANTICS.md` + `test_failure_semantics_doc.py` 锚点门。

### 3.3 孪生实现不收敛

**踩过的坑**：
- opportunities 生命周期已成化石（记忆：`project_opportunities_lifecycle_orphaned_2026-09-08.md`）
- #2945 删除 875 个测试函数，passed count 从 42 降到 −362（记忆：`project_pr2945_convergence_2026-09-07.md`）

**为什么会反复踩**：
- AGENTS.md 写着"一个能力一份实现"，但没有机器检查
- 删代码时只看调用点，没读契约文档（记忆：`feedback_2945_deletion_judgement.md`）

**R20 的对策**：`test_circuit_breaker_twin_parity.py` —— 两处实现的决策序列必须逐项相等。

### 3.4 测试空洞通过

**踩过的坑**：
- PR #3442：恢复已停止检查任何东西的 audit、spec 和 fixture（记忆：`commit 32daceebf`）
- 11 个 `fix(tests)` 修 drift（时间戳、fixture、断言过时）

**为什么会反复踩**：
- 测试断言写得太弱（`assert len(rows) > 0` 类似的重言式）
- 没有反重言门禁

**R20 的对策**：`test_no_vacuous_assertions.py` —— AST 扫描所有断言，拒绝永真式。

---

## 4. 尚未建立的门禁（来自 D1 处方）

文档 `COMPETITIVE_ANALYSIS_R20_2026-09-24.md` 开的处方是建 7 类门禁测试，**全部未建**：

| 门禁类型 | 对应规则（已写在 docs 里） | 状态 | 预估工作量 |
|---------|---------------------------|------|-----------|
| `test_circuit_breaker_twin_parity.py` | "一个能力一份实现" | ❌ 未建 | 3–5 天（需先列孪生清单） |
| `test_failure_semantics_doc.py` | 契约文档与代码一致 | ❌ 未建 | 2 天（锚点扫描） |
| `test_timestamp_unit_convention.py` | 时间戳单位一致 | ❌ 未建 | 1 天（命名扫描） |
| `test_directory_docs_current.py` | 模块清单与实际双向对拍 | ❌ 未建 | 1 天 |
| `test_no_vacuous_assertions.py` | 测试不得空洞通过 | ❌ 未建 | 2–3 天（AST 分析） |
| `test_production_data_isolation.py` | 测试不得读写生产 data/ | ⚠️  部分有 | 0.5 天（补强现有检查） |
| AST 对拍门（`tests/extraction/`） | 大重构语义等价 | ❌ 未建 | 按需建（每次抽取一个） |

**总预估**：12–15 天可建完前 6 个常驻门禁。

---

## 5. 交付验收缺失（D2 问题）

### 现状

最近 113 个 PR 的 commit message 都带 `Runtime qualification: Not claimed by this source repair` —— 这是一个合法的空答，允许改动完全没有运行证据。

### 后果

- #3134 吸收 113 PR 才敢合并（记忆：`project_dev_cadence_3134_bus_2026-09-24.md`）
- 修复后不知道 Fedora 实例是否真的变好（例如 Gate B 链条修复前后，只能靠观察窗口等）

### 建议的改进

PR template 增加必答项：

```markdown
## 可用性证据（三选一，不得为空）
- [ ] 真实运行证据：<Fedora 实际周期 / 实际订单 ID / 实际 Outcome>
- [ ] 不适用理由：<为什么这个改动不可能有运行证据>
- [ ] 阻塞项：<具体缺什么，谁能解开>
```

第三项会把"缺什么"暴露成可追踪的一条线（例如"缺 staging 环境"→ 可以建 issue 追踪）。

---

## 6. 当前 master 状态

### 6.1 测试基线

```bash
uv run pytest tests -q -m "fast"
# → 预期通过（fast 标记的测试子集）

uv run pytest tests -q
# → 全量需约 5–10 分钟（macOS 不建议跑全量，Fedora 可以）
```

**已知的测试限制**（来自 CLAUDE.md / AGENTS.md）：
- macOS 是开发机，不跑完整测试（会冲突 data 目录）
- 不能并发跑两个完整 pytest 套件（共享 `ALETHEIA_DATA_DIR`）
- 完整 e2e 测试需要 Fedora（paper runtime）

### 6.2 最近的 commit 链（从新到旧前 10 个）

```
6c6e33cdd fix(execution): enrich modify_request instrument with venue quantity step
2dac6c656 fix(execution): include receiving-profile generation in order-constraints cache key
34e4c9dbc fix(execution): fail closed on order-rule listing-view profile resolution failure
254daa14e fix(universe): include allowlist in candidate-selection policy identity
c3d1da230 fix(universe): canonicalize allowlist/blacklist/fallback_symbols at settings write boundary
240e51c2b fix(tests): normalize age_seconds in test_universe_routes adapter dump comparison
bd00021eb fix(tests): derive _execution_fact timestamps from epoch boundaries
581eeb1af fix(tests): seed paper_execution_mode in acceptance test client fixture
687ebb3d2 fix(tests): update Overview.tsx assertion to match component props and routing
0aa1f9b8a fix(tests): update PriceChart useEffect dependency array assertion
```

### 6.3 未合并的工作

```bash
gh pr list --state open
```

当前无 open PR（#3134 已合并）。

### 6.4 已知的技术债（来自记忆索引）

- **paper_internal 要退役**（记忆：`project_paper_internal_retirement.md`）—— 但它是唯一有 e2e 覆盖的模式，须先建 exchange-mode 测试脊柱（#2993）
- **learning 模块 4 缺口**（记忆：`project_learning_contribution_gaps_2026-07-19.md`）—— G1 时间戳 bug / G2 epoch churn / G3 路径硬编码 / G4 shadow 死锁
- **市场观察器恒空转**（记忆：`project_gaps_found_2026-09-08.md`）—— 已发现但未修复
- **seed guards 永不落盘**（同上）—— 安全路径，不应静默启用

---

## 7. 建议的修复路线图（按 ROI 排序）

### Phase 1: 建门禁测试（最高 ROI，12–15 天）

优先级按"违反后的代价"排：

1. **P0**: `test_circuit_breaker_twin_parity.py`（3–5 天）
   - 先列孪生清单（从 AGENTS.md "双主线清扫" 提到的领域开始）
   - 建门禁：两处实现的决策序列必须逐项相等
   
2. **P0**: `test_failure_semantics_doc.py`（2 天）
   - 建 `docs/design/contracts/FAILURE_SEMANTICS.md` 失败语义表
   - 门禁：表中引用的每个符号/测试必须真实存在且可加载

3. **P1**: `test_timestamp_unit_convention.py`（1 天）
   - 扫描所有时间戳字段，`_ms` / `_ts` 后缀即语义
   - 例外表必须为空

4. **P1**: `test_no_vacuous_assertions.py`（2–3 天）
   - AST 扫描所有 `assert` 语句
   - 拒绝永真式（`assert len(x) >= 0` / `assert True` / `assert obj is not None or obj is None`）

5. **P1**: `test_directory_docs_current.py`（1 天）
   - 模块清单与实际双向对拍
   - 防止文档腐烂

6. **P2**: 补强 `test_production_data_isolation.py`（0.5 天）
   - 已部分有 `ALETHEIA_DATA_DIR` 检查
   - 补全：测试不得读写生产 `~/.aletheia`

**每建一个门，就可以从 `docs/` 删掉对应的文字段落** —— 目标是把 143,808 行治理文档压到 3,000 行以内，同时治理强度上升。

### Phase 2: 交付验收机制（3–5 天）

1. **PR template 改造**（1 天）
   - 增加"可用性证据"必答项（三选一格式）
   - 拒绝 `Runtime qualification: Not claimed` 空答

2. **Staging 环境或 shadow 模式**（2–4 天）
   - 让 PR 作者能拿到运行证据
   - 可以先从 shadow 模式开始（无需真实环境）

### Phase 3: 补历史测试债（按需，每个 1–3 天）

从记忆索引中已知的债开始：
- `test_learning_contamination.py` 覆盖 G1–G4 缺口
- `test_market_observer_liveness.py` 防止恒空转
- `test_paper_internal_retirement.py` 建 exchange-mode 测试脊柱后可退役

---

## 8. 交接清单（给 Oh my Pi）

### 8.1 必读文档

| 文档路径 | 用途 |
|---------|------|
| `AGENTS.md` | 仓库路由器，D0/D1/D2 变更分类，commit/PR 规则 |
| `CLAUDE.md` | 命令速查、架构地图 |
| `docs/design/contracts/` | 合同层定义（优先级高于调用点） |
| `docs/research/audits/COMPETITIVE_ANALYSIS_R20_2026-09-24.md` | 根本性差距诊断 |
| `docs/governance/decisions/DECISIONS.md` | 决策记录 |
| `~/.claude/projects/-Users-yixian-Projects-aletheia/memory/MEMORY.md` | 长期记忆索引（包含历史踩坑、已修复缺口） |

### 8.2 关键约束（容易踩的坑）

- **macOS 是开发/控制机**：不要推断 Fedora runtime 状态，不要启动本地 gateway
- **不能并发跑两个完整测试套件**：共享 data 目录会互相 crash
- **远程 CI 已退役**：验证全本地，合并用 `gh pr merge --squash` 直接合
- **commit message 必须带 Co-Authored-By**: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- **PR body 必须带归属**: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`（如果是 Oh my Pi 接手，改成对应归属）

### 8.3 诊断工具

```bash
# 运行时状态
uv run aletheia control status --json

# 健康检查
uv run aletheia doctor

# 本地验证计划
uv run python scripts/ci_plan.py

# CodeGraph 代码定位（如果 .codegraph/ 存在）
codegraph explore "<symbol or question>"
```

### 8.4 当前环境

- **Python**: uv 管理，`uv sync` 同步依赖
- **Frontend**: `npm run build` 写回 `core/aletheia/dashboard_static/`，刷新浏览器即见效
- **Runtime**: Fedora (u7p) 是 paper runtime，Mac 本机不跑实例
- **Git branch**: `master` 是主分支，已经合并 #3134（113 PR）

### 8.5 未完成的工作（优先级排序）

1. **Phase 1 门禁测试**（12–15 天，最高 ROI）
2. **PR 验收机制**（3–5 天）
3. **learning 模块 4 缺口**（G1–G4，已定位但未修）
4. **市场观察器空转**（已发现）
5. **paper_internal 退役**（需先建测试脊柱）

---

## 9. 最后的建议

### 给 Oh my Pi 的提示

如果你在修某个 bug，请先：
1. **读契约文档**（`docs/design/contracts/`），不要只读调用点（记忆：`feedback_2945_deletion_judgement.md`）
2. **搜 Hindsight 记忆**（工具：`hindsight_search_knowledge_pages`），很多坑已经踩过并记录
3. **查 open PR**（`gh pr list`），避免重复工作（记忆：`feedback_check_open_prs_first.md`）
4. **grep 验证删除**（记忆：`feedback_issue_mass_close_method.md`），"路径消失"不代表功能真的废弃
5. **每个 fix 必须带测试**（最近已做到 0 个无测试修复，保持住）

### 给人类的建议

如果预算允许，Phase 1 的 6 个门禁测试建完后，143,808 行治理文档可以压缩到 3,000 行以内，同时治理强度上升——这是 ROI 最高的一次性投入。

之后的维护成本会显著下降：
- 违反规则的 PR 在 CI 阶段就翻红，不需要人肉 review 12,000 行规则
- 新人上手只需要读 3,000 行精简文档 + 跑一次测试看门禁
- 每次改动都有机器验证的运行证据，#3134 式的"113 PR 才敢合"不会再发生

---

**交接完成。祝 Oh my Pi 修复顺利。**

---

## 附录 A — 2026-09-25 前提核验（Oh my Pi）

> 本节由接手方在开始工作前对本文档的关键前提做代码 + Fedora 只读实测后追加。
> 原正文保留不动；以下三条**已证伪**，后续会话不要按原 §8.5 的第 3、4 项重复劳动。

### A.1 §6.4 / §8.5-3「learning 模块 4 缺口 G1–G4」：**已全部解决，不是待修项**

| 缺口 | 现状 | 证据 |
|---|---|---|
| G1 时间戳 bug | FIXED | `extensions/learning/cohorts.py:493-497` 把不可解析时间拒为 `missing_outcome_time`（不再回退 1970）；`extensions/learning/verdicts.py:706-715` `_parse_time` 归一到 UTC |
| G2 epoch churn | FIXED | `extensions/learning/experiments.py:271-345` `_policy_epoch_definition` 只折叠 path-relevant 字段，显式排除 `cfg.risk`/`cfg.trading`/build fingerprint；`policy_epochs.py:117-148` 仅有效哈希变化才轮转 |
| G3 「硬编码路径」 | FIXED（原文是误读） | 审计指的是 learning-path **id 白名单**而非文件系统路径。现为 `cohorts.py:49-51` 从 `LEARNING_PATHS` registry 派生；`extensions/learning/` 内无绝对路径/`~/.aletheia` 字面量 |
| G4 shadow 死锁 | DELETED + 替代 | `aletheia/learning/lifecycle.py` 整个模块已不存在；由 `extensions/observation_lab/` 的 owner-admission 取代（`canonical.py:3373-3384`、`manifest.py:283-284` 要求 `owner_admission_ref`）。guard 侧 auto-promotion 亦已退役（`config/schema.py:1871-1872` `auto_promote_enabled=False`） |

### A.2 §6.4 / §8.5-4「市场观察器恒空转」：**设计退役，不是待修 bug；正确动作是退役而非恢复**

- 原 `universe → opportunity` 生产者 `core/aletheia/universe/signal_emitter.py` 已按 #2945/#2931 删除。
- `repo.insert_opportunity_once_by_snapshot` / `insert_opportunity_generation` **零生产调用者**，且被 `tests/specs/spec_pipeline_middle_state_integrity.py:983-1020` 以 AST 断言禁止复活。
- live 主链是 `Candidate → candidate_planning_generations → core/aletheia/agent_shell/entry_planner.py::plan_next_candidate → EntryPlan`，**不消费 `opportunities` 表**。
- Fedora 实测：`market_observer` 24h 运行 **165 次，每次约 63ms**（`agent_runs`），工作集结构性为空（#2990）。
- 因此 §7 Phase 3 的 `test_market_observer_liveness.py`（"防止恒空转"）是**错误处方**：该机制正在被 #2933/#2934 退役（标签 `do-not-auto-merge`）。为它恢复 producer 等于复活 #2934 正在杀的第二套 entry 权威，违反 AGENTS.md 的 convergence 规则。

### A.3 §5 / doctor `stale_overdue`「settler 停摆」：**不是停摆；3019 条行永久不可达**

- Fedora 实测：`filter_candidate_outcomes` 有 **3019** 条 `pending` 且 `due_at <= now`，但**可达数为 0**。
- 根因：这 3019 条全部挂在 `universe_discovery_benchmarks` 的**唯一一条**记录上，该记录 `status='invalidated'`（Observation-Lab 切换时由 `core/aletheia/universe/discovery_benchmark.py:225` 以 `superseded_by_observation_window_kernel` 作废）。
- settler 选择器（`repositories.py:10180`、`:10362`）与终态写守卫（`repositories.py:369-385`、`_settle_outcome_with_exact_venue`）**都拒绝非 active benchmark 的行**，所以它们既不会被选中也不会被写入 → 永远 `pending`。
- 而 `doctor._probe_filter_evidence` 的 `overdue`/`stale_overdue` 聚合**没有可达性谓词**，把这些行算成 settler 积压并建议"check its scheduler cadence"——**误诊**。
- 该误诊违反既有契约：`tests/test_settlement_deferral.py:691` 明文定义 "Overdue and stale_overdue describe rows the settler will still get to"。
- 已修（D0）：谓词集中为 `repositories.FILTER_OUTCOME_SETTLER_REACHABLE_SQL`，选择器/计数器/doctor 共用；新增 `unreachable_pending` 计数与真话 fix hint；settler 日志补同类警告；回归测试 `tests/test_doctor.py::test_doctor_separates_unreachable_outcomes_from_settler_backlog`（修前 `stale_overdue=2`+空 hint，修后 `stale_overdue=1, unreachable_pending=1`+warn）。
- **仍待 owner 决策（D1）**：这 3019 条行的终态处置（它们已不可能结算，继续留在 `pending` 只会永久污染计数）。

### A.4 Fedora 部署状态（实测，2026-09-25）

- Fedora `~/aletheia`：branch `master`，worktree clean，HEAD = `47a5edbd1917f21ac581d9a8d6040ad3cdee0ba2`。
- `origin/master` = `6c6e33cdd` → **Fedora 落后 17 个 commit**，#3441–#3447（含 5 个 fail-closed 生产修复与审计/spec 恢复）**均未部署**。
- Gateway 运行中（session `05414936c3d2`，boot_sha `47a5edbd`，schema v87，bitget_demo），`verified_live_gateway=true`。
- 循环本身在产（7d）：`candidate_planning_generations` entry_plan=99 / unavailable=306；`entry_plans` failed=74 / invalidated=12；`orders` filled=14 / cancelled=10 / error=10 / submission_rejected=7；`positions` closed=427 / open=2。
- 结论：**"循环在转，但结果腿对 3019 条历史行永久死亡，且诊断对此说谎"**，比原文"settler 停摆"更精确。

