"""PyInstaller entrypoint for the self-contained Aletheia runtime/CLI payload.

Private child modes let the frozen executable supervise, bootstrap, stage, and migrate
itself without pretending the binary is a generic Python interpreter. Normal user
arguments continue into the existing Typer application unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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
    # Reuse uvicorn's own CLI parser so source and packaged child processes accept
    # the same options and exit semantics.
    sys.argv = ["uvicorn", *sys.argv[2:]]
    from uvicorn.main import main as uvicorn_main

    uvicorn_main()


def _run_bootstrap() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--feed-url-file", type=Path)
    args = parser.parse_args(sys.argv[2:])

    from aletheia.update.bootstrap import install_bootstrap_runtime

    result = install_bootstrap_runtime(
        host_root=args.host_root,
        source_root=args.source_root,
        public_key=args.public_key,
        feed_url_file=args.feed_url_file,
    )
    print(json.dumps(result, ensure_ascii=False))


def _run_update_stage() -> None:
    from aletheia.update.packaged_commands import stage_update_blocking

    result = stage_update_blocking()
    print(json.dumps(result, ensure_ascii=False))
    state = result.get("state") or {}
    if not result.get("accepted") or state.get("status") == "failed":
        raise SystemExit(1)


def _run_update_validate() -> None:
    # This is the sole data-preparation entry used after the old runtime is stopped.
    # Normal updates apply/validate migrations. Manual rollback restores the exact
    # pre-update backup first, then validates the old release against restored data.
    from aletheia.update.packaged_commands import prepare_activation_data

    print(json.dumps(prepare_activation_data(), ensure_ascii=False))


def _run_update_restore() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("__packaged_update_restore requires one absolute backup path")
    archive = Path(sys.argv[2]).expanduser()
    if not archive.is_absolute():
        raise SystemExit("backup path must be absolute")
    from aletheia.update.packaged_commands import restore_update_backup

    print(json.dumps(restore_update_backup(archive), ensure_ascii=False))


def main() -> None:
    _install_process_adapter()
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == "__packaged_gateway_runner":
        _run_packaged_gateway_runner()
        return
    if mode == "__packaged_uvicorn":
        _run_packaged_uvicorn()
        return
    if mode == "__packaged_bootstrap":
        _run_bootstrap()
        return
    if mode == "__packaged_update_stage":
        _run_update_stage()
        return
    if mode == "__packaged_update_validate":
        _run_update_validate()
        return
    if mode == "__packaged_update_restore":
        _run_update_restore()
        return

    from aletheia.main import app

    app()


if __name__ == "__main__":
    main()
