---
type: concept
title: Test Infrastructure
description: Comprehensive test framework with pytest markers, safety guards, and 7 test tiers.
tags: [testing, pytest, markers, safety, conftest]
---

# Test Infrastructure

Aletheia uses a sophisticated **pytest-based** testing infrastructure with **test markers for safety guards, performance tiers, and domain classification**. The infrastructure is designed to prevent real trades during testing, support multiple execution speeds, and organize tests by domain ownership.

## Test Markers

**Source:** `tests/conftest.py`

### Performance Tiers

| Marker | Description | When to use |
|---|---|---|
| `@pytest.mark.fast` | Unit tests that run in <1s | Default for quick validation |
| `@pytest.mark.integration` | Tests requiring multiple components | Requires more components initialized |
| `@pytest.mark.e2e` | End-to-end tests | Full system integration tests |
| `@pytest.mark.slow` | Tests that take >30s | Run with `--runslow` flag |

### Safety Guards

| Marker | Description | Safety Mechanism |
|---|---|---|
| `@pytest.mark.real_place_order` | Tests that place real orders | **Monkeypatched** to redirect to paper trading |
| `@pytest.mark.real_llm` | Tests that call real LLM APIs | **Monkeypatched** to use mock responses |
| `@pytest.mark.real_network` | Tests that hit real network | **Monkeypatched** or skipped by default |
| `@pytest.mark.real_db` | Tests that use real database | **Monkeypatched** to temp database |

**Important Safety Mechanism:** The `real_place_order` marker activates a **monkeypatch guard** in `conftest.py` that prevents any test from placing real orders on the Bitget exchange. All order attempts are redirected to paper trading mode.

### Spec Layers

| Marker | Description |
|---|---|
| `@pytest.mark.spec` | Spec tests (L1a) — Component interface contracts |
| `@pytest.mark.L2a` | Spec tests (L2a) — Integration interface contracts |
| `@pytest.mark.L2b` | Spec tests (L2b) — Data contract validation |
| `@pytest.mark.L2c` | Spec tests (L2c) — Cross-component contracts |

### Tier Classifications

| Marker | Description |
|---|---|
| `@pytest.mark.tier_unit` | Unit tests (L1a) |
| `@pytest.mark.tier_integration` | Integration tests (L2) |
| `@pytest.mark.tier_e2e` | End-to-end tests (L3) |
| `@pytest.mark.tier_spec` | Spec/contract tests |

### Online Access

| Marker | Description |
|---|---|
| `@pytest.mark.online` | Tests that require real-time data | Skipped in offline mode |

### Domain Classifications

| Marker | Owning Domain |
|---|---|
| `@pytest.mark.domain_operator` | System Operator |
| `@pytest.mark.domain_universe` | Universe selection |
| `@pytest.mark.domain_serenity` | Serenity Lens |
| `@pytest.mark.domain_risk` | Risk Engine |
| `@pytest.mark.domain_execution` | Execution Gateway |
| `@pytest.mark.domain_market_data` | Market data collectors |
| `@pytest.mark.domain_learning` | Learning System (AT-01) |
| `@pytest.mark.domain_frontend` | Web frontend |
| `@pytest.mark.domain_runtime` | Runtime Engine |
| `@pytest.mark.domain_storage` | Data layer (SQLite) |
| `@pytest.mark.domain_security` | Security layer |
| `@pytest.mark.domain_config` | Configuration |
| `@pytest.mark.domain_misc` | Miscellaneous |

## Test Organization

```
tests/
├── conftest.py              # Global test configuration, markers, fixtures
├── markers.py               # Marker definitions and constants
├── fixtures/                # Shared test fixtures
│   ├── market_data.py       # Market data fixtures
│   ├── positions.py         # Position fixtures
│   ├── risk.py              # Risk fixtures
│   └── learning.py          # Learning fixtures
├── test_runtime/            # Runtime engine tests
├── test_components/         # Component tests
├── test_data/              # Data layer tests
├── test_learning/          # Learning system tests
├── test_knowledge/         # Knowledge base tests
├── test_risk/              # Risk engine tests
├── test_execution/         # Execution gateway tests
├── test_frontend/          # Web frontend tests
├── test_web/               # Web API tests
└── test_integration/       # End-to-end tests
```

