"""Install a user-level shim that reroutes to this checkout's uv project."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
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


def _shim_path(bin_dir: Path, *, platform: str = os.name) -> Path:
    return bin_dir / ("aletheia.cmd" if platform == "nt" else "aletheia")


def _shim_content(root: Path, *, platform: str = os.name) -> str:
    root_str = str(root).rstrip("/\\")
    if platform == "nt":
        # Double-quote the project path so paths with spaces and parentheses
        # work in CMD. Windows file paths cannot contain literal " characters.
        return (
            f"@rem {_MARKER}\r\n"
            "@echo off\r\n"
            f'uv run --project "{root_str}" -- aletheia %*\r\n'
        )
    return (
        f"#!/bin/sh\n# {_MARKER}\n"
        f"exec uv run --project {shlex.quote(root_str)} -- aletheia \"$@\"\n"
    )


def _is_managed_shim(path: Path) -> bool:
    try:
        return _MARKER in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


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


def _verify_shim(shim: Path, root: Path) -> bool:
    if not shim.exists():
        return False
    try:
        content = shim.read_text(encoding="utf-8")
        root_str = str(root).rstrip("/\\")
        return _MARKER in content and root_str in content
    except (OSError, UnicodeDecodeError):
        return False


def _run_update_shell() -> int:
    result = subprocess.run(
        ["uv", "tool", "update-shell"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        typer.echo(
            f"Warning: `uv tool update-shell` failed:\n{result.stderr.strip()}",
            err=True,
        )
    else:
        typer.echo(
            "PATH configuration updated.\n"
            "Open a new terminal, then run:\n\n    aletheia doctor"
        )
    return result.returncode


@app.command()
def install(
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing executable that is not an Aletheia uv shim.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Auto-accept PATH setup via `uv tool update-shell` (for CI/automation).",
    ),
    no_update_shell: bool = typer.Option(
        False,
        "--no-update-shell",
        help="Install the shim but never modify shell PATH configuration.",
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

    root = project_root()
    _write_shim(shim, _shim_content(root))

    if not _verify_shim(shim, root):
        typer.echo("Shim verification failed after install.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Aletheia CLI shim installed at {shim}.")

    if _path_contains(bin_dir, os.environ.get("PATH", "")):
        typer.echo("You can now run `aletheia` from any directory.")
        return

    if no_update_shell:
        typer.echo(
            f"Note: {bin_dir} is not on PATH. "
            "Add it manually or run `uv tool update-shell` and open a new terminal.",
            err=True,
        )
        return

    if yes:
        _run_update_shell()
        return

    if not _is_interactive():
        typer.echo(
            f"Note: {bin_dir} is not on PATH. "
            "Run `uv tool update-shell`, then open a new terminal.",
            err=True,
        )
        return

    typer.echo(
        f"\nAletheia's command directory is not currently on PATH:\n  {bin_dir}\n"
    )
    if typer.confirm(
        "Add it to your shell PATH using `uv tool update-shell`?", default=True
    ):
        _run_update_shell()
    else:
        typer.echo(
            "Skipped. Run `uv tool update-shell` manually, then open a new terminal.",
            err=True,
        )
