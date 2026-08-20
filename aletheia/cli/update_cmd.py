"""Software-update CLI for development and installed Aletheia runtimes."""

from __future__ import annotations

import json
import sys
import threading

import typer

_BRA  = "⣾⣽⣻⢿⡿⣟⣯⣷"
_R    = "\033[0m"
_BOLD = "\033[1m"
_DIM  = "\033[2m"
_RED  = "\033[31m"
_GRN  = "\033[32m"
_CYN  = "\033[36m"
_WH   = "\033[97m"


class _Spinner:
    """2-line Braille spinner for the source-checkout update path."""

    def __init__(self):
        self._phase  = ""
        self._detail = ""
        self._drawn  = False
        self._i      = 0
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._erase()

    @property
    def last_phase(self) -> str:
        return self._phase

    def step(self, message: str) -> None:
        with self._lock:
            if self._phase:
                self._mark_done()
            self._phase  = message
            self._detail = ""
            self._drawn  = False

    def _erase(self) -> None:
        if not self._drawn:
            sys.stdout.write("\r\033[K")
        else:
            sys.stdout.write("\033[A\r\033[K\r\n\033[K\033[A\r\033[K")
        sys.stdout.flush()
        self._drawn = False

    def _mark_done(self) -> None:
        self._erase()
        sys.stdout.write(f"  {_GRN}{_BOLD}✓{_R}  {_DIM}{self._phase}{_R}\n")
        sys.stdout.flush()
        self._phase  = ""
        self._detail = ""

    def _draw(self) -> None:
        frame = _BRA[self._i % len(_BRA)]
        l1 = f"  {_CYN}↓{_R}  {_BOLD}{_WH}{self._phase}{_R}  {_DIM}{frame}{_R}"
        l2 = f"      {_DIM}{_CYN}{self._detail}{_R}" if self._detail else "      "
        if self._drawn:
            sys.stdout.write(f"\033[A\r\033[K{l1}\r\n\033[K{l2}")
        else:
            sys.stdout.write(f"\r\033[K{l1}\r\n\033[K{l2}")
            self._drawn = True
        sys.stdout.flush()

    def _spin(self) -> None:
        while not self._stop.wait(0.08):
            with self._lock:
                if self._phase:
                    self._draw()
                    self._i += 1


def _tty_phase_done(phase: str) -> None:
    sys.stdout.write(f"  {_GRN}{_BOLD}✓{_R}  {_DIM}{phase}{_R}\n")
    sys.stdout.flush()


def _tty_phase_fail(phase: str) -> None:
    sys.stdout.write(f"  {_RED}{_BOLD}✗{_R}  {_RED}{_BOLD}{phase}{_R}\n")
    sys.stdout.flush()


def _tty_error(code: str, phase: str, detail: str, fix: str = "") -> None:
    sys.stdout.write(f"\n  {_RED}{_BOLD}✗  Update failed:{_R}  {_BOLD}{code}{_R}\n\n")
    sys.stdout.write(f"    {_DIM}code    {_R}{code}\n")
    if phase:
        sys.stdout.write(f"    {_DIM}phase   {_R}{phase}\n")
    if detail:
        sys.stdout.write(f"    {_DIM}detail  {_R}{detail}\n")
    if fix:
        sys.stdout.write(f"    {_DIM}fix     {_R}{fix}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()

update_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="Update a source checkout end to end or manage packaged releases.",
)
auto_app = typer.Typer(
    no_args_is_help=True,
    help="Follow stable release tags on an explicitly opted-in Linux Paper runtime every five minutes.",
)


def _is_packaged() -> bool:
    from aletheia.update.packaged_commands import is_packaged_mode

    return is_packaged_mode()


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    restart: bool = typer.Option(
        True,
        "--restart/--no-restart",
        help="Rebuild dashboard assets and restart a source runtime after verification.",
    ),
) -> None:
    """Install the latest verified update for this runtime."""

    if ctx.invoked_subcommand is not None:
        return
    _run_update(restart=restart)


