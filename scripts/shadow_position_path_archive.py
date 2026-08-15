"""Shadow-encode Position-path observations without deleting hot evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from aletheia.analytics.position_lifecycle_story import build_position_lifecycle_story
from aletheia.analytics.position_path_observations import build_position_path_report
from aletheia.analytics.position_replay import build_position_replay
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.position_path_archive import canonical_row

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--position-id")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--as-of")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    db = repo.get_db()
    as_of = _parse_as_of(args.as_of)
    source_before = _canonical_rows(
        [
            dict(row)
            for row in _source_rows(db, args.position_id, args.start_ms, args.end_ms)
        ]
    )
    hot_before = _hot_count(db, args.position_id, args.start_ms, args.end_ms)
    archive_before = _archive_rows(db, args.position_id, args.start_ms, args.end_ms)
    shadow_position_ids = sorted(
        {str(row["position_id"]) for row in source_before}
        | {str(row["position_id"]) for row in archive_before}
    )
    truth_before = {
        position_key: repo.list_position_path_observations_unified(
            position_key,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            as_of=as_of,
        )
        for position_key in shadow_position_ids
    }
    reports_before = {
        position_key: build_position_path_report(position_key, as_of=as_of)
        for position_key in shadow_position_ids
    }
    stories_before = {
        position_key: build_position_lifecycle_story(
            position_key,
            as_of_at=as_of,
            db=db,
        )
        for position_key in shadow_position_ids
    }
    replays_before = {
        position_key: build_position_replay(
            position_key,
            as_of_at=as_of,
            db=db,
        )
        for position_key in shadow_position_ids
    }
    summary = repo.archive_position_path_segments(
        args.position_id,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
    )
    hot_after = _hot_count(db, args.position_id, args.start_ms, args.end_ms)
    source_after = _canonical_rows(
        [
            dict(row)
            for row in _source_rows(db, args.position_id, args.start_ms, args.end_ms)
        ]
    )
    archive_rows = _archive_rows(db, args.position_id, args.start_ms, args.end_ms)
    archive_position_ids = {str(row["position_id"]) for row in archive_rows}
    archived_rows: list[dict] = []
    for archived_position_id in sorted(archive_position_ids):
        archived_rows.extend(
            repo.list_archived_position_path_observations(
                archived_position_id,
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                as_of=as_of,
            )
        )
    truth_rows = [
        row
        for position_key in sorted(archive_position_ids)
        for row in truth_before.get(position_key, [])
    ]
    archive_parity = _canonical_rows(truth_rows) == _canonical_rows(archived_rows)
    unified_parity = True
    for archived_position_id in sorted(archive_position_ids):
        expected = truth_before.get(archived_position_id, [])
        unified = repo.list_position_path_observations_unified(
            archived_position_id,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            as_of=as_of,
        )
        unified_parity = unified_parity and (
            _canonical_rows(expected) == _canonical_rows(unified)
        )
    reports_after = {
        position_key: build_position_path_report(position_key, as_of=as_of)
        for position_key in sorted(archive_position_ids)
    }
    stories_after = {
        position_key: build_position_lifecycle_story(
            position_key,
            as_of_at=as_of,
            db=db,
        )
        for position_key in sorted(archive_position_ids)
    }
    replays_after = {
        position_key: build_position_replay(
            position_key,
            as_of_at=as_of,
            db=db,
        )
        for position_key in sorted(archive_position_ids)
    }
    report_parity = all(
        _canonical_json(reports_before.get(position_key))
        == _canonical_json(reports_after.get(position_key))
        for position_key in archive_position_ids
    )
    story_parity = all(
        _canonical_json(stories_before.get(position_key))
        == _canonical_json(stories_after.get(position_key))
        for position_key in archive_position_ids
    )
    position_replay_parity = all(
        _position_replay_projection(replays_before.get(position_key))
        == _position_replay_projection(replays_after.get(position_key))
        for position_key in archive_position_ids
    )
    coverage_parity = all(
        _coverage_projection(reports_before.get(position_key))
        == _coverage_projection(reports_after.get(position_key))
        and _observer_projection(stories_before.get(position_key))
        == _observer_projection(stories_after.get(position_key))
        for position_key in archive_position_ids
    )
    excursion_parity = all(
        _excursion_projection(reports_before.get(position_key))
        == _excursion_projection(reports_after.get(position_key))
        and _story_excursion_projection(stories_before.get(position_key))
        == _story_excursion_projection(stories_after.get(position_key))
        for position_key in archive_position_ids
    )
    story_event_parity = all(
        _structured_events(stories_before.get(position_key))
        == _structured_events(stories_after.get(position_key))
        for position_key in archive_position_ids
    )
    as_of_parity = unified_parity and all(
        stories_before.get(position_key, {}).get("as_of_at") == as_of.isoformat()
        and stories_after.get(position_key, {}).get("as_of_at") == as_of.isoformat()
        for position_key in archive_position_ids
    )
    provenance_evidence_gap_parity = (
        _provenance_projection(truth_rows)
        == _provenance_projection(archived_rows)
        and all(
            stories_before.get(position_key, {}).get("evidence_gaps", [])
            == stories_after.get(position_key, {}).get("evidence_gaps", [])
            for position_key in archive_position_ids
        )
    )
    parity_checks = {
        "observations": archive_parity,
        "coverage_expected_available_unavailable_missing": coverage_parity,
        "mfe_mae_giveback": excursion_parity,
        "story_events": story_event_parity,
        "as_of": as_of_parity,
        "provenance_and_evidence_gaps": provenance_evidence_gap_parity,
        "position_replay_position_path": position_replay_parity,
    }
    read_model_parity = (
        all(parity_checks.values())
        and report_parity
        and story_parity
        and position_replay_parity
    )
    source_unchanged = hot_before == hot_after and source_before == source_after
    no_archive_for_skipped = (
        (
            bool(summary.get("positions_skipped"))
            and not archive_position_ids
        )
        or (
            not shadow_position_ids
            and int(summary.get("source_rows") or 0) == 0
        )
    )
    status = (
        "skipped"
        if no_archive_for_skipped
        else "passed"
        if source_unchanged
        and archive_parity
        and unified_parity
        and read_model_parity
        and not summary.get("positions_skipped")
        else "partial"
        if source_unchanged
        and archive_parity
        and unified_parity
        and read_model_parity
        else "failed"
    )
    report = {
        "contract_version": "position_path_archive_shadow/v2",
        "mode": "shadow",
        "status": status,
        "source_unchanged": source_unchanged,
        "archive_parity": archive_parity,
        "unified_parity": unified_parity,
        "position_path_report_parity": report_parity,
        "position_lifecycle_story_parity": story_parity,
        "position_replay_parity": position_replay_parity,
        "read_model_parity": read_model_parity,
        "parity_checks": parity_checks,
        "hot_rows_before": hot_before,
        "hot_rows_after": hot_after,
        "source_rows_before": len(source_before),
        "source_rows_after": len(source_after),
        "archive_segments": len(archive_rows),
        "archive_rows_verified": len(archived_rows),
        "archive": summary,
        "position_path_report": reports_after.get(args.position_id)
        if args.position_id
        else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.insert_audit_event(
        AuditEvent(
            actor="system",
            source="position_path_archive_shadow",
            event_type="position_path_archive_shadow_completed",
            entity_type="position_path_archive",
            message=json.dumps(report, sort_keys=True, separators=(",", ":")),
        )
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":") if args.as_json else None))
    return 0 if report["status"] in {"passed", "skipped"} else 1


def _source_rows(
    db,
    position_id: str | None,
    start_ms: int | None,
    end_ms: int | None,
) -> list[dict]:
    clauses = []
    params: list[object] = []
    if position_id:
        clauses.append("position_id=?")
        params.append(position_id)
    if start_ms is not None:
        clauses.append("bucket_open_ms>=?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("bucket_open_ms<?")
        params.append(end_ms)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return db.execute(
        "SELECT * FROM position_path_observations"
        + where
        + " ORDER BY position_id, bucket_open_ms",
        tuple(params),
    ).fetchall()


def _canonical_rows(rows: list[dict]) -> list[dict]:
    return [
        canonical_row(row)
        for row in sorted(
            rows,
            key=lambda row: (str(row["position_id"]), int(row["bucket_open_ms"])),
        )
    ]


def _visible_as_of(row: dict, as_of: str | None) -> bool:
    if as_of is None:
        return True
    try:
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(
            str(row["observed_at"]).replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("as_of and observed_at must be ISO timestamps") from exc
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc) <= cutoff.astimezone(timezone.utc)


def _parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("as_of must be an ISO timestamp") from exc
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return cutoff.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _projection(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    mapping = value if isinstance(value, dict) else {}
    return {key: mapping.get(key) for key in keys}


def _coverage_projection(value: Any) -> dict[str, Any]:
    return _projection(
        value,
        (
            "path_evidence_status",
            "path_evidence_reason",
            "expected_bucket_count",
            "covered_bucket_count",
            "missing_bucket_count",
            "available_count",
            "unavailable_count",
        ),
    )


def _observer_projection(value: Any) -> dict[str, Any]:
    story = value if isinstance(value, dict) else {}
    audit = story.get("audit") if isinstance(story.get("audit"), dict) else {}
    observer = audit.get("observer") if isinstance(audit.get("observer"), dict) else {}
    return _projection(
        observer,
        (
            "status",
            "reason",
            "expected_bucket_count",
            "available_bucket_count",
            "unavailable_bucket_count",
            "missing_bucket_count",
            "missing_bucket_open_ms",
        ),
    )


def _excursion_projection(value: Any) -> dict[str, Any]:
    return _projection(
        value,
        (
            "mfe_observed_pct",
            "mae_observed_pct",
            "max_giveback_to_last_observation_pct",
            "giveback_to_exit_pct",
        ),
    )


def _story_excursion_projection(value: Any) -> dict[str, Any]:
    story = value if isinstance(value, dict) else {}
    return _projection(
        story.get("economic_path"),
        (
            "maximum_favorable_excursion_pct",
            "maximum_adverse_excursion_pct",
            "giveback_from_peak_to_exit_pct",
        ),
    )


def _structured_events(value: Any) -> list[Any]:
    story = value if isinstance(value, dict) else {}
    events = story.get("structured_events")
    return events if isinstance(events, list) else []


def _position_replay_projection(value: Any) -> str:
    replay = value if isinstance(value, dict) else {}
    stages = replay.get("stages") if isinstance(replay.get("stages"), dict) else {}
    holding = stages.get("holding") if isinstance(stages.get("holding"), dict) else {}
    return _canonical_json(holding.get("position_path"))


def _provenance_projection(rows: list[dict]) -> list[dict[str, Any]]:
    return [
        _projection(
            row,
            (
                "position_id",
                "bucket_open_ms",
                "observed_at",
                "producer_commit",
                "runtime_session_id",
                "capture_mode",
                "evidence_status",
                "evidence_reason",
            ),
        )
        for row in sorted(
            rows,
            key=lambda item: (str(item.get("position_id")), int(item.get("bucket_open_ms", 0))),
        )
    ]


def _hot_count(db, position_id: str | None, start_ms: int | None, end_ms: int | None) -> int:
    clauses = []
    params: list[object] = []
    if position_id:
        clauses.append("position_id=?")
        params.append(position_id)
    if start_ms is not None:
        clauses.append("bucket_open_ms>=?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("bucket_open_ms<?")
        params.append(end_ms)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return int(
        db.execute(
            "SELECT COUNT(*) FROM position_path_observations" + where,
            tuple(params),
        ).fetchone()[0]
    )


def _archive_rows(db, position_id: str | None, start_ms: int | None, end_ms: int | None):
    clauses = []
    params: list[object] = []
    if position_id:
        clauses.append("position_id=?")
        params.append(position_id)
    if start_ms is not None:
        clauses.append("segment_end_ms>?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("segment_start_ms<?")
        params.append(end_ms)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        return db.execute(
            "SELECT * FROM position_path_archive_segments"
            + where
            + " ORDER BY position_id, segment_start_ms",
            tuple(params),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table: position_path_archive_segments" not in str(exc).lower():
            raise
        return []


if __name__ == "__main__":
    raise SystemExit(main())
