# 低价值 Mock 测试清理清单

> 审计日期：2026-06-23
> 总计：~26 个低价值测试，分布在 7 个文件中
> 操作：后续重构时顺手删除或重写为 e2e/contract 测试

---

## Tier 1 — 几乎全是低价值（可整文件重写或删除）

### 1. `tests/test_event_triggers.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 7 |
| 低价值 | 6 |
| 保留 | 1 (`test_get_watched_symbols_includes_positions`) |

**模式**: patch `_notify`，只 assert `mock_notify.assert_called_once()` / `assert_not_called()`。
不验证计算结果、返回值或 DB 状态。

**处理建议**: 删除 6 个 mock 测试，保留 1 个。如果需要验证触发逻辑，改写为检查 DB 中是否产生了对应记录。

---

### 2. `tests/test_event_driven_design.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 9 |
| 低价值 | 7 |
| 保留 | 2 (`test_emitter_emit_calls_insert_opportunity`, `test_emitter_signal_requires_active_universe_member`) |

**模式**: patch `_run_strategy_designer`，4-5 个 setup patch，只 assert `mock_trigger.assert_called_once()` 或 `assert_not_called()`。

**处理建议**: 删除 7 个。保留的 2 个验证了实际 DB 状态。

---

## Tier 2 — 部分低价值（删除坏子集，保留好的）

### 3. `tests/test_learning_loop_integration.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 14 |
| 低价值 | 4 (TestPostTradeEventChain 类) |
| 保留 | 10 |

**低价值测试**:
- `test_trigger_fires_when_enough_lessons` — 只 assert_called_once
- `test_full_flow_triggers_on_case_studies` — 6 patch, 只验证 call args
- `test_no_trigger_when_insufficient_lessons` — 只 assert_not_called
- `test_no_adapt_when_insufficient_lessons` — 同上

**保留**: P0/P1/P2 regime tests、penalty tests 全部有价值。

---

### 4. `tests/test_paper_trader_guards.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 9 |
| 低价值 | 3 |
| 保留 | 6 |

**低价值测试**:
- `test_returns_evaluation_on_match` — mock evaluate_guards, assert 返回值不变
- `test_caps_penalty_at_max` — 同上模式
- `test_builds_context_with_risk_flags` — 验证 mock 调用参数构造

**保留**: `TestMakeCandidateGuardFields` 类全部有价值（验证实际 dict 结构）。

---

### 5. `tests/test_crypto_fallback.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 18 |
| 低价值 | 3 |
| 保留 | 15 |

**低价值测试**:
- `test_first_provider_succeeds` — 唯一有意义的 assert 是 `p2.assert_not_called()`
- `test_close_calls_all` — patch `.close()` 只验证被调用
- 1 个 borderline isinstance 测试

---

### 6. `tests/test_strategy_orchestrator.py`

| 指标 | 值 |
|------|---|
| 总测试数 | 7 |
| 低价值 | 2 |
| 保留 | 5 |

**模式**: 7+ patches，混合了 assert_called_once 和真实 result 断言。
低价值部分是 call-verification 那一半。

---

## Tier 3 — 极低优先级

### 7. `tests/test_universe_service.py`

| 指标 | 值 |
|------|---|
| 低价值 | 1-2 |

`test_refresh_returns_none_on_failure` — 验证异常时返回 None，价值极低但无害。

---

## 高价值文件（不要动）

以下文件经审计全部有价值，使用真实 DB / ASGI roundtrip / 精确数值断言：

- `test_operator_loop.py`, `test_operator_routes.py`, `test_strategy_lifecycle.py`
- `test_risk_engine.py`, `test_position_manager.py`, `test_daily_loss_breaker.py`
- `test_kill_switch.py`, `test_exchange_sync.py`, `test_model_provider.py`
- `test_data_collectors.py`, `test_universe_scanners.py`, `test_demo_trading.py`
- `test_self_heal.py`, `test_supervisor.py`, `test_startup_gate.py`
- `test_pnl_pipeline.py`, `test_market_aware_exits.py`, `tests/e2e/*`

---

## 清理原则

1. **不急着批量删** — 碰到哪个文件时顺手清理
2. **删了 mock 测试后**，如果该功能确实需要测试覆盖，写一个 e2e 管道场景替代
3. **contract test 替代**：对于模块间接口（Observer→Ranker、Ranker→RiskEngine），写 schema 契约测试比 mock 测试有价值得多
4. **保留的标准**：测试是否验证了"数据经过计算后的正确性"？如果只验证"某函数被调用了" → 删
