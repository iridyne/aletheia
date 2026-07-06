# Vergex 持仓结构信号整合实施计划

> 制定日期：2026-07-06  
> 当前分支：`iridite/data_source_enhance`  
> 输入材料：`C:\Users\ollama\Documents\vergex-security-report.zip`（只读分析，未运行 PoC 脚本）  
> 目标：把 Vergex 可合作的数据和理论转成 Aletheia 可消费、可验证、可审计的交易证据。  
> 边界：本计划不要求修改 live trading、risk limits、execution gateway、credentials、交易所 signer；第一阶段只做 observation/shadow，不直接改变下单权限。

---

## 0. 人话结论

Vergex 报告里最值得借鉴的不是“漏洞”，也不是他人策略或账户数据，而是一套 **Hyperliquid 永续持仓结构分析方法**：

- 市场参与者的成本分布在哪里。
- 哪些价格带有清算燃料。
- 哪边账面亏损更重，容易形成抛压或逼空。
- POC / Value Area / support / resistance 是否和当前价格接近。
- Top10 / Top50 大户集中度是否过高。

Aletheia 现在已有 Hyperliquid funding/OI，但还没有“持仓成本结构 + 清算燃料 + 成本墙”这一层。Vergex 合作最适合补的正是这层。

**推荐合作方式**：Vergex 提供聚合后的结构信号，Aletheia 把它作为 `market_observer` 的证据块和 shadow evaluation 的输入。不要把完整地址、他人策略 prompt、他人 agent 资产数据、credential 类字段接入 Aletheia。

---

## 1. 从报告中确认的可用理论

### 1.1 七维结构模型

| 维度 | 人话含义 | Aletheia 可用方式 |
|---|---|---|
| `CGO` | 被套成本压力：亏损多头/空头谁更重 | 判断上方抛压或下方支撑 |
| `VAP` | 当前价在 POC / Value Area 中的位置 | 判断追涨追跌是否划算 |
| `SKW` | 当前价离最近被套锚点多近 | 判断哪边更容易先被触发 |
| `LFA` | 上方空头清算燃料 vs 下方多头清算燃料 | 判断 squeeze / flush 风险 |
| `WP` | 盈亏平衡墙 | 识别支撑/阻力墙 |
| `convexity` | 清算集中度 | 判断一旦触发是否爆发性强 |
| `cascade` | 近端级联清算风险 | 判断短时间连环强平风险 |

报告拟合显示 `LFA` 和 `CGO` 权重最高。对 Aletheia 来说，先接这两个维度的原始聚合数字，比直接接综合分更稳。

### 1.2 不建议直接照搬的部分

- 不直接采用 Vergex 的 `composite_z` 作为交易开关。
- 不把 Vergex 的 AI 交易建议 `OPEN_LONG/OPEN_SHORT` 直接映射成 Aletheia proposal。
- 不把地址级 whale 明细落库为默认数据。
- 不把报告中的策略 prompt、用户资产、交易所账户、agent 配置、credential 信息引入系统。

原因：Aletheia 的优先级是状态一致性、可审计、证据诚实。我们需要的是可解释的结构证据，不是另一个黑盒分数替代本地决策链。

---

## 2. 与当前分支的差距

当前 `docs/design/data_utilization.md` 已登记：

- Hyperliquid funding/OI 通过 `HyperliquidCollector` 写入 funding/OI 表。
- `market_observer` 会读取 Hyperliquid funding，渲染跨 venue funding 对比。
- Coinalyze 负责 key-guard 的 OI/liquidation 补充。
- doctor 已能暴露 orderbook、Coinalyze、prediction market 数据是否新鲜。

缺口：

| 缺口 | 当前状态 | Vergex 可补什么 |
|---|---|---|
| 持仓成本分布 | 无 | POC、Value Area、support/resistance、magnet |
| 清算燃料方向 | 只有已发生 liquidation 或 Coinalyze 聚合 | 上方空头清算燃料、下方多头清算燃料 |
| 账面盈亏压力 | 无 | long/short unrealized PnL overhang |
| 大户集中度 | 无 | top10/top50 concentration、holder count |
| 结构信号历史 | 无 | 每轮结构快照，供 1h/4h/24h 后验评估 |

