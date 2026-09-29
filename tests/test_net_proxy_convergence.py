"""Phase 0 / #1639: httpx and CCXT resolve the same proxy policy.

Regression proof for the three scenarios from #1639 review:
  A. shell proxy A + config proxy B → both stacks use B (config authority)
  B. proxy rotation/disable → newly constructed sessions do not reuse stale A
  C. no proxy configured → aiohttp/CCXT go direct even when shell has a proxy
"""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import aiohttp
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

_fake_connector = MagicMock(spec=aiohttp.TCPConnector)
_fake_connector.close = MagicMock()


def _mock_session(monkeypatch):
    """Patch ClientSession, TCPConnector and ThreadedResolver so no event loop is needed."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.connector = _fake_connector
    monkeypatch.setattr("aletheia.net.ThreadedResolver", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("aiohttp.TCPConnector", MagicMock(return_value=_fake_connector))
    monkeypatch.setattr("aiohttp.ClientSession", MagicMock(return_value=mock_session))
    return mock_session


@pytest.fixture()
def clean_proxy_env(monkeypatch):
    """Remove all proxy env vars so each test starts from a known state."""
    for var in (*_PROXY_VARS, "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    yield


# ── Scenario A: config proxy overrides shell proxy ────────────────────────────


def test_aiohttp_session_uses_config_proxy_not_shell_proxy(clean_proxy_env, monkeypatch):
    """When shell has proxy A and config has proxy B, aiohttp_session() writes B.

    Previously apply_proxy_env() preserved A (shell wins). Now _effective_proxy()
    is authoritative so config B is written and trust_env=True picks it up.
    """
    from aletheia import net

    _mock_session(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://shell-proxy.invalid:9999")
    monkeypatch.setattr(net, "_effective_proxy", lambda: "http://config-proxy.invalid:7890")
    monkeypatch.setattr(net, "_no_proxy_value", lambda: "127.0.0.1,localhost")

    net.aiohttp_session()

    assert os.environ.get("HTTPS_PROXY") == "http://config-proxy.invalid:7890"
    assert os.environ.get("HTTP_PROXY") == "http://config-proxy.invalid:7890"


def test_aiohttp_session_writes_config_proxy_env_for_trust_env(clean_proxy_env, monkeypatch):
    """aiohttp_session writes the canonical proxy URL to env for trust_env=True to pick up.

    This is the env-convergence side of #1639: after aiohttp_session() constructs,
    HTTPS_PROXY in the process env reflects the config-derived proxy (not shell),
    so trust_env=True resolves the same proxy as httpx_client() does explicitly.
    """
    from aletheia import net

    config_proxy = "http://unified-proxy.invalid:7890"
    _mock_session(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://stale-shell-proxy.invalid:9999")
    monkeypatch.setattr(net, "_effective_proxy", lambda: config_proxy)
    monkeypatch.setattr(net, "_no_proxy_value", lambda: "127.0.0.1,localhost")

    net.aiohttp_session()

    assert os.environ.get("HTTPS_PROXY") == config_proxy, (
        "aiohttp_session must write config proxy to HTTPS_PROXY for trust_env=True; "
        "previously shell proxy was preserved, causing split egress"
    )
    assert os.environ.get("HTTP_PROXY") == config_proxy
    assert os.environ.get("ALL_PROXY") == config_proxy


# ── Scenario B: proxy rotation/disable ───────────────────────────────────────


def test_disabled_proxy_clears_stale_env_proxy(clean_proxy_env, monkeypatch):
    """After proxy is disabled (effective_proxy=None), aiohttp_session clears env vars."""
    from aletheia import net

    _mock_session(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://old-proxy.invalid:7890")
    monkeypatch.setenv("HTTP_PROXY", "http://old-proxy.invalid:7890")
    monkeypatch.setattr(net, "_effective_proxy", lambda: None)
    monkeypatch.setattr(net, "_no_proxy_value", lambda: "127.0.0.1,localhost")

    net.aiohttp_session()

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert var not in os.environ, (
            f"aiohttp_session must clear {var} when effective proxy is None"
        )


# ── Scenario C: no proxy configured ──────────────────────────────────────────


def test_no_proxy_configured_clears_shell_proxy(clean_proxy_env, monkeypatch):
    """When no proxy is configured, aiohttp_session() clears any shell-set proxy."""
    from aletheia import net

    _mock_session(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://system-proxy.invalid:3128")
    monkeypatch.setattr(net, "_effective_proxy", lambda: None)
    monkeypatch.setattr(net, "_no_proxy_value", lambda: "127.0.0.1,localhost")

    net.aiohttp_session()

    for var in _PROXY_VARS:
        assert var not in os.environ, (
            f"aiohttp_session must clear {var} for direct connections"
        )


# ── NO_PROXY refresh ──────────────────────────────────────────────────────────


def test_aiohttp_session_always_refreshes_no_proxy(clean_proxy_env, monkeypatch):
    """aiohttp_session always writes NO_PROXY even when proxy=None (loopback stays direct)."""
    from aletheia import net

    _mock_session(monkeypatch)
    monkeypatch.setattr(net, "_effective_proxy", lambda: None)
    monkeypatch.setattr(net, "_no_proxy_value", lambda: "127.0.0.1,localhost,::1")

    net.aiohttp_session()

    assert "127.0.0.1" in os.environ.get("NO_PROXY", ""), "loopback must always be in NO_PROXY"
    assert "127.0.0.1" in os.environ.get("no_proxy", "")


# ── SOCKS path unchanged ──────────────────────────────────────────────────────


def test_aiohttp_session_socks_uses_proxy_connector(clean_proxy_env, monkeypatch):
    """SOCKS proxy path builds a ProxyConnector, not env-trust-env (unchanged behavior)."""
    from aletheia import net

    socks_url = "socks5://127.0.0.1:1080"
    monkeypatch.setattr(net, "_effective_proxy", lambda: socks_url)

    mock_connector = MagicMock()
    mock_connector_cls = MagicMock(return_value=mock_connector)

    with patch.dict("sys.modules", {"aiohttp_socks": MagicMock(ProxyConnector=mock_connector_cls)}):
        with patch("aiohttp.ClientSession.__init__", return_value=None):
            try:
                net.aiohttp_session()
            except Exception:
                pass

    mock_connector_cls.from_url.assert_called_once_with(socks_url)
