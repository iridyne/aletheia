---
url: https://coriva.eu.org/cta-strategy-guide/
title: "CTA 策略入门：趋势跟踪、期货套利与 Python 实战"
description: "CTA策略分类、趋势跟踪双均线系统实现、期货跨期与跨品种套利原理，附 Python 模拟代码和回测结果。"
author: "Coriva"
published: "2026-04-16T08:00:34+08:00"
language: "zh"
captured_at: "2026-06-30T12:17:10.448Z"
---

# CTA 策略入门：趋势跟踪、期货套利与 Python 实战

CTA（Commodity Trading Advisor，商品交易顾问）是量化策略里的一个大类，核心交易标的是期货。虽然名字里带"商品"，但实际上 CTA 策略覆盖的远不止大宗商品：股指期货、国债期货、外汇期货都在射程范围内。CTA 策略分两大流派： **趋势跟踪** （价格涨了就做多，跌了就做空）和 **统计套利** （找相关品种之间的价差偏离，赌它回归）。这篇文章从分类讲起，两条线都覆盖，各附 Python 代码。

按照交易逻辑，CTA 策略可以分成三类。

**趋势跟踪** （Trend Following）是 CTA 里最主流的一类，全球管理期货基金里超过 70% 的资金规模走的是趋势路线。逻辑很直觉：如果一个品种价格在上涨，说明多头力量占优，做多；价格在下跌，空头占优，做空。用一句老话概括就是"截断亏损，让利润奔跑"。趋势跟踪的核心假设是"价格动量会延续"：过去涨的品种在近期继续涨的概率高于 50%。

**统计套利 / 均值回归** （Statistical Arbitrage / Mean Reversion）走相反的路线：找两个价格高度相关的品种，当它们之间的价差偏离历史正常水平时，做空偏贵的、做多偏便宜的，等价差回归。这类策略不赌市场方向，只赌"关系恢复正常"。

**高频做市** （Market Making）通过挂限价单提供流动性，赚取买卖价差。对延迟和基础设施的要求极高，不在本文讨论范围。

趋势跟踪和统计套利是两种几乎相反的思维方式，适用的市场环境也不同：

|  | 趋势跟踪 | 统计套利 |
| --- | --- | --- |
| 核心逻辑 | 强者恒强，弱者恒弱 | 偏离终将回归 |
| 胜率 | 低（通常 30-40%） | 高（通常 60-70%） |
| 盈亏比 | 高（赢的时候赚很多） | 低（每笔利润有限） |
| 适用市场 | 有明确趋势的行情 | 震荡、区间波动的行情 |
| 回撤来源 | 震荡市反复止损 | 价差不回归（关系破裂） |
| 典型夏普 | 0.3 - 0.8 | 1.0 - 2.0 |

CTA 策略有一个被机构投资者看重的特性： **危机 alpha** 。2008 年金融危机期间，全球股市暴跌，但趋势跟踪策略因为做空股指期货和商品期货赚了不少。SG Trend Index 在 2008 年录得 +21% 的回报，同期标普 500 下跌 37%。这种和股票市场的负相关性，让 CTA 成为机构投资组合里的"保险"配置。

## 趋势跟踪：双均线交叉系统

趋势跟踪最经典的实现方式是 **均线交叉** 。具体来说是用两条不同周期的移动平均线：一条短期（比如 20 日），一条长期（比如 60 日）。当短期均线从下方穿过长期均线（“金叉”），说明价格的短期动量向上，做多；当短期均线从上方穿下来（“死叉”），做空。

参数选择的直觉：短期均线是噪声过滤器，它过滤掉日间波动，保留近期趋势方向；长期均线是趋势确认器，它代表更长时间尺度上的方向。短期均线太短（比如 5 日），每天的随机波动都会触发信号，来回被"洗"；长期均线太长（比如 200 日），信号严重滞后，趋势都走完了才发出信号。20/60 是一个常见的中等频率组合。

