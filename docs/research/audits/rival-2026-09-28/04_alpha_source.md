# 04 · alpha 来源

> 本域是全报告的核心。口径：信号从哪来（函数 path:line）、参数是拍的还是搜出来的、**回测与实盘是否同一套代码**、有无 PnL 归因/baseline/反事实/OPE/样本外、标的池是算法还是手工。
> 判定原则：**代码静态存在 ≠ 默认启用 ≠ 有运行或回测证据**。

## 横向总表

| 维度 | Aletheia | R20/AstraQuant | OpenAlice | Vibe-Trading | nofx | tickflow |
|---|---|---|---|---|---|---|
| 信号性质 | 六机制 PCHIP 单调曲线打分 + SMC/ICT 结构 | 8 项启发式 composite score + LLM | **无信号层**（只有指标库+公式引擎） | LLM 现场生成策略代码 | LLM 自由裁量 + 三层确定性约束 | 人工规则（技术指标+涨跌停状态机） |
| 决策主体 | 确定性 Ranker → Operator → Risk | LLM + 5 拦截器 | **LLM 提案 + 人类审批** | LLM ReAct agent | LLM，默认方向权交外部信号商 | 用户选策略+参数 |
| 有回测引擎 | **❌ 无** | ✅ 有（不同源） | 退化版 185 行（不同源） | ✅ 有（跑 LLM 生成码） | Python 回放（手写复刻 Go） | ✅ 有（**唯一同源**） |
| **回测=实盘同源** | — | **❌** | **❌**（架构禁止） | **❌** | **❌**（两套实现人工同步） | **✅** |
| 参数搜索 | **❌ 零命中** | ❌（拍的，注释自认"业务语义非可调参数"） | ❌ 零命中 | ❌ 零命中（LLM 拍字面量） | **✅ optuna TPE + train/holdout** | **✅ 网格 2000 上限 + 独立 walk-forward** |
| 样本外验证 | 前瞻账本（非回测 OOS） | ❌ | ❌ | 有 `evaluation_start_date` + purged CV 库，**但需显式开启** | holdout 一次性脚本 | `walkforward.py` IS/OOS + degradation 度量 |
| **反事实 / OPE** | **✅ 唯一** | ❌ | ❌ | ❌ | ❌ | ❌ |
| PnL 归因 | 三项成本分离（fee/spread+slip/funding） | 真实账单（交易所实收） | ❌ | `quantlib/attribution.py` | ❌ | Newey-West HAC t 检验（因子 IC） |
| 标的池 | 算法发现（455 members / 1170 registry） | 10 个手工清单 | **无 universe 概念** | 用户/LLM 给 basket | 三模式：手工/交易所榜/第三方信号商 | 算法（SDK universe API） |
| 统计显著性工具 | 独立单元定义 + 按币聚类 | ❌ | ❌ | Monte Carlo 置换 + Bootstrap CI（可选） | 搜索目标函数 `ret - 0.5*max_dd` | Newey-West HAC t + BH-FDR 已接线；**DSR/PSR 写了但零调用** |

## 最重要的发现：六家里五家的"回测通过"不构成对实盘的任何保证

这条推翻了"Aletheia 缺回测引擎所以落后"的直觉。逐个坐实：

### Vibe-Trading：回测跑的是 LLM 当场写的一次性代码

证据链完整：

1. skill 指导 LLM 写策略文件（`agent/src/skills/strategy-generate/SKILL.md` 工作流）：
   ```
   3. Strategy coding: write `code/signal_engine.py`
   5. Run backtest: call the `backtest` tool
   7. Iterative fixing: if results are poor, modify with edit_file → run backtest → re-evaluate
   ```
   **第 7 步是对回测结果的直接过拟合循环。**

2. 落盘路径：`agent/backtest/runner.py:1249` `signal_path = run_dir / "code" / "signal_engine.py"`

3. 执行点：`agent/backtest/runner.py:215-230`
   ```python
   def _load_module_from_file(file_path, module_name):
       _validate_signal_engine_source(file_path)   # AST 白名单门禁
       spec = importlib.util.spec_from_file_location(module_name, file_path)
       module = importlib.util.module_from_spec(spec)
       spec.loader.exec_module(module)             # ← 执行 LLM 写的代码
   ```
   沙箱是 **AST 白名单 + 禁用符号**（`_reject_forbidden_open`:552、`_reject_forbidden_pathlib_write`:583 等），**不是进程/容器隔离**。

