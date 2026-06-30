---
url: https://coriva.eu.org/how-to-use-yfinance-to-download-stock-p-e-ratio/
title: "用 yfinance 下载股票市盈率"
description: "用 Python yfinance 库获取股票市盈率（P/E Ratio）的完整代码示例。"
author: "Coriva"
published: "2024-12-14T18:52:04+08:00"
language: "zh"
captured_at: "2026-06-30T12:19:58.798Z"
---

# 用 yfinance 下载股票市盈率

市盈率（P/E Ratio）是最常用的估值指标之一，反映投资者愿意为每单位盈利支付的价格。用 Python 的 `yfinance` 库可以很方便地获取这个数据。

---

## 第一步：安装和导入

先安装 `yfinance` ：

```fallback
pip install yfinance
```

导入库：

```fallback
import yfinance as yf
```

---

## 第二步：获取股票数据

通过 `Ticker` 对象获取股票数据，里面包含各种财务统计指标：

```fallback
# 创建 Ticker 对象

stock = yf.Ticker("AAPL")  # 以 Apple 为例
```

`Ticker` 对象包含历史数据、公司信息和关键统计指标。

---

## 第三步：提取市盈率

市盈率在 `info` 属性中：

```fallback
# 获取股票信息

info = stock.info

# 提取远期市盈率

pe_ratio = info.get("forwardPE")

print(f"AAPL 的远期市盈率: {pe_ratio}")
```

### info 中的关键字段

- `forwardPE` ：远期市盈率，基于预测盈利计算。
- `trailingPE` ：滚动市盈率，基于过去 12 个月的实际盈利。

根据需要选择：

```fallback
# 提取滚动市盈率

trailing_pe = info.get("trailingPE")

print(f"AAPL 的滚动市盈率: {trailing_pe}")
```

---

## 第四步：批量获取多只股票的市盈率

用循环批量处理：

```fallback
# 股票代码列表

tickers = ["AAPL", "MSFT", "GOOGL"]

for ticker in tickers:

    stock = yf.Ticker(ticker)

    info = stock.info

    forward_pe = info.get("forwardPE")

    trailing_pe = info.get("trailingPE")

    print(f"{ticker}: 远期 P/E = {forward_pe}, 滚动 P/E = {trailing_pe}")
```

---

## 第五步：处理缺失数据

有些股票可能没有市盈率数据，返回 `None` ，需要做好异常处理：

```fallback
if forward_pe is None:

    print(f"{ticker}: 远期市盈率数据不可用")

else:

    print(f"{ticker}: 远期 P/E = {forward_pe}")
```

---

用 `yfinance` 获取市盈率和其他财务指标非常简单直接，无论是分析单只股票还是整个投资组合都能轻松应对。