# 01 · 项目组织度

> 对标日期 2026-09-28。所有竞品仓库已 pull 到最新 HEAD。
> 口径：模块边界是否真解耦、工作单元形态、AI 协作治理文件、契约/设计文档层、重复实现与双主线。

## 横向总表

| 维度 | Aletheia | R20/AstraQuant | OpenAlice | Vibe-Trading | nofx | tickflow |
|---|---|---|---|---|---|---|
| 作者数 | 8 | 4 | 9 | **193** | 3 | 36 |
| Top1 作者占比 | 61%（iridite 2869/4713） | — | 68.5%（Ame 3021/4410） | 49.1%（Haozhe Wu 1287/2619） | 88%（50/57） | 55%（shy3130 517/948） |
| merge PR 数 | — | — | 1255 | 663（#21→#1612） | PR 制但不强制 | 203 |
| AI 治理文件 | AGENTS.md + 6 份 agent-guides | **0** | AGENTS.md 195 行 + CLAUDE.md 6 行 | **0**（`agent/SKILL.md` 是产品资产不是治理） | `agents.md` 922 行（是产品助手规范，非编码治理） | AGENTS.md 12 行（路由到 CONTRIBUTING.md 374 行） |
| 契约/设计文档层 | `docs/design/contracts/` + `tests/specs/` 108 个可执行 spec | **0** | `docs/` 50 文件 / 24,960 行 | `docs/` 被 CI 显式排除 | `docs/architecture/` + `docs/plans/` | `CONTRIBUTING.md` 含 4 类硬数据契约 |
| md 总行数 | **148,633** | 2,761 | 36,469 | 67,634（含 7 语言 README 副本） | 37,954（含 i18n 3,938 + 第三方论文报告 2,521） | 8,887 |
| md / 生产代码 | **55%** | 1.4% | 8.6% | 27% | 61% | 10% |
| 双主线/重复实现 | — | — | 未发现（有防别名门禁） | **有**：`cli/main.py` + `cli/_legacy.py` 6656 行影子模块 | **有**：`kernel/engine.go` 自由裁量 + `kernel/grid_engine.go` 618 行独立网格引擎 | 未发现（`execution_backend` 是有意的渐进迁移标记） |

## Aletheia 自身事实

模块边界：`core/aletheia/`（38 个子域）+ `extensions/`（14 个可选域）+ `ops/` + `legacy/`。生产 py 268,953 行，其中最大三个文件：

- `core/aletheia/storage/migrations.py` 9,251 行
- `core/aletheia/storage/repositories.py` 6,656 行
- `core/aletheia/agent_runtime/scheduler.py` 4,657 行

工作单元：GitHub issue + SDD Depth（D0/D1/D2）+ Contract Delta 声明 + `tests/specs/` 可执行规范。19 个 release tag（`v0.8.6`），走 release-please。

**最显著的结构特征**：`docs/` 145,822 行 md 对 268,953 行生产代码 = 55%。六家竞品中只有 nofx 的 61% 更高，但 nofx 的 md 里有 3,938 行 i18n 翻译副本 + 2,521 行是**第三方论文分析报告**（`docs/research/AI-Trader-Analysis-Report.md`），剔除后实际比例远低。

## 各仓关键事实

### R20 / AstraQuant — 零治理文件，但已商业化改名

09-24 审计的结论（零 `AGENTS.md`/`CLAUDE.md`、零 `.github/`、零契约层、零 issue 跟踪）本次仍需复核（R20 delta 报告）。**本次新发现的组织度变化**：

- `c7424081` 全量改名内部代号 r20 → astra，**含运行态数据迁移**
- `2c6a183d` 改名收尾接入**启动前 fail-closed 检查**，并修掉它抓出的三处真实缺陷
- `c84bf651` 改名完整性审计：修掉一个潜伏故障并**给这一类加门禁**
- `bfe67d56` 仓库地址改为 `0xethanq/astra-quant-agent`

这是一个**大规模重命名的工程样本**：改名 → 启动前 fail-closed 校验 → 用校验抓出真实缺陷 → 给这一类加门禁。Aletheia 若要做大规模概念改名（#3569 的"5 个模块为一个概念铸造独立语法"正是这类问题），这个序列可直接借。

### OpenAlice — 组织度最强，但 `src/` 藏着 12.5 万行单体

