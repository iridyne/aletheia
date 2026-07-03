"""Knowledge base CLI commands (experience loop, §6)."""

import typer

knowledge_app = typer.Typer(no_args_is_help=True)


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
    typer.echo(f"Reindexed {count} high-confidence knowledge item(s) into the search index.")


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
