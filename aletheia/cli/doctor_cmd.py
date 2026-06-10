"""`aletheia doctor` — probe and print the machine's real state.

Read-only by default. `--fix` applies the conservative infrastructure repairs
(start gateway / clear orphans); it never clears the kill switch or touches
trading state. `--fetch` does a `git fetch` first so the code-sync line reflects
live origin rather than the last fetch.
"""

import typer

doctor_app = typer.Typer(no_args_is_help=False)

_SEV_GLYPH = {"ok": "✓", "info": "·", "warn": "!", "blocker": "✗"}


def _print_report(report) -> None:
    typer.echo(f"Aletheia doctor — {report.generated_at}")
    typer.echo("=" * 64)
    for c in report.checks:
        glyph = _SEV_GLYPH.get(c.severity, "·")
        typer.echo(f" {glyph} {c.name:26s} {c.detail}")
        if c.fix_hint and c.severity in ("warn", "blocker"):
            typer.echo(f"     → {c.fix_hint}")
    typer.echo("=" * 64)

    blockers, warns = report.blockers, report.warns
    if blockers:
        typer.echo(f"BLOCKERS ({len(blockers)}): {', '.join(c.name for c in blockers)}")
    if warns:
        typer.echo(f"WARNINGS ({len(warns)}): {', '.join(c.name for c in warns)}")
    if not blockers and not warns:
        typer.echo("All clear.")


@doctor_app.callback(invoke_without_command=True)
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Apply conservative infra repairs (start gateway, clear orphans). Never clears kill switch."),
    fetch: bool = typer.Option(False, "--fetch", help="git fetch first so code-sync reflects live origin."),
):
    """Probe and print this machine's real state (the single source of truth)."""
    if fetch:
        from aletheia.update import git_ops
        if git_ops.is_repo():
            r = git_ops.fetch(git_ops.current_branch() or "master")
            typer.echo("fetched origin." if r.get("ok") else f"fetch skipped: {r.get('reason')}")

    from aletheia.diagnostics import doctor as dx
    report = dx.build_report()
    _print_report(report)

    if fix:
        typer.echo("\nApplying fixes (--fix)...")
        for action in dx.apply_fixes():
            typer.echo(f"  • {action}")
        typer.echo("\nRe-probing after fixes:")
        _print_report(dx.build_report())

    raise typer.Exit(1 if report.blockers else 0)
