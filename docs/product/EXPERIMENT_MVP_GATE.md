# Round 1 MVP 就绪门控

> **性质**: Round 1 启动的前置条件。所有 6 个领域全部 PASS → Round 1 时钟正式开始。
> 任一 FAIL → 修复后重新检查。验证脚本: `scripts/mvp_gate_check.py`

---

## 一句话定义

系统处于"能产出有效实验数据"的最低可运行状态。不要求完美,只要求数据链路畅通。

---

## 领域 1: 决策引擎（System Operator）

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 1.1 | PI daemon 活跃 | 最近 10min 内有 `pi_decisions` 记录 |
| 1.2 | Signal degradation 不误触发 | `data_health.signal_status` ≠ `"degraded"`(方向修正后的 win_rate) |
| 1.3 | LLM 评估功能正常 | 48h 内 ≥1 笔 `pi_decisions` 记录 conviction > 0 |
| 1.4 | Permission mode 允许交易 | `config.agent.permission_mode` ∈ {Paper, Full} |

**验证命令**:
```python
from aletheia.agent_shell.trading_context import _get_data_health_summary
health = _get_data_health_summary()
assert health["signal_status"] != "degraded"
```

---

## 领域 2: 数据管道

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 2.1 | OHLCV 覆盖 | Active universe 全员 ≥ `min_bars` (200) 1h bars |
| 2.2 | Feature snapshots | Context build 返回 features count ≥ 1 |
| 2.3 | Market regime 新鲜 | 最新 regime 记录 < 1h 前 |
| 2.4 | Opportunities 流动 | 24h 内 ≥ 50 new opportunities |
| 2.5 | Forward outcomes 采集 | `universe_forward_outcomes` pending > 0 |

**降级处理**: 2.1 允许暂时 FAIL(新币等待回填),但 ≥ 75% members 必须 ready。

---

## 领域 3: 执行管道

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 3.1 | Exchange 连接 | `portfolio_snapshots` 最新记录 < 5min |
| 3.2 | 无 stuck orders | status='submitted' 且 > 24h 的 orders = 0 |
| 3.3 | Paper execution mode | `config.exchange.paper_execution_mode` = `"okx_demo"` |
| 3.4 | Fill 路径验证 | 历史 `orders` 表有 status='filled' 记录 |

---

## 领域 4: 风控管道

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 4.1 | Kill switch 未激活 | Config 中无 kill_switch_active = true |
| 4.2 | Daily loss 未触发 | today_realized_pnl > -(max_daily_loss_pct × equity) |
| 4.3 | 仓位上限 > 0 | `max_open_positions_demo` > 0 |
| 4.4 | 风控不无条件 reject | 历史 risk_decisions 近 7d approve 率 > 50% |

---

## 领域 5: 运维与监控

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 5.1 | Gateway 进程存活 | Python 进程存在 |
| 5.2 | Portfolio sync 活跃 | portfolio_snapshots 最新 < 5min |
| 5.3 | Doctor 无 critical | `aletheia doctor` 无 severity=critical 项 |

---

## 领域 6: 实验基础设施

| # | 检查项 | PASS 条件 |
|---|--------|-----------|
| 6.1 | Protocol 文件存在 | `docs/product/EXPERIMENT_PROTOCOL.md` 含 "ACTIVE" |
| 6.2 | CLAUDE.md 冻结规则 | CLAUDE.md 含 "EXPERIMENT FREEZE" |
| 6.3 | Git tag | `experiment/round-1-start` tag 存在 |
| 6.4 | 基线数据 | Protocol 文件含 "基线数据" section |

---

## 允许修复 vs 冻结边界

在 MVP gate PASS 之前(Round 1 正式开始之前),以下修复是合法的:

| 类型 | 合法? | 示例 |
|------|-------|------|
| 测量 bug(影响环境报告准确性) | ✅ | win_rate 方向计算修正 |
| 数据回填(新币种 OHLCV) | ✅ | RESOLV/USDT bars 补齐 |
| 清理历史脏数据 | ✅ | stuck orders 标记 cancelled |
| UI/文档/监控 | ✅ | 本文档 |
| 改变 AI 决策逻辑 | ❌ | 修改 prompt、信号规则、conviction 阈值 |
| 改变 AI 看到的信息内容 | ❌ | 加新数据维度、改 context 结构 |

**关键标准**: "修复后,同一市场状况下 AI 的决策输入是否更准确?" → 是 = 合法修复。
"修复后,AI 看到了之前看不到的信息?" → 是 = 违反冻结。

---

## Round 1 正式开始流程

1. 运行 `scripts/mvp_gate_check.py` → 6 领域全 PASS
2. 48h smoke test: 确认 System Operator 产出 conviction > 0 的评估(不要求出单,只要求 LLM 不再盲目 skip)
3. 打 git tag `experiment/round-1-start`
4. 更新 `EXPERIMENT_PROTOCOL.md` 中 Round 1 起止日期
5. 冻结生效,14 天计时开始

---

## 版本记录

| 日期 | 变更 |
|------|------|
| 2026-06-22 | 初版。定义 6 领域就绪标准,配套 gate_check 脚本 |
