---
id: v2-v3-convergence-plan
status: accepted
related_contracts: []
supersedes: []
---

# V2/V3 残留与双主线收敛执行计划

## Summary

本文件是一份**执行计划**，不是新的设计提案。它把一次只读审计的结果转成给执行者的确切动作清单：删除仓库中已退役的 V2 代码路径、消除真正的双写双读第二权威（Risk 扁平配置字段）、修正会误导未来开发者的文档状态，并**明确列出必须保留的 V2/V3 命名**，避免误删。

审计结论（先行）：

- 仓库**不存在**「两套同时在跑的竞争算法」。绝大多数 `xxx/v2`、`xxx/v3` 是契约版本串、迁移序号或外部协议事实，**不是**双主线。
- 真正的第二权威只有三处：`OpportunityGenerationV2` 退役 entry 权威、`profit_gradient_evidence/v1` 死验证分支、**Risk 扁平字段 ↔ 嵌套 mode policy 的双写双读**。后者是唯一真正会持续制造 bug 的双主线。
- 文档层有一处 D0 真值漂移：`docs/design/proposals/README.md` 把 4 个 `status: implemented` 的提案仍列在「Current proposals」。

## Current fact

审计时的 worktree：本地 `master` = `6d9180127afedb9ca55f1f085f3db0749ac55069`，与 `origin/master`（`84af70e2637d3c7beeb1db7a91da4b7422073d1d`，PR #3496）已分叉：本地领先 7 个 commit，落后 2 个。**本计划的所有行号以本地 `6d9180127` 为准。执行前必须先 rebase/merge origin/master，然后按第 0 阶段重新核对行号。**

已确认的 live 主链：`Candidate → candidate_planning_generations → agent_shell/entry_planner.py`。`opportunities` 表的 `generation_*` 列与 `OpportunityGenerationV2` 是**已退役的第二条 entry 权威**，不在 live 主链上。

---

## 给执行者的硬规则（每个阶段开始前重读）

1. **一次只做一个 Phase。** 做完一个 Phase，跑它的验收命令，commit，然后才开始下一个。不要把多个 Phase 攒在一个脏 worktree 里。
2. **每个 Phase 的 commit message 必须写清 `SDD Depth: D0/D1/D2` 和 `Contract Delta`。** 本文件每个 Phase 都给了这两个值，照抄。
3. **不要重命名任何 `xxx/v2`、`xxx/v3` 字符串常量。** 它们是持久化在数据库里的契约标识。改了会让历史数据校验失败。本计划里没有任何一条要求你改这类字符串。
4. **不要动 `core/aletheia/storage/migrations.py` 里任何 `_apply_*_vNN` 函数或 `_*_CHECKSUM` 常量。** 迁移是 checksum 冻结的；哪怕加一行日志都会破坏 ledger 校验。
5. **不要删除数据库列或表。** 数据层是 additive-only。本计划只删**代码路径**。
6. **遇到本文件没写到的情况就停下来报告。** 不要自己推断、不要扩大范围、不要「顺手」改别的。
7. **禁改清单见下一节。碰到就停。**

## 禁改清单（NEVER TOUCH）

执行者在任何 Phase 都不得修改以下内容。如果某个 Phase 的动作看起来需要碰到它们，说明理解错了，停下报告。

| 路径 / 内容 | 理由 |
|---|---|
| `core/aletheia/universe/features.py` | PR #3496 刚落地的 acquisition 语义哈希来源 |
| `core/aletheia/universe/candidate_selection_authority.py`、`candidate_selection_shadow.py` | 同上，#3496 改动面 |
| `tests/specs/spec_candidate_selection_utility.py`、`tests/test_candidate_selection_utility.py` | 同上 |
| `docs/design/contracts/candidate_selection_utility_contract.md` | 同上 |
| `docs/design/history/**` | 冻结的设计历史 |
| Candidate 阈值、Risk **评估语义**、Entry、Execution、Alpha 的行为 | 产品语义，本计划只做结构收敛，不改任何判定结果 |
| `KNOWN_BITGET_PROFILES` / `classic_v2` / `uta_v3`（`core/aletheia/config/schema.py:25,317`） | 真实交易所 API 协议版本，不是内部版本号 |
| 一切外部 URL 里的 `/api/v2`、`/api/v3`、`/api/v5` | 外部服务真实端点 |
| observation Main 库 ↔ Lab 库的 gated 收敛桥（`migrations.py:4489,7047,7071`） | #2737 有意设计的、带 gate 的迁移桥，Phase 8 未完成 |
| 全部 13 个 `docs/design/proposals/slim_core_*.md` | #3134 仍 OPEN，DoD 未达成 |
| `docs/design/proposals/observation_experiment_ownership_v2.md`、`usage_v2_workspace.md` 的**语义内容** | 活跃契约；Phase 5 只改 README 里的分类，不改这两个文件的正文 |

