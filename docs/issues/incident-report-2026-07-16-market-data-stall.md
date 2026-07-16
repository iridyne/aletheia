# 事件报告：市场数据管道停顿 2026-07-16T00:19Z～01:08Z（约 28 分钟）

**事件 ID**：THU-001  
**发现时间**：2026-07-16T01:07Z（定时观测）  
**停顿窗口**：约 00:19–01:08Z（49 分钟数据断层，其中核心调度锁约 28 分钟）  
**当前状态**：部分恢复（observers 正常，data.market 仍 stale）  
**严重程度**：🔴 高（整个交易决策管道断流，提案流 11h 无更新）

---

## 根因分析

### 直接根因

`market_observer` APScheduler Job（max_instances=1）在一次 PI Daemon LLM 调用中持有单实例锁长达 **~28 分钟**（08:40–09:07 CST），导致所有其他调度任务排队等待，无法执行：

```
09:07:09 WARNING  aletheia.agents:219  LLM structured call exceeded 180s total wall-clock budget
09:07:09 WARNING  aletheia.agent_shell.trading_decision:964  LLM assessment failed: llm_unavailable: structured_timeout
09:07:09 WARNING  aletheia.pi_daemon:302  pi_daemon: evaluation failed: llm_assessment_unavailable
```

### 触发链

1. **08:40:08 CST** — `market_observer` 启动（run_id=32c9fa1beaba，10 instruments，budget=460s）
2. **08:40:16 CST** — microstructure collector 在 18s cap 内超时，被跳过
3. **08:40 ～ 09:07 CST** — MO 运行中触发 PI daemon 评估，PI daemon 发起 LLM 结构化调用
4. **08:40 ～ 09:07 CST** — LLM 调用缓慢（Anthropic API 延迟/限速），所有其他 scheduler jobs 报 `maximum number of running instances reached (1)`，约 **150+ 次** jobs 被跳过或 miss
5. **09:07:09 CST** — LLM 180s 总预算耗尽，PI daemon 放弃评估
6. **09:07:28 CST** — 调度锁释放，filter_outcome_settler 立即运行（544 outcomes 结算）
7. **09:07:31 CST** — Registry sync（578 active assets from OKX）
8. **09:08:15 CST** — Universe 特征采集完成（99/578 assets）
9. **09:08:16 CST** — Universe refresh complete（15 selected，snapshot=dd6f0ab9）
10. **09:09:28 CST** — 下一次 market_observer 正常启动（run_id=12fb5151022d）

### 受影响范围

| 子系统 | 停顿时长 | 影响 |
|---|---|---|
| market_observer (universe refresh) | ~28min | 市场特征断层，候选评分无法更新 |
| price_feed | ~28min | 实时价格停止更新 |
| data_collectors (microstructure, derivatives) | ~28min | 全部被 skip 或 miss |
| PI daemon | 超时失败 | 候选评估链断裂，no_evaluable_candidate |
| data.market snapshot | ~49min | doctor 持续显示 WARNING |
| loop.newest_opportunity | ~75min | observer 停产，health.rollup degraded |
| 提案流 (proposals) | >11h | 无新提案产出 |

---

## 历史模式分析

这**不是首次**此类事件。在 `data/logs/errors.log.1`（历史旋转日志）中可见相同模式可追溯至 **2026-06-26**：

```
job market_observer: asyncio.run() teardown hung after 255s — executor thread abandoned (ProactorEventLoop IOCP drain). Total abandoned threads: N
market_observer job timed out after 240s
```

历史记录中 abandoned threads 最高达 **23**（errors.log.1 line 10522）。说明此问题在历史版本中以 240s 硬超时形式发生，今日事件是同类问题的新变体（LLM 180s 预算超时取代 MO 240s 超时）。

**复发频率估计**：errors.log.1 中多次出现，结合今日 restart_history 中 `self_heal_restart:market_data_stale=3`（24h内），此类事件每天可能发生 1–3 次。

---

## 并发副作用

### 1. OKX 市场数据问题

日志中多次出现 OKX API 错误：
```
no_live_price_for_symbol: ALLO/USDT — execution_eligible set to False
no_live_price_for_symbol: WLD/USDT — execution_eligible set to False
no_live_price_for_symbol: SLX/USDT — execution_eligible set to False
```
3 个 symbol 在 OKX 上无法获取实时价格，被设为 `execution_eligible=False`。

### 2. yfinance 美股指数拉取失败

```
$^GSPC: possibly delisted; no price data found  (period=90d)
$GC=F: possibly delisted; no price data found  (period=90d)
```
宏观数据采集器尝试用标准 ticker 格式（`^GSPC`）从 yfinance 拉取 S&P500、黄金期货，但格式不被接受（`instType=SWAP` 误传到 yfinance）。这是 AAPL/USDT、SPY/USDT 等美股代币出现在 universe 后引入的副作用。

### 3. executor thread 泄漏

在 28 分钟停顿窗口内，多个 job 的 asyncio 事件循环在超时后未能清理：
```
job price_feed: asyncio.run() teardown hung after 60s — executor thread abandoned. Total: 1→10+
job data_collectors: asyncio.run() teardown hung after 60s
job market_data: asyncio.run() teardown hung after 105s
```
这些泄漏的线程会积累内存并可能干扰后续运行。

---

## 恢复状态（01:12Z 确认）

| 项目 | 状态 |
|---|---|
| `data.collectors` | ✅ 7/8 完成（coinalyze inactive 预期） |
| `loop.newest_opportunity` | ✅ 1min 前（observer 恢复） |
| `data.market` snapshot | ⚠️ 仍 48min stale（MO 未写出新快照） |
| `health.rollup` | ⚠️ degraded: market_data_stale:48min |
| LLM 服务 | ✅（下次 MO 运行正常） |

`data.market` 残余 stale 状态预计在下一次完整的 market_observer run 完成后自动清除（通常 < 15min）。

---

## 潜在修复方向

1. **解耦 LLM 调用与调度锁**：PI daemon 的 LLM 调用不应在 market_observer 的 APScheduler job 锁范围内执行。可以让 MO 完成后立即释放锁，PI daemon 异步评估。
2. **降低 max_instances 阻塞影响**：将 market_observer 的 max_instances 改为"上限并发+超时强杀"，而非无限等待。
3. **LLM 超时熔断**：当 LLM 调用 >30s 时返回 `no_evaluable_candidate` 并释放锁，而不是等待 180s。
4. **修复 yfinance ticker 格式**：美股代币的宏观数据应通过专用适配器，而非 ccxt + OKX API 路径。

---

*记录时间：2026-07-16T01:15Z（本地观测）*  
*日志来源：`data/logs/errors.log`（当前）+ `data/logs/errors.log.1`（历史）*  
*相关监控条目：MONITORING_2026-07-14.md § THU-001*