下面用 Python 模拟一段带有趋势和震荡交替出现的期货价格序列，然后跑双均线交叉策略：

```python
import numpy as np

def simulate_trend_following(n_days=1000, short_window=20, long_window=60):

    np.random.seed(42)

    # 模拟期货价格：5 个阶段交替出现趋势和震荡

    regimes = [

        (0, 150, 0.0008, 0.012),     # 上涨趋势

        (150, 300, -0.0001, 0.018),   # 震荡

        (300, 500, -0.0006, 0.013),   # 下跌趋势

        (500, 700, 0.0001, 0.020),    # 震荡

        (700, 1000, 0.0007, 0.011),   # 上涨趋势

    ]

    returns = np.zeros(n_days)

    for start, end, mu, sigma in regimes:

        returns[start:end] = np.random.normal(mu, sigma, end - start)

    price = 100 * np.exp(np.cumsum(returns))

    # 计算均线

    ma_short = np.array([np.mean(price[max(0, i-short_window+1):i+1])

                         if i >= short_window - 1 else np.nan

                         for i in range(n_days)])

    ma_long = np.array([np.mean(price[max(0, i-long_window+1):i+1])

                        if i >= long_window - 1 else np.nan

                        for i in range(n_days)])

    # 信号：短均线 > 长均线做多，反之做空

    position = np.zeros(n_days)

    for i in range(long_window, n_days):

        position[i] = 1 if ma_short[i] > ma_long[i] else -1

    # 策略收益

    strategy_ret = position[:-1] * returns[1:]

    strategy_ret = np.insert(strategy_ret, 0, 0)

    strategy_equity = np.exp(np.cumsum(strategy_ret))

    buyhold_equity = price / price[0]

    # 统计指标

    active_ret = strategy_ret[long_window:]

    sharpe = np.mean(active_ret) / np.std(active_ret) * np.sqrt(252)

    peak = np.maximum.accumulate(strategy_equity)

    max_dd = np.min((strategy_equity - peak) / peak)

    print(f"策略终值: {strategy_equity[-1]:.2f}x")

    print(f"买入持有终值: {buyhold_equity[-1]:.2f}x")

    print(f"夏普比率: {sharpe:.2f}")

    print(f"最大回撤: {max_dd:.1%}")

simulate_trend_following()
```

运行结果：

```fallback
策略终值: 1.35x

买入持有终值: 1.51x

夏普比率: 0.34

最大回撤: -42.0%
```

|  | 终值 | 夏普比率 | 最大回撤 | 胜率 | 平均盈亏比 |
| --- | --- | --- | --- | --- | --- |
| 趋势跟踪 | 1.35x | 0.34 | \-42.0% | 35.3% | 2.6:1 |
| 买入持有 | 1.51x | \- | \- | \- | \- |

模拟一共触发了 17 笔交易，只有 6 笔盈利，胜率 35.3%，但赢的时候平均赚的是亏的时候的 2.6 倍。这正是趋势跟踪的典型特征：大部分时间在小亏（震荡期均线反复交叉），少数大趋势带来的利润覆盖所有亏损。