---

## Phase 0 — 基线核对（必做，不产生代码改动）

**目的**：本计划的行号取自 `6d9180127`。你开工时的 HEAD 可能已经变了。先证明行号还对得上，再动手。

**动作**

1. 同步：`git fetch origin && git merge origin/master`（或按仓库惯例 rebase）。
2. 逐条运行下列命令，确认输出里包含括号内的符号名。**任何一条不匹配，就停下报告，不要猜测新行号后继续。**

```
sed -n '182p' core/aletheia/storage/models.py            # class OpportunityGenerationV2(BaseModel):
sed -n '903p' core/aletheia/storage/repositories.py      # def canonical_opportunity_generation(
sed -n '917p' core/aletheia/storage/repositories.py      # def insert_opportunity_generation(
sed -n '47p'  core/aletheia/analytics/profit_gradient_evidence.py   # LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION = "profit_gradient_evidence/v1"
sed -n '55p'  core/aletheia/analytics/profit_gradient_evidence.py   # _LEGACY_COMPILER_PRODUCER = {
sed -n '1834p' core/aletheia/analytics/profit_gradient_evidence.py  # LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION,
sed -n '944p' core/aletheia/risk/engine.py               # risk = resolved_policy.legacy_view(cfg.risk)
sed -n '1084p' core/aletheia/config/schema.py            # def __setattr__(self, name, value) -> None:
sed -n '9229p' core/aletheia/storage/migrations.py       # def run_migrations(
```

**验收命令**

```
uv run python scripts/ci/dev_check.py
```

**Contract Delta**：`NONE`。**SDD Depth**：无（不产生改动）。

---

## 基线修复（已完成，先于所有 Phase）

起草本计划时，仓库在 `185934f90`（本分支之前）就有 12 个 e2e 用例是红的。已在两个独立的干净
worktree 上复现证实，与本计划的任何改动无关。两个互不相关的原因，**都是测试缺数据，产品行为无一处需要改**：

**原因一（11 red + 1 error）—— 缺交易所币种清单。** commit `edc93a594`

下单前要查「这个币能不能交易」。沙盒模式下缓存为空时，代码会同步去交易所拉取清单，
而 e2e 的 `_mock_exchange` 护栏（`tests/e2e/conftest.py:97`）正确地拦住了一切真实网络连接。
抛出的 `RuntimeError` 被 `execution/gateway.py:1527` 的宽 `except Exception` 收敛成
`execution_precheck_failed`，真因不可见；`KeyError: 'order_id'` 是同一原因在下一步的表现（订单根本没建成）。

修法：在 `tests/e2e/conftest.py` 加 autouse fixture，给 `bitget_demo` 预置本地清单并在结束时还原。
`load_venue_capabilities` 的 TTL、锁、fail-closed 分支全部照常执行，只是清单变成本地事实。

**原因二（1 red）—— 测试没开启它要验证的那项检查。** commit `edc93a594`

`test_administrative_close_is_raw_only_and_risk_fails_closed_without_fake_loss`
断言 `account_loss_unavailable:administrative_close` 这条 fail-closed 拒绝，却把
`max_daily_loss_pct` 留在 0。按 `engine.py:1241-1250` 的契约，0 = 该项检查停用，
于是账户亏损快照根本不会被取。测试现显式开启它依赖的上限。

**附带清理（2 处过时断言）。** commit `335046594`

- `test_settings_get` 断言 `max_daily_loss_pct == 1.0`，但 0.0 才是当前声明的默认值
  （`schema.py:619`、`:1136`）。它钉的是一个早已改掉的常量，现改为核对接口如实返回已加载配置。
- `test_retired_settings_fields_are_rejected` 额外探测
  `PATCH /api/settings/model-integrations/{provider}`。该接口已整体退役、无任何路由——
  spec `LLMH-S07` 正是断言它不存在。期待 404 实际钉的是路由匹配顺序，一旦同一会话里 SPA
  静态挂载被挂上（见下节）就会变成 405，于是该用例依赖执行顺序。保留有价值的那半（真实的
  model-profiles 写入口必须以 422 拒绝退役字段）。

**结果**：`tests/e2e/` 91 passed，`tests/specs/` 1309 passed，`dev_check` PASS。

---

## 已修复的顺序依赖脆弱性（曾先于本工作存在）

> 本节原先记录为「已知未修，成本是重构 61 个测试文件」。该成本估算是错的，实际修法只动
> 1 个文件、约 20 行，见本节末「实际修法」。历史判断保留在此以便追溯。

`tests/test_model_profiles.py` 里 16 个「已退役路由应返回 404」的用例曾**依赖测试执行顺序**，
只在整套一起跑时变红（`assert 405 == 404`），单文件跑全绿。

