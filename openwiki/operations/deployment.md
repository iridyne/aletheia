---
type: concept
title: Deployment Guide
description: Production deployment, operational runbooks, and system lifecycle management.
tags: [operations, deployment, production, runbook, lifecycle]
---

# Deployment Guide

Aletheia is deployed as a **single daemon process** with an optional web frontend. The system supports multiple deployment modes, from local development to production cloud deployment.

## Deployment Modes

| Mode | Description | Components | Database |
|---|---|---|---|
| `local-dev` | Local development, all mock services | Daemon + Web | Temp SQLite |
| `local-paper` | Local paper trading | Daemon + Web | Local SQLite |
| `staging` | Staging environment | Daemon + Web + Monitoring | Staging SQLite |
| `production` | Production live trading | Daemon + Web + Monitoring + Alerting | Production SQLite |

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|---|---|---|
| **CPU** | 4 cores | 8+ cores |
| **RAM** | 8 GB | 16+ GB |
| **Storage** | 50 GB SSD | 200+ GB SSD |
| **Network** | 100 Mbps | 1 Gbps |

### Dependencies

```bash
# Python 3.10+
python3 --version

# System packages
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for development

# Exchange API keys (stored in keyring)
python -m aletheia.exchanges.setup_keys
```

## Deployment Steps

### 1. Install Dependencies

```bash
git clone <repo>
cd aletheia
pip install -r requirements.txt
```

### 2. Configure Environment

**Source:** `config/production.yaml`

```bash
# Copy config template
cp config/template.yaml config/production.yaml

# Edit for production
vim config/production.yaml
```

Key production settings:
```yaml
# config/production.yaml
runtime:
  name: production
  log_level: INFO
  
data:
  database_path: /var/lib/aletheia/aletheia.db
  backup_dir: /var/backups/aletheia

exchanges:
  binance:
    api_key: ${BINANCE_API_KEY}
    api_secret: ${BINANCE_API_SECRET}

web:
  enabled: true
  host: 0.0.0.0
  port: 8000

learning:
  stage: L2  # Start at paper trading
  autonomous: false  # Manual approval required
```

### 3. Initialize Database

```bash
# Run migrations
python -m aletheia.data.migrate

# Verify
python -m aletheia.data.migrate status
```

### 4. Start Services

#### Production (Systemd)

```bash
# Install systemd service
sudo cp scripts/aletheia.service /etc/systemd/system/
sudo systemctl daemon-reload

# Start
sudo systemctl start aletheia
sudo systemctl enable aletheia

# Check status
sudo systemctl status aletheia
```

#### Docker

```bash
# Build image
docker build -t aletheia .

# Run with environment
docker run -d \
  --name aletheia \
  -v /var/lib/aletheia:/data \
  -e BINANCE_API_KEY=... \
  -e BINANCE_API_SECRET=... \
  aletheia:latest
```

### 5. Verify Deployment

```bash
# Check system status
python -m aletheia.operator.cli --batch "show status"

# Check health
python -m aletheia.operator.cli --batch "show health"

# Check web frontend
curl http://localhost:8000/
```

## Service Management

### Systemd Service File

**Source:** `scripts/aletheia.service`

```ini
[Unit]
Description=Aletheia Trading Daemon
After=network.target

[Service]
Type=simple
User=aletheia
WorkingDirectory=/opt/aletheia
ExecStart=/usr/bin/python -m aletheia.runtime.pi
Restart=always
RestartSec=10
EnvironmentFile=/etc/aletheia/env

[Install]
WantedBy=multi-user.target
```

### Health Checks

**Source:** `aletheia/runtime/health.py`

```bash
# Check if daemon is running
systemctl is-active aletheia

# Check health endpoint
curl http://localhost:8000/api/v1/system/health

# Check component health
python -m aletheia.operator.cli --batch "show health"
```

## Operational Runbooks

### Routine Operations

#### Daily Check

```bash
# 1. Check system status
python -m aletheia.operator.cli --batch "show status"

# 2. Check health
python -m aletheia.operator.cli --batch "show health"

# 3. Check positions
python -m aletheia.operator.cli --batch "show positions"

# 4. Check learning
python -m aletheia.operator.cli --batch "show learning"
```

#### Weekly Maintenance

