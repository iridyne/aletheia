"""Contract for `uta.manager.probe_account_reachable`.

This function replaces the per-venue hand-rolled `test_connection()` methods. It
exists because those probes signed their own request with their own auth and parsed
their own response code, so a probe could report success against an account the rest
of the system would reject.

Two of its branches are here because an earlier version got them wrong, and neither
error was visible without a test:

* `credentials_not_configured` never fired. It was classified by string-matching the
  exception message, but `build_uta_registry` silently OMITS a mode whose secrets are
  absent, so `create_uta_adapter` raises
  `KeyError("UTA account is not registered: …")` — a message containing neither
  "credential" nor "not configured". The one failure a credential check exists to
  report was the one it could not report.
* `expected_account` was not passed, so `query_uta` skipped BOTH identity
  comparisons and the probe confirmed reachability of whatever account the current
  credentials happened to open.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import aletheia.uta.manager as manager


def _registry(monkeypatch, identities):
    monkeypatch.setattr(
        manager,
        "build_uta_registry",
        lambda *a, **k: SimpleNamespace(configured_identities=lambda: identities),
    )


def _identity(execution_mode: str):
    return SimpleNamespace(execution_mode=execution_mode)


def test_missing_credentials_are_reported_as_such(monkeypatch):
    """Detected by asking the registry, not by reading an exception message."""
    _registry(monkeypatch, ())

    def must_not_run(*a, **k):
        raise AssertionError("no venue query may be attempted without credentials")

    monkeypatch.setattr(manager, "query_uta", must_not_run)

    assert manager.probe_account_reachable("bitget_demo") == {
        "connected": False,
        "execution_mode": "bitget_demo",
        "error": "credentials_not_configured",
    }


def test_the_probe_binds_the_expected_account(monkeypatch):
    """Without `expected_account`, `query_uta` skips both identity comparisons."""
    identity = _identity("bitget_demo")
    _registry(monkeypatch, (identity,))
    seen: dict = {}

    async def query(mode, method, query_arg=None, *, cfg=None, expected_account=None):
        seen.update(mode=mode, method=method, expected=expected_account)
        return SimpleNamespace(
            completeness=manager.ReadCompleteness.COMPLETE, reason_code=None
        )

    monkeypatch.setattr(manager, "query_uta", query)

    assert manager.probe_account_reachable("bitget_demo") == {
        "connected": True,
        "execution_mode": "bitget_demo",
    }
    assert seen["method"] == "get_account"
    assert seen["expected"] is identity, (
        "expected_account was not bound — the probe would accept any account the "
        "current credentials open"
    )


def test_the_probe_resolves_the_requested_mode_not_the_first_configured(monkeypatch):
    demo, live = _identity("bitget_demo"), _identity("bitget_live_limited")
    _registry(monkeypatch, (demo, live))
    seen: dict = {}

    async def query(mode, method, query_arg=None, *, cfg=None, expected_account=None):
        seen["expected"] = expected_account
        return SimpleNamespace(
            completeness=manager.ReadCompleteness.COMPLETE, reason_code=None
        )

    monkeypatch.setattr(manager, "query_uta", query)
    manager.probe_account_reachable("bitget_live_limited")
    assert seen["expected"] is live


def test_an_incomplete_read_is_not_a_successful_probe(monkeypatch):
    _registry(monkeypatch, (_identity("okx_demo"),))

    async def partial(mode, method, query_arg=None, *, cfg=None, expected_account=None):
        return SimpleNamespace(
            completeness=manager.ReadCompleteness.PARTIAL,
            reason_code="account_read_partial",
        )

    monkeypatch.setattr(manager, "query_uta", partial)
    result = manager.probe_account_reachable("okx_demo")
    assert result["connected"] is False
    assert result["error"] == "account_read_partial"


def test_identity_mismatch_is_classified(monkeypatch):
    _registry(monkeypatch, (_identity("bitget_demo"),))

    async def mismatch(mode, method, query_arg=None, *, cfg=None, expected_account=None):
        raise ValueError("uta_account_identity_mismatch")

    monkeypatch.setattr(manager, "query_uta", mismatch)
    assert manager.probe_account_reachable("bitget_demo")["error"] == (
        "account_identity_mismatch"
    )


@pytest.mark.parametrize(
    "raised",
    [
        RuntimeError("signature=SECRETKEY123 apiKey=LEAKED_KEY_MATERIAL"),
        ValueError("POST /api/v2/account?apiKey=LEAKED_KEY_MATERIAL failed"),
    ],
    ids=["message-with-secret", "message-with-signed-url"],
)
def test_the_probe_never_echoes_the_exception_text(monkeypatch, raised):
    """A CCXT/venue error can carry the signed request, and so the API key.

    Only the exception CLASS name may reach the caller. This is a credential
    surface: `cli/secrets_cmd.py` prints the result to a terminal and
    `routes/bitget.py`-style endpoints return it over HTTP.
    """
    _registry(monkeypatch, (_identity("bitget_demo"),))

    async def leaky(mode, method, query_arg=None, *, cfg=None, expected_account=None):
        raise raised

    monkeypatch.setattr(manager, "query_uta", leaky)
    result = manager.probe_account_reachable("bitget_demo")

    assert result["error"] == type(raised).__name__
    rendered = str(result)
    assert "LEAKED_KEY_MATERIAL" not in rendered
    assert "SECRETKEY123" not in rendered
