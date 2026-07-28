# Market Context Eval — 为什么 Aletheia 永远输出 neutral / speculative

**日期**: 2026-06-08 · **范围**: market_observer 数据供给与提示词工程
**触发**: dashboard Markets 页所有 opportunity direction=neutral, confidence=speculative
**结论提前**: 不是 LLM 没在跑(codex_cli healthy),是 **prompt 输入维度=1**(只有 24h_change)
导致模型按基率回答。证据 + 业界对照 + 适配方案见下。

---

## 0. 一句话定位

> 我们当前喂给 LLM 的市场上下文 ≈ 一行字符串「BTC 24h_change=0.05%」。
> 不是模型保守 — **是数据底盘只有一维**。模型最大化"诚实输出"就是 neutral。
> 修这个问题不靠改 prompt,而是补**数据序列化层**(预计算指标 + 短窗时间序列 +
> markdown 表格 + 结构化输出 schema)。

---

## 1. 现状审计 — 我们的市场上下文有多薄

### 1.1 当前喂给 LLM 的全部输入

`aletheia/agents/market_observer.py:38-47`:

```python
context_lines = []
for sym in symbols:
    snap = latest_snapshot(sym)  # 取 SQLite 里最新一行 MarketSnapshot
    if snap:
        context_lines.append(f"{sym}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%")
    else:
        context_lines.append(f"{sym}: no snapshot yet")
context = "\n".join(context_lines)
```

实际 prompt 输入(3 个 symbol):
```
BTCUSDT: price=61777.34 24h_change=0.05%
ETHUSDT: price=2390.10 24h_change=0.07%
SOLUSDT: price=148.30 24h_change=0.03%
```

system prompt 还告诉模型 *"Be conservative: use neutral / speculative unless the data
clearly supports a directional read"*。**1 维输入 + 保守指令** → 输出永远 neutral。

### 1.2 已经能拿到、但没上桌的数据

| 维度 | 已有 API | 当前是否进 prompt |
|---|---|---|
| 多时间框架 OHLCV | `BitgetClient.get_ohlcv(symbol, timeframe=1m/5m/15m/1H/4H/1D)` ✅ | ❌ |
| volume 24h | 已存 `MarketSnapshot.volume_24h` ✅ | ❌ |
| 历史 snapshot 序列 | `list_market_snapshots(symbol, limit)` ✅ | ❌ |
| 永续 funding rate | Bitget API 有 `/api/v2/mix/market/current-fund-rate` ❌ 未封装 | ❌ |
| open interest | Bitget API 有 ❌ 未封装 | ❌ |
| orderbook depth | Bitget API 有 ❌ 未封装 | ❌ |
| 跨资产相对强度 | 派生(已有 BTC/ETH/SOL 全 ticker) ✅ | ❌ |

**关键发现**: `get_ohlcv` 已经实现且**已经验证可用**(本 eval 实测 1h 5 根抓回成功),
我们却只用了 24h_change 这一个标量。**最大的提升是把已有数据上桌**,不需引新源。

### 1.3 依赖约束

- `pyproject.toml` 只有 stdlib + httpx + pydantic + apscheduler。**无 numpy / pandas / ta-lib**。
  → 指标计算必须用 stdlib(`statistics` 模块够用)。
- 无网络新源(red line: AGENTS.md 禁止 web ingestion / news scraping)。
- 不能动 change-controlled(client.py 已经够,不再扩接口面)。

---

## 2. 业界对照 — 三个标杆怎么做

> OpenAlice 与 TradingAgents 的详研源笔记已被本文吸纳，不再作为独立权威文件保留。
> VergeX **未公开**(技术细节
> 仅营销页,docs.vergex.ai 证书故障)→ 不可作为参考样本。

### 2.1 OpenAlice — 工具化按需拉取,不预填上下文

- **数据维度**: equity 基本面 + 技术指标 DSL(`SMA/RSI/BBANDS/MACD/ATR/RVOL/OBV/MFI/VWAP`)
  + 多时间框架(`1d/1w/1h/1m`) + ETF/sector rotation + 宏观(FRED/BLS/EIA) + 新闻归档。
  **无 orderbook depth / funding / on-chain / social(社交在子模块外挂)**。
