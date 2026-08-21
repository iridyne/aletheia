"""Regression tests for Issue #1234: file-loaded config must share API route bounds.

Validates that configuration models reject invalid boundary values at YAML load time,
not just API mutation time.
"""

import pytest  # pyright: ignore[reportMissingImports]
from pydantic import ValidationError

from aletheia.config.schema import HistoricalDataConfig, NewsConfig, PiDaemonConfig, HealthConfig


# --- HistoricalDataConfig ---


def test_historical_data_nightly_backfill_hour_cst_bounds():
    """nightly_backfill_hour_cst must be 0-23 (valid cron hour)."""
    HistoricalDataConfig(nightly_backfill_hour_cst=0)
    HistoricalDataConfig(nightly_backfill_hour_cst=23)
    with pytest.raises(ValidationError, match="nightly_backfill_hour_cst"):
        HistoricalDataConfig(nightly_backfill_hour_cst=99)
    with pytest.raises(ValidationError, match="nightly_backfill_hour_cst"):
        HistoricalDataConfig(nightly_backfill_hour_cst=-1)


def test_historical_data_max_bars_per_pair_bounds():
    """max_bars_per_pair must be 1_000-200_000."""
    HistoricalDataConfig(max_bars_per_pair=1_000)
    HistoricalDataConfig(max_bars_per_pair=200_000)
    with pytest.raises(ValidationError, match="max_bars_per_pair"):
        HistoricalDataConfig(max_bars_per_pair=999)
    with pytest.raises(ValidationError, match="max_bars_per_pair"):
        HistoricalDataConfig(max_bars_per_pair=200_001)


def test_historical_data_min_bars_for_backtest_bounds():
    """min_bars_for_backtest must be 30-5_000."""
    HistoricalDataConfig(min_bars_for_backtest=30)
    HistoricalDataConfig(min_bars_for_backtest=5_000)
    with pytest.raises(ValidationError, match="min_bars_for_backtest"):
        HistoricalDataConfig(min_bars_for_backtest=29)
    with pytest.raises(ValidationError, match="min_bars_for_backtest"):
        HistoricalDataConfig(min_bars_for_backtest=5_001)


# --- NewsConfig ---


def test_news_fetch_interval_minutes_bounds():
    """fetch_interval_minutes must be 5-120."""
    NewsConfig(fetch_interval_minutes=5)
    NewsConfig(fetch_interval_minutes=120)
    with pytest.raises(ValidationError, match="fetch_interval_minutes"):
        NewsConfig(fetch_interval_minutes=4)
    with pytest.raises(ValidationError, match="fetch_interval_minutes"):
        NewsConfig(fetch_interval_minutes=121)


def test_news_retention_days_bounds():
    """retention_days must be 1-90; 0 would delete all older news."""
    NewsConfig(retention_days=1)
    NewsConfig(retention_days=90)
    with pytest.raises(ValidationError, match="retention_days"):
        NewsConfig(retention_days=0)
    with pytest.raises(ValidationError, match="retention_days"):
        NewsConfig(retention_days=91)


# --- PiDaemonConfig ---


def test_pi_daemon_heartbeat_sec_bounds():
    """heartbeat_sec must be 60-3600; negative/small values create busy loops."""
    PiDaemonConfig(heartbeat_sec=60.0)
    PiDaemonConfig(heartbeat_sec=3600.0)
    with pytest.raises(ValidationError, match="heartbeat_sec"):
        PiDaemonConfig(heartbeat_sec=-1.0)
    with pytest.raises(ValidationError, match="heartbeat_sec"):
        PiDaemonConfig(heartbeat_sec=59.9)
    with pytest.raises(ValidationError, match="heartbeat_sec"):
        PiDaemonConfig(heartbeat_sec=3600.1)


def test_pi_daemon_debounce_sec_bounds():
    """debounce_sec must be 0-60."""
    PiDaemonConfig(debounce_sec=0.0)
    PiDaemonConfig(debounce_sec=60.0)
    with pytest.raises(ValidationError, match="debounce_sec"):
        PiDaemonConfig(debounce_sec=-0.1)
    with pytest.raises(ValidationError, match="debounce_sec"):
        PiDaemonConfig(debounce_sec=60.1)