---

## 3. 数据合同（Vergex → Aletheia）

第一版只接受聚合字段，不接受地址明细。

```json
{
  "symbol": "BTC/USDT",
  "venue": "hyperliquid",
  "source": "vergex",
  "as_of_time": "2026-07-04T14:16:53Z",
  "data_quality": "complete|partial|degraded",
  "coverage_note": "optional short text",

  "poc": 62267.64,
  "value_area_low": 60000.0,
  "value_area_high": 65000.0,
  "support": 62267.64,
  "resistance": 76151.29,
  "magnet": 70000.0,

  "short_liq_above_usd": 335141283.44,
  "long_liq_below_usd": 220161274.01,
  "long_unrealized_pnl_usd": -42052467.75,
  "short_unrealized_pnl_usd": 18025227.36,

  "top10_concentration": 0.21,
  "top50_concentration": 0.44,
  "holder_count": 30843,
  "long_holder_count": 18396,
  "short_holder_count": 12447,

  "cgo_direction": "bullish|bearish|neutral|conflict",
  "vap_direction": "bullish|bearish|neutral|conflict",
  "lfa_direction": "bullish|bearish|neutral|conflict",
  "cascade_risk": 0.0,
  "convexity": 0.0,

  "vendor_bias": "bullish|bearish|neutral|conflict",
  "vendor_rank": 7,
  "vendor_universe_size": 30
}
```

### 明确拒收字段

| 类型 | 处置 |
|---|---|
| 完整钱包地址列表 | 默认拒收；如以后确需使用，必须单独授权、脱敏、只做聚合 |
| 他人策略 prompt / system prompt | 拒收 |
| 交易所余额、订单、持仓、账户 ID | 拒收 |
| API key、token、credential、proxy IP | 拒收 |
| 可逆向权重的完整输入/输出训练样本 | 不对外暴露；内部也只保留必要字段 |

---

## 4. 推荐落地架构

### 4.1 新数据域

新增逻辑域：`perp_structure`

| 项 | 方案 |
|---|---|
| collector | `VergexStructureCollector`（或后续自建 `HyperliquidStructureCollector`） |
| storage | 新表 `perp_structure_snapshots` |
| consumer 1 | `market_observer` 的结构证据块 |
| consumer 2 | `context_builder` / `normalizer` 的 leverage/structure state |
| consumer 3 | shadow evaluation，评估 1h/4h/24h 后验 |
| doctor | `data.perp_structure` 新鲜度和覆盖度 |
| 默认行为 | observation-only，不直接触发交易 |

### 4.2 表结构草案

```sql
CREATE TABLE IF NOT EXISTS perp_structure_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'hyperliquid',
    source TEXT NOT NULL DEFAULT 'vergex',
    as_of_time TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    data_quality TEXT NOT NULL DEFAULT 'unknown',
    coverage_note TEXT,

    poc REAL,
    value_area_low REAL,
    value_area_high REAL,
    support REAL,
    resistance REAL,
    magnet REAL,

    short_liq_above_usd REAL,
    long_liq_below_usd REAL,
    long_unrealized_pnl_usd REAL,
    short_unrealized_pnl_usd REAL,

    top10_concentration REAL,
    top50_concentration REAL,
    holder_count INTEGER,
    long_holder_count INTEGER,
    short_holder_count INTEGER,

    cgo_direction TEXT,
    vap_direction TEXT,
    lfa_direction TEXT,
    cascade_risk REAL,
    convexity REAL,

    vendor_bias TEXT,
    vendor_rank INTEGER,
    vendor_universe_size INTEGER,

    raw_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_perp_structure_sym_ts
    ON perp_structure_snapshots(symbol, as_of_time DESC);
```

注意：`raw_json` 必须经过字段白名单过滤，不能把地址明细或敏感对象原样塞进去。

