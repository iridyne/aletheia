# Prediction Market Module — 设计文档

> 状态：观察阶段（Observation-only）  
> 最后更新：2026-06-18  
> 负责模块：`aletheia/agents/prediction_observer.py`, `aletheia/data/polymarket_provider.py`

---

## 1. 当前状态

### 已实现

- Polymarket Gamma API（只读）抓取活跃市场
- LLM 分析概率动量，生成结构化信号
- 数据持久化：`prediction_events`, `prediction_markets`, `prediction_probability_snapshots`, `prediction_market_claims`
- 前端 Prediction Lens 页面展示市场雷达、概率变动、observer 运行历史
- Category 关键词过滤 + top-volume fallback（当无匹配时降级到全市场）
- 高信号市场自动写入 `serenity_evidence` 作为 crypto 交易辅助参照

### 关键约束

- `execution_eligible=False`：prediction opportunity 不进入执行通道
- `asset_class="prediction"`：Paper Trader 直接跳过
- 无 Polymarket CLOB 下单能力：只有只读 API
- 信号→执行的链路断开：识别了概率变动也不会变成仓位

### 当前作用

纯信息参照。通过 `serenity_evidence` 给 crypto 方向性判断提供辅助（触发条件：`|Δ24h| > 10pp AND vol > $50k AND 问题含 crypto 关键词`）。

---

## 2. 潜在盈利路径分析

### 路径 A：直接交易 Polymarket

**思路**：在 Polymarket 上做市/方向性交易，赚概率偏差的钱。

| 维度 | 评估 |
|---|---|
| 需要 | CLOB API 接入（Polygon USDC）、做市逻辑、结算处理 |
| 优势 | 市场效率低于 crypto CEX，alpha 空间大 |
| 风险 | 流动性薄、Polygon gas、结算延迟、法律合规 |
| 工作量 | 高（新交易所适配层 + 资金链路 + 独立风控） |
| 回报 | 不确定，取决于能否找到持续的概率定价偏差 |

### 路径 B：Prediction Signal → Crypto 方向性交易

**思路**：prediction market 概率变动作为 crypto 交易的领先指标。

| 维度 | 评估 |
|---|---|
| 需要 | 因果映射表（event → symbol）、信号阈值、回测验证 |
| 优势 | 复用现有 crypto 执行通道，增量改动小 |
| 风险 | 因果关系不稳定、信号延迟（30min 轮询） |
| 工作量 | 中（映射表 + 权重逻辑 + 历史回测） |
| 回报 | 潜在但需验证——prediction 市场是否真的领先 crypto spot |

**具体需要**：
1. 因果映射表：`{"BTC price" → "BTC/USDT", "ETH merge" → "ETH/USDT", "Fed rate" → ["BTC/USDT", "SOL/USDT"]}`
2. 信号强度→仓位权重转换函数
3. 至少 30 天历史数据的回测
4. 延迟分析：Polymarket 概率变动是否真的 lead crypto price（还是 lag）

### 路径 C：新闻/信息面驱动的快速反应

**思路**：低延迟新闻抓取 + NLP → 在市场 price in 之前建仓。

| 维度 | 评估 |
|---|---|
| 需要 | 低延迟新闻源（<30s）、情绪/事件提取 NLP、与执行联动 |
| 优势 | 信息不对称是经典 alpha 来源 |
| 风险 | 延迟竞争（机构级玩家用微秒级）、假新闻、NLP 误判 |
| 工作量 | 中高（新闻源接入 + NLP pipeline + 低延迟要求） |
| 回报 | 对散户不现实做到微秒级，但分钟级在 altcoin 仍有空间 |

---

## 3. 决策记录

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-06-18 | 维持 observation-only，暂不开发盈利路径 | MVP 阶段集中精力在 crypto 核心 alpha 上 |
| 2026-06-18 | 修复 category fallback（831599c） | Polymarket API 不再返回 category 字段导致全空 |

---

## 4. 未来实施优先级（待决定）

如果要启动盈利路径，建议顺序：

1. **先积累数据**：保持 observer 运行 2-4 周，积累 probability_snapshots 时间序列
2. **回测验证路径 B**：用历史数据验证 prediction signal 是否 lead crypto price
3. **如果验证通过**：实现因果映射表 + 信号权重 → 接入现有 paper_trader
4. **路径 A/C 延后**：工作量大，ROI 不确定

---

## 5. 架构图

```
Polymarket Gamma API (只读)
        │
        ▼
fetch_active_markets() ── category filter + fallback
        │
        ▼
prediction_observer agent
  ├─ _persist_normalized() → prediction_events/markets/snapshots
  ├─ llm_structured() → 概率动量分析
  ├─ insert_opportunity(execution_eligible=False) → 前端展示
  ├─ _generate_heuristic_claims() → 自动标注
  └─ insert_serenity_evidence() → crypto 辅助参照 (高信号时)
        │
        ▼
  ┌─────────────────────────────────────┐
  │ Prediction Lens (前端)              │  ← 当前终点
  │  · Market Radar                     │
  │  · Probability Movers               │
  │  · Observer Runs                    │
  └─────────────────────────────────────┘
        │
        ▼ (未实现)
  ┌─────────────────────────────────────┐
  │ 盈利路径（待定）                      │
  │  · 路径 B: signal → crypto trade    │
  │  · 路径 A: Polymarket CLOB trade    │
  │  · 路径 C: news-driven fast react   │
  └─────────────────────────────────────┘
```
