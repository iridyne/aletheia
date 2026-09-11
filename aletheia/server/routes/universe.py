"""Universe Generator and owner-facing Entry Workbench API routes."""

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, JsonValue
from typing_extensions import NotRequired, TypedDict


# Read projections intentionally describe SQLite JSON, not domain write validators.
# Documentation-only response schemas preserve historical keys and absent fields.


def _hide_retired_candidate_scores(member: dict) -> None:
    if member.get("candidate_selection_policy") == "candidate_selection_utility_v1":
        member.update(dict.fromkeys((
            "total_score", "opportunity_score", "tradability_score", "liquidity_score",
            "crowding_risk", "late_entry_penalty", "all_scores",
        )))


class UniverseSnapshot(TypedDict):
    id: str
    created_at: str
    market_type: str
    policy_id: str | None
    status: str
    source_kind: str
    expires_at: str | None
    summary: str | None


class CandidateLedgerFields(TypedDict):
    id: str
    snapshot_id: str
    asset_id: str
    symbol: str
    rank: int
    total_score: float | None
    opportunity_score: float | None
    tradability_score: float | None
    liquidity_score: float | None
    estimated_cost: float | None
    crowding_risk: float | None
    late_entry_penalty: float | None
    scanner_family: str | None
    scanner_direction: str | None
    ttl_sec: int
    expires_at: str
    inclusion_reason: str | None
    risk_flags: str | None
    execution_eligible: int | None
    quote_volume_24h: float | None
    spread_bps: float | None
    entry_price_usd: float | None
    market_type: str
    regime_tag: str | None
    atr_pct: float | None
    all_scores: str | None
    learning_candidate_key: str | None
    candidate_selection_policy: str
    candidate_selection_policy_epoch: str | None
    candidate_selection_utility_pct: float | None
    opportunity_mechanism: str | None
    opportunity_mechanism_version: str | None
    opportunity_source_component: str | None
    opportunity_source_cutoff_at: str | None
    opportunity_evidence_hash: str | None
    candidate_opportunity_context_version: str | None
    candidate_opportunity_context_json: str | None
    admission_basis: str | None
    startup_reason: str | None
    pessimistic_strength: float | None
    candidate_opportunity_context_hash: str | None


class CandidateRow(CandidateLedgerFields):
    venue: str | None


class Candidate(TypedDict):
    id: str | None
    snapshot_id: str | None
    asset_id: str | None
    symbol: str
    rank: int
    total_score: float | None
    opportunity_score: NotRequired[float | None]
    tradability_score: float | None
    liquidity_score: NotRequired[float | None]
    estimated_cost: NotRequired[float | None]
    crowding_risk: NotRequired[float | None]
    late_entry_penalty: NotRequired[float | None]
    scanner_family: str | None
    scanner_direction: NotRequired[str | None]
    ttl_sec: int
    expires_at: str | None
    inclusion_reason: str | None
    risk_flags: str | None
    execution_eligible: int | None
    quote_volume_24h: NotRequired[float | None]
    spread_bps: NotRequired[float | None]
    entry_price_usd: NotRequired[float | None]
    market_type: NotRequired[str]
    regime_tag: NotRequired[str | None]
    atr_pct: NotRequired[float | None]
    all_scores: NotRequired[str | None]
    learning_candidate_key: NotRequired[str | None]
    venue: NotRequired[str | None]
    candidate_selection_policy: str
    candidate_selection_policy_epoch: str | None
    candidate_selection_utility_pct: float | None
    opportunity_mechanism: NotRequired[str | None]
    opportunity_mechanism_version: NotRequired[str | None]
    opportunity_source_component: NotRequired[str | None]
    opportunity_source_cutoff_at: NotRequired[str | None]
    opportunity_evidence_hash: NotRequired[str | None]
    candidate_opportunity_context_version: NotRequired[str | None]
    candidate_opportunity_context_json: NotRequired[str | None]
    candidate_opportunity_context_hash: NotRequired[str | None]
    stale: NotRequired[bool]
    entry_plan_id: NotRequired[str | None]
    entry_plan_status: NotRequired[str | None]
    entry_plan_execution_mode: NotRequired[str | None]
    setup_state: NotRequired[str | None]
    triggered_opportunity_id: NotRequired[str | None]
    admission_basis: NotRequired[str | None]
    startup_reason: NotRequired[str | None]
    pessimistic_strength: NotRequired[float | None]