4. 实盘是另一条链路：`agent/src/trading/service.py:685` `place_order(...)` → connector SDK；`:711-740` 走 `OrderIntent` + mandate gate。**实盘不执行任何 `signal_engine.py`。**

5. 跨层引用实测仅 **2 处**，且都是数据 loader：
   - `agent/src/live/enforcement.py:703` `from backtest.loaders.base import NoAvailableSourceError`
   - `:704` `from backtest.loaders.registry import resolve_loader`

**结论**：回测验证的是"LLM 刚写的这段代码在历史数据上表现如何"，实盘决策由另一套链路做出。**"回测通过"与实盘无因果关系。**

### R20：回测测 SMA 交叉，实盘用 composite score —— 且 README 声称相反

本次逐行复核成立，**并且比 09-24 的结论更严重**。

回测入场信号（`scripts/backtest_engine.py:180-194`，本人独立读取原文）：

```python
closes = [float(c["close"]) for c in candle_series]
for idx in range(15, len(candle_series)):
    ma_short = sum(closes[idx - 5 : idx]) / 5      # ← SMA(5)
    ma_long  = sum(closes[idx - 15 : idx]) / 15    # ← SMA(15)
    vol = (max(closes[idx-5:idx]) - min(closes[idx-5:idx])) / (c or 1)
    conf = 0.82 if abs(ma_short - ma_long) / c > 0.004 else 0.65   # ← 硬编码置信度
    rr   = 2.2 if vol > 0.008 else 1.5                             # ← 硬编码盈亏比
    if ma_short > ma_long and c > ma_short:
        signal_map[ts] = {"action": "BUY", "confidence": conf, "rr": rr,
                          "atr": max(c * 0.012, 0.0001)}           # ← 硬编码 ATR
```

import 图双向确认（不靠命名相似）：

| 检查 | 结果 |
|---|---|
| backtest 是否 import `factors/scoring`（composite score 所在） | **零命中** |
| backtest 是否 import `risk_constants` / `instrument_pool` | **零命中** |
| backtest 的真实 import（`:35-39`） | 只有 `scripts.backtest.lifecycle` 与 `scripts.backtest.metrics` |
| `score_composite_alpha` 的消费方 | `scripts/factor_library.py:33,268-270`，**不是 backtest** |
| 谁调用 backtest | 只有自身（`:335`）。**无生产路径调用** |

我另行独立复核了 `scripts/backtest/lifecycle.py` 的共享情况：`grep -rn "backtest.lifecycle"` 与 `grep -rn "evaluate_position_exit"` 在非测试代码中**只命中 `backtest_engine.py:35` 一处** —— 即该模块只服务回测，不被实盘 import。

**本次新发现的文档-代码矛盾**（`README.md:215`，逐字）：

> | 9 | 🧪 **Backtest & Sandboxing** | Multi-instrument portfolio backtesting and sandbox replays **executing the exact same Python risk and sizing code paths as live trading** |

代码事实：回测**不 import** `risk_constants`、`instrument_pool`、`factors/scoring` 任何一项。准确表述应是"回测与实盘同源地覆盖了持仓退出与结算（`lifecycle.py`），不同源地覆盖了入场信号、风险参数与仓位大小"。**README 用 `exact same ... risk and sizing code paths` 描述后者，与代码不符。**

**这条的实际后果**：§A.3 的两次高频参数改动，即便有人去跑回测，**回测也不会跑那套被改的参数**（因为 backtest 不 import `risk_constants`）。所以"没有回测证据"不是"忘记跑"，而是**结构上跑不出对应证据**。

`score_composite_alpha`（`scripts/factors/scoring.py`）是 8 项启发式预测分，`:73` `score = 0.0` 起算，8 项编号注释在 `:75,83,90,96,102,107,125,137`。jerk 阈值 `JERK_DAMPEN_THRESHOLD = 1.8`（`:62`）、乘子 `score *= 0.6`（`:149`）；"业务语义，不是可调参数"的原话在 `scripts/calculus/primitives.py:24`：

