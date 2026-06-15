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
        result = []
        for m in members:
            if execution_eligible_only and not m.get("execution_eligible", True):
                continue
            if m.get("expires_at") and m["expires_at"] < now:
                continue
            result.append(m)

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
        from aletheia.universe.allocator import allocate_universe, compute_total_score

        cfg = load_config()
        run = UniverseScannerRun()
        repo.insert_universe_scanner_run(run)

        try:
            count = await sync_registry(cfg)
            assets = repo.list_universe_assets(limit=1000)
            repo.update_universe_scanner_run(run.id, assets_scanned=len(assets))

            features_list = await collect_features(assets, cfg)

            filtered = apply_tradability_filters(features_list, cfg.universe)
            repo.update_universe_scanner_run(run.id, assets_passed=len(filtered))

            scored = []
            for feat in filtered:
                scanner_result = run_all_scanners(feat)
                total = compute_total_score(feat, scanner_result, cfg.universe)
                scored.append({**feat, **scanner_result, "total_score": total})

            allocated = allocate_universe(scored, cfg.universe)

            now = datetime.now(timezone.utc)
            expires_at = (now + timedelta(minutes=cfg.universe.refresh_interval_min * 2)).isoformat()
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

            for rank, item in enumerate(allocated, 1):
                ttl_sec = item.get("ttl_sec", 3600)
                member_expires = (now + timedelta(seconds=ttl_sec)).isoformat()
                member = UniverseMember(
                    snapshot_id=snap.id,
                    asset_id=item["asset_id"],
                    symbol=item["symbol"],
                    rank=rank,
                    total_score=item["total_score"],
                    opportunity_score=item.get("opportunity_score"),
                    tradability_score=item.get("tradability_score"),
                    liquidity_score=item.get("liquidity_score"),
                    estimated_cost=item.get("estimated_cost"),
                    crowding_risk=item.get("crowding_risk"),
                    late_entry_penalty=item.get("late_entry_penalty"),
                    scanner_family=item.get("scanner_family"),
                    quote_volume_24h=item.get("quote_volume_24h"),
                    spread_bps=item.get("spread_bps"),
                    ttl_sec=ttl_sec,
                    expires_at=member_expires,
                    inclusion_reason=json.dumps(item.get("reasons", [])),
                    risk_flags=json.dumps(item.get("risk_flags", [])),
                    execution_eligible=item.get("execution_eligible", True),
                )
                repo.insert_universe_member(member)

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
