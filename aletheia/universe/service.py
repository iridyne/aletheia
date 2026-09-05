"""UniverseService — orchestrates the 4-layer universe generation pipeline.

Responsible for: registry sync → feature collection → filter/score → allocate.
Consumers call get_current_universe() to get the active candidate pool.
Static instruments are an explicitly named observation fallback, never Candidates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from aletheia.config.loader import load_config
from aletheia.learning.opportunity_information_policy import POLICY_EPOCH_LABEL
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    UniverseMember,
    UniverseScannerRun,
    UniverseSnapshot,
)
from aletheia.universe.fast_stream import fast_market_data_unavailability_reason
from aletheia.universe.candidate_opportunity import (
    legacy_candidate_opportunity_context,
    target_candidate_opportunity_context,
)

logger = logging.getLogger("aletheia.universe")

_TWIN_SHADOW_TIMEOUT_SEC = 10.0
_terminal_candidate_selection_policy_fingerprint: str | None = None
_terminal_candidate_selection_policy_lock = threading.Lock()
_candidate_shadow_tasks: set[asyncio.Task] = set()

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


def _execution_eligible(member: object) -> bool:
    """Only execution-eligible Candidates may enter the planning queue."""
    if isinstance(member, dict):
        return bool(member.get("execution_eligible", True))
    return bool(getattr(member, "execution_eligible", True))


def _prepare_planning_requalifications(members) -> dict[str, dict | None]:
    proofs = {}
    for member in members:
        candidate = member if isinstance(member, dict) else member.model_dump()
        try:
            proofs[candidate["id"]] = repo.prepare_candidate_requalification(candidate)
        except ValueError as exc:
            logger.info("Candidate requalification unavailable: candidate=%s reason=%s", candidate["id"], exc)
            proofs[candidate["id"]] = None
    return proofs



def _ensure_planning_generations(
    members,
    *,
    soft_lease_sec: int,
    max_attention_age_sec: int,
    now: datetime,
    requalifications: dict[str, dict | None] | None = None,
) -> tuple[set[str], set[str], int]:
    """Publish planning authority only for execution-eligible Candidates."""

    selected: set[str] = set()
    created_ids: set[str] = set()
    created_count = 0
    for member in members:
        if not _execution_eligible(member):
            continue
        candidate_id = str(member["id"] if isinstance(member, dict) else member.id)
        try:
            generation, created = repo.ensure_candidate_planning_generation(
                candidate_id,
                soft_lease_sec=soft_lease_sec,
                max_attention_age_sec=max_attention_age_sec,
                now=now,
                requalification=requalifications.get(candidate_id) if requalifications is not None else None,
            )
        except ValueError as exc:
            logger.info(
                "Candidate unavailable to Entry Planner: candidate=%s reason=%s",
                candidate_id,
                exc,
            )
            continue
        generation_id = str(generation["id"])
        selected.add(generation_id)
        if created:
            created_ids.add(generation_id)
            created_count += 1
    return selected, created_ids, created_count


async def _compute_twin_shadow_nonfatal(
    builder,
    inputs: tuple,
    *,
    scanner_run_id: str,
    base_policy=None,
    policy_factory=None,
    policy_kwargs: dict | None = None,
    policy_keyword: str = "base_policy",
):
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
                **{policy_keyword: base_policy},
            ),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
    except Exception as shadow_exc:
        logger.warning(
            "Universe twin Shadow batch failed without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return ()


async def _compute_relative_rank_nonfatal(*args, **kwargs):
    return await _compute_twin_shadow_nonfatal(
        *args,
        policy_keyword="policy",
        **kwargs,
    )


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


async def _compute_relative_rank_allocation_nonfatal(
    replay,
    annotator,
    batch,
    inputs: tuple,
    *,
    authority_selected: list[dict],
    **kwargs,
):
    if not batch:
        return None
    try:
        replayed = await asyncio.wait_for(
            asyncio.to_thread(replay, batch, inputs, **kwargs),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
        return annotator(replayed, authority_selected)
    except Exception as shadow_exc:
        logger.warning(
            "Universe relative-rank Shadow replay unavailable without authority impact: %s",
            type(shadow_exc).__name__,
        )
        return None


async def _persist_twin_shadow_nonfatal(
    builder,
    persist,
    records: tuple,
    inputs: tuple,
    *,
    policy_factory,
    policy_kwargs: dict[str, object],
    capture_due=None,
    min_interval_sec: int = 55 * 60,
    **kwargs,
) -> int:
    """Build and persist diagnostic evidence without delaying authority failure."""

    if not records:
        return 0

    def build_and_persist() -> int:
        captured_at = str(kwargs.get("captured_at") or "")
        if (
            capture_due is not None
            and captured_at
            and not capture_due(
                captured_at,
                min_interval_sec=min_interval_sec,
            )
        ):
            return 0
        policy = policy_factory(**policy_kwargs)
        bundle = builder(records, inputs, policy=policy, **kwargs)
        if not hasattr(bundle, "evidence"):
            return persist(
                bundle.batch,
                outcomes=bundle.outcomes,
                min_interval_sec=min_interval_sec,
            )
        return persist(
            bundle.batch,
            bundle.evidence,
            outcomes=bundle.outcomes,
            min_interval_sec=min_interval_sec,
        )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(build_and_persist),
            timeout=_TWIN_SHADOW_TIMEOUT_SEC,
        )
    except Exception as shadow_exc:
        logger.warning(
            "Universe twin Shadow persistence unavailable without authority impact: %s: %s (scanner_run_id=%s)",
            type(shadow_exc).__name__,
            shadow_exc,
            kwargs.get("scanner_run_id", "not_available"),
        )
        return 0
async def _persist_relative_rank_shadow_nonfatal(*args, **kwargs) -> int:
    return await _persist_twin_shadow_nonfatal(*args, **kwargs)


def _build_candidate_selection_bundle(
    *,
    population: list[dict],
    legacy_scored: list[dict],
    legacy_allocated: list[dict],
    scanner_run_id: str,
    captured_at: str,
    cfg,
    family_spread_tolerances: dict[str, float] | None = None,
    family_spread_evidence: dict[str, object] | None = None,
    authority_applied: bool = False,
    qualification_evidence: dict | None = None,
    training_diagnostics_out: dict[str, object] | None = None,
):
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )

    action_routes = {
        (
            str(item.get("feature_snapshot_id") or ""),
            str(item.get("venue") or ""),
            str(item.get("market_type") or "spot"),
            str(item.get("symbol") or ""),
        ): fast_market_data_unavailability_reason(
            cfg,
            str(item.get("venue") or ""),
            str(item.get("market_type") or "spot"),
        )
        for item in population
    }
    selection_policy = candidate_selection_policy_identity(cfg)
    training_cutoff = captured_at
    return build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id=scanner_run_id,
        captured_at=captured_at,
        universe_config=cfg.universe,
        legacy_scored=legacy_scored,
        legacy_allocated=legacy_allocated,
        action_route_reasons=action_routes,
        historical_outcomes=load_candidate_selection_outcomes(
            selection_policy=selection_policy,
            diagnostics_out=training_diagnostics_out,
            settled_through=training_cutoff,
        ),
        estimator_training_cutoff=training_cutoff,
        selection_policy=selection_policy,
        authority_applied=authority_applied,
        qualification_evidence=qualification_evidence,
    )

def _candidate_selection_reason_counts(bundle) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evidence in bundle.evidence:
        decision = json.loads(evidence.compact_output_json)["candidate_selection"]
        for reason in [decision.get("admission_status"), *(decision.get("hard_gate", {}).get("reason_codes") or [])]:
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return counts


async def _persist_candidate_selection_shadow_nonfatal(
    *,
    population: list[dict],
    legacy_scored: list[dict],
    legacy_allocated: list[dict],
    scanner_run_id: str,
    captured_at: str,
    cfg,
    family_spread_tolerances: dict[str, float] | None = None,
    family_spread_evidence: dict[str, object] | None = None,
    legacy_control_restricted: bool = False,
) -> dict[str, object]:
    """Persist #2729 full-denominator evidence without joining authority failure."""

    if legacy_control_restricted:
        logger.info(
            "Candidate Selection Shadow skipped while Learning controls the legacy arm"
        )
        result = {"status": "restricted", "record_count": 0, "reason": "legacy_control_restricted"}
        _update_candidate_shadow_status(scanner_run_id, result)
        return result
    from aletheia.universe.candidate_selection_shadow import (
        CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC,
    )

    try:
        due = repo.universe_twin_shadow_capture_due(
            captured_at, min_interval_sec=CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC,
        )
    except Exception:
        result = {"status": "unavailable", "record_count": 0, "reason": "capture_cadence_unavailable"}
        _update_candidate_shadow_status(scanner_run_id, result)
        return result
    if not due:
        result = {"status": "not_due", "record_count": 0, "reason": None}
        _update_candidate_shadow_status(scanner_run_id, result)
        return result

    deadline = time.monotonic() + _TWIN_SHADOW_TIMEOUT_SEC

    def build_and_persist() -> dict[str, object]:
        global _terminal_candidate_selection_policy_fingerprint

        from aletheia.universe.candidate_eligibility import canonical_fingerprint
        from aletheia.universe.candidate_selection_authority import (
            candidate_selection_policy_identity,
        )
        from aletheia.universe.candidate_selection_evaluation import (
            evaluate_candidate_selection_policy,
        )

        current_policy = candidate_selection_policy_identity(
            cfg,
            family_spread_tolerances=family_spread_tolerances,
            family_spread_evidence=family_spread_evidence,
        )
        policy_fingerprint = canonical_fingerprint(current_policy)
        with _terminal_candidate_selection_policy_lock:
            if policy_fingerprint == _terminal_candidate_selection_policy_fingerprint:
                return {"status": "terminal", "record_count": 0, "reason": None}
            qualification = evaluate_candidate_selection_policy(as_of_at=captured_at)
            if (
                qualification["verdict"] in {"positive", "negative", "neutral", "invalid"}
                and qualification.get("selection_policy") == current_policy
            ):
                _terminal_candidate_selection_policy_fingerprint = policy_fingerprint
                return {"status": "terminal", "record_count": 0, "reason": None}
        training_diagnostics: dict[str, object] = {}
        bundle = _build_candidate_selection_bundle(
            population=population,
            scanner_run_id=scanner_run_id,
            captured_at=captured_at,
            legacy_scored=legacy_scored,
            legacy_allocated=legacy_allocated,
            cfg=cfg,
            family_spread_tolerances=family_spread_tolerances,
            family_spread_evidence=family_spread_evidence,
            training_diagnostics_out=training_diagnostics,
        )
        reason_counts = _candidate_selection_reason_counts(bundle)
        with repo.transaction(immediate=True):
            if time.monotonic() >= deadline:
                return {"status": "unavailable", "record_count": 0, "reason": "capture_deadline_elapsed"}
            count = repo.insert_universe_twin_shadow_bundle(
                bundle.batch, bundle.evidence, outcomes=bundle.outcomes,
                min_interval_sec=CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC,
            )
            repo.execute_write(
                "UPDATE universe_scanner_runs SET metrics=json_set(COALESCE(metrics,'{}'), "
                "'$.candidate_path.selection_reason_counts',json(?),'$.candidate_path.policy_epoch',?, "
                "'$.candidate_path.data.feature_acquisition_excluded',?) WHERE id=?",
                (json.dumps(reason_counts), bundle.batch.policy_epoch_id, training_diagnostics.get("feature_acquisition_excluded", 0), scanner_run_id),
            )
        return {"status": "captured" if count else "not_due", "record_count": count, "reason": None}

    def completed_worker():
        try:
            result = build_and_persist()
        except Exception:
            logger.warning("Candidate Selection Shadow unavailable", exc_info=True)
            result = {"status": "unavailable", "record_count": 0, "reason": "capture_failed"}
        _update_candidate_shadow_status(scanner_run_id, result)
        return result

    task = asyncio.create_task(asyncio.to_thread(completed_worker))
    _candidate_shadow_tasks.add(task)
    task.add_done_callback(_candidate_shadow_tasks.discard)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_TWIN_SHADOW_TIMEOUT_SEC)
    except TimeoutError:
        result = {"status": "pending", "record_count": 0, "reason": "capture_completion_unknown"}
        update = asyncio.create_task(asyncio.to_thread(_update_candidate_shadow_status, scanner_run_id, result))
        _candidate_shadow_tasks.add(update)
        update.add_done_callback(_candidate_shadow_tasks.discard)
        return result