> "## ⚠️ 这些阈值是**业务语义**，不是可调参数 … 全部原样保留。按用户约束，**不抽常量、不改数值**"

即：**连"不抽常量"这个决定都是用户约束，不是工程判断。**

R20 的 `jerk`/`velocity`/`acceleration` 数学实质（`calculate.py:251-281`）：velocity = Δ EMA₃(log P)、acceleration = Δ²、jerk = Δ³，除以 20 根 returns 滚动标准差，clip 到 [-3,3]。`energy_integral` 等价于 12 根 bar 的总 log return（望远镜求和）。

### nofx：Python 回放脚本手写复刻 Go 生产逻辑

`scripts/optimize/simulate.py:5` 自述 **"Mirrors the live semantics of trader/auto_trader_throttle.go"**，并标注"matching the 2026-07-24 fix that converted the live thresholds from margin basis to price basis"。

**两套独立实现，靠开发者手动同步，无自动化 drift 检测。** Go 侧改了节流逻辑而 Python 忘了同步，参数搜索结果就失真。

更关键的限定写在 `simulate.py:14-16`：

> "Known limitation: decisions are replayed as recorded. Parameters that would have changed WHAT the AI decided...are out of scope; parameters that gate/size/exit those decisions are faithfully simulated."

即：**这个回测只验证节流/止损止盈参数，不验证 AI 的开仓判断本身。**

### OpenAlice：架构上禁止回测依赖交易层

`src/domain/analysis/simulate.ts` **185 行**，全部 import 只有 `import type { BarService, BarSourceRef, BarSourceKind }`（`:21`）—— 零券商依赖、无 Order/Contract 概念、无撮合、无滑点/手续费模型、仅支持单次入场+单次出场。

实盘是 `UnifiedTradingAccount.ts`（1,262 行）→ `brokers/*Broker.ts` → 券商 SDK。

`simulate.ts:20` 注释明说 `Lives in domain/analysis (analysis → market-data dependency direction)` —— **分析层在架构上被禁止依赖交易层，所以"回测复现实盘"在依赖方向上不可能发生**。这是有意分层，不是遗漏。

它的定位是"回答 AI 的一个回顾性问题"（`src/tool/simulate.ts:19`："if I'd bought X on date D, would <exit rule> have saved me?"），不是策略验证。

### tickflow：唯一的真同源，值得作为设计范本

涨停/连板信号定义在 `backend/app/price_limits.py`（纯函数）。该模块被**两条独立消费链同时 import**：

- 回测矩阵层：`backend/app/backtest/matrix.py:31`，用于 `_limit_lock_matrices`（`matrix.py:2155`）构建 `limit_up_locked`/`limit_down_locked`
- 看板指标管线：`backend/app/indicators/pipeline.py:35`，生成 `signal_limit_up`/`consecutive_limit_ups`（`pipeline.py:906-957`），经 `api/screener.py:807-838` 的 `/limit-ladder` 输出给 `frontend/src/pages/LimitUpLadder.tsx:1540`

更进一步：26 个 builtin 策略全部用 `EXECUTION_BACKEND = "matrix_native"`（如 `near_limit_up.py:55`），通过 `matrix_feature(market, "change_pct")`（`:71`）调用 `matrix.py:3790` 定义的框架函数 —— **策略信号计算与回测引擎共享同一 `MarketDataMatrix` 数据结构和特征库**。composite 策略强制子策略全部 `matrix_native`，否则 fail-closed 拒绝（`strategy.py:766,785-788`）。

**这是"单一真相源 + 强制同源"的正确实现方式。**

## Aletheia 的 alpha 装置：六家结构上做不到的事

### 它有什么

**方向性假设层**（`core/aletheia/universe/continuous_scoring.py`，1,672 行）：六个 mechanism 打分函数，用 PCHIP 单调曲线（`shape_preserving_curve`）把 RS/RSI/return/funding/OI/taker-ratio 映射成 0-100：

```
:449   _score_momentum          （延续假设）
:543   _score_mean_reversion    （反转假设，方向与 momentum 相反）
:627   _score_volatility
:819   _score_derivatives       （持仓强制流假设）
:924   _score_event             （催化剂假设）
:1013  _score_short_bias
:1149  _score_tradability       （无方向，只答"能不能交易"）
```

