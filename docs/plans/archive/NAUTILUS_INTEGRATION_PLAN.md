# NautilusTrader 集成方案

> 状态：方向已定，暂不实施。等策略产出量足够高时再启动。

## 背景

Aletheia 自建的 bar-level 回测器（`aletheia/strategy/backtest.py`）是一个过渡方案。
长期应使用 [nautilus-practice](https://github.com/iridite/nautilus-practice) 作为验证引擎，
它是 [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) 的 production wrapper。

## 为什么用 wrapper 而不是直接调原版

| 原版痛点 | wrapper 已解决 |
|---------|---------------|
| 配置 boilerplate（Venue/Instrument/DataConfig/Strategy 多对象实例化） | 3 层 YAML→Pydantic→Adapter，一个 `active.yaml` 切换 |
| 无内建仓位百分比/ATR 计算 | BaseStrategy 提供 3 种模式（fixed/percent/ATR risk） |
| 数据需预存在 | 自动下载 + Parquet 缓存 + Hash 一致性校验 |
| one instance = one instrument | Universe + engine_high 封装批量多标的 |
| 结果分散在对象里 | 统一 JSON 输出到 `output/backtest/result/` |

wrapper 之下仍是 NautilusTrader Rust 引擎（纳秒精度、事件驱动、18+ 交易所适配）。

## 方案 A：subprocess 调用

```
Aletheia (策略工厂)                     nautilus-practice (验证引擎)
┌─────────────────────┐                 ┌─────────────────────────┐
│ strategy_designer   │                 │                         │
│   → RuleSet JSON    │──translate──→   │ config/strategies/*.yaml│
│   → symbol/tf/dir   │                 │ config/environments/    │
│                     │                 │                         │
│ orchestrator        │──subprocess──→  │ python main.py backtest │
│   (daily 04:00)     │                 │   --type high           │
│                     │                 │   --symbol BTCUSDT      │
│                     │←──parse JSON──  │ output/backtest/result/ │
│                     │                 │   → pnl, sharpe, dd,    │
│ review.py           │                 │     win_rate, trades    │
│   (kill/keep/review)│                 └─────────────────────────┘
│                     │
│ performance_ledger  │
│   (meta-metrics)    │
└─────────────────────┘
```

### 集成要件（启动时需实现）

1. **翻译层**：RuleSet JSON → nautilus-practice Strategy Python 文件或 YAML config
   - 选项 A：生成 `.py` 策略文件继承 BaseStrategy，on_bar 里检查条件
   - 选项 B：在 wrapper 里加一个 "generic rule evaluator" Strategy 接收 JSON 规则
   - 推荐 B — 避免动态代码生成的安全风险

2. **数据共享**：两个项目的 OHLCV 数据如何对齐
   - nautilus-practice 用 CSV/Parquet 在 `data/raw/`
   - Aletheia 用 SQLite `ohlcv_candles` 表
   - 需要一个导出脚本或共享目录

3. **结果解析**：wrapper 输出 JSON → Aletheia review.py metrics 映射
   ```json
   // nautilus-practice output format:
   {
     "pnl": {"USDT": {"PnL (total)": 150.5, "PnL% (total)": 1.5, "Win Rate": 0.55}},
     "returns": {"Sharpe Ratio (252 days)": 1.8, "Max Drawdown": -0.05},
     "performance": {"total_trades": 42}
   }
   ```
   映射到 review.py 的: trade_count, win_rate, expectancy, profit_factor, max_drawdown_pct

4. **环境隔离**：nautilus-practice 需独立 venv（NautilusTrader 依赖重，Rust 编译）

## nautilus-practice 架构速览

```
nautilus-practice/
├── backtest/
│   ├── engine_high.py    # BacktestNode + Parquet（推荐，快 32%）
│   └── engine_low.py     # BacktestEngine 直接调用（调试/自定义数据）
├── strategy/
│   ├── core/base.py      # BaseStrategy（仓位计算/自动止损/HEDGING跟踪）
│   ├── common/           # 可复用组件（indicators/signals/universe）
│   └── *.py              # 具体策略（Keltner RS, Dual Thrust）
├── core/
│   ├── adapter.py        # YAML→Engine 配置适配器
│   └── schemas.py        # Pydantic 配置模型
├── config/
│   ├── active.yaml       # 活跃策略+环境选择器
│   ├── strategies/       # 策略参数 YAML
│   └── environments/     # 环境配置（dev/sandbox/live）
└── output/backtest/result/*.json  # 回测结果
```

CLI: `uv run python main.py backtest --type high --env dev --symbol BTCUSDT`

## 当前 Aletheia 自建回测的定位

在集成完成前，`aletheia/strategy/backtest.py` 作为**快速筛选器**存在：
- 用途：每日 04:00 cron 快速跑 500 bar 粗筛，秒级出结果
- 精度：bar-level，flat fee，单仓位 — 只够判断"明显垃圾"
- 互补：通过粗筛的策略才进入 nautilus-practice 做高保真验证

这不是冗余，是两级过滤漏斗：粗筛（自建）→ 精筛（NautilusTrader）。

## 启动条件

当以下两个条件同时满足时启动集成：
1. strategy_designer 稳定产出 ≥3 个/天的 RuleSet 策略（有足够量需要高保真验证）
2. nautilus-practice 已在本机或同局域网可调用（wrapper 部署就绪）
