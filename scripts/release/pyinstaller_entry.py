"""PyInstaller entrypoint for the self-contained Aletheia runtime/CLI payload.

Private child modes let the frozen executable supervise itself without pretending the
binary is a generic Python interpreter. Normal user arguments continue into the existing
Typer application unchanged.
"""

from __future__ import annotations

import sys


def _install_process_adapter() -> None:
    from aletheia.packaged_process import install_frozen_subprocess_adapter

    install_frozen_subprocess_adapter()


def _run_packaged_gateway_runner() -> None:
    _install_process_adapter()
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    from aletheia._gateway_runner import main

    main()


def _run_packaged_uvicorn() -> None:
    _install_process_adapter()
    # Reuse uvicorn's own CLI parser so the source and packaged child processes accept
    # the same options and exit semantics.
    sys.argv = ["uvicorn", *sys.argv[2:]]
    from uvicorn.main import main as uvicorn_main

    uvicorn_main()


def main() -> None:
    _install_process_adapter()
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == "__packaged_gateway_runner":
        _run_packaged_gateway_runner()
        return
    if mode == "__packaged_uvicorn":
        _run_packaged_uvicorn()
        return

    from aletheia.main import app

    app()


if __name__ == "__main__":
    main()