def _update_candidate_shadow_status(scanner_run_id: str, result: dict) -> None:
    try:
        repo.execute_write(
            "UPDATE universe_scanner_runs SET metrics=json_set(COALESCE(metrics,'{}'), "
            "'$.candidate_path.shadow_capture_status', ?, '$.candidate_path.shadow_capture_reason', ?) WHERE id=? "
            "AND (? != 'pending' OR json_extract(metrics, '$.candidate_path.shadow_capture_status')='pending')",
            (result["status"], result["reason"], scanner_run_id, result["status"]),
        )
    except Exception:
        logger.warning("Shadow scanner completion unavailable", exc_info=True)


@contextmanager
def _atomic_universe_publication():
    """Publish snapshot, Candidates, and derived setup facts as one unit.

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


def _assert_candidate_selection_publication_authority(
    expected_policy_id: str,
    expected_selection_policy: dict[str, object],
) -> None:
    """Revalidate target authority while holding the publication writer permit."""

    current = load_config(use_cache=False, persist_migrations=False)
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity, candidate_selection_authority_block_reason,
    )
    if (
        candidate_selection_authority_block_reason(current) is not None
        or current.universe.candidate_selection_policy_epoch != expected_policy_id
        or candidate_selection_policy_identity(current) != expected_selection_policy
    ):
        raise RuntimeError("candidate_selection_authority_changed_before_publication")


def _persist_attempt_learning_exposures(writes: list[dict]) -> None:
    """Join deferred scoring evidence to the Universe publication transaction."""
    from aletheia.learning.ledger import insert_exposure
    from aletheia.observation_lab.canonical import append_delivery_receipt

    for write in writes:
        canonical_receipt = write.get("canonical_receipt")
        if canonical_receipt is not None:
            append_delivery_receipt(**canonical_receipt, db=repo.get_db())
            continue
        inserted = insert_exposure(**write)
        if inserted is None:
            raise RuntimeError("learning_experiment_closed_before_publication")


def _volume_ranked_instruments(rows: list, default_venue: str):
    """Normalize structured and legacy volume-ranking rows to exact instruments."""
    from aletheia.data.symbols import MARKET_PERP, MARKET_SPOT, InstrumentId

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


def _member_fingerprint(
    allocated: list[dict],
    regime_tag: str | None,
    *,
    policy_id: str = "legacy_total_score_v1",
) -> str:
    """Deterministic hash of a sorted allocation for change-suppression.

    The fingerprint covers every persisted member field that can change the
    downstream candidate's identity, eligibility, TTL, evidence seed, or guard
    treatment. Legacy score jitter remains excluded; target Utility is included
    because it is named, persisted owner evidence rather than compatibility rank.
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
        material.append(
            (
                str(item.get("symbol") or ""),
                str(item.get("venue") or ""),
                str(item.get("market_type") or "spot"),
                int(rank),
                str(item.get("scanner_family") or ""),
                str(item.get("scanner_direction") or item.get("direction") or ""),
                str(item.get("candidate_selection_policy") or "legacy_total_score_v1"),
                str(item.get("candidate_selection_policy_epoch") or ""),
                item.get("candidate_selection_utility_pct"),
                str(item.get("opportunity_mechanism") or ""),
                str(item.get("opportunity_mechanism_version") or ""),
                str(item.get("opportunity_source_component") or ""),
                str(item.get("opportunity_source_cutoff_at") or ""),
                str(item.get("opportunity_evidence_hash") or ""),
                str(
                    (item.get("_candidate_opportunity_record") or {}).get("source_record_hash")
                    if isinstance(item.get("_candidate_opportunity_record"), dict) else ""
                ),
                bool(item.get("execution_eligible", True)),
                int(item.get("ttl_sec") or 0),
                _risk_flags(item.get("risk_flags")),
                _entry_price(item),
                json.dumps(
                    item.get("instrument_background"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    item.get("analysis_context"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                (regime_tag if (item.get("analysis_context") or {}).get("kind") == "crypto" else None),
            )
        )
    payload = json.dumps(
        {
            "information_policy_epoch": POLICY_EPOCH_LABEL,
            "candidate_selection_policy": policy_id,
            "members": material,
            "regime": regime_tag,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _previous_snapshot_fingerprint(
    market_type: str,
) -> tuple[str | None, str | None, float | None]:
    """Return (fingerprint, snapshot_id, age_seconds) of the newest active snapshot.

    Legacy snapshots did not freeze their policy epoch and background-aware
    fingerprint, so they deliberately cannot be reused.
    """
    prev = repo.get_active_universe_snapshot(market_type)
    if not prev:
        return None, None, None
    try:
        summary = prev.get("summary") or {}
        summary = json.loads(summary) if isinstance(summary, str) else summary
    except (TypeError, ValueError, json.JSONDecodeError):
        summary = {}
    fp = None
    if (
        isinstance(summary, dict)
        and summary.get("information_policy_epoch") == POLICY_EPOCH_LABEL
        and isinstance(summary.get("member_fingerprint"), str)
    ):
        fp = summary["member_fingerprint"]
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


def _build_experiment_candidate_outcomes(
    scored: list[dict],
    *,
    entry_ts: str,
    allocated: list[dict] | None = None,
) -> list:
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
        direction = item.get("scanner_direction") or item.get("direction")
        venue = item.get("venue")
        entry_price = item.get("last_price")
        target_horizon = item.get("learning_target_horizon")
        if (
            direction not in {"long", "short"}
            or not isinstance(venue, str)
            or not venue.strip()
            or not item.get("symbol")
            or float(entry_price or 0) <= 0
            or not target_horizon
        ):
            raise RuntimeError("universe_learning_outcome_identity_incomplete")
        built = build_candidate_outcomes(
                    outcome_candidate_key=key,
                    venue=venue.strip(),
                    symbol=item.get("symbol"),
                    market_type=item.get("market_type", "spot"),
                    direction=direction,
                    scanner_family=item.get("scanner_family"),
                    regime_tag=None,
                    entry_price_usd=float(entry_price),
                    entry_spread_bps=item.get("spread_bps"),
                    funding_rate_at_entry=item.get("funding_rate"),
                    entry_ts=str(item.get("learning_outcome_entry_ts") or entry_ts),
                    allocation_selected=str(key) in selected_keys,
                    horizons=(str(target_horizon),),
                    rsi_1h_at_entry=item.get("rsi_14"),
                    rsi_15m_at_entry=item.get("rsi_15m"),
                    return_24h_at_entry=item.get("return_24h"),
                    direction_exhaustion_signal=item.get("direction_exhaustion_signal"),
                )
        if len(built) != 1:
            raise RuntimeError("universe_learning_outcome_build_incomplete")
        outcomes.extend(built)
    return outcomes


def _register_experiment_candidate_outcomes(
    scored: list[dict],
    *,
    entry_ts: str,
    allocated: list[dict] | None = None,
) -> int:
    """Compatibility writer for callers that still invoke the old helper.

    The refresh pipeline builds and persists outcomes in one explicit flow.  A
    few older tests/tools call the former register helper directly; preserve
    that API without creating another implementation path.  Lab authority
    deliberately does not write the legacy Main table.
    """
    try:
        from aletheia.observation_lab.canonical import get_authority_source

        if get_authority_source().source == "lab":
            return 0
    except sqlite3.OperationalError as exc:
        # Pre-V2 databases have no durable authority row and are unambiguously
        # legacy; any other SQLite failure remains fail-closed.
        if "no such table: observation_authority_state" not in str(exc):
            return 0
    except Exception:
        return 0
    valid_scored = []
    for item in scored:
        try:
            if (
                item.get("scanner_direction") or item.get("direction")
            ) not in {"long", "short"} or not isinstance(item.get("venue"), str):
                continue
            if (
                not item["venue"].strip()
                or not item.get("symbol")
                or float(item.get("last_price") or 0) <= 0
                or not item.get("learning_target_horizon")
            ):
                continue
        except (TypeError, ValueError):
            continue
        valid_scored.append(item)
    outcomes = _build_experiment_candidate_outcomes(
        valid_scored,
        entry_ts=entry_ts,
        allocated=allocated,
    )
    return int(repo.insert_filter_candidate_outcomes_batch(outcomes))


def _capture_universe_discovery_nonfatal(
    *,
    scanner_run_id: str,
    population: list[dict],
    allocated: list[dict],
    entry_ts: str,
    regime_tag: str | None,
    cfg,
) -> None:
    """Observe one completed run without affecting Universe publication."""
    try:
        from aletheia.universe.discovery_benchmark import capture_completed_run

        result = capture_completed_run(
            scanner_run_id=scanner_run_id,
            population=population,
            allocated=allocated,
            entry_ts=entry_ts,
            regime_tag=regime_tag,
            cfg=cfg,
        )
        if result.get("accepted") and not result.get("idempotent"):
            logger.info(
                "Universe Discovery Benchmark captured run=%s batch=%s outcomes=%s",
                scanner_run_id,
                result.get("batch_id"),
                result.get("outcomes_inserted"),
            )
    except Exception as exc:
        logger.warning("Universe Discovery Benchmark capture failed (non-fatal): %s", exc)


def _active_paper_learning_identity(path_id: str) -> tuple[bool, dict | None]:
    """Return one canonical admitted Paper identity, failing closed otherwise."""
    try:
        from aletheia.learning.hypotheses import hypothesis_fingerprint
        from aletheia.learning.ledger import get_record, list_open_experiments
        from aletheia.learning.registry import get_learning_path

        paper = [
            row
            for row in list_open_experiments(path_id=path_id)
            if str(row.get("stage") or "") in {"paper_10", "paper_25", "paper_50", "paper_active"}
        ]
        if not paper:
            return False, None
        if len(paper) != 1:
            logger.error(
                "Learning identity restriction found %d active Paper experiments for %s",
                len(paper),
                path_id,
            )
            return True, None
        hypothesis = get_record("learning_hypotheses", str(paper[0]["hypothesis_id"]))
        if not hypothesis or hypothesis.get("path_id") != path_id:
            raise ValueError("active Paper hypothesis is missing or belongs to another path")
        scope = json.loads(str(hypothesis.get("scope_json") or "null"))
        action = json.loads(str(hypothesis.get("action_json") or "null"))
        if not isinstance(scope, dict) or not isinstance(action, dict):
            raise ValueError("active Paper scope/action is not an object")
        if (
            set(scope) != {"family", "direction", "market_type"}
            or scope.get("family") not in {"momentum", "mean_reversion", "volatility", "derivatives"}
            or scope.get("direction") not in {"long", "short"}
            or scope.get("market_type") not in {"spot", "perp"}
        ):
            raise ValueError("active Paper UFC scope is not canonical")
        target_horizon = str(hypothesis.get("target_horizon") or "")
        source_cohort = str(hypothesis.get("source_type") or "")
        behavior_version = str(hypothesis.get("behavior_contract_version") or "")
        schema_version = int(hypothesis["fingerprint_schema_version"])
        if not target_horizon or not source_cohort or not behavior_version:
            raise ValueError("active Paper hypothesis identity is incomplete")
        if get_learning_path(path_id).behavior_contract_version != behavior_version:
            raise ValueError("active Paper behavior contract is not current")
        fingerprint = hypothesis_fingerprint(
            path_id=path_id,
            scope=scope,
            action=action,
            target_horizon=target_horizon,
            source_cohort=source_cohort,
            behavior_contract_version=behavior_version,
            schema_version=schema_version,
        )
        if fingerprint != hypothesis.get("fingerprint"):
            raise ValueError("active Paper hypothesis fingerprint mismatch")
        return True, {
            "experiment_id": str(paper[0]["experiment_id"]),
            "fingerprint": fingerprint,
            "scope": scope,
            "action": action,
            "target_horizon": target_horizon,
            "source_cohort": source_cohort,
            "behavior_contract_version": behavior_version,
            "fingerprint_schema_version": schema_version,
        }
    except Exception:
        logger.exception(
            "Learning identity restriction unavailable for %s; suppressing influence",
            path_id,
        )
        return True, None


class UniverseService:
    """Main orchestrator for the dynamic universe pipeline."""

    def get_current_universe(
        self,
        market_type: str = "crypto",
        execution_eligible_only: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Return only durable current universe members.

        Returns list of dicts with at minimum: symbol, scanner_family, total_score,
        snapshot_id, member_id, ttl_sec, expires_at, execution_eligible.
        """
        from aletheia.universe.candidate_selection_authority import (
            recover_candidate_selection_authority_transition,
        )

        recover_candidate_selection_authority_transition()
        cfg = load_config()

        try:
            repo.reconcile_audit_event_outbox((
                "candidate_selection_authority_admitted",
                "candidate_selection_authority_rolled_back",
            ))
        except Exception as exc:
            logger.warning("Candidate authority audit mirror retry failed: %s", exc)
        if not cfg.universe.enabled:
            return []

        snap = repo.get_active_universe_snapshot(market_type)
        if not snap:
            return []

        members = repo.list_universe_members(snap["id"], limit=limit or 50)
        if not members:
            return []

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


        if limit and len(result) > limit:
            result = result[:limit]

        return result

    async def refresh(self, market_type: str = "crypto") -> str | None:
        """Run the full 4-layer pipeline and produce a new universe snapshot.

        Returns the snapshot_id on success, None on failure.
        """
        from aletheia.universe.allocator import decorate_universe_candidates
        from aletheia.universe.features import collect_features
        from aletheia.universe.filters import (
            apply_coarse_tradability_filters,
            apply_fine_tradability_filters,
        )
        from aletheia.universe.outcome_recorder import build_pending, settle_due
        from aletheia.universe.registry import sync_registry
        from aletheia.universe.scanners import run_all_scanners
        from aletheia.universe.signal_emitter import _infer_direction

        from aletheia.universe.candidate_selection_authority import (
            recover_candidate_selection_authority_transition,
        )

        recover_candidate_selection_authority_transition()
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
            from aletheia.regime.detector import debounced_detect
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
                    current_regime.state,
                    current_regime.confidence,
                    current_regime.long_bias,
                    current_regime.short_bias,
                )
        except Exception as e:
            logger.warning("Regime detection failed (non-fatal): %s", e)

        run = UniverseScannerRun()
        repo.insert_universe_scanner_run(run)
        data_diagnostics: dict[str, object] = {}
        path_metrics = {
            "selection_policy": cfg.universe.candidate_selection_policy,
            "policy_epoch": cfg.universe.candidate_selection_policy_epoch,
            "selected_count": 0, "planning_supported_count": 0, "observe_only_count": 0,
            "selection_reason_counts": {}, "data": data_diagnostics,
            "shadow_capture_status": "pending", "shadow_capture_reason": None,
        }
        attempted_snapshot_id: str | None = None

        try:
            await sync_registry(cfg)
            assets = repo.list_universe_assets(
                venue=cfg.market_data.ccxt_exchange,
                limit=1000,
            )
            priority_assets = repo.list_candidate_planning_refresh_assets(
                venue=cfg.market_data.ccxt_exchange,
            )
            priority_asset_ids = {str(asset["id"]) for asset in priority_assets if asset.get("id")}
            if priority_asset_ids:
                assets = priority_assets + [
                    asset for asset in assets if str(asset.get("id") or "") not in priority_asset_ids
                ]
            repo.update_universe_scanner_run(run.id, assets_scanned=len(assets))

            # Inject volume-ranking symbols as additional candidates
            try:
                from aletheia.agent_runtime.scheduler import get_latest_volume_ranking

                vol = get_latest_volume_ranking()
                vol_symbols = vol.get("symbols") or [] if vol else []
                ranked_instruments = _volume_ranked_instruments(
                    vol_symbols[:20],
                    cfg.market_data.ccxt_exchange,
                )
                rank_index = {
                    (item.exchange, item.symbol, item.market_type): index
                    for index, item in enumerate(ranked_instruments)
                }
                if rank_index:
                    assets.sort(
                        key=lambda asset: (
                            0 if str(asset.get("id") or "") in priority_asset_ids else 1,
                            rank_index.get(
                                (
                                    asset.get("venue"),
                                    asset.get("symbol"),
                                    asset.get("market_type"),
                                ),
                                len(rank_index),
                            ),
                        )
                    )
            except Exception as e:
                logger.debug("volume_ranking injection skipped: %s", e)

            feature_kwargs = {"priority_asset_ids": priority_asset_ids} if priority_asset_ids else {}
            features_list = await collect_features(
                assets,
                cfg,
                scanner_run_id=run.id,
                diagnostics_out=data_diagnostics,
                **feature_kwargs,
            )

            # Pass 1: coarse filter (stablecoin/blacklist/freshness/volume + extreme spread ≥3×).
            # Borderline spread cases (2×–3× max_spread_bps) pass through for family-aware
            # fine filtering after scanner assigns families.
            coarse_filtered = apply_coarse_tradability_filters(
                features_list,
                cfg.universe,
                scanner_run_id=run.id,
            )

            family_tolerances = {}
            family_spread_evidence = {}

            # Scan all coarsely-filtered candidates to get family + direction assignments.
            pre_scored = []
            deferred_exposure_writes: list[dict] = []
            for feat in coarse_filtered:
                scanner_result = run_all_scanners(feat)
                pre_scored.append({**feat, **scanner_result})

            # Pass 2: fine spread filter with per-family tolerance. Records rejections
            # via record_filter_decision with scanner_family → filter_candidate_outcomes
            # rows get family populated, closing the calibration feedback loop.
            scored = apply_fine_tradability_filters(
                pre_scored,
                cfg.universe,
                family_tolerances=family_tolerances or None,
                scanner_run_id=run.id,
            )
            repo.update_universe_scanner_run(run.id, assets_passed=len(scored))

            # Utility owns cross-market selection; retain every physical instrument.
            unique_scored = scored
            legacy_allocated = []
            now = datetime.now(timezone.utc)
            entry_ts = now.isoformat()
            allocated = legacy_allocated
            candidate_selection_authority_bundle = None
            if cfg.universe.candidate_selection_policy == "candidate_selection_utility_v1":
                from aletheia.universe.candidate_selection_authority import (
                    candidate_selection_authority_block_reason,
                    candidate_selection_overlap_block_reason,
                )
                from aletheia.universe.candidate_selection_shadow import (
                    target_admitted_identity_keys,
                    target_candidates_from_bundle,
                )

                overlap_block_reason = candidate_selection_overlap_block_reason()
                if overlap_block_reason is not None:
                    raise RuntimeError(overlap_block_reason)
                from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
                qualification = {"selection_policy": candidate_selection_policy_identity(cfg)}
                block_reason = candidate_selection_authority_block_reason(cfg)
                if block_reason is not None:
                    raise RuntimeError(block_reason)
                candidate_selection_authority_bundle = await asyncio.wait_for(
                    asyncio.to_thread(
                        _build_candidate_selection_bundle,
                        population=features_list,
                        legacy_scored=unique_scored,
                        legacy_allocated=legacy_allocated,
                        scanner_run_id=run.id,
                        captured_at=entry_ts,
                        cfg=cfg,
                        family_spread_tolerances=family_tolerances,
                        family_spread_evidence=family_spread_evidence,
                        authority_applied=True,
                        qualification_evidence=qualification,
                        training_diagnostics_out=data_diagnostics,
                    ),
                    timeout=_TWIN_SHADOW_TIMEOUT_SEC,
                )
                if (
                    candidate_selection_authority_bundle.batch.policy_epoch_id
                    != cfg.universe.candidate_selection_policy_epoch
                ):
                    raise RuntimeError("candidate_selection_runtime_policy_epoch_mismatch")
                allocated = target_candidates_from_bundle(
                    candidate_selection_authority_bundle,
                    pre_scored,
                )
                decorate_universe_candidates(allocated, cfg.universe)
                eligible_attention_identities = target_admitted_identity_keys(
                    candidate_selection_authority_bundle,
                )
            else:
                raise RuntimeError("candidate_selection_legacy_runtime_removed")

            path_metrics["selected_count"] = len(allocated)
            path_metrics["shadow_capture_status"] = "terminal" if candidate_selection_authority_bundle is not None else "pending"
            for item in allocated:
                record = item.get("_candidate_opportunity_record")
                context = (target_candidate_opportunity_context(
                    candidate_id="diagnostic", decision=record["decision"],
                    policy_epoch=str(item["candidate_selection_policy_epoch"]),
                    source_record_hash=record["source_record_hash"], detected_at=entry_ts,
                ) if isinstance(record, dict) else legacy_candidate_opportunity_context(
                    candidate_id="diagnostic", candidate=item, detected_at=entry_ts,
                ))
                capability = context.opportunity["capability_status"] if context is not None else "unavailable"
                if capability == "supported":
                    path_metrics["planning_supported_count"] += 1
                elif capability == "observe_only":
                    path_metrics["observe_only_count"] += 1
            if candidate_selection_authority_bundle is not None:
                path_metrics["selection_reason_counts"] = _candidate_selection_reason_counts(candidate_selection_authority_bundle)
            snapshot_ttl_sec = int(cfg.universe.snapshot_ttl_hours * 3600)
            expires_at = (now + timedelta(seconds=snapshot_ttl_sec)).isoformat()
            regime_tag_for_fp = current_regime.state if current_regime else None
            snapshot_policy_id = (
                str(cfg.universe.candidate_selection_policy_epoch or "")
                if candidate_selection_authority_bundle is not None
                else "legacy_total_score_v1"
            )

            # Phase 3 retention: change-suppression on universe_snapshots.
            # If the allocation fingerprint matches the currently-active snapshot
            # AND that snapshot is younger than the heartbeat floor, skip the
            # write and reuse the previous snapshot. This collapses ~5 of every
            # 6 refreshes to a no-op when the top-N is stable.
            new_fp = _member_fingerprint(
                allocated,
                regime_tag_for_fp,
                policy_id=snapshot_policy_id,
            )
            prev_fp, prev_snapshot_id, prev_age_sec = _previous_snapshot_fingerprint(
                market_type,
            )
            reuse_previous_snapshot = (
                prev_snapshot_id is not None
                and prev_fp == new_fp
                and prev_age_sec is not None
                and prev_age_sec < _SNAPSHOT_HEARTBEAT_SEC
            )

            if reuse_previous_snapshot:
                logger.debug(
                    "Universe refresh: suppressed snapshot write (fingerprint unchanged, prev_age=%.0fs, prev=%s)",
                    prev_age_sec,
                    prev_snapshot_id,
                )
                # Slide member plus linked open/queued opportunity expiries. A
                # queued row is revalidated only because this exact source
                # snapshot remains active and semantically unchanged. Without this,
                # a stable-allocation window longer than the family TTL causes
                # every carrier to be ttl_expired on the next ranker scan even
                # though we just re-confirmed the same working set is valid.
                # See repositories.refresh_active_snapshot_ttls for semantics.
                refreshed_members = refreshed_opps = 0
                if candidate_selection_authority_bundle is None:
                    try:
                        refreshed_members, refreshed_opps = repo.refresh_active_snapshot_ttls(
                            prev_snapshot_id,
                            now,
                        )
                    except Exception as _e:
                        # Legacy TTL renewal remains best-effort; target renewal
                        # joins the required evidence transaction below.
                        logger.warning(
                            "Universe refresh: TTL slide failed (non-fatal): %s",
                            _e,
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
                        "Universe refresh (suppressed): cascade expiry failed (non-fatal): %s",
                        _e,
                    )
                experiment_outcomes = _build_experiment_candidate_outcomes(
                    pre_scored,
                    entry_ts=entry_ts,
                    allocated=allocated,
                )
                previous_members = repo.list_universe_members(prev_snapshot_id, limit=500)
                requalifications = _prepare_planning_requalifications(previous_members)
                with _atomic_universe_publication():
                    if candidate_selection_authority_bundle is not None:
                        _assert_candidate_selection_publication_authority(
                            snapshot_policy_id,
                            qualification["selection_policy"],
                        )
                    if candidate_selection_authority_bundle is not None:
                        refreshed_members, refreshed_opps = repo.refresh_active_snapshot_ttls(
                            prev_snapshot_id,
                            now,
                        )
                    _persist_attempt_learning_exposures(deferred_exposure_writes)
                    selected_generation_ids, new_generation_ids, planning_generations_created = (
                        _ensure_planning_generations(
                            previous_members,
                            soft_lease_sec=cfg.universe.entry_ttl_sec,
                            max_attention_age_sec=cfg.universe.candidate_max_continuous_age_sec,
                            now=now,
                            requalifications=requalifications,
                        )
                    )
                    repo.reconcile_candidate_attention(
                        selected_generation_ids,
                        eligible_identity_keys=eligible_attention_identities,
                        refresh_source={
                            "scanner_run_id": run.id,
                            "snapshot_id": prev_snapshot_id,
                            "member_fingerprint": new_fp,
                            "change_suppressed": True,
                        },
                        soft_lease_sec=cfg.universe.entry_ttl_sec,
                        new_generation_ids=new_generation_ids,
                        now=now,
                    )
                    repo.insert_filter_candidate_outcomes_batch(experiment_outcomes)
                    repo.update_universe_scanner_run(
                        run.id,
                        status="completed",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        snapshot_id=prev_snapshot_id,
                        metrics=json.dumps({"candidate_path": path_metrics}),
                    )
                if refreshed_members or refreshed_opps:
                    logger.info(
                        "Universe refresh (suppressed): TTL slid forward — members=%d opps=%d snapshot=%s",
                        refreshed_members,
                        refreshed_opps,
                        prev_snapshot_id,
                    )
                if candidate_selection_authority_bundle is not None and refreshed_opps:
                    try:
                        from aletheia.agent_runtime.scheduler import notify_paper_trader

                        notify_paper_trader(source="universe_refresh_ttl_slide")
                    except Exception as _e:
                        logger.debug("paper_trader nudge skipped (non-fatal): %s", _e)
                if experiment_outcomes:
                    logger.info(
                        "Registered %d experiment candidate outcome row(s) across full arms",
                        len(experiment_outcomes),
                    )
                _capture_universe_discovery_nonfatal(
                    scanner_run_id=run.id,
                    population=unique_scored,
                    allocated=allocated,
                    entry_ts=entry_ts,
                    regime_tag=regime_tag_for_fp,
                    cfg=cfg,
                )
                logger.info(
                    "Universe refresh complete (change-suppressed): reused snapshot=%s",
                    prev_snapshot_id,
                )
                if planning_generations_created:
                    _notify_operator_feature_refresh("candidate_planning_generation_created")
                return prev_snapshot_id

            snap = UniverseSnapshot(
                market_type=market_type,
                policy_id=snapshot_policy_id,
                expires_at=expires_at,
                summary=json.dumps(
                    {
                        "scanned": len(assets),
                        "filtered": len(scored),
                        "selected": len(allocated),
                        "member_fingerprint": new_fp,
                        "information_policy_epoch": POLICY_EPOCH_LABEL,
                        "candidate_selection_policy": cfg.universe.candidate_selection_policy,
                        "candidate_selection_policy_epoch": cfg.universe.candidate_selection_policy_epoch,
                    }
                ),
            )
            attempted_snapshot_id = snap.id
            # Precompute every member object and JSON payload before acquiring
            # SQLite's single writer slot. The transaction below should spend
            # its time executing SQL, not serializing Python structures.
            global_regime_tag = current_regime.state if current_regime else None
            member_models: list[UniverseMember] = []
            inserted_members: list[dict] = []
            for rank, item in enumerate(allocated, 1):
                regime_tag = global_regime_tag if (item.get("analysis_context") or {}).get("kind") == "crypto" else None
                ttl_sec = min(item.get("ttl_sec", 3600), snapshot_ttl_sec)
                member_expires = (now + timedelta(seconds=ttl_sec)).isoformat()
                entry_price = item.get("last_price")
                scanner_direction = item.get("scanner_direction") or _infer_direction(item.get("scanner_family"), item)
                member_id = uuid4().hex[:12]
                target_record = item.get("_candidate_opportunity_record")
                if item.get("candidate_selection_policy") == "candidate_selection_utility_v1":
                    if not isinstance(target_record, dict):
                        raise ValueError("candidate_selection_opportunity_record_missing")
                    opportunity_context = target_candidate_opportunity_context(
                        candidate_id=member_id,
                        decision=target_record["decision"],
                        policy_epoch=str(item["candidate_selection_policy_epoch"]),
                        source_record_hash=str(target_record["source_record_hash"]),
                        detected_at=entry_ts,
                    )
                else:
                    opportunity_context = legacy_candidate_opportunity_context(
                        candidate_id=member_id,
                        candidate={**item, "scanner_direction": scanner_direction},
                        detected_at=entry_ts,
                    )
                member = UniverseMember(
                    id=member_id,
                    snapshot_id=snap.id,
                    asset_id=item["asset_id"],
                    venue=item.get("venue"),
                    symbol=item["symbol"],
                    market_type=item.get("market_type", "spot"),
                    rank=rank,
                    # Historical NOT NULL carrier only; Utility never reads this score.
                    total_score=0.0,
                    opportunity_score=item.get("opportunity_score"),
                    tradability_score=item.get("tradability_score"),
                    liquidity_score=item.get("liquidity_score"),
                    estimated_cost=(2.0 * cfg.trading.execution_fee_pct
                                    + (float(item["spread_bps"]) + 2.0 * cfg.trading.slippage_bps) / 100.0),
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
                    candidate_selection_policy=item.get(
                        "candidate_selection_policy", "legacy_total_score_v1",
                    ),
                    candidate_selection_policy_epoch=item.get(
                        "candidate_selection_policy_epoch",
                    ),
                    candidate_selection_utility_pct=item.get(
                        "candidate_selection_utility_pct",
                    ),
                    opportunity_mechanism=(
                        opportunity_context.opportunity["primary_mechanism"]
                        if opportunity_context else None
                    ),
                    opportunity_mechanism_version=item.get("opportunity_mechanism_version"),
                    opportunity_source_component=item.get("opportunity_source_component"),
                    opportunity_source_cutoff_at=(
                        opportunity_context.source["source_cutoff_at"]
                        if opportunity_context else None
                    ),
                    opportunity_evidence_hash=item.get("opportunity_evidence_hash"),
                    candidate_opportunity_context_version=(
                        opportunity_context.version if opportunity_context else None
                    ),
                    candidate_opportunity_context_json=(
                        opportunity_context.model_dump_json() if opportunity_context else None
                    ),
                    candidate_opportunity_context_hash=(
                        opportunity_context.context_hash if opportunity_context else None
                    ),
                )
                member_models.append(member)
                inserted_members.append(
                    {
                        "id": member.id,
                        "snapshot_id": snap.id,
                        "symbol": item["symbol"],
                        "venue": item.get("venue"),
                        "market_type": item.get("market_type", "spot"),
                        "scanner_family": item.get("scanner_family"),
                        "regime_tag": regime_tag,
                        "direction": scanner_direction,
                        "entry_price_usd": entry_price,
                        "spread_bps": item.get("spread_bps"),
                        "funding_rate": item.get("funding_rate"),
                        "total_score": member.total_score,
                        "candidate_selection_policy": item.get(
                            "candidate_selection_policy", "legacy_total_score_v1",
                        ),
                        "candidate_selection_policy_epoch": item.get(
                            "candidate_selection_policy_epoch",
                        ),
                        "candidate_selection_utility_pct": item.get(
                            "candidate_selection_utility_pct",
                        ),
                        "opportunity_mechanism": item.get("opportunity_mechanism"),
                        "opportunity_mechanism_version": item.get("opportunity_mechanism_version"),
                        "opportunity_source_component": item.get("opportunity_source_component"),
                        "opportunity_source_cutoff_at": item.get("opportunity_source_cutoff_at"),
                        "opportunity_evidence_hash": item.get("opportunity_evidence_hash"),
                        "learning_candidate_key": item.get("learning_candidate_key"),
                        "learning_exposure_id": item.get("learning_exposure_id"),
                        "instrument_background": item.get("instrument_background"),
                        "analysis_context": item.get("analysis_context"),
                        "expires_at": member_expires,
                    }
                )
            pending_outcomes = build_pending(inserted_members, entry_ts=entry_ts)
            experiment_outcomes = _build_experiment_candidate_outcomes(
                pre_scored,
                entry_ts=entry_ts,
                allocated=allocated,
            )
            sample_alpha_control = _should_sample_alpha_control(now)
            requalifications = _prepare_planning_requalifications(member_models)
            # Do not expose the new active snapshot before every selected member
            # and its Opportunity handoff are durable. Superseding the previous
            # healthy snapshot is the final write in this publication unit.
            with _atomic_universe_publication():
                if candidate_selection_authority_bundle is not None:
                    _assert_candidate_selection_publication_authority(
                        snapshot_policy_id,
                        qualification["selection_policy"],
                    )
                _persist_attempt_learning_exposures(deferred_exposure_writes)
                repo.insert_universe_snapshot(snap)
                repo.insert_universe_members_batch(member_models)

                selected_generation_ids, new_generation_ids, planning_generations_created = (
                    _ensure_planning_generations(
                        member_models,
                        soft_lease_sec=cfg.universe.entry_ttl_sec,
                        max_attention_age_sec=cfg.universe.candidate_max_continuous_age_sec,
                        now=now,
                        requalifications=requalifications,
                    )
                )

                repo.reconcile_candidate_attention(
                    selected_generation_ids,
                    eligible_identity_keys=eligible_attention_identities,
                    refresh_source={
                        "scanner_run_id": run.id,
                        "snapshot_id": snap.id,
                        "member_fingerprint": new_fp,
                        "change_suppressed": False,
                    },
                    soft_lease_sec=cfg.universe.entry_ttl_sec,
                    new_generation_ids=new_generation_ids,
                    now=now,
                )

                # Register forward outcome tracking for all members with valid prices
                repo.insert_forward_outcomes_batch(pending_outcomes)
                repo.insert_filter_candidate_outcomes_batch(experiment_outcomes)

                repo.expire_universe_snapshots(market_type, except_id=snap.id)
                # Cascade: end only unclaimed open analytical generations whose
                # parent snapshot just became superseded. Queued Operator carriers
                # retain their origin ids and live by Opportunity.valid_until.
                # Before the soft-TTL filter open generations expired
                # naturally at the family-TTL cliff; now they need explicit
                # cleanup or the pool grows unboundedly (see
                # repositories.expire_opportunities_from_superseded_snapshots).
                # This is part of the publication unit: leaving the old open
                # generation beside its replacement would create two rankable
                # owners. Any evidence/CAS failure must roll the unit back.
                cascaded = repo.expire_opportunities_from_superseded_snapshots(market_type)
                if cascaded:
                    logger.info(
                        "Universe refresh: cascaded expiry — %d open generation(s) "
                        "from superseded snapshots marked expired",
                        cascaded,
                    )

                repo.update_universe_scanner_run(
                    run.id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    snapshot_id=snap.id,
                    metrics=json.dumps({"candidate_path": path_metrics}),
                )
            _capture_universe_discovery_nonfatal(
                scanner_run_id=run.id,
                population=unique_scored,
                allocated=allocated,
                entry_ts=entry_ts,
                regime_tag=global_regime_tag,
                cfg=cfg,
            )

            if experiment_outcomes:
                logger.info(
                    "Registered %d experiment candidate outcome row(s) across full arms",
                    len(experiment_outcomes),
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
                logger.debug("Universe causal control cohort sampling skipped (hourly cadence)")

            _notify_operator_feature_refresh("universe_refresh_new_snapshot")
            logger.info(
                "Universe refresh complete: %d scanned → %d filtered → %d selected (snapshot=%s)",
                len(assets),
                len(scored),
                len(allocated),
                snap.id,
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
                metrics=json.dumps({"candidate_path": path_metrics}),
            )
            return None

    def fallback_static_universe(self) -> list[str]:
        """Return the static fallback symbol list."""
        return load_config().universe.fallback_symbols

