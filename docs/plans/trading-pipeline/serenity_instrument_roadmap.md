# Serenity 标的扩展 Roadmap（Phase 6）

> 2026-06-28 | 父计划：[`docs/plans/trading-pipeline/serenity_execution_path.md`](serenity_execution_path.md) §7
> 哲学源头：[`docs/research/audits/SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS_2026-06-27.md`](../../research/audits/SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS_2026-06-27.md) §5 / §8
> Owner 拍板：D1 = 「长期 roadmap，现在不做」（记忆 `aletheia-serenity-integration-decisions`）
> 状态：**纯文档**。不动 code、不动 schema、不动 spec、不动 Settings UI。

---

## 0. 这份文档是什么

Serenity 执行路径多阶段计划的 Phase 6。前 5 个 phase 是代码+spec 工作（D2 证据生成/评测分离、D3 promote→PI 评估桥、D4 paper-vs-live 结构预埋、Phase 4 trade_school 长持机制、Phase 5 live-readiness 结构核验）。Phase 6 是 **roadmap，不写代码**：

- 记录 Serenity 哲学（深研产业链供需，找出被市场低估的环节、长期持有）与当前可触达标的之间的**真实差距**。
- 列出后续可调研的方向（Bitget RWA / OKX 产业链相关标的），作为 future work 入口，**不立即纳入 universe**。
- 锁定一条防止此文档悄悄触发副作用的护栏（§6）。

**重要**：本文档不开启 Serenity 的任何新标的、不翻转 `execution_eligible`、不改 universe seed、不动 OBS-S04 spec。任何后续真实纳入都需要单独 plan + SDD spec + owner 显式批准 + 写进 DECISIONS.md。

---

## 1. 现状诊断：Serenity 论点与执行标的的结构性错位

### 1.1 当前 10 个 xStock 是什么

`aletheia/data/symbols.py:_XSTOCK_BASE_TO_TICKER` 显式枚举的标的：

| xStock 内部基址 | 实质映射 | 类别 |
|---|---|---|
| AAPLON | AAPL | 美股大盘 — 消费电子终端品牌 |
| TSLAON | TSLA | 美股大盘 — 整车 OEM |
| NVDAON | NVDA | 美股大盘 — GPU 设计（fabless） |
| MSFTON | MSFT | 美股大盘 — SaaS / cloud |
| AMZNON | AMZN | 美股大盘 — 电商 / cloud |
| GOOGLON | GOOGL | 美股大盘 — 广告 / cloud |
| METAON | META | 美股大盘 — 广告 / social |
| NFLXON | NFLX | 美股大盘 — 流媒体 |
| GOOGLBON | GOOG | 美股大盘 — GOOGL 同公司另一类股 |
| SPYON | SPY | 美股 S&P 500 大盘 ETF |

**spec 状态**：`OBS-S04` 锁定 `execution_eligible=False`——这 10 个 xStock 是观测可达、执行不可达（结构上不允许下单），属 Serenity 研究观测窗口的 fact-base，而非实际交易载体。

### 1.2 上游报告 §5 的诊断（verbatim 引用）

> **🔴 5.1 — 10 个 xStock 不是产业链，是 S&P 名人堂。** 可用名单：AAPL、TSLA、NVDA、MSFT、AMZN、GOOGL、META、NFLX、GOOG、SPY——**全是美股大盘科技/消费**。产业链上游真正的名字（ASML、LRCX、安森美、村田、Foxconn、SK海力士、尼得科）**一个都没有**。
>
> **🔴 5.2 — xStock 流动性/跟踪保真度未验证。** 若价差 >0.5% 或日量 <$50k，xStock 现货对任何有意义的仓位都不是可行载体。

### 1.3 结构性错位

- Serenity 的论点结构是 **"产业链 N 级供应商被市场低估"**（产业链中游/上游零部件、设备、材料），其 alpha 来自工业供需的一手信号（订单、库存、产能利用率），而非市值最高的几家品牌商。
- 当前 10 个标的全是终端品牌或 ETF，**论点空间与标的空间正交**。即便 Serenity 研究做到信号极致，也无法用现有标的表达「ASML 光刻机产能受限 → 7nm 制程供给收紧」「村田 MLCC 库存周期触底」之类核心命题。
- 报告 §8 D1 owner 原问：「用这 10 个 xStock，你**真正在意的产业链论点里，哪 1–3 个是可表达的**？」当前答案：极少；以 NVDA/AAPL 表达「AI 算力 / 消费电子终端」尚可,但**任何 1 级以下供应链命题都无法表达**。

---

## 2. Bitget 调研方向（RWA 系列）

Owner 提到见过 `rSpaceX`（SpaceX 私募股权代币化）等 RWA（Real-World Asset）类标的。调研问题（**调研，未承诺纳入**）：

### 2.1 调研问题列表