这是**四组互斥且方向相反的经济假说**，不是单一动量因子。对比 R20 的 8 项加权求和（全部同向叠加）。

**成本完整的前瞻结果账本**（`migrations.py:337` 建表）：`universe_forward_outcomes` + `filter_candidate_outcomes`，对**每个候选**（不论是否开仓）按固定 horizon 记录结果。

**拒绝把未知当零**（`core/aletheia/universe/outcome_economics.py:40-45`，逐字实测）：

> "Net is deliberately unavailable when the direction is unknown, a derivative has no point-in-time funding rate, or the settlement price is too far from the requested horizon. **Unknown economic inputs must not become optimistic zeros.**"

实现（`:95-102`）：

```python
net: float | None = None
if price_quality_ok and directional is not None and funding_pct is not None:
    net = directional - fee_pct - spread_slippage_pct - funding_pct
```

**独立单元定义**（`core/aletheia/universe/economic_utility.py:21`）：

```python
INDEPENDENT_UNIT_VERSION = "scanner-t0-cross-section-nonoverlap-4h/v1"
```

即"4 小时不重叠的 T0 横截面"才算一个独立样本 —— **六家中无任何一家定义了独立样本单位**。

**反事实/OPE**（`extensions/counterfactual/`，3,898 行）：`propensity.py` 466 行、`trades.py` 950 行、`estimators.py`、`calibration.py`、`family_scorecard.py`、`filter_replay.py`。

**门禁保护**：`tests/audit/test_alpha_cohort_reachability.py:338` 断言每个 cohort anchor 必须有生产可达的 writer，含两个负向自检（`:367`、`:385`）。**这是六家中唯一"度量装置自身被门禁保护"的样本。**

### 它没有什么

- **零回测引擎**：`grep` 全仓无 backtest engine；只有 `analytics/momentum_geometry_replay.py`、`analytics/position_replay.py`、`extensions/counterfactual/filter_replay.py`
- **零参数搜索**：`walk_forward|walkforward|grid_search|param_search|optuna` 在 `core`/`extensions`/`scripts` **零命中**

### 它量出了什么（09-24 实测，本次未重跑）

714 笔成本完整样本：方向毛收益 **+0.0046%**，成本地板 0.3024%/往返，净 **−0.2972%**，胜率 29.7%。

按币聚类（n=56）：方向毛收益 t = **−0.10** —— **与 0 不可区分**。这是"策略没边缘"，不是"成本吃掉边缘"。毛边缘离成本地板差 65 倍。

**但样本只有 3 小时 20 分 / 约 1 个独立单元**，这个结论只能证明"机器能转"，不能证明"策略长期无效"。

**关键含义**：Aletheia 是六家中**唯一能得出"我的信号没有边缘"这个结论的系统**。R20/nofx/Vibe/OpenAlice/tickflow 无论跑多久都产不出这个结论——它们只记录已执行的单，没有对照组。

## 参数来源：两家做了真搜索，四家是拍的

### nofx —— 唯一有贝叶斯搜索 + holdout 的

`scripts/optimize/`（Python，656 行）：
- `extract.py`（180 行）：从生产 `data/data.db` 的 `decision_records` 表提取真实历史决策 + K 线构造回放数据集
- `simulate.py`（339 行）：回放录制的历史决策
- `search.py`（137 行）：**optuna TPE 贝叶斯搜索，训练集用前 70%，报告未触碰的 holdout window**（`:2-3`），搜索 14 个参数（`:20-36`）

**搜索结果落地到生产常量且可追溯**（`trader/auto_trader_throttle.go:13-20` 注释）：

> "validated by decision replay (2026-07-26, 4154 cycles, 3-fold robustness): gates beat no-gates by 34 pts and the old rigid 4h/8h by 16 pts of worst-fold score"

**这是六家中唯一把"参数怎么来的"写进生产代码注释的样本。** 值得 Aletheia 直接借这个习惯。

限制：搜索目标函数只是 `ret_pct - 0.5*max_dd_pct`（`search.py:39-44`），不是归因分析；holdout 是一次性脚本运行，不在 CI。

### tickflow —— 网格搜索 + 独立的 walk-forward 服务

