---
type: concept
title: Data Migrations & Schema
description: SQLite database migrations for schema evolution across Aletheia components.
tags: [data, migrations, schema, sqlite, versioning]
---

# Data Migrations & Schema

This page documents Aletheia's database migration system and schema evolution patterns.

## Migration System

**Source:** `files/schema.py`

Aletheia uses a simple sequential migration system. Migrations are applied in numeric order and tracked in the `schema_migrations` table.

### Migration Runner

```bash
# Apply all pending migrations
python -m aletheia.data.migrate

# Show migration status
python -m aletheia.data.migrate status

# Rollback last migration
python -m aletheia.data.migrate rollback

# Migrate to specific version
python -m aletheia.data.migrate to 0015
```

### Migration Format

```python
from aletheia.data.migrations import Migration

class Migration0001(Migration):
    version = "0001"
    description = "Initial schema"
    
    sql_up = """
        CREATE TABLE positions (...);
        CREATE TABLE orders (...);
    """
    
    sql_down = """
        DROP TABLE orders;
        DROP TABLE positions;
    """
```

## Schema Overview

### Market Data Tables

| Table | Purpose |
|---|---|
| `market_data` | OHLCV snapshots |
| `market_data_oi` | Open interest |
| `market_data_funding` | Funding rates |
| `market_data_macro` | Macro indicators |
| `prediction_markets` | Prediction market data |

### Trading Tables

| Table | Purpose |
|---|---|
| `positions` | Active positions |
| `orders` | All orders |
| `fills` | Execution fills |
| `slippage_log` | Slippage analysis |

### Learning Tables

| Table | Purpose |
|---|---|
| `learning_experiments` | Experiment definitions |
| `learning_ledger` | Learning events |
| `learning_verdicts` | Verdict results |
| `behavior_versions` | Version history |
| `learning_rollbacks` | Rollback events |

### Knowledge Tables

| Table | Purpose |
|---|---|
| `knowledge_base` | Facts and lessons |
| `guardrails` | Active guards |
| `guardrail_events` | Guardrail trigger events |

### Audit Tables

| Table | Purpose |
|---|---|
| `audit_log` | Hash-chained audit events |
| `system_events` | System-level events |
| `job_executions` | Scheduler job history |

## Schema Management Best Practices

1. **Always provide rollback SQL** — Every migration must be reversible
2. **Test migrations** — Run against test databases before production
3. **Backup before migrating** — Always backup DB before production migrations
4. **Small, focused migrations** — One logical change per migration
