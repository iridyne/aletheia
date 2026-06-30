---
url: https://coriva.eu.org/option-greeks-guide/
title: "期权Greeks完全指南：Delta、Gamma、Theta、Vega 的公式与代码实现"
description: "期权Greeks入门指南：Black-Scholes 框架下 Delta、Gamma、Theta、Vega 的公式解读，附 Python 计算代码、做市商对冲经验和可视化图表。"
author: "Coriva"
published: "2026-04-12T23:03:45+08:00"
language: "zh"
captured_at: "2026-06-30T12:19:14.685Z"
---

# 期权Greeks完全指南：Delta、Gamma、Theta、Vega 的公式与代码实现

期权定价的核心不是猜标的涨跌，是量化多维风险。标的价格在动、时间在流逝、波动率在变、利率在调，这四个变量同时影响期权的价格，做交易决策时需要知道每个变量对持仓的冲击有多大。 **期权Greeks** 就是干这件事的：把期权价格对每个变量的敏感度提取出来，变成一组可以直接用于风控和对冲的数字。

这是期权Greeks系列的第一篇，覆盖五个一阶Greeks（Delta、Gamma、Theta、Vega、Rho）的数学公式、直觉解释和 Python 代码实现。后续两篇会分别讲二阶Greeks和实战交易应用。

## 期权Greeks 速查

Greeks 的本质是偏导数。Black-Scholes 模型的输入有五个变量：标的价格 $$ 、行权价 $$ 、到期时间 $$ 、无风险利率 $$ 、波动率 $$ 。期权价格 $$ 对每个变量求偏导，就得到对应的 Greek。

| Greek | 符号 | 衡量的敏感度 | Call 取值范围 | Put 取值范围 |
| --- | --- | --- | --- | --- |
| Delta | $$ | 标的价格变动 $1 对期权价格的影响 | \[0, 1\] | \[-1, 0\] |
| Gamma | $$ | Delta 本身随标的价格的变化率 | \> 0 | \> 0 |
| Theta | $$ | 每过一天期权价值的损耗 | 通常 < 0 | 通常 < 0 |
| Vega | $$ | 波动率变动 1% 对期权价格的影响 | \> 0 | \> 0 |
| Rho | $$ | 利率变动 1% 对期权价格的影响 | Call > 0 | Put < 0 |

Gamma 和 Vega 对 Call 和 Put 是一样的（相同行权价和到期日）。推导只要一步：Put-Call Parity 说 $$ ，右边对 $$ 的二阶导和对 $$ 的导数都是零，所以 Call 和 Put 的 Gamma、Vega 必须相等。Theta 理论上也可能为正（深度实值的 Put），但绝大多数情况下是负的。

## Black-Scholes 公式与 Greeks 的数学基础

BSM 模型给出的欧式看涨期权价格公式：

$$

$$

欧式看跌期权：

$$

$$

其中：

$$

$$

$$ 是标准正态分布的累积分布函数， $$ 是对应的概率密度函数。

$$ 的直觉解释：它度量的是"当前标的价格相对行权价有多远"，用波动率标准化后的距离。 $$ 越大，期权越深度实值，越接近于直接持有标的。 $$ 是风险中性测度下期权到期实值的概率， $$ 是对应的标准正态分位数。

下面是完整的 Python 实现。这段代码计算 BSM 价格和所有一阶Greeks，后面各节的分析都基于它：

