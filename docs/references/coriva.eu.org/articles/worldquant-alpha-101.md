---
url: https://coriva.eu.org/alpha101-overview/
title: "WorldQuant Alpha 101 全解读：因子分类与算子速查"
description: "WorldQuant Alpha 101 论文深度解读：101 个量化因子按经济逻辑分为五大类，附算子直觉讲解、pandas 代码与完整分类对照表。"
author: "Coriva"
published: "2026-04-14T11:33:08+08:00"
language: "zh"
captured_at: "2026-06-30T12:18:38.634Z"
---

# WorldQuant Alpha 101 全解读：因子分类与算子速查

WorldQuant 在 2016 年发表了一篇只有 6 页的论文，标题叫 “101 Formulaic Alphas”，作者 Zura Kakushadze。论文做了一件很简单的事：把 101 个量化因子的计算公式直接公开了。这些因子在 WorldQuant 的生产环境中有 80 个在实际使用，持仓周期 0.6 到 6.4 天，因子之间的平均相关性只有 15.9%。

但论文只给了公式，没有任何解释。Alpha#3 为什么是那个样子，背后在捕捉什么市场现象，一句话都没有。这就是本系列文章要做的事：把 101 个 Alpha 101 因子按经济逻辑分类，拆解公式背后的动机，搞清楚每个因子到底在赌什么。

这个系列分五篇：

1. **本篇：全局概览** ，建立分类框架，讲清楚算子是什么意思，给出完整分类对照表
2. 量价背离类因子详解（32 个因子）
3. 动量与反转类因子详解（23 个因子）
4. 波动率与日内结构类因子详解（23 个因子）
5. 流动性、复合因子与因子组合实战（23 个因子）

## Alpha 101 的数据输入

101 个公式用到的原始数据就那么几个字段，全是股票日频行情数据：

| 字段 | 含义 | 备注 |
| --- | --- | --- |
| `open` | 开盘价 |  |
| `close` | 收盘价 |  |
| `high` | 最高价 |  |
| `low` | 最低价 |  |
| `volume` | 成交量 |  |
| `vwap` | 成交量加权平均价 | yfinance 不直接提供，需要自算 |
| `returns` | 日收益率 | `close / delay(close,1) - 1` |
| `cap` | 总市值 | 用于 `rank` 加权 |
| `adv{d}` | 过去 d 天日均成交额 | `adv20` = 过去 20 天日均成交额（price × volume） |
| `IndClass` | 行业分类 | 用于行业中性化 |

`vwap` 在论文里出现了十几次，但大多数免费数据源不提供这个字段。近似计算方法：用日内 `(high + low + close) / 3` 作为粗略替代，或者用分钟线数据计算真实 vwap。对于日频因子研究来说，粗略替代够用了。

`adv{d}` 有多种变体： `adv5` 、 `adv10` 、 `adv15` 、 `adv20` 、 `adv30` 、 `adv40` 、 `adv50` 、 `adv60` 、 `adv81` 、 `adv120` 、 `adv150` 、 `adv180` 。论文中 `adv{d}` 严格来说是日均成交额（价格 × 成交量）的滚动均值。不过很多开源实现直接用 volume 的均值代替，因为对于 rank 之后的因子来说，成交额和成交量的排名高度相关。

