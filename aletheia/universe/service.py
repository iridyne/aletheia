"""UniverseService — orchestrates the 4-layer universe generation pipeline.

Responsible for: registry sync → feature collection → filter/score → allocate.
Consumers call get_current_universe() to get the active candidate pool.
Falls back to static BTC/ETH/SOL when disabled or on any failure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    UniverseSnapshot, UniverseMember, UniverseScannerRun,
)

logger = logging.getLogger("aletheia.universe")


def _volume_ranked_instruments(rows: list, default_venue: str):
    """Normalize structured and legacy volume-ranking rows to exact instruments."""
    from aletheia.data.symbols import InstrumentId, MARKET_PERP, MARKET_SPOT

    result: list[InstrumentId] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            symbol = row.get("symbol")
            venue = row.get("exchange") or default_venue
            market_type = row.get("market_type") or MARKET_SPOT
        else:
            symbol = row
            venue = default_venue
            market_type = MARKET_SPOT
        if venue != default_venue or not symbol:
            continue
        if market_type == "usdt_futures":
            market_type = MARKET_PERP
        try:
            instrument = InstrumentId.from_parts(venue, symbol, market_type)
        except ValueError:
            continue
        key = str(instrument)
        if key not in seen:
            seen.add(key)
            result.append(instrument)
    return result


def _dedupe_by_symbol_preferred_market(allocated: list[dict], cfg) -> list[dict]:
    """Keep one market instrument per symbol, respecting configured market preference."""
    preferred = getattr(cfg.exchange, "preferred_market_type", "auto")
    spot_enabled = cfg.exchange.enabled_markets.get("spot", True)
    perp_enabled = cfg.exchange.enabled_markets.get("usdt_futures", False)
    preferred_market = "spot" if preferred == "spot" else "perp"

    def market_rank(item: dict) -> int:
        if spot_enabled and perp_enabled:
            return 0 if item.get("market_type", "spot") == preferred_market else 1
        return 0

    seen_symbols: dict[str, dict] = {}
    for item in allocated:
        sym = item["symbol"]
        current = seen_symbols.get(sym)
        if current is None:
            seen_symbols[sym] = item
            continue

        current_key = (market_rank(current), -current.get("total_score", 0))
        item_key = (market_rank(item), -item.get("total_score", 0))
        if item_key < current_key:
            seen_symbols[sym] = item

    return sorted(seen_symbols.values(), key=lambda x: x["total_score"], reverse=True)


def _register_experiment_candidate_outcomes(scored: list[dict], cfg, *, entry_ts: str) -> int:
    """P0-3: settle the FULL experiment arms, not just the survivors.

    A Universe Learning exposure is created for every calibrated candidate during
    scoring (allocator.compute_total_score), before allocation narrows the pool.
    Forward outcomes, however, were only registered for the members that were
    selected — so a candidate whose treatment multiplier pushed it out of the
    Universe silently had no outcome. Missing outcomes then correlate with the
    treatment itself → post-treatment selection bias (the verdict compares two
    self-selected survivor subsets, not treatment-vs-control on the same pool).

    Register an exposure-aligned fixed-horizon candidate outcome for EVERY exposed
    candidate (treatment or control, selected or dropped), keyed by the exposure's
    ``candidate_key``. linked_settled_exposure_counts joins the filter_candidate_outcomes
    branch as ``outcome_candidate_key || ':' || horizon``, which equals the exposure's
    ``outcome_candidate_key`` (``candidate_key:horizon``) — so both arms settle in full.
    """
    from aletheia.analytics.filter_evidence import ensure_candidate_outcomes

    default_venue = getattr(getattr(cfg, "market_data", None), "ccxt_exchange", None)
    registered = 0
    for item in scored:
        key = item.get("learning_candidate_key")
        if not key:
            continue
        try:
            registered += ensure_candidate_outcomes(
                outcome_candidate_key=key,
                venue=item.get("venue") or default_venue,
                symbol=item.get("symbol"),
                market_type=item.get("market_type", "spot"),
                direction=item.get("scanner_direction") or item.get("direction"),
                scanner_family=item.get("scanner_family"),
                regime_tag=None,
                entry_price_usd=item.get("last_price"),
                entry_spread_bps=item.get("spread_bps"),
                funding_rate_at_entry=item.get("funding_rate"),
                entry_ts=entry_ts,
            )
        except Exception:
            logger.debug(
                "experiment candidate outcome registration skipped for %s",
                item.get("symbol"), exc_info=True,
            )
    if registered:
        logger.info(
            "Registered %d experiment candidate outcome row(s) across full arms "
            "(selection-bias fix)", registered,
        )
    return registered


class UniverseService:
    """Main orchestrator for the dynamic universe pipeline."""

    def get_current_universe(
        self,
        market_type: str = "crypto",
        execution_eligible_only: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Return current active universe members, or fallback symbols.

        Returns list of dicts with at minimum: symbol, scanner_family, total_score,
        snapshot_id, member_id, ttl_sec, expires_at, execution_eligible.
        """
        cfg = load_config()
        if not cfg.universe.enabled:
            return self._fallback_as_members(cfg)

        snap = repo.get_active_universe_snapshot(market_type)
        if not snap:
            logger.warning("No active universe snapshot; falling back to static pool")
            return self._fallback_as_members(cfg)

        members = repo.list_universe_members(snap["id"], limit=limit or 50)
        if not members:
            logger.warning("Active snapshot has no members; falling back to static pool")
            return self._fallback_as_members(cfg)

        now = datetime.now(timezone.utc).isoformat()
        ttl_enabled = cfg.universe.snapshot_ttl_enabled
        result = []
        for m in members:
            if execution_eligible_only and not m.get("execution_eligible", True):
                continue
            m_out = dict(m)
            if ttl_enabled and m_out.get("expires_at") and m_out["expires_at"] < now:
                if execution_eligible_only:
                    continue  # never use stale data for execution decisions
                m_out["stale"] = True
            result.append(m_out)

        if not result:
            logger.warning("All universe members expired; falling back to static pool")
            return self._fallback_as_members(cfg)

        if limit and len(result) > limit:
            result = result[:limit]

        return result

    async def refresh(self, market_type: str = "crypto") -> str | None:
        """Run the full 4-layer pipeline and produce a new universe snapshot.

        Returns the snapshot_id on success, None on failure.
        """
        from aletheia.universe.registry import sync_registry
        from aletheia.universe.features import collect_features
        from aletheia.universe.filters import apply_tradability_filters
        from aletheia.universe.scanners import run_all_scanners
        from aletheia.universe.allocator import (
            allocate_universe, compute_total_score, get_family_multipliers,
        )
        from aletheia.universe.outcome_recorder import settle_due, register_pending
        from aletheia.universe.signal_emitter import (
            emit_deterministic_opportunities, _infer_direction,
        )

        cfg = load_config()

        # Settle any overdue forward outcomes before producing new ones.
        # Clean outlier rows first (idempotent — fast no-op after first pass).
        try:
            cleaned = repo.mark_outlier_outcomes_failed()
            if cleaned:
                logger.info("Cleaned %d outlier forward outcome rows", cleaned)
            await settle_due()
        except Exception as e:
            logger.warning("Forward outcome settlement failed (non-fatal): %s", e)

        # Detect market regime (non-fatal — falls back to sideways)
        current_regime = None
        try:
            if not cfg.regime.enabled:
                raise RuntimeError("regime disabled by config")

            from aletheia.regime.collector import collect_regime_inputs
            from aletheia.regime.detector import debounced_detect, MarketRegime
            from aletheia.storage.models import MarketRegimeRecord

            regime_inputs = await collect_regime_inputs()
            if regime_inputs:
                current_regime = debounced_detect(
                    min_hold_periods=cfg.regime.min_hold_periods,
                    **regime_inputs,
                )
                record = MarketRegimeRecord(
                    state=current_regime.state,
                    confidence=current_regime.confidence,
                    long_bias=current_regime.long_bias,
                    short_bias=current_regime.short_bias,
                    signals=json.dumps(current_regime.signals),
                    detected_at=current_regime.detected_at,
                )
                repo.insert_market_regime(record)
                # If short globally disabled, zero out short_bias so gate blocks all shorts
                if not cfg.regime.short_enabled:
                    current_regime.short_bias = 0.0
                logger.info(
                    "Regime: %s (conf=%.2f, long_bias=%.2f, short_bias=%.2f)",
                    current_regime.state, current_regime.confidence,
                    current_regime.long_bias, current_regime.short_bias,
                )
        except Exception as e:
            logger.warning("Regime detection failed (non-fatal): %s", e)

        run = UniverseScannerRun()
        repo.insert_universe_scanner_run(run)

        try:
            count = await sync_registry(cfg)
            assets = repo.list_universe_assets(
                venue=cfg.market_data.ccxt_exchange, limit=1000,
            )
            repo.update_universe_scanner_run(run.id, assets_scanned=len(assets))

            # Inject volume-ranking symbols as additional candidates
            try:
                from aletheia.agent_runtime.scheduler import get_latest_volume_ranking
                vol = get_latest_volume_ranking()
                vol_symbols = vol.get("symbols") or [] if vol else []
                ranked_instruments = _volume_ranked_instruments(
                    vol_symbols[:20], cfg.market_data.ccxt_exchange,
                )
                rank_index = {
                    (item.exchange, item.symbol, item.market_type): index
                    for index, item in enumerate(ranked_instruments)
                }
                if rank_index:
                    assets.sort(key=lambda asset: rank_index.get(
                        (
                            asset.get("venue"),
                            asset.get("symbol"),
                            asset.get("market_type"),
                        ),
                        len(rank_index),
                    ))
            except Exception as e:
                logger.debug("volume_ranking injection skipped: %s", e)

            features_list = await collect_features(
                assets, cfg, scanner_run_id=run.id,
            )

            filtered = apply_tradability_filters(
                features_list, cfg.universe, scanner_run_id=run.id,
            )
            repo.update_universe_scanner_run(run.id, assets_passed=len(filtered))

            calibration = get_family_multipliers()
            if calibration:
                logger.info("Calibration multipliers: %s", calibration)

            scored = []
            for feat in filtered:
                scanner_result = run_all_scanners(feat)
                total = compute_total_score(
                    feat, scanner_result, cfg.universe, calibration=calibration or None,
                )
                scored.append({**feat, **scanner_result, "total_score": total})

            # A symbol's spot/perp alternatives represent one candidate thesis.
            # Deduplicate before quota allocation so the discarded instrument does
            # not consume a family/explore slot that is never refilled.
            unique_scored = _dedupe_by_symbol_preferred_market(scored, cfg)
            allocated = allocate_universe(
                unique_scored, cfg.universe,
                regime_state=current_regime.state if current_regime else None,
            )

            now = datetime.now(timezone.utc)
            snapshot_ttl_sec = int(cfg.universe.snapshot_ttl_hours * 3600)
            expires_at = (now + timedelta(seconds=snapshot_ttl_sec)).isoformat()
            snap = UniverseSnapshot(
                market_type=market_type,
                expires_at=expires_at,
                summary=json.dumps({
                    "scanned": len(assets),
                    "filtered": len(filtered),
                    "selected": len(allocated),
                }),
            )
            repo.insert_universe_snapshot(snap)
            repo.expire_universe_snapshots(market_type, except_id=snap.id)

            regime_tag = current_regime.state if current_regime else None
            inserted_members: list[dict] = []
            for rank, item in enumerate(allocated, 1):
                ttl_sec = min(item.get("ttl_sec", 3600), snapshot_ttl_sec)
                member_expires = (now + timedelta(seconds=ttl_sec)).isoformat()
                entry_price = item.get("last_price")
                scanner_direction = (
                    item.get("scanner_direction")
                    or _infer_direction(item.get("scanner_family"), item)
                )
                member = UniverseMember(
                    snapshot_id=snap.id,
                    asset_id=item["asset_id"],
                    symbol=item["symbol"],
                    market_type=item.get("market_type", "spot"),
                    rank=rank,
                    total_score=item["total_score"],
                    opportunity_score=item.get("opportunity_score"),
                    tradability_score=item.get("tradability_score"),
                    liquidity_score=item.get("liquidity_score"),
                    estimated_cost=item.get("estimated_cost"),
                    crowding_risk=item.get("crowding_risk"),
                    late_entry_penalty=item.get("late_entry_penalty"),
                    scanner_family=item.get("scanner_family"),
                    scanner_direction=scanner_direction,
                    regime_tag=regime_tag,
                    all_scores=json.dumps(item.get("all_scores", {})),
                    quote_volume_24h=item.get("quote_volume_24h"),
                    spread_bps=item.get("spread_bps"),
                    ttl_sec=ttl_sec,
                    expires_at=member_expires,
                    inclusion_reason=json.dumps(item.get("reasons", [])),
                    risk_flags=json.dumps(item.get("risk_flags", [])),
                    execution_eligible=item.get("execution_eligible", True),
                    entry_price_usd=entry_price,
                )
                repo.insert_universe_member(member)
                inserted_members.append({
                    "id": member.id,
                    "snapshot_id": snap.id,
                    "symbol": item["symbol"],
                    "venue": item.get("venue") or cfg.market_data.ccxt_exchange,
                    # Carry market_type so signal_emitter labels perp opportunities
                    # correctly. Without this key every emitted opportunity fell back
                    # to "spot" (signal_emitter:70), mislabeling perp/derivatives.
                    "market_type": item.get("market_type", "spot"),
                    "scanner_family": item.get("scanner_family"),
                    "regime_tag": regime_tag,
                    # Direction MUST come from the same source the opportunity
                    # uses (signal_emitter: scanner_direction first). Re-inferring
                    # here dropped the scanner's own direction — momentum/event
                    # candidates whose _infer_direction returned None were logged
                    # to forward outcomes as direction=None (54% of momentum rows),
                    # and the recorded direction could disagree with the emitted
                    # opportunity, double-corrupting win-rate stats.
                    "direction": scanner_direction,
                    "entry_price_usd": entry_price,
                    "spread_bps": item.get("spread_bps"),
                    "funding_rate": item.get("funding_rate"),
                    "total_score": item["total_score"],
                    "learning_candidate_key": item.get("learning_candidate_key"),
                    "learning_exposure_id": item.get("learning_exposure_id"),
                    "expires_at": member_expires,
                })

            # Register forward outcome tracking for all members with valid prices
            try:
                register_pending(inserted_members, entry_ts=now.isoformat())
            except Exception as e:
                logger.warning("Forward outcome registration failed (non-fatal): %s", e)

            try:
                from aletheia.analytics.causal_controls import record_universe_control_cohort
                record_universe_control_cohort(
                    snapshot_id=snap.id,
                    selected_members=inserted_members,
                    scored_candidates=scored,
                    entry_ts=now.isoformat(),
                )
            except Exception as e:
                logger.debug("Universe causal control cohort logging skipped: %s", e)

            # P0-3: settle the full treatment/control arms (every exposed candidate),
            # not only the allocator's survivors, so the contribution verdict is not
            # corrupted by post-treatment selection bias.
            try:
                _register_experiment_candidate_outcomes(scored, cfg, entry_ts=now.isoformat())
            except Exception as e:
                logger.debug("experiment candidate outcome registration skipped: %s", e)

            # Emit deterministic opportunities for high-scoring members (LLM-free path)
            try:
                emit_deterministic_opportunities(
                    inserted_members, snap.id, scored_features=scored,
                    regime=current_regime,
                    short_enabled=cfg.regime.short_enabled,
                )
            except Exception as e:
                logger.warning("Deterministic opportunity emission failed (non-fatal): %s", e)

            repo.update_universe_scanner_run(
                run.id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                snapshot_id=snap.id,
            )
            logger.info(
                "Universe refresh complete: %d scanned → %d filtered → %d selected (snapshot=%s)",
                len(assets), len(filtered), len(allocated), snap.id,
            )
            return snap.id

        except Exception as e:
            logger.error("Universe refresh failed: %s", e, exc_info=True)
            repo.update_universe_scanner_run(
                run.id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                errors=json.dumps([str(e)]),
            )
            return None

    def fallback_static_universe(self) -> list[str]:
        """Return the static fallback symbol list."""
        return load_config().universe.fallback_symbols

    def _fallback_as_members(self, cfg) -> list[dict]:
        """Wrap fallback symbols as member-like dicts for uniform consumption."""
        return [
            {
                "symbol": sym,
                "scanner_family": "core",
                "total_score": 100.0,
                "snapshot_id": None,
                "id": None,
                "asset_id": None,
                "rank": i,
                "tradability_score": 100.0,
                "ttl_sec": 86400,
                "expires_at": None,
                "execution_eligible": 1,
                "inclusion_reason": '["static_fallback"]',
                "risk_flags": "[]",
            }
            for i, sym in enumerate(cfg.universe.fallback_symbols, 1)
        ]