- **架构选择**: 不预填 prompt 上下文 — 每个数据维度封装成 AI SDK tool(`tool.ts`),
  让宿主 CLI(Claude Code/Codex)自己 tool-calling 拉。
- **提示词系统**: persona 5 行身份;真重量在 **tool description**(每个工具几百字 mini-prompt 教
  模型怎么用)+ **SKILLs**(SKILL.md 给"价值链分解→短名单→双侧论证"等 procedure)。
- **保守程度**:
  - 强制 "go to the data, don't answer from memory"
  - tool 描述里坦白局限("treat as right-side confirmation, NOT alpha")
  - **不要求 long/short/neutral 输出** — 框架是 "left-side / right-side / neither",
    避开二元方向票
  - falsifiable thesis(每论点列 *what must be true* + *disconfirming signals*)
    = 隐式置信度 + 可执行反证
- **教训**: OpenAlice 的"不要求方向票"在我们这里**不能照抄** — Aletheia 必须给
  `Opportunity.direction_bias` 一个枚举值(因果链下游 paper_trader 需要)。但
  *"falsifiable thesis"* 思路可借鉴。

### 2.2 TradingAgents — 预计算 + markdown 表格 + 结构化输出(最直接对标)

源:`tradingagents/agents/analysts/market_analyst.py:18-65`、
`dataflows/y_finance.py:54-180`、`dataflows/market_data_validator.py:55-130`、
`agents/schemas.py`。

**三种数据交付形态**(关键):

1. **CSV 形态** — header 注释 + 标准 CSV 喂 OHLCV 给模型。
2. **单指标时间序列文本** — 每行 `日期: 数值`,后面附**用法字典**:
   ```
   ## rsi values from 2024-01-01 to 2024-03-01:
   2024-03-01: 62.34
   2024-02-29: N/A: Not a trading day
   2024-02-28: 61.10
   ...
   RSI: Measures momentum to flag overbought/oversold conditions.
   Usage: Apply 70/30 thresholds and watch for divergence to signal reversals.
   Tips: In strong trends, RSI may remain extreme; cross-check with trend.
   ```
