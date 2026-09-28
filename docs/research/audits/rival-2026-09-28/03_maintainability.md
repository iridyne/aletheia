# 03 · 可维护度

> 口径：测试规模与分层、**门禁型测试到底断言什么**、lint/type-check 是否真跑、CI 默认门禁、依赖与锁、迁移策略、配置管理、技术债标记。
> 核心问题不是"有多少测试"，而是"**合并一个 PR 时，什么在真的阻断**"。

## 横向总表：测试规模 vs 真实门禁

| 仓库 | 测试文件 | 测试 LOC | 测试/生产比 | **默认 PR 门禁实际跑什么** |
|---|---|---|---|---|
| **Aletheia** | 885 | 265,913 | **0.99** | `dev_check`（变更行 ruff + basedpyright + 文档治理 + test hygiene）；CI 分批全量 pytest + `ruff check core/aletheia/ tests/` |
| **R20 / AstraQuant** | 508（全仓 `test_*.py`） | 138,808（09-24 口径） | 2.60（09-24 口径） | **无 `.github/`**（本次复核成立）；无 CI，门禁靠本地 `pytest tests/`（10,525 通过 / 1 跳过 / 371.93s） |
| **OpenAlice** | 831 | 161,060 | 0.38 | **只 2 项**：`test:contract:workflow` + `tsc --noEmit` |
| **Vibe-Trading** | 708（py）+68（ts） | 189,656 | 0.76 | 全量 pytest（`e2e_backtest/` 永久 ignore）+ 6 道 grep/AST 门禁 + tsc + vitest；**ruff/mypy 零执行** |
| **nofx** | 75 | 15,941 | **0.25** | **只 `go vet` + `go build`**；测试 job 全部 `continue-on-error: true` |
| **tickflow** | 270（py）+15（ts） | 52,518 | 0.62 | 全量 `uv run pytest tests -q` + 前端 `pnpm build`（含 tsc）；**前端 lint 未接 CI** |

**最重要的横向发现**：测试/生产比与门禁强度**完全脱钩**。OpenAlice 有 16 万行测试但 PR 只跑 2 项；nofx 的测试 job 明确标注"Don't block PRs"。R20 的比值最高（2.60）且门禁最强，**但它根本没有 CI** —— 门禁只在维护者本地跑，对外部贡献者零约束（它也没有外部贡献者）。

## 门禁型测试对照：谁的"门"是真的

这是本域的核心。判定标准：**断言的是运行时状态/结构化数据，还是注释文本？违反时是否真的翻红？**

### Aletheia（12 个 `tests/audit/` + 108 个 `tests/specs/`）

09-24 的 R20 报告说"Aletheia 分散，无独立 audit 目录"—— **这条已过时**。当前实测：

| 门禁 | 断言什么 | 判定 |
|---|---|---|
| `tests/audit/test_no_silent_except_pass.py:77` | AST walk `core/aletheia` + `extensions` 的每个 `except ...: pass` 站点，**不在 baseline 里的一律翻红** | **真门禁** |
| 同上 `:109` `test_baseline_count_only_decreases` | baseline 条目数**只减不增** | **真门禁**（棘轮） |
| `tests/audit/test_alpha_cohort_reachability.py:338` | 每个 cohort anchor 必须有**从生产入口可达**的 writer | **真门禁** |
| 同上 `:367`、`:385` | **两个负向自检**：只被测试调用的 writer 必须被报告为 `structurally unmeasurable`；未接线的内部 wrapper 不得满足可达性 | **真门禁 + 自证有效** |
| `tests/audit/test_scheduler_no_order_submission.py` | scheduler 不得提交订单（权限边界） | 真门禁 |
| `tests/audit/test_reconciliation_no_gateway_imports.py` | reconciliation 不得 import gateway（依赖方向） | 真门禁 |
| `tests/audit/test_doctor_no_execution_writes.py` | doctor 命令不得写执行态 | 真门禁 |

