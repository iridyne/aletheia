# 竞品横向对标报告 · 2026-09-28

> 对标对象：`/Users/yixian/Projects/references/` 下 6 个仓库
> Aletheia 基线：`master @ 6525cee5a`（2026-09-28 09:14 +0800）
> **所有竞品仓库在本次分析前已 fetch + ff-only 到最新**（OpenAlice 补 1946 commit、Vibe-Trading 1278、tickflow-stock-panel 948、R20 55、nofx 7）
> 数字全部本机实测，命令口径记录在各分域文档

## 本报告的分域文档

| # | 文档 | 回答什么 |
|---|---|---|
| 01 | [组织度](01_organization.md) | 模块边界、工作单元、治理文件、文档层 |
| 02 | [可读性](02_readability.md) | 文件/函数尺度、类型化、命名、注释、死代码 |
| 03 | [可维护度](03_maintainability.md) | 测试分层、门禁强度、CI 实际跑什么、lint/type、依赖与迁移 |
| 04 | [alpha 来源](04_alpha_source.md) | 信号从哪来、参数怎么定、回测与实盘是否同源、能否证伪 |
| 05 | [可靠性与交付](05_reliability_delivery.md) | 部署形态、release 节奏、失败语义、凭证、kill switch、运行证据 |
| 06 | [执行链缺口](06_execution_chain_gap.md) | demo 亏损里执行损耗 vs 方向判断的拆分 |
| 07 | [前端性能](07_frontend_performance.md) | K 线加载/UI 响应慢在哪 |
| 08 | [前后端剥离](08_frontend_backend_split.md) | 前端重写前哪些后端修复可先做 |
| **16** | **[alpha 机制对比（主线）](16_alpha_mechanism_comparison.md)** | **我们 vs R20 的 alpha 机制异同、可借鉴项** |
| — | [证据附录](99_evidence_index.md) | 六仓原始证据清单索引 + 本次未验证项 |
| — | 09–15（技术分支，含已暂停项） | detector 排查、regime 门测试等子问题，详见各文档开头的性质说明 |

## 参与对标的六个仓库

| 仓库 | 形态 | HEAD | commits | 作者 | 手写规模（实测口径） |
|---|---|---|---|---|---|
| **Aletheia** | 本地 AI-first 加密交易系统（Py+React） | `6525cee5a` 09-28 | 4713 | 8 | py 生产 268,953 / 测试 265,913 |
| **R20 / AstraQuant** | 加密波段量化终端（Py+Vue），已商业化改名 | `642701c4` 09-28 | 1372 | 4 | py 197,415（含测试） |
| **OpenAlice** | AI-first 个人交易运维平台（TS monorepo） | `0d4faa90` 09-24 | 4410 | 9 | 手写 ts+tsx 422,610（含测试 161,060） |
| **Vibe-Trading** | 开源 LLM 研究平台（Py），HKUDS | `0244ecea` 09-27 | 2619 | **193** | py 生产 248,957 / 测试 189,656 |
| **nofx** | Go 加密自动交易（LLM 决策） | `638d404` 09-05 | **57** | 3 | go 62,629 / 测试 15,941 |
| **tickflow-stock-panel** | A 股看板+回测（Py+React），商业化 | `3391e34` 09-27 | 948 | 36 | py 生产 85,031 / 测试 52,518 |

（第七个 `tickflow-ashare-akshare` 是 265 行的数据适配器，仅在 04/05 域作为失败语义样本出现。）

## 一句话结论

**Aletheia 在"能否证伪自己"这一维度上领先全部六家；在"runtime 能否自证还活着"这一维度上落后。**

> 初稿把结论写成"在能否交付上落后全部六家"。**Owner 裁定后修正**：Aletheia 是自用系统，分发能力（Docker/release/安装器/桌面包）不构成差距。真正的落后项收窄为一条具体的 runtime 缺口（见下表末行与 #3612）。

这两件事是同一个根因的两面：Aletheia 把工程预算投在了**度量装置**（前瞻账本、成本归因、反事实/OPE、独立单元定义）和**语义治理**（148,633 行 md、108 个 executable spec），而六家竞品把预算投在**产品闭环**（前台配置中心、真实成交、自监管）。

关键实测对照：