## Running Tests

### Fast Tests (default)

```bash
# Run only fast tests (default)
pytest

# Run with specific markers
pytest -m "fast and domain_risk"

# Run all tests except slow
pytest -m "not slow"
```

### Specific Tiers

```bash
# Run unit tests only
pytest -m "tier_unit"

# Run integration tests
pytest -m "tier_integration"

# Run e2e tests
pytest -m "tier_e2e"

# Run spec tests
pytest -m "tier_spec"
```

### Domain-Specific

```bash
# Run all learning system tests
pytest -m "domain_learning"

# Run all risk engine + execution gateway tests
pytest -m "domain_risk or domain_execution"

# Run all market data tests
pytest -m "domain_market_data"
```

### Safety-Guarded Tests

```bash
# Enable real LLM calls (DANGEROUS - uses real API credits)
pytest -m "real_llm" --runreal

# Enable real network calls
pytest -m "real_network" --runreal

# Enable real database (uses temp DB for isolation)
pytest -m "real_db" --runreal

# Enable real order placement (redirected to paper trading)
pytest -m "real_place_order"
```

### Offline Mode

```bash
# Run all tests in offline mode (skip online tests)
pytest --offline

# Run online tests only
pytest -m "online"
```

## Test Fixtures

**Source:** `tests/conftest.py`, `tests/fixtures/`

### Database Fixtures

```python
@pytest.fixture
def temp_db():
    """Create a temporary database for test isolation."""
    db = create_test_db()
    yield db
    db.cleanup()

@pytest.fixture
def populated_db(temp_db):
    """Database pre-populated with test data."""
    load_test_data(temp_db)
    yield temp_db
```

### Market Data Fixtures

```python
@pytest.fixture
def sample_ohlcv():
    """Sample OHLCV data."""
    return load_fixture("market_data/btc_ohlcv.json")

@pytest.fixture
def mock_price_feed():
    """Mock price feed for testing."""
    return MockPriceFeed()
```

### Component Fixtures

```python
@pytest.fixture
def risk_engine(temp_db):
    """Initialized risk engine."""
    return RiskEngine(database=temp_db)

@pytest.fixture
def committee(temp_db, risk_engine):
    """Initialized investment committee."""
    return InvestmentCommittee(db=temp_db, risk_engine=risk_engine)
```

## Test Data

**Source:** `tests/fixtures/`, `tests/data/`

Static test data is stored in JSON format:

| File | Contents |
|---|---|
| `market_data/btc_ohlcv.json` | BTC OHLCV history |
| `market_data/eth_ohlcv.json` | ETH OHLCV history |
| `positions/test_positions.json` | Test position data |
| `risk/scenarios.json` | Risk stress test scenarios |
| `learning/experiments.json` | Test experiment definitions |
| `knowledge/test_facts.json` | Test knowledge base facts |

## Safety Mechanisms

### Monkeypatch Guard for Order Placement

**Source:** `tests/conftest.py`

```python
@pytest.fixture(autouse=True)
def prevent_real_orders(monkeypatch):
    """Prevent real orders during tests."""
    if not pytest.mark.real_place_order:
        # Redirect all order placements to paper trading
        monkeypatch.setattr(
            "aletheia.execution.placement.place_order",
            mock_place_order_paper
        )
        # Specifically guard against real Bitget orders
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

### Test Isolation

All tests use:
1. **Temporary databases** — Each test gets a fresh DB
2. **Mock services** — LLM, exchange APIs, network calls mocked by default
3. **Deterministic data** — All test data is fixed, not random
4. **Cleanup** — All temporary resources cleaned after each test

## Hypothesis Settings

**Source:** `tests/conftest.py`

Property-based testing via Hypothesis:

```python
from hypothesis import settings, Phase