---

## 5. Prompt 渲染原则

只给 LLM 可消化的短证据，不堆原始 JSON。

示例：

```text
### Perp structure (Vergex / Hyperliquid)
BTC/USDT: POC $62,268, value area $60,000-$65,000; price near support.
Liquidation fuel: short-liq above $335M vs long-liq below $220M, squeeze fuel dominates.
Concentration: top10 21%, top50 44%; data quality complete, as-of 12min ago.
Interpretation: upside breakout can trigger short squeeze; avoid chasing short into support.
```

渲染规则：

- 数据过旧则不渲染，或明确标注 stale。
- `data_quality=degraded` 时必须写明 degraded，不允许当完整数据使用。
- 只输出相对关系和关键金额，不输出地址。
- `vendor_bias` 只作为“外部结构读数”，不能覆盖 Aletheia 自己的方向判断。

---

## 6. 实施批次

### P0：计划与合同锁定

1. 在 `docs/design/data_utilization.md` 登记 `perp_structure` 数据域。
2. 在 `tests/specs/spec_data_utilization.py` 增加 DU claim：
   - 有数据源登记。
   - 有消费者声明。
   - 不允许无消费者落库。
3. 新建/更新 API 合同文档：
   - 白名单字段。
   - 拒收字段。
   - stale 阈值。
   - raw_json 脱敏规则。

验收：

- 只改文档和 spec。
- `uv run pytest tests/specs/spec_data_utilization.py -q` 通过。

### P1：存储与 doctor

1. 新增 `PerpStructureSnapshot` model。
2. 新增 migration 表。
3. repository 增加：
   - `insert_perp_structure_snapshot`
   - `latest_perp_structure(symbol, source=None)`
   - `prune_perp_structure_snapshots(days=30)`
4. `doctor` 增加 `data.perp_structure`：
   - 无数据：info 或 warn（取决于是否启用）
   - 过旧：warn
   - 新鲜：ok，显示 symbol、age、data_quality

验收：

- focused storage tests。
- doctor tests。
- 不触碰 execution/risk/live。

### P2：VergexStructureCollector

1. 新增配置：
   - `enable_vergex_structure: bool = False`
   - `vergex_structure_endpoint: str | None = None`
   - `vergex_structure_api_key: str | None = None` 或 secret ref
   - `vergex_structure_symbols: list[str] = []`
2. 前端 Settings 同步增加控制项（遵守 Config ↔ UI rule）。
3. collector 只接受白名单字段，丢弃拒收字段。
4. scheduler 低频运行，建议 10-15 分钟，不进入热路径。
5. 无 key 或未配置时诚实跳过。

验收：

- collector 单元测试用 fixture，不打真实 Vergex。
- 配置和 UI 测试。
- doctor 能看见启用但无数据/有数据/过旧三种状态。

### P3：market_observer 消费

1. `context_builder` 增加 `perp_structure` 参数。
2. `render_enriched_context_md` 增加 `### Perp structure` 块。
3. `market_observer` 从 DB 读取新鲜 structure 快照，拼入 enriched context。
4. 只影响 prompt 证据，不直接改变 proposal 生成阈值。

验收：

- 给定新鲜 structure row，prompt 含 POC / liquidation fuel / concentration。
- 给定 stale row，prompt 不含或明确 stale。
- `data_quality=degraded` 必须显示 degraded。

### P4：shadow evaluation

1. 每个 structure snapshot 记录后续 1h/4h/24h price outcome。
2. 先评估这几个问题：
   - `short_liq_above_usd > long_liq_below_usd` 是否更容易上行？
   - 价格靠近 POC / support / resistance 时，反转或突破概率如何？
   - top50 concentration 高时，波动和止损风险是否更高？
   - `data_quality=degraded` 的信号是否显著变差？
3. 形成本地 `structure_signal_effectiveness` 报告。

验收：

- 至少 2 周 observation window。
- 不以单日样本决定是否进交易链。
- 只在本地数据证明有用后，才考虑进入 scoring。