**根因（已用探针在真实失败现场取证）**：`core/aletheia/server/app.py:158` 在 lifespan 启动时
把静态前端 `app.mount("/", ...)` 挂在根路径上——它匹配任何未被 API 接住的地址。而
`aletheia.server.app.app` 是模块级全局单例，被 61 个测试文件共享。任何用
`with TestClient(app)` 触发过一次 lifespan 的测试（如 `tests/test_doctor_json.py:91`、
`tests/test_settings_honesty.py:97`、`tests/test_outcome_reporting.py:35`），都会把这个根挂载
**永久留在共享 app 上**，且每次启动累加。探针实测：失败现场共享 app 上有 3 条 `path=""`
的根挂载，全部匹配 `/api/model/openai-oauth/start` 与
`/api/settings/model-integrations/openai_native`，于是本该 404 的退役地址返回 405。

**判定证据**：

| 版本 | 完整跑（无 `-x`）结果 |
|---|---|
| `185934f90`（本工作之前） | 1 failed, 10051 passed |
| 本分支 `557581b57` | 16 failed, 10048 passed |
| 本分支但 revert 掉 `335046594` | **23 failed**, 10041 passed |

回退本工作的改动反而让红变多（16 → 23），证明这些用例的红由**顺序**决定，不由改动内容决定。
`185934f90` 只报 1 个红是因为那次用了 `-x`（遇错即停），在第一个失败处就停了。

**405 的确切来源**：`Mount("/")` 的 `path` 被 `rstrip("/")` 成空串，按前缀匹配、**不看 method**，
对任何路径都返回 `Match.FULL`，于是请求进 `StaticFiles`，而它第一行就是
「非 `GET`/`HEAD` 直接抛 405」（`starlette/staticfiles.py:113`）。所以失败的 16 个用例全是
`POST`/`PATCH`——`GET` 的退役路由拿到的是 404，从来不报错。

**两处先前的误记（已更正）**：`tests/test_settings_honesty.py:97` 并非污染源，它在 `:95` 自建
`FastAPI()` 只挂 settings router，不碰共享 app；先前遗漏了
`tests/test_profitability_execution_mode_scope.py:311,341` 与
`tests/test_positions_route_pagination.py:52`。真实污染源是 4 个文件共 7 处
`with TestClient(app)`：`test_doctor_json.py:91,108,126`、
`test_profitability_execution_mode_scope.py:311,341`、`test_outcome_reporting.py:35`、
`test_positions_route_pagination.py:52`。

**实际修法（D0，`tests/conftest.py` 一处 autouse fixture）**：每个测试前后把共享 app 上
`name == "dashboard"` 且 `path == ""` 的根 Mount 原地摘掉。三个要点：

- 用 `sys.modules.get` 而非 `import`，否则每个纯单元测试都要为这个 autouse fixture 拉起
  FastAPI 与 23 个 route 模块。
- 用 `routes[:] = [...]` 原地改，因为 `app.routes` 返回的就是 `router.routes` 同一个 list
  对象，活的 `Router` 每次请求实时遍历它，重新赋值改不到。
- 零生产代码改动。`tests/` 下没有任何用例依赖这条静态挂载存在：唯一断言根路径返回 SPA 的
  `tests/test_frontdoor.py:314,434` 用的是 `frontdoor.create_app(static_dir=...)` 自建 app。

**不选另外三条路的理由**：把挂载改成幂等只能止住累积（3 条变 1 条），一条根 Mount 就足够吞掉
所有未被 API 认领的地址，405 照旧；让 4 个文件各建 app 最贵，`server/app.py` 没有 `create_app`
工厂、`app` 是模块级单例后跟 40+ 次 `include_router`，且将来任何新写的 `with TestClient(app)`
会把问题原样带回；把断言放宽成 `in (404, 405)` 行数最少，但削弱契约并掩盖共享 app 被永久污染
这件会误伤其它测试的事。

**验收**：最小复现 `uv run pytest tests/test_doctor_json.py tests/test_model_profiles.py -q
-p no:randomly`——修前 16 failed / 89 passed，修后 105 passed（7 秒，不必等全套 6 分钟）。
全套 `tests/`（不含 specs/e2e）由 16 failed, 10048 passed 变为 **10064 passed, 0 failed**；
`tests/specs` 1309 passed、`tests/e2e` 91 passed、`ruff` 全绿，均无回归。

---

## Phase 1 — 删除退役的 `OpportunityGenerationV2` entry 权威

**为什么**：这是一整条已退役的第二 entry 权威，零生产调用者，而且已经被 AST 规则明令禁止复活。留着它，未来任何人读 `storage/models.py` 都会以为 `opportunities` 表还在 entry 主链上。这是本计划里最有价值的一刀。

**分类**：`DEAD`。**SDD Depth**：`D2`（删除退役代码路径）。**Contract Delta**：`NONE`（删除零调用者的退役路径，不改变任何现有行为）。

**证据**