| 维度 | Aletheia | 六家竞品 |
|---|---|---|
| 有反事实/OPE 度量 | **✅ 唯一** | 全部 ❌（六家零命中） |
| 有回测引擎 | ❌ 没有 | R20/Vibe/tickflow 有；OpenAlice/nofx 有退化版 |
| 回测与实盘同源 | — | **仅 tickflow 同源**；R20/Vibe/OpenAlice/nofx 均非同源 |
| 真实成交闭环证据 | **❌ #2899 仍 open** | R20/nofx 有（自述）；其余无 |
| 启动前置检查（fail-closed） | **✅ 有**（`preflight.py` + `assert_migrations_current`） | R20/OpenAlice/nofx 有；Vibe/tickflow 无 |
| **卡死能否被服务管理器发现** | **❌ 唯一真缺口**（`Type=notify` 无 `WatchdogSec`） | R20 ✅ 心跳四态；tickflow ✅ 悬死锁探针；OpenAlice ✅ guardian |
| 静默失败是否有门禁 | 部分（except-pass 42 处已棘轮；**281 处宽异常无门**） | OpenAlice 空 catch 仅 5 处；nofx `_ = err` 为 0 |
| 文档/生产代码比 | 55% | R20 1.4%、tickflow 8%、OpenAlice 9%、Vibe 27%、nofx 61% |
| 分发能力（Docker/tag） | 不适用（自用） | 全部有 —— **不计为差距** |

## 五个跨仓横向发现

### 1. "回测通过"在六家中有五家不构成对实盘的任何保证

这是本次最有价值的横向发现，因为它证伪了"Aletheia 缺回测引擎"是一个短板：

- **Vibe-Trading**：回测执行的 `signal_engine.py` 是 **LLM 每次 run 现场生成的一次性代码**（`agent/backtest/runner.py:226-229` importlib exec_module）；实盘走 `trading/service.py:685` 的 connector SDK。跨层引用仅 2 处且都是数据 loader。
- **R20**：回测引擎测 SMA(5)/SMA(15) 交叉，实盘用 `scoring.py` 的 composite score，两套逻辑互不相关（09-24 审计已坐实，本次复核见 04 域）。
- **nofx**：Python 回放脚本**手写复刻** Go 生产节流逻辑，`simulate.py:5` 自述 "Mirrors the live semantics of trader/auto_trader_throttle.go" —— 两套实现靠人工同步，无 drift 检测。
- **OpenAlice**：`simulate.ts` 185 行，零券商依赖、无撮合/滑点/手续费模型，架构上被依赖方向禁止依赖交易层（`simulate.ts:20`）。
- **tickflow-stock-panel**：**唯一真同源**——回测矩阵层与看板指标管线 import 同一个 `price_limits.py`（`matrix.py:31` / `pipeline.py:35`）。

**含义**：如果 Aletheia 补一个回测引擎但不保证与实盘同源，就是加入这五家的行列。同源性比"有没有"重要得多。

### 2. 六家全部没有反事实度量——这不是巧合，是结构性选择

grep 全仓，counterfactual / OPE / forward-outcome / shadow-cohort 在六家均零命中。它们的共同形态是**只记录已执行的单**：

- R20 `trades` 表只记真实成交 → 无法回答"那道 75% 置信度门禁在挣钱还是亏钱"
- nofx `store.DecisionRecord` 记 prompt/响应/CoT 全文，但不记"被拒绝的候选后来涨没涨"
- OpenAlice `trade-provenance.ts` 把 AI 决策与券商订单用 hash 绑定（审计链很强），但同样不含对照组

Aletheia 的 `universe_forward_outcomes` / `filter_candidate_outcomes` 对**每个候选**（不论是否开仓）记录固定 horizon 结果 + 三项成本分离归因，且 `net` 在成本未知时返回 None 而非 0（`outcome_economics.py:42`）。这是六家结构上做不到的事。

**但要诚实**：这个装置目前只量出"方向毛边缘 t = −0.10，与 0 不可区分"（3.3 小时 / 1 个独立单元）。尺子准，但还没量出正边缘。

### 3. 门禁的载体决定治理成本；三家已经把纪律编译成测试

09-24 的 R20 报告结论（"R20 把纪律编译成测试，Aletheia 写成文字"）**在本次扩大样本后需要修正**：Aletheia 已经补上了这一层。

| 仓库 | 真门禁样本 | 断言的是什么 |
|---|---|---|
| **Aletheia** | `tests/audit/test_no_silent_except_pass.py` | baseline 冻结 + 只减不增；164→42 处（`silent_except_pass_baseline.json`） |
| **Aletheia** | `tests/audit/test_alpha_cohort_reachability.py` | cohort anchor 必须有生产可达的 writer，**含两个负向自检**（只被测试调用的 writer 必须被报告） |
| **Vibe-Trading** | `agent/tests/test_readme_counts.py` | README 数字 == 运行时注册表（`asyncio.run(mcp.list_tools())`），15 个契约测试 |
| **Vibe-Trading** | `tools/ci_grep_gates.sh:131-144` | AST 级：`agent/src/config/` 之外禁止任何 `os.getenv` |
| **tickflow** | `backend/tests/test_write_lock_scope.py` | 重活必须在全局写锁**之外**（对应 2026-09-07 全站冻结事故） |
| **OpenAlice** | `scripts/development-test-contract.spec.ts:66` | 禁止别名残留（`toBeUndefined()`），clean cutover 工程化 |

**反面**：nofx 无门禁型测试（测试全是行为断言）；R20 本次**退役了 74 个提取门禁用例**（见 04 域）。

