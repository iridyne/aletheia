---
type: reference
title: Test Safety Guards
description: Pytest safety guards that prevent real orders, real LLM calls, and real network access during tests.
tags: [testing, safety, guards, conftest, pytest]
openwiki:
  roles: [testing, operations]
  change_kinds: [lifecycle]
  source_paths:
    - tests/conftest.py
  symbols:
    - prevent_real_orders
    - _guard_bitget_order
    - _PYTEST_MARKERS
    - _INTEGRATION_TOKENS
    - _SLOW_PATH_TOKENS
    - _DOMAIN_PATTERNS
  test_paths:
    - tests/conftest.py
  invariants:
    - Tests never touch the real exchange — BitgetClient.place_order is monkeypatched by default.
    - Real LLM calls are blocked unless a test provides its own mock.
    - Real network calls are skipped unless explicitly enabled.
  validation_commands:
    - uv run pytest tests/test_risk_engine.py
    - uv run pytest -m "not e2e and not integration and not slow"
---

# Test Safety Guards

Aletheia's test suite uses **autouse safety guards** in `tests/conftest.py` to prevent tests from placing real orders, calling real LLM APIs, or hitting real network endpoints. These guards are structural — they apply to all tests by default.

## The Problem

`bitget_demo` (the default `paper_execution_mode`) places REAL orders on Bitget's demo API. Secrets are read from the OS keyring first, which the per-test `tmp_path` DATA_DIR monkeypatch does NOT sandbox. Without a guard, an unmocked test that runs paper_trader to a trade could fire a real demo order over the network during pytest.

## The Solution

The autouse guard in `conftest.py` makes "tests never touch the real exchange" structural: it replaces `BitgetClient.place_order` with a loud error by default. Tests that exercise the exchange path provide their own `monkeypatch.setattr(...place_order, fake)`, which overrides this within the test body.

## Safety Guard Fixtures

### `prevent_real_orders`

```python
@pytest.fixture(autouse=True)
def prevent_real_orders(monkeypatch):
    """Prevent real orders during tests."""
    if not pytest.mark.real_place_order:
        monkeypatch.setattr(
            "aletheia.execution.placement.place_order",
            mock_place_order_paper
        )
        monkeypatch.setattr(
            "aletheia.exchanges.bitget.place_order",
            _guard_bitget_order
        )

def _guard_bitget_order(*args, **kwargs):
    """Hard guard: never allow real Bitget orders."""
    raise RuntimeError(
        "Real Bitget order blocked during tests. "
        "Use paper trading mode."
    )
```

This fixture is `autouse=True` — it applies to **all tests** in the repository. There is no way to accidentally submit a real order through Bitget during testing.

### Guard Override

Tests that need to exercise the exchange path can override the guard:

```python
@pytest.mark.real_place_order
def test_bitget_order(monkeypatch):
    monkeypatch.setattr(BitgetClient, "place_order", fake_place_order)
    # Test code here
```

## Test Markers

### Performance Tiers

| Marker | Description | When to use |
|---|---|---|
| `@pytest.mark.fast` | Default AI feedback set; cheap local tests | Every bug-fix loop |
| `@pytest.mark.integration` | Tests crossing route, DB, scheduler, broker, runtime, or service boundaries | Multi-component tests |
| `@pytest.mark.e2e` | End-to-end pipeline tests | Full system integration |
| `@pytest.mark.slow` | Manually marked slow tests | Daily loop can skip with `-m 'not slow'` |

### Safety Guards

| Marker | Description | Safety Mechanism |
|---|---|---|
| `@pytest.mark.real_place_order` | Test exercises BitgetClient.place_order directly with httpx mocked (no network) | Opt out of the place_order guard |
| `@pytest.mark.real_llm` | Test provides its own LLM mock | Opt out of the fast-fail guard |
| `@pytest.mark.real_network` | Test needs real network calls | Opt out of the market-data guard |
| `@pytest.mark.real_db` | Test requires a real file-backed SQLite DB (WAL/lock/cross-connection) | Opt out of the in-memory DB injection |

