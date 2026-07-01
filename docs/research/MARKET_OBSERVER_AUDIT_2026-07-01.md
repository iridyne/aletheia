# Market Observer 审计报告（完整版）

> 审计日期：2026-07-01  
> 审计范围：`market_observer.py`、`trading/market_context.py`、`data/context_builder.py`、`knowledge/router.py`、`knowledge/retrieval.py`、`agent_shell/trading_context.py`  
> 数据来源：`opportunities` DB 实测（19,597 条）+ 代码全量静态分析 + 两路并行 Explore agent 交叉验证  
> 方法：主审 + observer-auditor agent + context_builder/market_context/router 深度 agent

---

## 0. 实施状态与核实订正（2026-07-01，分支 `fix/scanner-observer-audit-fixes`）

本报告 P0–P3 已按 `docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md` 实施并通过 spec（UP-01~13）+ 回归测试。写报告后逐条读源码亲核，订正以下偏差：

- **3.1 `key_observations`**：确认 PI layer1 不读 + 渲染只用 `thesis[:150]`。**两处均已修**（MO-1：layer1 SQL 结果补 `key_observations`，`trading_decision` 渲染追加 obs，thesis 150→220）。
- **3.2 guard advisory**：确认架构错误（循环外用 `symbols`(list) 缺 scanner_family → `_applies_to_context` 恒 False）。已修（MO-2：移入 `_analyze_symbol`，用 per-symbol `scanner_family` 经新 `build_guard_advisory` 构建，闭合学习回路断口 F）。
- **3.3 F&G**：渲染层（`build_market_context_snapshot`）本就渲染 fng/top_movers；真问题在**组装层** `build_market_context_from_db` 未填充 MacroSnapshot 这三字段。已修（MO-3）。
- **3.4 crowding=0**：报告的 `(lsr-1)/0.5 if lsr else 0` 逻辑**在当前代码不存在**。LSR 已处理 `None→"n/a"`，crowding 用独立字段 `lv.crowding_score`（normalizer 计算）。真问题：无 funding/OI/LSR 输入时 `crowding_score=0.0` 渲染 "0/100" 被 LLM 误读。已修（MO-8：无输入渲染 "n/a" + MO-9 加 glossary）。
- **3.5 FTS token 不匹配**：**不成立，撤销**。`_fts_query` 已把 "BTC/USDT" 拆成 `"BTC" OR "USDT"`，召回正常。
- **3.6/3.7 RS rank / fallback**：确认影响 market_observer（它逐 symbol 调 `build_market_context`）。已修（MO-6：<2 symbol 省略 RS rank；MO-7：fallback 标注 ⚠️ DEGRADED）。
- **3.9/3.10 FTS**：降级 P2。`MATCH` + confidence 门已保证 token 相关性 → 硬阈值风险>收益（MO-10 **skip**）；snippet 24→48 已改（MO-11）。

各项最终处置见下方 §3/§4 与 plan 文档 §2/§3。

---

## 1. 当前行为总览

Market Observer 是一个每 15 分钟运行的 per-symbol 循环：

```
per-symbol 循环
  → build_market_context (OHLCV + ticker → markdown snapshot)
  → 附加 enriched context (orderbook / funding / OI / macro / xvenue / prediction market / news)
  → _has_sufficient_signal 预过滤（RSI 偏差 / ATR% / 关键字）
  → llm_structured(MarketRead) → direction_bias / confidence / confidence_score / key_observations
  → 若 universe_snapshot_id 存在 → annotate existing opp（更新 thesis + confidence）
  → 否则 → insert_opportunity
  → 通知 pi_daemon
```

输出到 PI 决策层的路径：

```
opportunity.thesis[:150]          → PI layer1 snapshot (10 条)
opportunity.key_observations      → ❌ PI 完全不读（18,865 条内容被丢弃）
opportunity.confidence_score      → PI layer1 confidence
opportunity.scanner_family        → PI layer1 for regime/guard
```

**关键设计决策**（有意，非缺陷）：
- system prompt 故意不告诉 LLM 新字段含义（Round 2 决定，防止先验偏向）
- 预过滤 `_has_sufficient_signal` 跳过平静市场，节省 wall-clock budget
- 200s wall-clock 上限防止线程泄漏（历史根因已修复，但保留上限作为保护）