def test_pi_daemon_cooldown_sec_bounds():
    """cooldown_sec must be 0-600."""
    PiDaemonConfig(cooldown_sec=0.0)
    PiDaemonConfig(cooldown_sec=600.0)
    with pytest.raises(ValidationError, match="cooldown_sec"):
        PiDaemonConfig(cooldown_sec=-0.1)
    with pytest.raises(ValidationError, match="cooldown_sec"):
        PiDaemonConfig(cooldown_sec=600.1)


# --- HealthConfig ---


def test_health_exchange_probe_interval_sec_bounds():
    """exchange_probe_interval_sec must be 10-300; 0 can break the monitor."""
    HealthConfig(exchange_probe_interval_sec=10)
    HealthConfig(exchange_probe_interval_sec=300)
    with pytest.raises(ValidationError, match="exchange_probe_interval_sec"):
        HealthConfig(exchange_probe_interval_sec=0)
    with pytest.raises(ValidationError, match="exchange_probe_interval_sec"):
        HealthConfig(exchange_probe_interval_sec=9)
    with pytest.raises(ValidationError, match="exchange_probe_interval_sec"):
        HealthConfig(exchange_probe_interval_sec=301)


def test_health_auto_resume_stall_backoff_factor_bounds():
    """backoff_factor >= 1.0 ensures each stall escalation never shrinks the interval."""
    HealthConfig(auto_resume_stall_backoff_factor=1.0)   # no-backoff edge
    HealthConfig(auto_resume_stall_backoff_factor=2.0)   # typical doubling
    HealthConfig(auto_resume_stall_backoff_factor=10.0)  # upper bound
    with pytest.raises(ValidationError, match="auto_resume_stall_backoff_factor"):
        HealthConfig(auto_resume_stall_backoff_factor=0.999)  # fractional — shrinks interval
    with pytest.raises(ValidationError, match="auto_resume_stall_backoff_factor"):
        HealthConfig(auto_resume_stall_backoff_factor=0.0)
    with pytest.raises(ValidationError, match="auto_resume_stall_backoff_factor"):
        HealthConfig(auto_resume_stall_backoff_factor=-1.0)
    with pytest.raises(ValidationError, match="auto_resume_stall_backoff_factor"):
        HealthConfig(auto_resume_stall_backoff_factor=10.1)


def test_health_auto_resume_max_interval_sec_bounds():
    """auto_resume_max_interval_sec must be >= 1 to prevent zero/negative sleep."""
    HealthConfig(auto_resume_max_interval_sec=1)
    HealthConfig(auto_resume_max_interval_sec=86400)
    with pytest.raises(ValidationError, match="auto_resume_max_interval_sec"):
        HealthConfig(auto_resume_max_interval_sec=0)
    with pytest.raises(ValidationError, match="auto_resume_max_interval_sec"):
        HealthConfig(auto_resume_max_interval_sec=-1)
    with pytest.raises(ValidationError, match="auto_resume_max_interval_sec"):
        HealthConfig(auto_resume_max_interval_sec=86401)


# --- Runtime assignment validation ---


def test_historical_data_assignment_validates():
    """Runtime setattr must also enforce bounds (validate_assignment=True)."""
    cfg = HistoricalDataConfig()
    with pytest.raises(ValidationError, match="nightly_backfill_hour_cst"):
        cfg.nightly_backfill_hour_cst = 99


def test_news_assignment_validates():
    """Runtime setattr must also enforce bounds (validate_assignment=True)."""
    cfg = NewsConfig()
    with pytest.raises(ValidationError, match="retention_days"):
        cfg.retention_days = 0


def test_pi_daemon_assignment_validates():
    """Runtime setattr must also enforce bounds (validate_assignment=True)."""
    cfg = PiDaemonConfig()
    with pytest.raises(ValidationError, match="heartbeat_sec"):
        cfg.heartbeat_sec = -1.0


def test_health_assignment_validates():
    """Runtime setattr must also enforce bounds (validate_assignment=True)."""
    cfg = HealthConfig()
    with pytest.raises(ValidationError, match="exchange_probe_interval_sec"):
        cfg.exchange_probe_interval_sec = 0