### 4. "测试很多"在四家中被证明不等于"合并被守护"

| 仓库 | 测试规模 | 默认 PR 门禁实际跑什么 |
|---|---|---|
| **OpenAlice** | 831 文件 / 161,060 行 | **只跑 2 项**：`test:contract:workflow` + `tsc --noEmit`。三个主 job 全部 `if: workflow_dispatch`（`ci.yml:60-63`） |
| **nofx** | 75 文件 / 15,941 行 | 测试 job 全部 `continue-on-error: true`（`test.yml:17,39`）；**唯一阻断的是 `go vet` + `go build`** |
| **Vibe-Trading** | 708 文件 / 189,656 行 | 跑全量 pytest，但 `e2e_backtest/` 永久 `--ignore`；**ruff/mypy 在 CI 零执行** |
| **Aletheia** | 885 文件 / 265,913 行 | `dev_check` 变更行门禁 + CI 分批 pytest；`tier_unit` 8,495 用例 300s 跑不完 |

OpenAlice 的做法值得单独说：它**不伪装**。`ci.yml:17-21` 主动写明这是"bounded automatic gate"，且分类器从 base revision 加载以防候选分支削弱自己的 lane（`:39`）。比"跑 800 个测试但全被 skip"诚实得多。

### 5. 商业化会反向塑造 alpha —— R20 是本次最重要的新信号

R20 在 v8.3.0 → v8.4.0（+55 commit）期间：改名 AstraQuant（`0xethanq/astra-quant-agent`）、**硬编码 OKX broker tag**、新增策略广场分享、并把预设策略升级为 **"high trade frequency" + "dynamic aggressive risk"**。

结构性激励：交易频率 ↑ → 手续费 ↑ → 经纪商返佣 ↑。而手续费是交易者的成本。

**这不是指控动机，是指出一个结构**：当返佣成为收入来源，"提高频率"的参数改动就不再是中性的工程决策，而它**没有任何回测证据支持高频更赚钱**（详见 04 域）。

对 Aletheia 的直接含义：**不要把 R20 的参数选择当作经过验证的基准**。09-24 报告建议"收窄标的池、加置信度门禁"仍然成立（那是政策决定），但 R20 新版的频率/风险参数不应被参考。

## 按收益排序的建议

> **范围裁定（2026-09-28，Owner）**：Aletheia 是**自用系统，不面向外部客户**。因此 Docker、release 节奏、安装器、桌面分发等"分发能力"维度**不作为差距项**，已从建议中移除。保留的是同机 **runtime 行为**与 **alpha 度量**。

| # | 动作 | 依据 | 状态 / 量级 |
|---|---|---|---|
| 1 | **前瞻账本连续跑 ≥2 周**，按 `nonoverlap-4h/v1` 聚合 | 唯一的稀缺资产目前只有约 1 个独立单元；六家都没有这个装置，浪费它是最大的损失 | 运行纪律，非代码 |
| 2 | **给 systemd 一个"循环还在转"的信号** | unit 是 `Type=notify` 却无 `WatchdogSec`，`READY=1` 只在启动发一次 → `Restart=on-failure` 只在**进程退出**时生效，"进程活着、循环不转"永不重启。R20 `e506da2e` 把这个失败模式命名为**"一切正常，就是不下单"** | **已开 [#3612](https://github.com/iridyne/aletheia/issues/3612)** |
| 3 | **把静默宽异常纳入现有门禁棘轮** | `test_no_silent_except_pass.py` 只管 body 字面为 `pass` 的 42 处；`except Exception: return <中性值>` 无门禁，实测 281 处，且 `run_logger.py:176-188` 已把未知成本渲染成 `0.0` —— 违反本仓自己的 `outcome_economics.py:40-45` | **已开 [#3613](https://github.com/iridyne/aletheia/issues/3613)** |
| 4 | **`tier_unit` 反馈循环压到秒级** | 8,495 用例 300s 未跑完；这是乘性成本，每轮 AI 迭代都要等 | 需要先定位慢用例 |
| 5 | 若要补回测，**必须与实盘同源** | 五家中四家的回测不同源，等于没有；tickflow 的 `price_limits.py` 单一真相源是唯一正确样本 | 设计约束，不是工作量 |

**不该做的**：

- 不要因为"六家都有回测引擎"就补一个不同源的回测引擎。
- 不要参考 R20 v8.4.0 的频率/风险参数（返佣激励污染）。
- 不要为了追赶文档/代码比而删治理文档——应该是**把能编译成门禁的编译掉**，Aletheia 的 `tests/audit/` 已经证明这条路可行（164→42）。
- 不要把六家的"测试文件数"当作对标目标（四家已证明与门禁强度脱钩）。
- **不要按"竞品有什么"反推"我们缺什么"**。本次初稿据此误判了两条（启动前置检查、unit 不在仓库），复核后发现 Aletheia 都有对等物且实现更强。正确顺序是先在本仓找对等物。