```python
import numpy as np

from scipy.stats import norm

class BSMCalculator:

    """Black-Scholes-Merton 期权定价与 Greeks 计算"""

    def __init__(self, S, K, T, r, sigma):

        """

        S: 标的价格

        K: 行权价

        T: 到期时间（年）

        r: 无风险利率

        sigma: 波动率

        """

        self.S = S

        self.K = K

        self.T = T

        self.r = r

        self.sigma = sigma

        self.d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))

        self.d2 = self.d1 - sigma * np.sqrt(T)

    def call_price(self):

        return self.S * norm.cdf(self.d1) - self.K * np.exp(-self.r * self.T) * norm.cdf(self.d2)

    def put_price(self):

        return self.K * np.exp(-self.r * self.T) * norm.cdf(-self.d2) - self.S * norm.cdf(-self.d1)

    def delta(self, option_type='call'):

        if option_type == 'call':

            return norm.cdf(self.d1)

        return norm.cdf(self.d1) - 1

    def gamma(self):

        return norm.pdf(self.d1) / (self.S * self.sigma * np.sqrt(self.T))

    def theta(self, option_type='call'):

        """返回每日 Theta（年化值除以 365）"""

        common = -self.S * norm.pdf(self.d1) * self.sigma / (2 * np.sqrt(self.T))

        if option_type == 'call':

            return (common - self.r * self.K * np.exp(-self.r * self.T) * norm.cdf(self.d2)) / 365

        return (common + self.r * self.K * np.exp(-self.r * self.T) * norm.cdf(-self.d2)) / 365

    def vega(self):

        """返回每 1% 波动率变化的价格变动"""

        return self.S * norm.pdf(self.d1) * np.sqrt(self.T) / 100

    def rho(self, option_type='call'):

        """返回每 1% 利率变化的价格变动"""

        if option_type == 'call':

            return self.K * self.T * np.exp(-self.r * self.T) * norm.cdf(self.d2) / 100

        return -self.K * self.T * np.exp(-self.r * self.T) * norm.cdf(-self.d2) / 100

# 示例：计算 S=100, K=100, T=0.25(90天), r=5%, sigma=25%

bsm = BSMCalculator(S=100, K=100, T=0.25, r=0.05, sigma=0.25)

print(f"Call Price:  {bsm.call_price():.4f}")   # 5.5984

print(f"Put Price:   {bsm.put_price():.4f}")    # 4.3562

print(f"Call Delta:  {bsm.delta('call'):.4f}")   # 0.5645

print(f"Put Delta:   {bsm.delta('put'):.4f}")    # -0.4355

print(f"Gamma:       {bsm.gamma():.4f}")         # 0.0315

print(f"Call Theta:  {bsm.theta('call'):.4f}")   # -0.0339

print(f"Vega:        {bsm.vega():.4f}")          # 0.1969

print(f"Call Rho:    {bsm.rho('call'):.4f}")     # 0.1271
```

代码里有两个容易踩坑的地方。Theta 的约定不统一：有些教材给的是年化值，有些给每日值，有些给每个交易日值（除以 252 而不是 365）。实盘中大多数券商和交易平台用的是日历日 Theta（除以 365），这里也采用这个约定。Vega 同理，有人定义为波动率变动 1 个百分点（即 0.01）的影响，有人定义为变动一个单位（即 1.0）的影响，差 100 倍。上面代码用的是"1 个百分点"的约定，和做市商报价习惯一致。

这段代码只做教学演示，没有输入校验。 $$ 或 $$ 会导致除零或 NaN。生产环境里需要加参数合法性检查。

下面这张图是五大Greeks的全景：