### Spec Layers

| Marker | Description |
|---|---|
| `@pytest.mark.spec` | Spec-driven development test |
| `@pytest.mark.L2a` | Structural spec check (static, fast) |
| `@pytest.mark.L2b` | Behavioral spec check (needs fixtures) |
| `@pytest.mark.L2c` | Invariant spec check (cross-operation) |

### Tier Classifications

| Marker | Description |
|---|---|
| `@pytest.mark.tier_unit` | Fast unit/pure-function tests |
| `@pytest.mark.tier_integration` | Tests crossing module, route, DB, scheduler, broker, or runtime boundaries |
| `@pytest.mark.tier_e2e` | End-to-end pipeline tests |
| `@pytest.mark.tier_spec` | Executable specification tests under tests/specs |

### Online Access

| Marker | Description |
|---|---|
| `@pytest.mark.online` | Tests that hit a live external API (network-gated; skipped when offline) |

### Domain Classifications

| Marker | Owning Domain |
|---|---|
| `@pytest.mark.domain_operator` | System Operator / agent shell / operator loop / trading decision / proposal |
| `@pytest.mark.domain_universe` | Universe scanner / allocator / signal / filter evidence / filter replay |
| `@pytest.mark.domain_serenity` | Serenity Lens / research pipeline |
| `@pytest.mark.domain_risk` | Risk Engine / kill switch / risk route |
| `@pytest.mark.domain_execution` | Order, broker, position, trade, outcome, exchange sync, PnL, reconciliation, sizing, execution-path |
| `@pytest.mark.domain_market_data` | Market data, exchange data, price feed, collector, OHLCV, orderbook, instrument, data freshness |
| `@pytest.mark.domain_learning` | Knowledge, learning, dreaming, lesson, feedback, alpha attribution, analytics, experience loop |
| `@pytest.mark.domain_frontend` | Frontend/static UI source checks and UI component tests |
| `@pytest.mark.domain_runtime` | Gateway, scheduler, runtime continuity, lifecycle, supervisor, workflow, connectivity, notification |
| `@pytest.mark.domain_storage` | Storage, DB, migration, retention, backup, IO honesty, audit-log |
| `@pytest.mark.domain_security` | Secrets, auth, permission, trust boundary, export redaction, safety guard |
| `@pytest.mark.domain_config` | Config/settings |
| `@pytest.mark.domain_misc` | Fallback domain marker |

## Test Isolation

All tests use:
1. **Temporary databases** — Each test gets a fresh DB
2. **Mock services** — LLM, exchange APIs, network calls mocked by default
3. **Deterministic data** — All test data is fixed, not random
4. **Cleanup** — All temporary resources cleaned after each test

## Windows Event Loop Policy

The test process applies the same process-level SelectorEventLoop policy as production:

```python
from aletheia.async_utils import apply_windows_selector_policy as _apply_selector_policy
_apply_selector_policy()
```

This prevents broker tests from hanging ~255s on IOCP teardown.

## Hypothesis Settings

```python
from hypothesis import settings as _h_settings, HealthCheck
_h_settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
_h_settings.register_profile("dev", max_examples=20, suppress_health_check=[HealthCheck.too_slow])
_h_settings.load_profile("dev")
```

## Key Invariants

1. **No real orders** — BitgetClient.place_order is monkeypatched by default.
2. **No real LLM** — Real LLM calls are blocked unless a test provides its own mock.
3. **No real network** — Real network calls are skipped unless explicitly enabled.
4. **Windows compatibility** — The test process matches production's event loop policy.

## Relationship to Other Components

- [Test Infrastructure](test-infrastructure.md) — Full test framework reference
- [Risk Engine](../components/risk-engine.md) — Risk calculation tests
- [Execution Gateway](../components/execution-gateway.md) — Execution tests