class UniverseFeatureSnapshot(TypedDict):
    id: str
    asset_id: str
    ts: str
    quote_volume_24h: float | None
    volume_1h: float | None
    spread_bps: float | None
    depth_10bps_usd: float | None
    atr_pct: float | None
    rsi_14: float | None
    rsi_15m: float | None
    atr_15m_pct: float | None
    return_15m: float | None
    last_candle_range_atr_15m: float | None
    return_1h: float | None
    return_4h: float | None
    return_24h: float | None
    funding_rate: float | None
    open_interest: float | None
    oi_delta_1h: float | None
    data_freshness_sec: int | None
    rsi_4h: float | None
    atr_4h_pct: float | None
    bb_width_4h: float | None
    vol_ratio_4h: float | None
    bb_squeeze: int | None
    raw: str | None
    btc_beta: float | None
    taker_buy_ratio: float | None
    vol_ratio: float | None
    relative_strength_24h: float | None
    oi_price_divergence: int | None
    technical_algorithm_version: str | None
    technical_snapshot_manifest: str
    long_short_ratio: float | None
    top_trader_position_ratio: float | None


class CandidateOpportunity(TypedDict):
    id: str
    symbol: str
    market: str
    direction_bias: str | None
    thesis: str | None
    confidence: str
    confidence_score: int | None
    source_agent: str | None
    created_at: str
    status: str
    last_ranked_at: str | None
    linked_strategy_id: str | None
    asset_class: str | None
    metadata: str | None
    universe_snapshot_id: str | None
    universe_member_id: str | None
    scanner_family: str | None
    execution_eligible: int | None
    valid_until: str | None
    entry_zone: str | None
    stop_loss_hint: float | None
    tp_hint: float | None
    asset_subclass: str
    canonical_ticker: str
    regime_tag: str | None
    key_observations: str
    funding_rate: float | None
    open_interest: float | None
    oi_delta_1h: float | None
    runtime_session_id: str | None
    producer_commit: str | None
    behavior_fingerprint: str | None
    setup_id: str | None
    signal_mark: str | None
    trigger_behavior_version: str | None
    generation_version: str | None
    generation_json: str | None
    generation_hash: str | None


class EntryLifecycleEvent(TypedDict):
    event: str
    at: str | None
    reason: str | None


class EntryLifecycleCycle(TypedDict):
    generation_id: str | None
    cycle_number: int
    previous_generation_id: str | None
    cycle_reason: str | None
    requalification_candidate_id: str | None
    last_stage: str | None
    model_invoked: bool
    compiler_invoked: bool
    outcome_class: str | None
    outcome_reason: str | None
    entry_plan_id: str | None
    entry_plan_status: str | None
    execution_state: str
    execution: dict[str, Any]
    events: list[EntryLifecycleEvent]


class EntryLifecycleProjection(TypedDict):
    lifecycle_state: str
    lifecycle_reason: str | None
    current_cycle_number: int | None
    expired_plan_count: int
    current_generation_id: str | None
    current_plan_id: str | None
    last_stage: str | None
    model_invoked: bool
    compiler_invoked: bool
    state_changed_at: str | None
    execution_state: str
    next_action: str
    cycles: list[EntryLifecycleCycle]