- `backend/app/backtest/optimizer.py`（443 行）：笛卡尔积网格搜索，`GRID_MAX_COMBINATIONS = 2000` 硬上限（`:22`），`ranked = sorted(results, ...)`（`:360`）。**单独用等于全样本选最优 = 教科书式过拟合。**
- `backend/app/backtest/walkforward.py`（391 行）：**独立存在且被前端实际调用**。`generate_folds()`（`:29`）切训练/测试窗口，每折在训练区间调 `StrategyOptimizer.optimize()`，再用最优参数在紧邻测试区间做样本外回测；`aggregate_oos()`（`:71`）显式计算 **`degradation`**（IS 目标均值 − OOS 目标均值，`:108`，正值=过拟合信号）。
- 前端有 `StrategyWalkForward.tsx` 与 `RobustnessValidation.tsx`，`frontend/src/lib/walkforwardTask.ts` 调用对应 API —— **walk-forward 不是摆设**。

**但**：策略默认参数（如 `min_change: 7.0`、`limit_gap: 3.0`，`near_limit_up.py:22-47`）**来源未见任何统计搜索证据**，是作者手写经验值。系统**不强制用户走 OOS 路径**——`/optimize` 与 `/walkforward` 两个入口并存，优化器不会提示"你应该做 walk-forward 验证"。

### 其余四家

- **R20**：jerk 阈值 1.8 / 乘子 0.6 拍的，注释自认"业务语义，不是可调参数"
- **Vibe-Trading**：`optuna`/`grid_search`/`train_test_split` **零命中**；参数由 LLM 基于领域知识拍字面量
- **OpenAlice**：退出规则参数由调用方传入；`optuna|grid.?search` 零命中
- **Aletheia**：零参数搜索代码。PCHIP 曲线的控制点来自何处，本次未追溯（**未验证项**）

## 三个特殊形态

### OpenAlice —— alpha 根本不在代码里

全仓搜索 `(class|function|const|interface|type) +\w*(Strategy|Signal|Alpha|Indicator)\w*`，命中全是误报：`IndicatorCalculator`（MACD/EMA 计算器）、`signalValue = EMA(macdHistory, signalPeriod)`（MACD signal line）、`AbortSignal`、`signalProcessGroup`（进程信号）、`isAlpha = (ch) => /[a-zA-Z_]/`（字母表判断）、`MIN_SERVER_VER_OPEN_ORDER_AD_STRATEGY`（IBKR 协议常量）。

**没有任何 Strategy 类、Signal 生成器、alpha 模型。**

它的决策输出是 **"给人类审批的待执行操作提案（staged proposal）"**，走 Trading-as-Git 三阶段：

```
[1] LLM 分析（src/tool/trading.ts 暴露 tool 集）
[2] stagePlaceOrder / stageModifyOrder / ...（:672）
[3] tradingCommit（:779-785）→ 只提交 staging，不触达券商
[4] tradingPush（:798）→ 关键门禁：
      if (!allowAiTrading()) return { message: 'Push requires manual approval...' }  (:821-832)
[5] HTTP 人类审批：routes-trading.ts:503 POST /uta/:id/wallet/push
      (:502 注释 "the AI tool is hollowed out, only humans can push")
[6] UnifiedTradingAccount.push(expectedPendingHash) → :184 broker.placeOrder(...)
```

`allowAiTrading` 默认 **false**（`src/core/config.ts:249` `z.boolean().default(false)`），门禁在 tool execute 内部早返回，**不是 UI 隐藏** —— 即使 LLM 被 prompt injection 诱导调用 `tradingPush`，返回的也只是"请让用户去 Web UI 审批"。

**alpha 来源 = 用户自己的判断。系统提供数据/指标/分析/执行/审计，不提供信号。**

### nofx —— 默认把方向判断权交给第三方付费信号商

三层约束 LLM 输出：

**第一层** `kernel/engine_position.go:validateDecisionForMode`（26-146）：
- leverage 超限自动 clamp（`:61-64`）
- position size 硬底线 BTC/ETH ≥60 USDT、其他 ≥12 USDT（`:70-81`，编译期常量）
- position value 硬顶 `accountEquity * posRatio`，超 1% 容差直接拒绝（`:83-93`）
- **风险回报比强制 ≥3.0:1**，否则整条 decision 报错（`:139-142`）

