---
url: https://coriva.eu.org/option-greeks-second-order/
title: "二阶Greeks详解：Vanna、Charm、Volga 的公式与波动率曲面建模"
description: "二阶期权Greeks公式与Python实现：Vanna、Charm、Volga的数学推导、直觉解释，以及它们如何塑造波动率曲面。"
author: "Coriva"
published: "2026-04-12T23:37:10+08:00"
language: "zh"
captured_at: "2026-06-30T12:19:05.289Z"
---

# 二阶Greeks详解：Vanna、Charm、Volga 的公式与波动率曲面建模

[上一篇](https://coriva.eu.org/option-greeks-guide/) 讲了五个一阶Greeks：Delta、Gamma、Theta、Vega、Rho。用它们做对冲和风控，大多数时候够了，但有个前提：一阶Greeks本身是稳定的。现实是它们一直在变。标的价格动了，Delta变了（这是Gamma）；波动率变了，Delta也变了，这是什么？一阶框架里没有对应的名字。 **二阶Greeks** 就是填这个空的：它们量化一阶Greeks自身的不稳定性。

这是期权Greeks系列的第二篇。覆盖三个最重要的二阶Greeks（Vanna、Charm、Volga），加上它们在波动率曲面建模中的应用。第三篇会讲实战策略。

## 为什么需要二阶Greeks

一阶Greeks的隐含假设是"在其他变量不动的前提下"。Delta告诉你标的价格变 $1 期权价格动多少，但前提是波动率、时间都没变。实际交易中，标的跳了 3%，IV 同时飙了 5 个点，时间又过了一天，三个变量同时在动。一阶近似的误差就出来了。

二阶Greeks可以理解为一阶Greeks的导数。数学上就是期权价格的混合偏导或二阶偏导。BSM模型下所有二阶Greeks都有解析公式，不需要数值微分。

| 二阶Greek | 数学定义 | 直觉含义 |
| --- | --- | --- |
| **Vanna** | $$ | IV 变了，你的 Delta 对冲要调多少 |
| **Charm** | $$ | 过了一天，你的 Delta 自动漂移多少 |
| **Volga** (Vomma) | $$ | Vega 本身的凸性：vol 大幅变动时 Vega 不是常数 |
| **Speed** | $$ | Gamma 的变化率，标的大幅移动时对冲误差来源 |
| **Color** | $$ | Gamma 的时间衰减率 |
| **Ultima** | $$ | Volga 的变化率，极端 vol 环境下才需要关心 |

做市商为什么在意这些？一个持有上千个期权头寸的组合，每天早上算完净 Delta 做了对冲之后，到下午收盘 Delta 已经漂了。Charm 和 Vanna 在暗中推着它动。只看一阶的做市商会发现自己的"Delta neutral"组合其实一直在裸奔。

下面的 Python 代码扩展了 [第一篇](https://coriva.eu.org/option-greeks-guide/) 的 `BSMCalculator` ，加入三个核心二阶Greeks：

```python
import numpy as np

from scipy.stats import norm

class BSMSecondOrder:

    """BSM 二阶Greeks计算（扩展一阶）"""

    def __init__(self, S, K, T, r, sigma):

        self.S = S

        self.K = K

        self.T = T

        self.r = r

        self.sigma = sigma

        self.d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))

        self.d2 = self.d1 - sigma * np.sqrt(T)

    def vanna(self):

        """dDelta/dSigma = dVega/dS"""

        d1, d2 = self.d1, self.d2

        return -norm.pdf(d1) * d2 / self.sigma

    def charm(self, option_type='call'):

        """dDelta/dT（注意符号：T 减小 = 时间流逝）"""

        d1, d2 = self.d1, self.d2

        S, K, T, r, sigma = self.S, self.K, self.T, self.r, self.sigma

        common = -norm.pdf(d1) * (2 * r * T - d2 * sigma * np.sqrt(T)) / (2 * T * sigma * np.sqrt(T))

        if option_type == 'call':

            return common

        return common + r * np.exp(-r * T) * norm.cdf(-d2)  # put charm 需要加利率项

    def volga(self):

        """d²V/dSigma² = Vega 的凸性"""

        d1, d2 = self.d1, self.d2

        S, T, sigma = self.S, self.T, self.sigma

        vega = S * norm.pdf(d1) * np.sqrt(T)

        return vega * d1 * d2 / sigma

# 示例：同样的参数 S=100, K=100, T=0.25, r=5%, sigma=25%

bsm2 = BSMSecondOrder(S=100, K=100, T=0.25, r=0.05, sigma=0.25)

print(f"Vanna:  {bsm2.vanna():.6f}")    # -0.059056

print(f"Charm:  {bsm2.charm():.6f}")    # -0.127956

print(f"Volga:  {bsm2.volga():.6f}")    #  0.479834
```

代码里有个容易混淆的点：Charm 的符号。Charm 定义为 $$ ，T 是"剩余到期时间"。T 减小意味着时间在流逝。所以 Charm 为正代表随着 T 减小（时间流逝），Delta 在增大。有些文献用 $$ （日历时间，和 T 方向相反），符号就反过来了。这里用的是 T 的约定，和大多数教材一致。

## Vanna — Delta对波动率的敏感度

Vanna 有一个漂亮的对称性：它既是 Delta 对波动率的偏导，也是 Vega 对标的价格的偏导。

$$

$$

这个对称性不是巧合，是混合偏导的交换律（Schwarz 定理）。但它给了两个完全不同的交易直觉：

**从 Delta 角度看** ：IV 涨了，你的 Delta 对冲需要调整。假设你持有 OTM Call（Delta=0.25），Vanna 为正。IV 突然飙升 10 个点，你的 Delta 不再是 0.25，而是更高，你比预期更多地暴露在标的上涨的风险中。做市商在 IV 剧烈波动的时候会发现自己的"Delta neutral"组合突然不中性了，调整的幅度就由 Vanna 决定。

**从 Vega 角度看** ：标的涨了，你的 Vega 暴露也变了。Vanna 告诉你 Vega 怎么随着 spot 变化。OTM 期权的 Vega 比 ATM 小，标的朝着行权价方向动的时候 Vega 在增大，这就是 Vanna 在起作用。

![Vanna 随标的价格和波动率变化的热力图](https://coriva.eu.org/option-greeks-second-order/vanna-surface.webp)

Vanna 在 ATM 附近穿过零点，ATM 期权的 Delta 对波动率不太敏感（Vanna ≈ 0）。偏离ATM之后 Vanna 的绝对值增大，OTM 和 ITM 区域的 Delta 才真正受波动率影响。这解释了一个实盘现象：ATM straddle 的 Delta 在 IV 变化时相对稳定，但 OTM wings 的 Delta 会随 IV 大幅漂移。

**Vanna 在 Skew Trading 中的角色** ：做 Risk Reversal（买 OTM Call + 卖 OTM Put）时，你拿到的不只是方向性头寸。如果 skew 很陡，Put 的 IV 远高于 Call 的 IV，Risk Reversal 的 Vanna 暴露很大。IV 整体上移时，高 IV 那侧的 Delta 变化更剧烈，组合的净 Delta 会偏移。忽略 Vanna 的交易者会以为自己做的是纯方向博弈，其实 vol-of-vol 风险已经悄悄进来了。

## Charm — 隔夜Delta漂移

$$

$$

Charm 量化的是"什么都没发生，光是时间过了一天，Delta 变了多少"。这在实操中非常实际：你今天收盘前把组合调到 Delta neutral，明天开盘标的没动，你的净 Delta 已经偏了。偏多少？Charm × 1天。

![Charm 随标的价格变化（不同到期日对比）](https://coriva.eu.org/option-greeks-second-order/charm-curve.webp)

Charm 的特征和 Gamma 有关联。ATM 附近的 Charm 绝对值通常很大，因为这是 Delta 变化最快的区域，时间流逝对 Delta 的推力也最强。OTM Call 的 Charm 通常为负（Delta 随时间流逝而减小，期权越来越可能到期虚值），ITM Call 的 Charm 为正（Delta 向 1 靠近）。

**周五效应** ：周五收盘到周一开盘跨越整个周末。做市商在周五下午特别关注 Charm，因为周末期间完全无法调仓，但 Delta 在静默中漂移两天。一个典型场景：你持有大量临到期 OTM Call，周五 Delta 还可控，周一开盘发现 Delta 显著下降，期权离到期更近了，OTM 的概率更低了。如果不提前考虑 Charm 预调仓位，周末就是在赌博。

Charm 在临到期时尤其凶猛。和 Gamma 一样，它在 T → 0 时会发散。最后一两天，ATM 附近期权的 Delta 可以每小时肉眼可见地在变，不是因为标的在动，而是时间本身在推它。

## Volga (Vomma) — Vega的凸性

$$

$$

Volga 衡量的是 Vega 本身对波动率的敏感度。一阶分析里你用 Vega 估算 “IV 涨 1 个点期权价格涨多少”，但如果 IV 涨了 10 个点呢？线性外推靠谱吗？Volga 告诉你偏差有多大。

![Volga 随标的价格变化（不同 moneyness 对比）](https://coriva.eu.org/option-greeks-second-order/volga-curve.webp)

Volga 的分布有一个显著特征： **ATM 期权的 Volga 接近零，OTM 期权（特别是 deep wings）的 Volga 很大** 。

为什么？ATM 期权的 $$ 接近 0， $$ 也接近 0， $$ ，所以 Volga ≈ 0。ATM 期权的 Vega 对 IV 变化基本是线性响应，IV 涨 5 点和涨 10 点，Vega 带来的收益几乎是正比的。

OTM 期权不一样。它们的 $$ 和 $$ 都很大，Volga 远大于零。IV 涨了，OTM 期权的 Vega 本身也在增大（因为 OTM 期权在 IV 更高的环境里更"接近"ATM），所以 IV 大幅上涨时 OTM 期权的价格涨幅比线性 Vega 估计的更多。

这直接解释了期权市场一个经典现象： **OTM 期权（特别是 OTM Put）为什么那么贵** 。除了 skew 的方向性因素，Volga 是结构性原因。持有 OTM Put 的人买到了"波动率的波动率"。如果市场恐慌（VIX 从 15 涨到 40），OTM Put 的涨幅远超线性 Vega 预测值，因为 Volga 放大了收益。卖出 OTM Put 的做市商知道这一点，所以要收 Volga premium 来补偿这个凸性风险。

## 二阶Greeks交互矩阵

一阶和二阶 Greeks 构成一个清晰的偏导数网络。行是被求导的一阶量，列是求导方向：

|  | 对 $$ | 对 $$ | 对 $$ |
| --- | --- | --- | --- |
| **Delta** ($$) | Gamma ($$) | Vanna ($$) | Charm ($$) |
| **Vega** ($$) | Vanna ($$) | Volga ($$) | Veta ($$) |
| **Theta** ($$) | Charm ($$) | Veta ($$) | — |

对称性立刻跳出来了：矩阵关于对角线对称（Schwarz 定理）。Vanna 出现两次，Charm 也出现两次，Veta 也出现两次。这意味着你不需要记住所有交叉导数，记住对称性就够了。

对 Call 和 Put 的差异：Gamma、Vanna、Volga 对 Call 和 Put 相同（和一阶的 Gamma、Vega 一样的道理，Put-Call Parity 右边的 $$ 对这些变量的高阶导为零）。Charm 对 Call 和 Put 不同，因为 Put-Call Parity 右边对 $$ 和 $$ 的混合偏导不是零。

## 波动率曲面与二阶Greeks

BSM 模型假设所有行权价和到期日的隐含波动率都一样，就是一个常数 $$ 。现实中一看期权链就知道这不对：OTM Put 的 IV 高于 ATM，形成 volatility smile 或 skew。

![典型的波动率微笑与偏斜曲线](https://coriva.eu.org/option-greeks-second-order/vol-smile.webp)

Smile 和 Skew 为什么存在？本质上是因为标的资产的真实分布不是正态的，有肥尾和负偏。市场给 OTM Put 定更高的 IV，补偿尾部风险。但从定价机制上看，Vanna 和 Volga 正是 vol surface 形状的两个核心驱动力。

**Vanna-Volga 定价法** 是对 BSM 的一种实用修正。思路很简洁：用 BSM 算 ATM 期权价格（BSM 在 ATM 附近很准），然后用 Vanna 和 Volga 做调整，把 smile 的影响加回来。

$$

$$

这不是严格推导，而是一个一阶/二阶泰勒展开的近似。但在 FX 期权市场（外汇市场），Vanna-Volga pricing 几十年来一直是标准做法，因为外汇的 vol surface 相对平坦，二阶近似足够精确。

```python
def vanna_volga_adjustment(S, K, T, r, sigma_atm, sigma_k, bsm2):

    """

    Vanna-Volga 定价调整（简化版）

    sigma_atm: ATM 隐含波动率

    sigma_k: 行权价 K 的市场隐含波动率

    bsm2: BSMSecondOrder 实例（用 sigma_atm 初始化）

    """

    delta_sigma = sigma_k - sigma_atm

    vanna_adj = bsm2.vanna() * delta_sigma

    volga_adj = 0.5 * bsm2.volga() * delta_sigma**2

    return vanna_adj + volga_adj

# 例：ATM vol=25%, OTM Put (K=90) 的市场 vol=30%

bsm2_k90 = BSMSecondOrder(S=100, K=90, T=0.25, r=0.05, sigma=0.25)

adj = vanna_volga_adjustment(100, 90, 0.25, 0.05, 0.25, 0.30, bsm2_k90)

print(f"Vanna-Volga adjustment: {adj:.4f}")  # 正值：OTM Put 比 BSM 定价贵
```

**Sticky Strike vs Sticky Delta** ：vol surface 不仅有形状，还有动态行为。标的价格从 100 涨到 105，K=100 的期权从 ATM 变成了 OTM。它的 IV 怎么变？

- **Sticky Strike** ：K=100 的 IV 不变。Vol surface “钉"在行权价上。标的动了，你沿着 moneyness 轴移动了位置。大多数股票期权市场接近这个模式。
- **Sticky Delta** ：保持相同 Delta 值的期权 IV 不变。Vol surface “钉"在 Delta 上。标的动了，整个 surface 跟着平移。部分 FX 市场接近这个模式。

两种模式下 Vanna 的对冲方式完全不同。Sticky Strike 下，spot 变动不直接改变 IV，Vanna 的影响主要通过 Delta 变化间接体现。Sticky Delta 下，spot 变动伴随 IV 变动，Vanna 暴露被双倍放大。不区分这两种模式就做 Vanna 对冲，是很多量化策略亏钱的隐性原因。

## 下一篇：Greeks实战交易应用

二阶Greeks给了你一个更精细的风险镜头。Vanna 让你预判 IV 变化对 Delta 的冲击，Charm 让你提前处理隔夜漂移，Volga 让你理解 wings 定价的结构性原因。

但知道这些数字只是第一步。怎么用它们构建策略、做动态对冲、实操 Gamma Scalping？下一篇是这个系列的最后一篇： [期权Greeks实战：策略分析、动态对冲与Gamma Scalping](https://coriva.eu.org/option-greeks-trading/) 。

系列回顾：

- 第一篇： [期权Greeks完全指南：Delta、Gamma、Theta、Vega 的公式与代码实现](https://coriva.eu.org/option-greeks-guide/)
- 第三篇： [期权Greeks实战：策略分析、动态对冲与Gamma Scalping](https://coriva.eu.org/option-greeks-trading/)
- 工具篇： [期权Greeks计算器](https://coriva.eu.org/option-greeks-calculator/)

如果你对量化交易的其他基础指标感兴趣，可以参考 [量化投资常用指标大全](https://coriva.eu.org/quant-metrics-guide/) 。