![期权 Greeks 全景图：Delta、Gamma、Theta、Vega 随标的价格变化](https://coriva.eu.org/option-greeks-guide/greeks-overview.webp)

## Delta — 期权价格对标的价格的敏感度

Delta 的公式在 BSM 框架下很简洁：

$$

$$

Delta 有三层含义，每层在实际交易中都有用。

第一层是价格敏感度。Call Delta 0.5 意味着标的涨 $1，期权价格大约涨 $0.50。说"大约"是因为这是线性近似，Delta 本身也在变（这就是 Gamma 的事）。

第二层是对冲比率。如果你持有 1 手 Delta 0.5 的 Call（假设合约乘数 100），需要卖空 50 股标的才能构建 Delta 中性头寸。做市商每天做的事情本质上就是这个：不断调整标的持仓，让组合的净 Delta 接近零。

第三层是近似到期概率。Delta 的绝对值可以粗略理解为期权到期时处于实值状态的概率。Delta 0.3 的 Call 大约有 30% 的概率到期实值。这不是精确的概率（精确值是 $$ 而不是 $$ ），但在快速评估时够用。

![Call/Put Delta 随标的价格变化的 S 型曲线](https://coriva.eu.org/option-greeks-guide/delta-curve.webp)

从图上看，Delta 曲线是一个 S 型。深度虚值（OTM）的 Call Delta 接近 0，深度实值（ITM）的接近 1。平值（ATM）附近 Delta 大约 0.5，也是 Delta 变化最快的区域，ATM 期权的对冲需求最频繁。

Delta 对冲的频率选择是个实操问题。理论上，BSM 模型假设连续对冲（无限频率），但现实中每次对冲都要付交易成本。对冲频率太高，交易成本吃掉利润；太低，Gamma 暴露太大。做市商的做法通常是设一个 Delta 带宽：净 Delta 超过阈值（比如 ±200 股当量）才触发对冲，而不是每分钟都调。具体带宽取决于 Gamma 大小和交易成本，没有通用答案。

## Gamma — Delta 的变化速率

Gamma 是 Delta 对标的价格的二阶偏导，也是期权价格对标的价格的二阶偏导：

$$

$$

其中 $$ 是标准正态分布的概率密度函数在 $$ 处的值。

Gamma 的值恒为正（Call 和 Put 一样）。它在 ATM 附近最大，向两侧迅速衰减。直觉上好理解：平值期权的 Delta 正在从 0 向 1（或从 -1 向 0）快速变化，这个区域 Delta 对标的价格最敏感。深度实值或深度虚值的期权，Delta 几乎不动了，Gamma 自然很小。

![Gamma 随标的价格变化曲线（不同到期日对比）](https://coriva.eu.org/option-greeks-guide/gamma-curve.webp)

图上可以看到一个关键特征：到期日越近，ATM 的 Gamma 越大，但 Gamma 曲线也越窄。临到期的 ATM 期权，标的稍微动一下 Delta 就剧烈变化，对冲难度极大。这就是做市商在到期日前一两天特别紧张的原因，所谓的 **Gamma Risk** 。

Long Gamma 和 Short Gamma 的含义：

- **Long Gamma** （买期权）：标的怎么动你都不吃亏。涨了多赚，跌了少亏（下面的数字例子会算清楚）。问题是每天早上醒来账户里少一点 Theta。
- **Short Gamma** （卖期权）：每天白捡 Theta，直到某天标的来一个大幅跳空，Gamma 把你之前赚的 Theta 全部吐回去还倒贴。2018 年 2 月的 VIX 暴涨事件就是 Short Gamma 的极端后果。

用一个简化的数字说明 Long Gamma 的效果（这里用凑整后的 Delta=0.50、Gamma=0.03 方便心算，不是上面代码的精确输出）：假设你持有 ATM Call，Gamma = 0.03，Delta = 0.50。标的从 100 涨到 105，Delta 变成约 0.65（= 0.50 + 0.03 × 5）。这 5 块钱的上涨，你赚的不是 0.50 × 5 = 2.50，而是大约 (0.50 + 0.65)/2 × 5 = 2.875。多出来的 0.375 就是 Gamma 带来的"凸性收益"。反过来标的跌 5 块，你亏的是约 (0.50 + 0.35)/2 × 5 = 2.125，比线性估计的 2.50 少亏 0.375。涨多赚、跌少亏，这就是 Long Gamma 的好处。但天下没有免费午餐，这个好处的对价是每天被 Theta 吃掉一点时间价值。注意线性 Gamma 近似（ $$ ）只在小幅变动时准确，大幅变动时 Gamma 本身也在变，实际 Delta 会和线性估计有偏差。

## Theta — 时间是期权买方的敌人

看涨期权的 Theta 公式：

$$

$$

Theta 几乎总是负的（期权买方的角度），意味着随着时间流逝，期权价值在减少。ATM 期权的 Theta 绝对值最大，因为 ATM 期权的时间价值最高、衰减空间最大。

![期权价格随到期时间衰减（非线性加速特征）](https://coriva.eu.org/option-greeks-guide/theta-decay.webp)

这张图展示了期权价值随到期时间的衰减路径。衰减不是线性的，最后 30 天加速明显。ATM 期权（蓝线）在前半年衰减不多，但最后一个月里价值急剧缩水：

- 卖方策略（Covered Call、Iron Condor 等）倾向于卖近月期权，因为最后 30 天的 Theta 收割效率最高
- 买方策略尽量避免持有到最后一个月，除非是赌方向性事件（财报、FDA 审批等）

Theta 和 Gamma 之间存在一个跷跷板关系。BSM 模型下，对于 Delta 中性的组合：

$$

$$

当 $$ 接近 0 时， $$ 。含义很直接：Long Gamma 的代价就是负 Theta，Short Gamma 的回报就是正 Theta。不可能同时持有正 Gamma 和正 Theta，风险和收益必须匹配。这个关系式在构建组合策略时极其有用，可以直接检查 Gamma 和 Theta 的配比是否合理。

## Vega — 波动率变一个点值多少钱

$$

$$

注意：上面是"波动率变动 1.0"对应的 Vega。实际使用中通常除以 100，变成"波动率变动 1 个百分点"对应的 Vega，比如波动率从 25% 变到 26% 时期权价格的变化。

Vega 和 Gamma 都在 ATM 处峰值。区别在时间维度上：Gamma 临期爆炸，Vega 越远月越大，波动率需要时间来兑现。

![Vega 随标的价格变化（不同到期日对比）](https://coriva.eu.org/option-greeks-guide/vega-curve.webp)

IV Crush 是 Vega 最直观的实战体现。公司发财报前，期权的隐含波动率（IV）通常被推高到 50%、60% 甚至更高，因为市场预期财报会导致大幅波动。财报公布后，不确定性消除，IV 迅速坍塌回正常水平，可能从 60% 跌到 30%。假设你持有一手 ATM Call，Vega = 0.20，IV 从 60% 跌到 30%，光是波动率下降这一项就让期权价格损失 0.20 × 30 = 6 块钱。即使标的按照你预期的方向涨了，Delta 赚的钱可能还不够覆盖 IV Crush 的亏损。这就是为什么很多人在财报前买入期权、方向猜对了却还亏钱，Vega 是真正的杀手。

## Rho — 利率敏感度

$$

$$

Rho 在大多数场景下可以忽略。短期期权（1-3 个月）的 Rho 很小，利率变动 25 个基点对期权价格的影响不到几分钱。但两种情况下 Rho 不能忽略：

第一种是长期期权（LEAPS），到期时间 1-2 年，Rho 可以到 0.5 以上，利率变动 1% 意味着期权价格变动 $0.50。2022-2023 年美联储加息周期中，长期期权的定价受 Rho 影响不小。

第二种是利率环境快速变化的时期。如果央行在几个月内加息 200-300 个基点，即使中短期期权的 Rho 也会累积出可观的影响。

## 下一篇：二阶Greeks与波动率曲面

有了 Delta、Gamma、Theta、Vega 四个数字，你基本上能回答任何期权持仓的核心问题：标的动了我亏多少？动得猛会怎样？拖一天成本多少？IV 变了影响多大？

但一阶描述是不够的。Delta 本身会随波动率变化（这是 Vanna），也会随时间变化（这是 Charm）；Vega 本身也有"凸性"（这是 Volga）。做市商管理大规模期权组合时，二阶Greeks是必须看的。下一篇会讲这些高阶希腊字母，以及它们在波动率曲面建模中的作用。

系列导航：

- 第二篇： [二阶Greeks详解：Vanna、Charm、Volga 的公式与波动率曲面建模](https://coriva.eu.org/option-greeks-second-order/)
- 第三篇： [期权Greeks实战：策略分析、动态对冲与Gamma Scalping](https://coriva.eu.org/option-greeks-trading/)
- 工具篇： [期权Greeks计算器](https://coriva.eu.org/option-greeks-calculator/)

如果你对量化交易的其他基础指标感兴趣，可以参考 [量化投资常用指标大全](https://coriva.eu.org/quant-metrics-guide/) 。