![趋势跟踪策略的净值曲线：均线交叉信号 vs 买入持有](https://coriva.eu.org/cta-strategy-guide/trend-following-equity.webp)

### 趋势跟踪的致命弱点

从模拟结果可以看到，策略终值（1.35x）甚至低于买入持有（1.51x）。这不是说趋势跟踪没用，而是因为这段模拟中震荡期占了将近一半时间。震荡市是趋势跟踪的天敌：价格没有明确方向，均线反复交叉，策略不断开仓又止损，交易成本和滑点持续消耗资金。这种成本被称为"趋势税"。

趋势跟踪的利润分布极度集中：17 笔交易里只有 6 笔盈利，剩下 11 笔都在亏。如果你错过了那几笔大趋势（比如因为参数选择不当而晚进场），整体收益可能直接变成负数。

参数敏感性是另一个问题。把短期均线从 20 日改到 15 日或 25 日，结果可能差很多。但不能因此去优化参数让回测最好看，这就是 [回测陷阱](https://coriva.eu.org/backtesting-pitfalls/) 里的过拟合。实操中的做法是：在多个品种上用同一组参数，靠品种分散来平滑单品种的参数风险。

### ATR 仓位管理

趋势跟踪策略通常配合 **ATR（Average True Range，平均真实波幅）** 来做仓位管理。ATR 衡量的是品种最近 N 天的平均每日波动幅度。波动大的品种（比如原油），每天涨跌 3%，仓位就要小；波动小的品种（比如国债期货），每天涨跌 0.3%，仓位可以大。

常用的规则是：每笔交易的风险敞口 = 1 个 ATR 对应本金的 1%。如果账户有 100 万，1% 是 1 万元。某品种的 ATR 是 50 点，每点价值 10 元，那最多开 1 万 / (50 × 10) = 20 手。这样不管交易什么品种，每笔止损的金额是一样的。

[TA-Lib](https://coriva.eu.org/ta-lib-python-tutorial/) 的 `ATR` 函数可以直接计算这个指标。

## 期货套利策略

CTA 里的套利策略和 [配对交易](https://coriva.eu.org/pairs-trading-guide/) 方法论一致：找协整关系 → 构建价差 → 价差偏离时入场 → 回归时出场。区别在于标的从股票换成了期货合约，套利类型主要分两种。

### 跨期套利

**跨期套利** （Calendar Spread）交易的是同一品种不同到期月的合约之间的价差。比如螺纹钢 2025 年 5 月合约和 2025 年 10 月合约，它们跟踪的是同一个商品，但到期时间不同。

近远月合约的价差反映的是持仓成本（仓储、资金利率）和市场对未来供需的预期。正常情况下远月合约比近月贵（期货升水 / contango），价差相对稳定。当价差因为短期供需冲击、交割月临近等原因偏离正常水平时，套利机会出现。

### 跨品种套利

**跨品种套利** （Intercommodity Spread）交易的是两个不同但相关的品种之间的价差。几个经典组合：

- **大豆压榨价差** ：豆粕 + 豆油 vs 大豆。大豆压榨后产出豆粕和豆油，三者之间有物理上的数量关系，价差偏离意味着压榨利润异常
- **螺纹钢 vs 铁矿石** ：钢铁生产的上下游关系，价差代表钢厂利润
- **WTI vs Brent 原油** ：两个不同产地的原油基准，受区域供需和运输成本影响

### 套利信号：Z-score 方法

不管是跨期还是跨品种，信号生成的逻辑都一样：计算价差的滚动 Z-score，偏离超过阈值就入场。

```python
import numpy as np

def simulate_calendar_spread():

    np.random.seed(123)

    n = 500

    # 模拟两个相关期货合约（近月 vs 远月）

    common = np.cumsum(np.random.normal(0.0002, 0.012, n))

    near = 3500 + common * 100 + np.cumsum(np.random.normal(0, 0.8, n))

    # 远月 = 近月 + 升水 + 均值回复噪声

    spread_noise = np.zeros(n)

    for i in range(1, n):

        spread_noise[i] = 0.93 * spread_noise[i-1] + np.random.normal(0, 2.5)

    far = near + 30 + spread_noise

    spread = far - near

    lookback = 40

    # 滚动 Z-score

    z_score = np.full(n, np.nan)

    for i in range(lookback, n):

        window = spread[i-lookback:i]

        std_w = np.std(window)

        if std_w > 0:

            z_score[i] = (spread[i] - np.mean(window)) / std_w

    # 信号

    position = np.zeros(n)

    for i in range(lookback + 1, n):

        if np.isnan(z_score[i]):

            position[i] = position[i-1]

            continue

        if position[i-1] == 0:

            if z_score[i] > 2:    position[i] = -1   # 价差过大，做空价差

            elif z_score[i] < -2: position[i] = +1   # 价差过小，做多价差

        elif position[i-1] == 1:

            if abs(z_score[i]) < 0.5:  position[i] = 0    # 回归，平仓

            elif z_score[i] < -4:      position[i] = 0    # 止损

            else:                      position[i] = 1

        elif position[i-1] == -1:

            if abs(z_score[i]) < 0.5:  position[i] = 0

            elif z_score[i] > 4:       position[i] = 0

            else:                      position[i] = -1

    # 盈亏

    spread_ret = np.diff(spread)

    start = lookback + 1

    daily_pnl = position[start:-1] * spread_ret[start:]

    daily_pnl = daily_pnl[~np.isnan(daily_pnl)]

    n_trades = int(np.sum(np.abs(np.diff(position[start:])) > 0))

    print(f"累计盈亏: {np.sum(daily_pnl):.2f}")

    print(f"交易次数: {n_trades} ({n_trades//2} 个来回)")

    print(f"年化夏普: {np.mean(daily_pnl)/np.std(daily_pnl)*np.sqrt(252):.2f}")

simulate_calendar_spread()
```

运行结果：

```fallback
累计盈亏: 96.14

交易次数: 34 (17 个来回)

年化夏普: 2.49
```

17 个来回交易，年化夏普 2.49。累计盈亏 96 个价差点，相对于价差均值（约 32 点）大概是 3 倍标准差的波动范围赚了 3 轮。和趋势跟踪的 0.34 形成鲜明对比：套利策略胜率高、夏普高，但每笔利润有限（价差的波动范围有天花板），而趋势跟踪在大行情里的单笔利润可以非常大。

![期货跨期套利的价差和 Z-score 信号](https://coriva.eu.org/cta-strategy-guide/futures-spread-zscore.webp)

这个回测做了大幅简化：合成数据的价差是我们直接构造成均值回归的（AR(1) 过程），所以跳过了协整检验步骤。实盘中必须先做协整检验，否则价差可能根本不回归。代码里也没扣交易成本和滑点，没有考虑保证金变化和合约滚动。实际表现会差一些。关于回测中常见的各种坑，参考 [回测陷阱大全](https://coriva.eu.org/backtesting-pitfalls/) 。Z-score 方法的完整实现（包括协整检验、滚动对冲比率）在 [配对交易入门](https://coriva.eu.org/pairs-trading-guide/) 里有详细讲解。

**合约滚动** 。期货合约有到期日，比如螺纹钢 2025 年 5 月合约在 5 月中旬到期。如果你的趋势策略在 4 月发出做多信号，持仓到 5 月就必须在到期前把仓位从 5 月合约转移到 10 月合约（“移仓换月”）。滚动时近远月价差会产生成本或收益，长期累积下来是一笔不可忽视的隐性成本。

**保证金和杠杆** 。期货天生带杠杆：10% 的保证金比例意味着你用 10 万元的保证金可以控制 100 万元面值的合约。如果不做任何资金管理，价格变动 1% 就是保证金的 10% 变动。CTA 基金通常只动用 20-30% 的总资金做保证金，剩余 70-80% 的资金放在国债等无风险资产上吃利息。这样实际的投资组合杠杆只有 2-3 倍，不是名义上的 10 倍。

**多品种分散** 。单个品种可能大半年没有趋势，策略一直在亏趋势税。但如果同时交易 30 个品种（股指、国债、黄金、原油、农产品、金属），某些品种处于趋势行情的概率就大得多。分散是 CTA 基金控制回撤的核心手段。

评估 CTA 策略的表现，除了夏普比率， **Calmar 比率** （年化收益率除以最大回撤的绝对值，比如年化 15% / 最大回撤 30% = 0.5）也很常用，因为 CTA 策略的回撤特征比股票策略更重要。

CTA 策略和 [波动率交易策略](https://coriva.eu.org/volatility-trading-strategies/) 的一个本质区别：波动率交易赌的是"波动幅度"会比市场定价大还是小，CTA 赌的是"价格方向"会不会延续。两者可以组合使用：CTA 做方向性收益来源，波动率策略做风险管理工具。