`silent_except_pass_baseline.json` 的记录：**2026-09-27 清理 164 → 42 处**，122 处改为 warning/debug 日志。文件注释自称"**这是耻辱榜，不是许可证**"。

**这直接回应了 09-24 报告 §G1（"治理写成文档而非编译成门禁"）—— 该建议已被执行，且 `test_no_silent_except_pass.py:32-35` 显式 Refs 那份报告。**

`test_alpha_cohort_reachability.py` 的负向自检是本次六仓对比中**唯一**做到"门禁自证有效"的样本：它不只检查生产代码，还验证"如果有人写一个只被测试调用的 writer，门会不会报出来"。这比 R20 的 AST 对拍门更进一步（后者证明的是语义等价，不证明自己没坏）。

### Vibe-Trading（两类，都是真门禁）

**① `tools/ci_grep_gates.sh` 六道仓库级门禁**：

| 门 | 断言 | 行号 |
|---|---|---|
| (a) | 无绕过 `safe_load` 的 `yaml.load(`（RCE） | `:44-56` |
| (b) | 全仓无字面量 "WorldQuant"（商标） | `:58-75` |
| (c) | wiki/alpha-library 内无逐股代码数据（Vendor ToS） | `:77-103` |
| (d) | 无 `datetime.utcnow(` / 裸 `datetime.now()` | `:105-129` |
| (e) | **`agent/src/config/` 之外禁止任何 `os.getenv`/`os.environ.get`**（AST 级，`tools/ci_env_var_gate.py`） | `:131-144` |
| (f) | 测试内无进程级 `os` patch | `:146-163` |

门 (f) 的注释写明具体事故：**#1123，一个测试造成约 3000 个级联错误**。

**② `agent/tests/test_readme_counts.py`（15 个契约测试）** —— 断言 **README 里的数字 == 运行时注册表**：

- `:247-256` 列出的工具名 == `asyncio.run(mcp.list_tools())` 结果，顺序一致无重复
- `:297-343` 源码树注释 == **无凭证全新安装**的注册表大小（关闭 4 个凭证门后测量）
- `:471-490` `EXPECTED_BROKERS = 18` 常量 == profile registry 实际 connector 数
- `:545-552` 各语言 README 互不漂移

该文件自陈为什么必要（`:68-75`）：

> "the count tests only prove the five READMEs agree with each other, **which they did while all five were uniformly wrong** — eToro shipped as the 13th connector and the pin stayed at 12."

**判定：真门禁。** 断言对象是运行时状态，不是文本。而且维护者记录了"全员一致地错"这一失效模式。

### R20 / AstraQuant（本次复核：audit 门禁**净增强**，被削弱的是 extraction 历史锁）

09-24 报告把 R20 的 `tests/audit/`（48 文件）+ `tests/extraction/`（76 个 AST 对拍门）列为最高优先级提取项。本次同口径实测：

| 目录 | @34a0d29（旧报告时点） | @HEAD | Δ |
|---|---:|---:|---:|
| `tests/audit/` 文件数 | 48 | **54** | **+6** |
| `tests/audit/` 用例数 | 544 | **575** | **+31** |
| `tests/extraction/` 文件数 | 77 | 78 | +1 |
| `tests/extraction/` 用例数 | 1,483 | **1,423** | **−60** |

口径：`grep -rhc 'def test_' <dir>/*.py` 求和。`fa65097e` body 自述"1497 → 1423"，与实测 @HEAD 的 1423 吻合。

**逐门验真**（判定标准：读被保护对象的代码/AST/加载真实对象 = 真门禁；只在字符串里 `in` 匹配 = 纯文本）：

