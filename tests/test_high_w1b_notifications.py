"""Test notification reliability fixes for #1469, #1731, #1764, #1729, #1755."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.telegram.bot import TelegramBot
from aletheia.notifications import _send_telegram_alert, _send_webhook


class TestTelegramBotReliability:
    """Test fixes for Telegram bot issues."""

    @pytest.mark.asyncio
    async def test_send_message_raises_on_http_error(self):
        """Issue #1469: HTTP errors should raise, not silently succeed."""
        bot = TelegramBot()
        bot.token = "test_token"
        
        with patch("aletheia.telegram.bot.httpx_client") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = Exception("HTTP 500")
            mock_client.post.return_value = mock_response
            mock_client_ctx.return_value.__aenter__.return_value = mock_client
            
            with pytest.raises(Exception, match="HTTP 500"):
                await bot.send_message(123, "test")

    @pytest.mark.asyncio
    async def test_command_exception_does_not_kill_polling(self):
        """Issue #1764: exceptions in command handling should not stop the poll loop."""
        bot = TelegramBot()
        bot.token = "test_token"
        bot.allowed_chat_ids = {123}
        bot._running = True
        
        updates_count = 0
        
        async def mock_fetch_updates(_client):
            nonlocal updates_count
            updates_count += 1
            if updates_count == 1:
                return [{"update_id": 1, "message": {"chat": {"id": 123}, "text": "/status"}}]
            elif updates_count == 2:
                bot._running = False
                return []
            return []
        
        with patch.object(bot, "configure", return_value=True):
            with patch.object(bot, "_fetch_updates", side_effect=mock_fetch_updates):
                with patch.object(bot, "handle_message", side_effect=RuntimeError("command failed")):
                    with patch("aletheia.telegram.bot.httpx_client") as mock_client_ctx:
                        mock_client = AsyncMock()
                        mock_client_ctx.return_value.__aenter__.return_value = mock_client
                        
                        # Should complete without raising, logging the error instead
                        await bot.run(poll_interval=0.01)
        
        assert updates_count == 2, "Poll loop should continue after command exception"

    def test_authorization_uses_fresh_config(self):
        """Issue #1729: authorization should re-read config on every check."""
        bot = TelegramBot()
        bot.token = "test_token"
        
        with patch("aletheia.telegram.bot.load_config") as mock_load:
            mock_cfg = MagicMock()
            mock_cfg.telegram.enabled = True
            mock_cfg.telegram.allowed_chat_ids = [123]
            mock_load.return_value = mock_cfg
            
            # First check: authorized
            assert bot._is_authorized(123) is True
            
            # Config changes: revoke chat_id
            mock_cfg.telegram.allowed_chat_ids = []
            
            # Second check: should now be unauthorized (fresh config)
            assert bot._is_authorized(123) is False


class TestNotificationNonBlocking:
    """Test fixes for notification blocking issues."""
    def test_telegram_alert_does_not_block_async_caller(self):
        """Issue #1731: advisory notifications should not block the gateway."""
        with patch("aletheia.notifications._is_duplicate", return_value=False):
            with patch("aletheia.config.loader.load_config") as mock_cfg:
                mock_cfg.return_value.telegram.enabled = True
                mock_cfg.return_value.health.alert_via_telegram = True
                
                with patch("aletheia.telegram.bot.TelegramBot") as mock_bot_cls:
                    mock_bot = MagicMock()
                    mock_bot.configure.return_value = True
                    mock_bot.allowed_chat_ids = {123}
                    mock_bot_cls.return_value = mock_bot
                    
                    with patch("aletheia.notifications.asyncio.get_running_loop") as mock_loop:
                        mock_loop.return_value = asyncio.new_event_loop()
                        submitted = []
                        def _capture_and_close(coro):
                            submitted.append(coro)
                            coro.close()  # prevent unawaited-coroutine warning
                        with patch("aletheia.notifications.asyncio.create_task", side_effect=_capture_and_close):
                            # Should call create_task, not block
                            _send_telegram_alert("test message", level="warning")
                        assert len(submitted) == 1, "create_task should be called once (fire-and-forget)"

    def test_webhook_failure_logs_at_warning_level(self, caplog):
        """Issue #1755: webhook delivery failures should log at WARNING level."""
        with patch("aletheia.config.loader.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.webhook.enabled = True
            cfg.webhook.url = "http://test.example.com/webhook"
            cfg.webhook.notify_alerts = True
            cfg.webhook.secret = None  # no signature
            cfg.webhook.timeout_sec = 5.0
            mock_cfg.return_value = cfg
            
            with patch("aletheia.net.build_urllib_opener") as mock_opener:
                mock_opener.return_value.open.side_effect = Exception("Network error")
                
                with caplog.at_level(logging.WARNING):
                    _send_webhook({"title": "test", "message": "test"}, event_type="alert")
                
                # Should log at WARNING level, not DEBUG
                assert any("Webhook notification failed" in record.message 
                          and record.levelname == "WARNING" 
                          for record in caplog.records), \
                    "Webhook failure should log at WARNING level"


def test_polling_loop_has_exception_handler():
    """Source check: polling loop wraps _handle_update with try/except."""
    import inspect
    source = inspect.getsource(TelegramBot.run)
    # Should have try/except around _handle_update call
    assert "try:" in source and "_handle_update" in source, \
        "Polling loop should wrap _handle_update with exception handling"


def test_send_message_checks_http_status():
    """Source check: send_message calls raise_for_status."""
    import inspect
    source = inspect.getsource(TelegramBot.send_message)
    assert "raise_for_status" in source, \
        "send_message should check HTTP response status"


def test_webhook_failure_warns():
    """Source check: _send_webhook logs failures at WARNING level."""
    import inspect
    source = inspect.getsource(_send_webhook)
    # Should contain logger.warning in the exception handler
    lines = source.split("\n")
    in_exception_handler = False
    for line in lines:
        if "except Exception" in line:
            in_exception_handler = True
        if in_exception_handler and "logger.warning" in line and "Webhook" in line:
            return
    raise AssertionError("_send_webhook should log failures at WARNING level")