### P5：进入交易决策前的安全门

只有满足以下条件才允许影响交易排序或仓位：

- 数据源连续稳定。
- doctor 绿。
- shadow evaluation 证明有边际价值。
- prompt 中能解释该信号如何支持或反对交易。
- 不和 risk engine 冲突。
- 不绕过 System Operator / PI / risk approval。

第一版最多允许：

- 降低低质量机会排序。
- 提醒避免追空/追多。
- 增加“需要人工确认”的理由。

不允许：

- 单独触发开仓。
- 单独提高杠杆。
- 单独解除 kill switch 或 live gate。

---

## 7. 安全与合作边界

### 7.1 对 Vergex 合作接口的要求

Vergex 最好提供一个专门的 partner endpoint，只返回聚合结构信号：

- 不返回完整地址。
- 不返回用户/策略/agent/交易所账户信息。
- 不返回内部 prompt。
- 不同时返回足以逆向权重的完整训练对。
- 每条数据有 `as_of_time`、`data_quality`、`source_version`。

### 7.2 Aletheia 对外 API 的反向教训

以后我们自己的 API / dashboard 也要避免：

- 同时暴露全部输入指标、维度分、最终综合分、详细公式解释。
- 把 prompt、策略私有配置、agent 内部状态原样返回。
- 暴露完整交易所资产、订单、持仓给非 owner 上下文。
- 用过宽分页导出内部历史信号。

如果以后 Aletheia 有 marketplace 或策略分享，默认只分享结果摘要，不分享私有 prompt 和完整参数。

---

## 8. 测试清单

| 层 | 测试 |
|---|---|
| 数据合同 | fixture 包含拒收字段时，collector 丢弃并不落库 |
| 存储 | insert/latest/prune 正常 |
| doctor | disabled / enabled-no-data / stale / fresh 四态 |
| 渲染 | fresh 渲染、stale 不渲染、degraded 明示 |
| 数据利用 | `docs/design/data_utilization.md` 登记 + DU claim |
| 配置 | schema 字段有 Settings 控件 |
| shadow | outcome 记录不会影响交易状态 |

建议命令：

```powershell
uv run pytest tests/specs/spec_data_utilization.py -q
uv run pytest tests/test_doctor.py -q
uv run pytest tests/test_config.py -q
uv run pytest tests/test_market_context.py -q
```

---

## 9. 推荐实施顺序

1. **先做 P0 文档/spec 登记**：锁住数据来源、消费者、拒收字段。
2. **再做 P1 存储/doctor**：确保接入前可观测。
3. **再做 P2 collector**：只用 fixture 和 partner endpoint，禁用默认开关。
4. **再做 P3 prompt 消费**：让 agent 看见结构证据。
5. **再做 P4 shadow evaluation**：用本地结果证明信号有效。
6. **最后才考虑 P5 影响排序/仓位**：且必须保持 risk/execution 不变。

---

## 10. 暂不做事项

| 事项 | 暂不做理由 |
|---|---|
| 复刻完整 Vergex composite_z | 黑盒分数不如可解释字段；也不一定适配 Aletheia |
| 接入完整 whale 地址库 | 合作边界和隐私风险高；第一版只需聚合 |
| 用 Vergex AI 建议直接开仓 | 破坏 Aletheia 决策链和审计链 |
| 自建 Hyperliquid 全量地址索引 | 工程量大，先验证 partner 聚合信号是否有价值 |
| 接入 marketplace 策略 prompt | 不符合合作安全边界，也会污染本地 agent |

---

## 11. 最小可交付版本

最小版本只需要做到：

1. 有 `perp_structure_snapshots` 表。
2. 有 fixture collector 写入 BTC/ETH/SOL 三个样例。
3. `doctor` 能看到数据新鲜度。
4. `market_observer` prompt 能显示：
   - POC / Value Area
   - short-liq vs long-liq fuel
   - top10/top50 concentration
   - data_quality / as_of_time
5. 所有信号只 observation，不影响下单。

这版就足够开始 2 周 observation window。

