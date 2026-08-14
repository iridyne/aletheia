"""Exact, read-only market-fact identity for active pending candidates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from typing import Iterable

from aletheia.data.symbols import InstrumentId, to_canonical
from aletheia.storage import repositories as repo


_LEGACY_PENDING_MAX_AGE_MINUTES = 30
CANDIDATE_OHLCV_MAX_AGE_SEC = {"1h": 2 * 60 * 60, "4h": 8 * 60 * 60}


def _physical_market_type(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {"perp", "swap", "futures", "usdt_futures"}:
        return "perp"
    return "spot" if raw == "spot" else None


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _millis_to_utc(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _active_pending_rows(max_age_minutes: int) -> list[dict]:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=max(1, max_age_minutes))
    ).isoformat()
    after_pending = (
        "(julianday({alias}.created_at)>julianday(p.created_at) "
        "OR (julianday({alias}.created_at)=julianday(p.created_at) "
        "AND {alias}.rowid>p.rowid))"
    )
    unique_final = (
        "(SELECT COUNT(*) FROM pi_decisions all_f "
        "WHERE all_f.opportunity_id=p.opportunity_id "
        "AND COALESCE(all_f.signal_source,'market_observer')="
        "    COALESCE(p.signal_source,'market_observer') "
        "AND all_f.action NOT IN ('signal_pending','close','reduce') "
        f"AND {after_pending.format(alias='all_f')})=1"
    )
    exact_final = (
        "f.opportunity_id=p.opportunity_id "
        "AND COALESCE(f.signal_source,'market_observer')="
        "    COALESCE(p.signal_source,'market_observer') "
        "AND f.action NOT IN ('signal_pending','close','reduce') "
        "AND f.symbol IS p.symbol AND f.direction IS p.direction "
        "AND f.market_type IS p.market_type "
        f"AND {after_pending.format(alias='f')} AND {unique_final}"
    )
    rows = repo.get_db().execute(
        "SELECT p.rowid AS carrier_rowid, p.opportunity_id, p.symbol, "
        "p.direction, p.market_type, p.created_at, o.symbol AS source_symbol, "
        "o.market AS source_market, o.universe_member_id, o.valid_until, "
        "um.symbol AS member_symbol, um.market_type AS member_market, "
        "um.asset_id AS member_asset_id, ur.id AS asset_id, ur.venue, "
        "ur.symbol AS asset_symbol, ur.market_type AS asset_market "
        "FROM pi_decisions p "
        "LEFT JOIN opportunities o ON o.id=p.opportunity_id "
        "LEFT JOIN universe_members um ON um.id=o.universe_member_id "
        "LEFT JOIN universe_registry ur ON ur.id=um.asset_id "
        "WHERE p.action='signal_pending' "
        "AND COALESCE(p.signal_source,'market_observer')='market_observer' "
        "AND (EXISTS (SELECT 1 FROM pi_decisions f WHERE "
        + exact_final
        + " AND f.proposal_status='submitting') "
        " OR ((o.status IN ('queued','reviewed') "
        "      AND datetime(o.valid_until)>datetime('now')) "
        "     OR ((o.id IS NULL OR (o.status IN ('queued','reviewed') "
        "                           AND datetime(o.valid_until) IS NULL)) "
        "         AND datetime(p.created_at)>=datetime(?)))) "
        "AND NOT EXISTS (SELECT 1 FROM pi_decisions f WHERE "
        + exact_final
        + " AND COALESCE(f.proposal_status,'')!='submitting') "
        "ORDER BY datetime(p.created_at), p.rowid",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def _unique_legacy_asset(symbol: str, market_type: str) -> dict | None:
    rows = repo.get_db().execute(
        "SELECT * FROM universe_registry WHERE symbol=? AND status='active' "
        "AND is_blacklisted=0 ORDER BY datetime(last_seen_at) DESC, id",
        (symbol,),
    ).fetchall()
    matches = [
        dict(row) for row in rows
        if _physical_market_type(row["market_type"]) == market_type
    ]
    return matches[0] if len(matches) == 1 else None


def list_pending_candidate_facts(
    *, max_age_minutes: int = _LEGACY_PENDING_MAX_AGE_MINUTES,
) -> list[dict]:
    """Return active market-observer carriers with fail-closed asset identity."""
    facts: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in _active_pending_rows(max_age_minutes):
        symbol = to_canonical(str(row.get("symbol") or ""))
        market_type = _physical_market_type(row.get("market_type"))
        key = (
            str(row.get("opportunity_id") or ""),
            symbol,
            str(row.get("direction") or ""),
            market_type or "",
        )
        if key in seen:
            continue
        seen.add(key)

        reason: str | None = None
        if not all(key):
            reason = "carrier_identity_incomplete"
        elif (
            to_canonical(str(row.get("source_symbol") or "")) != symbol
            or _physical_market_type(row.get("source_market")) != market_type
        ):
            reason = "carrier_source_identity_mismatch"

        asset = None
        if row.get("universe_member_id"):
            if not row.get("asset_id"):
                reason = reason or "linked_asset_unavailable"
            else:
                asset = {
                    "id": row.get("asset_id"),
                    "venue": row.get("venue"),
                    "symbol": row.get("asset_symbol"),
                    "market_type": row.get("asset_market"),
                }
                if (
                    row.get("member_asset_id") != row.get("asset_id")
                    or to_canonical(str(row.get("member_symbol") or "")) != symbol
                    or _physical_market_type(row.get("member_market")) != market_type
                ):
                    reason = reason or "linked_member_identity_mismatch"
        elif symbol and market_type:
            asset = _unique_legacy_asset(symbol, market_type)
            if asset is None:
                reason = reason or "legacy_asset_identity_not_unique"

        venue = str((asset or {}).get("venue") or "").strip().lower()
        if asset and (
            not venue
            or to_canonical(str(asset.get("symbol") or "")) != symbol
            or _physical_market_type(asset.get("market_type")) != market_type
        ):
            reason = reason or "linked_asset_identity_mismatch"

        instrument = None
        if reason is None:
            try:
                instrument = InstrumentId.from_parts(venue, symbol, market_type or "")
            except ValueError:
                reason = "instrument_identity_invalid"

        facts.append({
            "opportunity_id": row.get("opportunity_id"),
            "carrier_created_at": row.get("created_at"),
            "valid_until": row.get("valid_until"),
            "symbol": symbol,
            "direction": row.get("direction"),
            "market_type": row.get("market_type"),
            "physical_market_type": market_type,
            "universe_member_id": row.get("universe_member_id"),
            "asset_id": (asset or {}).get("id"),
            "venue": venue or None,
            "identity_status": "exact" if instrument is not None else "unavailable",
            "identity_reason": reason,
        })
    return facts


def pending_candidate_instruments() -> list[InstrumentId]:
    """Deduplicated exact instruments, preserving oldest-carrier priority."""
    result: list[InstrumentId] = []
    seen: set[str] = set()
    for fact in list_pending_candidate_facts():
        if fact["identity_status"] != "exact":
            continue
        instrument = InstrumentId.from_parts(
            str(fact["venue"]),
            str(fact["symbol"]),
            str(fact["physical_market_type"]),
        )
        if str(instrument) not in seen:
            seen.add(str(instrument))
            result.append(instrument)
    return result


def pending_candidate_assets(*, venue: str | None = None) -> list[dict]:
    """Exact durable assets required by active pending carriers."""
    result: list[dict] = []
    seen: set[str] = set()
    expected_venue = str(venue or "").strip().lower()
    for fact in list_pending_candidate_facts():
        asset_id = str(fact.get("asset_id") or "")
        if (
            fact["identity_status"] != "exact"
            or not asset_id
            or (expected_venue and fact.get("venue") != expected_venue)
            or asset_id in seen
        ):
            continue
        asset = repo.get_universe_asset(asset_id)
        if asset is not None:
            seen.add(asset_id)
            result.append(asset)
    return result


def record_candidate_refresh_failure(
    instrument: InstrumentId,
    *,
    component: str,
    reason: str,
) -> None:
    """Expose a bounded, non-sensitive per-instrument refresh failure."""
    from aletheia.infra.events import emit_infra_event

    emit_infra_event(
        severity="warning",
        source="candidate_market_facts",
        code=f"{component}:{reason}",
        entity_id=str(instrument),
        message=f"candidate {component} refresh failed for {instrument}: {reason}",
        metadata={"component": component, "reason": reason},
        dedupe_sec=300,
    )


def wake_pending_candidate_defers(
    *,
    defer_reasons: Iterable[str],
    source: str,
    asset_ids: Iterable[str] = (),
    instruments: Iterable[InstrumentId] = (),
) -> bool:
    """Wake once when refreshed facts exactly match a live data defer."""
    reasons = {str(reason) for reason in defer_reasons if reason}
    assets = {str(asset_id) for asset_id in asset_ids if asset_id}
    identities = {str(instrument) for instrument in instruments}
    if not reasons or (not assets and not identities):
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    db = repo.get_db()
    for fact in list_pending_candidate_facts():
        if fact["identity_status"] != "exact":
            continue
        identity = str(InstrumentId.from_parts(
            str(fact["venue"]),
            str(fact["symbol"]),
            str(fact["physical_market_type"]),
        ))
        if str(fact.get("asset_id") or "") not in assets and identity not in identities:
            continue
        event = db.execute(
            "SELECT json_extract(e.queue_snapshot_json,'$.defer_reason') AS reason "
            "FROM operator_evaluation_events e "
            "WHERE json_extract(e.queue_snapshot_json,'$.opportunity_id')=? "
            "AND e.event_kind LIKE 'operator_decision_deferred_%' "
            "AND json_extract(e.queue_snapshot_json,'$.defer_until')>? "
            "AND NOT EXISTS ("
            " SELECT 1 FROM operator_evaluation_events newer "
            " WHERE json_extract(newer.queue_snapshot_json,'$.opportunity_id')="
            "       json_extract(e.queue_snapshot_json,'$.opportunity_id') "
            " AND (julianday(newer.created_at)>julianday(e.created_at) "
            "      OR (julianday(newer.created_at)=julianday(e.created_at) "
            "          AND newer.rowid>e.rowid))"
            ") ORDER BY datetime(e.created_at) DESC, e.rowid DESC LIMIT 1",
            (fact["opportunity_id"], now_iso),
        ).fetchone()
        if event is None or str(event["reason"] or "") not in reasons:
            continue
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon

        notify_pi_daemon(source=source)
        return True
    return False


def _recent_failure(
    identity: str,
    *,
    feature_at: datetime | None,
    mark_at: datetime | None,
) -> dict | None:
    from aletheia.infra.events import list_infra_events

    for event in list_infra_events(limit=100):
        if event.get("source") != "candidate_market_facts":
            continue
        if event.get("entity_id") != identity:
            continue
        event_at = _parse_utc(event.get("created_at"))
        component = str((event.get("metadata") or {}).get("component") or "")
        success_at = mark_at if component == "price_mark" else feature_at
        if success_at is None or (event_at is not None and event_at > success_at):
            return {
                "component": component,
                "reason": (event.get("metadata") or {}).get("reason"),
                "at": event.get("created_at"),
            }
    return None


def build_pending_candidate_market_report(
    *, now: datetime | None = None,
) -> dict:
    """Per-carrier feature/OHLCV/mark coverage for Doctor and runtime APIs."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    facts = list_pending_candidate_facts()
    if not facts:
        return {
            "status": "no_pending",
            "pending_total": 0,
            "complete_count": 0,
            "partial_count": 0,
            "unavailable_count": 0,
            "reason_counts": {},
            "items": [],
        }

    try:
        from aletheia.config.loader import load_config

        feature_venue = str(load_config().market_data.ccxt_exchange or "").lower()
    except Exception:
        feature_venue = ""

    items: list[dict] = []
    all_reasons: Counter[str] = Counter()
    for fact in facts:
        item = dict(fact)
        reasons: list[str] = []
        if fact["identity_status"] != "exact":
            reasons.append(str(fact.get("identity_reason") or "candidate_identity_unavailable"))
            item.update({
                "availability": "unavailable",
                "failure_category": "scheduling_not_covered",
                "feature": {"status": "unavailable"},
                "ohlcv": {},
                "entry_mark": {"status": "unavailable"},
                "reasons": reasons,
            })
            all_reasons.update(reasons)
            items.append(item)
            continue

        instrument = InstrumentId.from_parts(
            str(fact["venue"]), str(fact["symbol"]),
            str(fact["physical_market_type"]),
        )
        if feature_venue and instrument.exchange != feature_venue:
            reasons.append("candidate_feature_venue_not_scheduled")

        feature = repo.get_latest_feature_snapshot(str(fact["asset_id"]))
        feature_at = _parse_utc((feature or {}).get("ts"))
        feature_context = dict(feature or {})
        feature_context.update({
            "asset_id": fact["asset_id"],
            "feature_snapshot_id": (feature or {}).get("id"),
            "feature_ts": (feature or {}).get("ts"),
        })
        from aletheia.agent_shell.decision_facts import (
            candidate_context_unavailable_reason,
        )

        technical_reason = candidate_context_unavailable_reason({
            "layer1": {"features": [feature_context] if feature else []},
            "layer2": {"pending_signals": [{
                "opportunity_id": fact["opportunity_id"],
                "signal_source": "market_observer",
                "asset_id": fact["asset_id"],
                "symbol": fact["symbol"],
                "market_type": fact["physical_market_type"],
            }]},
        }, opportunity_id=str(fact["opportunity_id"]))
        if technical_reason:
            reasons.append(technical_reason)
        feature_age = (
            max(0.0, (current - feature_at).total_seconds())
            if feature_at is not None else None
        )
        try:
            technical_manifest = json.loads(
                str((feature or {}).get("technical_snapshot_manifest") or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            technical_manifest = {}
        item["feature"] = {
            "status": "available" if technical_reason is None else "unavailable",
            "snapshot_id": (feature or {}).get("id"),
            "snapshot_at": (feature or {}).get("ts"),
            "row_age_sec": round(feature_age, 3) if feature_age is not None else None,
            "data_freshness_sec": (feature or {}).get("data_freshness_sec"),
            "ohlcv_bar_close_at": {
                timeframe: (
                    _millis_to_utc((snapshot or {}).get("bar_close_ts")).isoformat()
                    if _millis_to_utc((snapshot or {}).get("bar_close_ts")) else None
                )
                for timeframe, snapshot in technical_manifest.items()
                if isinstance(snapshot, dict)
            },
            "rejection_reason": technical_reason,
        }

        ohlcv: dict[str, dict] = {}
        for timeframe, max_age_sec in CANDIDATE_OHLCV_MAX_AGE_SEC.items():
            coverage = repo.get_ohlcv_coverage(
                instrument.symbol,
                timeframe,
                exchange=instrument.exchange,
                market_type=instrument.market_type,
            )
            newest = _millis_to_utc(coverage.get("newest_ts"))
            age = (
                max(0.0, (current - newest).total_seconds())
                if newest is not None else None
            )
            status = (
                "available" if age is not None and age <= max_age_sec
                else "stale" if age is not None else "unavailable"
            )
            if status != "available":
                reasons.append(f"candidate_ohlcv_{timeframe}_{status}")
            state = repo.get_ohlcv_backfill_state(
                instrument.exchange, instrument.symbol, instrument.market_type, timeframe,
            ) or {}
            ohlcv[timeframe] = {
                "status": status,
                "bar_count": int(coverage.get("bar_count") or 0),
                "newest_at": newest.isoformat() if newest else None,
                "age_sec": round(age, 3) if age is not None else None,
                "last_attempt_at": state.get("last_attempt_at"),
                "last_error": state.get("last_error"),
            }
        item["ohlcv"] = ohlcv

        from aletheia.data.price_feed import (
            get_fresh_mark_for_protective_close,
            get_latest_exact_market_snapshot,
        )

        latest_mark = get_latest_exact_market_snapshot(
            instrument.symbol, instrument.market_type, instrument.exchange,
        )
        fresh_mark = get_fresh_mark_for_protective_close(
            instrument.symbol, instrument.market_type, venue=instrument.exchange,
        )
        mark_at = _parse_utc(
            (fresh_mark or {}).get("observed_at") or (latest_mark or {}).get("snapshot_at")
        )
        if fresh_mark is None:
            reasons.append("candidate_entry_mark_unavailable")
        item["entry_mark"] = {
            "status": "available" if fresh_mark is not None else "unavailable",
            "snapshot_id": (latest_mark or {}).get("id"),
            "observed_at": (
                (fresh_mark or {}).get("observed_at")
                or (latest_mark or {}).get("snapshot_at")
            ),
            "age_sec": (
                round(max(0.0, (current - mark_at).total_seconds()), 3)
                if mark_at is not None else None
            ),
            "source": (fresh_mark or {}).get("source"),
            "rejection_reason": (
                None if fresh_mark is not None else "candidate_entry_mark_unavailable"
            ),
        }

        failure = _recent_failure(
            str(instrument), feature_at=feature_at, mark_at=mark_at,
        )
        if failure:
            item["latest_refresh_failure"] = failure

        if any(reason.startswith("candidate_feature_venue") for reason in reasons):
            category = "scheduling_not_covered"
        elif failure and failure.get("reason") == "upstream_quote_unavailable":
            category = "upstream_quote_unavailable"
        elif any(reason.startswith("candidate_ohlcv_") for reason in reasons):
            category = "ohlcv_not_updated"
        elif technical_reason:
            category = "feature_not_updated"
        elif fresh_mark is None:
            category = "mark_not_updated"
        else:
            category = None
        item["availability"] = "complete" if not reasons else "partial"
        item["failure_category"] = category
        item["reasons"] = reasons
        all_reasons.update(reasons)
        items.append(item)

    complete = sum(item["availability"] == "complete" for item in items)
    unavailable = sum(item["availability"] == "unavailable" for item in items)
    partial = len(items) - complete - unavailable
    return {
        "status": (
            "complete" if complete == len(items)
            else "unavailable" if unavailable == len(items)
            else "partial"
        ),
        "pending_total": len(items),
        "complete_count": complete,
        "partial_count": partial,
        "unavailable_count": unavailable,
        "reason_counts": dict(all_reasons),
        "items": items,
    }


__all__ = [
    "CANDIDATE_OHLCV_MAX_AGE_SEC",
    "build_pending_candidate_market_report",
    "list_pending_candidate_facts",
    "pending_candidate_assets",
    "pending_candidate_instruments",
    "record_candidate_refresh_failure",
    "wake_pending_candidate_defers",
]
