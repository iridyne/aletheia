"""Spec: Managed Network Egress (system proxy).

Source: docs/plans/system_proxy_support_plan.md (code session 2026-07-04)

First-principles goal (owner directive 2026-07-04, Option C): EVERY outbound
network interaction — exchange trading, market data, news, LLM, webhooks — must
be routable through a single managed proxy path in our control zone, and when
``enforce`` is on the app must FAIL CLOSED (never silently fall back to a direct
connection that leaks the real IP) rather than fail open.

The choke point is ``aletheia/net.py``. This spec locks:
  - structured proxy config (protocol / host / port) that composes ``proxy_url``
    (NET-S01/S02) and surfaces in the settings route (NET-S09);
  - a single ccxt construction path ``net.make_ccxt`` that always injects the
    proxy-aware session, with NO bare ccxt exchange constructor anywhere else
    (NET-S03/S04 — the choke-point invariant);
  - enforce=True → proxy-only (no direct fallback) / enforce=False → direct
    fallback preserved (NET-S05/S06);
  - ``apply_proxy_env`` wires NO_PROXY (loopback + config) + ALL_PROXY so
    internal/loopback traffic stays direct and every env-reading client
    (aiohttp, urllib) honours the same proxy (NET-S07);
  - the connectivity monitor rides the same net.py client, so an enforce-mode
    proxy outage is detected and auto-pauses trading via the existing gate
    (NET-S08);
  - sync urllib egress (webhook, polymarket fallback) goes through net's opener,
    not a raw urlopen that ignores the managed proxy (NET-S10).

New logical contract ``network_egress_contract`` (not in REQUIRED_CONTRACTS —
cross-cutting infrastructure, mirroring gateway_stability / persistence).
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "network_egress_contract"
_PKG = Path(__file__).resolve().parents[2] / "aletheia"

# ccxt exchange ids we construct anywhere in the tree. The choke-point invariant
# (NET-S04) is that `ccxt.<id>(...)` appears ONLY inside net.py (via make_ccxt).
_CCXT_EXCHANGE_IDS = {"okx", "binance", "binanceusdm", "binancecoinm", "bitget"}


def _iter_py_files():
    for p in _PKG.rglob("*.py"):
        yield p


def _find_bare_ccxt_constructors() -> list[str]:
    """Return 'file:line' for every ccxt.<exchange>(...) call outside net.py."""
    offenders: list[str] = []
    for path in _iter_py_files():
        if path.name == "net.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            # match `ccxt.<id>(...)` where <id> is an exchange id
            if (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "ccxt"
                and fn.attr in _CCXT_EXCHANGE_IDS
            ):
                offenders.append(f"{path.relative_to(_PKG.parent)}:{node.lineno}")
    return offenders


# ---------------------------------------------------------------------------
# NET-S01 — L2a Structural: structured proxy config fields exist
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S01", _CONTRACT, "§1 structured proxy config", "L2a",
              "NetworkConfig exposes proxy_protocol (http/socks5), proxy_host, "
              "proxy_port, enforce, and no_proxy so the proxy can be defined by "
              "parts and the fail-closed policy is a first-class config field"),
)
def test_network_config_has_structured_fields():
    from aletheia.config.schema import NetworkConfig

    fields = NetworkConfig.model_fields
    for name in ("proxy_protocol", "proxy_host", "proxy_port", "enforce",
                 "no_proxy", "proxy_url"):
        assert name in fields, f"NetworkConfig missing field: {name}"

    cfg = NetworkConfig()
    assert cfg.proxy_protocol in ("http", "https", "socks5")
    assert isinstance(cfg.proxy_port, int)
    assert isinstance(cfg.enforce, bool)


# ---------------------------------------------------------------------------
# NET-S02 — L2b Behavioral: proxy_url composed from parts + legacy migration
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S02", _CONTRACT, "§1 compose + legacy migrate", "L2b",
              "proxy_url is recomposed from protocol://host:port; a legacy config "
              "carrying only proxy_url is parsed back into the structured fields; "
              "an empty host means proxy disabled (direct)"),
)
def test_proxy_url_composition_and_migration():
    from aletheia.config.schema import NetworkConfig

    # compose from parts
    cfg = NetworkConfig(proxy_protocol="socks5", proxy_host="127.0.0.1", proxy_port=7891)
    assert cfg.proxy_url == "socks5://127.0.0.1:7891"

    # legacy: only proxy_url provided → parsed into parts
    legacy = NetworkConfig(proxy_url="http://10.0.0.5:1080")
    assert legacy.proxy_host == "10.0.0.5"
    assert legacy.proxy_port == 1080
    assert legacy.proxy_protocol == "http"
    assert legacy.proxy_url == "http://10.0.0.5:1080"

    # empty host disables the proxy (direct)
    disabled = NetworkConfig(proxy_host="")
    assert disabled.proxy_url == ""


# ---------------------------------------------------------------------------
# NET-S03 — L2a Structural: net.make_ccxt injects a session
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S03", _CONTRACT, "§2 make_ccxt session injection", "L2a",
              "aletheia.net.make_ccxt exists and always injects a proxy-aware "
              "aiohttp session into the ccxt constructor (the only sanctioned way "
              "to build an exchange)"),
)
def test_make_ccxt_injects_session():
    from aletheia import net

    assert hasattr(net, "make_ccxt"), "net.make_ccxt must exist"
    src = inspect.getsource(net.make_ccxt)
    assert "aiohttp_session" in src, "make_ccxt must build a proxy-aware session"
    assert '"session"' in src or "'session'" in src or "session=session" in src, (
        "make_ccxt must inject the session into the ccxt constructor"
    )


# ---------------------------------------------------------------------------
# NET-S04 — L2a Structural: choke point — no bare ccxt constructor elsewhere
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S04", _CONTRACT, "§2 ccxt choke point", "L2a",
              "no ccxt.<exchange>(...) constructor appears outside net.make_ccxt; "
              "every exchange client is built through the managed factory so none "
              "can silently bypass the proxy session"),
)
def test_no_bare_ccxt_constructors():
    offenders = _find_bare_ccxt_constructors()
    assert not offenders, (
        "bare ccxt exchange constructors bypass the managed proxy session — "
        "route through aletheia.net.make_ccxt:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# NET-S05 — L2b Behavioral: enforce=True is fail-closed (no direct fallback)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S05", _CONTRACT, "§3 enforce fail-closed", "L2b",
              "with enforce=True and a configured proxy, _effective_proxy() returns "
              "the proxy even when the reachability probe reports it DOWN — the "
              "transport must never silently fall back to a direct connection"),
)
def test_enforce_returns_proxy_even_when_unreachable(monkeypatch):
    from aletheia import net

    monkeypatch.setattr(net, "_proxy_url", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(net, "_enforce", lambda: True)
    # Probe says unreachable — enforce must still refuse to go direct.
    monkeypatch.setattr(net, "_is_proxy_reachable", lambda url: False)

    assert net._effective_proxy() == "http://127.0.0.1:7890", (
        "enforce mode must keep the proxy (fail closed), not fall back to direct"
    )


# ---------------------------------------------------------------------------
# NET-S06 — L2b Behavioral: enforce=False preserves direct fallback
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S06", _CONTRACT, "§3 fail-open fallback", "L2b",
              "with enforce=False and an unreachable proxy, _effective_proxy() "
              "returns None so httpx falls back to a direct connection (availability "
              "over strictness) — the pre-existing behaviour is preserved"),
)
def test_no_enforce_falls_back_direct(monkeypatch):
    from aletheia import net

    monkeypatch.setattr(net, "_proxy_url", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(net, "_enforce", lambda: False)
    monkeypatch.setattr(net, "_is_proxy_reachable", lambda url: False)

    assert net._effective_proxy() is None, (
        "non-enforce mode must fall back to direct when the proxy is unreachable"
    )


# ---------------------------------------------------------------------------
# NET-S07 — L2b Behavioral: apply_proxy_env wires NO_PROXY + ALL_PROXY
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S07", _CONTRACT, "§4 NO_PROXY / ALL_PROXY wiring", "L2b",
              "apply_proxy_env sets NO_PROXY (always covering loopback 127.0.0.1/"
              "localhost/::1 plus config.network.no_proxy) and ALL_PROXY, so every "
              "env-reading client keeps internal traffic direct and external traffic "
              "on the managed proxy"),
)
def test_apply_proxy_env_sets_no_proxy_and_all_proxy(monkeypatch):
    from aletheia import net

    # Clean slate — no pre-existing env proxy.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    class _Net:
        proxy_url = "http://127.0.0.1:7890"
        no_proxy = "example.internal"

    class _Cfg:
        network = _Net()

    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: _Cfg())
    net.apply_proxy_env()

    no_proxy = os.environ.get("NO_PROXY", "")
    assert "127.0.0.1" in no_proxy and "localhost" in no_proxy, (
        f"NO_PROXY must cover loopback, got: {no_proxy!r}"
    )
    assert "example.internal" in no_proxy, "NO_PROXY must include config.network.no_proxy"
    assert os.environ.get("ALL_PROXY"), "ALL_PROXY must be set for SOCKS-aware clients"


# ---------------------------------------------------------------------------
# NET-S08 — L2a Structural: connectivity monitor rides net.py (auto-pause path)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S08", _CONTRACT, "§3 enforce ↔ connectivity gate", "L2a",
              "the exchange connectivity monitor issues its probe through "
              "aletheia.net (httpx_client), so an enforce-mode proxy outage fails "
              "the probe and the existing connectivity gate auto-pauses trading — "
              "fail-closed at the system level, not just the request level"),
)
def test_connectivity_monitor_uses_net_client():
    from aletheia.agent_runtime import connectivity

    src = inspect.getsource(connectivity)
    assert "from aletheia.net import" in src and "httpx_client" in src, (
        "connectivity monitor must probe through aletheia.net.httpx_client so the "
        "managed proxy path (and its enforce failure) governs the pause decision"
    )


# ---------------------------------------------------------------------------
# NET-S09 — L2a Structural: settings route exposes the structured proxy + enforce
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S09", _CONTRACT, "§5 settings route surface", "L2a",
              "the /settings/network update model exposes proxy_protocol, "
              "proxy_host, proxy_port and enforce (plus no_proxy) so the web panel "
              "can drive the full proxy definition and the fail-closed toggle"),
)
def test_settings_network_update_surface():
    from aletheia.server.routes.settings import NetworkConfigUpdate

    fields = NetworkConfigUpdate.model_fields
    for name in ("proxy_protocol", "proxy_host", "proxy_port", "enforce", "no_proxy"):
        assert name in fields, f"NetworkConfigUpdate missing field: {name}"


# ---------------------------------------------------------------------------
# NET-S10 — L2a Structural: sync urllib egress goes through net's opener
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S10", _CONTRACT, "§4 sync urllib choke point", "L2a",
              "external sync egress (webhook notifications, polymarket urllib "
              "fallback) builds its opener via aletheia.net so it honours the "
              "managed proxy instead of a raw urlopen that ignores it"),
)
def test_sync_urllib_uses_net_opener():
    from aletheia import notifications

    src = inspect.getsource(notifications._send_webhook)
    # Either it routes through net's opener, or it relies on env proxy set by
    # apply_proxy_env via a bare urlopen. We require the explicit, testable path.
    assert "build_urllib_opener" in src or "aletheia.net" in src, (
        "_send_webhook must build its opener through aletheia.net so the webhook "
        "POST honours the managed proxy / enforce policy"
    )


# ---------------------------------------------------------------------------
# NET-S11 — L2b Behavioral: CLI surface persists structured proxy config
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("NET-S11", _CONTRACT, "§5 CLI network surface", "L2b",
              "the `aletheia network` command group exists; `set` and `status` "
              "persist and read the structured proxy config through the config "
              "loader, keeping proxy_url recomposed from protocol/host/port"),
)
def test_network_cli_surface_persists_structured_config(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    from aletheia.main import app

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(jsonl, "_last_audit_hash", None)
    monkeypatch.setattr("aletheia.net.apply_proxy_env", lambda: None)

    runner = CliRunner()
    help_result = runner.invoke(app, ["network", "--help"])
    assert help_result.exit_code == 0
    assert "status" in help_result.output
    assert "set" in help_result.output

    set_result = runner.invoke(
        app,
        ["network", "set", "--protocol", "http", "--host", "127.0.0.1", "--port", "7890"],
    )
    assert set_result.exit_code == 0

    cfg = loader.load_config()
    assert cfg.network.proxy_protocol == "http"
    assert cfg.network.proxy_host == "127.0.0.1"
    assert cfg.network.proxy_port == 7890
    assert cfg.network.proxy_url == "http://127.0.0.1:7890"

    status_result = runner.invoke(app, ["network", "status"])
    assert status_result.exit_code == 0
    assert "Effective proxy: http://127.0.0.1:7890" in status_result.output
