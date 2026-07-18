"""Knowledge base CLI commands (experience loop, §6)."""

import json
import typer

knowledge_app = typer.Typer(no_args_is_help=True)


@knowledge_app.command("remediate-outcomes")
def remediate_outcomes(
    apply: bool = typer.Option(
        False, "--apply",
        help="Apply additive classification/archive changes. Default is dry-run.",
    ),
):
    """Plan non-destructive outcome-history remediation; apply only when requested.

    Paper-cash compensation is always report-only and still requires a separate owner
    decision. This command cannot apply it.
    """
    from aletheia.storage.migrations import run_migrations
    from aletheia.maintenance.outcome_integrity import remediate_outcome_history

    run_migrations()
    result = remediate_outcome_history(dry_run=not apply)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@knowledge_app.command()
def reindex():
    """Rebuild the full-text search index from existing high-confidence knowledge.

    Use this once after enabling the experience loop so prior lessons become
    retrievable. Idempotent and safe to re-run.
    """
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.retrieval import reindex_all

    run_migrations()
    count = reindex_all()
    typer.echo(f"Reindexed {count} knowledge item(s) into the search index.")


@knowledge_app.command()
def search(query: str = typer.Argument(...), k: int = typer.Option(5, help="Max results")):
    """Search prior lessons (high-confidence + active only) — preview what agents would retrieve."""
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.retrieval import retrieve_lessons

    run_migrations()
    lessons = retrieve_lessons(query, k=k)
    if not lessons:
        typer.echo("No matching lessons (index empty, or none high-confidence/active).")
        return
    for ls in lessons:
        typer.echo(f"[{ls.item_type} · {ls.confidence} · {ls.created_at[:10]}] (score={ls.score})")
        typer.echo(f"  {ls.title}: {ls.snippet}")


@knowledge_app.command()
def archive_synthetic_case_studies(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only count matching items."),
):
    """Archive historical synthetic trade case studies and rebuild FTS.

    This keeps the rows for audit history but removes them from active Learning
    retrieval. It targets case studies with synthetic close reasons such as
    reconciled_gone and demo_validation_cleanup.
    """
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.maintenance import archive_synthetic_trade_case_studies

    run_migrations()
    summary = archive_synthetic_trade_case_studies(dry_run=dry_run)
    typer.echo(summary)


@knowledge_app.command()
def downgrade_unindexable_high_confidence(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only count matching items."),
):
    """Downgrade high-confidence knowledge rows that cannot be rebuilt into FTS."""
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.maintenance import downgrade_unindexable_high_confidence_items

    run_migrations()
    summary = downgrade_unindexable_high_confidence_items(dry_run=dry_run)
    typer.echo(summary)


@knowledge_app.command("backfill-content")
def backfill_content(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only count matching items."),
):
    """Copy Markdown bodies from disk into knowledge_items.content.

    Rebuilds the FTS index at the end. Safe to re-run — only touches rows whose
    DB content is empty and whose file_path resolves to a readable Markdown
    file (via the Windows/POSIX legacy-path migration).
    """
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.maintenance import backfill_content_from_disk

    run_migrations()
    summary = backfill_content_from_disk(dry_run=dry_run)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


@knowledge_app.command("scan-recovery-sources")
def scan_recovery_sources(
    extra_root: list[str] = typer.Option(
        [], "--extra-root",
        help="Additional filesystem root to scan (e.g. a Time Machine restore).",
    ),
    limit: int = typer.Option(
        0, "--limit",
        help="Maximum number of orphan rows to inspect (0 = no limit).",
    ),
    show_candidates: int = typer.Option(
        20, "--show-candidates",
        help="How many resolved candidates to include in the JSON output.",
    ),
):
    """Locate on-disk bodies for orphan high-confidence knowledge items.

    READ-ONLY. Produces a dry-run report of which orphan rows could be recovered
    from candidate filesystem roots. Use this before ``downgrade-unindexable``
    so recoverable items are not downgraded prematurely.
    """
    from pathlib import Path
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.recovery import scan_orphan_bodies

    run_migrations()
    roots = [Path(r).expanduser() for r in extra_root]
    summary = scan_orphan_bodies(
        extra_roots=roots or None,
        limit=limit or None,
    )
    preview = summary["candidates"][:show_candidates] if show_candidates else []
    printable = {
        k: v for k, v in summary.items() if k != "candidates"
    }
    printable["preview"] = preview
    typer.echo(json.dumps(printable, indent=2, ensure_ascii=False))


@knowledge_app.command()
def downgrade_unattributed_case_studies(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only count matching items."),
):
    """Downgrade high-confidence trade cases with no real scanner_family."""
    from aletheia.storage.migrations import run_migrations
    from aletheia.knowledge.maintenance import downgrade_unattributed_trade_case_studies

    run_migrations()
    summary = downgrade_unattributed_trade_case_studies(dry_run=dry_run)
    typer.echo(summary)
