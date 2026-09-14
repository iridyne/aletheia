"""Read-only Learning qualification and research-economics commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import typer

learning_app = typer.Typer(no_args_is_help=True)
nominations_app = typer.Typer(no_args_is_help=True)
economic_knowledge_app = typer.Typer(no_args_is_help=True)
yield_app = typer.Typer(no_args_is_help=True)
learning_app.add_typer(nominations_app, name="nominations")
learning_app.add_typer(economic_knowledge_app, name="knowledge")
learning_app.add_typer(yield_app, name="yield")


def _echo(value) -> None:
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _window(start: str | None, end: str | None) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (
        start or (now - timedelta(days=30)).isoformat(),
        end or now.isoformat(),
    )


@nominations_app.command("list")
def list_nominations(limit: int = typer.Option(100, min=1, max=1000)):
    """List append-only hypothesis generation attempts."""
    from aletheia.learning.ledger import list_nominations

    _echo({"contract_version": "hypothesis-nomination/v1", "items": list_nominations(limit=limit)})


@nominations_app.command("inspect")
def inspect_nomination(nomination_id: str):
    """Inspect one exact generation attempt and publication result."""
    from aletheia.learning.ledger import get_nomination

    item = get_nomination(nomination_id)
    _echo({
        "availability": "available" if item is not None else "unavailable",
        "unavailable_reason": None if item is not None else "nomination_not_found",
        "item": item,
    })


@economic_knowledge_app.command("list")
def list_economic_knowledge():
    """List current read-only Economic Knowledge qualifications."""
    from aletheia.learning.economic_knowledge import assess_economic_knowledge

    assessment = assess_economic_knowledge("")
    _echo({
        "contract_version": "economic-knowledge/v1",
        "availability": assessment.status,
        "unavailable_reasons": assessment.reasons,
        "current_qualified_n": None,
        "items": [],
        "production_economic_contribution_state": "unavailable",
        "fixture_evidence_is_not_production_proof": True,
    })


@economic_knowledge_app.command("inspect")
def inspect_economic_knowledge(hypothesis_id: str):
    """Inspect current and historical qualification for one hypothesis."""
    from aletheia.learning.economic_knowledge import assess_economic_knowledge

    assessment = assess_economic_knowledge("")
    _echo({
        "hypothesis_id": hypothesis_id,
        "availability": assessment.status,
        "unavailable_reasons": assessment.reasons,
        "current": [],
        "history": [],
    })


@yield_app.command("report")
def yield_report(
    start: str | None = typer.Option(None, help="Inclusive ISO-8601 start; defaults to 30 days ago."),
    end: str | None = typer.Option(None, help="Exclusive ISO-8601 end; defaults to now."),
    path_id: str | None = typer.Option(None),
):
    """Report version-compatible Knowledge Yield and explicit missing coverage."""
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report

    window_start, window_end = _window(start, end)
    _echo(build_knowledge_yield_report(
        window_start=window_start,
        window_end=window_end,
        path_id=path_id,
    ))


@learning_app.command("meta-recommendations")
def meta_recommendations(
    start: str | None = typer.Option(None),
    end: str | None = typer.Option(None),
    path_id: str | None = typer.Option(None),
):
    """Return deterministic advisory #1093 research actions."""
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.meta_evolution import recommend_meta_evolution

    window_start, window_end = _window(start, end)
    report = build_knowledge_yield_report(
        window_start=window_start,
        window_end=window_end,
        path_id=path_id,
    )
    _echo({
        "contract_version": "meta-evolution-recommendation/v1",
        "advisory_only": True,
        "items": recommend_meta_evolution(report),
    })
