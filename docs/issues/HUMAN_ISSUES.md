1. 关于 Trade & position 的页面，显示如下：
Okx Demo · USDT Futures
Lev
BTC/USDT	Short	0.00	59,544.40	59,779.90	1x	—	—	-0.08
PEPE/USDT	Short	9,000,000.00	0.00	0.00	1x	—	—	-0.08
ADA/USDT	Short	159.00	0.14	0.14	1x	—	—	-0.16
XRP/USDT	Short	42.00	1.04	1.04	1x	—	—	-0.25
ETH/USDT	Short	0.06	1,560.95	1,568.65	1x	—	—	-0.36
BNB/USDT	Short	0.03	562.73	561.20	1x	—	—	+0.06

但是实际上使用的时候，这边明明看到他申报里的标识并不是期货的标识，但上面还是显示“USDT futures”。这个时候需要正确检查一下。

2. 在 Overview 页面上，第一排有 5 个卡片。第一个卡片 Account Value 下方会显示不同交易所的持仓信息。

我希望把这个持仓信息做成可折叠的，或者只有在点击卡片时才展开显示具体内容。

不然的话，为了跟第一个卡片的垂直长度保持一致，剩余的 4 个卡片（Today P&L、Unrealized、Realized 和 Exposure）的下方都会留出一个较大的空档。这是 UI 上的 bug，需要修复。

3. 关于 Market Observer 的发现中，有一个很蓝的 Synthesis，里面显示了具体的推断信息。

我希望这个推断信息可以更格式化、更具体，可以多加一些换行或小标题，把判断分割开，让人一眼能够看出具体的内容是什么，而非把所有内容都聚合在一段里面。人类很难从中提取到具体的内容。
Thesis

ADA/USDT exhibits multi-timeframe bearish setup with consistent downside momentum and deeply oversold daily RSI. Price is below SMA50 on 1d (0.14 < n/a reference level), 4h momentum is -3.17%, and daily momentum is severely negative at -16.87%. Daily RSI 21.9 signals capitulation-level oversold conditions, and macro headwinds (Dollar Index RSI 73.7, VIX +2.44% 5d, risk-off sentiment) support downside. Prediction market probability +18.0pp momentum is contrarian noise given on-chain reality. Historical edge shows short strategy dominance (47.7% win, +1.28% avg). Volume is unusually suppressed across all timeframes (Vol z negative: -0.89h, -1.24 4h, -2.48 1d), reducing immediate execution risk but indicating sideways consolidation risk. Commitment to short is warranted by directional confluence, though thin 1h volume and price stalling at 0.14 (10-bar identical closes) suggest waiting for volume confirmation before entry. Observations: 1d RSI 21.9 + Mom10 -16.87%: extreme oversold with severe downside momentum; price locked at 0.14 across last 10 1h closes signals capitulation low or consolidation trap. | Price below SMA50 on 4h (0.14 < 0.16) and 1d (0.14 vs n/a); 4h momentum -3.17%: confirmed downtrend across intermediate timeframes. | All volume z-scores negative (-0.89 to -2.48): unusually low volume across all timeframes; reduces fill confidence but suggests institutions are absent—lower slippage on short entry if conviction trig | DXY RSI 73.7 (overbought) + VIX +2.44% 5d: macro risk-off environment supports ADA weakness; ADA ranks -2.98% 24h relative strength. score=68/100

4. 另外，需要修复这个框架的 News Intel 功能：常规的可以通过 yfinance 或者是其他一些 web-based 的 news source 去获取信息。剩余的部分，可以参考一下我们隔壁的竞争对手 Open Alice 是如何做这一块的实现的

5. 这边注意到在 Risk Limit 的配置选项中（如数字选项 Max Daily Loss），原本的值是 1，如果想要改成 3，必须要先输入 3，再单独删除 1 才行。

这应该是做了最小值限制，但很影响实际 UI 的输入体验。请看一下能不能在保证最小值校验的情况下做一下优化。

比如，可以把最小值校验放在“保存”按钮上：按下保存按钮时，再检查数值是否合法；如果不合法，提示用户重新进行输入。