**第二层** `trader/auto_trader_throttle.go`：最短持仓 90 分钟、噪音区 3 小时、同 symbol 重开冷却 4 小时；止损/止盈旁路阈值 −3%/+8%（`:20-21`）。**这些数值是 optuna 搜出来的**（见上）。

**第三层（本次新增）** `trader/auto_trader_signal_policy.go`：
- `applyVergexSignalPolicy`（136-205）：用外部信号板（Claw402/Vergex direction board）的 bias **强制过滤** AI 的开平仓 —— 已有仓位方向不匹配就强制平仓（`:144-167`）；新开仓方向与信号板不一致直接 blocked（`:193-202`）
- `validateProtectionPrices`（29-55）：`signalManaged=true` 时**强制 takeProfit 必须为 0**（`:51-53`），退出完全交给信号板状态机

**关键事实**：`store/strategy.go:1012` `GetDefaultStrategyConfig` 把 `SourceType` 设为 **`"vergex_signal"`** —— 默认配置下，候选币池 + 方向判断来自第三方付费信号商 `Claw402.ai`（`provider/vergex/client.go:22` `DefaultBaseURL = "https://claw402.ai"`），通过链上 ECDSA 签名请求（`:35`）。

**即：nofx 在默认模式下没有独立的 alpha 生成，是 signal-following + 风控包装。** 只有切到 ai500/oi_top 等模式才是 LLM 自由裁量。

### R20 v8.4.0 —— 商业化反向塑造参数（本次最重要的新信号）

v8.3.0 → HEAD（+55 commit）的商业化线：

| commit | 内容 |
|---|---|
| `c7424081`/`bfe67d56` | 改名 AstraQuant，仓库转 `0xethanq/astra-quant-agent` |
| `075909d6` | "eliminate hardcoded USD caps for pure dynamic margin sizing & **hardcode OKX broker tag**" |
| `1ceff7ef`/`54406653`/`d3f5d95e`/`f3c3c5a0`/`ee7fa14a` | broker code / 邀请链接 / tag 不得丢；平仓也带经纪商 tag；应急一键平仓补 tag |
| `6188055a`/`1da3ee63` | "upgrade preset strategies for **high trade frequency**, dynamic **aggressive** risk" |
| `7dbade51` | 默认 `risk_per_trade_usd` 提到 **250U**（随后 `075909d6` 改为纯比例 + 可选硬顶，0 = 无硬顶） |
| `5d4462f2` | strategy plaza 公开端点：**战绩遥测（`total_roi_pct`/`win_rate_pct`/`total_pnl_usdt`）+ 一键克隆参数**，`plaza_share.py:141-145` live-only fail-closed |

**两次预设改动的具体数字**（diff 原文，aggressive 套件）：

| 参数 | 原值 | `1da3ee63` | `6188055a` |
|---|---:|---:|---:|
| `ASTRA_MAX_LEVERAGE` | 7.0 | 8.0 | **9.9** |
| `ASTRA_MIN_LEVERAGE` | — | 5.0 | **6.0** |
| `ASTRA_MAX_MARGIN_EQUITY_RATIO` | 0.25 | 0.35 | **0.40** |
| `ASTRA_SINGLE_ASSET_EQUITY_RATIO` | 0.40 | 0.45 | **0.48** |
| `ASTRA_MAX_SINGLE_ASSET_MARGIN_USDT` | 800.0 | **0.0**（取消绝对硬顶） | — |
| `ASTRA_RISK_PER_TRADE_RATIO` | — | 0.03 | **0.045** |
| `ASTRA_MIN_ENTRY_CONFIDENCE` | — | 72.0 | **68.0** |
| `ASTRA_MAX_SAME_DIRECTION_POSITIONS` | — | 4 | **5** |
| `ASTRA_STOP_LOSS_ATR_MULT` | — | 2.2 | **2.0** |
| `ASTRA_TIME_STOP_HOURS` | — | 16.0 | **8.0** |
| `ASTRA_STOP_COOLDOWN_MINUTES` | 30 | 20 | **15** |
| `ASTRA_MAX_TOTAL_EXPOSURE_USDT` | 3000.0 | 5000.0 | **50000.0** |
| `ASTRA_SCALE_OUT_TRIGGER_ATR` | — | 1.50 | **1.00** |
| `ASTRA_DAILY_LOSS_EQUITY_RATIO` | 0.08 | 0.10 | — |

