"""Version-compatible Knowledge Yield and Learning cost accounting."""

from __future__ import annotations

import json
from datetime import datetime
from statistics import median

from pydantic import BaseModel, ConfigDict, Field

from aletheia.learning.economic_knowledge import project_current_economic_knowledge


class KnowledgeYieldSliceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_id: str
    behavior_contract_version: str
    metric_version: str
    outcome_contract_version: str
    target_horizon: str
    consumers: list[str] = Field(default_factory=list)
    generation_methods: list[str] = Field(default_factory=list)
    producers: list[str] = Field(default_factory=list)
    producer_versions: list[str] = Field(default_factory=list)
    source_bundle_hashes: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    root_outcome_identities: list[str] = Field(default_factory=list)
    derivation_parent_hypothesis_ids: list[str] = Field(default_factory=list)
    generation_attempt_n: int = Field(default=0, ge=0)
    research_status_counts: dict[str, int] = Field(default_factory=dict)
    compilable_nomination_n: int = Field(default=0, ge=0)
    advisory_nomination_n: int = Field(default=0, ge=0)
    unique_hypothesis_n: int = Field(default=0, ge=0)
    shadow_admission_n: int = Field(default=0, ge=0)
    real_intervention_hypothesis_n: int = Field(default=0, ge=0)
    confirmatory_outcome_n: int = Field(default=0, ge=0)
    redundancy_replay_ratio: float | None = Field(default=None, ge=0, le=1)
    exclusions: list[str] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    hypotheses_entering_prospective_test_n: int = Field(ge=0)
    qualified_economic_knowledge_n: int = Field(ge=0)
    knowledge_yield: float | None = Field(default=None, ge=0, le=1)
    invalid_verdict_n: int = Field(ge=0)
    refrozen_hypothesis_n: int = Field(ge=0)
    median_days_to_evidence: float | None = Field(default=None, ge=0)
    estimated_generation_cost_usd: float | None = Field(default=None, ge=0)
    operational_drag_ratio: float | None = Field(default=None, ge=0)
    availability: str
    unavailable_reasons: list[str] = Field(default_factory=list)


class KnowledgeYieldReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "knowledge-yield/v1"
    window_start: str
    window_end: str
    generation_attempt_n: int = Field(default=0, ge=0)
    published_nomination_n: int = Field(default=0, ge=0)
    unpublished_nomination_n: int = Field(default=0, ge=0)
    rejected_or_advisory_nomination_n: int = Field(default=0, ge=0)
    unique_nominated_hypothesis_n: int = Field(default=0, ge=0)
    generation_to_hypothesis_conversion: float | None = Field(
        default=None, ge=0, le=1,
    )
    total_recorded_generation_cost_usd: float | None = Field(default=None, ge=0)
    generation_cost_availability: str = "unavailable"
    current_economic_knowledge_n: int = Field(ge=0)
    slices: list[KnowledgeYieldSliceV1]
    cross_slice_aggregate: None = None