- `core/aletheia/storage/models.py:182` — `class OpportunityGenerationV2(BaseModel)`
- `core/aletheia/storage/repositories.py:903` — `canonical_opportunity_generation(generation: OpportunityGenerationV2)`
- `core/aletheia/storage/repositories.py:917` — `insert_opportunity_generation(opportunity, generation: OpportunityGenerationV2)`
- 生产调用者：**零**。live 主链是 `Candidate → candidate_planning_generations → entry_planner`，不消费 `opportunities` 表的 generation 路径。
- 唯一引用者是测试与一条 AST 禁复活断言：
  - `tests/observer_admission_fixture.py:9,59,91`
  - `tests/specs/spec_pipeline_middle_state_integrity.py:989`（AST-ban 条目）

**确切动作**

1. 删除 `core/aletheia/storage/models.py` 中 `class OpportunityGenerationV2` 整个类定义。
2. 删除 `core/aletheia/storage/repositories.py` 中 `canonical_opportunity_generation` 与 `insert_opportunity_generation` 两个函数整体。
3. `grep -rn "OpportunityGenerationV2\|canonical_opportunity_generation\|insert_opportunity_generation" --include=*.py .` — 把剩下的每一个引用都清掉：
   - 删除 `tests/observer_admission_fixture.py`（该 fixture 只为这条退役路径存在）。
   - 删除依赖该 fixture 的测试。
   - 从 `tests/specs/spec_pipeline_middle_state_integrity.py:989` 的 AST-ban 清单里移除对应条目（符号已不存在，禁令失去对象）。
   - 如果 `models.py` / `repositories.py` 有 `__all__`，删掉对应条目。
4. **不要**删 `OPPORTUNITY_GENERATION_VERSION = "opportunity-generation/v2"`（`repositories.py:900`）除非第 3 步的 grep 证明它也零引用。它被 `repositories.py:894,1108,9358` 引用——**大概率要保留**，先查再说。
5. **不要**删 `opportunities` 表的 `generation_version` / `generation_json` / `generation_hash` 列，也不要删 `opportunity_entry_validity_assessments` 表。它们是已应用迁移写入的历史数据（`migrations.py:3876-3976`），属 additive-only 数据层。

**如果发现任何一个生产调用者**（非 `tests/`、非 `scripts/`）：**停下报告**，不要删。

**验收命令**

```
uv run python scripts/ci/dev_check.py
uv run pytest tests/specs/spec_pipeline_middle_state_integrity.py -q
```

---

## Phase 2 — 删除 `profit_gradient_evidence/v1` 死验证分支

**为什么**：验证器同时接受 v1 与 v2 两种契约版本，但 `build_profit_gradient_evidence`
的 producer 常量硬编码为 v2，消费侧 CLI 命令在调用验证器前也已经把非 v2 拒掉。
没有任何生产路径能产出或消费 v1。这是一条永远走不到的分支，会让下一个读者以为 v1 还受支持。

**分类**：`COMPAT_SHIM`（实际已 `DEAD`）。**SDD Depth**：`D1`（收窄验证器接受范围）。**Contract Delta**：`MODIFIED: verify_profit_gradient_evidence 只接受 profit_gradient_evidence/v2；没有生产路径能产出或消费 v1，无行为变化。`

**证据**

- `core/aletheia/analytics/profit_gradient_evidence.py:47` — `LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION = "profit_gradient_evidence/v1"`
- `core/aletheia/analytics/profit_gradient_evidence.py:55-58` — `_LEGACY_COMPILER_PRODUCER`
- `core/aletheia/analytics/profit_gradient_evidence.py:1833-1843` — 验证器同时接受 v1/v2，并按版本选 producer
- **生产已 gate 掉 v1**：`core/aletheia/cli/inspect_cmd.py:343` 在调用验证器前先 `if payload.get("contract_version") != PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION: raise BadParameter`
- 钉住 v1 的测试有两个，不是一个：
  - `tests/test_profit_gradient_evidence.py:61-77` `test_historical_v1_artifact_keeps_hash_verification_without_new_selector_shape` —— 直接钉 v1 分支，随分支一起删。
  - `tests/test_profit_gradient_evidence.py:860` `test_profit_gradient_evidence_cli_writes_compiler_artifact` —— 它 monkeypatch `build_profit_gradient_evidence` 返回一个**合成的 v1 payload**，因此会走到生成器命令 `cli/inspect_cmd.py:397` 的验证调用（**该命令没有前置版本 gate**，与 :343 的消费者命令不同）。它测的是「CLI 写出文件」，v1 只是其 fixture 的偶然选择。把 fixture 的两处 `contract_version` 与 :930 的断言改成 v2 即可，fixture 其余部分已是完整 v2 形状（含 `reconciliation`）。

**确切动作**

