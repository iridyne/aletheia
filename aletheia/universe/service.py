"""UniverseService — orchestrates the 4-layer universe generation pipeline.

Responsible for: registry sync → feature collection → filter/score → allocate.
Consumers call get_current_universe() to get the active candidate pool.
Falls back to static BTC/ETH/SOL when disabled or on any failure.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
import json
import logging
import random
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

_TWIN_SHADOW_TIMEOUT_SEC = 10.0

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


def _notify_operator_feature_refresh(source: str) -> bool:
    """Wake the decision daemon after candidate feature facts are refreshed."""
    try:
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon

        return bool(notify_pi_daemon(source=source))
    except Exception as exc:
        logger.debug("PI feature-refresh nudge skipped (non-fatal): %s", exc)
        return False


async def _compute_twin_shadow_nonfatal(
    builder,
    inputs: tuple,
    *,
    scanner_run_id: str,
    base_policy=None,
    policy_factory=None,
    policy_kwargs: dict | None = None,
) -> tuple:
    """Run diagnostic scoring off-thread without failing step-v1 refresh."""
    if not inputs:
        return ()
    try:
        if base_policy is None:
            if policy_factory is None:
                raise ValueError("twin_shadow_policy_not_available")
            base_policy = policy_factory(**(policy_kwargs or {}))
        return await asyncio.wait_for(
            asyncio.to_thread(
                builder,
                inputs,
                scanner_run_id=scanner_run_id,
                base_policy=base_policy,
            ),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
    except Exception as shadow_exc:
        logger.warning(
            "Universe twin Shadow batch failed without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return ()


def _finalize_twin_shadow_nonfatal(
    records: tuple,
    *,
    annotator,
    summarizer,
    fine_filtered: list[dict],
    allocated: list[dict],
) -> tuple[tuple, object | None]:
    """Attach authority facts and summarize without becoming a failure path."""
    if not records:
        return (), None
    try:
        annotated = annotator(
            records,
            fine_filtered=fine_filtered,
            allocated=allocated,
        )
        return annotated, summarizer(annotated)
    except Exception as shadow_exc:
        logger.warning(
            "Universe twin Shadow finalization failed without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return (), None


async def _compute_twin_shadow_allocation_nonfatal(
    replay,
    marker,
    records: tuple,
    inputs: tuple,
    **kwargs,
) -> tuple:
    """Replay Shadow allocation off-thread while preserving scoring evidence."""
    if not records:
        return ()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(replay, records, inputs, **kwargs),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
    except Exception as shadow_exc:
        reason = f"shadow_allocation_replay_failed:{type(shadow_exc).__name__}"
        logger.warning(
            "Universe twin Shadow allocation replay unavailable without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return marker(records, reason)


async def _persist_twin_shadow_nonfatal(
    builder,
    persist,
    records: tuple,
    inputs: tuple,
    *,
    policy_factory,
    policy_kwargs: dict[str, object],
    **kwargs,
) -> tuple[int, int]:
    """Build and persist diagnostic evidence without delaying authority failure."""

    if not records:
        return (0, 0)

    def build_and_persist() -> tuple[int, int]:
        policy = policy_factory(**policy_kwargs)
        bundle = builder(records, inputs, policy=policy, **kwargs)
        return persist(bundle.batch, bundle.evidence, list(bundle.outcomes))

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(build_and_persist),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
    except Exception as shadow_exc:
        logger.warning(
            "Universe twin Shadow persistence unavailable without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return (0, 0)


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

    The fingerprint covers every persisted member field that can change the
    downstream candidate's identity, eligibility, TTL, evidence seed, or guard
    treatment. Numeric ranking scores remain excluded because small score jitter
    alone does not change those semantics.
    """

    def _risk_flags(value) -> tuple[str, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = [value]
        if not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(sorted(str(flag) for flag in value))

    def _entry_price(item: dict) -> float | None:
        raw = item.get("last_price")
        if raw is None:
            raw = item.get("entry_price_usd")
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    material: list[tuple] = []
    for rank, item in enumerate(allocated, 1):
        material.append((
            str(item.get("symbol") or ""),
            str(item.get("venue") or ""),
            str(item.get("market_type") or "spot"),
            int(rank),
            str(item.get("scanner_family") or ""),
            str(item.get("scanner_direction") or item.get("direction") or ""),
            bool(item.get("execution_eligible", True)),
            int(item.get("ttl_sec") or 0),
            _risk_flags(item.get("risk_flags")),
            _entry_price(item),
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
    venue_by_asset: dict[str, str] = {}
    materialized = []
    for m in sorted(prev_members, key=lambda row: row.get("rank") or 0):
        asset_id = str(m.get("asset_id") or "")
        if asset_id not in venue_by_asset:
            asset = repo.get_universe_asset(asset_id) if asset_id else None
            venue_by_asset[asset_id] = str((asset or {}).get("venue") or "")
        materialized.append({
            "symbol": m.get("symbol"),
            "venue": venue_by_asset[asset_id],
            "market_type": m.get("market_type"),
            "scanner_family": m.get("scanner_family"),
            "scanner_direction": m.get("scanner_direction"),
            "execution_eligible": m.get("execution_eligible", True),
            "ttl_sec": m.get("ttl_sec"),
            "risk_flags": m.get("risk_flags"),
            "entry_price_usd": m.get("entry_price_usd"),
        })
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


def _register_experiment_candidate_outcomes(
    scored: list[dict],
    cfg,
    *,
    entry_ts: str,
    allocated: list[dict] | None = None,
) -> int:
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
    ``candidate_key``. Persist the final allocator-selection fact on the target horizon so
    contribution evaluates intent-to-treat allocation utility without discarding the
    dropped denominator. Each exposure declares exactly one ``target_horizon``;
    persisting the other three canonical horizons created unconsumed rows. General
    filter evidence still keeps its four-horizon matrix. ``linked_settled_exposure_counts``
    joins the experiment row as ``outcome_candidate_key || ':' || horizon``, which
    equals the exposure's ``outcome_candidate_key`` (``candidate_key:horizon``).
    """
    from aletheia.analytics.filter_evidence import build_candidate_outcomes

    default_venue = getattr(getattr(cfg, "market_data", None), "ccxt_exchange", None)
    selected_keys = {
        str(item["learning_candidate_key"])
        for item in (scored if allocated is None else allocated)
        if item.get("learning_candidate_key")
    }
    outcomes = []
    for item in scored:
        key = item.get("learning_candidate_key")
        if not key:
            continue
        try:
            direction = item.get("scanner_direction") or item.get("direction")
            venue = item.get("venue") or default_venue
            entry_price = item.get("last_price")
            target_horizon = item.get("learning_target_horizon")
            if direction not in {"long", "short"} or not venue or not item.get("symbol"):
                continue
            if float(entry_price or 0) <= 0 or not target_horizon:
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
                    allocation_selected=str(key) in selected_keys,
                    horizons=(str(target_horizon),),
                )
            )
        except Exception:
            logger.debug(
                "experiment candidate outcome registration skipped for %s",
                item.get("symbol"), exc_info=True,
            )
    if not outcomes:
        return 0
    try:
        return repo.insert_filter_candidate_outcomes_batch(outcomes)
    except Exception:
        logger.debug("experiment candidate outcome persistence skipped", exc_info=True)
        return 0


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
        from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
        from aletheia.universe.twin_scoring import (
            TwinShadowInput,
            annotate_authority_selection,
            build_twin_shadow_batch,
            mark_shadow_allocation_unavailable,
            summarize_twin_shadow_records,
        )
        from aletheia.universe.twin_allocation import replay_twin_shadow_allocations
        from aletheia.universe.twin_persistence import build_twin_shadow_bundle
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

            calibration_evidence: dict[str, dict] = {}
            calibration = get_family_multipliers(
                evidence_out=calibration_evidence,
            )
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
            twin_shadow_inputs: list[TwinShadowInput] = []
            for feat in coarse_filtered:
                scanner_result = run_all_scanners(feat)
                authority_score_audit: dict[str, object] = {}
                total = compute_total_score(
                    feat, scanner_result, cfg.universe, calibration=calibration or None,
                    calibration_evidence=calibration_evidence or None,
                    scoring_cohort_key=run.id,
                    scoring_audit_out=authority_score_audit,
                )
                pre_scored.append({**feat, **scanner_result, "total_score": total})
                twin_shadow_inputs.append(TwinShadowInput(
                    feature=dict(feat),
                    authority_scanner_result=dict(scanner_result),
                    authority_total_score=total,
                    authority_score_audit=dict(authority_score_audit),
                ))

            # Task 4: compute every coarse-filter survivor's twin score outside
            # the async event loop and outside every authority collection.
            twin_shadow_records = await _compute_twin_shadow_nonfatal(
                build_twin_shadow_batch,
                tuple(twin_shadow_inputs),
                scanner_run_id=run.id,
                policy_factory=ContinuousScoringPolicy,
                policy_kwargs={
                    "min_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
                    "min_depth_10bps_usd": cfg.universe.min_depth_10bps_usd,
                },
            )

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
            allocator_random_state = random.getstate()
            allocated = allocate_universe(
                unique_scored, cfg.universe,
                regime_state=current_regime.state if current_regime else None,
            )

            twin_shadow_records = await _compute_twin_shadow_allocation_nonfatal(
                replay_twin_shadow_allocations,
                mark_shadow_allocation_unavailable,
                twin_shadow_records,
                tuple(twin_shadow_inputs),
                scanner_run_id=run.id,
                universe_config=cfg.universe,
                app_config=cfg,
                family_tolerances=family_tolerances,
                regime_state=current_regime.state if current_regime else None,
                allocator_random_state=allocator_random_state,
                dedupe=_dedupe_by_symbol_preferred_market,
            )

            twin_shadow_records, shadow_summary = _finalize_twin_shadow_nonfatal(
                twin_shadow_records,
                annotator=annotate_authority_selection,
                summarizer=summarize_twin_shadow_records,
                fine_filtered=scored,
                allocated=allocated,
            )
            if shadow_summary is not None:
                logger.info(
                    "Universe twin Shadow diagnostic only: version=%s candidates=%d "
                    "valid=%d invalid=%d family_changes=%d direction_changes=%d "
                    "point_threshold_crossings=%d pessimistic_threshold_crossings=%d "
                    "budget_exceeded=%d concentrated=%d single_source=%d "
                    "missingness=%d residual_unavailable=%d "
                    "authority_selected=%d point_selected=%d pessimistic_selected=%d "
                    "point_selection_changes=%d pessimistic_selection_changes=%d "
                    "shadow_authority_applied=false",
                    shadow_summary.record_version,
                    shadow_summary.candidate_count,
                    shadow_summary.valid_shadow_count,
                    shadow_summary.invalid_shadow_count,
                    shadow_summary.family_change_count,
                    shadow_summary.direction_change_count,
                    shadow_summary.point_threshold_crossing_count,
                    shadow_summary.pessimistic_threshold_crossing_count,
                    shadow_summary.budget_exceeded_count,
                    shadow_summary.concentrated_evidence_count,
                    shadow_summary.single_source_evidence_count,
                    shadow_summary.missingness_count,
                    shadow_summary.interaction_residual_unavailable_count,
                    shadow_summary.authority_selected_count,
                    shadow_summary.shadow_point_selected_count,
                    shadow_summary.shadow_pessimistic_selected_count,
                    shadow_summary.point_selection_change_count,
                    shadow_summary.pessimistic_selection_change_count,
                )

            now = datetime.now(timezone.utc)
            entry_ts = now.isoformat()
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
                # Slide member plus linked open/queued opportunity expiries. A
                # queued row is revalidated only because this exact source
                # snapshot remains active and semantically unchanged. Without this,
                # a stable-allocation window longer than the family TTL causes
                # every carrier to be ttl_expired on the next ranker scan even
                # though we just re-confirmed the same working set is valid.
                # See repositories.refresh_active_snapshot_ttls for semantics.
                refreshed_opps = 0
                try:
                    refreshed_members, refreshed_opps = repo.refresh_active_snapshot_ttls(
                        prev_snapshot_id, now,
                    )
                    if refreshed_members or refreshed_opps:
                        logger.info(
                            "Universe refresh (suppressed): TTL slid forward — "
                            "members=%d opps=%d snapshot=%s",
                            refreshed_members, refreshed_opps, prev_snapshot_id,
                        )
                except Exception as _e:
                    # Non-fatal: the worst case is the pre-fix behavior (some
                    # carriers hard-reject as ttl_expired next scan).
                    logger.warning(
                        "Universe refresh: TTL slide failed (non-fatal): %s", _e,
                    )
                # Nudge ranker only when the slide made previously-expired
                # opportunities eligible again. Without this the operator has
                # to wait up to 3 min for the next tick to re-see them.
                if refreshed_opps:
                    try:
                        from aletheia.agent_runtime.scheduler import notify_paper_trader
                        notify_paper_trader(source="universe_refresh_ttl_slide")
                    except Exception as _e:
                        logger.debug("paper_trader nudge skipped (non-fatal): %s", _e)
                # Even on reuse, sweep unclaimed open generations left over from
                # earlier snapshots that became superseded before the cascade
                # existed. Idempotent — a no-op once the pool is clean.
                try:
                    cascaded = repo.expire_opportunities_from_superseded_snapshots(market_type)
                    if cascaded:
                        logger.info(
                            "Universe refresh (suppressed): cascaded expiry — %d orphan "
                            "open generation(s) from superseded snapshots marked expired",
                            cascaded,
                        )
                except Exception as _e:
                    logger.warning(
                        "Universe refresh (suppressed): cascade expiry failed (non-fatal): %s", _e,
                    )
                inserted_experiment_outcomes = _register_experiment_candidate_outcomes(
                    pre_scored,
                    cfg,
                    entry_ts=entry_ts,
                    allocated=allocated,
                )
                if inserted_experiment_outcomes:
                    logger.info(
                        "Registered %d experiment candidate outcome row(s) across full arms",
                        inserted_experiment_outcomes,
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
                await _persist_twin_shadow_nonfatal(
                    build_twin_shadow_bundle,
                    repo.insert_universe_twin_shadow_bundle,
                    twin_shadow_records,
                    tuple(twin_shadow_inputs),
                    policy_factory=ContinuousScoringPolicy,
                    policy_kwargs={
                        "min_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
                        "min_depth_10bps_usd": cfg.universe.min_depth_10bps_usd,
                    },
                    scanner_run_id=run.id,
                    authority_snapshot_id=prev_snapshot_id,
                    allocator_random_state=allocator_random_state,
                    universe_config=cfg.universe,
                    enabled_markets=cfg.exchange.enabled_markets,
                    preferred_market_type=cfg.exchange.preferred_market_type,
                    family_tolerances=family_tolerances,
                    regime_state=regime_tag_for_fp,
                    provider_id=(
                        f"{cfg.market_data.provider}:{cfg.market_data.ccxt_exchange}"
                    ),
                    captured_at=entry_ts,
                )
                logger.info(
                    "Universe refresh complete (change-suppressed): reused snapshot=%s",
                    prev_snapshot_id,
                )
                _notify_operator_feature_refresh(
                    "universe_feature_refresh_reused_snapshot"
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
                    learning_candidate_key=item.get("learning_candidate_key"),
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
            pending_outcomes = build_pending(inserted_members, entry_ts=entry_ts)
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

                # The emitter already converts auditable per-member insert failures
                # into terminal evidence. Any exception that escapes the function is
                # therefore a publication failure and must roll back this snapshot.
                emit_deterministic_opportunities(
                    inserted_members, snap.id, scored_features=scored,
                    regime=current_regime,
                    short_enabled=cfg.regime.short_enabled,
                )
                repo.expire_universe_snapshots(market_type, except_id=snap.id)
                # Cascade: end only unclaimed open analytical generations whose
                # parent snapshot just became superseded. Queued Operator carriers
                # retain their origin ids and live by Opportunity.valid_until.
                # Before the soft-TTL filter open generations expired
                # naturally at the family-TTL cliff; now they need explicit
                # cleanup or the pool grows unboundedly (see
                # repositories.expire_opportunities_from_superseded_snapshots).
                try:
                    cascaded = repo.expire_opportunities_from_superseded_snapshots(market_type)
                    if cascaded:
                        logger.info(
                            "Universe refresh: cascaded expiry — %d open generation(s) "
                            "from superseded snapshots marked expired",
                            cascaded,
                        )
                except Exception as _e:
                    logger.warning(
                        "Universe refresh: cascade expiry failed (non-fatal): %s", _e,
                    )

            # Selection is evidence only after the snapshot and Opportunity handoff
            # commit. A failed publication must not look like an applied paper arm.
            inserted_experiment_outcomes = _register_experiment_candidate_outcomes(
                pre_scored,
                cfg,
                entry_ts=entry_ts,
                allocated=allocated,
            )
            if inserted_experiment_outcomes:
                logger.info(
                    "Registered %d experiment candidate outcome row(s) across full arms",
                    inserted_experiment_outcomes,
                )

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
            await _persist_twin_shadow_nonfatal(
                build_twin_shadow_bundle,
                repo.insert_universe_twin_shadow_bundle,
                twin_shadow_records,
                tuple(twin_shadow_inputs),
                policy_factory=ContinuousScoringPolicy,
                policy_kwargs={
                    "min_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
                    "min_depth_10bps_usd": cfg.universe.min_depth_10bps_usd,
                },
                scanner_run_id=run.id,
                authority_snapshot_id=snap.id,
                allocator_random_state=allocator_random_state,
                universe_config=cfg.universe,
                enabled_markets=cfg.exchange.enabled_markets,
                preferred_market_type=cfg.exchange.preferred_market_type,
                family_tolerances=family_tolerances,
                regime_state=regime_tag_for_fp,
                provider_id=(f"{cfg.market_data.provider}:{cfg.market_data.ccxt_exchange}"),
                captured_at=entry_ts,
            )
            # Nudge paper_trader ranker to evaluate the newly-emitted carriers
            # instead of waiting for its 3-min interval tick. Debounced,
            # fire-and-forget, respects scheduler pause / kill switch.
            try:
                from aletheia.agent_runtime.scheduler import notify_paper_trader
                notify_paper_trader(source="universe_refresh_new_snapshot")
            except Exception as _e:
                logger.debug("paper_trader nudge skipped (non-fatal): %s", _e)
            _notify_operator_feature_refresh("universe_feature_refresh_new_snapshot")
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
