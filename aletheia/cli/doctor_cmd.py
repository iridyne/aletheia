"""`aletheia doctor` — probe and print the machine's real state.

Read-only by default. `--fix` applies the conservative infrastructure repairs
(start gateway / clear orphans); it never clears the kill switch or touches
trading state. `--fetch` does a `git fetch` first so the code-sync line reflects
live origin rather than the last fetch.
"""

import typer
import sys
from datetime import datetime, timezone
from pathlib import Path

doctor_app = typer.Typer(no_args_is_help=False)

_SEV_GLYPH = {"ok": "✓", "info": "·", "warn": "!", "blocker": "✗"}
_SEV_ASCII = {"ok": "OK", "info": "-", "warn": "!", "blocker": "X"}


def _stdout_supports(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


def _glyph(severity: str) -> str:
    glyph = _SEV_GLYPH.get(severity, "·")
    return glyph if _stdout_supports(glyph) else _SEV_ASCII.get(severity, "-")


def _arrow() -> str:
    return "→" if _stdout_supports("→") else "->"


def _print_report(report) -> None:
    typer.echo(f"Aletheia doctor — {report.generated_at}")
    typer.echo("=" * 64)
    for c in report.checks:
        glyph = _glyph(c.severity)
        typer.echo(f" {glyph} {c.name:26s} {c.detail}")
        if c.fix_hint and c.severity in ("warn", "blocker"):
            typer.echo(f"     {_arrow()} {c.fix_hint}")
    typer.echo("=" * 64)

    blockers, warns = report.blockers, report.warns
    if blockers:
        typer.echo(f"BLOCKERS ({len(blockers)}): {', '.join(c.name for c in blockers)}")
    if warns:
        typer.echo(f"WARNINGS ({len(warns)}): {', '.join(c.name for c in warns)}")
    if not blockers and not warns:
        typer.echo("All clear.")


def _print_deep_diagnostics(bundle: dict) -> None:
    typer.echo("\nDeep diagnostics")
    typer.echo("-" * 64)

    attention = bundle.get("attention") or []
    if attention:
        typer.echo("Attention:")
        for item in attention[:20]:
            typer.echo(f"  - {item.get('severity')} {item.get('name')}: {item.get('detail')}")
            if item.get("log_hint"):
                typer.echo(f"    log: {item.get('log_hint')}")
    else:
        typer.echo("Attention: no warn/blocker checks")

    logs = bundle.get("logs") or {}
    policy = logs.get("policy") or {}
    if policy:
        typer.echo("\nLog policy:")
        typer.echo(
            "  text logs: "
            f"{policy.get('text_logs_rotate_at_mb')}MB x "
            f"{policy.get('text_logs_keep_generations')} generations"
        )
        typer.echo(f"  jsonl logs: {policy.get('jsonl_rotate_at_mb')}")
    if logs.get("files"):
        typer.echo("\nLog files:")
        for row in logs["files"][:25]:
            if row.get("exists"):
                typer.echo(f"  - {row.get('name')}: {row.get('bytes')} bytes")

    failures = bundle.get("recent_failures") or []
    typer.echo("\nRecent failed agent runs:")
    if failures:
        for row in failures[:12]:
            typer.echo(
                f"  - {row.get('agent_name')} {row.get('status')} "
                f"{row.get('finished_at') or row.get('started_at')}: {row.get('error') or ''}"
            )
    else:
        typer.echo("  none")

    tails = logs.get("tails") or {}
    typer.echo("\nRedacted log tails:")
    for name, lines in tails.items():
        if not lines:
            continue
        typer.echo(f"  [{name}]")
        for line in lines[-8:]:
            typer.echo(f"    {line}")


def _write_bundle(path: Path, report, diagnostics: dict, fix_actions=None) -> Path:
    import json
    out = report.to_dict()
    out["diagnostics"] = diagnostics
    if fix_actions is not None:
        out["fix_actions"] = fix_actions
    out["bundle_generated_at"] = datetime.now(timezone.utc).isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@doctor_app.callback(invoke_without_command=True)
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Apply conservative infra repairs (start gateway, clear orphans). Never clears kill switch."),
    fetch: bool = typer.Option(False, "--fetch", help="git fetch first so code-sync reflects live origin."),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as machine-readable JSON (for AI agents / scripts) instead of the human table."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include redacted log tails, recent failures, and next-log pointers."),
    deep: bool = typer.Option(False, "--deep", help="Alias for --verbose; intended for interactive troubleshooting."),
    bundle: Path | None = typer.Option(None, "--bundle", help="Write a redacted JSON diagnostic bundle to this file."),
):
    """Probe and print this machine's real state (the single source of truth).

    `--json` makes doctor an AI-agent-readable diagnostic surface: the report is
    emitted as a stable JSON contract (severity/name/detail/fix_hint as fields),
    so an agent can detect anomalies without scraping the printed table.
    """
    if fetch and not json_out:
        from aletheia.update import git_ops
        if git_ops.is_repo():
            r = git_ops.fetch(git_ops.current_branch() or "master")
            typer.echo("fetched origin." if r.get("ok") else f"fetch skipped: {r.get('reason')}")
    elif fetch:
        from aletheia.update import git_ops
        if git_ops.is_repo():
            git_ops.fetch(git_ops.current_branch() or "master")  # silent under --json

    from aletheia.diagnostics import doctor as dx
    report = dx.build_report()

    include_deep = verbose or deep or bundle is not None

    if json_out:
        import json
        fix_actions = None
        if fix:
            fix_actions = dx.apply_fixes()
            report = dx.build_report()  # re-probe so JSON reflects post-fix state
        out = report.to_dict()
        if include_deep:
            diagnostics = dx.build_deep_diagnostics(report)
            out["diagnostics"] = diagnostics
            if bundle is not None:
                _write_bundle(bundle, report, diagnostics, fix_actions=fix_actions)
        if fix_actions is not None:
            out["fix_actions"] = fix_actions
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        raise typer.Exit(1 if report.blockers else 0)

    _print_report(report)
    diagnostics = None
    if include_deep:
        diagnostics = dx.build_deep_diagnostics(report)
        if verbose or deep:
            _print_deep_diagnostics(diagnostics)
    fix_actions = None
    if fix:
        typer.echo("\nApplying fixes (--fix)...")
        fix_actions = dx.apply_fixes()
        for action in fix_actions:
            typer.echo(f"  - {action}")
        typer.echo("\nRe-probing after fixes:")
        report = dx.build_report()
        _print_report(report)
        if include_deep:
            diagnostics = dx.build_deep_diagnostics(report)
            if verbose or deep:
                _print_deep_diagnostics(diagnostics)
    if bundle is not None:
        diagnostics = diagnostics or dx.build_deep_diagnostics(report)
        written = _write_bundle(bundle, report, diagnostics, fix_actions=fix_actions)
        typer.echo(f"\nDiagnostic bundle written: {written}")

    raise typer.Exit(1 if report.blockers else 0)