1. Bitget 当前上市的 RWA 系列完整名单是什么？（不限 xStock ON 命名，扩展到 r-前缀 / 其他后缀的 tokenized equity / private equity）
2. 其中是否有**产业链 mid-stream / up-stream** 的代币化标的（半导体设备厂、关键材料厂、关键零部件厂、能源储能上游、农产品 ETF）？
3. 各标的的**追踪保真度**（参考原资产 NAV 偏差）与**实际链上流动性**（24h volume、taker 价差、cross-exchange 套利窗口）。
4. 监管风险：RWA 标的的发行方（如 Backed Finance / Ondo / Maple 类）合规状态、跨境清算是否依赖单一受托人。
5. tokenized 标的的**分红 / 公司行为**（split, dividend, voting）处理路径——影响长持期间的 corporate action 风险。

### 2.2 候选示例（占位，待真实调研填入）

> ⚠️ 以下名称仅为 **research stub**，不代表 Bitget 实际上市或推荐。任何条目要真正进入 Aletheia universe，需独立 plan + spec + owner 批准。

- `rSpaceX`（owner observed）：私营航天供应链上游（火箭/卫星）的代理标的——理论上能表达「私营航天 vs 传统航空航天产业链分流」论点；保真度 + 流动性待审。
- `rOpenAI` / `rStripe` 等私募股权代币化：**与 Serenity 产业链供需逻辑契合度低**（赋能层而非供应链节点），优先级低。
- RWA 商品类（铜、白银、可再生能源、农产品）：若 Bitget 上市,**契合度高**——能表达「上游金属供应紧缺 / 农产品周期」此类纯供需论点。

### 2.3 调研产出形式

不是 PR。调研结果写入本文档 §2.2 的候选示例表，标注 `保真度`、`24h vol`、`spread`、`产业链节点`、`论点匹配度`，再由 owner 审。

---

## 3. OKX 调研方向（产业链相关标的）

OKX 在 Aletheia 已有 `CCXTProvider(exchange="okx")` 基础设施可复用,无需新加交易所。

### 3.1 调研问题列表

1. OKX 上市的**美股代币化 / 商品代币化**列表（与 Bitget xStock ON 系列对照,有无差异化标的）。
2. OKX 是否有**亚洲产业链原生**标的（如日韩半导体供应链、东南亚 EMS / Foxconn 系、台积电 ADR、SK 海力士 ADR 的代币化版本）——这是 Bitget 当前缺失的方向。
3. OKX 永续合约市场是否有产业链相关 perp（多数为加密原生,**非 Serenity 范畴**)。优先级：低 —— Serenity 是长持现货逻辑,perp 资金费会侵蚀长期收益（参考 BTC perp 年化 funding ~11%）。
4. OKX 的 tokenized equity 监管套利风险（vs Bitget 同类产品）：发行人、托管人、清算路径。

### 3.2 候选示例（占位）

> ⚠️ 同 §2.2，stub-only。

- ASML / LRCX / TSMC ADR 代币化：**契合度极高**——直接表达光刻 / 刻蚀 / 代工产业链节点。
- 工业用稀有金属 / 锂 / 铜 / 钴 RWA：表达 EV 供应链 / 半导体供应链上游材料论点。
- 日本 MLCC / 电容 / 电感厂代币化（村田 / TDK / 太阳诱电）：表达被动元件周期论点（Serenity 经典思路之一）。

---

## 4. 候选-论点对齐 Matrix（Empty Template）

Owner 最终决定纳入哪些标的时,需要以下视角统一审视。**矩阵当前为空模板,Phase 6 不填**——填它的人是后续做真实调研的 owner / Phase 6.x 的执行者。

| 候选标的 | 交易所 | 产业链节点 | Serenity 论点（一句话） | 24h Vol | Spread | 保真度（vs 原资产） | corporate action 风险 | 监管 / 托管风险 | 论点匹配 (1-5) |
|---|---|---|---|---|---|---|---|---|---|
| _（待调研填入）_ | | | | | | | | | |

### 4.1 论点匹配评分约束

任何候选标的进入「待纳入」清单的最低门槛（建议,**不强制成 spec**）：

- **论点匹配 ≥4**：候选标的能直接表达一个具体的、可证伪的 Serenity 论点（非 「AI 大趋势」之流的笼统论调）。
- **流动性门**：24h Vol ≥ $50k 且 Spread ≤ 0.5%（报告 §5.2 verbatim）。低于此线的标的**不进入** universe，无论论点多漂亮——执行不到位等于零。
- **保真度门**：tokenized 标的与原资产价格偏差 ≤ 1%（24h 平均）。偏差大于此线意味着代币本身是另一个市场,Serenity 论点的 reasoning 失效。

---

## 5. 论点缺口清单（Serenity 当前真正想表达却**无标的**的核心论点）

引自报告 §5.1 verbatim 名单 + 一线 Serenity 经典论点：