- `tests/audit/test_failure_semantics_doc.py` —— **真门禁，且是最强的一类**。`:60-63` `importlib.import_module(mod_name)`；`:66-68` `issubclass(v, _ut.TestCase) and callable(getattr(v, case, None))`；`:71-73` 断言锚点处是可加载 TestCase 类。作者自述（`:116`）："（文本匹配的天然弱点），**但骗不过 import + 属性检查**"。09-24 的这条赞美**成立**。
- `tests/audit/test_circuit_breaker_twin_parity.py` —— **真门禁，且比 09-24 描述的更强**。不是"跑两份实现比输出"，而是 `:73-75` `ast.parse` 两份源码 → `:86-99` 遍历 `ast.If`/`ast.ExceptHandler`/`ast.Return` 生成 `"IF <条件> -> <返回种类>"` 序列 → `:110-111` 逐项比对。**静态提取分支结构，"加固只改一边"立刻在序列上产生差异。**
- `tests/audit/test_no_runtime_artifacts_tracked.py`（`e506da2e` 新增，79 行）—— 真门禁。**"直接问 git『心跳路径是否被忽略』，而不是正则匹配 `.gitignore` 文本"**。

新增 6 个文件：`_repo_scan.py`（基础设施）、`test_brand_strings_are_consistent.py`、`test_no_runtime_artifacts_tracked.py`、`test_runtime_config_has_no_legacy_namespace.py`、`test_sandbox_is_fixture_complete.py`、`test_standalone_modules_stay_green.py`。

**被退役的 74 个用例是什么**（`fa65097e`：39 files changed, 20 insertions, **945 deletions**）：全部是"把当前代码逐字锁在特定历史提交上"那一类（`PRE = "9dccec0"` + `git show PRE:file` + AST 全等）。家族分布：`test_judgment_actually_notices_a_change` 30 例、`test_segment(s)_are_ast_identical_to_baseline` 20 例、`test_moved_bodies_match_pre_extraction` 13 例、其余 11 例；4 处改写（去掉"与抽取前基线逐字相同"，保留"当前代码不变量"）。

**退役有负向验证**：故意拆掉 `scripts/ai_factor_trader.py` 里的 `_order_submit_protected(...)` 调用，报红的是**两个行为测试**，不是保留的 shell 门禁。作者结论：**"退役历史锁没有掏空真实覆盖"**。

**残余能力实测**：42/78 文件仍调 `git show`、28 个仍声明 `PRE =`、67/78 仍 import `ast`。即历史锁机制**大面积拆除但未归零**。

**这一节最有价值的一条方法论**（`fa65097e` body 自述）：

> "上一轮我把 D 说成『519 个用例（5%）』。那个数是**错的**：它统计的是『文件里碰过 `git show` 的文件中的全部用例』，而那些文件里绝大多数是行为测试与不变量。逐方法判定后，真正的历史锁是 **74 个**（0.8%）。中间我报过的 286 也是错的。"

**以及最关键的一条门禁哲学**（作者自述保留下来的 shell 不变量**偏弱**）：

> 保留的判据是子串匹配 `"_order_submit_protected" in unparse(n)` —— 改成 `_order_submit_protected_BROKEN` **仍含该子串 ⇒ 抓不到**。作者"记为待办，不再这轮动"。

**对 Aletheia 的直接价值**：**门禁的强度不由"存在"决定，由"负向验证能红"决定。** 一个用子串匹配的不变量门，看着像门禁，实测抓不到。这与"文字规则不拦截"同构，只是升级成了"门禁不拦截"。Aletheia 的 `test_alpha_cohort_reachability.py:367,385` 的两个负向自检正是这个问题的正确解法。

### tickflow（门禁直接复现历史事故）

- `backend/tests/test_write_lock_scope.py:52-80` `test_heavy_merge_runs_outside_write_lock`：用 `monkeypatch` 让 Polars concat 人工耗时 0.4s，验证并发 upsert 期间写锁**可被其他线程获取**。对应 **2026-09-07 线上全站冻结事故**。
- `backend/tests/test_polars_guard.py:23-49`：断言 `_TOTAL_GATE`/`_BACKGROUND_LANE` 信号量确实限制并发 collect，且 interactive 车道不被 background 饿死。对应 Polars 上游死锁 issue #24448/#25754。
- `backend/tests/test_minute_timezone_contract.py:123` `test_guard_unrecognized_convention_fails_closed`：无法识别的时区约定**必须 fail-closed 抛错**。

