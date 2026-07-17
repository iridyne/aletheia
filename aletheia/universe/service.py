"""UniverseService — orchestrates the 4-layer universe generation pipeline.

Responsible for: registry sync → feature collection → filter/score → allocate.
Consumers call get_current_universe() to get the active candidate pool.
Falls back to static BTC/ETH/SOL when disabled or on any failure.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    UniverseSnapshot, UniverseMember, UniverseScannerRun,
)

logger = logging.getLogger("aletheia.universe")

# Phase 3 retention (2026-07-17): universe_snapshots + universe_members
# change-suppression. Universe refresh runs every ~10 min but the allocation
# is stable across many refreshes (top-N by score barely moves in calm regimes),
# so every 10-min tick used to write ~50 near-duplicate member rows.
# Suppress when the fingerprint (sorted member list + ranks + regime tag)
# is unchanged AND the previous active snapshot is younger than the heartbeat
# floor; force a fresh write past that floor so downstream freshness gates
# never see a stale snapshot.
_SNAPSHOT_HEARTBEAT_SEC = 55 * 60  # 6 × 10-min refresh cadence, minus jitter.
# alpha_control_observations sampling: reduce from every refresh (~10 min)
# to once per hour to keep the causal-audit table size bounded.
_ALPHA_CONTROL_SAMPLE_SEC = 55 * 60
_publication_metrics_lock = threading.Lock()
_publication_metrics = {
    "transactions": 0,
    "total_ms": 0.0,
    "max_ms": 0.0,
}


def universe_publication_metrics() -> dict[str, float | int]:
    with _publication_metrics_lock:
        count = int(_publication_metrics["transactions"])
        return {
            **_publication_metrics,
            "avg_ms": round(_publication_metrics["total_ms"] / count, 3) if count else 0.0,
        }


def _record_publication_duration(started: float) -> None:
    duration_ms = (time.monotonic() - started) * 1000
    with _publication_metrics_lock:
        _publication_metrics["transactions"] += 1
        _publication_metrics["total_ms"] = round(
            _publication_metrics["total_ms"] + duration_ms,
            3,
        )
        _publication_metrics["max_ms"] = round(
            max(_publication_metrics["max_ms"], duration_ms),
            3,
        )


@contextmanager
def _atomic_universe_publication():
    """Publish snapshot, members and emitted Opportunities as one unit.

    A refresh may be called while its thread already owns a SQLite transaction.
    Repository transactions intentionally join that caller, so use a nested
    SAVEPOINT in that case; otherwise acquire the writer slot up front.
    """
    started = time.monotonic()
    try:
        db = repo.get_db()
        if not db.in_transaction:
            with repo.transaction(immediate=True):
                yield
            return

        savepoint = f"universe_publication_{uuid4().hex}"
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
    finally:
        _record_publication_duration(started)


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


def _member_fingerprint(allocated: list[dict], regime_tag: str | None) -> str:
    """Deterministic hash of a sorted allocation for change-suppression.

    Uses (symbol, market_type, rank, scanner_family) per member plus the
    regime tag. Any reordering, drop/add, or regime flip yields a different
    hash. Numeric scores are intentionally excluded — small score jitter
    between refreshes is common and does not warrant a new snapshot.
    """
    material: list[tuple] = []
    for rank, item in enumerate(allocated, 1):
        material.append((
            str(item.get("symbol") or ""),
            str(item.get("market_type") or "spot"),
            int(rank),
            str(item.get("scanner_family") or ""),
        ))
    payload = json.dumps({"members": material, "regime": regime_tag}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _previous_snapshot_fingerprint(
    market_type: str, current_regime_tag: str | None,
) -> tuple[str | None, str | None, float | None]:
    """Return (fingerprint, snapshot_id, age_seconds) of the newest active snapshot.

    All three may be None if no active snapshot exists yet.
    """
    prev = repo.get_active_universe_snapshot(market_type)
    if not prev:
        return None, None, None
    prev_members = repo.list_universe_members(prev["id"], limit=1000)
    materialized = [
        {
            "symbol": m.get("symbol"),
            "market_type": m.get("market_type"),
            "scanner_family": m.get("scanner_family"),
        }
        for m in sorted(prev_members, key=lambda m: m.get("rank") or 0)
    ]
    prev_regime = None
    for m in prev_members:
        if m.get("regime_tag"):
            prev_regime = m["regime_tag"]
            break
    fp = _member_fingerprint(materialized, prev_regime)
    age = None
    created_at = prev.get("created_at")
    if created_at:
        try:
            parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - parsed).total_seconds()
        except ValueError:
            age = None
    return fp, prev.get("id"), age


def _should_sample_alpha_control(now: datetime) -> bool:
    """Return True if enough time has passed since the last universe-layer sample."""
    try:
        db = repo.get_db()
        row = db.execute(
            "SELECT created_at FROM alpha_control_observations "
            "WHERE layer = 'universe' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return True  # fail-open — a missed sample is worse than a duplicate
    if not row:
        return True
    created_at = row["created_at"] if hasattr(row, "keys") else row[0]
    if not created_at:
        return True
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() >= _ALPHA_CONTROL_SAMPLE_SEC


def _register_experiment_candidate_outcomes(scored: list[dict], cfg, *, entry_ts: str) -> list:
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
    from aletheia.analytics.filter_evidence import build_candidate_outcomes

    default_venue = getattr(getattr(cfg, "market_data", None), "ccxt_exchange", None)
    outcomes = []
    for item in scored:
        key = item.get("learning_candidate_key")
        if not key:
            continue
        try:
            direction = item.get("scanner_direction") or item.get("direction")
            venue = item.get("venue") or default_venue
            entry_price = item.get("last_price")
            if direction not in {"long", "short"} or not venue or not item.get("symbol"):
                continue
            if float(entry_price or 0) <= 0:
                continue
            outcomes.extend(
                build_candidate_outcomes(
                    outcome_candidate_key=key,
                    venue=venue,
                    symbol=item.get("symbol"),
                    market_type=item.get("market_type", "spot"),
                    direction=direction,
                    scanner_family=item.get("scanner_family"),
                    regime_tag=None,
                    entry_price_usd=float(entry_price),
                    entry_spread_bps=item.get("spread_bps"),
                    funding_rate_at_entry=item.get("funding_rate"),
                    entry_ts=entry_ts,
                )
            )
        except Exception:
            logger.debug(
                "experiment candidate outcome registration skipped for %s",
                item.get("symbol"), exc_info=True,
            )
    return outcomes


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
        from aletheia.universe.filters import (
            apply_coarse_tradability_filters,
            apply_fine_tradability_filters,
        )
        from aletheia.universe.filter_advisor import get_family_spread_tolerances
        from aletheia.universe.scanners import run_all_scanners
        from aletheia.universe.allocator import (
            allocate_universe, compute_total_score, get_family_multipliers,
        )
        from aletheia.universe.outcome_recorder import build_pending, settle_due
        from aletheia.universe.signal_emitter import (
            emit_deterministic_opportunities,
            _infer_direction,
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
        attempted_snapshot_id: str | None = None

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

            # Pass 1: coarse filter (stablecoin/blacklist/freshness/volume + extreme spread ≥3×).
            # Borderline spread cases (2×–3× max_spread_bps) pass through for family-aware
            # fine filtering after scanner assigns families.
            coarse_filtered = apply_coarse_tradability_filters(
                features_list, cfg.universe, scanner_run_id=run.id,
            )

            calibration = get_family_multipliers()
            if calibration:
                logger.info("Calibration multipliers: %s", calibration)

            # Load per-family spread tolerances from filter_advisor (empty until data
            # accumulates; defaults to 1.0 = no change from standard 2× threshold).
            try:
                family_tolerances = get_family_spread_tolerances()
            except Exception as _e:
                logger.debug("filter_advisor tolerance fetch failed (non-fatal): %s", _e)
                family_tolerances = {}

            # Scan all coarsely-filtered candidates to get family + direction assignments.
            pre_scored = []
            for feat in coarse_filtered:
                scanner_result = run_all_scanners(feat)
                total = compute_total_score(
                    feat, scanner_result, cfg.universe, calibration=calibration or None,
                    scoring_cohort_key=run.id,
                )
                pre_scored.append({**feat, **scanner_result, "total_score": total})

            # Pass 2: fine spread filter with per-family tolerance. Records rejections
            # via record_filter_decision with scanner_family → filter_candidate_outcomes
            # rows get family populated, closing the calibration feedback loop.
            scored = apply_fine_tradability_filters(
                pre_scored, cfg.universe,
                family_tolerances=family_tolerances or None,
                scanner_run_id=run.id,
            )
            repo.update_universe_scanner_run(run.id, assets_passed=len(scored))

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
            regime_tag_for_fp = current_regime.state if current_regime else None

            # Phase 3 retention: change-suppression on universe_snapshots.
            # If the allocation fingerprint matches the currently-active snapshot
            # AND that snapshot is younger than the heartbeat floor, skip the
            # write and reuse the previous snapshot. This collapses ~5 of every
            # 6 refreshes to a no-op when the top-N is stable.
            new_fp = _member_fingerprint(allocated, regime_tag_for_fp)
            prev_fp, prev_snapshot_id, prev_age_sec = _previous_snapshot_fingerprint(
                market_type, regime_tag_for_fp,
            )
            reuse_previous_snapshot = (
                prev_snapshot_id is not None
                and prev_fp == new_fp
                and prev_age_sec is not None
                and prev_age_sec < _SNAPSHOT_HEARTBEAT_SEC
            )

            if reuse_previous_snapshot:
                logger.debug(
                    "Universe refresh: suppressed snapshot write (fingerprint unchanged, "
                    "prev_age=%.0fs, prev=%s)",
                    prev_age_sec, prev_snapshot_id,
                )
                # Advance the scanner run pointer to the reused snapshot, then
                # short-circuit the publication block: no new members to insert,
                # no downstream cohort logging (freshness monitors read the
                # existing active snapshot as before).
                repo.update_universe_scanner_run(
                    run.id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    snapshot_id=prev_snapshot_id,
                )
                logger.info(
                    "Universe refresh complete (change-suppressed): reused snapshot=%s",
                    prev_snapshot_id,
                )
                return prev_snapshot_id

            snap = UniverseSnapshot(
                market_type=market_type,
                expires_at=expires_at,
                summary=json.dumps({
                    "scanned": len(assets),
                    "filtered": len(scored),
                    "selected": len(allocated),
                }),
            )
            attempted_snapshot_id = snap.id
            # Precompute every member object and JSON payload before acquiring
            # SQLite's single writer slot. The transaction below should spend
            # its time executing SQL, not serializing Python structures.
            regime_tag = current_regime.state if current_regime else None
            member_models: list[UniverseMember] = []
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
                member_models.append(member)
                inserted_members.append({
                    "id": member.id,
                    "snapshot_id": snap.id,
                    "symbol": item["symbol"],
                    "venue": item.get("venue") or cfg.market_data.ccxt_exchange,
                    "market_type": item.get("market_type", "spot"),
                    "scanner_family": item.get("scanner_family"),
                    "regime_tag": regime_tag,
                    "direction": scanner_direction,
                    "entry_price_usd": entry_price,
                    "spread_bps": item.get("spread_bps"),
                    "funding_rate": item.get("funding_rate"),
                    "total_score": item["total_score"],
                    "learning_candidate_key": item.get("learning_candidate_key"),
                    "learning_exposure_id": item.get("learning_exposure_id"),
                    "expires_at": member_expires,
                })
            entry_ts = now.isoformat()
            pending_outcomes = build_pending(inserted_members, entry_ts=entry_ts)
            try:
                experiment_outcomes = _register_experiment_candidate_outcomes(
                    scored, cfg, entry_ts=entry_ts,
                ) or []
            except Exception as e:
                experiment_outcomes = []
                logger.debug("experiment candidate outcome build skipped: %s", e)
            sample_alpha_control = _should_sample_alpha_control(now)
            # Do not expose the new active snapshot before every selected member
            # and its Opportunity handoff are durable. Superseding the previous
            # healthy snapshot is the final write in this publication unit.
            with _atomic_universe_publication():
                repo.insert_universe_snapshot(snap)
                repo.insert_universe_members_batch(member_models)

                # Register forward outcome tracking for all members with valid prices
                try:
                    repo.insert_forward_outcomes_batch(pending_outcomes)
                except Exception as e:
                    logger.warning("Forward outcome registration failed (non-fatal): %s", e)

                # P0-3: settle the full treatment/control arms (every exposed candidate),
                # not only the allocator's survivors, so the contribution verdict is not
                # corrupted by post-treatment selection bias.
                try:
                    inserted_experiment_outcomes = (
                        repo.insert_filter_candidate_outcomes_batch(experiment_outcomes)
                    )
                    if inserted_experiment_outcomes:
                        logger.info(
                            "Registered %d experiment candidate outcome row(s) "
                            "across full arms",
                            inserted_experiment_outcomes,
                        )
                except Exception as e:
                    logger.debug("experiment candidate outcome registration skipped: %s", e)

                # The emitter already converts auditable per-member insert failures
                # into terminal evidence. Any exception that escapes the function is
                # therefore a publication failure and must roll back this snapshot.
                emit_deterministic_opportunities(
                    inserted_members, snap.id, scored_features=scored,
                    regime=current_regime,
                    short_enabled=cfg.regime.short_enabled,
                )
                repo.expire_universe_snapshots(market_type, except_id=snap.id)

            # Alpha-control observations are analytics telemetry, not part of
            # snapshot/Opportunity authority. Persist them after the atomic
            # publication so their JSON/control construction cannot extend the
            # SQLite writer critical section.
            if sample_alpha_control:
                try:
                    from aletheia.analytics.causal_controls import record_universe_control_cohort

                    record_universe_control_cohort(
                        snapshot_id=snap.id,
                        selected_members=inserted_members,
                        scored_candidates=scored,
                        entry_ts=entry_ts,
                    )
                except Exception as e:
                    logger.debug("Universe causal control cohort logging skipped: %s", e)
            else:
                logger.debug(
                    "Universe causal control cohort sampling skipped (hourly cadence)"
                )

            repo.update_universe_scanner_run(
                run.id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                snapshot_id=snap.id,
            )
            logger.info(
                "Universe refresh complete: %d scanned → %d filtered → %d selected (snapshot=%s)",
                len(assets), len(scored), len(allocated), snap.id,
            )
            return snap.id

        except Exception as e:
            logger.error("Universe refresh failed: %s", e, exc_info=True)
            repo.update_universe_scanner_run(
                run.id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                snapshot_id=attempted_snapshot_id,
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