class CandidatePlanningFields(TypedDict):
    id: str
    planning_key: str
    planning_identity: str
    candidate_id: str
    last_candidate_id: str
    venue: str
    symbol: str
    market_type: str
    candidate_family: str
    direction: str
    is_current: int
    status: str
    result_kind: str | None
    owner_token: str | None
    claim_expires_at: str | None
    retry_not_before: str | None
    attempt_count: int
    result_json: str | None
    entry_plan_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    runtime_session_id: str | None
    producer_commit: str | None
    behavior_fingerprint: str | None
    attention_status: str
    attention_started_at: str | None
    last_requalified_at: str | None
    requalification_source_json: str | None
    soft_expires_at: str | None
    hard_expires_at: str | None
    attention_max_age_sec: int | None
    attention_policy_version: str | None
    attention_terminal_at: str | None
    attention_terminal_reason: str | None
    planner_input_json: str | None
    planner_input_hash: str | None
    opportunity_mechanism: str | None
    candidate_selection_policy: str | None
    candidate_selection_policy_epoch: str | None
    mechanism_version: str | None
    cycle_number: int | None
    previous_generation_id: str | None
    cycle_reason: str | None
    requalification_candidate_id: str | None
    planning_started_at: str | None
    model_invoked_at: str | None
    compiler_invoked_at: str | None
    outcome_class: str | None
    outcome_reason: str | None
    entry_behavior_fingerprint: str | None
    schema_fingerprint: str | None
    admission_basis: NotRequired[str | None]
    startup_reason: NotRequired[str | None]
    pessimistic_strength: NotRequired[float | None]


class CandidateAttention(CandidatePlanningFields):
    model_invoked: int
    compiler_invoked: int
    last_stage: str | None


class CandidateAttentionWorkbench(CandidatePlanningFields, EntryLifecycleProjection):
    identity_version: str | None
    planner_contract_version: str | None
    activation_identity: str | None
    rank: int | None
    total_score: float | None
    entry_price_usd: float | None
    liquidity_score: float | None
    spread_bps: float | None
    inclusion_reason: str | None
    risk_flags: str | None
    execution_eligible: int | None
    candidate_selection_utility_pct: float | None
    opportunity_mechanism_version: str | None
    opportunity_source_component: str | None
    candidate_opportunity_context_json: str | None
    candidate_opportunity_context_hash: str | None
    opportunity_source_cutoff_at: str | None
    opportunity_evidence_hash: str | None
    candidate_expires_at: str | None
    active_entry_plan_id: str | None
    active_entry_plan_status: str | None


class CandidateWorkbenchRow(CandidateLedgerFields, EntryLifecycleProjection):
    venue: str
    registry_venue: str
    planning_generation_id: str | None
    planning_status: str | None
    result_kind: str | None
    planning_result_json: str | None
    attempt_count: int | None
    attention_status: str | None
    soft_expires_at: str | None
    hard_expires_at: str | None
    attention_terminal_reason: str | None
    planning_key: str | None
    cycle_number: int | None
    outcome_class: str | None
    outcome_reason: str | None
    entry_plan_id: str | None
    entry_plan_status: str | None
    entry_plan_terminal_reason: str | None


class PlanningHistoryItem(CandidatePlanningFields):
    identity_version: str | None
    planner_contract_version: str | None
    activation_identity: str | None
    last_stage: str | None
    model_invoked: int
    compiler_invoked: int
    entry_plan_status: str | None
    execution_mode: str | None
    order_id: str | None
    entry_plan_terminal_at: str | None
    entry_plan_terminal_reason: str | None


class EntryPlanRow(TypedDict):
    id: str
    planning_generation_id: str
    candidate_id: str
    planning_key: str
    version: str | None
    venue: str
    symbol: str
    market_type: str
    direction: str
    execution_mode: str
    plan_json: str
    plan_hash: str
    status: str
    order_id: str | None
    created_at: str
    updated_at: str
    terminal_at: str | None
    terminal_reason: str | None
    runtime_session_id: str | None
    producer_commit: str | None
    behavior_fingerprint: str | None
    monitoring_state: str | None
    monitoring_evidence_json: str | None
    monitoring_evidence_hash: str | None


