# Filter Evaluation Framework（过滤器评估框架）

**状态：** 待实现  
**优先级：** 高  
**背景：** 2026-07-09 发现 `_proposal_direction_overheated` 参数设置过严（min_sample=5, max_share=0.80），导致 18 小时内 23 个有效多头决策被系统性拦截，未能转化为提案。根因是缺乏对 filter 行为的量化评估，参数纯靠直觉设定。

---

## 目标

每当新增 filter 或修改 filter 参数时，必须能够在提交前：
1. 用贴近真实市场的模拟数据集**量化**该 filter 的拦截率、误拦截率、漏拦截率
2. 验证 filter 行为是否符合预期（正常行情放行、异常信号拦截）
3. 对比不同参数组合的效果，辅助参数选择

---

## 架构设计

### 核心思路

不造假数据——**从 `trade_proposals` 历史表直接重放**，同时叠加若干构造的极端场景（趋势行情、信号失控、随机噪声）。评估模块作为独立脚本/测试，不依赖运行中的 gateway。

### 三层数据集

```
Layer 1: 历史重放 (Historical Replay)
  来源: trade_proposals + pi_decisions 历史数据
  目的: 验证新参数下历史事件的处理结果是否改善

Layer 2: 构造场景 (Synthetic Scenarios)  
  来源: 脚本生成，模拟典型市场状态
  场景清单见下方

Layer 3: 统计验证 (Statistical Sanity)
  对 Layer 1+2 的输出做统计检验
  确认拦截率在合理区间内（不能接近 0% 也不能接近 100%）
```

### 构造场景清单

| 场景名 | 描述 | 预期行为 |
|--------|------|----------|
| `bull_regime` | 24h 内 20 个多头提案（BTC 牛市行情） | 不应触发熔断（合理的方向性集中） |
| `bear_regime` | 24h 内 20 个空头提案（整体下跌） | 不应触发熔断 |
| `signal_runaway` | 24h 内 30 个多头提案后突然出现多头决策 | 只有在极端浓度（>95%）时才拦截 |
| `mixed_regime` | 多空各半，穿插出现 | 不应触发熔断 |
| `cold_start` | 24h 内只有 3 个提案（刚启动） | min_sample 未达，不应触发 |
| `manipulation_spike` | 2h 内涌入 50 个同向提案 | 应触发熔断（真正的异常集中） |
| `high_conviction_override` | direction=100% long，但 conviction=80 | 高置信度豁免，应放行 |
| `cross_symbol_trend` | BTC/ETH/SOL/MON 同时多头（行情共振） | 不应拦截（多品种同向是趋势共振，非信号失控） |

---

## 文件结构

```
tests/
  filter_eval/
    __init__.py
    conftest_filter.py          # 共用 fixtures：db fixture、scenario builder
    scenarios/
      direction_brake.py        # _proposal_direction_overheated 的场景定义
      risk_engine.py            # （占位）risk engine filter 场景
      lesson_block.py           # （占位）lesson_block_direction 场景
    test_direction_brake_eval.py   # 主评估测试（可直接 uv run pytest）
    report.py                      # 输出 Markdown 报告的辅助脚本
```

---

## 实现规格

### `scenarios/direction_brake.py`

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ProposalRecord:
    direction: Literal["long", "short"]
    created_at: str          # ISO-8601
    conviction: int = 60
    symbol: str = "BTC/USDT"

@dataclass
class FilterScenario:
    name: str
    description: str
    history: list[ProposalRecord]           # 24h 窗口内已有的历史提案
    incoming: list[ProposalRecord]          # 待评估的新提案请求
    expected_blocked: list[bool]            # 与 incoming 一一对应
    expected_passed: list[bool] = field(init=False)

    def __post_init__(self):
        self.expected_passed = [not b for b in self.expected_blocked]