---

## 2. 信号质量 DB 实测

| 指标 | 数值 | 说明 |
|---|---|---|
| 总 opportunity 数 | 19,597 | 全库 |
| thesis 长度 > 300 字符 | **18,865（96%）** | PI 截取 150 字符，剩余 96% 的 thesis 内容不可见 |
| key_observations 有实质内容（非空/非 None）| **13,551（69%）** | 共 13,551 条 opportunity 有具体数字引用，PI 全部不读 |
| 平均 key_observations 长度 | ~450 字符 | 远超 150 字符的 thesis 截取窗口 |
| derivatives 在 market=spot 里 | 34 条 | 来自 signal_emitter perp mislabeling（跨报告确认） |

---

## 3. 发现的问题（全量）

### 3.1 ❌【P0-Critical】`key_observations` PI 完全不读——LLM 最具体输出被系统性丢弃

**位置**：`aletheia/agent_shell/trading_context.py:152-173`

PI `_build_layer1_snapshot()` 读取字段：
```python
"id, symbol, market, direction_bias, confidence, confidence_score,
 scanner_family, thesis"
```

`key_observations` 不在查询里，也不在 `snapshot_opportunity` 对象里被传到 PI prompt。

**影响量化**：DB 实测 `key_observations` 有实质内容的共 13,551 条（69%）。这些 LLM 生成的「具体数字引用」（如「BTC 1h RSI 68 + price > SMA_20；funding 0.03% 偏多头」）对 PI 完全不可见。

同时，thesis 字段虽然被读取，但 PI 截取 `thesis[:150]`，而实测 18,865 条（96%）的 thesis 超过 150 字符。这两个问题叠加意味着：**PI 获得的每条 opportunity 信息量仅为 market_observer LLM 实际生成内容的 10%-20%**。

**修复**：在 `_build_layer1_snapshot` SQL 中加入 `key_observations` 字段：
```python
# trading_context.py:152 — 修改 SQL
SELECT id, symbol, market, direction_bias, confidence, confidence_score,
       scanner_family, thesis, key_observations
FROM opportunities
WHERE status='open' ORDER BY created_at DESC LIMIT 10
```
并在 `snapshot_opportunity` 格式化时在 thesis 后追加 `key_observations` 关键段。

---

### 3.2 ❌【P0-Critical】guard advisory 架构位置根本性错误——永远无法生效

**位置**：`market_observer.py:527-538`

guard 的 `_applies_to_context` 需要 `symbol`（单数）、`scanner_family`、`direction`、`confidence`——这些在 LLM 跑完**之后**才有值。当前实现把 `build_advisory_context` 放在循环外、LLM 调用前的全局位置：

```python
# market_observer.py:527 — 循环外调用
guard_ctx = {"symbols": symbols, "asset_class": "crypto"}  # ← 复数 symbols，无 family/direction
memory_block = build_advisory_context(role="market_observer", guard_context=guard_ctx)
# 下面每个 symbol 的 _analyze_symbol 都用同一个 memory_block
```

三重失效：
1. `symbols`（列表）vs `symbol`（字符串）：`_applies_to_context` 匹配单个 symbol，列表无法匹配任何 guard
2. 无 `scanner_family`：所有 guard 的 `applies_to.scanner_family=["momentum"]` 等，`_applies_to_context` 返回 False
3. 无 `direction`：方向相关 guard 无法匹配

结果：market_observer 的 guard advisory block 永远是空字符串，即使已有 8 个 active guard。

---

### 3.3 ❌【P1-High】F&G 和 top_movers 在运行路径中永远为 None

**位置**：`aletheia/data/context_builder.py:246-256`

`build_market_context_from_db`（market_observer 的主调用路径）从 macro_snapshot 表读取数据，但只读取了通用字段，从未填充 `MacroSnapshot` 模型的 `fng_value`/`fng_classification`/`top_movers_summary` 三个字段：

```python
# context_builder.py:246-256
macro = MacroSnapshot()
# ... 只赋了 timestamp, regime, description
# fng_value / fng_classification / top_movers_summary 全部是初始化默认值 None
```