class EntryPlanPayload(BaseModel):
    """Stored JSON, including immutable historical extension fields."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue]

    plan_id: str | None = None
    planning_generation_id: str
    candidate_id: str
    candidate_family: str
    venue: str
    symbol: str
    market_type: str
    direction: str
    thesis: str
    evidence_refs: list[str] | None = None
    scenario: str
    execution_mode: str
    trigger_spec_id: str | None = None
    trigger_params: dict[str, JsonValue] | None = None
    trigger_data_requirements: list[str] | None = None
    trigger_freshness_ms: int | None = None
    entry_zone: tuple[float, float]
    invalidation_conditions: list[str]
    invalidation_price: float
    stop_price: float
    target_price: float
    expected_horizon_hours: float
    approved_at: str
    expires_at: str
    order_tactic: str
    maker_required: bool
    time_in_force: str
    limit_price: float | None = None
    price_rule: str
    bbo_reference: str | None = None
    max_wait_sec: int
    cancel_replace_policy: str
    max_requotes: int
    partial_fill_policy: str
    taker_fallback_allowed: bool | None = None
    max_slippage_bps: float | None = None
    worst_price: float | None = None
    max_notional: float
    max_risk_pct: float
    max_leverage: int
    required_permission: str
    execution_account_mode: str
    risk_policy_id: str
    risk_policy_revision: int
    risk_policy_hash: str
    treatment_exposure_id: str | None = None
    runtime_session_id: str | None = None
    producer_commit: str | None = None
    behavior_fingerprint: str | None = None
    schema_fingerprint: str | None = None
    planning_identity: str | None = None
    cycle_number: int | None = None
    analysis_context_fingerprint: str | None = None
    entry_hypothesis_json: str | None = None
    entry_hypothesis_hash: str | None = None
    primary_anchor_snapshot: dict[str, JsonValue] | None = None
    invalidation_anchor_snapshot: dict[str, JsonValue] | None = None
    entry_profile_id: str | None = None
    compiled_geometry_fingerprint: str | None = None
    version: str | None = None
    behavior_version: str | None = None
    analysis_context_version: str | None = None
    entry_hypothesis_version: str | None = None
    entry_profile_version: str | None = None
    trigger_spec_version: str | None = None


class FundingReadiness(TypedDict):
    ready: bool
    reason: str | None
    venue: NotRequired[str]
    symbol: NotRequired[str]
    market_type: NotRequired[str]
    snapshot_id: NotRequired[str]
    source_observed_at: NotRequired[str | None]
    acquired_at: NotRequired[str | None]
    next_funding_time: NotRequired[str]


class EntryLineageOrder(TypedDict):
    id: str | None
    exchange_order_id: str | None
    status: str | None
    quantity: float | None
    position_id: str | None


class EntryLineageFills(TypedDict):
    count: int
    quantity: float
    latest_at: str | None


class EntryLineagePosition(TypedDict):
    id: str | None
    status: str | None
    quantity: float | None
    entry_price: float | None


class EntryPlanLineage(TypedDict):
    order: EntryLineageOrder | None
    fills: EntryLineageFills | None
    position: EntryLineagePosition | None
    availability: dict[str, Any]
    close_fills: list[dict[str, Any]]
    close_orders: list[dict[str, Any]]
    economics: dict[str, Any]
    entry_outcomes: list[dict[str, Any]]
    execution_state: str


class EntryPlanProjection(EntryPlanRow):
    plan: EntryPlanPayload | None
    contract_valid: bool
    funding_readiness: NotRequired[FundingReadiness]
    lineage: NotRequired[EntryPlanLineage]


class UniverseAvailability(TypedDict):
    available: bool
    stale: bool
    reason: NotRequired[str]


class WorkbenchUniverse(TypedDict):
    enabled: bool | None
    status: str
    snapshot: UniverseSnapshot | None
    fallback_instruments: list[str]


class WorkbenchSummary(TypedDict):
    active_entry_plans: int
    needs_planning: int
    expiring_soon: int


class EntryWorkbenchResponse(TypedDict):
    generated_at: str
    availability: dict[str, UniverseAvailability]
    universe: WorkbenchUniverse
    summary: WorkbenchSummary | None
    entry_plans: list[EntryPlanProjection] | None
    current_candidates: list[CandidateWorkbenchRow] | None
    candidate_attention: list[CandidateAttentionWorkbench] | None
    progress: dict[str, Any]


class UniverseCurrentResponse(TypedDict):
    snapshot: UniverseSnapshot | None
    members: list[Candidate]
    enabled: bool
    fallback: bool
    stale: bool
    stale_count: int


class PlanningHistoryResponse(TypedDict):
    items: list[PlanningHistoryItem]
    next_before: str | None


class UniverseSnapshotResponse(TypedDict):
    snapshot: UniverseSnapshot
    members: list[CandidateRow]


class CandidateDetail(TypedDict):
    member: Candidate
    features: UniverseFeatureSnapshot | None
    entry_plans: list[EntryPlanRow]
    opportunities: list[CandidateOpportunity]
    attention: CandidateAttention | None
    lifecycle: EntryLifecycleProjection | None


class UniverseRefreshResponse(TypedDict):
    snapshot_id: str
    status: str


class UniverseRegistryStats(TypedDict):
    total: int
    active: int
    blacklisted: int


class UniverseStoredPolicy(TypedDict):
    id: str
    created_at: str
    created_by: str
    active: int | None
    market_type: str
    family_quota: str
    scoring_weights: str
    filters: str
    ttl_policy: str
    explore_ratio: float
    max_weight_step: float | None
    change_reason: str | None
    previous_policy_id: str | None


class UniverseDefaultPolicy(TypedDict):
    id: None
    source: str
    market_type: str
    family_quota: dict[str, float]
    explore_ratio: float
    max_candidates: int
    min_volume_24h_usdt: float
    max_spread_bps: float


class CalibrationRow(TypedDict):
    scanner_family: str | None
    direction: str
    market_type: str
    horizon: str
    cost_model_version: str
    n: int
    avg_return: float | None
    win_count: int
    min_return: float | None
    max_return: float | None
    source_outcome_fingerprint: str | None
    source_window_start: str
    independent_n: int
    distinct_symbols: int
    distinct_days: int


class CalibrationFamilySummary(TypedDict):
    family: str
    direction: str
    market_type: str
    total_outcomes: int
    hit_rate_pct: float
    avg_return_pct: float


class CalibrationResponse(TypedDict):
    cost_model_version: str
    by_cohort_horizon: list[CalibrationRow]
    by_cohort: list[CalibrationFamilySummary]


from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/universe", tags=["universe"])


def _entry_plan_projection(row: dict) -> dict:
    import hashlib
    import json

    from aletheia.trading.entry_compat import decode_entry_plan

    item = dict(row)
    try:
        payload = str(item["plan_json"])
        plan = decode_entry_plan(payload)
        valid = hashlib.sha256(payload.encode()).hexdigest() == item["plan_hash"]
    except Exception:
        plan = None
        valid = False
    item["plan"] = json.loads(item["plan_json"]) if plan else None
    item["contract_valid"] = valid
    return item


def _entry_plan_projections(status: str = "active", limit: int = 100) -> list[dict]:
    items = [_entry_plan_projection(row) for row in repo.list_entry_plans(status=status, limit=limit)]
    if status == "active" and any(not item["contract_valid"] for item in items):
        raise ValueError("entry_plan_contract_invalid")
    return items


def _active_entry_plan_projections(limit: int) -> list[dict]:
    items = [_entry_plan_projection(row) for row in repo.list_active_entry_plans(limit)]
    if any(not item["contract_valid"] for item in items):
        raise ValueError("entry_plan_contract_invalid")
    return items

def _entry_plan_lineage(row: dict) -> dict:
    return repo.get_entry_plan_execution_lineage([row["id"]])[row["id"]]


@router.get("/workbench", responses={200: {"model": EntryWorkbenchResponse}})
def get_entry_workbench(
    limit: int = Query(default=100, ge=1, le=200),
    plan_status: Annotated[str, Query(pattern="^(active|expired|all)$")] = "active",
):
    """Bounded direct-ledger projection; section failures remain explicit."""
    response = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "availability": {},
        "universe": None,
        "summary": None,
        "entry_plans": None,
        "current_candidates": None,
        "candidate_attention": None,
    }
    active_snapshot_id = None
    universe_read_available = False
    try:
        cfg = load_config()
        snap = repo.get_active_universe_snapshot()
        now = datetime.now(timezone.utc).isoformat()
        stale = bool(snap and snap.get("expires_at") and str(snap["expires_at"]) <= now)
        status = (
            "disabled"
            if not cfg.universe.enabled
            else "no_active_snapshot"
            if snap is None
            else "stale_snapshot"
            if stale
            else "active"
        )
        response["universe"] = {
            "enabled": cfg.universe.enabled,
            "status": status,
            "snapshot": snap,
            "fallback_instruments": (
                list(cfg.universe.fallback_symbols) if status != "active" else []
            ),
        }
        response["availability"]["universe"] = {"available": True, "stale": stale}
        universe_read_available = True
        if status == "active":
            active_snapshot_id = str(snap["id"])
    except Exception as exc:
        response["universe"] = {
            "enabled": None,
            "status": "read_unavailable",
            "snapshot": None,
            "fallback_instruments": [],
        }
        response["availability"]["universe"] = {
            "available": False,
            "stale": False,
            "reason": type(exc).__name__,
        }
    readers = {
        "summary": lambda: repo.get_entry_workbench_summary(),
        "entry_plans": lambda: (
            _active_entry_plan_projections(limit)
            if plan_status == "active"
            else _entry_plan_projections(status=plan_status, limit=limit)
        ),
        "current_candidates": lambda: (
            repo.list_current_candidate_workbench(active_snapshot_id)
            if active_snapshot_id
            else []
            if universe_read_available
            else (_ for _ in ()).throw(RuntimeError("universe_read_unavailable"))
        ),
        "candidate_attention": lambda: repo.list_entry_workbench_candidates(limit),
    }
    for section, read in readers.items():
        try:
            response[section] = read()
            if section in {"current_candidates", "candidate_attention"}:
                for candidate in response[section]:
                    _hide_retired_candidate_scores(candidate)
            response["availability"][section] = {"available": True, "stale": False}
        except Exception as exc:
            response["availability"][section] = {
                "available": False,
                "stale": False,
                "reason": type(exc).__name__,
            }
    from aletheia.agent_runtime.health import get_candidate_path_progress_snapshot

    progress = get_candidate_path_progress_snapshot()
    response["progress"] = progress
    response["availability"]["progress"] = {**progress["availability"], "stale": progress["availability"].get("reason") == "cache_expired"}
    return response


@router.get("/entry-plans/{plan_id}", responses={200: {"model": EntryPlanProjection}})
def get_entry_plan_detail(plan_id: str):
    row = repo.get_entry_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Entry Plan not found")
    projection = _entry_plan_projection(row)
    if not projection["contract_valid"]:
        raise HTTPException(409, "Entry Plan contract validation failed")
    projection["lineage"] = _entry_plan_lineage(projection)
    return projection


@router.get("/planning-history", responses={200: {"model": PlanningHistoryResponse}})
def get_planning_history(
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = None,
):
    cursor = None
    if before:
        try:
            updated_at, generation_id = before.rsplit("|", 1)
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if not generation_id:
                raise ValueError
            cursor = (updated_at, generation_id)
        except ValueError as exc:
            raise HTTPException(422, "Invalid planning history cursor") from exc
    rows = repo.list_planning_history(limit=limit + 1, before=cursor)
    page = rows[:limit]
    next_before = None
    if len(rows) > limit and page:
        next_before = f"{page[-1]['updated_at']}|{page[-1]['id']}"
    return {"items": page, "next_before": next_before}


def _attach_candidate_handoff_state(members: list[dict]) -> list[dict]:
    member_ids = [str(member.get("id") or "") for member in members if member.get("id")]
    if not member_ids:
        return members
    placeholders = ",".join("?" for _ in member_ids)
    from aletheia.storage.sqlite import get_db

    plans_by_candidate = {}
    for candidate_id in member_ids:
        generation = repo.get_candidate_planning_for_candidate(candidate_id)
        if generation and generation.get("is_current") and generation.get("entry_plan_id"):
            plans_by_candidate[candidate_id] = repo.get_entry_plan(generation["entry_plan_id"])
    # Historical wire projection is not runtime authority.
    legacy_rows = get_db().execute(
        "SELECT us.member_id, us.state, us.opportunity_id "
        "FROM universe_setups us "
        "JOIN ("
        " SELECT member_id, MAX(datetime(created_at)) AS latest_created_at "
        " FROM universe_setups "
        f" WHERE member_id IN ({placeholders}) GROUP BY member_id"
        ") latest ON latest.member_id=us.member_id "
        "AND latest.latest_created_at=datetime(us.created_at)",
        tuple(member_ids),
    ).fetchall()
    legacy_by_member = {str(row["member_id"]): dict(row) for row in legacy_rows}
    enriched = []
    for member in members:
        item = dict(member)
        _hide_retired_candidate_scores(item)
        candidate_id = str(item.get("id") or "")
        plan = plans_by_candidate.get(candidate_id)
        item["entry_plan_id"] = plan.get("id") if plan else None
        item["entry_plan_status"] = plan.get("status") if plan else None
        item["entry_plan_execution_mode"] = plan.get("execution_mode") if plan else None
        setup = legacy_by_member.get(candidate_id)
        item["setup_state"] = setup.get("state") if setup else None
        item["triggered_opportunity_id"] = setup.get("opportunity_id") if setup else None
        enriched.append(item)
    return enriched


@router.get("/current", responses={200: {"model": UniverseCurrentResponse}})
def get_current_universe():
    """Return the current active universe snapshot and its Candidates."""
    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    cfg = load_config()

    # UniverseService owns the published snapshot namespace.  The configured
    # market_type controls which assets are scanned (crypto/stocks/mixed), while
    # snapshots are currently published under the service's canonical default.
    # Querying with cfg.universe.market_type here can otherwise return no
    # snapshot while the service returns members from the active one.
    snap = repo.get_active_universe_snapshot()
    # execution_eligible_only=False: UI sees stale members (tagged stale=True)
    # rather than falling back to the static 3-symbol pool.
    members = _attach_candidate_handoff_state(
        svc.get_current_universe(execution_eligible_only=False)
    )

    stale_count = sum(1 for m in members if m.get("stale"))
    return {
        "snapshot": snap,
        "members": members,  # compatibility alias: these rows are Candidates.
        "enabled": cfg.universe.enabled,
        "fallback": snap is None,
        "stale": stale_count > 0,
        "stale_count": stale_count,
    }


@router.get("/snapshots", responses={200: {"model": list[UniverseSnapshot]}})
def list_snapshots(market_type: str = "crypto", limit: int = 20):
    """List recent universe snapshots."""
    return repo.list_universe_snapshots(market_type, limit=limit)


@router.get("/snapshots/{snapshot_id}", responses={200: {"model": UniverseSnapshotResponse}})
def get_snapshot(snapshot_id: str):
    """Get a specific snapshot with its Candidates."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute("SELECT * FROM universe_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot not found")
    members = repo.list_universe_members(snapshot_id, limit=50)
    for member in members:
        _hide_retired_candidate_scores(member)
    return {"snapshot": dict(row), "members": members}


