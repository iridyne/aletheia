# 99 · 证据索引与未验证项

## 基线绑定（所有结论只对这些 commit 成立）

| 仓库 | 路径 | HEAD | 时间 | 本次 pull 补了多少 commit |
|---|---|---|---|---|
| Aletheia | `/Users/yixian/Projects/aletheia`（worktree `rival_analysis`） | `6525cee5a` | 2026-09-28 09:14 +0800 | — |
| R20 / AstraQuant | `references/r20-quantum-trader` | `642701c4`（tag v8.4.0） | 2026-09-28 08:52 +0800 | **55** |
| OpenAlice | `references/OpenAlice` | `0d4faa90` | 2026-09-24 22:50 +0800 | **1946** |
| Vibe-Trading | `references/Vibe-Trading` | `0244ecea` | 2026-09-27 10:57 +0800 | **1278** |
| nofx | `references/nofx`（`dev`） | `638d404` | 2026-09-05 11:54 +0900 | **7** |
| tickflow-stock-panel | `references/tickflow-stock-panel`（detached） | `3391e34` | 2026-09-27 23:01 +0800 | **948**（原本地只有 1 个 squash commit） |
| tickflow-ashare-akshare | `references/tickflow-ashare-akshare` | `7d22faf` | 2026-08-07 15:22 +0800 | 0（已最新） |

**pull 过程中的两处非常规操作**（记录以便追溯）：

1. OpenAlice 有一处本地未提交改动（`package.json` 的 `packageManager` 从 `pnpm@11.7.0` 改为 `pnpm@11.20.0+sha512...`），已 `git stash push -m "local pnpm packageManager pin (pre-rival-analysis 2026-09-28)"` 后 ff-only。**需要时可 `git stash pop` 恢复。**
2. tickflow-stock-panel 的本地 squash 快照与 origin **无共同祖先**（`refusing to merge unrelated histories`），改为 `git checkout --detach origin/main`。原本地 `main`（commit `99bdec8`）**未删除**，仍可切回。

R20 仓库有 28 条 untracked（`data/tmp*`、`tests/.collision-probe-*`、`.untracked-probe-*`）—— 溯源确认是 **R20 自身审计门的 `TemporaryDirectory` 探针残留**，非本次分析产生。

## 原始证据文档（子 Agent 产出，未精简）

| 文档 | 覆盖 | 规模 | 审计者 |
|---|---|---|---|
| `local://rival-r20-delta-evidence.md` | R20 v8.3.0→v8.4.0 增量 + 15 条旧结论三态复核 | ~84KB / 1011 行 | hard-task |
| `local://rival-openalice-evidence.md` | OpenAlice 全 7 域 | 1089 行 | hard-task |
| `local://rival-vibe-trading-evidence.md` | Vibe-Trading 全 7 域 + 20 条旧报告复核表 | 785 行 | hard-task |
| `local://rival-nofx-evidence.md` | nofx 全 7 域 | 165 行 | task |
| `local://rival-tickflow-evidence.md` | tickflow-stock-panel 全 7 域 + akshare 附录 | 147 行 | task |

**前置材料（本次复用，未重做）**：

- `docs/research/audits/COMPETITIVE_ANALYSIS_R20_2026-09-24.md`（399 行，绑定 R20 `34a0d29`/v8.3.0）
- `docs/research/audits/R20_MIGRATION_DECISION_2026-09-24.md`（251 行）
- `docs/research/audits/ALPHA_MEASURABILITY_2026-09-24.md`（333 行，Aletheia 自身 alpha 度量，714 笔样本）
- `references/Vibe-Trading-competitive-analysis-2026-08-10.md`（27KB，绑定旧 commit `b71ef5ce`）

## 我本人独立复核的条目（非采信子 Agent）

以下事实我亲自读了原文，不是转述：

| 事实 | 位置 |
|---|---|
| R20 README 声称回测与实盘共用 risk/sizing 代码路径 | `r20/README.md:215` |
| R20 回测实际是内联 SMA(5)/SMA(15) + 硬编码 conf/rr/ATR | `r20/scripts/backtest_engine.py:180-194` |
| R20 回测的全部 import（无 `scoring`/`risk_constants`） | `r20/scripts/backtest_engine.py:35-43` |
| `backtest/lifecycle.py` 只被 `backtest_engine.py` import，非实盘共享 | `grep -rn "backtest.lifecycle\|evaluate_position_exit"` 非测试代码仅 1 处 |
| broker tag 硬编码值 | `r20/scripts/okx_rest.py:49` |
| `6188055a` commit body 含 "maximize broker commissions" | `git show 6188055a --stat` |
| Aletheia `net` 拒绝把未知当零（docstring + 实现） | `core/aletheia/universe/outcome_economics.py:40-45, 95-102` |
| Aletheia `INDEPENDENT_UNIT_VERSION` | `core/aletheia/universe/economic_utility.py:21` |
| Aletheia 六个 mechanism 打分函数行号 | `core/aletheia/universe/continuous_scoring.py:449,543,627,819,924,1013,1149` |
| Aletheia except-pass 门禁与 baseline（164→42） | `tests/audit/test_no_silent_except_pass.py:1-36,77,109` + `silent_except_pass_baseline.json` |
| Aletheia alpha cohort 门禁的两个负向自检 | `tests/audit/test_alpha_cohort_reachability.py:338,367,385` |
| Aletheia 零回测引擎、零参数搜索 | `git ls-files | grep -iE 'backtest'` + `grep -rlEn 'walk_forward|grid_search|optuna' core extensions scripts` 零命中 |
| Aletheia 最长函数 AST 统计（7,046 函数，99 个 >200 行） | 本机 `ast` 脚本实测 |
| Aletheia `dev_check` 的门禁构成与"不跑 ruff format"的理由 | `scripts/dev_check.py:7-18,216-217` |
| Aletheia 类型化计数（288 BaseModel / 169 dataclass / 135 处 TS any） | `grep` 实测 |
| Aletheia 无 Docker、19 个 tag、2 个 systemd unit | `git ls-files | grep -i docker` 零命中；`git tag | wc -l`；`ls deploy/systemd/` |