**方向单向且一致**：频率相关全部上调（持仓 16h→8h、冷静期 30→15 分钟、止盈触发 ATR 1.50→1.00、置信度门槛 72→68），敞口全部放大（杠杆 7→9.9、单笔保证金占比 0.25→0.40、同向仓 4→5、**总敞口 3000→50000 即 16.7 倍**）。同时 `risk_constants.py` 的 `effective_single_asset_margin_cap` 从 `min(绝对封顶, 余额30%)` 改为纯比例（`:178`），绝对封顶为 0 时不再有硬顶兜底。

**有无回测证据支持？逐项核查后：无。**

| 检查 | 结果 |
|---|---|
| 区间内 backtest 代码是否为配合新参数而改动 | `git log 34a0d29..HEAD -- scripts/backtest_engine.py scripts/backtest/ tests/backtest/` → **仅 2 条**：`c7424081`（纯改名）、`9e7b676b`（纯品牌） |
| 两次改动附带的测试改动 | **仅 `tests/llm/test_leverage_range_and_council.py` 1 行**，内容是把断言从 `(5.0, 8.0)` 重钉到 `(6.0, 9.9)` |
| 该测试断言"更赚钱"吗 | **不是**。只断言 `MIN <= MAX` 与数值等于新值，**零收益/胜率/回撤断言** |
| commit body 声称的验证 | "verified 1000+ trading suites, 826 LLM prompt suites, full Vue 3 build" —— **全是测试通过数，不是回测收益** |

即：**测试追踪了参数，而不是验证了参数。** 且这不是"忘记跑回测"，而是**结构上跑不出**——回测不 import `risk_constants`（见上节）。

**结构性激励（只列事实链，不指控动机）**：

1. `6188055a` commit body 逐字（本人独立复核）：**"Economics: substantially multiplies daily trading frequency, margin allocation, and notional turnover to consistently exceed token costs and maximize broker commissions."**
2. 频率、保证金占用、名义周转——**三个量恰好同时是返佣基数的三个乘数**，而该 commit 把三者同时上调。
3. broker tag 硬编码在 `scripts/okx_rest.py:49`，`:57-63` 忽略环境变量；`ee7fa14a` body：**"所有用户都看不见这个 code，避免有人特意改掉（改掉=返佣没了）"**。
4. 手续费由交易者承担（Aletheia 实测成本地板 0.3024%/往返，其中 fee 0.12%），返佣归 tag 持有方。

**能断言的**：这项改动只有"提高频率"的证据（diff 里的数字），没有"提高收益"的证据。在当前证据水平下它是**未经证实的参数调整**。
**不能断言的**：不指控动机、不断言返佣是主要收入、**也不断言策略亏钱**（无回测、无实盘收益数据，两个方向都不可断言）。

**对 Aletheia 的直接含义**：

1. 09-24 报告建议的"收窄标的池、硬排除无边缘品种、加置信度门禁"仍然成立（政策决定，逻辑独立）。
2. **R20 v8.4.0 的频率/风险参数不应被当作经过验证的基准参考。**
3. 可搬运的一条纪律：**参数改动若声称"更优"，必须绑定一个产出物**（回测曲线/实盘周期对比）。R20 缺的正是这个绑定点——它的测试只钉数值，所以"改数字"与"改对数字"在门禁上**不可区分**。

## 对 Aletheia 的可提取项

| # | 来源 | 提取什么 | 代价 |
|---|---|---|---|
| 1 | tickflow `price_limits.py` + `matrix_native` 协议 | **若补回测，必须强制同源**：信号定义在一个纯函数模块，回测与实盘都 import 它；composite 强制全子策略同 backend，否则 fail-closed | 规则变更要同时验证两条消费链 |
| 2 | nofx `auto_trader_throttle.go:13-20` | **参数出处写进生产代码注释**（方法、样本量、效果对比、日期）。Aletheia 的 PCHIP 控制点、ATR 倍数、置信度阈值都该有这一行 | 零成本；但要求真做过搜索 |
| 3 | tickflow `walkforward.py:71,108` | **`degradation` = IS − OOS 显式度量**。Aletheia 若做参数搜索，这是防过拟合的最小装置 | 需要先有可搜索的参数面 |
| 4 | nofx `engine_position.go:139-142` | **风险回报比硬门**（≥3:1，不满足整条拒绝）。Aletheia 有 ATR 失效距离，但未见 RR 比硬约束 | 可能拒掉大量候选；应先用前瞻账本量它值不值 |
| 5 | OpenAlice `allowAiTrading` 默认 false + 5 层纵深 | **AI 自主下单默认关闭且门禁在代码层**。Aletheia 的 live-unlock 已有对等物，但可借"keyless 账号隐式 readOnly"这一档 | 无 |