而 `build_market_context`（另一路径，供 `trading_context.py` 使用）会查询 macro_collector 的最新数据。两路径行为不一致。

**影响**：market_observer 的每次 LLM 分析都缺少宏观恐贪指数和市场领涨/跌领域信息，而这些数据已在 DB 中（macro_collector 正常收集中）。

---

### 3.4 ❌【P1-High】`crowding` 在 LSR 缺失时被设为 0，LLM 误读为「极低拥挤」

**位置**：`aletheia/data/context_builder.py:106-118`（derivatives table 构建）

```python
lsr = lv.long_short_ratio  # 可能是 None
crowding = round((lsr - 1.0) / 0.5 * 100, 1) if lsr else 0  # ← None → 0
```

0 是量化数值，LLM 会解读为「极低拥挤度/空头大幅占优」。实际含义是「无数据」。在 funding 为正（多头支付）而 LSR 为 None 的组合下，LLM 可能输出「资金偏多头，拥挤度极低」——两个矛盾信号。

**修复**：无数据时应显式标注：
```python
crowding_str = f"{crowding}" if lsr is not None else "n/a"
```

---

### 3.5 ❌【P1-High】FTS 查询 token 与 DB 存储 token 不匹配

**位置**：`aletheia/knowledge/retrieval.py:196-202`

FTS5 tokenizer 默认把 `/` 视为分隔符，`"BTC/USDT"` 存储时分词为 `"BTC"` + `"USDT"` 两个 token，但 `retrieval.py` 构建查询时用 `"BTCUSDT"` 作为单一 token 查询——该 token 不存在于 FTS 索引，召回率为 0。

实际路径：
1. market_observer 调用 `retrieve_lessons(symbol="BTC/USDT")` 
2. `retrieval.py` 把 query 构建为 `"BTCUSDT"` 或其他变体
3. FTS5 返回 0 条 symbol-specific lesson

**修复**：拆分 symbol 查询为 `base AND quote` 或用 `fts_match(base_token OR quote_token)`：
```python
base, quote = symbol.split("/") if "/" in symbol else (symbol, "")
query = f"{base}" if not quote else f"{base} OR {quote}"
```

---

### 3.6 ❌【P1-Medium】RS（相对强度）rank 在单 symbol 路径下完全无意义

**位置**：`aletheia/trading/market_context.py:137-147`

`market_context.py` 的 `build_single_symbol_context` 计算 RS 排名，在只有单个 symbol 时输出 `"Rank 1 of 1"`——这是第一名，LLM 可能将其解读为「全市场最强/最弱」的看涨/看跌信号。但实际上是无意义的（样本量 1）。

---

### 3.7 ❌【P1-Medium】fallback 数据路径无降级提示，LLM 把降级数据当正常数据

**位置**：`aletheia/trading/market_context.py:248-264`

当前有 `debug["fallback"]=True` 标记，但生成给 LLM 的 context 里没有「数据降级」说明。LLM prompt 的「only cite numbers from this block」指令在降级路径下让 LLM 引用质量存疑的数据（如 1h 蜡烛模拟的「4h」数据），但 LLM 不知道。

---

### 3.8 ❌【P1-Medium】memory_block 对全部 symbol 相同

**位置**：`market_observer.py:527-562`

`build_advisory_context` 在循环外调用一次，产出全局 memory_block，所有 symbol 复用同一 block。这意味着 SOL 看到的 guard advisory 与 BTC 完全相同——对于 symbol-specific guard（`applies_to.symbols=["BTC/USDT"]`）无法匹配，且会把不相关 guard 的信息塞入每个 symbol 的 LLM 上下文。

---

### 3.9 ❌【P1-Medium】无最小相关度门槛——k=3 强制返回无关 lesson

**位置**：`aletheia/knowledge/retrieval.py:205-220`

FTS BM25 召回强制返回 k=3 条，即使最高分的 lesson 与当前 symbol/market 毫无关联（分数很低）。在知识库条目少时，BTC 的 lesson 可能被召回给 DOGE 的分析，或某个旧 mock-regime lesson 被当作最新 guard 提供给 LLM。

**修复**：加最小分数阈值（FTS 分数为负数，越大越相关）：
```python
results = [r for r in raw_results if r["score"] > MIN_FTS_SCORE]
```

