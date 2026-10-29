# AI 自动交易产品的 Alpha 从哪来（2026-10-10 调研）

> 研究证据，2026-10-10，不是已批准的策略变更。只读调研；未打开 crack / extracted_data / 安全报告类材料。WebSearch 不可用，网页证据靠直接抓取 + Wayback 存档 + HN Algolia。
> 术语：**Alpha** = 扣除大盘方向和全部成本后剩下的收益；**Beta** = 跟着大盘涨跌拿到的收益。证据等级：AUDITED（第三方审计或可独立复核）/ LIVE（真钱，公开，未审计）/ BACKTEST（回测）/ MARKETING（截图、排行榜、自述）/ NONE（没有）。

## (1) 总表

| 产品 | 信号来源 | 宣称的优势 | 证据等级 | 谁赚钱 |
|---|---|---|---|---|
| AstraQuant（R20） | LLM 读指标快照（EMA/RSI/量比），OKX 永续，提示词追求高胜率 | “机构级”；截图 1 天 5 笔胜率 80%、7 倍杠杆 | MARKETING；owner 的模拟盘台账 43 笔，扣费后净 −12.25U，扣大盘后截距 −0.019%（不显著），利润来自模拟盘失真成交价 | 平台：OKX 经纪商标签写死 `6e2191f027c6SUDE`（`_refs/r20-quantum-trader/scripts/okx_rest.py:49`）；README 邀请码表里 Binance/Gate 是“Partner Promotion Only (No API Trading)”（`README.md:60-68`） |
| NOFX / VergeX | “the strategy is a language model”（`_refs/nofx/README.md:26`）+ AI500/持仓量排行选币；默认 10 倍杠杆 | 公开排行榜“by realized return” | MARKETING：只收自愿展示的账户、取前 50、含未实现盈亏、不随充提同步（`manager/trader_manager.go`、`docs/pnl.md`）。逐笔账本：NONE | 平台：Hyperliquid 建造者费 `Fee: 50` = 每边 0.05%，用户在交易所费用之外额外付（`_refs/nofx/trader/hyperliquid/trader.go:62-73`）；9 家交易所邀请链接；数据/LLM 按次收费 |
| OpenAlice | 不产生信号；把用户自己的智能体接到数据和券商，人工按 “Trading as Git” 审批 | 不宣称；“no guarantees of ... profitability”、执行功能是 beta（`_refs/OpenAlice/README.md`） | NONE | 仓库里没找到返佣或经纪商标签（grep `src/`、`packages/`）；盈利模式未核实 |
| Vibe-Trading（HKUDS） | 研究系统：LLM 生成策略代码，Alpha Zoo 约 460 个因子，带回测和券商连接器 | 宣称能力，不宣称收益；有严格的 alpha-bench 随机对照、可校验的运行卡（`_refs/Vibe-Trading/README.md` 2026-06-01、2026-09-27 条） | BACKTEST。自己的 changelog 多次修过让回测失真的 bug（2026-08-31 股息未复权，2026-08-26 黄金回测零摩擦） | 无（MIT、学术实验室） |
| nof1 Alpha Arena S1（2025-10-17→11-03） | 6 个 LLM 各 1 万美元真钱，Hyperliquid 永续，6 个大币，只给价格和指标，可加杠杆 | 定位是基准测试，不是产品 | LIVE，链上可查；但 n=6、17 天、一种行情。闭幕前约 4.5 小时的存档数据：Qwen3 +18.4%、DeepSeek +2.8%、Claude −34.0%、Grok −49.7%、Gemini −59.3%、GPT-5 −64.2%，总权益 41,392/60,000，合计 **−31%**（[存档 API](https://web.archive.org/web/20251103172939id_/https://nof1.ai/api/leaderboard)）。流传的最终数字 Qwen 约 +22% 未核实 | nof1（流量、等候名单）、Hyperliquid（手续费） |
| Freqtrade / FreqAI | 用户自写指标策略；FreqAI 用用户给的特征和标签训练 ML 模型，并定期重训（[freqai.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/freqai.md) L46） | 不宣称；“educational purposes only”；示例策略“not ready to use” | BACKTEST 工具；文档警告超参搜索得到的低 p 值“not by itself proof of a genuine edge”（docs/backtesting.md L478） | 不赚钱：FreqAI 明确“not-for-profit, no token, does not sell signals”（freqai.md L10） |
| Hummingbot | 不做预测：做市（PMM、Avellaneda）、跨所做市、套利、网格、TWAP | 吃价差和交易所补贴；“$34 billion in trading volume”（[README](https://github.com/hummingbot/hummingbot)） | 只有成交量，没有用户盈亏 → 盈利方面算 MARKETING | 基金会：交易所经纪商合作和赞助（Binance 返 10%、Gate/KuCoin 返 20%），按交易量收钱 |
| 3Commas / Cryptohopper / Pionex | DCA、网格、信号机器人、跟单；“AI”是加价档位：3Commas QuantPilot，Cryptohopper Hero 档“A.I. strategies” | 定价页没有收益声明 | NONE | 平台订阅：3Commas 每月 15/38/105 美元（[pricing](https://3commas.io/pricing)）；Cryptohopper 每月 29/69/129 美元，跟单另收 9.99–99.99 美元（[pricing](https://www.cryptohopper.com/pricing)）。Pionex 本身是交易所，网格每次成交都收手续费（费率页 403，未核实） |
| ai16z / ElizaOS | “AI 智能体主导的风投基金” | 代币叙事 | NONE。代币 2025-01-02 峰值约 2.47 美元、市值超 26 亿，到 3 月跌 92%；集体诉讼 1:26-cv-03238（SDNY）指控“Marc AIndreessen”智能体其实由人操作（[claimdepot](https://www.claimdepot.com/cases/ai16z-class-action-alleges-founders-faked-ai-agent-misled-investors-in-26b-crypto-scheme)，属指控） | 代币发行方和早期持有人。Virtuals 的交易表现未检索到（未核实） |
| Numerai / Signals | 众包 ML 预测 + Signals；先按 Numerai 已有因子中性化，只奖励“原创”的部分（[signals 文档](https://docs.numer.ai/numerai-signals/signals-overview)）；质押 NMR，打分为负就销毁（[staking 文档](https://docs.numer.ai/numerai-tournament/staking)） | 集成大量弱相关信号 | LIVE，自报：2024 年净收益 25.45%、Sharpe 2.75、AUM 4.5 亿美元、JPMorgan 承诺 5 亿美元容量（[博客](https://blog.numer.ai/jpmorgan-secures-500m-capacity/)）。本次未见审计报告 | 基金（管理费和业绩费）和排名靠前的质押者 |
| 专业加密机构 | 做市：价差 + maker 返佣 + VIP 费率档 + 项目方借币和期权合约；资金费/基差套利；趋势 | — | 资金费 carry 年化平均超 10%、峰值 60%，来自“trend-chasing ... smaller investors seeking leveraged upside”（[BIS WP1087](https://www.bis.org/publ/work1087.htm)）；时间序列动量和关注度能预测收益（[NBER w24877](https://www.nber.org/papers/w24877)，学术研究、未扣成本） | 机构自己。反例：SEC 2024-10-09 起诉 Gotbit、ZM Quant、CLS Global 向项目方出售“manipulation-as-a-service”刷量（[SEC 2024-166](https://www.sec.gov/newsroom/press-releases/2024-166)）。做市细节属常识，本次未核实 |

## (2) 规律：哪些优势真的有证据

1. **“LLM 读指标、自己定方向”的方案，没有一处有正 Alpha 的证据。** 唯一真钱、可审计的大规模测试是 Alpha Arena S1，6 个前沿模型合计亏约 31%。主办方自己承认“early successes may be the result of luck”，并指出：模型对提示词的微小改动高度敏感；自报的信心分数“decoupled from actual trading performance”；早期“PnL was dominated by trading costs ... over-traded ... tiny gains that fees erased”（[TechPost1 存档](https://web.archive.org/web/20251119000000id_/https://nof1.ai/blog/TechPost1)）。nof1 现在的首页直接写“LLMs are shockingly bad at navigating financial markets ... minimal signs of improvement between releases”（https://nof1.ai/ ，2026-10-10 抓取）。S1 的赢家和输家主要差在仓位大小、杠杆和多头偏好，属于行为差异，不是信息优势。AstraQuant 和 NOFX 的结构和 S1 一样（指标快照 → LLM → 带杠杆的永续），证据只有营销材料或亏损的模拟盘。
2. **能确定在赚钱的，几乎都是平台，而且收入和用户盈亏脱钩。** 收入来源包括：经纪商标签和返佣（R20、NOFX、Hummingbot）、建造者费（NOFX 每边 0.05%）、订阅（3Commas、Cryptohopper）、交易所手续费（Pionex）、代币（ai16z）。这些收入都随交易量增长，所以平台的激励是让用户多交易，而不是让用户赚钱。
3. **有证据的 Alpha 都有可识别的对手方或结构性来源，而不是“更聪明的判断”：**
   - (a) 资金费和基差 carry：对手方是追涨、加杠杆的散户多头，有 BIS 论文支持；
   - (b) 做市价差和返佣：对手方是吃单者，要靠延迟优势、资本和交易所补贴，还有被逆向选择的风险；
   - (c) 时间序列动量：有学术证据，但周期是周级，并且没有扣山寨币永续的成本；
   - (d) Numerai 式的大量弱信号集成：前提是数千只股票的广度、先做因子中性化再奖励原创、用质押过滤噪声。它是唯一有大型机构背书（自报，未审计）的“AI 众包 Alpha”，但市场是股票、规模是机构级。
4. **评估方法本身决定可信度。** Freqtrade 和 Vibe-Trading 都专门警告或修复过回测假象：多重检验、复权、零摩擦。排行榜和截图都有幸存者偏差。R20 那份台账里看起来赚钱的部分，正是模拟盘的失真成交价。

## (3) 对小规模（约 500–5,000 USDT）LLM 山寨币永续交易者，什么能借鉴

- **别把 LLM 当信息优势。** 只给价格和指标的 LLM 方向判断，在真钱测试里平均为负。LLM 的价值应该放在：执行纪律（按预先写好的退出计划执行，S1 里 Claude 连续 443 次评估坚持原计划）、过滤明显的坏单、把结构化证据综合成一个判断。前提是证据里有它真正的信息来源。
- **成本先于信号。** S1 早期就是被手续费吃掉的。约 0.24% 的来回成本、建造者费、每 15 分钟一次的 LLM 调用费，在 500U 账户上可能直接超过任何优势。要做的是：降低交易频率、尽量挂单（maker）入场，并把 LLM 调用费计入每笔交易的成本。
- **押有对手方的结构性来源，并且用自己能算的数据先验证：**
  - (i) 资金费和拥挤度：资金费极高时说明多头拥挤、崩盘风险上升（BIS），可以作为逆向或回避的过滤条件，不需要现货腿；
  - (ii) 强平或止损连环后的回补，对手方是被迫成交的人（理论最站得住，但需要强平数据，见 `docs/research/trading-strategy/COMPETITOR_PNL_SOURCE_ASTRAQUANT_VERGEX_2026-10-09.md` §4）；
  - (iii) 日线到周线级趋势、按波动率缩放仓位（NBER），但 500U 的分散度不够，回撤会大。
  - 不适合：做市和返佣（拿不到费率档和延迟优势）；Numerai 式集成（需要广度和数据规模）。
- **借鉴 Numerai 的思路，而不是它的规模：** 先把信号按已知因子（BTC beta、动量、资金费）中性化，只保留“原创”的残差去评估；用“规则冻结后的前推收益”来给信号打分，不看自报的信心。
- **证据标准：** 只认真实成交；扣成本和同期大盘；覆盖全部交易，不挑样本；样本要几百笔、跨三种行情。去掉最赚的 5% 交易后仍为正，才算有优势（检查清单同上述文档 §6）。不要把任何排行榜、截图或模拟盘成交价当证据。
- **商业层面要警惕：** 经纪商标签、建造者费、订阅这些是平台的生意，不是交易优势。如果产品收入随交易量增长，就和“让用户少交易、交易得更好”这个目标冲突。

**未核实或局限：** Alpha Arena S1 的最终数字（我用的是闭幕前约 4.5 小时的存档）和 S1.5/S2 的结果；Pionex 费率；Virtuals 的交易表现；Numerai 是否经过审计；做市商的具体经济账。Alpha Arena 只有 6 个账户、17 天，不能据此断言 LLM 永远做不到，只能说明“目前没有证据表明能做到”。
