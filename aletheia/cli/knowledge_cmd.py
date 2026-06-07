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