### OpenAlice（真门禁，但断言的是命令面）

- `scripts/development-test-contract.spec.ts:29-43`：对根 `package.json` 的 scripts 字段做**精确字符串相等**比较（8 个 owner 命令循环断言）
- `:62-63` `expect(scripts['test:system:package-manager']).toBeUndefined()` —— **禁止别名残留**
- `:95` 断言 `vitestConfig.test.forceRerunTriggers` 包含 `collectionWideTestInputs`，且 **import 真实 config 对象**（`:6`）
- `packages/guardian-runtime/src/trading-mode.spec.ts:26-66` 是真·行为门禁：env 锁 > 持久化状态 > 账号推断的优先级逐条断言，fail-safe 默认（无 UTA 配置 → `lite` 最低权限）

**诚实判读**：`development-test-contract.spec.ts` 证明的只是"命令面没漂移"，不证明任何测试真的通过。但它确实 import 真实 config 并检查真实 `package.json` —— **不是幻象测试**。

### nofx（无门禁型测试）

75 个 `_test.go` 全部是**行为断言测试**（如 `trader/auto_trader_signal_policy_test.go` 223 行用 mock trader 验证过滤逻辑），断言的是函数返回值是否符合业务规则，**不是"代码结构是否合规"**。无 depguard/import-cycle 类断言，无独立 audit 目录。

## lint / type-check：配置存在 ≠ CI 执行

| 仓库 | lint | 静态类型 | CI 是否真跑 |
|---|---|---|---|
| **Aletheia** | ruff（B/ASYNC/RUF，E501 等已 ignore） | **basedpyright** basic/error 级 | ✅ 两者都在 `dev_check`（变更行过滤）+ CI `ruff check core/aletheia/ tests/` |
| **Vibe-Trading** | ruff 配置在 `pyproject.toml:306-308`（仅 E/F/W） | **无 mypy 配置** | ❌ **`.github/workflows/` 内 ruff 零命中**；无 `.pre-commit-config.yaml`；coverage `fail_under = 0` |
| **OpenAlice** | 未见独立 lint 命令；刻意剔除传递依赖里的 eslint | tsc strict | ✅ `npx tsc --noEmit`（`ci.yml:54-56`，5 分钟超时） |
| **nofx** | **无 `.golangci.yml`**；`Makefile:89` 有 `lint` target 但**未接入任何 workflow** | — | ✅ `go vet ./...` **真阻断**（`pr-checks.yml:210-211` 无 continue-on-error）；对比 `go fmt` 步骤故意 `continue-on-error: true`（`:196`） |
| **tickflow** | ruff（`select = ["E","F","I","N","UP","B","SIM","RUF"]`，比 Vibe 宽得多） | 无 mypy；依赖 pydantic 运行时校验 | ✅ backend 全量 pytest；frontend `pnpm build` 含 tsc；**前端 lint 未接 CI** |

**Aletheia 的 `dev_check` 值得记录其设计**（`scripts/dev_check.py:7-18`）：ruff 与 basedpyright **只报告变更行**，而 `check_docs` 文档治理门**不做变更行过滤**（理由写在 `:216-217`：已对当前树 baseline 化）。并明确说明为何不跑 `ruff format --check`：**"1152 files would be reformatted today"**。

这是一个**成本感知的门禁设计**：把"新引入的违规"与"历史存量"分开，前者硬阻断、后者棘轮式下降。与 `test_no_silent_except_pass.py` 的 baseline-only-decreases 是同一思想。

## CI 默认门禁的四种形态

### ① 窄而诚实（OpenAlice）

`ci.yml` 的 5 个 job：

| job | if 条件 | 跑什么 |
|---|---|---|
| `source-contracts` | **PR 时跑** | `pnpm test:contract:workflow` + `tsc --noEmit` |
| `workspace-build` | `workflow_dispatch` | `pnpm build` |
| `hermetic-tests` | `workflow_dispatch` | `pnpm test` |
| `cross-platform-test` | `workflow_dispatch` | macOS-14 build + test |
| `dev-smoke` | `workflow_dispatch` 或 `beta_release_prep != 'true'` | Windows guardian + dev-stack smoke |

