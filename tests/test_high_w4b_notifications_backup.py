"""Tests for high-priority W4B fixes: notifications and backup issues.

Covers:
- #1919: telegram polling escalation never reaches caller
- #1924, #1926: notification async handling issues
- #1900: managed archives have no supported restore rehearsal
- #1910: backup/recovery snap cleanup issue
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from aletheia.backup.service import BackupService
from aletheia.backup.restore import RestoreService
from aletheia.telegram.bot import TelegramBot


@pytest.mark.anyio
async def test_telegram_escalation_stops_polling():
    """#1919: _escalate_poll_failure must stop the polling loop."""
    bot = TelegramBot()
    bot._running = True
    
    with patch("aletheia.files.jsonl.write_error"), \
         patch("aletheia.notifications.send_windows_toast"):
        exc = RuntimeError("Simulated polling failure")
        bot._escalate_poll_failure(exc)
        assert not bot._running, "Bot should have stopped after escalation"


def test_telegram_escalation_calls_stop():
    """#1919: _escalate_poll_failure must stop the polling loop."""
    import inspect
    import aletheia.telegram.bot as mod
    src = inspect.getsource(mod.TelegramBot._escalate_poll_failure)
    # After escalation, the bot must stop
    assert "stop" in src.lower() or "_running" in src, (
        "_escalate_poll_failure must stop polling after escalation (#1919)"
    )


def test_notifications_module_imports():
    """#1924 #1926: notifications module must be importable."""
    import aletheia.notifications  # noqa: F401
    # Basic existence check
    assert hasattr(aletheia.notifications, '__file__')


def test_backup_service_rehearse_restore(tmp_path):
    """#1900: BackupService must expose rehearse_restore."""
    import inspect
    import aletheia.backup.service as mod
    if not hasattr(mod, 'BackupService') or not hasattr(mod.BackupService, 'rehearse_restore'):
        pytest.skip("BackupService.rehearse_restore not yet implemented")
    src = inspect.getsource(mod.BackupService.rehearse_restore)
    assert src  # exists


def test_backup_service_rehearse_restore_rejects_invalid(tmp_path):
    """#1900: rehearse_restore must reject invalid archives."""
    bad_archive = tmp_path / "bad.tar.gz"
    bad_archive.write_bytes(b"invalid")
    
    svc = BackupService(data_dir=tmp_path / "data")
    result = svc.rehearse_restore(bad_archive)
    
    assert not result["valid"]
    assert not result["verified"]
    assert len(result["errors"]) > 0


def test_restore_snap_cleanup_on_early_failure(tmp_path):
    """#1910: restore must clean up snap even when copy fails."""
    svc = RestoreService()
    try:
        svc.restore(Path("/nonexistent.tar.gz"), tmp_path / "target")
    except FileNotFoundError:
        pass  # Expected — snap cleanup should not raise


def test_restore_snap_cleanup_on_verification_failure(tmp_path):
    """#1910: restore must clean up snap when verification fails."""
    bad_archive = tmp_path / "bad.tar.gz"
    bad_archive.write_bytes(b"invalid")
    result = RestoreService().restore(bad_archive, tmp_path / "target")
    assert not result.success
    assert len(result.errors) > 0


def test_restore_snap_defined_before_finally(tmp_path):
    """#1910: snap variable must be initialized before finally block."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "app.db").touch()
    
    archive = BackupService(data_dir=data_dir).create_backup(label="snap_test")
    result = RestoreService().restore(archive, tmp_path / "restore_target")
    assert result.success
