---
url: https://coriva.eu.org/option-greeks-calculator/
title: "期权Greeks计算器：在线计算 Delta、Gamma、Theta、Vega"
description: "在线期权Greeks计算器：输入参数即时计算 Delta、Gamma、Theta、Vega 等一阶和二阶Greeks，附可视化曲线图。基于 Black-Scholes 模型。"
author: "Coriva"
published: "2026-04-14T16:55:20+08:00"
language: "zh"
captured_at: "2026-06-30T12:17:36.951Z"
---

# 期权Greeks计算器：在线计算 Delta、Gamma、Theta、Vega

调参数，看 Greeks 怎么变。基于 Black-Scholes 模型，支持一阶Greeks（Delta、Gamma、Theta、Vega、Rho）和二阶Greeks（Vanna、Charm、Volga）。拖动滑块，曲线和数值实时更新。

公式和直觉解读见 [期权Greeks完全指南](https://coriva.eu.org/option-greeks-guide/) ，二阶Greeks见 [Vanna、Charm、Volga 详解](https://coriva.eu.org/option-greeks-second-order/) ，实战应用见 [Greeks实战](https://coriva.eu.org/option-greeks-trading/) 。

Delta

0.5371

Gamma

0.0554

Theta/day

\-0.0544

Vega/1%

0.1139

Rho/1%

0.0416

Price

3.06

二阶Greeks

Vanna/1%

\-0.0003

Charm/day

\-0.00062

Volga/1%²

0.0009

## 公式基础

计算器基于 Black-Scholes 模型。核心是两个中间变量 $$ 和 $$ ：

$$

$$

所有一阶和二阶Greeks都是 $$ 、 $$ 和标准正态分布函数 $$ 、 $$ 的组合。具体公式推导见 [期权Greeks完全指南](https://coriva.eu.org/option-greeks-guide/) 。

二阶Greeks的含义：Vanna 衡量 Delta 对波动率的敏感度，Charm 衡量 Delta 随时间的衰减速度，Volga 衡量 Vega 对波动率自身的凸性。详细解读见 [二阶Greeks详解](https://coriva.eu.org/option-greeks-second-order/) 。

## 使用建议

拖动标的价格 $$ 的滑块，观察 Delta 从 0 到 1（Call）的 S 型变化。然后切换到 Gamma 视图，注意 Gamma 在 ATM 处达到峰值，随到期日缩短峰值急剧升高。这就是做市商在到期日前最紧张的原因：短期 ATM 期权的 Gamma 风险最大。

调低 T（到期天数）到 5 天以内，再看 Theta：时间衰减在最后几天加速，这就是 [Greeks实战](https://coriva.eu.org/option-greeks-trading/) 中讨论的 Theta 非线性衰减。

## 系列导航

- [期权Greeks完全指南：Delta、Gamma、Theta、Vega 的公式与代码实现](https://coriva.eu.org/option-greeks-guide/)
- [二阶Greeks详解：Vanna、Charm、Volga 的公式与波动率曲面建模](https://coriva.eu.org/option-greeks-second-order/)
- [期权Greeks实战：策略分析、动态对冲与Gamma Scalping](https://coriva.eu.org/option-greeks-trading/)
- **本篇：期权Greeks计算器**