**最重要的一条不是提取，是保留**：`extensions/counterfactual/` + 前瞻账本 + `INDEPENDENT_UNIT_VERSION` 是六家都没有的稀缺资产。09-24 的结论不变——**迁移或简化掉它，就失去唯一能回答"alpha 是否存在"的器官**。

## 必须点明的幻象

1. **"有回测引擎"在五家中不代表能验证实盘**。Vibe 的回测跑 LLM 现场代码；R20 测 SMA 交叉；nofx 是手写复刻；OpenAlice 架构上禁止依赖交易层。只有 tickflow 同源。
2. **tickflow 的 DSR（Deflated Sharpe Ratio）写好但零调用**：`backend/app/backtest/stats_v2.py` 实现了 DSR/PSR 与 BH-FDR。**本人独立复核修正了子 Agent 的结论**：`bh_fdr_qvalues` **确有 1 处真实调用**（`backend/app/backtest/factor.py:364`，对因子 p 值做多重检验校正），`newey_west_t` 接入因子 IC 检验页（`api/factors.py:163`）；**只有 `deflated_sharpe_psr` 是零调用的死代码**。所以"统计工具全是摆设"的说法过强——准确的是：多重检验校正真的在用，**唯独最能防"选出来的最优参数其实是运气"的 DSR 没接线**，而这恰好是 `optimizer.py` 全样本选最优最需要的那一个。
3. **tickflow 的 `optimizer.py` 单独用就是全样本过拟合**，且系统不引导用户走 `/walkforward`。纪律完全依赖使用者的量化经验。
4. **Vibe-Trading 的 purged K-fold / CPCV / Monte Carlo 是可选层**：`agent/src/quantlib/crossvalidation.py` 实现了 López de Prado 的 `purged_kfold_splits`(:227)、`combinatorial_purged_splits`(:451)、`detect_boundary_leakage`(:524)，方法论水平很高；但 `agent/backtest/validation.py:8` 明确"called automatically ... **when config['validation'] is present**" —— 是否执行取决于 LLM 有没有在 config 里填这一项。
5. **OpenAlice 的 UI 演示稿有教科书级的 walk-forward/样本外/成本归因表述，代码中零实现**：`ui/src/demo/fixtures/chat-workflows.ts:173-177` 写着"Use chronological walk-forward windows. Keep a simple equal-weight baseline..."，但 `grep -iE "walk.?forward|optuna|out.?of.?sample"` 在 `src/`/`services/`/`packages/` 零实现命中。该文案末句自己承认 `This preview is the study brief, not a completed backtest.`（`:179`）。**看截图会误判能力。**
6. **nofx 的"The model proposes. The runtime disposes."读起来像 LLM 自由推理是核心 alpha，但默认配置下方向权在第三方信号商**（`store/strategy.go:1012`）。
7. **R20 的胜率与 Aletheia 的 29.7% 不可并列**：R20 统计"筛选后实际开的单"用真实成交价；Aletheia 统计"筛选前所有候选"用偏悲观 11–13 倍的滑点假设（模型 15.49 bps vs 实际点差 1.419 bps）。分母和尺子都不同，并列即误导。

## 本域未验证项

- Aletheia 的 PCHIP 曲线控制点、ATR 倍数、置信度阈值的**来源**（是拍的还是有依据）—— 本次未追溯，与 nofx 的可追溯注释形成对比，建议单独查。
- R20 v8.4.0 预设策略的具体参数数值变化（`6188055a`/`1da3ee63` 的 diff 细节）—— 见 R20 delta 报告。
- 六家的**真实收益**。全部无从核查：R20/AstraQuant 官网未公布任何胜率数字；nofx 的 4154 cycles 是回放非实盘业绩；其余无运行记录。