**831 个测试文件默认不在 PR 门禁内。** 但 `ci.yml:17-21` 主动写明这是"bounded automatic gate"，且 `:39` 从 **base revision** 加载分类器（`git show "${BASE_SHA}:scripts/classify-beta-release-prep.mjs"`）—— **候选分支无法削弱自己的 lane**。

`dev-smoke` 只在 Windows 跑，理由写在 `:140-141`：**"Windows remains the one automatic native-host smoke because it is not covered by the maintainer's local macOS/Linux development loop"** —— 只自动化本地覆盖不到的盲区。

### ② 宽但不阻断（nofx）

`.github/workflows/test.yml:17,39` 的 backend/frontend 测试 job 都标 `continue-on-error: true`，注释 "Don't block PRs"。**测试挂了也能合并。**

### ③ 宽且阻断但缺类型检查（Vibe-Trading）

`test.yml` 5 个 job：hash-lock（3 平台 `--require-hashes`）、test（py 3.11+3.14 矩阵 → 6 道 grep 门禁 → compileall → 全量 pytest）、frontend（build + vitest）、windows-background（2 平台）、desktop-electron-windows（smoke:lifecycle）。

**质量极高的证据**是注释里的实测记录（见 02 域）。**两个实质缺口**：`agent/tests/e2e_backtest/` 与 `test_e2e_harness_v2.py` **永久 `--ignore`**（`:129`）；coverage `fail_under = 0`。

### ④ 反馈循环慢（Aletheia）

`.github/workflows/ci.yml` 只在 `workflow_dispatch` 触发 full-ci-verdict，`runs-on: [pr-ephemeral]`，`uv run ruff check` + 分批 `pytest --timeout=120`。

**09-24 报告实测的硬事实本次仍需正视**：`tier_unit`（最快层，纯单元）8,495 个用例 **300s 超时未跑完**。这是乘性成本——每轮 AI 迭代都要等。六家中 R20 的全量 7,488 用例 74.6s（但含环境缺依赖的快速失败，偏乐观）。

## 测试分层：OpenAlice 的五层 lane 是本次最精细的样本

OpenAlice 把测试按**副作用等级**而非目录分片，成员定义集中在 `scripts/test-lanes.mjs`（457 行）单一事实源，4 个 config 只是薄壳：

| # | Lane | Config | 需真实凭证 | 提交订单 | 默认 CI |
|---|---|---|---|---|---|
| 1 | hermetic | `vitest.config.ts` | ❌ | ❌ | ⚠ 仅 workflow_dispatch |
| 2 | integration | `vitest.e2e.config.ts`（**只 2 个文件**，强制串行 `singleFork`） | ❌ | ❌ | ❌ |
| 3 | external-readonly | `vitest.external.config.ts`（**本次 pull 后新增**） | ✅ | ❌（明确禁止） | ❌ |
| 4 | live-paper | `vitest.uta-live.config.ts` | ✅✅ | ✅（demo/paper） | ❌ 绝不跑 |
| 5 | system | `null`（真机/真容器/真远程） | ✅ | 视命令 | ⚠ 仅 Windows |

三个关键机制：

1. **fail-closed 的 live 门**：`vitest.uta-live.config.ts:12` 在 **config 加载时**调用 `assertLivePaperAcknowledgement()`（`test-lanes.mjs:450-457`）—— 未设 `OPENALICE_UTA_LIVE_PAPER=1` 就直接抛错，测试连收集都跑不起来。
2. **include/exclude 精确互斥**：同一个 e2e 目录被拆到三个 lane，`livePaperExcludes`（`:78-81`）显式把 `uta-lifecycle.e2e.spec.ts` 归 integration、`ccxt-hyperliquid-markets.e2e.spec.ts` 归 external。无重叠无遗漏。
3. **`an all-skipped run is not acceptance evidence`**（`test-lanes.mjs:98-107` 的 prerequisites）—— 直接封堵"测试全被 skip 所以是绿的"这一自欺。