---

### 3.10 ❌【P1-Medium】24-token FTS5 snippet 可能截取到 setup 段而非 outcome 段

**位置**：`aletheia/knowledge/retrieval.py:220`

`snippet(knowledge_fts, 0, '<b>', '</b>', '...', 24)` 返回匹配词附近 24 token。case_study 文件结构是：
```
## Setup ... (匹配词在这里)
## Outcome ... (PI 最需要的内容)
```

如果匹配词在 setup 段，snippet 截取到 setup，outcome（trade 结果、因子关键、lesson）完全不出现在 PI 看到的 lesson 摘要里。

---

### 3.11 ❌【P2-Medium】同一 opportunity 每个 cycle 被 LLM 覆写 thesis

**位置**：`market_observer.py:419-437`

annotation 路径在每次 15 min 触发时直接覆写 `thesis`。没有保留原始 scanner-generated thesis 的机制。如果 15min 后 LLM 分析方向变化，thesis 可能与最初开仓判断相反，PI 下次读到的是最新 LLM 输出而非最初分析。

---

### 3.12 ❌【P2-Medium】LSR 列无 glossary，LLM 对「拥挤度」的含义存在歧义

**位置**：`aletheia/data/context_builder.py:106-107`

derivatives 表中包含 `crowding` 列（由 LSR 推算），但 context 里没有说明 LSR 的含义（Long Short Ratio，多头账户数 / 空头账户数）。LLM 可能把 LSR=1.5 解读为「多头偏强 50%」或「1.5x 资金比」，两种解读方向完全不同。

**修复**：在 derivatives 表格 header 里加注：`| crowding (LSR: >1=long-heavy, <1=short-heavy) |`

---

### 3.13 ❌【P2-Medium】LLM user prompt 不包含 scanner_family——做通用分析而非针对性验证

**位置**：`market_observer.py:357`

market_observer 知道每个 symbol 的 scanner_family（`u_member.get("scanner_family")`），但 LLM prompt 只有：
```
Market data for {symbol}:
{context}{memory_block}
Return the market read JSON.
```

结果：derivatives scanner 筛出的 symbol，LLM 做的是通用技术分析（RSI/MA 为主），没有利用「这是 funding/OI 驱动的候选」的信息，也不会聚焦在 funding rate 方向验证。

---

### 3.14 ❌【P2-Low】news headlines 无时间范围过滤——旧新闻当最新信号

**位置**：`market_observer.py:321`

```python
"WHERE symbols_mentioned LIKE ? ORDER BY created_at DESC LIMIT 3"
```

无时间过滤，3 天前的旧新闻可能进入 LLM prompt。LLM 对时效性无感知，可能把「3 天前 BTC 暴跌」的新闻当作当前市场状态。

**修复**：
```python
"WHERE symbols_mentioned LIKE ? AND created_at > datetime('now', '-24 hours') ORDER BY created_at DESC LIMIT 3"
```

---

### 3.15 ❌【P2-Low】prediction market signals 无流动性过滤

**位置**：`market_observer.py:296-313`

按 `abs(probability_change_24h) DESC` 排序，零流动性市场概率波动可能很大（1 笔交易 50% 变化）会排在前面，混入 LLM 分析上下文。

---

### 3.16 ❌【P3-Low】per-symbol 循环内重复 `load_config()`

**位置**：`market_observer.py:343`

顶层已有 `cfg = load_config()`（line 481），但 `_has_sufficient_signal` 的 `force_full_scan` 检查在循环内重复加载。每个 symbol 额外一次文件 IO（非关键，但累计 30 symbol × 15min = 2700 次/天）。

---

### 3.17 ✅ 已有合理设计

- per-symbol LLM 调用（修复了原来「只用 symbols[0] 结果」的 bug）
- `confidence_score` 数字强制提交（不再有「中立隐藏所」）
- `key_observations` 强制引用具体数字（MarketRead schema）
- `MarketRead` Pydantic schema 兜底无效方向/置信度值
- enriched context 全部异常静默（不 break 主路径）
- universe 路径 annotate 不 insert（防止同 snapshot 重复 opportunity）
- 200s wall-clock 上限（线程泄漏防护）