# 示例场景
BULL_REGIME = FilterScenario(
    name="bull_regime",
    description="20 个多头提案后，新多头决策，不应被拦截（95% 阈值未达）",
    history=[ProposalRecord("long", f"2026-07-06T{h:02d}:00:00+00:00") for h in range(20)],
    incoming=[ProposalRecord("long", "2026-07-06T23:30:00+00:00", conviction=60)],
    expected_blocked=[False],  # 20/20=100% 但阈值 0.95，且样本仅 20 >= 15，所以……
    # 注：20/20=100% > 95%，conviction=60 < 75，应该被拦截！
    # 这正是评估的价值：发现预期与实际的偏差，再调参
)
```

> **设计说明：** `expected_blocked` 是设计者对"理想行为"的主观标注。如果评估结果与预期不符，说明参数或逻辑需要进一步调整。这个不符本身就是有价值的信息。

### `test_direction_brake_eval.py` 核心结构

```python
import pytest
from aletheia.agent_shell.trading_decision import _proposal_direction_overheated
from .scenarios.direction_brake import ALL_SCENARIOS

@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_direction_brake_scenario(scenario, mock_proposals_db):
    """
    对每个构造场景运行 _proposal_direction_overheated，
    验证结果与 expected_blocked 一致。
    mock_proposals_db fixture 将 scenario.history 注入临时 SQLite。
    """
    for record, expected_blocked in zip(scenario.incoming, scenario.expected_blocked):
        result = _proposal_direction_overheated(record.direction, record.conviction)
        blocked = result is not None
        assert blocked == expected_blocked, (
            f"Scenario '{scenario.name}': expected blocked={expected_blocked}, "
            f"got blocked={blocked} (reason: {result!r})\n"
            f"  direction={record.direction}, conviction={record.conviction}, "
            f"history_count={len(scenario.history)}"
        )
```

### `conftest_filter.py` fixture

```python
import pytest
import sqlite3
from datetime import datetime, timezone

@pytest.fixture
def mock_proposals_db(tmp_path, monkeypatch):
    """
    创建临时 SQLite，注入场景的 history proposals，
    monkeypatch aletheia.storage.sqlite.get_db 返回这个临时 db。
    """
    db_path = tmp_path / "test_proposals.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            created_at TEXT,
            source TEXT DEFAULT 'native'
        )
    """)
    conn.commit()

    def inject(scenario):
        for i, p in enumerate(scenario.history):
            conn.execute(
                "INSERT INTO trade_proposals VALUES (?,?,?,?,?)",
                (f"hist_{i}", p.symbol, p.direction, p.created_at, "native")
            )
        conn.commit()

        class _FakeDB:
            def execute(self, sql, params=()):
                return conn.execute(sql, params)

        monkeypatch.setattr(
            "aletheia.storage.sqlite.get_db",
            lambda: _FakeDB()
        )
        return conn

    return inject
```

---

## 执行方式

```bash
# 运行所有 filter 评估场景
uv run pytest tests/filter_eval/ -v

# 只跑方向熔断评估
uv run pytest tests/filter_eval/test_direction_brake_eval.py -v

# 生成 Markdown 报告
uv run python tests/filter_eval/report.py > docs/archive/legacy/plans/filter_eval_report.md
```

---

## 新增 / 修改 filter 的标准流程

```
1. 修改 filter 代码 / 参数
2. 在对应 scenarios/*.py 中添加或更新覆盖该 filter 的场景
3. 运行 pytest tests/filter_eval/ — 必须全部通过才可提交
4. 如有参数调整，在 PR / commit message 中附上评估结果摘要
```

---

## 待实现的其他 filter 评估模块（占位）

| Filter | 来源文件 | 评估场景文件 | 状态 |
|--------|----------|-------------|------|
| `_proposal_direction_overheated` | `trading_decision.py` | `scenarios/direction_brake.py` | **本期实现** |
| `lesson_block_direction` | `risk/engine.py` | `scenarios/lesson_block.py` | 待定 |
| `sector_concentration` | `risk/engine.py` | `scenarios/sector_conc.py` | 待定 |
| `symbol_already_open` | `risk/engine.py` | `scenarios/symbol_open.py` | 待定 |
| `execution_eligible` gate | `universe/scanner.py` | `scenarios/exec_eligible.py` | **本期实现（由 sub agent 处理）** |

---

## 实现优先级

1. `tests/filter_eval/` 骨架（conftest + scenario dataclass） — **优先**
2. `direction_brake.py` 完整场景集 + `test_direction_brake_eval.py` — **优先**
3. `report.py` Markdown 输出 — 次优先
4. 其他 filter 的场景文件 — 按需添加