**关键事实：`src/` 不在 pnpm workspace glob 内**（`pnpm-workspace.yaml:1-6` 只有 `apps/*`、`packages/*`、`services/*`、`ui`）。`src/` 是根 package 自己的源码目录，124,642 行，含 webui routes + workspaces 服务 + core config + domain 逻辑。

内部大文件：`src/workspaces/service.ts` 3,612 行、`src/webui/routes/workspaces.ts` 3,398 行。

workspace 切分发生在**按可替换的外部依赖切包**：11 个包里 5 个是券商适配器（`uta-broker-alpaca`/`-ccxt`/`-longbridge`/`-ibkr`/`-leverup`）+ 2 个协议层（`uta-protocol`/`connector-protocol`）。这个切分理由是真实的，但不应读成"整体模块化良好"。

显式所有权契约：`docs/project-structure.md:341` 写下 **`UTA is the only owner of broker connections and trading writes`** —— 这是文档化的单一所有权，不是口号。Aletheia 的 Execution Gateway 唯一入口是同一类设计。

工作单元四层并存：issue（编号直接进测试名，`src/tool/trading.spec.ts:285` `describe('tradingPush — AI-trading gate (#95)')`）+ `plans/` 19 文件 + Conventional Commits（覆盖率仅 34.6%）+ 1255 个 merge PR。

**粒度失控**：`plans/office-floor.md` 单文件 **569.6KB**，`plans/tui-experience.md` 279.7KB。文档粒度失控比代码粒度更显著。

防别名门禁：`scripts/development-test-contract.spec.ts:66` `it('removes the ambiguous legacy root test names instead of keeping aliases')`，`:63` `expect(scripts['test:system:package-manager']).toBeUndefined()`。同一 commit `9fe01e51` 删除 `vitest.bbProvider.config.ts` 并新增 `vitest.external.config.ts` —— **分片归并而非留别名**，clean cutover 被工程化执行。

### Vibe-Trading — 唯一真社区项目，但双主线明确

193 位作者、663 个 merge PR（#21→#1612）、有 PR 模板。**但无 `CODEOWNERS`**（`git ls-files | grep CODEOWNERS` 无输出），PR 模板里声明的"保护区域"（`src/agent/`、`src/session/`、`src/providers/`）没有 GitHub 层面的强制 reviewer 绑定。

**双主线坐实**：`agent/cli/main.py`（64KB）+ `agent/cli/_legacy.py`（**6,656 行**）并存。`agent/cli/__init__.py:28-35` 用 `dir(_legacy)` 循环 re-export 旧模块公共符号；`:96-99` 再对每个 `cmd_*` 包一层 wrapper。

wrapper 存在的理由写在 `agent/cli/__init__.py:76-79`：

> "Wrap every `cmd_*` callable re-exported from `_legacy` so a package-level monkeypatch of any symbol in `_LEGACY_SYNCED_GLOBALS` is propagated to `_legacy` before the call."

即：**生产代码为了配合单元测试的 monkeypatch 而存在**。`_legacy.py` 是活代码（`__init__.py:73` 仍调 `_legacy.cmd_init()`），不是死代码，但也不是新架构的一部分。

**这是 Aletheia 必须警惕的形态**：测试形状开始反向塑造生产代码。Aletheia 有 885 个测试文件，同类风险存在。

文档层的反直觉事实：`docs/` 被 CI 门禁**显式排除**，理由写在 `tools/ci_grep_gates.sh:59-63`：

> "docs/ is excluded: those are internal planning docs ... docs/ is not shipped to PyPI / public consumers (memory: feedback_no_push_docs)"

注意 `(memory: feedback_no_push_docs)` —— 维护者用外部 memory 系统记录协作偏好，该 memory **不在仓库内，外部贡献者不可见**。

### nofx — 57 个 commit 里第一条吃掉 16.8 万行

`277b5ad`（2026-06-30）：**620 files changed, 168740 insertions(+), 0 deletions(-)**。这是一次性倒入，之前的开发过程完全不可追溯。

**含义**：任何基于"这个团队的 commit 粒度/PR 流程有多规范"的判断只能基于最近约 2 个月的窗口，不应外推。

双主线：`kernel/engine.go`+`engine_prompt.go`+`engine_analysis.go`（自由裁量 AI 决策，主线）与 `kernel/grid_engine.go`（618 行，独立的网格 prompt/parse，`GetGridDecisions`/`parseGridDecisions` 在 `:443,489`）。两者共享 `Decision` 类型但 prompt 构建与解析函数完全独立。