---

## 4. 改善建议（优先级排序）

### P0 — 立即修（信息流根本性断裂）

**P0-A：`key_observations` 加入 PI layer1 查询**（`trading_context.py:152`）

```python
# SQL 补 key_observations 字段
row_text = f"[{opp['scanner_family']}] {opp['symbol']} {opp['direction_bias']} | " \
           f"conf={opp['confidence_score']} | {opp['thesis'][:120]}"
# 补充 key_observations（前 150 字符）
if opp.get("key_observations"):
    import json
    try:
        obs = json.loads(opp["key_observations"]) if isinstance(opp["key_observations"], str) else opp["key_observations"]
        if obs:
            row_text += f" | obs: {'; '.join(obs[:2])}"
    except Exception:
        pass
```

成本：+2行，零风险。收益：PI 可见 LLM 实际生成内容提升 4-5x。

**P0-B：guard advisory 移入 `_analyze_symbol`，LLM 结果已知后调用**

```python
# _analyze_symbol 内，LLM 返回结果后
if market_read:
    guard_ctx = {
        "symbol": symbol,  # 单数
        "asset_class": "crypto",
        "scanner_family": scanner_family or market_read.inferred_family,
        "direction": market_read.direction_bias,
        "confidence": market_read.confidence,
    }
    sym_advisory = build_advisory_context(role="market_observer", guard_context=guard_ctx, k=2)
    # 写入 opportunity 的 annotation 而非提前注入 prompt
```

### P1 — 关键质量修复

**P1-A：`context_builder.py` 补填 F&G + top_movers**

`build_market_context_from_db` 读 macro_snapshot 时补填：
```python
fng_row = db.execute("SELECT fng_value, fng_label, top_movers_json FROM macro_snapshots ORDER BY collected_at DESC LIMIT 1").fetchone()
if fng_row:
    macro.fng_value = fng_row["fng_value"]
    macro.fng_classification = fng_row["fng_label"]
    macro.top_movers_summary = fng_row["top_movers_json"]
```

**P1-B：crowding=None 时显式标注**（`context_builder.py:110`）

```python
crowding_str = f"{crowding:.0f}" if lsr is not None else "n/a"
```

**P1-C：FTS token mismatch 修复**（`retrieval.py:196`）

```python
if "/" in symbol:
    base, quote = symbol.split("/", 1)
    fts_query = f"{base}"  # 只用 base 查，避免 BTC/USDT 被 tokenizer 误拆
else:
    fts_query = symbol
```

**P1-D：在 LLM prompt 中加 scanner_family 提示**（`market_observer.py:357`）

```python
scanner_hint = f"\n\nThis symbol was selected by the '{scanner_family}' scanner. " \
               f"Focus your analysis on validating that specific signal family." \
               if scanner_family else ""
user_prompt = f"/no_think\nMarket data for {symbol}:\n{context}{memory_block}{scanner_hint}\n\nReturn the market read JSON."
```

**P1-E：news headlines 加 24h 时间过滤**（`market_observer.py:321`）

**P1-F：FTS 加最低相关度门槛**（`retrieval.py:205`）

```python
MIN_RELEVANCE = -5.0  # FTS5 BM25 score，越接近0越相关
results = [r for r in raw_results if r.get("bm25_score", -999) > MIN_RELEVANCE]
```

### P2 — 可观测性和一致性

**P2-A：fallback 路径加降级提示**（`market_context.py:248`）

在 context 里加「[注意：以下数据来自降级路径，精度有限]」标注，或在 prompt 中说明「某些指标可能基于低分辨率估算」。

**P2-B：LSR 列加 glossary**（`context_builder.py:107`）

表格 header 改为：`| crowding [>0=long-heavy, <0=short-heavy, n/a=no data] |`

**P2-C：annotation 路径保留原 scanner thesis**（`market_observer.py:419`）

```python
# 只更新 key_observations + confidence，不覆写 thesis
# 或追加而非替换：thesis = f"{original[:120]}... [obs@{ts}]"
```

**P2-D：load_config 移到循环外**（`market_observer.py:343`）

把 `cfg_for_filter = load_config()` 提到循环外，用已有的顶层 `cfg`。

---

