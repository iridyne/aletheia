"""Deterministic Trading Guard Engine.

Guards are data (YAML frontmatter in markdown files), not code.
The engine evaluates guards against a context dict using only whitelisted fields.
No LLM, no arbitrary code execution, no live/risk modification.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aletheia.config.loader import get_data_dir, load_config
from aletheia.knowledge.frontmatter import parse_frontmatter

logger = logging.getLogger("aletheia.knowledge.guards")

# Default TTL for automatically learned guards (days). Owner-authored seed/manual
# guards are persistent and never subject to this window (#195).
LEARNED_GUARD_TTL_DAYS = 30

# Legacy learned guards written before this fix lack explicit expires_at.  They
# receive a bounded migration deadline rather than remaining immortal.  After this
# date the loader excludes them and the daily job soft-deprecates them (#195).
LEARNED_GUARD_MIGRATION_DEADLINE_ISO = "2026-10-22T00:00:00+00:00"

GuardScope = Literal["strategy_design", "market_read", "opportunity", "paper_entry", "exit_policy"]
GuardEnforcement = Literal["advisory", "scoring", "blocking"]
GuardSeverity = Literal["info", "warning", "critical"]


class GuardPredicate(BaseModel):
    field: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"]
    value: Any


class GuardAppliesTo(BaseModel):
    asset_class: list[str] | None = None
    scanner_family: list[str] | None = None
    symbol: list[str] | None = None
    direction: list[str] | None = None
    market_type: list[str] | None = None
    confidence_bucket: list[str] | None = None
    late_entry_bucket: list[str] | None = None


class TradingGuard(BaseModel):
    guard_id: str
    title: str
    description: str = ""
    status: Literal["candidate", "active", "deprecated"] = "candidate"
    scope: GuardScope = "paper_entry"
    enforcement: GuardEnforcement = "advisory"
    severity: GuardSeverity = "info"
    score_penalty: float = 0.0
    block_candidate: bool = False
    # Automatic guards are persisted as observe-only, but their proposed effect
    # must remain available for a later prospective treatment.  Keeping the
    # proposal separate from the current effect prevents shadow storage from
    # silently destroying the behavior that is actually being evaluated.
    proposed_score_penalty: float | None = None
    proposed_block_candidate: bool | None = None
    # Compatibility with the reversible downgrade written by guard_promotion.
    pre_shadow_score_penalty: float | None = None
    pre_shadow_block_candidate: bool | None = None
    # Observe-only marker. A shadow guard is evaluated and its hit is recorded for
    # audit, but it can neither block nor penalize (P0 2026-07-12). All auto-generated
    # guards are created shadow-only; the enforcement seam in evaluate_guards refuses
    # to honor block_candidate/score_penalty for shadow/learned guards until an
    # experiment grants authority. Previously dropped on load → enforcement layer was
    # blind to shadow status.
    shadow_only: bool = False
    applies_to: GuardAppliesTo = Field(default_factory=GuardAppliesTo)
    predicates: list[GuardPredicate] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # ISO timestamp — set at creation for auto-generated learned guards. None for
    # seed/manual guards (persistent). Used by the loader and promotion check (#195).
    created_at: str | None = None
    expires_at: str | None = None


class GuardHit(BaseModel):
    guard_id: str
    title: str
    enforcement: GuardEnforcement
    severity: GuardSeverity
    score_penalty: float = 0.0
    block_candidate: bool = False
    reason: str


class GuardEvaluation(BaseModel):
    blocked: bool = False
    total_penalty: float = 0.0
    hits: list[GuardHit] = Field(default_factory=list)


ALLOWED_GUARD_FIELDS = {
    "symbol",
    "asset_class",
    "direction",
    "scanner_family",
    "market_type",
    "confidence",
    "confidence_score",
    "confidence_bucket",
    "late_entry_penalty",
    "late_entry_bucket",
    "tradability_score",
    "universe_total_score",
    "crowding_risk",
    "risk_flags",
    "factor_status",
    "factor_expectancy",
    "factor_trade_count",
    "strategy_has_ruleset",
    "ruleset_entry_result",
    "strategy_drift_status",
    "paper_expectancy",
    "backtest_expectancy",
}


def _guards_dir() -> Path:
    return get_data_dir() / "knowledge" / "guards"


def _guard_candidates_dir() -> Path:
    return get_data_dir() / "knowledge" / "guard_candidates"


def _applies_to_from_meta(meta: dict) -> dict:
    """Build a GuardAppliesTo dict from a guard's metadata.

    Active/promoted guards carry an explicit nested ``applies_to``. Auto-generated
    candidates (outcome_recorder) instead carry FLAT scanner_family / direction
    keys; bridge those into applies_to so the promoter can match the loss pattern
    (without this the candidate matched no trades → never promoted — L-Bug-D/B3).
    """
    applies_to = dict(meta.get("applies_to") or {})
    if not applies_to:
        symbol = meta.get("symbol")
        fam = meta.get("scanner_family")
        direction = meta.get("direction")
        market_type = meta.get("market_type")
        if symbol:
            applies_to["symbol"] = [symbol] if isinstance(symbol, str) else symbol
        if fam:
            applies_to["scanner_family"] = [fam] if isinstance(fam, str) else fam
        if direction:
            applies_to["direction"] = [direction] if isinstance(direction, str) else direction
        if market_type:
            applies_to["market_type"] = [market_type] if isinstance(market_type, str) else market_type
    return applies_to


def _effective_learned_expires_at(meta: dict) -> str:
    """Return the effective ISO expiry for a learned guard metadata dict.

    Order of precedence (#195):
    1. explicit ``expires_at`` in the metadata
    2. ``created_at + LEARNED_GUARD_TTL_DAYS`` when created_at is present
    3. ``LEARNED_GUARD_MIGRATION_DEADLINE_ISO`` (bounded migration for legacy items)
    """
    if meta.get("expires_at"):
        return str(meta["expires_at"])
    created_at_raw = meta.get("created_at")
    if created_at_raw:
        try:
            created_dt = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
            return (created_dt + timedelta(days=LEARNED_GUARD_TTL_DAYS)).isoformat()
        except (ValueError, TypeError):
            pass
    return LEARNED_GUARD_MIGRATION_DEADLINE_ISO


def _learned_guard_is_expired(meta: dict) -> bool:
    """Return True if a learned guard has passed its effective expiry."""
    effective = _effective_learned_expires_at(meta)
    try:
        exp = datetime.fromisoformat(effective.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= exp
    except (ValueError, TypeError):
        return False


def load_trading_guards(
    scope: str | None = None,
    status: str = "active",
) -> list[TradingGuard]:
    """Load trading guards from markdown files.

    Scans BOTH knowledge/guards/ (active/promoted) and knowledge/guard_candidates/
    (auto-generated candidates) so the promotion check — which calls this with
    status="candidate" — can actually reach the堆积的 candidates. Reading only
    guards/ was why 0 candidates were ever promoted (L-Bug-D/B2).

    Expired learned guards are excluded at load time even if the scheduled cleanup
    has not yet run (#195). Seed and manual guards are never excluded this way.
    """
    guards: list[TradingGuard] = []
    for guards_dir in (_guards_dir(), _guard_candidates_dir()):
        if not guards_dir.exists():
            continue
        for path in sorted(guards_dir.glob("*.md")):
            try:
                meta, _ = parse_frontmatter(path)
                if not meta:
                    continue
                if meta.get("status") != status:
                    continue
                if scope and meta.get("scope") != scope:
                    continue
                # Expired learned guards are invisible at load time (#195).
                # Seed/manual guards are persistent and are never excluded here.
                if guard_origin(meta) == "learned" and _learned_guard_is_expired(meta):
                    continue

                applies_to = _applies_to_from_meta(meta)
                predicates_raw = meta.get("predicates") or []
                predicates = [GuardPredicate(**p) for p in predicates_raw if isinstance(p, dict)]

                guard = TradingGuard(
                    guard_id=meta.get("guard_id", path.stem),
                    title=meta.get("title", path.stem),
                    description=meta.get("description", ""),
                    status=meta.get("status", "candidate"),
                    scope=meta.get("scope", "paper_entry"),
                    enforcement=meta.get("enforcement", "advisory"),
                    severity=meta.get("severity", "info"),
                    score_penalty=float(meta.get("score_penalty", 0)),
                    block_candidate=bool(meta.get("block_candidate", False)),
                    proposed_score_penalty=(
                        float(meta["proposed_score_penalty"])
                        if meta.get("proposed_score_penalty") is not None else None
                    ),
                    proposed_block_candidate=(
                        bool(meta["proposed_block_candidate"])
                        if meta.get("proposed_block_candidate") is not None else None
                    ),
                    pre_shadow_score_penalty=(
                        float(meta["pre_shadow_score_penalty"])
                        if meta.get("pre_shadow_score_penalty") is not None else None
                    ),
                    pre_shadow_block_candidate=(
                        bool(meta["pre_shadow_block_candidate"])
                        if meta.get("pre_shadow_block_candidate") is not None else None
                    ),
                    shadow_only=bool(meta.get("shadow_only", False)),
                    applies_to=GuardAppliesTo(**applies_to) if isinstance(applies_to, dict) else GuardAppliesTo(),
                    predicates=predicates,
                    evidence_refs=meta.get("evidence_refs") or [],
                    tags=meta.get("tags") or [],
                    created_at=meta.get("created_at"),
                    expires_at=meta.get("expires_at"),
                )
                guards.append(guard)
            except Exception as e:
                logger.warning("Failed to load guard from %s: %s", path, e)
                continue

    return guards


# Tags any auto-generation source stamps onto a learned guard.
_LEARNED_GUARD_TAGS = {"auto-generated", "auto_generated", "shadow-only"}


def _seed_guard_ids() -> frozenset[str]:
    """guard_ids of the built-in safety guards (lazy import to avoid a cycle)."""
    try:
        from aletheia.knowledge.seed_guards import seed_guard_ids
        return seed_guard_ids()
    except Exception:  # pragma: no cover - seed module optional at import time
        return frozenset()


def guard_origin(guard: "TradingGuard | dict") -> str:
    """Classify a guard's provenance: ``seed`` | ``manual`` | ``learned``.

    - ``seed``    — a built-in safety guard shipped in seed_guards.py.
    - ``learned`` — auto-generated by the trade/loss learning loop (``auto_*`` id,
      the ``auto_generated`` flag, or an auto/shadow tag).
    - ``manual``  — everything else (owner hand-authored).

    Safety-type seed/manual guards and learned guards must NEVER be processed by
    the same automatic promotion/deprecation path, and learned guards have no
    block/penalty authority until an experiment grants it (P0 2026-07-12).
    """
    if isinstance(guard, dict):
        guard_id = str(guard.get("guard_id") or "")
        tags = guard.get("tags") or []
        auto_flag = bool(guard.get("auto_generated"))
    else:
        guard_id = guard.guard_id or ""
        tags = guard.tags or []
        auto_flag = False
    if guard_id in _seed_guard_ids():
        return "seed"
    if auto_flag or guard_id.startswith("auto_") or (_LEARNED_GUARD_TAGS & set(tags)):
        return "learned"
    return "manual"


def _learned_guard_proposed_effect(guard: TradingGuard) -> tuple[bool, float]:
    """Return the effect proposed by a learned Guard, never an invented default."""
    proposed_block = guard.proposed_block_candidate
    if proposed_block is None:
        proposed_block = guard.pre_shadow_block_candidate
    if proposed_block is None:
        proposed_block = guard.block_candidate

    proposed_penalty = guard.proposed_score_penalty
    if proposed_penalty is None:
        proposed_penalty = guard.pre_shadow_score_penalty
    if proposed_penalty is None:
        proposed_penalty = guard.score_penalty
    return bool(proposed_block), max(0.0, float(proposed_penalty or 0.0))


def learned_guard_block_authorized(
    guard: TradingGuard,
    context: dict | None = None,
) -> bool:
    """Return per-candidate authority for one learned Guard treatment.

    The static baseline is observe-only (0).  Every real candidate is assigned
    through the existing prospective Learning experiment path (treatment=1),
    which records the candidate, outcome and Opportunity identities.  Missing
    identity, control assignment, disabled/unallowlisted influence, stale
    evidence, or any adapter failure all retain the observe-only baseline.
    """
    context = context or {}
    proposed_block, proposed_penalty = _learned_guard_proposed_effect(guard)
    if not proposed_block and proposed_penalty <= 0:
        return False
    try:
        from aletheia.learning.experiments import apply_numeric_treatment

        decision = apply_numeric_treatment(
            path_id="ranker_learned_guard",
            scope={"guard_id": guard.guard_id},
            action={
                "type": "candidate_guard",
                "guard_id": guard.guard_id,
                "block_candidate": proposed_block,
                "score_penalty": proposed_penalty,
            },
            source_cohort=f"learned_guard:{guard.guard_id}:v1",
            target_horizon="4h",
            candidate_key=context.get("_candidate_key"),
            baseline=0.0,
            treatment=1.0,
            consumer="paper_trader_ranker",
            consumer_stage="guard_filter",
            outcome_candidate_key=context.get("_outcome_candidate_key"),
            opportunity_id=context.get("_opportunity_id"),
            manifest={
                "guard_id": guard.guard_id,
                "proposed_block_candidate": proposed_block,
                "proposed_score_penalty": proposed_penalty,
            },
        )
        return bool(decision.applied and float(decision.value) == 1.0)
    except Exception as exc:
        logger.warning(
            "Learned Guard authority failed closed for %s: %s",
            guard.guard_id,
            type(exc).__name__,
        )
        return False


def _guard_influence_suppressed(guard: TradingGuard, context: dict) -> bool:
    """A learned/shadow guard observes only — it can neither block nor penalize
    until authorized. Seed and manual safety guards are never suppressed."""
    from aletheia.trust.behavior_authority import authorize_candidate_block

    origin = guard_origin(guard)
    source = {
        "seed": "seed_guard",
        "manual": "manual_guard",
        "learned": "learned_guard",
    }[origin]
    prospective_authority = (
        learned_guard_block_authorized(guard, context) if origin == "learned" else False
    )
    authorized = authorize_candidate_block(
        source=source,
        prospective_authority=prospective_authority,
        learning_path_id=("ranker_learned_guard" if origin == "learned" else None),
    )
    # shadow_only is the learned Guard's static baseline, not a permanent veto:
    # an exact authorized treatment may apply its separately preserved proposal.
    # Owner-authored manual shadow Guards, however, always remain observe-only.
    return (guard.shadow_only and origin != "learned") or not authorized


def evaluate_guards(
    context: dict,
    *,
    scope: str = "paper_entry",
    guards: list[TradingGuard] | None = None,
) -> GuardEvaluation:
    """Evaluate guards against a context dict. Deterministic, no LLM.

    Learned/shadow guards are observed (their hit is recorded for audit) but never
    contribute a block or a penalty until an experiment grants authority; seed and
    manual safety guards enforce as authored.
    """
    if guards is None:
        try:
            cfg = load_config()
            guard_cfg = getattr(cfg, "guard", None)
            if guard_cfg and not guard_cfg.enabled:
                return GuardEvaluation()
        except Exception:
            pass
        guards = load_trading_guards(scope=scope, status="active")

    hits: list[GuardHit] = []
    blocked = False
    total_penalty = 0.0

    for guard in guards:
        if not _applies_to_context(guard, context):
            continue
        if not _predicates_match(guard.predicates, context):
            continue

        origin = guard_origin(guard)
        proposed_block, proposed_penalty = (
            _learned_guard_proposed_effect(guard)
            if origin == "learned"
            else (guard.block_candidate, guard.score_penalty)
        )
        suppressed = _guard_influence_suppressed(guard, context)
        effective_block = proposed_block and not suppressed
        effective_penalty = 0.0 if suppressed else proposed_penalty
        reason = f"{guard.title}: predicates matched"
        if suppressed:
            reason += " (shadow: observed only — no block/penalty authority)"
        hit = GuardHit(
            guard_id=guard.guard_id,
            title=guard.title,
            enforcement=guard.enforcement,
            severity=guard.severity,
            score_penalty=effective_penalty,
            block_candidate=effective_block,
            reason=reason,
        )
        hits.append(hit)

        if effective_block:
            blocked = True
        total_penalty += effective_penalty

    return GuardEvaluation(blocked=blocked, total_penalty=total_penalty, hits=hits)


def format_guard_block(eval_result: GuardEvaluation, max_chars: int = 1200) -> str:
    """Format guard evaluation for inclusion in proposal rationale."""
    if not eval_result.hits:
        return ""
    lines = []
    for h in eval_result.hits:
        action = "BLOCKED" if h.block_candidate else f"penalty={h.score_penalty}"
        lines.append(f"  [{h.severity}] {h.guard_id}: {action}")
    text = "Guard hits:\n" + "\n".join(lines)
    return text[:max_chars]


def _applies_to_context(guard: TradingGuard, context: dict) -> bool:
    """Check if guard's applies_to filters match the context.

    Returns False when a required field is None in context (guard cannot
    meaningfully evaluate without the data it depends on).
    """
    at = guard.applies_to
    if at.asset_class:
        val = context.get("asset_class")
        if val is None or val not in at.asset_class:
            return False
    if at.scanner_family:
        val = context.get("scanner_family")
        if val is None or val not in at.scanner_family:
            return False
    if at.symbol:
        val = context.get("symbol")
        if val is None or val not in at.symbol:
            return False
    if at.direction:
        val = context.get("direction")
        if val is None or val not in at.direction:
            return False
    if at.market_type:
        val = _normalize_market_type(context.get("market_type"))
        allowed = {_normalize_market_type(item) for item in at.market_type}
        if val is None or val not in allowed:
            return False
    if at.confidence_bucket:
        val = context.get("confidence_bucket")
        if val is None or val not in at.confidence_bucket:
            return False
    if at.late_entry_bucket:
        val = context.get("late_entry_bucket")
        if val is None or val not in at.late_entry_bucket:
            return False
    return True


def _normalize_market_type(value: Any) -> str | None:
    raw = str(value or "").lower()
    if not raw:
        return None
    if raw in {"perp", "futures", "swap", "usdt_futures"}:
        return "perp"
    return "spot" if raw == "spot" else raw


def _predicates_match(predicates: list[GuardPredicate], context: dict) -> bool:
    """All predicates must match (AND logic). Unknown fields → no match."""
    if not predicates:
        return False
    for pred in predicates:
        if pred.field not in ALLOWED_GUARD_FIELDS:
            logger.debug("Guard predicate uses disallowed field: %s", pred.field)
            return False
        ctx_val = context.get(pred.field)
        if not _eval_predicate(pred.op, ctx_val, pred.value):
            return False
    return True


def _eval_predicate(op: str, ctx_val: Any, target: Any) -> bool:
    """Evaluate a single predicate. Returns False on type errors rather than raising."""
    if ctx_val is None:
        return False
    try:
        if op == "eq":
            return ctx_val == target
        elif op == "ne":
            return ctx_val != target
        elif op == "gt":
            return float(ctx_val) > float(target)
        elif op == "gte":
            return float(ctx_val) >= float(target)
        elif op == "lt":
            return float(ctx_val) < float(target)
        elif op == "lte":
            return float(ctx_val) <= float(target)
        elif op == "in":
            if isinstance(target, list):
                return ctx_val in target
            return False
        elif op == "contains":
            if isinstance(ctx_val, list):
                return target in ctx_val
            if isinstance(ctx_val, str):
                return target in ctx_val
            return False
        return False
    except (TypeError, ValueError):
        return False
