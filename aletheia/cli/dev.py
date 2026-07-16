"""Explicit, foreground-only development preview commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from aletheia.runtime_paths import preview_instance_dir

dev_app = typer.Typer(no_args_is_help=True)
_RESERVED_PORTS = frozenset({8787, 8788})


@dev_app.callback()
def dev() -> None:
    """Development preview commands; none manage the stable gateway."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _preview_data_dir(root: Path, port: int) -> Path:
    return preview_instance_dir(root, port)


@dev_app.command()
def api(
    port: int = typer.Option(..., "--port", min=1024, max=65535, help="Unused loopback port, for example 8791."),
):
    """Run an isolated reloadable API; never starts or controls the main gateway."""
    if port in _RESERVED_PORTS:
        raise typer.BadParameter(f"port {port} is reserved for the stable gateway", param_hint="--port")

    root = project_root()
    data_dir = _preview_data_dir(root, port)
    env = os.environ.copy()
    env.update({
        "ALETHEIA_PREVIEW_MODE": "1",
        "ALETHEIA_PREVIEW_DATA_DIR": str(data_dir),
        "ALETHEIA_RUNTIME_MODE": "shadow",
        "ALETHEIA_RUNTIME_STATE": "shadow",
        "ALETHEIA_RUNTIME_ID": f"dev-preview-{port}",
        "ALETHEIA_RUNTIME_PORT": str(port),
    })
    command = [
        sys.executable, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--reload", "--reload-dir", str(root / "aletheia"),
    ]
    typer.echo(
        f"Starting isolated preview API at http://127.0.0.1:{port} "
        f"with isolated runtime state under {data_dir}"
    )
    result = subprocess.run(command, cwd=root, env=env, check=False)
    raise typer.Exit(getattr(result, "returncode", 0))