def _run_update(*, restart: bool) -> None:
    if _is_packaged():
        if not restart:
            typer.echo(
                "`--no-restart` is only available for source checkouts; "
                "packaged updates require Tray activation and restart.",
                err=True,
            )
            raise typer.Exit(1)
        from aletheia.update.packaged_commands import stage_update_blocking

        result = stage_update_blocking()
        state = result.get("state") or {}
        if not result.get("accepted"):
            typer.echo(f"Update not started: {result.get('reason')}")
            raise typer.Exit(1)
        if state.get("status") == "up_to_date":
            typer.echo("Already up to date.")
            return
        if state.get("status") == "failed":
            typer.echo(f"Update staging failed: {state.get('error')}", err=True)
            raise typer.Exit(1)
        if state.get("phase") != "ready_to_switch":
            typer.echo(f"Unexpected update state: {state.get('phase')}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Release {state.get('target_release')} is verified and migration-preflighted.")
        typer.echo("Aletheia Tray will complete the short switch and automatic health check.")
        return

    from aletheia.update.continuity import apply_update_with_continuity

    if sys.stdout.isatty():
        spinner = _Spinner()
        with spinner:
            result = apply_update_with_continuity(restart=restart, progress=spinner.step)
        last_phase = spinner.last_phase
    else:
        spinner = None
        last_phase = ""
        result = apply_update_with_continuity(restart=restart)

    if not result.get("applied"):
        if spinner:
            if last_phase:
                _tty_phase_fail(last_phase)
            _tty_error(
                result.get("reason", "unknown"),
                last_phase,
                result.get("message") or "",
                fix=result.get("fix_hint") or "",
            )
        else:
            typer.echo(f"Update not applied: {result.get('reason')}")
            if result.get("message"):
                typer.echo(f"  {result['message']}")
        raise typer.Exit(1)

    continuity_state = result.get("continuity_state")
    comparison = (result.get("continuity") or {}).get("comparison") or {}
    old_commit = str(result.get("old_commit") or "")
    new_commit = str(result.get("new_commit") or "")
    source_changed = result.get("source_changed")
    if source_changed is None:
        source_changed = old_commit != new_commit
    runtime_old_commit = str(result.get("runtime_old_commit") or "")
    display_old_commit = runtime_old_commit if restart and runtime_old_commit else old_commit
    display_changed = bool(
        new_commit and display_old_commit and display_old_commit != new_commit
    )
    source_label = (new_commit or display_old_commit or "unknown")[:8]

    # Only directly observed structural breakage is a failed deployment. Missing
    # evidence is surfaced as UNPROVEN without rewriting an already-applied
    # source update into a false hard failure.
    if continuity_state == "failed":
        failures = comparison.get("failures") or []
        update_label = (
            f"Updated {display_old_commit[:8]} -> {new_commit[:8]}"
            if display_changed
            else f"Source unchanged at {source_label}"
        )
        if spinner and last_phase:
            _tty_phase_fail(last_phase)
        typer.echo(
            f"{update_label}, but Observer continuity verification FAILED.",
            err=True,
        )
        if failures:
            typer.echo(f"  failures: {', '.join(str(item) for item in failures)}", err=True)
        if result.get("message"):
            typer.echo(f"  {result['message']}", err=True)
        typer.echo(
            (
                "  Source update remains applied;"
                if source_changed
                else "  Source checkout is unchanged;"
            )
            + " inspect `aletheia control status --json` "
            "and `aletheia observe overview --json` before further mutation.",
            err=True,
        )
        raise typer.Exit(1)

    if spinner and last_phase:
        _tty_phase_done(last_phase)

    if display_changed:
        typer.echo(
            f"Updated {display_old_commit[:8]} -> {new_commit[:8]} "
            f"(deps reinstalled: {result['deps_reinstalled']})."
        )
    else:
        typer.echo(
            f"Already up to date at {source_label} "
            f"(deps reinstalled: {result['deps_reinstalled']})."
        )
    if restart:
        typer.echo("Gateway restarted and readiness verified.")
    else:
        typer.echo("Restart the gateway to load the new code: aletheia gateway restart")

    if continuity_state in {"unproven", "pending_restart"}:
        proof_gaps = comparison.get("proof_gaps") or []
        typer.echo(
            "Observer continuity is UNPROVEN; no verified claim was emitted.",
            err=True,
        )
        if proof_gaps:
            typer.echo(
                f"  proof gaps: {', '.join(str(item) for item in proof_gaps)}",
                err=True,
            )
        if result.get("message"):
            typer.echo(f"  {result['message']}", err=True)
        return

    if result.get("continuity_verified") is True:
        owner = comparison.get("owner") or "unknown"
        worker_sha = comparison.get("worker_sha_after")
        worker_text = str(worker_sha)[:12] if worker_sha else "unavailable"
        typer.echo(
            "Observer continuity verified: "
            f"owner={owner}, worker_sha={worker_text}, "
            f"data_root_unchanged={comparison.get('data_root_unchanged')}, "
            f"main_health={comparison.get('main_health_status')}."
        )
        warnings = comparison.get("warnings") or []
        if warnings:
            typer.echo(f"Observer warnings: {', '.join(str(item) for item in warnings)}")