## 5. 问题优先级总表

| 优先级 | 类型 | 位置 | 描述 | DB 实测 |
|---|---|---|---|---|
| P0-Critical | 信息断裂 | `trading_context.py:152` | key_observations PI 不读 | 13,551 条有内容被丢弃 |
| P0-Critical | 架构错误 | `market_observer.py:527` | guard advisory 在 LLM 前构建，永远无效 | — |
| P1-High | 宏观缺失 | `context_builder.py:246` | F&G / top_movers 运行路径不填充 | — |
| P1-High | 数据噪音 | `context_builder.py:110` | crowding=0 when LSR absent，LLM 误读 | — |
| P1-High | 召回断裂 | `retrieval.py:196` | FTS token 不匹配 symbol 格式 | — |
| P1-High | 截断 | `trading_context.py:168` | thesis[:150] 截掉 96% 内容（叠加 P0-Critical）| 18,865 条超长 |
| P1-Medium | RS 无意义 | `market_context.py:137` | 单 symbol RS rank=1/1，LLM 误读为「最强」| — |
| P1-Medium | 降级无标注 | `market_context.py:248` | fallback 数据 LLM 不知情 | — |
| P1-Medium | 全局 memory | `market_observer.py:562` | 所有 symbol 共用同一 memory_block | — |
| P1-Medium | 无相关度阈值 | `retrieval.py:205` | k=3 强制返回，低相关 lesson 进 prompt | — |
| P1-Medium | snippet 错位 | `retrieval.py:220` | 24-token snippet 可能只截 setup 段 | — |
| P2-Medium | thesis 覆写 | `market_observer.py:419` | annotation 每 15min 覆写原始 scanner thesis | — |
| P2-Medium | LSR 歧义 | `context_builder.py:107` | LSR 无 glossary，LLM 解读不稳定 | — |
| P2-Medium | 无 scanner 上下文 | `market_observer.py:357` | LLM prompt 不告知 scanner_family | — |
| P2-Low | 旧新闻 | `market_observer.py:321` | news 无时间过滤 | — |
| P2-Low | 零流动性 | `market_observer.py:296` | prediction signals 无 volume 阈值 | — |
| P3-Low | 冗余 IO | `market_observer.py:343` | per-symbol 循环内 load_config() | — |

---

## 6. 效率改善

| 问题 | 影响 | 建议 |
|---|---|---|
| per-symbol `load_config()` | 30 symbol × 15min = 2700次/天 | 移出循环 |
| `get_latest_memo()` 每 cycle 调用 | memo 每天生成一次，不必每 cycle 查 DB | 加 1h TTL 缓存 |
| enriched context 全量构建后才做 `_has_sufficient_signal` 检查 | 每次都要走 OB/funding/news 查询，再决定是否跳过 | 先做简版信号检查，通过后再构建 enriched context |
| `build_market_context_from_db` 两次 DB 调用（macro + lv_list）| 可合并为一次 JOIN | — |

---

## 7. 附录：market_observer 与上下游联动

### 上游（universe scanner → market_observer）

```
universe scanner
  → signal_emitter 生成初始 Opportunity（thesis = scanner reasons, key_observations = []）
  
market_observer（15min 后触发）
  → per-symbol LLM 分析（缺 scanner_family 提示）
  → annotation 路径：覆写 thesis、填 key_observations、更新 confidence/derivatives 字段
```

### 下游（market_observer → PI agent）

```
PI agent
  → _build_layer1_snapshot()
    → 读 thesis[:150]（截掉 96% 内容）
    → ❌ 不读 key_observations（13,551 条被丢弃）
  → layer3.lessons（knowledge_items/case_study，content=NULL bug 见断口D）
  → layer3.guards（guard 命中但 penalty=0，见断口G）
```

### 断口关联

| 断口 | 位置 | 市场Observer具体表现 |
|---|---|---|
| 断口F | `market_observer.py:531` | guard advisory 永远空（架构根本性错误，不只是字段名问题）|
| P0-Critical（此报告）| `trading_context.py:152` | key_observations 不传 PI，PI 决策缺最具体信号依据 |
| P1-High（此报告） | `context_builder.py:246` | F&G 缺失，LLM 分析缺宏观情绪 |