1. 删除 `LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION`（:47）与 `_LEGACY_COMPILER_PRODUCER`（:55-58）。
2. 把验证器（:1833 起）的版本检查从「集合包含 v1 与 v2」改为「必须等于 `PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION`」；删掉随之失效的 `expected_producer` 三元选择，直接用 `_COMPILER_PRODUCER`。
3. `grep -n "LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION\|_LEGACY_COMPILER_PRODUCER" -r .` 清掉所有剩余引用，包括 `profit_gradient_evidence.py` 的 `__all__`（约 :2099）。
4. 删除测试 `test_historical_v1_artifact_keeps_hash_verification_without_new_selector_shape`。这不是在削弱验收——这个测试钉的是一条生产走不到的分支，分支删了测试就没有对象了。
5. **不要**改 `PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION = "profit_gradient_evidence/v2"`（:48）和 `PROFIT_GRADIENT_CONTRACT_VERSION = "profit_gradient/v3"`（`profit_gradient.py:22`）。这两个是当前 live 契约标识。

**验收命令**

```
uv run python scripts/ci/dev_check.py
uv run pytest tests/test_profit_gradient_evidence.py -q
```

---

## Phase 3 — 消除 Risk 扁平字段 ↔ 嵌套 mode policy 的双写双读（唯一真正的双主线）

**为什么**：这是本计划里唯一一处「同一份配置有两个权威、且双向自动同步」的地方，也是最会持续制造 bug 的地方。代码注释自己承认了：`config/schema.py:1060-1066` 写着共享扁平字段「一个扁平名对应一对嵌套值，nested-only 写入让 demo/live 不一致时没有正确的单一扁平答案……**删除这些扁平名才是真正的修复，而且它自己是一次独立的改动**」。

**分类**：`DUAL_MAINLINE`。**SDD Depth**：`D1`（配置权威收敛）。**Contract Delta**：`MODIFIED: RiskConfig 扁平 legacy 字段退出读写路径，嵌套 ModeRiskPolicy 成为唯一权威。风险判定阈值与结果不变。`

**⚠️ 这个 Phase 风险最高，因为它落在 Risk 域边界上。执行规则：**

- **只做结构收敛，不改任何一个阈值、默认值、判定逻辑。** 收敛前后，同一份配置必须产出完全相同的 Risk 判定。
- **本 Phase 必须拆成下面 3 个独立 commit**，每个 commit 单独跑验收。不要一次做完。
- **Phase 3c 需要 Owner 批准才能执行。** 做完 3a、3b 就停下汇报，等批准。

**两侧实现**

- 胜者（保留）：嵌套 `RiskConfig.demo` / `RiskConfig.live: ModeRiskPolicy`（`config/schema.py:961-962`）
- 败者（最终删除）：`RiskConfig` 扁平字段 + 双向同步机制
  - `config/schema.py:1084-1103` — `__setattr__`，扁平写 → 嵌套（docstring: "Keep one-release legacy assignments synchronized with v2 policies"）
  - `config/schema.py:1029-1073` — `_mirror_nested_into_flat`，嵌套写 → 扁平镜像
  - `config/schema.py:868-947` — `_RISK_MODE_PATHS` 映射表
  - `core/aletheia/risk/policy.py:31` — `ResolvedRiskPolicy.legacy_view()`；`:35` — `RiskPolicyView`（扁平名 → 嵌套的读桥）

**`legacy_view` 的 4 个调用点（全部要处理）**

```
core/aletheia/risk/engine.py:944                      risk = resolved_policy.legacy_view(cfg.risk)   # 并在 :946 重绑 cfg.risk
core/aletheia/portfolio/position_manager.py:537       return resolved.legacy_view(cfg.risk)
core/aletheia/agent_shell/operator_recommendations.py:336   risk = resolved.legacy_view(cfg.risk)
core/aletheia/risk/policy.py:31                       定义处
```

**绕过 `legacy_view` 直接读扁平字段的生产读者（raw readers）**

```
core/aletheia/risk/engine.py:2752-2755   check_account_leverage: getattr(cfg.risk, "max_account_leverage_live", 1.5) / "max_account_leverage_demo"
core/aletheia/diagnostics/doctor.py:764-777   max_demo_position_margin_usdt
```

执行者必须自己再跑一次确认，不要只信这两条：

```
grep -rn "cfg\.risk\.max_\|cfg\.risk\.starting_capital_usdt\|cfg\.risk\.max_daily_loss_pct\|getattr(cfg\.risk," --include=*.py core/ extensions/
```

**Phase 3a — 把 raw flat readers 改成读嵌套 ✅ 已完成（commit `09fbb6c9f`）**

两处已改为经 `resolve_risk_policy(...)` 读嵌套 mode policy：