数据源方面，美股用 [yfinance](https://coriva.eu.org/a-tutorial-to-use-yfinance/) 就能拿到 OHLCV，A 股用 Tushare 或 AKShare。

## 算子详解：读懂公式的钥匙

Alpha 101 的公式看起来吓人，但用到的算子翻来覆去就那么十几个。搞懂这些算子，101 个公式大部分都能直接读懂。

### 截面算子：在同一天的所有股票之间比较

**`rank(x)`** ：把当天所有股票的 $$ 值排名，然后归一化到 0~1 之间。排名最低的股票得 0，最高的得 1。

为什么要 rank？因为不同指标的量纲天差地别。成交量可能是几百万股，收益率是百分之零点几。直接算相关性或做加减法，量纲大的指标会完全压过量纲小的。rank 之后全部变成 0~1 的分位数，苹果和苹果才能比。

rank 还有一个好处：天然抗极端值。某只股票今天成交量暴增 10 倍，raw 值可能把所有统计量搞崩，但 rank 之后它最多也就是排第一（值为 1.0），不会影响其他股票的相对排名。

这就是为什么 101 个因子里 rank 出现了 90 多次。它是整个公式体系的"通用适配器"。

### 时序算子：在同一只股票的历史上计算

**`delay(x, d)`** ： $$ 天前的值。 `delay(close, 1)` = 昨天的收盘价。

**`delta(x, d)`** ：今天的值减去 $$ 天前的值。 `delta(close, 5)` = 最近 5 天收盘价变了多少。比 returns 更直接，不做除法归一化，保留了绝对变化幅度的信息。

**`ts_rank(x, d)`** ：当前值在过去 $$ 天里排第几。如果今天的收盘价是最近 10 天里最高的， `ts_rank(close, 10)` 接近 1.0。这个算子把绝对值翻译成"相对于近期历史的位置"。一只股票今天涨了 2%，这算多还是少？如果过去 10 天都在涨 3%+，那 2% 其实偏弱。ts\_rank 捕捉的就是这种相对强弱。

**`ts_argmax(x, d)` / `ts_argmin(x, d)`** ：过去 $$ 天内，最大值（或最小值）出现在窗口的第几个位置。返回值是 0-based 索引：0 = 窗口最早的一天， $$ = 今天。如果 `ts_argmax(close, 10)` = 9，说明最高价就在今天，价格可能还在上升趋势中。如果 = 0，说明最高价出现在 10 天前的第一天，之后一路下滑。这个算子衡量的是"极值出现在窗口的什么位置"，值越大说明极值越近。

**`correlation(x, y, d)`** ：过去 $$ 天内 $$ 和 $$ 的 Pearson 相关系数。量价背离类因子大量使用这个算子，比如计算价格排名和成交量排名的相关性。相关性高说明量价同步，相关性低或为负说明量价背离。

**`covariance(x, y, d)`** ：过去 $$ 天内 $$ 和 $$ 的协方差。和 correlation 类似但不归一化，保留了量纲信息。

**`decay_linear(x, d)`** ：过去 $$ 天的加权平均，权重线性递减（最近一天权重最大， $$ 天前权重最小）。普通移动平均对每一天一视同仁，但在量化因子里，近期信息通常比远期更有预测力。 `decay_linear` 是一种简单的"越近越重要"加权方案。权重分配：第 1 天（最近）权重 $$ ，第 2 天权重 $$ ，以此类推，最后归一化。

**`stddev(x, d)`** ：过去 $$ 天 $$ 的标准差，就是波动率。

**`sum(x, d)` / `product(x, d)`** ：过去 $$ 天的求和 / 连乘。

**`min(x, d)` / `max(x, d)`** ：过去 $$ 天的最小值 / 最大值。

### 变换算子

**`scale(x, a=1)`** ：把截面上所有股票的 $$ 值缩放，使绝对值之和等于 $$ 。用处是把因子值转换成"仓位权重"。如果 $$ ，scale 之后的因子值直接就是每只股票该分配多少仓位（正值做多，负值做空），全部仓位加起来正好满仓。

**`signedpower(x, a)`** ：保留符号的幂运算， $$ 。当 $$ 时，大的值被放大得更多，小的值被压缩。效果是拉大因子值的差距，让头部和尾部股票的信号更强。

### 行业中性化

**`IndNeutralize(x, IndClass)`** ：在每个行业内部做截面去均值。

为什么需要这个？假设你的因子是"成交量变化率"。银行股整体成交量就是比医药股大几个数量级。如果不做行业中性化，因子排名会天然偏向成交量大的行业，你选出来的"成交量异常放大"的股票可能全是银行股，根本不是因为它们有 alpha，只是行业特征。

IndNeutralize 之后，银行股和银行股比，医药股和医药股比，去掉了行业层面的系统性差异，剩下的才是个股层面的 alpha 信号。

101 个因子里有 26 个用到了 IndNeutralize。如果你没有行业分类数据，这 26 个因子可以先跳过，剩下 75 个照样能做。

### pandas 实现核心算子

```python
import numpy as np

import pandas as pd

def rank(df):

    """截面排名归一化到 [0, 1]"""

    return df.rank(axis=1, pct=True)

def delay(df, d):

    """时移 d 天"""

    return df.shift(d)

def delta(df, d):

    """d 天差分"""

    return df - delay(df, d)

def ts_rank(df, d):

    """当前值在过去 d 天中的排名分位数"""

    return df.rolling(d).apply(

        lambda x: x.rank().iloc[-1] / len(x), raw=False

    )

def ts_argmax(df, d):

    """过去 d 天最大值的位置（0 = 最早，d-1 = 今天）"""

    return df.rolling(d).apply(lambda x: x.argmax(), raw=True)

def ts_argmin(df, d):

    """过去 d 天最小值的位置（0 = 最早，d-1 = 今天）"""

    return df.rolling(d).apply(lambda x: x.argmin(), raw=True)

def decay_linear(df, d):

    """线性衰减加权平均，近期权重大"""

    weights = np.arange(1, d + 1, dtype=float)

    weights /= weights.sum()

    return df.rolling(d).apply(

        lambda x: np.dot(x, weights), raw=True

    )

def scale(df, a=1):

    """截面缩放，使绝对值之和等于 a"""

    return df.mul(a).div(df.abs().sum(axis=1), axis=0)

def signedpower(df, a):

    """保留符号的幂运算"""

    return np.sign(df) * df.abs().pow(a)

def ts_corr(x, y, d):

    """滚动相关系数"""

    return x.rolling(d).corr(y)

def ts_cov(x, y, d):

    """滚动协方差"""

    return x.rolling(d).cov(y)
```

### 算子速查表

| 算子 | 类型 | 直觉 | 类比 |
| --- | --- | --- | --- |
| `rank` | 截面 | 排名归一化，消除量纲 | 考试排名 vs 原始分数 |
| `delay` | 时序 | 回看 d 天前 | “昨天的收盘价” |
| `delta` | 时序 | d 天变化量 | “这周涨了多少” |
| `ts_rank` | 时序 | 当前值在近期的位置 | “今天是近期最高还是最低” |
| `ts_argmax` | 时序 | 极值在窗口的位置 | “最高价出现在窗口第几天” |
| `correlation` | 时序 | 两序列同步程度 | “量涨价也涨？” |
| `decay_linear` | 时序 | 近期加权均值 | “越近的数据越重要” |
| `stddev` | 时序 | 波动率 | “最近波动大不大” |
| `scale` | 截面 | 归一化为仓位权重 | “总仓位加起来 = 1” |
| `signedpower` | 变换 | 放大头尾信号 | “强的更强，弱的更弱” |
| `IndNeutralize` | 截面 | 去行业偏差 | “银行和银行比，医药和医药比” |

## 五大分类：每个因子到底在赌什么

读完算子，可以开始看因子了。101 个因子看上去五花八门，但按背后的经济逻辑归类，其实就五种思路。

### 量价背离类（32 个因子）

核心逻辑：价格和成交量应该同步。如果价格在涨但成交量在缩，这个上涨可能缺乏支撑。如果成交量突然放大但价格没怎么动，说明买卖双方有分歧，可能在酝酿方向选择。

量价背离是技术分析里最古老的观点之一，Alpha 101 把它公式化了。

**Alpha#3** 是这类因子里最干净的一个：

$$

$$

一层一层拆：

1. `rank(open)` ：今天所有股票按开盘价排名，归一化到 0~1
2. `rank(volume)` ：今天所有股票按成交量排名，归一化到 0~1
3. `correlation(..., 10)` ：过去 10 天，这两组排名的相关系数
4. `-1 *` ：取反

过去 10 天里，如果一只股票开盘价排名高的时候成交量排名也高，相关系数为正，乘以 -1 后因子值为负（做空信号）。反过来，如果开盘价排名和成交量排名是反的（量价背离），因子值为正（做多信号）。

为什么量价背离反而是买入信号？一种解释是：开盘价上涨但成交量没跟上来，说明不是散户追涨推动的，可能是基本面在缓慢定价。而开盘价涨、成交量也暴涨的情况，往往是短期过热，容易回调。

**Alpha#12** 更直接：

$$

$$

翻译成白话：如果今天成交量比昨天大（ `sign(delta(volume, 1))` = +1），就做空今天的价格变动方向（乘以 `-1 * delta(close, 1)` ）。如果今天成交量缩了，就做多今天的价格变动方向。

逻辑是：放量上涨后看跌，缩量下跌后看涨。量价配合"异常"的方向更可能反转。

这类因子在 Alpha 101 里数量最多（32 个）。从 WorldQuant 的角度看，量价关系在短期因子里提供了最多的独立信号源。

```python
# Alpha#3 计算示例

# 假设 df_open 和 df_volume 是 DataFrame，行=日期，列=股票

alpha3 = -1 * ts_corr(rank(df_open), rank(df_volume), 10)
```

### 动量与反转类（23 个因子）

核心逻辑：股票短期（1~5 天）倾向于反转，中期（10~20 天）倾向于延续。

短期反转的经济直觉很直白：今天涨多了的股票，明天有人获利了结，价格回落。今天跌多了的股票，超卖后有人抄底，价格反弹。这在流动性好的市场里是个稳定现象，背后是做市商和短线交易者的行为模式。

中期动量的来源不同：信息扩散需要时间，好消息出来后不是所有人同时反应，先知先觉的资金先买，后知后觉的资金跟进，形成趋势。

**Alpha#9** 是一个带条件判断的反转因子：

$$

$$

前两个条件在判断趋势是否一致：如果过去 5 天每天都在涨（最小变化 > 0），或者每天都在跌（最大变化 < 0），就用 `ts_rank` 来衡量当前涨/跌幅在近期的位置。否则（涨跌交替），直接用反转信号。两个趋势分支的公式相同，这不是写错了：论文原文就是对"连续上涨"和"连续下跌"两种趋势给出相同的动量度量，区别体现在 delta(close,1) 的符号上。

这个设计有意思：在单边行情里用动量逻辑（ts\_rank），在震荡行情里用反转逻辑。比无脑反转多了一层市场状态判断。

**Alpha#20** 抓的是跳空缺口的回补：

$$

$$

三项都在衡量今天开盘价相对于昨天 K 线的位置。如果今天高开（跳空高于昨天的 high、close、low），三项都为正，乘积为正，再乘 -1 变成负值（做空信号）。逻辑是：大幅跳空高开往往过度反应，日内倾向于回落。

### 波动率类（11 个因子）

核心逻辑：波动率有均值回归的特性。高波动之后往往回落，低波动之后往往放大。

更细致地说，波动率类因子捕捉的是"波动率状态切换"的时机。一只股票如果最近 5 天非常平静，突然有一天大幅波动，这往往意味着新信息到来，后续可能有趋势性行情。反过来，持续高波动之后，市场逐渐消化信息，波动率趋于收敛。

**Alpha#1** 是一个条件波动率信号：

$$

$$

拆开看：先按条件选信号源（下跌用波动率，上涨用收盘价），用 signedpower 放大差异，ts\_argmax 看过去 5 天最大值出现在窗口的哪个位置，最后做截面 rank 再减 0.5（使因子值中心化到 0 附近，正值做多负值做空）。

核心思路：下跌阶段关注波动率，上涨阶段关注价格。下跌时波动率越高说明恐慌越严重，恐慌到极致（ `ts_argmax` 最近发生）反而是反转信号。

波动率类因子数量不多（11 个），但底层思路和 [波动率交易策略](https://coriva.eu.org/volatility-trading-strategies/) 相通：波动率本身就是可以交易的标的。

### 日内结构类（12 个因子）

核心逻辑：开盘价、收盘价、最高价、最低价的相对关系，反映了日内交易者的行为模式。

比如收盘价比开盘价高多少（日内涨幅），收盘价在日内振幅中的位置（靠近最高价还是最低价），vwap 和收盘价的关系（机构是在均价之上还是之下交易）。

**Alpha#101** 是结构最简单的一个：

$$

$$

分子是日内涨跌，分母是日内振幅（加 0.001 防止除零）。这个比值衡量的是"日内收益占振幅的比例"。值接近 +1 说明开盘就是最低价、收盘就是最高价（全天单边上涨）。值接近 0 说明收盘价和开盘价差不多，日内波动都是噪音。

这个因子本身就是一个经典的技术分析指标，叫做 “close location value”。它的预测含义是：全天单边上涨的股票，次日倾向于（微弱地）延续；全天来回震荡最终平收的股票，缺乏方向性动能。

**Alpha#42** 关注 vwap 和收盘价的关系：

$$

$$

`vwap - close` > 0 说明加权平均成交价高于收盘价，意味着"大部分成交发生在价格较高的时候"，收盘时价格回落了。这往往是机构在日内高位出货的信号。因子对这种行为给出做空信号。

注意 Alpha#42 没有用 delay，它用的全是当天数据。论文里标注这个因子的持仓周期是 0 天，意味着它是一个日内信号。在 T+1 的 A 股市场，这类因子不适用。

### 流动性与复合类（23 个因子）

核心逻辑：流动性（成交量、换手率）的变化反映了机构资金的进出。

机构交易和散户交易的一个本质区别是：机构的单子太大，不可能一次性买完。一只股票如果连续几天成交量放大但价格没怎么动，很可能是有大资金在分批建仓。成交量突然萎缩，可能是大资金建仓完毕或者市场进入观望状态。

**Alpha#7** 是一个带条件的流动性信号：

$$

$$

条件判断：今天的成交量是否超过 20 日均量。如果超过（放量），因子用过去 7 天价格变化的绝对值在 60 天里的排名，乘以变化方向，再取反。如果没放量（缩量），直接给 -1（做空信号）。

逻辑是：缩量的时候一律看空，因为缩量意味着参与度下降，支撑力弱。放量的时候就看价格变化的"力度"在近期历史中处于什么位置，力度越大说明这笔放量越有意义。

这类因子比较复杂，经常嵌套多个算子和条件判断。它们属于"复合因子"，融合了量、价、波动率等多维信息。单独理解时比较绕，但赚钱逻辑大多可以归结为"跟着聪明钱走"。

## Alpha 101 因子完整分类对照表

下表把 101 个因子全部归入五个类别。“核心算子"列标出该因子最核心的计算逻辑，“IndNeut” 标记是否需要行业中性化。

![Alpha 101 factor classification distribution](https://coriva.eu.org/alpha101-overview/alpha101-classification.webp)

| 编号 | 类别 | 核心算子 | 一句话逻辑 | IndNeut | 详解篇 |
| --- | --- | --- | --- | --- | --- |
| #1 | 波动率 | stddev, ts\_argmax | 下跌时看波动率极值位置 | 否 | 第4篇 |
| #2 | 量价背离 | correlation, rank | 量价排名走势相关性 + 日内涨跌 | 否 | 第2篇 |
| #3 | 量价背离 | correlation, rank | 开盘价-成交量排名相关性 | 否 | 第2篇 |
| #4 | 动量反转 | ts\_rank | 低位做空信号 | 否 | 第3篇 |
| #5 | 日内结构 | rank, ts\_rank, vwap | vwap 偏离的排名变化 | 否 | 第4篇 |
| #6 | 量价背离 | correlation | 开盘价与成交量的直接相关性 | 否 | 第2篇 |
| #7 | 流动性 | adv, ts\_rank, delta | 放量/缩量条件下的价格动量 | 否 | 第5篇 |
| #8 | 动量反转 | rank, delta, sum | 开盘价变化与收益排名的交互 | 否 | 第3篇 |
| #9 | 动量反转 | delta, ts\_min/max | 条件反转：趋势 vs 震荡 | 否 | 第3篇 |
| #10 | 动量反转 | delta, ts\_min/max | 与#9 类似的条件动量 | 否 | 第3篇 |
| #11 | 量价背离 | rank, correlation, vwap | vwap/close/volume 三者互动 | 否 | 第2篇 |
| #12 | 量价背离 | sign, delta | 量变方向 × 价变方向 | 否 | 第2篇 |
| #13 | 量价背离 | rank, covariance | 收盘价与成交量的协方差排名 | 否 | 第2篇 |
| #14 | 量价背离 | correlation, returns | 开盘价与收益率的相关性 | 否 | 第2篇 |
| #15 | 量价背离 | correlation, rank | 最高价与成交量排名的相关性 | 否 | 第2篇 |
| #16 | 量价背离 | covariance, rank | 最高价与成交量的协方差排名 | 否 | 第2篇 |
| #17 | 量价背离 | rank, ts\_rank, delta, volume | 收盘价排名与 volume 动量排名 | 否 | 第2篇 |
| #18 | 波动率 | correlation, stddev | 收盘价与开盘价的相关性 | 否 | 第4篇 |
| #19 | 动量反转 | returns, delay | 条件收益率反转 | 否 | 第3篇 |
| #20 | 动量反转 | rank, open, delay | 跳空缺口回补 | 否 | 第3篇 |
| #21 | 波动率 | mean, stddev, close | 均值偏离 + 成交量条件 | 否 | 第4篇 |
| #22 | 量价背离 | correlation, delta, stddev | 最高价与成交量相关性 + 价格变化 | 否 | 第2篇 |
| #23 | 动量反转 | sma, delta, high | 最高价均值条件反转 | 否 | 第3篇 |
| #24 | 动量反转 | sma, delta, close | 收盘价均值条件反转 | 否 | 第3篇 |
| #25 | 量价背离 | rank, returns, adv, vwap | 收益率 × 成交量偏离 × vwap 排名 | 否 | 第2篇 |
| #26 | 量价背离 | correlation, ts\_rank, volume | 时序排名相关性 + 成交量 | 否 | 第2篇 |
| #27 | 量价背离 | rank, correlation, volume | 量价相关性的排名分位 | 否 | 第2篇 |
| #28 | 量价背离 | correlation, adv, rank | 成交量与均量的相关性 | 否 | 第2篇 |
| #29 | 动量反转 | delta, ts\_rank, returns | 收盘价变化的排名趋势 | 否 | 第3篇 |
| #30 | 动量反转 | delta, close, volume | 价量变化的符号交互 | 否 | 第3篇 |
| #31 | 流动性 | rank, adv, correlation, decay\_linear | 流动性 + 量价衰减相关 | 否 | 第5篇 |
| #32 | 量价背离 | scale, correlation | 均值偏离 × 量价相关性衰减 | 否 | 第2篇 |
| #33 | 动量反转 | rank, open/close | 开收盘价排名反转 | 否 | 第3篇 |
| #34 | 波动率 | rank, stddev, delta, close | 波动率排名与价格反转 | 否 | 第4篇 |
| #35 | 动量反转 | rank, returns, volume, ts\_rank | 多因子复合反转 | 否 | 第3篇 |
| #36 | 波动率 | correlation, rank, adv | 多维相关性排名 | 否 | 第4篇 |
| #37 | 动量反转 | correlation, rank, delay | 开盘价排名的自相关 | 否 | 第3篇 |
| #38 | 动量反转 | rank, close, open | 收盘价排名与日内涨幅 | 否 | 第3篇 |
| #39 | 动量反转 | rank, delta, decay\_linear, returns | 衰减动量信号 | 否 | 第3篇 |
| #40 | 量价背离 | rank, stddev, correlation | 高价波动率 × 成交量排名 | 否 | 第2篇 |
| #41 | 日内结构 | high, low, vwap | 日内振幅与 vwap 的关系 | 否 | 第4篇 |
| #42 | 日内结构 | rank, vwap, close | vwap 与收盘价偏离 | 否 | 第4篇 |
| #43 | 量价背离 | rank, adv, delta, close | 流动性排名 × 价格变化 | 否 | 第2篇 |
| #44 | 量价背离 | correlation, rank | 最低价与成交量排名相关性 | 否 | 第2篇 |
| #45 | 量价背离 | rank, delta, correlation, decay\_linear | 收盘价变化排名 × 量价衰减相关 | 否 | 第2篇 |
| #46 | 动量反转 | delay, delta, close | 条件价格动量 | 否 | 第3篇 |
| #47 | 日内结构 | rank, adv, high, close, vwap | 流动性加权的 vwap-close 偏离 | 否 | 第4篇 |
| #48 | 流动性 | correlation, rank, delta | 行业中性量价相关 | 是 | 第5篇 |
| #49 | 动量反转 | delta, delay, close | 条件价格反转 | 否 | 第3篇 |
| #50 | 量价背离 | correlation, rank, ts\_rank, volume | 量价排名的时序相关 | 否 | 第2篇 |
| #51 | 动量反转 | delta, delay, close | 条件延迟反转 | 否 | 第3篇 |
| #52 | 动量反转 | delta, ts\_min, ts\_rank, volume | 价格低点 + 成交量排名 | 否 | 第3篇 |
| #53 | 动量反转 | delta, close, high, low | 日内位置变化 | 否 | 第3篇 |
| #54 | 日内结构 | open, close, low | 日内偏离结构 | 否 | 第4篇 |
| #55 | 量价背离 | correlation, rank, high, low, volume | 日内振幅与成交量相关 | 否 | 第2篇 |
| #56 | 动量反转 | rank, returns, cap | 市值与收益排名的交互 | 否 | 第3篇 |
| #57 | 波动率 | decay\_linear, rank, ts\_argmin, close | 衰减最低点位置排名 | 否 | 第4篇 |
| #58 | 流动性 | correlation, rank, volume | 行业中性量价相关 | 是 | 第5篇 |
| #59 | 流动性 | correlation, rank, volume | 行业中性量价相关变体 | 是 | 第5篇 |
| #60 | 量价背离 | scale, correlation, rank | 量价相关性 × 日内涨跌缩放 | 否 | 第2篇 |
| #61 | 流动性 | rank, vwap, adv | vwap 排名与均量排名较量 | 否 | 第5篇 |
| #62 | 日内结构 | rank, vwap, correlation, adv | vwap 排名 × 量价相关 | 否 | 第4篇 |
| #63 | 流动性 | rank, vwap, delta, decay\_linear, adv | 流动性衰减信号 | 是 | 第5篇 |
| #64 | 日内结构 | rank, correlation, vwap, adv | vwap 流动性相关排名 | 是 | 第4篇 |
| #65 | 日内结构 | rank, correlation, vwap, adv | vwap 排名相关性衰减 | 是 | 第4篇 |
| #66 | 日内结构 | rank, decay\_linear, vwap, delta | vwap 偏离的衰减动量 | 是 | 第4篇 |
| #67 | 流动性 | rank, correlation, high, adv | 最高价与均量的相关排名 | 是 | 第5篇 |
| #68 | 日内结构 | rank, correlation, high, adv, close | 最高价-均量相关 × 收盘排名 | 是 | 第4篇 |
| #69 | 流动性 | rank, ts\_rank, delta, close, adv | 行业中性价格动量排名 | 是 | 第5篇 |
| #70 | 流动性 | rank, delta, close, vwap | 行业中性价格-vwap 变化 | 是 | 第5篇 |
| #71 | 流动性 | decay\_linear, correlation, rank, close, adv | 多层衰减流动性信号 | 是 | 第5篇 |
| #72 | 流动性 | rank, correlation, vwap, adv, decay\_linear | 衰减量价排名相关 | 是 | 第5篇 |
| #73 | 日内结构 | rank, decay\_linear, vwap, delta, open | vwap 衰减 × 开盘变化 | 是 | 第4篇 |
| #74 | 量价背离 | rank, correlation, close, adv | 收盘价与均量相关排名 | 是 | 第2篇 |
| #75 | 流动性 | rank, correlation, vwap, volume | vwap 与成交量排名的相关性 | 否 | 第5篇 |
| #76 | 流动性 | rank, decay\_linear, correlation, vwap | 衰减 vwap 排名相关 | 是 | 第5篇 |
| #77 | 量价背离 | rank, decay\_linear, delta, correlation | 价格变化衰减 × 量价相关 | 否 | 第2篇 |
| #78 | 量价背离 | rank, correlation, low, adv | 最低价与均量相关排名 | 否 | 第2篇 |
| #79 | 流动性 | rank, delta, vwap, correlation, close | 行业中性 vwap 变化排名 | 是 | 第5篇 |
| #80 | 流动性 | rank, delta, close, correlation, adv | 行业中性变化与量价相关 | 是 | 第5篇 |
| #81 | 量价背离 | rank, correlation, vwap, adv | vwap 与均量的排名相关 | 否 | 第2篇 |
| #82 | 流动性 | rank, delta, open, correlation, adv | 行业中性开盘变化排名 | 是 | 第5篇 |
| #83 | 波动率 | rank, delay, high, low | 日内振幅的滞后排名 | 否 | 第4篇 |
| #84 | 动量反转 | rank, vwap, close, signedpower | vwap-close 偏离的幂放大 | 否 | 第3篇 |
| #85 | 量价背离 | rank, correlation, high, adv | 最高价排名与均量相关 | 否 | 第2篇 |
| #86 | 波动率 | rank, correlation, close, adv, delay | 滞后量价相关条件信号 | 是 | 第4篇 |
| #87 | 流动性 | rank, decay\_linear, delta, vwap, adv | 衰减 vwap 动量 | 是 | 第5篇 |
| #88 | 量价背离 | rank, decay\_linear, correlation, open | 开盘价排名衰减相关 | 否 | 第2篇 |
| #89 | 流动性 | decay\_linear, correlation, rank | 衰减相关性信号 | 否 | 第5篇 |
| #90 | 动量反转 | rank, correlation, close, adv | 收盘与均量排名相关反转 | 否 | 第3篇 |
| #91 | 流动性 | rank, correlation, close, adv, decay\_linear | 收盘-均量衰减相关 | 是 | 第5篇 |
| #92 | 量价背离 | rank, decay\_linear, delta, correlation | 多维衰减量价信号 | 否 | 第2篇 |
| #93 | 流动性 | rank, correlation, vwap, adv, decay\_linear | 行业中性衰减 vwap 相关 | 是 | 第5篇 |
| #94 | 波动率 | rank, correlation, adv | 成交量排名与低位信号 | 否 | 第4篇 |
| #95 | 量价背离 | rank, correlation, high, adv | 最高价排名与均量相关 | 否 | 第2篇 |
| #96 | 波动率 | decay\_linear, rank, ts\_argmax, correlation | 衰减极值位置 × 量价相关 | 否 | 第4篇 |
| #97 | 流动性 | rank, decay\_linear, delta, correlation, adv | 行业中性衰减流动性 | 是 | 第5篇 |
| #98 | 波动率 | rank, decay\_linear, correlation, vwap, adv | 衰减 vwap 相关排名 | 否 | 第4篇 |
| #99 | 量价背离 | rank, correlation, high, volume | 最高价与成交量排名相关 | 否 | 第2篇 |
| #100 | 流动性 | rank, stddev, correlation, adv | 波动率与流动性的相关排名 | 是 | 第5篇 |
| #101 | 日内结构 | close, open, high, low | 日内收益占振幅比 | 否 | 第4篇 |

## 实操提示

**公式里的小数参数不要过度解读。** Alpha#4 里出现了 `ts_rank(...)` 的窗口期 9.99922，Alpha#35 里有 `2.21` 这种看起来很精确的数字。这些参数是机器优化出来的，不是人拍脑袋定的。它们在样本内 overfit 的概率很高，换成整数（10、2）效果可能差不多。

**26 个因子需要行业分类数据。** 表里标了 “IndNeut” 的因子用到了 IndNeutralize，需要每只股票的行业归属。美股用 GICS 或 SIC 分类，A 股用申万行业分类。如果你手头没有行业数据，先做剩下的 75 个因子，这些足够搭建一个像样的因子库了。

**少数因子是纯日内信号。** Alpha#42、#54 这类因子没有用 delay，完全基于当天 OHLCV 数据。论文里它们的持仓周期接近 0 天，是日内交易信号。在 T+1 的 A 股市场直接做不了，但可以把信号延迟一天作为次日的选股因子，效果会打折。

**单因子 ≠ 策略。** 论文发表于 2016 年，距今已经 10 年。任何单个因子在被公开之后都会经历 alpha 衰减，因为太多人用同样的信号交易，超额收益被套利掉。但分类逻辑不会过时：量价背离、动量反转、波动率均值回归这些经济规律不会因为一篇论文的发表而消失。

**论文的设计背景是美股大盘股。** 这 101 个因子是在约 1000 只美股大盘股上开发的，假设了美元中性、市值加权等约束条件。直接搬到 A 股小盘股或其他市场，某些因子的表现可能差异很大。比如 A 股的 T+1 限制、涨跌停板、散户比例偏高，都会影响反转类因子的有效性。跨市场使用时需要做本地化回测验证。

把单因子当作积木，通过因子组合（多因子模型）来构建策略，才是 Alpha 101 的正确用法。因子之间的平均相关性只有 15.9%，说明它们捕捉的是不同维度的信息，组合后的分散化效果很好。策略评估可以参考 [量化投资常用指标大全](https://coriva.eu.org/quant-metrics-guide/) ，用夏普比率、最大回撤等指标衡量组合表现。

## 系列导航

这是 Alpha 101 系列的第一篇，后续四篇会按类别深入拆解具体因子：

- **[第 2 篇：量价背离类因子](https://coriva.eu.org/alpha101-price-volume/)** （32 个），拆解价量关系的多种度量方式
- **[第 3 篇：动量与反转类因子](https://coriva.eu.org/alpha101-momentum/)** （23 个），搞清楚什么时候该跟趋势、什么时候该做反转
- **[第 4 篇：波动率与日内结构类因子](https://coriva.eu.org/alpha101-volatility/)** （23 个），波动率均值回归 + OHLCV 的日内信息
- **[第 5 篇：流动性与复合因子 + 因子组合实战](https://coriva.eu.org/alpha101-liquidity/)** （23 个），从单因子到多因子模型

如果你对量化交易的基础评估指标还不太熟悉，建议先看 [量化投资常用指标大全](https://coriva.eu.org/quant-metrics-guide/) ，搞清楚夏普比率、最大回撤这些概念。波动率类因子和 [波动率交易策略](https://coriva.eu.org/volatility-trading-strategies/) 的思路相通，感兴趣可以交叉阅读。