def build_knowledge_yield_report(
    *, window_start: str, window_end: str, path_id: str | None = None,
) -> KnowledgeYieldReportV1:
    """Measure conversion by compatible contract identity; never pool unlike metrics."""
    from aletheia.storage.sqlite import get_db

    start = _instant(window_start)
    end = _instant(window_end)
    if start is None or end is None or end <= start:
        raise ValueError("Knowledge Yield requires a valid increasing UTC window")
    experiments = get_db().execute(
        "SELECT e.experiment_id, e.created_at, e.ended_at, e.stage, "
        "e.status AS experiment_status, "
        "e.primary_metric, h.hypothesis_id, h.path_id, h.consumer, "
        "h.behavior_contract_version, "
        "h.target_horizon, h.status AS hypothesis_status "
        "FROM learning_experiments e JOIN learning_hypotheses h "
        "ON h.hypothesis_id=e.hypothesis_id ORDER BY e.created_at, e.experiment_id"
    ).fetchall()
    generation_rows = get_db().execute(
        "SELECT hypothesis_id, research_status, publication_status, envelope_json, "
        "created_at FROM learning_hypothesis_nominations ORDER BY created_at, nomination_id"
    ).fetchall()
    generation_attempts: list[dict] = []
    generation_costs: list[float] = []
    generation_cost_missing = False
    for row in generation_rows:
        created = _instant(str(row["created_at"]))
        if created is None or not start <= created < end:
            continue
        try:
            envelope = json.loads(row["envelope_json"])
            draft_path = str(envelope["draft"]["path_id"])
            cost = envelope["generation_manifest"].get("estimated_cost_usd")
        except (KeyError, TypeError, ValueError):
            draft_path, cost = "unavailable", None
        if path_id is not None and draft_path != path_id:
            continue
        generation_attempts.append(dict(row))
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            generation_cost_missing = True
        else:
            generation_costs.append(float(cost))
    facts = project_current_economic_knowledge()
    facts_by_experiment = {fact.experiment_id: fact for fact in facts}
    from aletheia.learning.registry import get_learning_path

    grouped: dict[tuple[str, str, str, str, str], list[dict]] = {}
    for row in experiments:
        data = dict(row)
        created = _instant(str(data["created_at"]))
        if created is None or not start <= created < end:
            continue
        if path_id is not None and data["path_id"] != path_id:
            continue
        data["outcome_contract_version"] = get_learning_path(
            str(data["path_id"])
        ).outcome_contract_version
        key = (
            str(data["path_id"]),
            str(data["behavior_contract_version"] or "unavailable"),
            str(data["primary_metric"] or "unavailable"),
            str(data["outcome_contract_version"] or "unavailable"),
            str(data["target_horizon"] or "unavailable"),
        )
        grouped.setdefault(key, []).append(data)

    slices: list[KnowledgeYieldSliceV1] = []
    for key, members in sorted(grouped.items()):
        experiment_ids = {str(member["experiment_id"]) for member in members}
        hypothesis_ids = {str(member["hypothesis_id"]) for member in members}
        qualified = [
            fact for experiment_id, fact in facts_by_experiment.items()
            if experiment_id in experiment_ids
            and (evidenced := _instant(fact.window_end)) is not None
            and start <= evidenced < end
        ]
        invalid_rows = get_db().execute(
            f"SELECT COUNT(*) FROM learning_contribution_verdicts "
            f"WHERE verdict='invalid' AND experiment_id IN "
            f"({','.join('?' for _ in experiment_ids)})",
            tuple(sorted(experiment_ids)),
        ).fetchone()
        invalid_n = int(invalid_rows[0]) if invalid_rows is not None else 0
        refrozen_n = sum(
            str(member["hypothesis_status"]) in {"refrozen", "idle_expired", "rejected"}
            for member in members
        )
        evidence_days: list[float] = []
        for fact in qualified:
            experiment = next(
                member for member in members
                if member["experiment_id"] == fact.experiment_id
            )
            created = _instant(str(experiment["created_at"]))
            evidenced = _instant(fact.window_end)
            if created is not None and evidenced is not None and evidenced >= created:
                evidence_days.append((evidenced - created).total_seconds() / 86_400)

        nomination_rows = get_db().execute(
            f"SELECT envelope_json, source_bundle_hash FROM learning_hypothesis_nominations "
            f"WHERE hypothesis_id IN ({','.join('?' for _ in hypothesis_ids)})",
            tuple(sorted(hypothesis_ids)),
        ).fetchall()
        costs: list[float] = []
        missing_cost = False
        methods: set[str] = set()
        producers: set[str] = set()
        producer_versions: set[str] = set()
        source_bundles: set[str] = set()
        source_artifacts: set[str] = set()
        root_outcomes: set[str] = set()
        parents: set[str] = set()
        research_counts: dict[str, int] = {}
        compilable_n = 0
        advisory_n = 0
        for row in nomination_rows:
            source_bundles.add(str(row["source_bundle_hash"]))
            try:
                envelope = json.loads(row["envelope_json"])
                manifest = envelope["generation_manifest"]
                draft = envelope["draft"]
                research = envelope["research_evidence"]
                value = manifest.get("estimated_cost_usd")
                methods.add(str(manifest.get("method") or "unavailable"))
                producers.add(str(manifest.get("producer") or "unavailable"))
                producer_versions.add(str(manifest.get("producer_version") or "unavailable"))
                parents.update(str(value) for value in manifest.get("parent_hypothesis_ids", []))
                source_artifacts.update(str(value) for value in draft.get("source_artifact_ids", []))
                root_outcomes.update(
                    str(value)
                    for value in research.get("observation", {}).get("outcome_ids", [])
                )
                research_status = str(research.get("research_status") or "unavailable")
                research_counts[research_status] = research_counts.get(research_status, 0) + 1
                compilable_n += draft.get("status") == "compilable"
                advisory_n += draft.get("status") == "advisory_only"
            except (KeyError, TypeError, ValueError):
                value = None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                missing_cost = True
            else:
                costs.append(float(value))
        if not nomination_rows:
            missing_cost = True

        verdict_rows = get_db().execute(
            f"SELECT hypothesis_id, verdict, inference_role, harm_metrics_json "
            f"FROM learning_contribution_verdicts WHERE experiment_id IN "
            f"({','.join('?' for _ in experiment_ids)})",
            tuple(sorted(experiment_ids)),
        ).fetchall()
        real_intervention_hypotheses: set[str] = set()
        for verdict_row in verdict_rows:
            try:
                verdict_metrics = json.loads(verdict_row["harm_metrics_json"] or "{}")
            except (TypeError, ValueError):
                verdict_metrics = {}
            if (
                isinstance(verdict_metrics, dict)
                and isinstance(verdict_metrics.get("actual_intervention_treatment_n"), int)
                and verdict_metrics["actual_intervention_treatment_n"] > 0
            ):
                real_intervention_hypotheses.add(str(verdict_row["hypothesis_id"]))
        confirmatory_n = sum(
            str(row["inference_role"]) == "confirmatory" for row in verdict_rows
        )

        denominator = len(hypothesis_ids)
        qualified_n = len({fact.hypothesis_id for fact in qualified})
        unavailable: list[str] = []
        exclusions: list[str] = []
        if missing_cost:
            unavailable.append("generation_cost_incomplete")
        if source_artifacts and not root_outcomes:
            unavailable.append("root_outcome_lineage_incomplete")
        if denominator == 0:
            unavailable.append("no_prospective_hypotheses")
        if invalid_n:
            exclusions.append("invalid_verdict")
        if refrozen_n:
            exclusions.append("refrozen_hypothesis")
        drag_events = invalid_n + refrozen_n
        slices.append(KnowledgeYieldSliceV1(
            path_id=key[0],
            behavior_contract_version=key[1],
            metric_version=key[2],
            outcome_contract_version=key[3],
            target_horizon=key[4],
            consumers=sorted({str(member["consumer"]) for member in members}),
            generation_methods=sorted(methods),
            producers=sorted(producers),
            producer_versions=sorted(producer_versions),
            source_bundle_hashes=sorted(source_bundles),
            source_artifact_ids=sorted(source_artifacts),
            root_outcome_identities=sorted(root_outcomes),
            derivation_parent_hypothesis_ids=sorted(parents),
            generation_attempt_n=len(nomination_rows),
            research_status_counts=dict(sorted(research_counts.items())),
            compilable_nomination_n=compilable_n,
            advisory_nomination_n=advisory_n,
            unique_hypothesis_n=len(hypothesis_ids),
            shadow_admission_n=sum(
                str(member["stage"]) == "shadow" for member in members
            ),
            real_intervention_hypothesis_n=len(real_intervention_hypotheses),
            confirmatory_outcome_n=confirmatory_n,
            redundancy_replay_ratio=(
                1.0 - len(source_bundles) / len(nomination_rows)
                if nomination_rows else None
            ),
            exclusions=exclusions,
            coverage={
                "experiments": len(members),
                "hypotheses": len(hypothesis_ids),
                "nominations": len(nomination_rows),
                "verdicts": len(verdict_rows),
            },
            hypotheses_entering_prospective_test_n=denominator,
            qualified_economic_knowledge_n=qualified_n,
            knowledge_yield=qualified_n / denominator if denominator else None,
            invalid_verdict_n=invalid_n,
            refrozen_hypothesis_n=refrozen_n,
            median_days_to_evidence=median(evidence_days) if evidence_days else None,
            estimated_generation_cost_usd=sum(costs) if costs and not missing_cost else None,
            operational_drag_ratio=drag_events / denominator if denominator else None,
            availability="complete" if not unavailable else "partial",
            unavailable_reasons=unavailable,
        ))
    published_n = sum(
        row["publication_status"] == "published" and row["hypothesis_id"] is not None
        for row in generation_attempts
    )
    unique_nominated = len({
        str(row["hypothesis_id"])
        for row in generation_attempts
        if row["hypothesis_id"] is not None
    })
    rejected_or_advisory_n = sum(
        str(row["research_status"]) in {
            "rejected", "insufficient_context", "advisory_only",
        }
        or row["publication_status"] != "published"
        for row in generation_attempts
    )
    return KnowledgeYieldReportV1(
        window_start=window_start,
        window_end=window_end,
        generation_attempt_n=len(generation_attempts),
        published_nomination_n=published_n,
        unpublished_nomination_n=len(generation_attempts) - published_n,
        rejected_or_advisory_nomination_n=rejected_or_advisory_n,
        unique_nominated_hypothesis_n=unique_nominated,
        generation_to_hypothesis_conversion=(
            unique_nominated / len(generation_attempts)
            if generation_attempts else None
        ),
        total_recorded_generation_cost_usd=(
            sum(generation_costs)
            if generation_costs and not generation_cost_missing else None
        ),
        generation_cost_availability=(
            "complete"
            if generation_attempts and not generation_cost_missing
            else "partial"
            if generation_costs
            else "unavailable"
        ),
        current_economic_knowledge_n=len(facts),
        slices=slices,
    )


def _instant(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
