"""Regression coverage for notification settings trust-boundary validation."""

import asyncio
import logging
from math import inf, nan

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aletheia.config.schema import AletheiaConfig, TelegramConfig, WebhookConfig
from aletheia.server.routes import settings


@pytest.mark.parametrize("value", [-1, 24])
def test_telegram_hours_reject_values_outside_a_day(value):
    for field in ("daily_summary_hour_cst", "dnd_start_hour", "dnd_end_hour"):
        with pytest.raises(ValidationError):
            TelegramConfig(**{field: value})


def test_telegram_hour_boundaries_are_accepted():
    cfg = TelegramConfig(
        daily_summary_hour_cst=0,
        dnd_start_hour=23,
        dnd_end_hour=0,
    )
    assert cfg.daily_summary_hour_cst == 0
    assert cfg.dnd_start_hour == 23
    assert cfg.dnd_end_hour == 0


@pytest.mark.parametrize("value", [0, -1, 60.1, nan, inf, -inf])
def test_webhook_timeout_rejects_non_finite_or_out_of_range_values(value):
    with pytest.raises(ValidationError):
        WebhookConfig(timeout_sec=value)


def test_webhook_timeout_boundaries_are_accepted():
    assert WebhookConfig(timeout_sec=1).timeout_sec == 1
    assert WebhookConfig(timeout_sec=60).timeout_sec == 60


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (settings.TelegramConfigUpdate, "daily_summary_hour_cst", 24),
        (settings.TelegramConfigUpdate, "dnd_start_hour", -1),
        (settings.TelegramConfigUpdate, "dnd_end_hour", 24),
        (settings.WebhookConfigUpdate, "timeout_sec", nan),
        (settings.WebhookConfigUpdate, "timeout_sec", 61),
    ],
)
def test_settings_requests_share_canonical_notification_constraints(model, field, value):
    with pytest.raises(ValidationError):
        model(**{field: value})


def test_invalid_telegram_candidate_has_no_persistence_audit_or_reconcile(monkeypatch):
    cfg = AletheiaConfig()
    saved = []
    audited = []
    reconciled = []

    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda value: saved.append(value))
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audit",
        lambda event: audited.append(event),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.reschedule_telegram_summary",
        lambda hour: reconciled.append(hour) or True,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            settings.update_telegram_config(
                settings.TelegramConfigUpdate.model_construct(
                    daily_summary_hour_cst=24,
                ),
            ),
        )

    assert exc_info.value.status_code == 422
    assert saved == []
    assert audited == []
    assert reconciled == []
    assert cfg.telegram.daily_summary_hour_cst == 8


def test_telegram_reschedule_failure_restores_previous_config(monkeypatch):
    cfg = AletheiaConfig()
    saved_hours = []
    audited = []

    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(
        settings,
        "save_config",
        lambda value: saved_hours.append(value.telegram.daily_summary_hour_cst),
    )
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audit",
        lambda event: audited.append(event),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.reschedule_telegram_summary",
        lambda _hour: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            settings.update_telegram_config(
                settings.TelegramConfigUpdate(daily_summary_hour_cst=20),
            ),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "telegram_summary_reschedule_failed"
    assert saved_hours == [20, 8]
    assert cfg.telegram.daily_summary_hour_cst == 8
    assert audited == []


def test_scheduler_startup_logs_configuration_blocker_for_invalid_legacy_config(
    monkeypatch, caplog
):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_universe_scanner_startup_cutoff", "test")
    monkeypatch.setattr(scheduler, "_universe_scanner_startup_recovery_done", True)
    monkeypatch.setattr(scheduler, "_universe_scanner_audit_mirror_done", True)
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: None)

    def invalid_config():
        return AletheiaConfig.model_validate(
            {"telegram": {"daily_summary_hour_cst": 24}},
        )

    monkeypatch.setattr(scheduler, "load_config", invalid_config)
    caplog.set_level(logging.ERROR, logger="aletheia.scheduler")

    with pytest.raises(ValidationError):
        scheduler.init_scheduler()

    assert scheduler._scheduler is None
    assert "configuration blocker" in caplog.text