@router.get("/members/{member_id}")
def get_member(member_id: str):
    """Get a Candidate with its current Entry Plan and compatibility history."""
    member = repo.get_universe_member(member_id)
    if not member:
        raise HTTPException(404, "Member not found")
    features = repo.get_latest_feature_snapshot(member["asset_id"])
    opportunities = repo.list_opportunities_by_member(member_id, limit=5)
    [member] = _attach_candidate_handoff_state([member])
    entry_plan = repo.get_entry_plan(member["entry_plan_id"]) if member.get("entry_plan_id") else None
    return {
        "member": member,
        "features": features,
        "entry_plans": [entry_plan] if entry_plan else [],
        "opportunities": opportunities,  # Backward-compatible historical read model.
    }


@router.get("/candidates/{candidate_id}", responses={200: {"model": CandidateDetail}})
def get_candidate(candidate_id: str):
    """Canonical Candidate detail alias; /members remains compatibility."""

    detail = get_member(candidate_id)
    attention = repo.get_candidate_planning_for_candidate(candidate_id)
    if attention is not None:
        attention.update({field: detail["member"].get(field) for field in (
            "admission_basis", "startup_reason", "pessimistic_strength",
        )})
    detail["attention"] = attention
    try:
        detail["lifecycle"] = repo.get_candidate_entry_lifecycle(candidate_id)
    except ValueError:
        detail["lifecycle"] = None
    return detail


