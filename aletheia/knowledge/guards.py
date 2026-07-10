"""Deterministic Trading Guard Engine.

Guards are data (YAML frontmatter in markdown files), not code.
The engine evaluates guards against a context dict using only whitelisted fields.
No LLM, no arbitrary code execution, no live/risk modification.
"""

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aletheia.config.loader import get_data_dir, load_config
from aletheia.knowledge.frontmatter import parse_frontmatter

logger = logging.getLogger("aletheia.knowledge.guards")

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
    applies_to: GuardAppliesTo = Field(default_factory=GuardAppliesTo)
    predicates: list[GuardPredicate] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


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


def load_trading_guards(
    scope: str | None = None,
    status: str = "active",
) -> list[TradingGuard]:
    """Load trading guards from markdown files.

    Scans BOTH knowledge/guards/ (active/promoted) and knowledge/guard_candidates/
    (auto-generated candidates) so the promotion check — which calls this with
    status="candidate" — can actually reach the堆积的 candidates. Reading only
    guards/ was why 0 candidates were ever promoted (L-Bug-D/B2).
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
                    applies_to=GuardAppliesTo(**applies_to) if isinstance(applies_to, dict) else GuardAppliesTo(),
                    predicates=predicates,
                    evidence_refs=meta.get("evidence_refs") or [],
                    tags=meta.get("tags") or [],
                )
                guards.append(guard)
            except Exception as e:
                logger.warning("Failed to load guard from %s: %s", path, e)
                continue

    return guards


def evaluate_guards(
    context: dict,
    *,
    scope: str = "paper_entry",
    guards: list[TradingGuard] | None = None,
) -> GuardEvaluation:
    """Evaluate guards against a context dict. Deterministic, no LLM."""
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

        reason = f"{guard.title}: predicates matched"
        hit = GuardHit(
            guard_id=guard.guard_id,
            title=guard.title,
            enforcement=guard.enforcement,
            severity=guard.severity,
            score_penalty=guard.score_penalty,
            block_candidate=guard.block_candidate,
            reason=reason,
        )
        hits.append(hit)

        if guard.block_candidate:
            blocked = True
        total_penalty += guard.score_penalty

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