```bash
# 1. Run knowledge maintenance manually (also runs automatically Sunday 00:00)
python -m aletheia.operator.cli --batch "knowledge maintenance"

# 2. Check backup
python -m aletheia.operator.cli --batch "show health"

# 3. Review experiments
python -m aletheia.operator.cli --batch "show experiments"
```

### Emergency Procedures

#### System Pause

```bash
# Emergency pause
python -m aletheia.operator.cli --batch "pause system"

# Verify paused
python -m aletheia.operator.cli --batch "show status"
```

#### Component Restart

```bash
# Restart specific component
python -m aletheia.operator.cli --batch "restart data_collector"

# Restart entire daemon
sudo systemctl restart aletheia
```

#### Rollback Learning Behavior

```bash
# Check active behaviors
python -m aletheia.operator.cli --batch "show learning"

# Rollback specific behavior
python -m aletheia.operator.cli --batch "rollback behavior risk_multiplier"

# Reduce learning stage
python -m aletheia.operator.cli --batch "set learning stage L1"
```

## Monitoring

### Logs

```bash
# View daemon logs
journalctl -u aletheia -f

# View specific component logs
journalctl -u aletheia -f | grep "RiskEngine"

# Export logs
journalctl -u aletheia --since "2024-01-01" > logs.log
```

### Web Monitoring

**Source:** `web/dashboard/`

- **Dashboard** — Real-time system status, P&L, regime
- **Positions** — Active positions with exit controls
- **Health** — Component health and alerts
- **Learning** — AT-01 stage and experiment progress
- **Audit** — Hash chain verification

### Alerts

**Source:** `operator/alerting.py`

| Alert | Severity | Response Time |
|---|---|---|
| System unhealthy | CRITICAL | < 5 min |
| Real order blocked | CRITICAL | < 1 min |
| Data stale | WARNING | < 30 min |
| Budget exceeded | WARNING | < 1 hour |
| Learning rollback | INFO | < 24 hours |

## Backup and Recovery

### Backup Strategy

| Backup Type | Frequency | Retention |
|---|---|---|
| Database | Daily (02:00 UTC) | 7 days |
| Knowledge Base | Weekly (Sunday 00:00) | 30 days |
| Config | On each deploy | 90 days |
| Audit Log | Continuous | 2 years |

### Recovery Procedure

```bash
# 1. Stop daemon
sudo systemctl stop aletheia

# 2. Restore database from backup
cp backups/aletheia_20240101_020000.db /var/lib/aletheia/aletheia.db

# 3. Run migrations (if needed)
python -m aletheia.data.migrate

# 4. Start daemon
sudo systemctl start aletheia

# 5. Verify
python -m aletheia.operator.cli --batch "show status"
```

## Upgrading

### Version Upgrade

```bash
# 1. Backup current state
python -m aletheia.operator.cli --batch "pause system"

# 2. Pull new version
git pull origin main

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run migrations
python -m aletheia.data.migrate

# 5. Restart
sudo systemctl restart aletheia

# 6. Resume
python -m aletheia.operator.cli --batch "resume system"
```

### Configuration Changes

```bash
# Edit config
vim config/production.yaml

# Apply without restart (if supported)
python -m aletheia.operator.cli --batch "reload config"

# Or restart
sudo systemctl restart aletheia
```

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `Pi` | `runtime/pi.py` | Main daemon entry point |
| `SYSTEMD_SERVICE` | `scripts/aletheia.service` | Systemd service file |
| `PRODUCTION_CONFIG` | `config/production.yaml` | Production configuration |
| `HEALTH_CHECKS` | `runtime/health.py` | Health check functions |
| `BACKUP_SCHEDULE` | `data/backup.py` | Backup schedule |
| `MIGRATION_RUNNER` | `data/migrate.py` | Migration runner CLI |

## Test Coverage

- `tests/test_deployment.py` — Tests for deployment configuration
- `tests/test_migrations.py` — Tests for migration system
- `tests/test_backup.py` — Tests for backup/recovery
- `tests/test_ops_runbooks.py` — Tests for operational procedures

## Relationship to Other Components

- [Runtime Engine](../runtime/engine.md) — Daemon lifecycle
- [System Operator](../runtime/system-operator.md) — Operational commands
- [Data Layer](../data-layer/overview.md) — Database management
- [Learning System](../components/learning-system.md) — Stage management
- [Web Frontend](../frontend/overview.md) — Web monitoring