HYPOTHESIS_SETTINGS = settings(
    max_examples=100,
    deadline=1000,  # 1 second per test
    phases=[Phase.generate, Phase.shrinks, Phase.explicit],
    suppress_health_check=[HealthCheck.too_slow]
)
```

## CI Configuration

**Source:** `.github/workflows/tests.yml`

CI runs tests in multiple stages:

| Stage | Markers | Timeout |
|---|---|---|
| Fast tests | `-m "fast"` | 10 minutes |
| Integration | `-m "integration and not slow"` | 20 minutes |
| Spec tests | `-m "tier_spec"` | 15 minutes |
| Domain tests | `-m "domain_*"` | 25 minutes each |
| Full suite | (no marker filter) | 60 minutes |

## Test Coverage Requirements

| Component | Coverage Target |
|---|---|
| Core runtime | 90% |
| Risk Engine | 95% |
| Execution Gateway | 90% |
| Learning System | 85% |
| Knowledge Base | 85% |
| Data Layer | 90% |
| Web Frontend | 80% |
| Market Observer | 85% |

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `prevent_real_orders` | `tests/conftest.py` | Auto-use fixture guarding orders |
| `_guard_bitget_order` | `tests/conftest.py` | Hard guard for Bitget API |
| `temp_db` | `tests/conftest.py` | Temporary database fixture |
| `sample_ohlcv` | `tests/conftest.py` | Sample OHLCV fixture |
| `HYPOTHESIS_SETTINGS` | `tests/conftest.py` | Hypothesis test settings |
| `ALL_MARKERS` | `tests/markers.py` | All marker definitions |
| `WINDOWS_EVENT_LOOP` | `tests/conftest.py` | Windows event loop config |

## Test Files by Component

| Component | Test Files |
|---|---|
| Runtime Engine | `tests/test_runtime_engine.py`, `tests/test_event_bus.py`, `tests/test_component_registry.py` |
| Learning System | `tests/test_learning_system.py`, `tests/test_learning_stages.py`, `tests/test_experiments.py`, `tests/test_verdicts.py` |
| Knowledge Base | `tests/test_knowledge_base.py`, `tests/test_fact_critic.py`, `tests/test_guardrails.py` |
| Risk Engine | `tests/test_risk_engine.py`, `tests/test_risk_calculations.py`, `tests/test_stress_tests.py` |
| Execution Gateway | `tests/test_execution_gateway.py`, `tests/test_slippage.py`, `tests/test_bitget_orders.py` |
| Position Manager | `tests/test_position_manager.py`, `tests/test_position_pnl.py` |
| Serenity Lens | `tests/test_serenity_lens.py`, `tests/test_regime_detection.py` |
| Data Layer | `tests/test_data_layer.py`, `tests/test_freshness.py`, `tests/test_migrations.py` |
| Market Observer | `tests/test_market_observer.py`, `tests/test_lofo_patterns.py` |
| Web Frontend | `tests/test_web_frontend.py`, `tests/test_web_api.py`, `tests/test_web_security.py` |
| Committee | `tests/test_committee_core.py`, `tests/test_committee_voting.py` |
| Prediction Markets | `tests/test_prediction_markets.py`, `tests/test_prediction_validation.py` |

## Relationship to Other Components

- [Runtime Engine](../runtime/engine.md) — Engine lifecycle tests
- [Learning System](../components/learning-system.md) — Experiment and verdict tests
- [Risk Engine](../components/risk-engine.md) — Risk calculation tests
- [Execution Gateway](../components/execution-gateway.md) — Order placement tests (paper-traded)
- [Data Layer](../data-layer/overview.md) — Database and freshness tests