1. `risk/engine.py` `check_account_leverage` —— 改前复现：嵌套上限 7.0，仪表盘报 10.0。改后两者一致。
2. `diagnostics/doctor.py` demo 保证金上限告警 —— 改前复现：实际执行上限 12，doctor 读到 100000（因此上限被调低后告警永不触发）。

额外修正（同一 commit）：doctor 原先在「该模式解析不出风控策略」时静默跳过检查。诊断工具不能把「无法验证」显示成「健康」，现改为报 blocker。

回归测试：`tests/test_doctor.py` 94 passed；`-k "leverage or account_leverage"` 82 passed；
`-k "risk or doctor or diagnostics or leverage or settings"` 1184 passed。
新增 `test_demo_margin_gate_reads_the_cap_admission_enforces`，已验证「回退修复则红、恢复修复则绿」。

**Phase 3b — 收敛写路径为单一入口（降级为可选，起草时高估了收益）**

起草时把「双写」当成核心风险。读完 `schema.py:1034-1072` 的完整注释后，这个判断要修正：

`__setattr__`（扁平写 → 嵌套）和 `_mirror_nested_into_flat`（嵌套写 → 扁平）**不是两个竞争的写入者**，
而是同一次写入的两个同步方向。后者是补丁，因为只有前者时，copy / replace / reset-defaults
这三条只写嵌套的路线会让扁平名永久停在旧默认值上。

注释里那句 *"Enforcement was never wrong"* 是关键：准入树入口就 `cfg.risk = legacy_view(...)`
重绑了，门内所有检查读的都是嵌套。**危险从来在读侧，不在写侧**——而读侧已由 3a 修完。

因此 3b 的实际收益只是「让扁平字段不再是写入口」这一条结构整洁性，不修任何已知症状。
若要做，动作如下；不做也不阻塞任何事。

1. `core/aletheia/server/routes/settings.py` 的 `update_risk_config`（约 :1884-1923）当前对扁平 key 做 `setattr(candidate, field, value)`，靠 `__setattr__` 把值推进嵌套。改为**直接写嵌套 mode policy**。
2. 保留 `settings.py` 的 `_RISK_MINIMUMS` / `_RISK_BOUNDS` 校验语义不变——**边界校验是信任边界，不许简化掉**。
3. 做完后 `_mirror_nested_into_flat`（`schema.py:1034`）仍然存在（`GET /settings` 还在 dump 扁平视图），但扁平侧成为纯只读镜像。

验收：

```
uv run python scripts/ci/dev_check.py
uv run pytest tests/ -q -k "settings or risk_config or risk_policy"
```

**Phase 3c — 删除扁平字段本身（不建议现在做；需 Owner 批准）**

3a 已消灭全部已知症状。3c 剩下的是纯结构收敛，代价却明显更高：

- `GET /settings` 把整个扁平 dump 喂给一个 33 字段的 Risk-Limits 编辑器（`schema.py:1043-1045`）。删扁平字段等于重做那个接口的数据契约。
- 约 30 个测试写扁平侧，全部要迁移。注释也点出这批测试正是分歧能长期存活的原因：*"31 green account-leverage specs — every one of them writes the FLAT key"*。

建议：记为独立 Issue，与前端 Risk-Limits 编辑器的契约变更**一起**做，不要单独拆出来改。

若 Owner 决定执行，动作是：删除 `RiskConfig` 扁平字段声明、`__setattr__` 同步钩子、`_mirror_nested_into_flat`、`_RISK_MODE_PATHS`、`legacy_view` / `RiskPolicyView`，并把 4 个 `legacy_view` 调用点改为直接用 `ResolvedRiskPolicy`，同时迁移所有写扁平侧的测试。这是 `D1` 偏 `D2`，必须独立 PR。

---

## Phase 4 —（已撤回）删除 `run_migrations` 兼容别名

**状态：撤回，不执行。** 本 Phase 的成本估计在计划起草时是错的。

起草时依据的是「约 20+ 处调用者」。实际 `grep -rn "run_migrations" --include=*.py .`
返回 **200+ 个调用点，分布在 180+ 个测试文件**（几乎每个 test 模块的
`_isolate`/`setup` fixture 都用它）。为了消掉一个函数名而产生一个触及 180+ 文件的
diff，不可评审，且回归风险远超收益。

更重要的是，这个别名**已经有护栏防止它污染生产**：
`tests/test_migration_fast_path.py:1707`
`test_production_code_has_no_legacy_implicit_run_migrations_callers` 用 AST 断言
`core/aletheia` 下没有任何生产代码 import 或调用 `run_migrations`。也就是说它已经
被约束成一个纯测试 fixture 入口，不是一个会渗进生产的第二权威。

**结论**：这不是双主线，是一个被测试守住的测试便利函数。保留。
如果将来要动，正确做法是在 `conftest.py` 层统一收口，而不是逐个改 180 个文件。

---

## Phase 5 — 修正 proposals README 的真值漂移（D0）