@router.post("/refresh", responses={200: {"model": UniverseRefreshResponse}})
async def trigger_refresh():
    """Manually trigger a universe refresh. Returns immediately with the snapshot_id."""
    cfg = load_config()
    if not cfg.universe.enabled:
        raise HTTPException(400, detail="Universe generator is disabled in config")

    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    snapshot_id = await svc.refresh()
    if snapshot_id is None:
        raise HTTPException(500, detail="Universe refresh failed — check logs")
    return {"snapshot_id": snapshot_id, "status": "completed"}


@router.get("/policy", responses={200: {"model": UniverseStoredPolicy | UniverseDefaultPolicy}})
def get_current_policy():
    """Return the current active universe policy."""
    cfg = load_config()
    policy = repo.get_active_universe_policy(cfg.universe.market_type)
    if policy:
        return policy
    # Return the config-derived defaults as a "virtual" policy
    return {
        "id": None,
        "source": "config_defaults",
        "market_type": cfg.universe.market_type,
        "family_quota": cfg.universe.family_quota,
        "explore_ratio": cfg.universe.explore_ratio,
        "max_candidates": cfg.universe.max_candidates,
        "min_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
        "max_spread_bps": cfg.universe.max_spread_bps,
    }


@router.get("/calibration", responses={200: {"model": CalibrationResponse}})
def get_calibration():
    """Net calibration: family/direction/market/horizon aggregate stats.

    Answers: "do the scanner families actually pick winners?"
    """
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    stats = repo.calibration_stats(cost_model_version=COST_MODEL_VERSION)

    cohort_agg: dict[tuple[str, str, str], dict] = {}
    for row in stats:
        fam = row["scanner_family"] or "unknown"
        direction = row.get("direction") or "unknown"
        market_type = row.get("market_type") or "spot"
        key = (fam, direction, market_type)
        if key not in cohort_agg:
            cohort_agg[key] = {
                "family": fam,
                "direction": direction,
                "market_type": market_type,
                "total_n": 0,
                "total_wins": 0,
                "weighted_return": 0.0,
            }
        cohort_agg[key]["total_n"] += row["n"]
        cohort_agg[key]["total_wins"] += row["win_count"]
        cohort_agg[key]["weighted_return"] += (row["avg_return"] or 0) * row["n"]

    summaries = []
    for _, agg in sorted(cohort_agg.items()):
        n = agg["total_n"]
        summaries.append({
            "family": agg["family"],
            "direction": agg["direction"],
            "market_type": agg["market_type"],
            "total_outcomes": n,
            "hit_rate_pct": round(agg["total_wins"] / n * 100, 1) if n > 0 else 0,
            "avg_return_pct": round(agg["weighted_return"] / n, 3) if n > 0 else 0,
        })

    return {
        "cost_model_version": COST_MODEL_VERSION,
        "by_cohort_horizon": stats,
        "by_cohort": summaries,
    }


@router.get("/members/{member_id}/outcomes")
def get_member_outcomes(member_id: str):
    """Forward outcomes for a specific universe member."""
    outcomes = repo.list_forward_outcomes_by_member(member_id)
    if not outcomes:
        raise HTTPException(404, "No outcomes found for this member")
    return outcomes


@router.get("/registry/stats", responses={200: {"model": UniverseRegistryStats}})
def registry_stats():
    """Asset registry statistics."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM universe_registry").fetchone()
    active = db.execute(
        "SELECT COUNT(*) as c FROM universe_registry WHERE status = 'active' AND is_blacklisted = 0"
    ).fetchone()
    blacklisted = db.execute(
        "SELECT COUNT(*) as c FROM universe_registry WHERE is_blacklisted = 1"
    ).fetchone()
    return {
        "total": total["c"] if total else 0,
        "active": active["c"] if active else 0,
        "blacklisted": blacklisted["c"] if blacklisted else 0,
    }