## 本次未验证 / 不可验证项（不要当结论用）

### 结构上无法核查

1. **六家的真实收益**。R20/AstraQuant 官网未公布任何胜率数字；nofx 的 4154 cycles 是决策回放不是实盘业绩；OpenAlice/Vibe/tickflow 无收益记录。**任何"谁更赚钱"的判断都没有证据基础。**
2. **R20 的实盘样本量、时间跨度、资金规模**。本次审计期间在其 `astra_gateway.db` 见到真实事件（08:31 开仓 SUI 等），证明"它在真的交易"，但不证明盈利。
3. **R20 策略广场的战绩真实性** —— 自报，无第三方校验。

### 本次没做（可补）

4. **Aletheia 的 PCHIP 曲线控制点、ATR 倍数、置信度阈值的来源**。是拍的还是有依据，未追溯。这与 nofx 的可追溯注释（`auto_trader_throttle.go:13-20`）形成直接对比，建议单独查。
5. **Aletheia 的 1454 处 `except Exception` 的分布与危险度**。只统计了数量，未分类哪些在交易权威路径上。
6. **OpenAlice 的最长函数 AST 统计**。其审计报告显式标 `[推断]` 未做，本报告对应格留空。
7. **未重跑 Aletheia 的 alpha 度量**。04 域引用的 714 笔样本 / t=−0.10 全部来自 09-24 的 `ALPHA_MEASURABILITY` 报告，本次未重新查库。
8. **六家的测试实际执行状态**。全程未跑任何测试套件（只读约束），所有"测试数"是文件/函数定义数，不是通过数。09-24 报告在本机跑 R20 得到 7488/74.6s 但含 742 errors（环境缺依赖），该数字**偏乐观、不可引用**。
9. **tickflow 的 `fuyao` 模块**（HEAD commit 提到 `test(fuyao)`）未深挖是什么。

### 口径警告

10. **所有 LOC 数字都依赖剔除口径**。OpenAlice 的 36 万行 TS 含 4.9 万行生成码/i18n；其 3.17 万行 py 中 3.14 万是 vendored IBKR SDK（手写仅 298 行）。Vibe-Trading 的 44 万行 py 中 43% 是测试，且 `.mp4`/`.png` 按 `\n` 计数会额外虚增 16 万"行"。**跨仓引用规模必须带口径。**
11. **"测试文件数"不可比**：各仓命名约定不同（`test_*.py` / `*_test.go` / `*.spec.ts` / `spec_*.py`），本报告已尽量统一但仍有 ±5% 口径差。
12. **R20 的测试/生产比 2.60 沿用 09-24 口径**，本次未重算（改名后路径全变）。

## 对 09-24 R20 报告的修正清单

本次把三份既有审计当"待核实的假设集"，以下是需要修正的：

| 09-24 的结论 | 本次判定 | 依据 |
|---|---|---|
| "Aletheia 分散，无独立 audit 目录" | **已过时** | 当前有 `tests/audit/`（12 文件）+ `tests/specs/`（108 文件）；`test_no_silent_except_pass.py:32-35` 显式 Refs 那份报告 |
| "R20 的 AST 提取对拍门是 P0 提取项" | **需降级** | R20 自己退役了 74 个此类用例（`fa65097e`，945 行删除），理由是"占 0.8% 用例却需 38 个文件维护基线"；且保留的 shell 不变量用子串匹配、实测抓不到改名 |
| "R20 的 `tests/audit/` 48 个门" | **成立且增强** | 实测 48→54 文件、544→575 用例。`test_failure_semantics_doc.py`（import+issubclass 三重校验）与 `test_circuit_breaker_twin_parity.py`（AST 决策序列比对）均验真 |
| "文档腐烂双向门、时间戳单位门、空洞断言门是 P1" | **仍成立** | 本次未逐个复核，但 `test_directory_docs_current.py` 在区间内被 modified，机制仍在 |
| "R20 零契约层" | **成立（微调）** | 无契约目录，但有 `docs/BEIJING_TIME_CONTRACT.md`、`docs/FAILURE_SEMANTICS.md` 这类单文件契约 |
| "R20 的 `risk_per_trade_usd` 是固定美元额" | **已证伪** | `075909d6` 移除硬编码 15U/250U，改为纯比例 + 可选硬顶（0 = 无硬顶） |
| "R20 只做 10 个手工币" | **成立** | `scripts/instrument_pool.py:57-67` `DEFAULT_INSTRUMENTS` 仍是 10 条 |
| "R20 议会/多模型生产未启用" | **成立** | `astra_backend/council/__init__.py:25-27` 原文仍写"未启用（`enabled=false`）" |
| "R20 无反事实/shadow/forward-outcome" | **成立** | `grep -rni "counterfactual\|shadow_mode\|forward_outcome\|propensity" --include=*.py` **零命中** |
| "R20 的 md 2195 行 / 文档占产出 4%" | **数字过时，量级成立** | 现 2761 行 / 17 文件；对 55,539 行生产 py ≈ 5.0%，仍是量级差异 |
| "把 15,900 行治理文档压到 3,000 行以内" | **建议保留但要修正方向** | 正确做法是"能编译成门禁的编译掉"，Aletheia 的 except-pass 门（164→42）已证明可行；不是单纯删文档 |
