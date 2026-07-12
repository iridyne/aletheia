"""Trading-guard admin CLI (P0 2026-07-12).

Read-only audit of learned/auto guards and a reversible downgrade of any that the
old auto-promotion chain turned into live blockers/scorers back to shadow-only.
"""

import json
import typer

guard_app = typer.Typer(no_args_is_help=True)


@guard_app.command()
def audit():
    """READ-ONLY: list learned (auto-generated) guards that are active.

    Flags which ones are still enforcing (blocking or scoring, not shadow-only) —
    i.e. changing the ranker's behavior without an experiment authorizing them.
    """
    from aletheia.analytics.guard_promotion import audit_auto_guards

    rows = audit_auto_guards()
    enforcing = [r for r in rows if r.get("still_enforcing")]
    typer.echo(json.dumps({
        "learned_active_total": len(rows),
        "still_enforcing": len(enforcing),
        "guards": rows,
    }, indent=2, ensure_ascii=False))


@guard_app.command("shadow-downgrade")
def shadow_downgrade(
    apply: bool = typer.Option(
        False, "--apply",
        help="Apply the downgrade. Default is dry-run (report only).",
    ),
):
    """Downgrade learned active guards to shadow-only (observe-only).

    Reversible + auditable: original enforcement/block/penalty are saved in
    ``pre_shadow_*`` frontmatter, a GUARD_DOWNGRADED_TO_SHADOW event is recorded, and
    files are kept. Seed/manual safety guards are never touched. Default is dry-run.
    """
    from aletheia.analytics.guard_promotion import downgrade_learned_guards_to_shadow

    result = downgrade_learned_guards_to_shadow(dry_run=not apply)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
