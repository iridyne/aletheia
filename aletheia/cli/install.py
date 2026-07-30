"""Install a user-level shim that reroutes to this checkout's uv project."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)
_MARKER = "Aletheia uv project shim"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _path_contains(directory: Path, path_value: str) -> bool:
    expected = os.path.normcase(os.path.abspath(directory))
    return any(
        os.path.normcase(os.path.abspath(entry)) == expected
        for entry in path_value.split(os.pathsep)
        if entry
    )


def _shim_path(bin_dir: Path) -> Path:
    return bin_dir / ("aletheia.cmd" if os.name == "nt" else "aletheia")


def _shim_content(root: Path) -> str:
    if os.name == "nt":
        return (
            f"@rem {_MARKER}\r\n"
            "@echo off\r\n"
            f"uv run --project {root} -- aletheia %*\r\n"
        )
    return (
        f"#!/bin/sh\n# {_MARKER}\n"
        f"exec uv run --project {shlex.quote(str(root))} -- aletheia \"$@\"\n"
    )


def _is_managed_shim(path: Path) -> bool:
    try:
        return _MARKER in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def _write_shim(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@app.command()
def install(
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing executable that is not an Aletheia uv shim.",
    ),
) -> None:
    """Make `aletheia` reroute to this checkout's locked uv environment."""
    bin_result = subprocess.run(
        ["uv", "tool", "dir", "--bin"],
        check=False,
        capture_output=True,
        text=True,
    )
    if bin_result.returncode:
        typer.echo("Could not locate uv's executable directory.", err=True)
        raise typer.Exit(bin_result.returncode)

    bin_dir = Path(bin_result.stdout.strip())
    shim = _shim_path(bin_dir)
    if shim.exists() and not _is_managed_shim(shim) and not force:
        typer.echo(
            f"Refusing to replace existing command: {shim}\n"
            "Review it before retrying with --force.",
            err=True,
        )
        raise typer.Exit(2)

    _write_shim(shim, _shim_content(project_root()))
    typer.echo(f"Aletheia CLI shim installed at {shim}.")
    if not _path_contains(bin_dir, os.environ.get("PATH", "")):
        typer.echo(
            "That directory is not on PATH yet. Run `uv tool update-shell`, "
            "then open a new shell.",
            err=True,
        )
    else:
        typer.echo("You can now run `aletheia` from any directory.")