职责重叠：leverage/position-size 校验在 `kernel/engine_position.go`，hold-time/re-entry 节流在 `trader/auto_trader_throttle.go`，两层都在"约束 AI 输出"，边界靠命名与调用顺序维系，**无显式接口契约文档**。

`agents.md` 922 行是"NOFXi 交易智能助手"的 skill 架构规范（面向对话助手），**不是编码 agent 协作规范**。未发现 `AGENTS.md`（大写）/`CLAUDE.md`/`.cursorrules`。

### tickflow-stock-panel — 分层治理最务实

顶层边界清晰：`backend/app`（FastAPI + Polars/NumPy/Numba）与 `frontend/src`（React+Vite）严格分离，`packaging/`（PyInstaller 桌面打包）独立。

`backend/app` 内部按领域分模块：`backtest/`、`strategy/`、`factors/`、`indicators/`、`data_providers/`、`tickflow/`、`plugins/`（`stocksdk`、`fuyao` 两个数据源插件，YAML 声明式注册）、`extensions/`（二次开发扩展点）。模块边界在 `CONTRIBUTING.md:63` "2.3 模块边界" 有显式定义。

**AGENTS.md 只有 12 行**，强制指向 `CONTRIBUTING.md`（374 行）与 `docs/secondary-development.md`（406 行）。这是**分层路由设计**而非空文件充数 —— 对比 Aletheia 的 AGENTS.md（本次实测 10.9KB）+ 6 份 agent-guides，tickflow 的做法是"一个入口 + 一份完整规范"。

契约文档最值得借的一条：`CONTRIBUTING.md` 的 **"3. 不可混用的数据契约"**，列出 4 类硬契约：比例/百分比、复权、时区/交易日、历史股本换手率。这些是多人协作中最容易被悄悄破坏的隐式契约。

文档不腐烂的机制：`docs/factor-system-design.md:740` 显式标注 **"【设计】" vs "【已实现】"** 状态迁移规则。

## 对 Aletheia 的可提取项

| # | 来源 | 提取什么 | 代价 |
|---|---|---|---|
| 1 | tickflow `CONTRIBUTING.md` | **"不可混用的数据契约"清单**：把最易被破坏的隐式契约（单位、时区、复权/费用口径）列成一张硬表 | 文档与代码同步滞后风险；应配门禁 |
| 2 | tickflow `docs/factor-system-design.md:740` | **文档内的【设计】/【已实现】状态标记** | 需要维护纪律；但成本极低 |
| 3 | R20 `c7424081`→`2c6a183d`→`c84bf651` | **大规模改名三步法**：全量改名 → 启动前 fail-closed 校验 → 用校验抓出的缺陷反推门禁 | 一次性工程；直接适用于 #3569 的概念统一 |
| 4 | OpenAlice `development-test-contract.spec.ts:66` | **禁止别名残留的门禁测试**（`toBeUndefined()`） | 重构摩擦：改名必须同时改测试；只覆盖命令面 |
| 5 | OpenAlice `docs/project-structure.md:341` | **单一所有权写成文档契约**（"UTA is the only owner of..."）；Aletheia 的 Gateway 唯一入口应有同等显式表述 | 无 |

## 必须点明的幻象

1. **nofx 的 md 61% 看起来治理很重 —— 实际含 3,938 行 i18n 翻译副本 + 2,521 行第三方论文报告**（`docs/research/AI-Trader-Analysis-Report.md` 是对一篇 arXiv 论文的分析，不是本项目文档）。
2. **Vibe-Trading 的 67,634 行 md 里有 7 个语言的 README 副本**，1278 commit 期间 README 变更 127 次 + 6 个翻译版各约 100 次 —— 很大一部分"文档量"是翻译同步维护。
3. **`agents.md` / `SKILL.md` 不一定是 AI 协作治理文件**。nofx 的 `agents.md` 是产品内的助手规范；Vibe 的 90+ 个 `skills/*/SKILL.md` 是运行时 prompt 资产。按文件名统计"有 AI 治理"会得出错误结论。
4. **OpenAlice 的 9 位作者里有 2 个是 AI 署名**（Claude 20、Cursor Agent 15），但另有 869 个 commit message 含 `codex/` 分支名且以 Ame 身份提交 —— **AI 署名计数严重低估实际 AI 参与度**。按作者数判断"人力投入"在 AI 时代已不可靠。