`vitest.bbProvider.config.ts` 的删除与 `vitest.external.config.ts` 的新增在**同一 commit `9fe01e51`**（"test: split owner and risk lanes"）—— 是分片归并，不是功能退化。

**对 Aletheia 的直接对应**：Aletheia 已有 `tier_unit`/`tier_integration`/`tier_e2e`/`tier_spec` 四个 marker（`pyproject.toml:71-74`）。缺的是 OpenAlice 的两条：**按副作用等级而非速度分层**（哪一层会写真实账户？）、以及 **"全跳过不算验收证据"的显式规则**。

## 依赖、迁移、配置

| 维度 | Aletheia | OpenAlice | Vibe-Trading | nofx | tickflow |
|---|---|---|---|---|---|
| 锁文件 | `uv.lock`（105 包，20 直接依赖） | `pnpm-lock.yaml` 341KB + **39 条 overrides** + 4 条 patchedDependencies | `requirements-lock.txt` 188 条 + channels 锁，**`--require-hashes` 三平台验证** | `go.mod` 28 直接依赖 | `uv.lock` 2,949 行 + `pnpm-lock.yaml` |
| 迁移策略 | **additive-only + schema 版本 + 审计日志**（`migrations.py` 9,251 行） | 编号迁移 `NNNN_name/`，到 0041，**每个迁移自带 `.spec.ts`** | 无 alembic；`CREATE TABLE` 散落 6 文件；`config/migrate.py` 迁移目录布局非 schema | GORM `AutoMigrate`，无 migration 目录 | 无 RDBMS；Parquet 分区 + DuckDB，**缓存 key 编码版本号即"迁移"**（`matrix.py:53-55`） |
| 配置管理 | `config/schema.py`（pydantic） | zod 全量 schema + `z.infer` | **AST 门禁强制集中**到 `agent/src/config/` | `.env.example` + `config/config.go` | `pydantic-settings`，区分 frozen/非 frozen 路径 |

三个值得注意的点：

1. **OpenAlice 的每个迁移自带测试**（`src/migrations/0041_connector_desk_flag/{index.ts, .spec.ts}`）—— 迁移有回归保障。Aletheia 的 `migrations.py` 是单文件 9,251 行，无 per-migration 测试。
2. **Vibe-Trading 的 `--require-hashes` 三平台验证**（`test.yml:61-68`），注释解释原因："package markers are evaluated for the host running pip" —— 单平台验证会漏掉 marker 问题。
3. **nofx 的依赖树里 `modernc.org/sqlite`（纯 Go）与 `mattn/go-sqlite3`（cgo）同时存在**（`go.mod:29,72`），实际用的是纯 Go（`store/driver.go:11`）—— 未清理的依赖。

## 对 Aletheia 的可提取项