@update_app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")):
    """Show durable local update progress without repeating a network check."""

    from aletheia.update.applier import update_progress

    result = update_progress()
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    typer.echo(f"Mode: {result.get('mode', 'unknown')}")
    typer.echo(f"Phase: {result.get('phase', 'unknown')}")
    typer.echo(f"Status: {result.get('status', 'unknown')}")
    if result.get("target_release"):
        typer.echo(f"Target: {result['target_release']}")
    if result.get("progress") is not None:
        typer.echo(f"Progress: {float(result['progress']) * 100:.0f}%")
    if result.get("message"):
        typer.echo(str(result["message"]))
    if result.get("error"):
        typer.echo(f"Error: {result['error']}", err=True)


@update_app.command()
def check():
    """Check whether a newer development commit or signed release is available."""

    from aletheia.update.applier import check_for_update

    result = check_for_update()
    if not result.get("ok"):
        typer.echo(f"Update check unavailable: {result.get('reason')}")
        if result.get("message"):
            typer.echo(f"  {result['message']}")
        raise typer.Exit(0)

    if result.get("mode") == "packaged":
        if result.get("has_update"):
            typer.echo(
                f"Signed update available: {result.get('available_release')} ({result.get('size_bytes', 0)} bytes)."
            )
            typer.echo("Run `aletheia update` to stage and install it through Aletheia Tray.")
        else:
            typer.echo(f"Up to date ({result.get('current_release')}).")
        return

    if result["has_update"]:
        typer.echo(f"Update available: {result['behind_count']} commit(s) behind origin/{result['branch']}.")
        typer.echo(f"  current: {result['current_commit'][:8]}  remote: {result['remote_commit'][:8]}")
        typer.echo("Run `aletheia update` for the complete pull, build, and restart.")
    else:
        typer.echo(f"Up to date on origin/{result['branch']} ({result['current_commit'][:8]}).")


@update_app.command()
def rollback(yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")):
    """Request rollback within the packaged update safety window."""

    if not _is_packaged():
        typer.echo("Packaged rollback is unavailable in a development checkout.", err=True)
        raise typer.Exit(1)
    if not yes and not typer.confirm(
        "Restore the pre-update backup and previous release? Changes since the update will be lost."
    ):
        typer.echo("Aborted.")
        raise typer.Exit(0)
    from aletheia.update.applier import rollback_update

    result = rollback_update()
    if not result.get("accepted"):
        typer.echo(f"Rollback not requested: {result.get('reason')}", err=True)
        if result.get("message"):
            typer.echo(f"  {result['message']}", err=True)
        raise typer.Exit(1)
    typer.echo("Rollback request accepted. Aletheia Tray will restore the checkpoint safely.")


@auto_app.command("status")
def auto_status(json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")):
    """Show durable release-tag deployment state without checking the network."""
    from aletheia.update.auto import status

    result = status()
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    typer.echo(f"Phase: {result.get('phase', 'idle')}")
    typer.echo(f"Status: {result.get('status', 'not_installed')}")
    if result.get("target_tag"):
        typer.echo(f"Target: {result['target_tag']} ({str(result.get('target_sha') or '')[:12]})")
    if result.get("reason"):
        typer.echo(f"Reason: {result['reason']}")


@auto_app.command("run")
def auto_run():
    """Check once and deploy a newer stable release tag on a Paper runtime."""
    from aletheia.update.auto import run

    result = run()
    typer.echo(result.get("reason", "unknown"))
    if result.get("detail"):
        typer.echo(f"  {result['detail']}")
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("resume")
def auto_resume():
    """Clear a recovery hold after the operator has repaired the runtime."""
    from aletheia.update.auto import resume

    result = resume()
    typer.echo(result.get("reason", "resumed"))
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("install")
def auto_install():
    """Install the timer; set ALETHEIA_RUNTIME_ROLE=paper for auto-deployment."""
    from aletheia.update.auto import install

    result = install()
    typer.echo(result.get("reason", "unknown"))
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("uninstall")
def auto_uninstall():
    """Remove only Aletheia's release-tag timer and service units."""
    from aletheia.update.auto import uninstall

    result = uninstall()
    typer.echo(result.get("reason", "unknown"))
    if not result.get("ok"):
        raise typer.Exit(1)


update_app.add_typer(auto_app, name="auto")
