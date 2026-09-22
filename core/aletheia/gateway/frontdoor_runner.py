"""Run the public frontdoor on one or more operator-selected IPv4 addresses."""

from __future__ import annotations

import asyncio
import socket

import uvicorn

from aletheia.async_utils import apply_windows_selector_policy
from aletheia.config.loader import load_config
from aletheia.gateway.network import frontdoor_bind_hosts


def _listen_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(socket.SOMAXCONN)
    sock.setblocking(False)
    return sock


async def serve() -> None:
    cfg = load_config()
    hosts = frontdoor_bind_hosts(cfg)
    if not hosts:
        raise RuntimeError("Remote Control is enabled but no network adapter is selected")

    sockets = [_listen_socket(host, cfg.app.dashboard_port) for host in hosts]
    config = uvicorn.Config(
        "aletheia.gateway.frontdoor:app",
        log_level="info",
        loop="none",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


def main() -> None:
    apply_windows_selector_policy()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