**为什么**：`docs/design/proposals/README.md` 的「Current proposals」段落里列着 4 个 frontmatter 已是 `status: implemented` 的提案。未来任何 Agent 读这个索引，都会以为这些还是待设计的工作，从而重复实现已经落地的东西。这是纯文档 D0 修复，但对「防止屎山化」的杠杆很高。

**分类**：文档真值漂移。**SDD Depth**：`D0`（恢复既有真值一致性）。**Contract Delta**：`NONE`。

**确切动作**

1. 在 `docs/design/proposals/README.md` 中，把下列 4 个文件从「Current proposals」段移到「Implemented proposals」段（它们的 frontmatter 已是 `status: implemented`）：
   - `usage_v2_workspace.md`
   - `gateway_service_lifecycle.md`
   - `ohlcv_provider_observation_result.md`
   - `serenity_research_attempt_truth.md`
2. 把 `tokenomics_harness_phase_plan_2026-07-30.md`（frontmatter `status: superseded`，`superseded_by: tokenomics_harness.md`）补录到「Superseded」段——它当前在 README 里是孤儿，完全没被列出。
3. **只改 README 的分类归属。不要改这些 proposal 文件本身的正文，不要移动文件，不要碰 `docs/design/history/`。**
4. **不要**改 README 对 `observation_experiment_ownership_v2.md` 的描述。它的措辞略超前（Phase 8 未完成），但改它涉及语义判断，留给 Owner。

**验收命令**

```
uv run python scripts/ci/check_docs.py
```

---

## 明确排除清单：这些 V2/V3 不要动

执行者在扫 `grep -rn "v2\|v3"` 的时候一定会撞到下面这些。它们**全部保留**。逐条写出来就是为了让你不误删。

### 外部协议事实

| 位置 | 内容 |
|---|---|
| `core/aletheia/config/schema.py:25,317` | `KNOWN_BITGET_PROFILES = {"classic_v2", "uta_v3"}`、`bitget_profile: str = "classic_v2"` — Bitget 真实 API 版本 |
| `core/aletheia/agent_runtime/startup_behavior_gate.py:399-415` | 同上的启动 gate |
| `core/aletheia/execution/order_constraints.py:135-138` | profile 切换使缓存失效（#3445） |
| `core/aletheia/server/routes/settings.py:578-579` | profile 取值校验 |
| `core/aletheia/agent_runtime/connectivity.py:32-37`、`diagnostics/doctor.py:2991`、`universe/fast_stream.py:459-460` | 外部端点 `/api/v2`、`/api/v3/ping`、`/api/v5` 等 |

### 契约版本串 / 语义标识（改了会让历史数据校验失败）

`profit_gradient/v3`（`analytics/profit_gradient.py:22`）、`profit_gradient_evidence/v2`（`profit_gradient_evidence.py:48`）、`exit-paired-evidence/v2`（`alpha_attribution.py:172`）、`exit-trailing-qualification/v2`（`exit_trailing_qualification.py:385`）、`paper-dynamic-profit-protection/v2`（`exit_trailing_qualification.py:17`）、`momentum-geometry-measurement/v2` + `TREATMENT_VERSION="v2"`（`momentum_geometry_replay.py:14,18`）、`trading_mandate_compliance/v2`（`trading_mandate_compliance.py:25`）、`sizing-counterfactual/v2`（`sizing_counterfactual.py:11`）、`skip-halt-v2`（`skip_diagnosis.py:30`）、`oi-v2`（`outcome_integrity.py:20`）、`outcome-integrity-v2-2026-09-23`（`learning_window.py:15`）、`catalog-model-family-v2`（`economics/pricing_sync.py:27`）、`universe-step-v3`（`universe/scanners.py:22`）、`candidate-selection-utility/v2`、`universe-economic-utility/v2`（`universe/economic_utility.py:360`）、`universe-opportunity-evidence/v2`、`opportunity-generation/v2`（`storage/repositories.py:900`）、`market-structure/v2`（`data/market_structure.py:19`）。

### 单一实现、仅命名带版本后缀（NAMING_ONLY，不是双主线）

- `EconomicUtilityEstimateV2`（`universe/economic_utility.py:359`）— 没有 V1 实现存在，改名收益为零、风险非零。
- `OpportunityEvidenceBundleV1`、`TailEvidenceV1`（`economic_utility.py:351`）、`CandidateSelectionUtilityV1`、`HardGateDecisionV1` — 同理。
- `CORE_ALGORITHM_VERSION`（`data/market_structure.py:18`）vs `COMPAT_ALGORITHM_VERSION`（`:19`）— **看起来像双算法，其实不是**。两个标签共用同一个内核 `derive_market_structure`（`data/market_structure.py:326`）；`universe/market_structure.py:70` 调的也是它。兼容 API `evaluate_market_structure` 有 live 生产消费者（`universe/momentum_trigger.py:321`）。保留。

