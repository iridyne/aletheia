"""System proxy / network egress CLI commands."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import typer

network_app = typer.Typer(no_args_is_help=True)

_Protocol = Literal["http", "https", "socks5"]
_DISABLED = "disabled (direct connection)"


def _safe_host(host: str) -> str:
    """Avoid printing accidental userinfo if a host was entered as user:pass@host."""
    return (host or "").rsplit("@", 1)[-1]


def _safe_proxy_url(url: str) -> str:
    if not url:
        return _DISABLED
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid proxy URL>"
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _network_snapshot(network) -> dict:
    data = network.model_dump(mode="json")
    data["proxy_host"] = _safe_host(str(data.get("proxy_host") or ""))
    data["proxy_url"] = _safe_proxy_url(str(data.get("proxy_url") or ""))
    return data


def _configured(network) -> bool:
    return bool((network.proxy_host or "").strip())


def _effective_proxy(network) -> str:
    return _safe_proxy_url(network.proxy_url)


def _ascii_detail(text: str) -> str:
    return text.replace("→", "->").replace("—", "-").replace("≠", "!=")


def _write_network_audit(before: dict, after: dict, message: str) -> None:
    from aletheia.files.jsonl import write_audit

    write_audit(
        {
            "actor": "human",
            "source": "cli",
            "event_type": "network_config_changed",
            "entity_type": "network_config",
            "before": before,
            "after": after,
            "message": message,
        }
    )


@network_app.command()
def status() -> None:
    """Show current proxy configuration."""
    from aletheia.config.loader import load_config

    net = load_config().network
    typer.echo("Network proxy configuration")
    typer.echo(f"  Protocol: {net.proxy_protocol}")
    typer.echo(f"  Host: {_safe_host(net.proxy_host)}")
    typer.echo(f"  Port: {net.proxy_port}")
    typer.echo(f"  Enforce (fail-closed): {bool(net.enforce)}")
    typer.echo(f"  No proxy: {net.no_proxy}")
    typer.echo(f"  Effective proxy: {_effective_proxy(net)}")
    typer.echo(f"  Configured: {'yes' if _configured(net) else 'no'}")


@network_app.command(name="set")
def set_cmd(
    protocol: _Protocol | None = typer.Option(
        None,
        "--protocol",
        help="Proxy protocol: http, https, or socks5.",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Proxy host. Empty string disables the proxy.",
    ),
    port: int | None = typer.Option(None, "--port", help="Proxy port."),
    enforce: bool | None = typer.Option(
        None,
        "--enforce/--no-enforce",
        help="Fail closed when the proxy is unavailable.",
    ),
    no_proxy: str | None = typer.Option(
        None,
        "--no-proxy",
        help="Comma-separated proxy bypass list.",
    ),
) -> None:
    """Update proxy configuration."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.net import apply_proxy_env

    if port is not None and not 1 <= port <= 65535:
        typer.echo("--port must be between 1 and 65535.", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    before = _network_snapshot(cfg.network)
    changed: list[str] = []

    if protocol is not None:
        cfg.network.proxy_protocol = protocol
        changed.append("proxy_protocol")
    if host is not None:
        cfg.network.proxy_host = host
        changed.append("proxy_host")
    if port is not None:
        cfg.network.proxy_port = port
        changed.append("proxy_port")
    if enforce is not None:
        cfg.network.enforce = enforce
        changed.append("enforce")
    if no_proxy is not None:
        cfg.network.no_proxy = no_proxy
        changed.append("no_proxy")

    cfg.network.recompose_proxy_url()
    save_config(cfg)
    apply_proxy_env()
    _write_network_audit(
        before,
        _network_snapshot(cfg.network),
        f"Network config updated via CLI: {changed or ['no changes']}",
    )

    typer.echo("Network proxy configuration updated.")
    typer.echo(f"  Effective proxy: {_effective_proxy(cfg.network)}")
    typer.echo(f"  Configured: {'yes' if _configured(cfg.network) else 'no'}")
    if cfg.network.proxy_protocol == "socks5":
        typer.echo("  Note: SOCKS5 support requires aiohttp-socks to be installed.")


@network_app.command()
def disable() -> None:
    """Disable proxy routing and use direct connections."""
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    before = _network_snapshot(cfg.network)
    cfg.network.proxy_host = ""
    cfg.network.recompose_proxy_url()
    save_config(cfg)
    _write_network_audit(
        before,
        _network_snapshot(cfg.network),
        "Network proxy disabled via CLI",
    )
    typer.echo("Network proxy disabled. Outbound traffic will use direct connections.")


@network_app.command(name="test")
def test_cmd() -> None:
    """Run proxy connectivity diagnostics."""
    from aletheia.config.loader import load_config
    from aletheia.diagnostics.doctor import Report, _probe_network

    net = load_config().network
    report = Report()
    _probe_network(report)
    checks = [c for c in report.checks if c.name == "network.proxy"]
    check = checks[-1] if checks else None

    typer.echo("Proxy test")
    typer.echo(f"  Configured: {'yes' if _configured(net) else 'no'}")
    typer.echo(f"  Effective proxy: {_effective_proxy(net)}")
    typer.echo(f"  Enforce: {bool(net.enforce)}")
    if check is None:
        typer.echo("  Reachable: unknown")
        return

    reachable = "yes" if check.severity == "ok" and net.proxy_url else "no"
    if not net.proxy_url and check.severity == "ok":
        reachable = "n/a"
    detail = _ascii_detail(check.detail)
    if net.proxy_url:
        detail = detail.replace(net.proxy_url, _safe_proxy_url(net.proxy_url))
    typer.echo(f"  Reachable: {reachable}")
    typer.echo(f"  Result: {check.severity}: {detail}")
    if check.fix_hint:
        typer.echo(f"  Suggestion: {_ascii_detail(check.fix_hint)}")
