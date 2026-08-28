"""Momentum setup construction, deterministic trigger state, and T0 materialization."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.data.market_structure import (
    COMPAT_ALGORITHM_VERSION,
    CORE_ALGORITHM_VERSION,
)
from aletheia.data.symbols import InstrumentId
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    ConditionalEntryAuthorization,
    FastTriggerMarketSnapshot,
    Opportunity,
    OpportunityEntryValidityAssessment,
    OpportunityGenerationV2,
    OpportunityInstrumentIdentity,
    PiDecision,
    TradeProposal,
    UniverseSetup,
)
from aletheia.trading.instruments import instrument_market_to_db_market
from aletheia.universe.fast_stream import (
    FastMarketEvent,
    fast_market_data_venue_for_config,
    fast_market_stream_available_for_config,
)


TRIGGER_SPEC_VERSION = "momentum-retrace-reclaim/v1"
TRIGGER_BEHAVIOR_VERSION = "momentum-fast-trigger/v1"
TARGET_RISK_REWARD = 2.0
_TRIGGER_SPEC = {
    "version": TRIGGER_SPEC_VERSION,
    "bars": 21,
    "timeframe": "15m",
    "breakout_window": 20,
    "pullback_atr": 0.25,
    "invalidation_atr": 1.0,
    "reclaim_quotes": 2,
    "reclaim_quote_spacing_ms": 300,
    "trade_window_ms": 10_000,
    "long_taker_buy_ratio": 0.60,
    "short_taker_buy_ratio": 0.40,
}
TRIGGER_SPEC_HASH = hashlib.sha256(
    json.dumps(_TRIGGER_SPEC, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _canonical_evidence(evidence: dict) -> tuple[str, str]:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validity_assessment(
    *,
    opportunity_id: str,
    setup: UniverseSetup,
    check_type: Literal["entry_zone", "structural_price", "market_structure"],
    authority: Literal["hard", "shadow"],
    verdict: Literal["allow", "block", "unavailable"],
    observed_at: str,
    algorithm_version: str,
    evidence: dict,
) -> OpportunityEntryValidityAssessment:
    payload, digest = _canonical_evidence(evidence)
    return OpportunityEntryValidityAssessment(
        opportunity_id=opportunity_id,
        setup_id=setup.id,
        venue=setup.venue,
        symbol=setup.symbol,
        market_type=setup.market_type,
        check_type=check_type,
        authority=authority,
        verdict=verdict,
        observed_at=observed_at,
        algorithm_version=algorithm_version,
        evidence_json=payload,
        evidence_hash=digest,
    )


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("fast trigger timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored fast trigger timestamp is timezone-naive")
    return parsed.astimezone(timezone.utc)


def _zone(raw: str) -> tuple[float, float]:
    try:
        values = json.loads(raw)
        low, high = float(values[0]), float(values[1])
    except (TypeError, ValueError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("invalid fast trigger price zone") from exc
    if not all(math.isfinite(value) and value > 0 for value in (low, high)) or low > high:
        raise ValueError("invalid fast trigger price zone")
    return low, high


def build_momentum_setup(
    *,
    snapshot_id: str,
    member_id: str,
    asset_id: str,
    venue: str,
    symbol: str,
    market_type: str,
    direction: Literal["long", "short"],
    regime_tag: str | None,
    rank: int,
    material_fingerprint: str,
    atr_15m_pct: float,
    candles: list[dict],
    motive_evidence: dict | None = None,
    funding_rate: float | None = None,
    funding_observed_at: str | None = None,
    now: datetime | None = None,
    ttl_minutes: int = 60,
) -> UniverseSetup:
    """Build v1 only when the final completed candle confirms the breakout."""

    identity = (snapshot_id, member_id, asset_id, venue, symbol, market_type)
    if not all(str(value).strip() for value in identity):
        raise ValueError("momentum_setup_instrument_identity_required")
    try:
        instrument = InstrumentId.from_parts(venue, symbol, market_type)
    except ValueError as exc:
        raise ValueError("momentum_setup_invalid_instrument_identity") from exc
    venue, symbol, market_type = (
        instrument.exchange,
        instrument.symbol,
        instrument.market_type,
    )
    if direction == "short" and market_type == "spot":
        raise ValueError("momentum_setup_spot_short_forbidden")
    if not 1 <= int(ttl_minutes) <= 60:
        raise ValueError("momentum_setup_ttl_out_of_range")
    if len(candles) < 21:
        raise ValueError("momentum_setup_requires_21_closed_15m_bars")
    rows = candles[-21:]
    try:
        timestamps = [int(row["ts"]) for row in rows]
        highs = [float(row["high"]) for row in rows]
        lows = [float(row["low"]) for row in rows]
        last_close = float(rows[-1]["close"])
        atr_pct = float(atr_15m_pct)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("momentum_setup_invalid_candle") from exc
    numeric = (*highs, *lows, last_close, atr_pct)
    if (
        any(not math.isfinite(value) or value <= 0 for value in numeric)
        or timestamps != sorted(timestamps)
        or len(set(timestamps)) != len(timestamps)
    ):
        raise ValueError("momentum_setup_invalid_candle")
    level = max(highs[:20]) if direction == "long" else min(lows[:20])
    if (direction == "long" and last_close <= level) or (
        direction == "short" and last_close >= level
    ):
        raise ValueError("momentum_setup_breakout_not_confirmed")
    atr = last_close * atr_pct / 100.0
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("momentum_setup_invalid_atr")
    quarter = atr * 0.25
    pullback_band = (level - quarter, level + quarter)
    entry_zone = (
        (level, level + quarter)
        if direction == "long"
        else (level - quarter, level)
    )
    invalidation = level - atr if direction == "long" else level + atr
    material_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "candidate_fingerprint": material_fingerprint,
                "instrument": [venue, symbol, market_type],
                "direction": direction,
                "regime": regime_tag,
                "trigger_spec_hash": TRIGGER_SPEC_HASH,
                "breakout_level": level,
                "atr": atr,
                "entry_zone": entry_zone,
                "invalidation_price": invalidation,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    current = _utc(now)
    return UniverseSetup(
        snapshot_id=snapshot_id,
        member_id=member_id,
        asset_id=asset_id,
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        rank=rank,
        material_fingerprint=material_fingerprint,
        trigger_spec_hash=TRIGGER_SPEC_HASH,
        scanner_family="momentum",
        motive_evidence=json.dumps(
            motive_evidence or {}, sort_keys=True, separators=(",", ":")
        ),
        funding_rate=funding_rate,
        funding_observed_at=funding_observed_at if funding_rate is not None else None,
        regime_tag=regime_tag,
        breakout_level=level,
        atr=atr,
        pullback_band=json.dumps(pullback_band, separators=(",", ":")),
        entry_zone=json.dumps(entry_zone, separators=(",", ":")),
        invalidation_price=invalidation,
        created_at=current.isoformat(),
        updated_at=current.isoformat(),
        valid_until=(current + timedelta(minutes=ttl_minutes)).isoformat(),
    )


def persist_momentum_setup(setup: UniverseSetup) -> dict:
    """Invalidate older material generations and idempotently own the new one."""

    with repo.transaction(immediate=True):
        db = repo.get_db()
        db.execute(
            "UPDATE universe_setups SET state='invalidated', updated_at=? "
            "WHERE venue=? AND symbol=? AND market_type=? AND direction=? "
            "AND material_fingerprint<>? "
            "AND state IN ('pending_authorization','armed','pullback_seen')",
            (
                datetime.now(timezone.utc).isoformat(),
                setup.venue,
                setup.symbol,
                setup.market_type,
                setup.direction,
                setup.material_fingerprint,
            ),
        )
        return repo.insert_universe_setup(setup)


class TriggerPolicy(BaseModel):
    max_spread_bps: float = Field(gt=0)
    min_depth_10bps_usd: float = Field(gt=0)


class TriggerSnapshot(BaseModel):
    observed_at: str
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_bps: float = Field(ge=0)
    depth_10bps_usd: float = Field(ge=0)
    taker_buy_ratio: float = Field(ge=0, le=1)
    quote_exchange_timestamp_ms: int = Field(ge=0)
    quote_received_timestamp_ms: int = Field(ge=0)
    trade_exchange_timestamp_ms: int = Field(ge=0)
    trade_received_timestamp_ms: int = Field(ge=0)
    depth_exchange_timestamp_ms: int = Field(ge=0)
    depth_received_timestamp_ms: int = Field(ge=0)
    quote_sequence: int = Field(ge=0)
    trade_sequence: int = Field(ge=0)
    depth_sequence: int = Field(ge=0)
    entry_trigger: dict = Field(default_factory=dict)
    market_structure: dict = Field(default_factory=dict)


class TriggerReady(TriggerSnapshot):
    entry_price: float


class TriggerEngine:
    """One in-memory state machine. It never writes SQLite or calls an LLM."""

    def __init__(
        self,
        *,
        instrument: InstrumentId,
        direction: Literal["long", "short"],
        breakout_level: float,
        pullback_band: tuple[float, float],
        entry_zone: tuple[float, float],
        policy: TriggerPolicy,
    ):
        self.instrument = instrument
        self.direction = direction
        self.breakout_level = float(breakout_level)
        self.pullback_band = pullback_band
        self.entry_zone = entry_zone
        self.policy = policy
        self.state = "armed"
        self.subscription_confirmed = False
        self.health_reason: str | None = None
        self._sequences: dict[str, int] = {}
        self._quote: FastMarketEvent | None = None
        self._depth: FastMarketEvent | None = None
        self._trades: deque[FastMarketEvent] = deque()
        self._first_reclaim_quote_ms: int | None = None
        self._first_reclaim_quote_sequence: int | None = None
        self._pullback_seen_ms: int | None = None
        self._closed_candles_1m: deque[dict] = deque(maxlen=120)
        self._current_candle_1m: dict | None = None

    def preload_closed_candles(self, candles: list[dict]) -> None:
        """Warm observation-only structure state without touching trigger state."""

        self._closed_candles_1m.clear()
        for candle in candles[-120:]:
            self._closed_candles_1m.append(dict(candle))

    def confirm_subscription(self) -> None:
        self.subscription_confirmed = True
        self.health_reason = None

    def observe(self, event: FastMarketEvent) -> TriggerReady | None:
        if event.instrument != self.instrument:
            self.health_reason = "fast_trigger_instrument_mismatch"
            return None
        previous = self._sequences.get(event.event_type)
        if previous is not None and event.sequence <= previous:
            self.health_reason = f"sequence_anomaly:{event.event_type}"
            return None
        self._sequences[event.event_type] = event.sequence
        if self.health_reason is not None:
            return None
        if event.event_type == "trade":
            self._trades.append(event)
            self._prune_trades(event.received_timestamp_ms)
        elif event.event_type == "depth":
            self._depth = event
        elif event.event_type == "candle" and event.candle is not None:
            open_, high, low, close, volume = event.candle
            candle = {
                "ts": event.exchange_timestamp_ms,
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }
            if (
                self._current_candle_1m is not None
                and candle["ts"] > self._current_candle_1m["ts"]
            ):
                self._closed_candles_1m.append(self._current_candle_1m)
            if self._current_candle_1m is None or candle["ts"] >= self._current_candle_1m["ts"]:
                self._current_candle_1m = candle
        elif event.event_type == "quote":
            self._quote = event
            mid = (float(event.bid) + float(event.ask)) / 2
            crossed_to_pullback_side = (
                mid <= self.breakout_level
                if self.direction == "long"
                else mid >= self.breakout_level
            )
            if (
                self.state == "armed"
                and self.pullback_band[0] <= mid <= self.pullback_band[1]
                and crossed_to_pullback_side
            ):
                self.state = "pullback_seen"
                self._pullback_seen_ms = event.received_timestamp_ms
            if self.state == "pullback_seen":
                reclaimed = (
                    mid > self.breakout_level
                    if self.direction == "long"
                    else mid < self.breakout_level
                )
                if not reclaimed:
                    self._first_reclaim_quote_ms = None
                    self._first_reclaim_quote_sequence = None
                elif self._first_reclaim_quote_ms is None:
                    self._first_reclaim_quote_ms = event.received_timestamp_ms
                    self._first_reclaim_quote_sequence = event.sequence
                elif event.received_timestamp_ms - self._first_reclaim_quote_ms >= 300:
                    return self.evaluate(event.received_timestamp_ms)
        return None

    def _prune_trades(self, now_ms: int) -> None:
        cutoff = now_ms - 10_000
        while self._trades and self._trades[0].received_timestamp_ms < cutoff:
            self._trades.popleft()

    def evaluate(self, now_ms: int) -> TriggerReady | None:
        if self.health_reason or not self.subscription_confirmed or self.state != "pullback_seen":
            return None
        quote, depth = self._quote, self._depth
        if quote is None or depth is None or not self._trades:
            return None
        self._prune_trades(now_ms)
        if not self._trades:
            return None
        if (
            now_ms - quote.received_timestamp_ms > 1_000
            or now_ms - depth.received_timestamp_ms > 1_000
            or now_ms - self._trades[-1].received_timestamp_ms > 2_000
        ):
            return None
        bid, ask = float(quote.bid), float(quote.ask)
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 10_000
        if spread > self.policy.max_spread_bps:
            return None
        depth_value = sum(
            price * size
            for price, size in depth.bids + depth.asks
            if abs(price / mid - 1.0) <= 0.001
        )
        if depth_value < self.policy.min_depth_10bps_usd:
            return None
        buy = sum(float(item.price) * float(item.size) for item in self._trades if item.side == "buy")
        total = sum(float(item.price) * float(item.size) for item in self._trades)
        if total <= 0:
            return None
        ratio = buy / total
        if (self.direction == "long" and ratio < 0.60) or (
            self.direction == "short" and ratio > 0.40
        ):
            return None
        entry = ask if self.direction == "long" else bid
        if not self.entry_zone[0] <= entry <= self.entry_zone[1]:
            return None
        observed = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
        from aletheia.universe.market_structure import evaluate_market_structure

        structure = evaluate_market_structure(
            list(self._closed_candles_1m), direction=self.direction
        )
        return TriggerReady(
            observed_at=observed,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            depth_10bps_usd=depth_value,
            taker_buy_ratio=ratio,
            quote_exchange_timestamp_ms=quote.exchange_timestamp_ms,
            quote_received_timestamp_ms=quote.received_timestamp_ms,
            trade_exchange_timestamp_ms=self._trades[-1].exchange_timestamp_ms,
            trade_received_timestamp_ms=self._trades[-1].received_timestamp_ms,
            depth_exchange_timestamp_ms=depth.exchange_timestamp_ms,
            depth_received_timestamp_ms=depth.received_timestamp_ms,
            quote_sequence=quote.sequence,
            trade_sequence=self._trades[-1].sequence,
            depth_sequence=depth.sequence,
            entry_price=entry,
            entry_trigger={
                "trigger_spec_version": TRIGGER_SPEC_VERSION,
                "breakout_level": self.breakout_level,
                "pullback_seen_at_ms": self._pullback_seen_ms,
                "first_reclaim_quote_at_ms": self._first_reclaim_quote_ms,
                "first_reclaim_quote_sequence": self._first_reclaim_quote_sequence,
                "confirming_reclaim_quote_at_ms": quote.received_timestamp_ms,
                "confirming_reclaim_quote_sequence": quote.sequence,
                "minimum_confirmation_spacing_ms": 300,
            },
            market_structure=structure,
        )


class _AuthorizationReview(BaseModel):
    decision: Literal["arm", "refuse"]
    reasoning: str = Field(min_length=1, max_length=2_000)
    max_risk_pct: float = Field(gt=0)
    max_leverage: int = Field(ge=1)
    target_risk_reward: Literal[2.0] = TARGET_RISK_REWARD
    expected_horizon_hours: float = Field(gt=0, le=48)


def authorize_setup(
    setup_id: str,
    *,
    reasoning: str,
    max_spread_bps: float,
    min_depth_10bps_usd: float,
    max_risk_pct: float,
    max_leverage: int,
    target_risk_reward: float,
    expected_horizon_hours: float,
    now: datetime | None = None,
    cfg=None,
) -> ConditionalEntryAuthorization:
    """Atomically persist one `PiDecision(action='arm')` and its formula authority."""

    from aletheia.config.loader import load_config
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.instruments import get_max_leverage

    cfg = cfg or load_config(persist_migrations=False)
    current = _utc(now)
    setup_row = repo.get_universe_setup(setup_id)
    if setup_row is None:
        raise ValueError("fast_trigger_setup_not_found")
    setup = UniverseSetup.model_validate(setup_row)
    if setup.state != "pending_authorization":
        existing = repo.get_conditional_entry_authorization(setup_id)
        if existing is not None:
            return ConditionalEntryAuthorization.model_validate(existing)
        raise ValueError(f"fast_trigger_setup_not_authorizable:{setup.state}")
    if _parse_instant(setup.valid_until) <= current:
        repo.transition_universe_setup(
            setup_id, expected_states=("pending_authorization",), state="expired"
        )
        raise ValueError("fast_trigger_setup_expired")
    execution_mode = cfg.exchange.paper_execution_mode
    if cfg.fast_trigger.mode not in {"shadow", "execute"}:
        raise ValueError("fast_trigger_disabled")
    trigger_venue = fast_market_data_venue_for_config(cfg)
    if setup.venue.lower() != trigger_venue:
        raise ValueError("fast_trigger_execution_venue_mismatch")
    policy = resolve_risk_policy(cfg, execution_mode).policy
    policy_risk = float(policy.loss.max_single_trade_risk_pct)
    if max_risk_pct > policy_risk:
        raise ValueError("fast_trigger_authorization_exceeds_risk_policy")
    physical_market = instrument_market_to_db_market(setup.market_type)
    asset_cap = 1 if physical_market == "spot" else get_max_leverage(setup.symbol, False, cfg)
    if max_leverage > asset_cap:
        raise ValueError("fast_trigger_authorization_exceeds_leverage_cap")
    if max_spread_bps > float(cfg.universe.max_spread_bps):
        raise ValueError("fast_trigger_authorization_loosens_spread")
    if min_depth_10bps_usd < float(cfg.universe.min_depth_10bps_usd):
        raise ValueError("fast_trigger_authorization_loosens_depth")
    if float(target_risk_reward) != TARGET_RISK_REWARD:
        raise ValueError("fast_trigger_authorization_target_must_be_2r")
    decision = PiDecision(
        symbol=setup.symbol,
        direction=setup.direction,
        market_type=physical_market,
        action="arm",
        reasoning=reasoning,
        proposal_status="not_applicable",
        proposal_reason="conditional_entry_authorization",
        setup_id=setup.id,
        evidence_manifest_json=json.dumps({
            "trigger_spec_hash": setup.trigger_spec_hash,
            "material_fingerprint": setup.material_fingerprint,
            "entry_zone": _zone(setup.entry_zone),
            "invalidation_price": setup.invalidation_price,
        }, sort_keys=True, separators=(",", ":")),
    )
    valid_until = min(
        _parse_instant(setup.valid_until),
        current + timedelta(minutes=int(cfg.fast_trigger.authorization_ttl_minutes)),
    )
    authorization = ConditionalEntryAuthorization(
        setup_id=setup.id,
        operator_decision_id=decision.id,
        authorized_mode=cfg.fast_trigger.mode,
        trigger_spec_hash=setup.trigger_spec_hash,
        direction=setup.direction,
        entry_zone=setup.entry_zone,
        invalidation_price=setup.invalidation_price,
        max_spread_bps=max_spread_bps,
        min_depth_10bps_usd=min_depth_10bps_usd,
        max_risk_pct=max_risk_pct,
        max_leverage=max_leverage,
        target_risk_reward=target_risk_reward,
        expected_horizon_hours=expected_horizon_hours,
        authorized_at=current.isoformat(),
        valid_until=valid_until.isoformat(),
    )
    with repo.transaction(immediate=True):
        repo.insert_pi_decision(decision)
        repo.insert_conditional_entry_authorization(authorization)
        if not repo.transition_universe_setup(
            setup.id, expected_states=("pending_authorization",), state="armed"
        ):
            raise RuntimeError("fast_trigger_setup_authorization_conflict")
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "agent",
        "source": "agent_runtime",
        "event_type": "conditional_entry_authorized",
        "entity_type": "universe_setup",
        "entity_id": setup.id,
        "before": {"status": "pending_authorization"},
        "after": {"status": "armed"},
        "message": "System Operator armed a bounded conditional Momentum setup.",
    })
    return authorization


async def authorize_next_setup(cfg=None) -> dict:
    """Slow-path System Operator review of only the highest-ranked pending setup."""

    from aletheia.agents.helpers import llm_structured
    from aletheia.config.loader import load_config
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.instruments import get_max_leverage

    cfg = cfg or load_config(persist_migrations=False)
    stream_reason = validate_fast_trigger_stream(cfg)
    if stream_reason:
        return {"status": "unavailable", "reason_code": stream_reason}
    repo.expire_universe_setups()
    rows = repo.list_pending_authorization_setups(limit=1)
    if not rows:
        return {"status": "idle"}
    setup = UniverseSetup.model_validate(rows[0])
    member = repo.get_universe_member(setup.member_id) or {}
    execution_mode = cfg.exchange.paper_execution_mode
    policy = resolve_risk_policy(cfg, execution_mode).policy
    risk_cap = float(policy.loss.max_single_trade_risk_pct)
    leverage_cap = (
        1
        if instrument_market_to_db_market(setup.market_type) == "spot"
        else get_max_leverage(setup.symbol, False, cfg)
    )
    prompt = json.dumps({
        "setup": {
            "setup_id": setup.id,
            "instrument": {
                "venue": setup.venue,
                "symbol": setup.symbol,
                "market_type": setup.market_type,
            },
            "direction": setup.direction,
            "rank": setup.rank,
            "regime": setup.regime_tag,
            "breakout_level": setup.breakout_level,
            "atr": setup.atr,
            "entry_zone": _zone(setup.entry_zone),
            "invalidation_price": setup.invalidation_price,
            "valid_until": setup.valid_until,
            "universe_score": member.get("total_score"),
            "universe_reasons": member.get("inclusion_reason"),
        },
        "hard_caps": {
            "max_risk_pct": risk_cap,
            "max_leverage": leverage_cap,
            "max_spread_bps": cfg.universe.max_spread_bps,
            "min_depth_10bps_usd": cfg.universe.min_depth_10bps_usd,
            "target_risk_reward": TARGET_RISK_REWARD,
            "authorization_ttl_minutes": cfg.fast_trigger.authorization_ttl_minutes,
        },
    }, sort_keys=True, default=str)
    review, error = await llm_structured(
        "You are Aletheia System Operator. Review one exact Momentum setup. "
        "Return arm only when its Universe motive, direction, regime, structure, "
        "invalidation, and bounded risk envelope are coherent. Never alter the "
        "TriggerSpec, invent an entry price/quantity/TP, expand a hard cap, or "
        "authorize Live. Refuse otherwise. Do not debate.",
        prompt,
        schema=_AuthorizationReview,
        fallback_to_defaults=False,
        caller="agent_shell.fast_trigger_authorizer",
        context_symbol=setup.symbol,
        reasoning_effort="low",
    )
    if review is None:
        return {
            "status": "pending_authorization",
            "setup_id": setup.id,
            "reason_code": "fast_trigger_operator_unavailable",
            "error_type": str(error or "unknown").split(":", 1)[0],
        }
    decision = _AuthorizationReview.model_validate(review)
    if decision.decision == "arm":
        try:
            authorization = authorize_setup(
                setup.id,
                reasoning=decision.reasoning,
                max_spread_bps=float(cfg.universe.max_spread_bps),
                min_depth_10bps_usd=float(cfg.universe.min_depth_10bps_usd),
                max_risk_pct=decision.max_risk_pct,
                max_leverage=decision.max_leverage,
                target_risk_reward=decision.target_risk_reward,
                expected_horizon_hours=decision.expected_horizon_hours,
                cfg=cfg,
            )
        except (RuntimeError, ValueError) as exc:
            return {
                "status": "pending_authorization",
                "setup_id": setup.id,
                "reason_code": str(exc)[:160],
            }
        return {
            "status": "armed",
            "setup_id": setup.id,
            "authorization_id": authorization.id,
        }
    refusal = PiDecision(
        symbol=setup.symbol,
        direction=setup.direction,
        market_type=instrument_market_to_db_market(setup.market_type),
        action="refuse",
        reasoning=decision.reasoning,
        proposal_status="not_applicable",
        proposal_reason="conditional_entry_refused",
        setup_id=setup.id,
        evidence_manifest_json=json.dumps({
            "trigger_spec_hash": setup.trigger_spec_hash,
            "material_fingerprint": setup.material_fingerprint,
        }, sort_keys=True, separators=(",", ":")),
    )
    with repo.transaction(immediate=True):
        repo.insert_pi_decision(refusal)
        if not repo.transition_universe_setup(
            setup.id, expected_states=("pending_authorization",), state="refused"
        ):
            raise RuntimeError("fast_trigger_setup_refusal_conflict")
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "agent",
        "source": "agent_runtime",
        "event_type": "conditional_entry_refused",
        "entity_type": "universe_setup",
        "entity_id": setup.id,
        "before": {"status": "pending_authorization"},
        "after": {"status": "refused", "operator_decision_id": refusal.id},
        "message": "System Operator refused the bounded Momentum setup.",
    })
    return {"status": "refused", "setup_id": setup.id, "decision_id": refusal.id}


def validate_fast_trigger_stream(cfg) -> str | None:
    if cfg.fast_trigger.mode == "off":
        return "fast_trigger_disabled"
    mode = str(cfg.exchange.paper_execution_mode or "")
    if "live" in mode:
        return "fast_trigger_live_forbidden"
    if not fast_market_stream_available_for_config(cfg):
        venue = fast_market_data_venue_for_config(cfg)
        return f"fast_trigger_stream_unavailable:{venue}"
    return None


def validate_fast_trigger_execution(cfg) -> str | None:
    return validate_fast_trigger_stream(cfg)


def validate_materialized_opportunity(
    setup_id: str,
    trigger: TriggerSnapshot,
    *,
    now: datetime | None = None,
) -> dict:
    """Revalidate one frozen generation immediately before Gateway submission."""

    setup_row = repo.get_universe_setup(setup_id)
    opportunity = repo.get_opportunity_by_setup(setup_id)
    authorization_row = repo.get_conditional_entry_authorization(setup_id)
    if setup_row is None or opportunity is None or authorization_row is None:
        return {"status": "refused", "reason_code": "fast_trigger_generation_missing"}
    setup = UniverseSetup.model_validate(setup_row)
    authorization = ConditionalEntryAuthorization.model_validate(authorization_row)
    current = _utc(now)
    observed = _parse_instant(trigger.observed_at)
    if abs((current - observed).total_seconds()) > 1.0:
        verdict, check_type, reason_code = (
            "block", "entry_zone", "fast_trigger_quote_stale"
        )
    else:
        executable = trigger.ask if setup.direction == "long" else trigger.bid
        structural_hit = (
            setup.direction == "long" and trigger.bid <= authorization.invalidation_price
        ) or (
            setup.direction == "short" and trigger.ask >= authorization.invalidation_price
        )
        if structural_hit:
            verdict, check_type, reason_code = (
                "block", "structural_price", "fast_trigger_structural_invalidation"
            )
        elif not _zone(authorization.entry_zone)[0] <= executable <= _zone(authorization.entry_zone)[1]:
            verdict, check_type, reason_code = (
                "block", "entry_zone", "fast_trigger_entry_zone_escaped"
            )
        else:
            verdict, check_type, reason_code = "allow", "entry_zone", None
    assessment = _validity_assessment(
        opportunity_id=str(opportunity["id"]),
        setup=setup,
        check_type=check_type,
        authority="hard",
        verdict=verdict,
        observed_at=trigger.observed_at,
        algorithm_version=(
            "structural-invalidation/v1"
            if check_type == "structural_price"
            else "entry-zone/v1"
        ),
        evidence={
            "bid": trigger.bid,
            "ask": trigger.ask,
            "entry_zone": _zone(authorization.entry_zone),
            "invalidation_price": authorization.invalidation_price,
            "reason_code": reason_code,
        },
    )
    with repo.transaction(immediate=True):
        repo.insert_opportunity_validity_assessment(assessment)
        if verdict == "block":
            changed = repo.transition_universe_setup(
                setup_id, expected_states=("triggering",), state="invalidated"
            )
            if changed:
                repo.execute_write(
                    "UPDATE opportunities SET status='invalidated' "
                    "WHERE id=? AND status IN ('open','queued','reviewed')",
                    (opportunity["id"],),
                )
                if setup.proposal_id:
                    repo.execute_write(
                        "UPDATE trade_proposals SET status='rejected' "
                        "WHERE id=? AND status='pending'",
                        (setup.proposal_id,),
                    )
    return {
        "status": "invalidated" if verdict == "block" else "valid",
        "reason_code": reason_code,
        "opportunity_id": opportunity["id"],
    }


def materialize_trigger(
    setup_id: str,
    trigger: TriggerSnapshot,
    *,
    cfg=None,
    now: datetime | None = None,
    execute_gateway: bool = True,
) -> dict:
    """Create one Opportunity/Proposal/T0 fact, then optionally call Gateway."""

    from aletheia.config.loader import load_config
    from aletheia.risk.engine import account_available_for_execution
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.instruments import get_max_leverage
    from aletheia.trading.sizing import available_balance_risk_budget_size

    cfg = cfg or load_config(persist_migrations=False)
    reason = validate_fast_trigger_execution(cfg)
    if reason:
        return {"status": "refused", "reason_code": reason, "setup_id": setup_id}
    if cfg.runtime.paused or cfg.runtime.kill_switch_active:
        return {
            "status": "refused",
            "reason_code": (
                "fast_trigger_runtime_paused"
                if cfg.runtime.paused
                else "fast_trigger_kill_switch_active"
            ),
            "setup_id": setup_id,
        }
    current = _utc(now)
    try:
        observed_at = _parse_instant(trigger.observed_at)
    except ValueError:
        return {
            "status": "refused",
            "reason_code": "fast_trigger_invalid_observed_at",
            "setup_id": setup_id,
        }
    age_seconds = (current - observed_at).total_seconds()
    if age_seconds > 1.0 or age_seconds < -1.0:
        return {
            "status": "refused",
            "reason_code": "fast_trigger_quote_stale",
            "setup_id": setup_id,
        }
    current_ms = int(current.timestamp() * 1_000)
    freshness = (
        ("quote", trigger.quote_exchange_timestamp_ms, trigger.quote_received_timestamp_ms, 1_000),
        ("trade", trigger.trade_exchange_timestamp_ms, trigger.trade_received_timestamp_ms, 2_000),
        ("depth", trigger.depth_exchange_timestamp_ms, trigger.depth_received_timestamp_ms, 1_000),
    )
    for kind, exchange_ms, received_ms, max_age_ms in freshness:
        if (
            current_ms - exchange_ms > max_age_ms
            or current_ms - received_ms > max_age_ms
            or exchange_ms - current_ms > 1_000
            or received_ms - current_ms > 1_000
        ):
            return {
                "status": "refused",
                "reason_code": f"fast_trigger_{kind}_stale",
                "setup_id": setup_id,
            }
    setup_row = repo.get_universe_setup(setup_id)
    if setup_row is None:
        return {"status": "not_found", "setup_id": setup_id}
    existing = repo.get_opportunity_by_setup(setup_id)
    if existing is not None:
        return {
            "status": "already_materialized",
            "setup_id": setup_id,
            "opportunity_id": existing["id"],
            "proposal_id": (repo.get_universe_setup(setup_id) or {}).get("proposal_id"),
        }
    setup = UniverseSetup.model_validate(setup_row)
    trigger_venue = fast_market_data_venue_for_config(cfg)
    if setup.venue.lower() != trigger_venue:
        return {
            "status": "refused",
            "reason_code": "fast_trigger_execution_venue_mismatch",
            "setup_id": setup_id,
        }
    auth_row = repo.get_conditional_entry_authorization(setup_id)
    if auth_row is None:
        return {"status": "refused", "reason_code": "fast_trigger_not_authorized", "setup_id": setup_id}
    authorization = ConditionalEntryAuthorization.model_validate(auth_row)
    if setup.state not in {"armed", "pullback_seen"}:
        return {"status": "refused", "reason_code": f"fast_trigger_setup_state:{setup.state}", "setup_id": setup_id}
    if (
        _parse_instant(setup.valid_until) <= current
        or _parse_instant(authorization.valid_until) <= current
    ):
        repo.transition_universe_setup(
            setup_id, expected_states=(setup.state,), state="expired"
        )
        return {"status": "refused", "reason_code": "fast_trigger_authorization_expired", "setup_id": setup_id}
    if (
        authorization.trigger_spec_hash != setup.trigger_spec_hash
        or authorization.authorized_mode != cfg.fast_trigger.mode
        or authorization.direction != setup.direction
        or authorization.entry_zone != setup.entry_zone
        or authorization.invalidation_price != setup.invalidation_price
    ):
        repo.transition_universe_setup(
            setup_id, expected_states=(setup.state,), state="invalidated"
        )
        return {"status": "refused", "reason_code": "fast_trigger_authorization_mismatch", "setup_id": setup_id}
    values = (
        trigger.bid, trigger.ask, trigger.spread_bps,
        trigger.depth_10bps_usd, trigger.taker_buy_ratio,
    )
    if not all(math.isfinite(float(value)) for value in values) or trigger.ask < trigger.bid:
        return {"status": "refused", "reason_code": "fast_trigger_invalid_market_snapshot", "setup_id": setup_id}
    if trigger.spread_bps > authorization.max_spread_bps:
        return {"status": "refused", "reason_code": "fast_trigger_spread_exceeded", "setup_id": setup_id}
    if trigger.depth_10bps_usd < authorization.min_depth_10bps_usd:
        return {"status": "refused", "reason_code": "fast_trigger_depth_insufficient", "setup_id": setup_id}
    if (setup.direction == "long" and trigger.taker_buy_ratio < 0.60) or (
        setup.direction == "short" and trigger.taker_buy_ratio > 0.40
    ):
        return {"status": "refused", "reason_code": "fast_trigger_trade_flow_unconfirmed", "setup_id": setup_id}
    entry = trigger.ask if setup.direction == "long" else trigger.bid
    entry_zone = _zone(authorization.entry_zone)
    structural_hit = (
        setup.direction == "long" and trigger.bid <= authorization.invalidation_price
    ) or (
        setup.direction == "short" and trigger.ask >= authorization.invalidation_price
    )
    if structural_hit:
        repo.transition_universe_setup(
            setup_id, expected_states=(setup.state,), state="invalidated"
        )
        return {
            "status": "refused",
            "reason_code": "fast_trigger_structural_invalidation",
            "setup_id": setup_id,
        }
    if not entry_zone[0] <= entry <= entry_zone[1]:
        repo.transition_universe_setup(
            setup_id, expected_states=(setup.state,), state="invalidated"
        )
        return {"status": "refused", "reason_code": "fast_trigger_entry_zone_escaped", "setup_id": setup_id}
    stop = authorization.invalidation_price
    risk_distance = entry - stop if setup.direction == "long" else stop - entry
    if risk_distance <= 0:
        return {"status": "refused", "reason_code": "fast_trigger_invalid_invalidation", "setup_id": setup_id}
    target = (
        entry + risk_distance * authorization.target_risk_reward
        if setup.direction == "long"
        else entry - risk_distance * authorization.target_risk_reward
    )
    if target <= 0:
        return {"status": "refused", "reason_code": "fast_trigger_invalid_target", "setup_id": setup_id}
    execution_mode = cfg.exchange.paper_execution_mode
    policy = resolve_risk_policy(cfg, execution_mode).policy
    risk_pct = min(
        authorization.max_risk_pct,
        float(policy.loss.max_single_trade_risk_pct),
    )
    db_market = instrument_market_to_db_market(setup.market_type)
    if (
        cfg.fast_trigger.mode == "execute"
        and execution_mode != "paper_internal"
        and db_market != "usdt_futures"
    ):
        return {
            "status": "refused",
            "reason_code": "fast_trigger_bitget_demo_market_unavailable",
            "setup_id": setup_id,
        }
    leverage = (
        1
        if db_market == "spot"
        else min(authorization.max_leverage, get_max_leverage(setup.symbol, False, cfg))
    )
    available = account_available_for_execution(execution_mode, cfg)
    if available is None or available <= 0:
        return {"status": "refused", "reason_code": "fast_trigger_available_balance_unavailable", "setup_id": setup_id}
    notional = available_balance_risk_budget_size(
        available, risk_pct, leverage=leverage
    )
    quantity = math.floor((notional / entry) * 1_000_000) / 1_000_000
    if quantity <= 0:
        return {"status": "refused", "reason_code": "fast_trigger_unrepresentable_quantity", "setup_id": setup_id}
    if setup.market_type == "perp" and cfg.fast_trigger.mode == "execute":
        if setup.funding_rate is None or setup.funding_observed_at is None:
            return {
                "status": "refused",
                "reason_code": "fast_trigger_funding_unavailable",
                "setup_id": setup_id,
            }
        if abs((current - _parse_instant(setup.funding_observed_at)).total_seconds()) > 3_600:
            return {
                "status": "refused",
                "reason_code": "fast_trigger_funding_stale",
                "setup_id": setup_id,
            }
    signal_mark = json.dumps({
        "venue": setup.venue,
        "symbol": setup.symbol,
        "market_type": setup.market_type,
        **trigger.model_dump(),
    }, sort_keys=True, separators=(",", ":"))
    generation_valid_until = min(
        _parse_instant(setup.valid_until), _parse_instant(authorization.valid_until)
    ).isoformat()
    try:
        motive_evidence = json.loads(setup.motive_evidence or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        motive_evidence = {}
    capacity_ratio = trigger.depth_10bps_usd / max(notional, 1e-12)
    estimated_slippage_bps = min(10.0, 10.0 / max(capacity_ratio, 1e-12))
    generation = OpportunityGenerationV2(
        instrument_identity=OpportunityInstrumentIdentity(
            venue=setup.venue,
            symbol=setup.symbol,
            market_type=setup.market_type,
        ),
        family="momentum",
        archetype="breakout",
        direction=setup.direction,
        detected_at=trigger.observed_at,
        signal_mark=entry,
        motive_evidence=motive_evidence,
        entry_trigger=trigger.entry_trigger or {
            "trigger_spec_version": setup.trigger_spec_version,
            "trigger_spec_hash": setup.trigger_spec_hash,
            "state_change": "pullback_seen->triggering",
        },
        entry_zone=entry_zone,
        invalidation={
            "kind": "structural_price",
            "price": stop,
            "entry_zone_escape_is_terminal": True,
        },
        horizon_hours=authorization.expected_horizon_hours,
        cost_snapshot={
            "cost_model_version": "fast-trigger-cost/v1",
            "spread_bps": trigger.spread_bps,
            "estimated_slippage_bps": estimated_slippage_bps,
            "funding": (
                {
                    "state": "not_applicable",
                    "rate": None,
                    "observed_at": None,
                }
                if setup.market_type == "spot"
                else {
                    "state": "available" if setup.funding_rate is not None else "unavailable",
                    "rate": setup.funding_rate,
                    "observed_at": setup.funding_observed_at,
                }
            ),
            "depth_10bps_usd": trigger.depth_10bps_usd,
            "target_notional_usd": notional,
            "capacity_ratio": capacity_ratio,
        },
        material_fingerprint=setup.material_fingerprint,
        valid_until=generation_valid_until,
    )
    _, generation_hash = repo.canonical_opportunity_generation(generation)
    opportunity = Opportunity(
        symbol=setup.symbol,
        market=db_market,
        direction_bias=setup.direction,
        thesis=f"[{TRIGGER_SPEC_VERSION}] pullback reclaim confirmed",
        confidence="high",
        source_agent="fast_trigger",
        status="reviewed",
        execution_eligible=True,
        universe_snapshot_id=setup.snapshot_id,
        universe_member_id=setup.member_id,
        scanner_family="momentum",
        valid_until=generation_valid_until,
        entry_zone=authorization.entry_zone,
        stop_loss_hint=stop,
        tp_hint=target,
        regime_tag=setup.regime_tag,
        setup_id=setup.id,
        signal_mark=signal_mark,
        trigger_behavior_version=cfg.fast_trigger.trigger_behavior_version,
        metadata=json.dumps({
            "authorization_id": authorization.id,
            "operator_decision_id": authorization.operator_decision_id,
            "material_fingerprint": setup.material_fingerprint,
            "trigger_spec_hash": setup.trigger_spec_hash,
            "generation_hash": generation_hash,
        }, sort_keys=True, separators=(",", ":")),
    )
    proposal = TradeProposal(
        opportunity_id=opportunity.id,
        symbol=setup.symbol,
        market_type=db_market,
        direction=setup.direction,
        order_type="market" if execution_mode == "paper_internal" else "limit",
        quantity=quantity,
        price=entry,
        leverage=leverage if db_market != "spot" else None,
        stop_price=stop,
        take_profit=target,
        risk_reward=authorization.target_risk_reward,
        expected_horizon_hours=authorization.expected_horizon_hours,
        stop_source="operator",
        execution_mode=execution_mode,
        agent_rationale="Conditional Momentum setup materialized by deterministic fast trigger.",
        attribution_snapshot=json.dumps({
            "instrument": {"exchange": setup.venue, "symbol": setup.symbol,
                           "market_type": setup.market_type},
            "setup_id": setup.id,
            "authorization_id": authorization.id,
            "signal_mark": json.loads(signal_mark),
            "opportunity_generation_hash": generation_hash,
            "sizing": {"available_balance_usdt": available, "risk_pct": risk_pct,
                       "notional_usdt": notional, "leverage": leverage},
        }, sort_keys=True, separators=(",", ":")),
        status="rejected" if cfg.fast_trigger.mode == "shadow" else "pending",
        setup_id=setup.id,
        authorization_id=authorization.id,
    )
    market_snapshot = FastTriggerMarketSnapshot(
        setup_id=setup.id,
        authorization_id=authorization.id,
        opportunity_id=opportunity.id,
        proposal_id=proposal.id,
        venue=setup.venue,
        symbol=setup.symbol,
        market_type=setup.market_type,
        **trigger.model_dump(),
    )
    hard_assessment = _validity_assessment(
        opportunity_id=opportunity.id,
        setup=setup,
        check_type="entry_zone",
        authority="hard",
        verdict="allow",
        observed_at=trigger.observed_at,
        algorithm_version="entry-zone/v1",
        evidence={
            "entry_price": entry,
            "entry_zone": entry_zone,
            "bid": trigger.bid,
            "ask": trigger.ask,
        },
    )
    structure = trigger.market_structure or {
        "algorithm_version": COMPAT_ALGORITHM_VERSION,
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "verdict": "unavailable",
        "reason": "not_preloaded",
    }
    structure_assessment = _validity_assessment(
        opportunity_id=opportunity.id,
        setup=setup,
        check_type="market_structure",
        authority="shadow",
        verdict=structure.get("verdict", "unavailable"),
        observed_at=trigger.observed_at,
        algorithm_version=str(
            structure.get("algorithm_version") or COMPAT_ALGORITHM_VERSION
        ),
        evidence=structure,
    )
    with repo.transaction(immediate=True):
        if repo.get_opportunity_by_setup(setup.id) is not None:
            return {
                "status": "already_materialized",
                "setup_id": setup.id,
                "opportunity_id": repo.get_opportunity_by_setup(setup.id)["id"],
            }
        if not repo.transition_universe_setup(
            setup.id,
            expected_states=("armed", "pullback_seen"),
            state="triggering",
            opportunity_id=opportunity.id,
            proposal_id=proposal.id,
        ):
            raise RuntimeError("fast_trigger_materialization_conflict")
        opportunity = repo.insert_opportunity_generation(opportunity, generation)
        repo.insert_trade_proposal(proposal, mandate_config=cfg)
        repo.insert_fast_trigger_market_snapshot(market_snapshot)
        repo.insert_opportunity_validity_assessment(hard_assessment)
        repo.insert_opportunity_validity_assessment(structure_assessment)
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "system",
        "source": "fast_trigger",
        "event_type": "conditional_entry_materialized",
        "entity_type": "universe_setup",
        "entity_id": setup.id,
        "before": {"status": setup.state},
        "after": {
            "status": "triggering",
            "opportunity_id": opportunity.id,
            "proposal_id": proposal.id,
            "authorization_id": authorization.id,
        },
        "message": "Authorized Momentum setup materialized from an exact-venue T0 snapshot.",
    })
    result = {
        "status": "shadow_materialized" if cfg.fast_trigger.mode == "shadow" else "materialized",
        "setup_id": setup.id,
        "authorization_id": authorization.id,
        "opportunity_id": opportunity.id,
        "proposal_id": proposal.id,
    }
    if not execute_gateway:
        return result
    if cfg.fast_trigger.mode == "execute":
        preflight = validate_materialized_opportunity(
            setup.id, trigger, now=current
        )
        result["entry_validity"] = preflight
        if preflight["status"] != "valid":
            result["status"] = "invalidated"
            return result
        from aletheia.execution.gateway import execute_existing_proposal

        gateway_started_at = datetime.now(timezone.utc)
        result["frame_to_gateway_ms"] = max(
            0.0, (gateway_started_at - observed_at).total_seconds() * 1_000
        )
        execution = execute_existing_proposal(proposal.id, execution_mode)
        result["execution"] = execution
        terminal = "executed" if execution.get("status") in {"executed", "already_executed"} else "refused"
    else:
        terminal = "refused"
    repo.transition_universe_setup(
        setup.id, expected_states=("triggering",), state=terminal
    )
    write_audit({
        "actor": "system",
        "source": "fast_trigger",
        "event_type": "conditional_entry_terminal",
        "entity_type": "universe_setup",
        "entity_id": setup.id,
        "before": {"status": "triggering"},
        "after": {
            "status": terminal,
            "frame_to_gateway_ms": result.get("frame_to_gateway_ms"),
        },
        "message": f"Fast Trigger reached terminal state {terminal}.",
    })
    return result
