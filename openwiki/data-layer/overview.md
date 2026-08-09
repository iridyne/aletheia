---
type: component
title: Data Layer Overview
description: SQLite-based data layer with schema-managed tables, migrations, and provider fallback.
tags: [data, sqlite, schema, migrations, providers]
---

# Data Layer Overview

The **Data Layer** (`aletheia/data/`) is Aletheia's central data management system. It uses **SQLite** as the primary storage engine, with a well-defined schema for market data, positions, knowledge, learning, and audit records. Data collection runs as isolated subprocesses with provider fallback and health monitoring.

## Architecture

```mermaid
flowchart TD
    A[Market Data\n(OHLCV, Orderbook)] --> B[SQLite]
    C[Position Data\n(Orders, Fills)] --> B
    D[Knowledge Data\n(Facts, Guardrails)] --> B
    E[Learning Data\n(Experiments, Verdicts)] --> B
    F[Audit Data\n(Hash chain)] --> B
    G[System State\n(Runtime, Health)] --> B
    B --> H[Data Access Layer\n(data/dao.py)]
    H --> I[Query Interface]
    I --> J[Risk Engine]
    I --> K[Position Manager]
    I --> L[Learning System]
    I --> M[Serenity Lens]
    I --> N[Committee]
    I --> O[Market Observer]
```

## Database Schema

**Source:** `files/schema.py` (or `aletheia/data/schema.py`)

### Core Tables

| Table | Description |
|---|---|
| `market_data` | OHLCV snapshots (1m-1d, all symbols) |
| `market_data_oi` | Open interest data |
| `market_data_funding` | Funding rates |
| `market_data_macro` | Macro indicators (VIX, DXY) |
| `market_data_microstructure` | Orderbook depth, bid-ask spread |
| `market_data_derivatives` | Perpetual contract specs |
| `positions` | Active positions |
| `orders` | All orders (placed, filled, cancelled) |
| `fills` | Trade execution details |
| `knowledge_base` | Learned facts and lessons |
| `guardrails` | Active safety rules |
| `learning_experiments` | AT-01 experiments |
| `learning_ledger` | Learning event log |
| `learning_verdicts` | Verdict results |
| `audit_log` | Hash-chained audit events |
| `runtime_state` | Persistent runtime state |
| `component_health` | Component health records |
| `job_executions` | Scheduler job history |
| `system_events` | System-level events |
| `news` | News headlines and sentiment |
| `news_entities` | News entity extractions |

## Migrations

**Source:** `aletheia/data/migrations/`

Migrations use a simple sequential system:

```bash
# Apply migrations
python -m aletheia.data.migrate

# Check migration status
python -m aletheia.data.migrate status

# Rollback last migration
python -m aletheia.data.migrate rollback
```

### Migration Format

```python
# migrations/0015_add_market_microstructure.py
"""Add market microstructure table."""
from aletheia.data.migrations import Migration

class Migration0015(Migration):
    version = "0015"
    description = "Add market microstructure table"
    
    sql_up = """
        CREATE TABLE market_data_microstructure (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            timestamp DATETIME,
            bid_price REAL,
            ask_price REAL,
            spread REAL,
            orderbook JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """
    
    sql_down = "DROP TABLE market_data_microstructure;"
```

## Data Access Layer (DAO)

**Source:** `aletheia/data/dao.py`

The DAO provides type-safe query interfaces:

```python
class DataDAO:
    """Data Access Object for all data queries."""
    
    def get_ohlcv(self, symbol, timeframe, start, end) -> DataFrame:
        """Get OHLCV data for a symbol."""
        ...
    
    def get_recent_positions(self, limit=10) -> List[Position]:
        """Get recent positions."""
        ...
    
    def get_active_guardrails(self) -> List[Guardrail]:
        """Get active guardrails."""
        ...
    
    def get_experiment(self, experiment_id) -> Experiment:
        """Get experiment by ID."""
        ...
    
    def insert_market_data(self, data: MarketDataPoint):
        """Insert a market data point."""
        ...
```

## Data Freshness

**Source:** `aletheia/data/freshness.py`

Validates that market data snapshots are current:

```python
def check_freshness(symbol, timeframe, max_age_seconds=30):
    """Check if market data is fresh."""
    latest = dao.get_latest_timestamp(symbol, timeframe)
    age = (datetime.utcnow() - latest).total_seconds()
    return age <= max_age_seconds
```

### Freshness Thresholds

| Data Type | Max Age | Critical Age |
|---|---|---|
| OHLCV 1m | 120s | 600s |
| OHLCV 5m | 300s | 600s |
| OHLCV 1h | 900s | 1800s |
| OHLCV 1d | 3600s | 7200s |
| Orderbook | 30s | 120s |
| Macro | 3600s | 14400s |
| Funding | 300s | 900s |

## Provider Fallback

**Source:** `aletheia/data/ccxt_provider.py`

The CCXT provider implements primary + fallback:

1. **Primary** — Binance (USDT pairs) → Coinbase (USD pairs)
2. **Fallback** — KuCoin → OKX → Bybit

Each provider has:
- Rate limit budget
- Health tracking (success/failure rate)
- Cooldown on failure (default 300s)

## Backup System

**Source:** `aletheia/data/backup.py`

Daily backup at 02:00 UTC:

```python
def backup_database():
    """Create a backup of the SQLite database."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backups/aletheia_{timestamp}.db"
    
    # Use SQLite backup API for consistency
    shutil.copy2("aletheia.db", backup_path)
    
    # Keep last 7 days of backups
    cleanup_old_backups(keep_days=7)
    
    return backup_path
```

## Data Archiving

**Source:** `aletheia/data/archive.py`

Data older than 90 days is archived:
- Moved to compressed Parquet files
- Retained for 2 years
- Archived data accessible via same DAO interface (transparent)

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `DataDAO` | `data/dao.py` | Data access layer |
| `check_freshness` | `data/freshness.py` | Freshness validation |
| `CCXTProvider` | `data/ccxt_provider.py` | Exchange data provider |
| `apply_migrations` | `data/migrations/__init__.py` | Migration runner |
| `backup_database` | `data/backup.py` | Database backup |
| `archive_old_data` | `data/archive.py` | Data archiving |
| `SCHEMA_VERSION` | `data/schema.py` | Current schema version |
| `FRESHNESS_THRESHOLDS` | `data/freshness.py` | Freshness config |

## Test Coverage

- `tests/test_data_layer.py` — Tests for DAO operations
- `tests/test_freshness.py` — Tests for freshness checks
- `tests/test_migrations.py` — Tests for migration system
- `tests/test_ccxt_provider.py` — Tests for provider fallback
- `tests/test_backup.py` — Tests for backup system
- `tests/test_archive.py` — Tests for data archiving

## Relationship to Other Components

- [Data Collector Subprocess](../architecture/data-collector-subprocess.md) — Data collection
- [Learning System](../components/learning-system.md) — Learning data storage
- [Knowledge Base](../components/knowledge-base.md) — Knowledge storage
- [Risk Engine](../components/risk-engine.md) — Market data input
- [Position Manager](../components/position-manager.md) — Position data storage
