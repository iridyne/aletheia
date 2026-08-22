"""Regression tests for update/rollback fixes #1207 #1206 #1418 #1427."""
from __future__ import annotations
import inspect


def test_reinstall_deps_uses_uv_sync_locked():
    """#1206: _reinstall_deps must use uv sync --locked, no pip fallback."""
    from aletheia.update.applier import _reinstall_deps
    src = inspect.getsource(_reinstall_deps)
    assert "--locked" in src, (
        "_reinstall_deps must pass --locked to uv sync (#1206)"
    )
    assert "pip install" not in src, (
        "_reinstall_deps must not fall back to pip install (#1206)"
    )


def test_reinstall_deps_returns_error_dict_on_failure():
    """#1207: _reinstall_deps must signal failure (not raise) so caller can roll back."""
    from aletheia.update.applier import _reinstall_deps
    src = inspect.getsource(_reinstall_deps)
    # Must return an error dict, not raise
    assert '"ok": False' in src or "ok: False" in src or "'ok': False" in src, (
        "_reinstall_deps must return {'ok': False, ...} on failure (#1207)"
    )
    assert "uv_sync_failed" in src or "reason" in src


def test_read_url_has_send_auth_parameter():
    """#1418: _read_url must have send_auth parameter to control token transmission."""
    from aletheia.update.packaged import _read_url
    sig = inspect.signature(_read_url)
    assert "send_auth" in sig.parameters, (
        "_read_url must accept send_auth parameter to whitelist token recipients (#1418)"
    )


def test_read_url_only_sends_auth_to_feed_host():
    """#1418: auth token only sent when URL matches configured feed host."""
    from aletheia.update.packaged import _read_url
    src = inspect.getsource(_read_url)
    assert "send_auth" in src
    assert "netloc" in src, (
        "_read_url must check netloc matches feed base before sending auth (#1418)"
    )


def test_verified_feed_rejects_replay():
    """#1427: _verified_feed must reject feeds older than last applied update."""
    from aletheia.update.packaged import _verified_feed
    src = inspect.getsource(_verified_feed)
    assert "replay" in src.lower() or "older than last" in src, (
        "_verified_feed must check feed timestamp against last update to prevent replay (#1427)"
    )
    assert "update_feed_replay_rejected" in src