### 数据层 additive-only（只删代码，永不删数据）

`opportunities.generation_*` 列、`opportunity_entry_validity_assessments` 表、legacy unkeyed shadow 回填函数（`repositories.py:12758,12781`）、`legacy_unknown` OHLCV 历史行、全部 88 个 `_apply_*_vNN` 迁移及其 checksum。

### 有意的、带 gate 的收敛桥（禁区）

observation Main 库 ↔ Lab 库（`migrations.py:4489` `retire_legacy_experiment_tables`、`:7047` `_apply_observation_ownership_v2_v61`、`:7071` `_apply_observation_authority_cutover_proof_v62`）。这是 #2737 的设计产物，受 `require_lab_authority` 和 `retire_legacy_experiment_schema` flag 双重 gate，Phase 8 未完成。**不要碰。**

### 13 个 `slim_core_*.md` proposal

全部指向仍 OPEN 的 #3134，DoD 未达成，是活跃程序的合法跟踪文档。不要移、不要改 status。

---

## 验证契约

每个 Phase 独立验收，顺序不可跳：

| Phase | 验收命令 | 通过标准 | 实际结果 |
|---|---|---|---|
| 0 | 9 条 `sed` 行号核对 | 全部匹配 | ✅ 全部匹配（`sed -n '1834p'` 的目标串实际在 1835，off-by-one，属计划文本笔误而非仓库漂移） |
| 基线修复 | `uv run pytest tests/e2e/ -q` | exit 0 | ✅ 91 passed（修前 12 red，见下「基线修复」节）；commit `edc93a594`、`335046594` |
| 1 | `uv run pytest tests/specs/spec_pipeline_middle_state_integrity.py tests/test_entry_logic.py -q` | exit 0 | ✅ 19 passed；另 `-k "opportunity or admission or entry_plan"` 467 passed |
| 2 | `uv run pytest tests/ -q -k "profit_gradient"` | exit 0 | ✅ 216 passed |
| 3a | `uv run pytest tests/ -q -k "risk or doctor or diagnostics or leverage or settings"` | exit 0 | ✅ 1184 passed；`dev_check` PASS；commit `09fbb6c9f` |
| 3b | `uv run pytest tests/ -q -k "settings or risk_config or risk_policy"` | exit 0 | 未执行 —— 已降级为可选（见 Phase 3b） |
| 3c | 需 Owner 批准后单独定义 | — | 未执行 —— 不建议现在做（见 Phase 3c） |
| 4 | — | — | 已撤回，见 Phase 4 |
| 5 | `uv run python scripts/ci/check_docs.py` | exit 0 | ✅ exit 0 |

全部完成后跑一次 `uv run python scripts/ci/ci_preflight.py`（或仓库当前的 PR 级 gate）再开 PR。

**不允许**为了让验收变绿而放宽断言、跳过测试、加 `xfail`、或改 `dev_check.py` / `check_docs.py` 本身。

## 停止条件

出现以下任一情况，**立即停下并报告**，不要自行决定：

1. Phase 0 的行号核对有任何一条不匹配。
2. 发现 Phase 1 / Phase 2 的「零生产调用者」判断不成立（找到了非测试、非脚本的调用者）。
3. Phase 3 任一步会改变 Risk 判定结果，而不只是改变读写路径。
4. Phase 3a 已完成；3b 已降级为可选、3c 不建议现在做，两者都需 Owner 表态，在此处硬停。
5. ~~Phase 4 中 `run_migrations` 与 `apply_migrations` 的签名转发关系不是一目了然。~~（Phase 4 已撤回）
6. 任何动作需要修改禁改清单里的内容。
7. 任何动作需要删除数据库列 / 表，或修改迁移 checksum。
8. 验收命令红了，而修复它需要超出本 Phase 范围的改动。

## Rollout and rollback

每个 Phase 一个 commit，各自可独立 revert。基线修复、Phase 1、2、3a、5 之间无依赖。

PR 切分建议：基线修复（`edc93a594`、`335046594`）+ Phase 1、2（退役代码删除）+ Phase 5（文档真值）一个 PR；Phase 3a（`09fbb6c9f`，Risk 监控读取修正）单独一个 PR，因为它是唯一改变了对外可见数值的改动。**Phase 3c 若执行必须独立 PR。**

## Resolution

**已完成**：基线修复（12 个历史 red → `tests/e2e/` 91 passed）、Phase 1、Phase 2、Phase 3a、Phase 5。
Phase 4 已撤回。

**剩余工作需 Owner 决策，不需要立即执行**：Phase 3b 已降级为可选（读侧风险已由 3a 消除，
它只剩结构整洁性收益）；Phase 3c 建议与前端 Risk-Limits 编辑器的契约变更合并为独立 Issue。

执行进度在对应 GitHub Issue / PR 中跟踪，不在本文件累积状态。
