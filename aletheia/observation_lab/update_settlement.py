"""Minimal Observation continuity cut used only by source/release activation.

The ordinary Observation bridge also mirrors audit intents, prunes acknowledged
transport rows and compacts finalized generations. Those are durable maintenance
operations, not prerequisites for switching source. This module keeps the
update-critical section limited to the Main delivery frontier and one strict
joint checkpoint while the caller holds ``worker_cycle_exclusion``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _delivery_transport_state(*, source_name: str = "main") -> dict[str, Any]:
    """Read only the facts needed to prove the Main→Lab delivery frontier idle."""
    from aletheia.config.loader import get_data_dir
    from aletheia.observation_lab.canonical import _hash
    from aletheia.observation_lab.db import readonly_lab_db
    from aletheia.storage.sqlite import read_snapshot

    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        return {"available": False, "reason": "lab_unavailable", "settled": False}

    try:
        with read_snapshot() as main:
            row = main.execute(
                "SELECT SUM(CASE WHEN acknowledged_at IS NULL THEN 1 ELSE 0 END), "
                "MAX(sequence) FROM observation_delivery_outbox"
            ).fetchone()
            sequence_row = main.execute(
                "SELECT seq FROM sqlite_sequence "
                "WHERE name='observation_delivery_outbox'"
            ).fetchone()
        pending_count = int(row[0] or 0)
        max_present_sequence = int(row[1] or 0)
        outbox_high_watermark = max(
            max_present_sequence,
            int(sequence_row[0]) if sequence_row is not None else 0,
        )

        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return {
                    "available": False,
                    "reason": "lab_unavailable",
                    "settled": False,
                }
            cursor = lab.execute(
                "SELECT contiguous_sequence, retry_sequences_json, "
                "source_checkpoint_json, source_checkpoint_fingerprint "
                "FROM observation_import_cursors WHERE source_name=?",
                (source_name,),
            ).fetchone()

        if cursor is None:
            # A fresh Lab legitimately has no cursor. Sequence zero is its
            # implicit starting cut; any existing Main suffix is importable.
            cursor_sequence = 0
            retry_sequences: list[int] = []
            cursor_valid = True
        else:
            cursor_sequence = int(cursor[0])
            retry_sequences = json.loads(cursor[1] or "[]")
            source_checkpoint = json.loads(cursor[2] or "{}")
            cursor_valid = (
                cursor_sequence >= 0
                and isinstance(retry_sequences, list)
                and all(
                    isinstance(item, int) and item >= 0
                    for item in retry_sequences
                )
                and len(set(retry_sequences)) == len(retry_sequences)
                and isinstance(source_checkpoint, dict)
                and source_checkpoint.get("source_name") == source_name
                and source_checkpoint.get("contiguous_sequence") == cursor_sequence
                and source_checkpoint.get("retry_sequences") == retry_sequences
                and isinstance(cursor[3], str)
                and _hash(source_checkpoint) == cursor[3]
                and isinstance(
                    source_checkpoint.get("receipt_chain_fingerprint"), str
                )
                and len(source_checkpoint["receipt_chain_fingerprint"]) == 64
                and all(
                    char in "0123456789abcdef"
                    for char in source_checkpoint["receipt_chain_fingerprint"]
                )
                and (
                    cursor_sequence == 0
                    or (
                        isinstance(source_checkpoint.get("last_receipt_id"), str)
                        and bool(source_checkpoint["last_receipt_id"])
                    )
                )
            )

        if not cursor_valid:
            return {
                "available": False,
                "reason": "source_checkpoint_invalid",
                "settled": False,
                "outbox_pending_count": pending_count,
                "outbox_high_watermark": outbox_high_watermark,
                "lab_imported_watermark": cursor_sequence,
                "retry_sequences": retry_sequences,
            }

        settled = bool(
            pending_count == 0
            and not retry_sequences
            and cursor_sequence == outbox_high_watermark
        )
        reason = (
            "fingerprint_frontier_settled"
            if settled
            else "delivery_import_retry_pending"
            if retry_sequences
            else "delivery_import_pending"
            if pending_count
            else "delivery_import_cursor_mismatch"
        )
        return {
            "available": True,
            "reason": reason,
            "settled": settled,
            "outbox_pending_count": pending_count,
            "outbox_high_watermark": outbox_high_watermark,
            "lab_imported_watermark": cursor_sequence,
            "retry_sequences": retry_sequences,
        }
    except (
        FileNotFoundError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "available": False,
            "reason": "delivery_transport_state_unavailable",
            "error": type(exc).__name__,
            "settled": False,
        }


def _drain_delivery_receipts(
    *, batch_size: int = 1000, max_batches: int = 100
) -> dict[str, Any]:
    """Drain a bounded contiguous prefix; never skip poison or retry state."""
    from aletheia.observation_lab.canonical import import_delivery_receipts

    batch = max(1, min(int(batch_size), 1000))
    limit = max(1, int(max_batches))
    processed_total = 0
    last: dict[str, Any] = {
        "available": True,
        "processed": 0,
        "failed_sequence": None,
        "retry_sequences": [],
        "backlog_count": 0,
    }
    for _ in range(limit):
        last = import_delivery_receipts(limit=batch)
        processed_total += int(last.get("processed") or 0)
        if (
            last.get("available") is not True
            or last.get("failed_sequence") is not None
            or last.get("retry_sequences")
        ):
            return {
                "ok": False,
                "reason": (
                    last.get("reason")
                    or last.get("failed_reason")
                    or "delivery_import_failed"
                ),
                "delivery_import": {
                    **last,
                    "processed": processed_total,
                },
            }
        if int(last.get("processed") or 0) < batch:
            return {
                "ok": True,
                "delivery_import": {
                    **last,
                    "processed": processed_total,
                },
            }
    return {
        "ok": False,
        "reason": "delivery_import_batch_limit",
        "delivery_import": {**last, "processed": processed_total},
    }


def settle_update_continuity_locked() -> dict[str, Any]:
    """Produce the smallest deployment-authorizing Observation continuity cut.

    The caller must already hold ``worker_cycle_exclusion`` and keep it held
    through authoritative backup and source activation.
    """
    from aletheia.config.loader import get_data_dir

    started = time.monotonic()
    timings: dict[str, int] = {}

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        payload["timings_ms"] = {**timings, "total": _elapsed_ms(started)}
        return payload

    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        return finish({"ok": False, "reason": "lab_unavailable"})

    from aletheia.observation_lab import runner
    from aletheia.observation_lab.canonical import delivery_import_interruption_status
    from aletheia.observation_lab.checkpoint import verify_joint_checkpoint

    step = time.monotonic()
    checkpoint = runner._pass_checkpoint()
    timings["checkpoint_preverify"] = _elapsed_ms(step)
    if not checkpoint.get("ok"):
        step = time.monotonic()
        interruption = delivery_import_interruption_status()
        timings["interruption_diagnosis"] = _elapsed_ms(step)
        return finish({
            "ok": False,
            "reason": checkpoint.get("reason"),
            "checkpoint": checkpoint,
            "delivery_import_interruption": interruption,
        })

    step = time.monotonic()
    before = _delivery_transport_state()
    timings["transport_state_before"] = _elapsed_ms(step)
    if before.get("available") is not True:
        return finish({
            "ok": False,
            "reason": before.get("reason") or "delivery_transport_state_unavailable",
            "checkpoint": checkpoint,
            "transport_state": before,
        })
    if before.get("retry_sequences"):
        return finish({
            "ok": False,
            "reason": "delivery_import_retry_pending",
            "checkpoint": checkpoint,
            "transport_state": before,
        })

    # Strictly verified + physically idle under the cycle lock means there is
    # nothing to settle. Recomputing/saving the same full-Lab fingerprint would
    # add cost without adding proof.
    if (
        checkpoint.get("reason") == "fingerprint_match"
        and before.get("settled") is True
    ):
        return finish({
            "ok": True,
            "reason": "fingerprint_match",
            "fast_path": True,
            "maintenance_deferred": True,
            "delivery_import": {
                "available": True,
                "processed": 0,
                "failed_sequence": None,
                "retry_sequences": [],
                "backlog_count": 0,
            },
            "transport_state": before,
            "checkpoint": checkpoint,
        })

    step = time.monotonic()
    drained = _drain_delivery_receipts()
    timings["delivery_import"] = _elapsed_ms(step)
    if drained.get("ok") is not True:
        return finish({
            "ok": False,
            "reason": drained.get("reason") or "delivery_import_failed",
            "checkpoint": checkpoint,
            "delivery_import": drained.get("delivery_import"),
        })

    step = time.monotonic()
    after = _delivery_transport_state()
    timings["transport_state_after"] = _elapsed_ms(step)
    if after.get("available") is not True or after.get("settled") is not True:
        return finish({
            "ok": False,
            "reason": after.get("reason") or "delivery_import_incomplete",
            "checkpoint": checkpoint,
            "delivery_import": drained.get("delivery_import"),
            "transport_state": after,
        })

    step = time.monotonic()
    runner._save_checkpoint(checkpoint)
    timings["checkpoint_save"] = _elapsed_ms(step)
    if not checkpoint.get("ok"):
        return finish({
            "ok": False,
            "reason": checkpoint.get("reason"),
            "checkpoint": checkpoint,
            "delivery_import": drained.get("delivery_import"),
            "transport_state": after,
        })

    step = time.monotonic()
    strict = verify_joint_checkpoint()
    timings["checkpoint_postverify"] = _elapsed_ms(step)
    if not strict.ok or strict.reason != "fingerprint_match":
        return finish({
            "ok": False,
            "reason": strict.reason,
            "checkpoint": checkpoint,
            "delivery_import": drained.get("delivery_import"),
            "transport_state": after,
        })

    return finish({
        "ok": True,
        "reason": strict.reason,
        "fast_path": False,
        "maintenance_deferred": True,
        "delivery_import": drained["delivery_import"],
        "transport_state": after,
        "checkpoint": checkpoint,
    })


__all__ = ["settle_update_continuity_locked"]
