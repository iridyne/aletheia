"""Typed durable records for the measured Learning lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningHypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=_id)
    fingerprint: str
    cohort_id: str
    path_id: str
    overlap_group: str
    source_type: str
    source_artifact_ids_json: str = "[]"
    scope_json: str
    action_json: str
    consumer: str
    target_horizon: str
    status: str = "proposed"
    version: int = 1
    effect_threshold: float = 0.0
    harm_budget_json: str = "{}"
    evidence_summary_json: str = "{}"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    expires_at: str | None = None


class LearningExperiment(BaseModel):
    experiment_id: str = Field(default_factory=_id)
    hypothesis_id: str
    stage: str = "shadow"
    assignment_salt: str
    allocation_pct: float = 0.5
    min_samples_per_arm: int = 50
    min_market_days: int = 7
    primary_metric: str = "net_return_pct"
    promotion_gate_json: str = "{}"
    harm_gate_json: str = "{}"
    status: str = "registered"
    parent_experiment_id: str | None = None
    activation_verdict_id: str | None = None
    stage_generation: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str = Field(default_factory=_now)


class LearningExposure(BaseModel):
    exposure_id: str = Field(default_factory=_id)
    dedup_key: str
    experiment_id: str
    hypothesis_id: str
    assignment: str
    consumer: str
    consumer_stage: str
    path_id: str
    overlap_group: str
    assignment_unit_key: str | None = None
    candidate_key: str | None = None
    opportunity_id: str | None = None
    proposal_id: str | None = None
    decision_id: str | None = None
    observer_run_id: str | None = None
    position_id: str | None = None
    before_json: str = "{}"
    after_json: str = "{}"
    manifest_json: str = "{}"
    outcome_candidate_key: str | None = None
    outcome_source: str | None = None
    outcome_contract_version: str = "v1"
    experiment_stage: str | None = None
    code_commit: str | None = None
    policy_version: str | None = None
    concurrent_policy_epoch: str
    exposed_at: str = Field(default_factory=_now)


class LearningContributionVerdict(BaseModel):
    verdict_id: str = Field(default_factory=_id)
    experiment_id: str
    hypothesis_id: str
    stage: str | None = None
    window_start: str
    window_end: str
    unique_treatment_n: int
    unique_control_n: int
    baseline: float | None = None
    observed: float | None = None
    delta: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    harm_metrics_json: str = "{}"
    verdict: str
    reason: str
    created_at: str = Field(default_factory=_now)
    # P0-4 (2026-07-12): cohort identity. Authority requires the verdict to match the
    # CURRENT experiment epoch/stage/versions — a verdict computed under a stale policy
    # epoch (config/code changed) or a different stage must never be reused. The AT-01
    # verdict runner MUST stamp these from the exposures it aggregated.
    policy_epoch: str | None = None
    target_horizon: str | None = None
    outcome_contract_version: str = "v1"
    metric_version: str | None = None
    code_commit: str | None = None


class LearningLifecycleEvent(BaseModel):
    event_id: str = Field(default_factory=_id)
    dedup_key: str
    hypothesis_id: str
    experiment_id: str
    successor_experiment_id: str | None = None
    verdict_id: str | None = None
    from_stage: str
    to_stage: str | None = None
    action: str
    reason: str
    policy_epoch: str | None = None
    created_at: str = Field(default_factory=_now)


class LearningJobHealth(BaseModel):
    job_id: str
    schedule_key: str
    last_expected_at: str | None = None
    last_started_at: str | None = None
    last_succeeded_at: str | None = None
    next_due_at: str | None = None
    input_watermark: str | None = None
    backlog_count: int = 0
    oldest_backlog_at: str | None = None
    consecutive_failures: int = 0
    status: str = "unknown"
    last_error: str | None = None
    last_failed_at: str | None = None
    last_failure_reason: str | None = None
    updated_at: str = Field(default_factory=_now)


class LearningPolicyEpoch(BaseModel):
    epoch_id: str = Field(default_factory=_id)
    epoch_kind: str
    effective_values_hash: str
    effective_values_json: str
    affected_path_ids_json: str
    source_event_id: str | None = None
    safety_required: bool = False
    started_at: str
    ended_at: str | None = None
    created_at: str = Field(default_factory=_now)