1. **光刻设备产能** — ASML 独家 EUV / DUV 供给链；现状：**无标的**。
2. **刻蚀 / 镀膜设备** — LRCX, Applied Materials, Tokyo Electron；现状：**无标的**。
3. **电源管理 IC / 离散器件** — 安森美, Infineon, ST Micro；现状：**无标的**。
4. **被动元件周期** — 村田, TDK, Yageo, 三星电机；现状：**无标的**。
5. **EMS / 组装产能** — Foxconn (鸿海), Pegatron, Quanta；现状：**无标的**。
6. **HBM / DRAM 供应** — SK 海力士, Micron, Samsung；现状：**有 NVDA 间接代理，缺直接表达**。
7. **精密电机** — 尼得科, 日本电产；现状：**无标的**。
8. **晶圆代工产能** — TSMC, GlobalFoundries, UMC；现状：**无标的**。
9. **基础材料 / 工业气体 / 化学品** — Linde, Air Liquide, Shin-Etsu；现状：**无标的**。
10. **EV 供应链上游**（锂 / 钴 / 镍 / 阴极材料）；现状：**无标的**（NVDA / TSLA 不算）。

每一项都是 Serenity 论点可能产出的**真实方向**。Phase 6 调研的核心问题是：上述 10 类中,在 Bitget / OKX / 任何已接入交易所有**几类**有可触达载体？

---

## 6. 本文档**不做**什么（护栏）

> 此文档 = 纯 markdown 路标。**任何代码 / schema / config / spec 改动都需要独立 PR，并显式声明依据。**

明确不做的事（任意一条破例都必须先单开新 plan + owner 批准）：

- ❌ 不向 `aletheia/data/symbols.py:_XSTOCK_BASE_TO_TICKER` 添加新 base address。
- ❌ 不向 `universe_registry` seed 添加新 symbol。
- ❌ 不翻转任何标的的 `execution_eligible=False` → `True`。
- ❌ 不动 spec `OBS-S04`（xStock 硬编码 execution_eligible=False）——该 spec 是 Round 1 后保护层,Phase 6 显式声明**绝不触碰**（报告 §7.1 red-team 第一条）。
- ❌ 不开启 Serenity live 交易（D4 锁定 paper-first,即便 Phase 5 已结构预埋）。
- ❌ 不动 risk engine / execution gateway / live unlock 路由（trust 边界）。
- ❌ 不动 frontend Settings 页面（无 config↔UI 联动需要）。
- ❌ 不动任何 SDD spec 文件（本 phase 无 claim 增减）。

如果哪天要破例（譬如 Bitget 新上 `rASML` 且 owner 决定纳入）,正确流程：

1. 单开 `docs/plans/serenity_rwa_phase_<n>.md`，列具体标的、流动性数据、论点匹配评分、执行 mode。
2. 起 PR，owner 显式 review（参考 DECISIONS.md 既有 D-decision 记录格式）。
3. SDD spec 同步：可能要补一个 `spec_serenity_universe.py`，把「哪些标的属于 Serenity 流派」编码成结构断言（避免 misclassification）。
4. 若涉及非 paper mode,触发风控边界审查 —— 走 `docs/governance/policies/trust_and_change_control.md` §6.1 受控模块流程。

---

## 7. 后续触发条件（when to next-act on this doc）

本 roadmap 进入"可执行"状态的触发条件（任一即可,需 owner 显式确认）：

- **A) Bitget / OKX 新上一个标的**,其属于 §5 缺口清单的具体行业节点（如 `rASML`, `rTSMC`, `r铜矿`）。
- **B) Owner 主动调研后**给出 1–3 个候选标的清单（含流动性证据）,要求纳入。
- **C) Serenity 研究 worker 反复产出指向某产业链节点的 high-tier evidence，但无标的可下单**——这是隐性 cost-of-no-instrument 累积到owner 决定补齐的信号。

触发后的执行单位：单独 plan + 单独 PR + 单独 SDD spec,**不在 Phase 6 范围内**。

---

## 8. 与父计划的关系

- **Phase 1（D2）**:证据生成/评测分离 → 已实施（commit b6f7750 `feat(serenity): D2 — separate evidence generator from authoritative tier evaluator`）。
- **Phase 2**:signal_source 决策级归因 → 已实施（PR #24 merged）。
- **Phase 3 + D5**:promote → PI 评估队列 → 已实施（PR #25 merged）。
- **Phase 4**:trade_school 执行级长持机制 → 已实施（PR #27 待 review/merge）。
- **Phase 5 + D4**:live-readiness 结构预埋（不开 live）→ 已实施（PR #28 待 review/merge）。
- **Phase 6（本文档）**:标的扩展 roadmap → **纯文档,无代码**。
- Serenity 执行路径 Option A 至此**结构完整**。剩余空间是**真实标的纳入**和**真实研究产出**——两者都不在工程范畴,需要 owner 真实决策驱动。

---

## 9. References

- 父计划：[`docs/plans/trading-pipeline/serenity_execution_path.md`](serenity_execution_path.md) §7
- 哲学源头：[`docs/research/audits/SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS_2026-06-27.md`](../../research/audits/SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS_2026-06-27.md) §5, §8
- xStock 标的字典源：`aletheia/data/symbols.py:_XSTOCK_BASE_TO_TICKER` (line 116–127)
- OBS-S04 spec（xStock 不可执行硬编码）：`tests/specs/spec_subsystem_observers.py`
- 信任边界审查：`docs/governance/policies/trust_and_change_control.md` §6.1
- D-decision 记录：`docs/_ai/decisions/DECISIONS.md`
- Owner 拍板汇总：memory `aletheia-serenity-integration-decisions`