3. **防幻觉验证表**(回应 issue #830 — LLM 编造"曾回踩 boll_lb"):

   ```markdown
   ## Verified market data snapshot for AAPL
   ### Latest verified OHLCV row
   | Field | Value |
   | Open | 187.15 |
   | Close | 185.64 |
   ...
   ### Verified technical indicators (latest row)
   | Indicator | Value |
   | close_50_sma | 184.22 |
   | rsi | 62.34 |
   ...
   ### Recent verified closes (last 30 rows)
   | Date | Close |
   ...
   Use this snapshot as the source of truth for exact OHLCV.
   If another tool conflicts with it, flag the discrepancy rather than inventing.
   ```

   prompt 强制"任何精确数值声明必须以此为准"。

**提示词工程要点**:
1. **指标白名单 + 字典**: 14 个指标的定义/场景/坑列在 system prompt,要求"选 ≤8 个互补的"。
2. **强制工具调用顺序**: "call get_stock_data first ... then get_indicators"。
3. **诚实兜底**: "Do not claim historical validation unless directly supported by tool output."
4. **统一收尾**: 报告末尾必须附 markdown 表格;最终决策 `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`(全队约定的停止信号)。

**结构化输出**(`agents/schemas.py`):
- `ResearchPlan.recommendation`: 5 档枚举 Buy / Overweight / Hold / Underweight / Sell
- `SentimentReport`: `overall_band`(6 档)+ `overall_score` (0-10 数字)+ `confidence` (low/medium/high) + `narrative`
- 设计原则: "保留 prose,但在决策点强加结构 — Field 描述就是模型的输出指令"。
- **裁决器 prompt 明令**: "Reserve Hold for situations where evidence on both sides
  is genuinely balanced; otherwise commit." → 直接对治"模型默认躲进 neutral"。

### 2.3 Sentiment 走"预获取注入"而非 tool-calling(`sentiment_analyst.py`)

`market_analyst` 用 tool-calling 让模型自己拉指标 — 适合维度多、不预知用哪个;
但 `sentiment_analyst` 选择**第 0 turn 就把数据塞进 system prompt**,理由:
"避免模型在缺数据时凭压力捏造"。**Aletheia 现状更接近 sentiment_analyst** —
我们 agent 是定时 cron,不是会话,不适合复杂 tool-calling 循环;预获取注入更稳。

---

## 3. 差距矩阵 — 我们差什么

| 维度 | OpenAlice | TradingAgents | Aletheia 现状 | 差距 |
|---|---|---|---|---|
| 多时间框架 OHLCV | ✅ DSL | ✅ tool | ❌ 只 24h | 🔴 必补 |
| 预计算技术指标 | ✅ 公式 DSL | ✅ 14 个 + 字典 | ❌ | 🔴 必补 |
| 短窗时间序列(N 个 close) | ✅ | ✅ last 30 | ❌ | 🔴 必补 |
| markdown 表格化交付 | 🟡 工具内 | ✅ 强制 | ❌ 一行字符串 | 🔴 必补 |
| 跨资产相对强度 | ✅ sector rot | 🟡 间接 | ❌ | 🟡 加 |
| 防幻觉验证表 | 🟡 tool 描述 | ✅ snapshot 表 | ❌ | 🟡 加 |
| 指标用法字典 | ✅ tool 描述 | ✅ system prompt | ❌ | 🟡 加 |
| 结构化输出 schema | ❌ falsifiable | ✅ Pydantic | 🟡 仅 dir/conf 文本 | 🟡 强化 |
| 强制 commit(不准默认 hold) | ✅ thesis 反证 | ✅ 裁决 prompt | ❌ 反向(劝保守) | 🔴 反转 |
| funding / OI / orderbook | ❌ | ❌ | ❌ | 🟢 暂不补(red line: 不引外部源) |
| 新闻 / sentiment / social | 🟡 归档查询 | ✅ tool/inject | ❌ | 🔴 AGENTS.md 明禁,**不做** |
| 工具白名单 + 描述长 prompt | ✅ | ✅ | 🟡 已有但简 | 🟢 已够 |

---

## 4. 适配方案 — 不破 red line 下能上的最大数据面

### 4.1 设计原则(钉死)

1. **不引外部源** — funding/OI/news/social 都不补(Aletheia red line + numpy/pandas 不引)。
2. **不动 change-controlled** — `exchanges/bitget/client.py` 已有 `get_ohlcv` 够用,
   不扩接口。
3. **借 TradingAgents 形态(预获取注入 + markdown 表 + 指标字典),
   不借 OpenAlice 工具化按需拉**(我们 agent 是 cron 不是会话)。
4. **结构化输出强化 — 但保留 neutral 出口** — 让 LLM 必须填 `confidence_score (0-100)`
   和 `key_observations[]`,而不是简单 `confidence: speculative`;让"我也不知道"
   有诚实表达(score=50 + observations=[])而不是默认 neutral。
5. **保守指令反转,但不强行 commit** — 删掉"use neutral/speculative unless"那句,
   改"observe what the data says, even if it's that signals are mixed";
   不学 TradingAgents 强制 commit(我们没有人审 desk,过度信任 LLM 风险更高)。

### 4.2 三层架构

```
┌──────────────────────────────────────────────┐
│ Layer A: 数据采集 (existing)                 │
│   collect_market_snapshots() — 每 5 min      │
│   现在只存 ticker。改动: 同时存 OHLCV 1h/4h  │
└──────────────────────────────────────────────┘
                ↓
┌──────────────────────────────────────────────┐
│ Layer B: market_context.py (NEW)             │
│   build_market_context(symbols) -> str       │
│   - 拉多时间框架 OHLCV(1h x 48, 4h x 30, 1d x 30)│
│   - 用 stdlib 算: sma_20 / sma_50 / rsi_14   │
│     / atr_14 / bbands_20 / momentum / vol_z  │
│   - 跨资产 relative strength 排名            │
│   - 渲染成 markdown 表格 + 短窗时间序列      │
│   - 附"指标用法字典"块                       │
│   - 失败降级到当前 1 行字符串(不阻塞)       │
└──────────────────────────────────────────────┘
                ↓
┌──────────────────────────────────────────────┐
│ Layer C: market_observer (MODIFIED)          │
│   - 替换 context 来源:context = build_market_context(symbols)│
│   - 强化 system prompt + Pydantic schema     │
│   - record_step("market_read", ...) 记下推理│
└──────────────────────────────────────────────┘
```

### 4.3 关键文件改动

| 文件 | 改动 | 受控? |
|---|---|---|
| `aletheia/trading/indicators.py`(新) | stdlib 实现 SMA/EMA/RSI/ATR/BBANDS/momentum/vol_z | 否 |
| `aletheia/trading/market_context.py`(新) | 多框架抓 + 指标计算 + 跨资产 RS + markdown 渲染 | 否 |
| `aletheia/agents/market_observer.py` | 接 builder + 升级 schema + 调整 prompt + record_step | 否 |
| `tests/test_market_context.py`(新) | 指标计算 + 渲染 + observer 集成 | 否 |
| historical `docs/archive/legacy/ai-context/logs/DEVLOG.md` / `docs/research/market-data/market_context_eval.md` | 文档落档 | 否 |

**0 个 change-controlled 模块改动**。

### 4.4 提示词工程改动(关键文本)

**system prompt(替换)**:
```
You are a crypto market observer for Aletheia paper-trading.

You will receive a verified market snapshot containing: latest prices, multi-
timeframe technical indicators (SMA/RSI/ATR/Bollinger/momentum), recent close
series, and cross-asset relative strength. Use this snapshot as your single
source of truth — do not invent numbers or claim levels not in the data.

Output a structured market read.

Calibration:
- direction_bias: "long" if multiple indicators across timeframes point up
  (e.g. price > SMA_50, RSI 50-70, positive 4h momentum, leading RS rank);
  "short" mirror; "neutral" only when signals genuinely conflict OR the data
  is too thin (note this in rationale).
- confidence: "high" only with multi-timeframe agreement and clear signal;
  "medium" with 1-2 timeframe agreement; "speculative" if data is noisy or
  conflicting. Speculative is honest, not a default.
- key_observations: 2-4 short bullets citing actual numbers from the snapshot
  (e.g. "BTC 1h RSI 68 + price > SMA_20: short-term overbought").

Not financial advice.
```

**Pydantic schema 升级**:
```python
class MarketRead(BaseModel):
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(50, ge=0, le=100,
        description="0=no idea, 100=overwhelming")
    rationale: str = ""
    key_observations: list[str] = Field(default_factory=list,
        description="2-4 specific data citations")
```

注意保留 neutral 作合法选项(不学 TradingAgents 强 commit) — 但要求**承认 neutral 的具体原因**,堵死"懒 neutral"。

### 4.5 不做的事(明确)

- ❌ 不接 funding rate / OI / orderbook(虽然 Bitget API 支持,但需要扩 client.py 接口面;
  且当前 stage-0 排序里这属感知层深化,远期项)。
- ❌ 不引 numpy/pandas/ta-lib(stdlib 够用 + 保持依赖薄)。
- ❌ 不上 tool-calling 让 LLM 自己拉数据(我们是 cron 短运行,不是会话)。
- ❌ 不补 news/sentiment/social(AGENTS.md 明禁 web ingestion)。
- ❌ 不在 prompt 里强制 commit(避免引导 LLM 编 long/short)。
- ❌ 不动 change-controlled(client.py / risk / execution / secrets 全部不碰)。

---

## 5. 验证标准

实施后 `market_observer` 一次手动运行(`aletheia agents run market_observer`),应满足:
1. opportunity 表至少出现过非 neutral 的 direction(多次运行后)。
2. confidence_score 字段填入 0-100 数字(不全 50)。
3. rationale 引用 snapshot 里出现的具体数值(grep 比对)。
4. agent_run_steps 里能看到 `record_step("market_read", input=full_context, output=read)`,
   前端 Agent Activity 抽屉看得见"AI 真在推理"。
5. 整套测试 ≥ 406 + 新增,全绿。
6. 数据不足时(snapshot 缺失)退回当前 1 行字符串,**不抛**。

---

## 6. 排序

排在 stage-0 稳跑证毕之前的"产出质量"修补里 — 因为现状是**质量证据全是 neutral**,
没法用它判断 stage-1 是否过 gate。把这个修了,Learning Ledger 的 activation_ratio 才
有真正的输入信号。

不算感知层深化(funding/OI/orderbook/news 都未引),只是**把已有数据用对**。
