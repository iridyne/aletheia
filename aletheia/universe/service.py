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
                existing_symbols = {a.get("symbol") for a in assets}
                # Perp duplicates for volume-ranked symbols that are explicit futures
                # symbols (futures_round2_deferred §5). sync_registry already seeds perp
                # assets (registry.py:117) and collect_features handles them
                # (features.py:336), so this only fills the volume-injection gap, which
                # was spot-only. Gated on usdt_futures so spot-only configs are unchanged.
                from aletheia.data.symbols import to_canonical
                futures_set = {to_canonical(s) for s in (cfg.exchange.futures_symbols or [])}
                inject_perp = cfg.exchange.enabled_markets.get("usdt_futures", False)
                existing_perp = {a.get("symbol") for a in assets if a.get("market_type") == "perp"}
                for sym in vol_symbols[:20]:
                    if sym and sym not in existing_symbols:
                        from aletheia.storage.models import UniverseAsset
                        asset = UniverseAsset(venue=cfg.market_data.ccxt_exchange, market_type="spot", symbol=sym)
                        repo.upsert_universe_asset(asset)
                    if (inject_perp and sym and to_canonical(sym) in futures_set
                            and sym not in existing_perp):
                        from aletheia.storage.models import UniverseAsset
                        repo.upsert_universe_asset(UniverseAsset(
                            venue=cfg.market_data.ccxt_exchange, market_type="perp", symbol=sym,
                        ))
                if vol_symbols:
                    assets = repo.list_universe_assets(venue=cfg.market_data.ccxt_exchange, limit=1000)
            except Exception as e:
                logger.debug("volume_ranking injection skipped: %s", e)

            features_list = await collect_features(assets, cfg)

            filtered = apply_tradability_filters(features_list, cfg.universe)
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

            allocated = allocate_universe(
                scored, cfg.universe,
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

            # Deduplicate by symbol — keep highest total_score per symbol
            seen_symbols: dict[str, dict] = {}
            for item in allocated:
                sym = item["symbol"]
                if sym not in seen_symbols or item["total_score"] > seen_symbols[sym]["total_score"]:
                    seen_symbols[sym] = item
            allocated = sorted(seen_symbols.values(), key=lambda x: x["total_score"], reverse=True)

            regime_tag = current_regime.state if current_regime else None
            inserted_members: list[dict] = []
            for rank, item in enumerate(allocated, 1):
                ttl_sec = min(item.get("ttl_sec", 3600), snapshot_ttl_sec)
                member_expires = (now + timedelta(seconds=ttl_sec)).isoformat()
                entry_price = item.get("last_price")
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
                    "direction": item.get("scanner_direction")
                    or _infer_direction(item.get("scanner_family"), item),
                    "entry_price_usd": entry_price,
                    "total_score": item["total_score"],
                    "expires_at": member_expires,
                })

            # Register forward outcome tracking for all members with valid prices
            try:
                register_pending(inserted_members, entry_ts=now.isoformat())
            except Exception as e:
                logger.warning("Forward outcome registration failed (non-fatal): %s", e)

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
