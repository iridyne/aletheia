"""`aletheia audit` — audit trail verification commands."""

import typer

audit_app = typer.Typer(no_args_is_help=True)


@audit_app.command("verify")
def verify():
    """Verify the integrity of the audit.jsonl hash chain."""
    from aletheia.files.jsonl import verify_audit_chain

    result = verify_audit_chain()
    typer.echo(f"Records: {result['records']}")

    if result["valid"]:
        typer.echo("Hash chain: VALID ✓")
        raise typer.Exit(0)
    else:
        typer.echo(f"Hash chain: BROKEN ({len(result['errors'])} error(s))")
        for err in result["errors"][:20]:
            typer.echo(f"  • {err}")
        if len(result["errors"]) > 20:
            typer.echo(f"  … and {len(result['errors']) - 20} more")
        raise typer.Exit(1)