| # | 来源 | 提取什么 | 代价 |
|---|---|---|---|
| 1 | R20 `fa65097e` 的负向验证做法 | **门禁必须自带负向验证**：故意违反 → 必须报红，且要确认报红的是**这道门**而不是别的测试。R20 靠它证明退役 74 个历史锁没有掏空覆盖；同一方法也暴露了保留下来的子串匹配门抓不到改名 | 每道门多一个自检用例；Aletheia `test_alpha_cohort_reachability.py:367,385` 已有范例，应推广 |
| 2 | OpenAlice `test-lanes.mjs:98-107` | **`an all-skipped run is not acceptance evidence`** 写成规则 + 测试分层按**副作用等级**（哪层会写真实账户） | 新测试要判断归属，判断错会静默漏测 |
| 3 | Vibe `test_readme_counts.py` / R20 `test_failure_semantics_doc.py` | **文档锚点必须绑定可加载的运行时对象**（import + `issubclass` + `getattr`，不是正则）。Aletheia 有 145,822 行 docs + 613 个 md + 108 个 spec，锚点全靠人维护 | 需要一个能从运行时取值的测试；产品数量变化必须同步改文档 |
| 4 | Vibe `ci_grep_gates.sh:131-144` / R20 `test_okx_rest.py:457-491` | **AST 级架构边界门 + 白名单唯一出口**。Aletheia 的对应物：`os.environ` 直读、DB 直连、**绕过 Execution Gateway 自拼订单** | 集中层成为必经点；新增出口要登记 |
| 5 | R20 `4dbc0549` 的沙箱夹具完整性门 | **测试隔离不得把"已配置"变成"内置默认"**。R20 实测 `assets` 8→0 导致走"空池=不发单"拒绝分支、凭证库空导致 `dry_run=True` —— **用例全绿但验证的是另一个配置世界**。Aletheia 有大量 `ALETHEIA_DATA_DIR` 隔离测试，同类风险直接存在 | 一个遍历白名单模块路径常量的门 + "隔离清空了什么"必须逐条落进三个桶（失败关闭型/确定性默认型/可再生记录型） |
| 6 | Aletheia 自身 `dev_check` + baseline 棘轮 | **已有的正确做法，应扩展**：把更多"存量违规"用 baseline 冻结 + 只减不增（当前只有 except-pass 一项；`except Exception` 1454 处尚无门） | 每类需要一个 baseline 文件 |

## 必须点明的幻象

1. **OpenAlice 的 831 个测试文件 / 161,060 行测试，在普通 PR 上不构成合并门禁**（`ci.yml:60-63` 三个主 job 全部 `if: workflow_dispatch`）。它不伪装，但结论必须说清。
2. **nofx 的 75 个测试文件全部允许失败**（`test.yml:17,39` `continue-on-error: true`）。**唯一真阻断的是 `go vet` + `go build`。** 测试/生产比 0.25 已是最低，且这 0.25 不阻断合并。
3. **Vibe-Trading 的"CI 很严"实际不含任何静态类型检查**：ruff 配置齐全但 `.github/workflows/` 内零命中，无 mypy，无 pre-commit。与 1,821 处 `Dict[str, Any]` 互相印证。
4. **tickflow 的 CI 注释引用了一个不存在的门禁测试**：`.github/workflows/ci.yml:5` 写"后端含 Tier A 开放契约快照测试 (test_openapi_contract.py)"，但该文件**在当前 HEAD 不存在，`git log --all` 也无添加记录**。读 CI 的人会误以为 OpenAPI 契约有快照保护。
5. **R20 的门禁体系被动过，但方向与直觉相反**：`tests/audit/` **净增 6 文件 / 31 用例**（48→54 / 544→575），被退役的是 `tests/extraction/` 的 74 个"历史锁"用例（−60 净，945 行删除），且退役有负向验证证明未造成覆盖真空。**09-24 报告把 AST 对拍门列为 P0 提取项，这个判断需要降级**：R20 自己把这一类判为"占 0.8% 用例却需 38 个文件维护基线"而退役，并且自述保留下来的 shell 不变量**用子串匹配、实测抓不到改名**。`d78a6143` 另自述测试提速的 A 项**未完成**（只迁移 3/23 道门，166s→156s，远低于估算的 ~30s）。
6. **R20 的 `tests/extraction/` 里有 9 个测试名看起来像历史锁，实际不是**（如 `test_all_seven_cases_byte_identical`、`test_non_swap_inst_id_kept_verbatim`）—— 它们是同名但语义不同的业务行为断言，不碰 git。**按测试名正则统计门禁类型会得出错误结论**，必须按机制判据（是否调 `git show`、是否有 `PRE` 基线常量）。R20 作者自己在这一点上错过两次（报过 519、286，实际 74）。
7. **测试/生产比在六家中从 0.25 到 2.60，但与门禁强度无相关性**。不要把这个比值当对标目标。R20 比值最高且门禁最强，却完全没有 CI。
