"""Sanitized failure evidence for deterministic control-loop runs."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import yaml


REQUIRED_ARTIFACTS = frozenset({
    "run.json",
    "seed.txt",
    "initial_config.yaml",
    "final_config.yaml",
    "operation_sequence.json",
    "minimized_sequence.json",
    "timeline.jsonl",
    "app.db",
    "invariant_failure.json",
    "reproduce.txt",
})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def write_failure_artifacts(
    harness: Any,
    error: BaseException,
    *,
    artifact_root: Path,
    run_id: str,
    seed: int,
    reproduce: str,
) -> Path:
    """Write a self-contained, secret-free replay pack for one failed invariant."""
    target = artifact_root / run_id
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "run.json", {
        "run_id": run_id,
        "schema_version": 1,
        "virtual_now": harness.clock.now().isoformat(),
        "operation_count": len(harness.operations),
    })
    (target / "seed.txt").write_text(f"{seed}\n")
    (target / "initial_config.yaml").write_text(
        yaml.safe_dump(harness.initial_config, sort_keys=True)
    )
    (target / "final_config.yaml").write_text(
        yaml.safe_dump(harness.safe_config(), sort_keys=True)
    )
    _write_json(target / "operation_sequence.json", harness.operations)
    # Hypothesis performs shrinking before reporting the final failing machine.
    # The current sequence is therefore the minimized sequence at final teardown.
    _write_json(target / "minimized_sequence.json", harness.operations)
    with (target / "timeline.jsonl").open("w", encoding="utf-8") as stream:
        for item in harness.timeline:
            stream.write(json.dumps(item, sort_keys=True, default=str) + "\n")
    _write_json(target / "invariant_failure.json", {
        "type": type(error).__name__,
        "message": str(error),
    })
    (target / "reproduce.txt").write_text(reproduce.rstrip() + "\n")
    destination = sqlite3.connect(target / "app.db")
    try:
        harness.db.backup(destination)
    finally:
        destination.close()
    return target
