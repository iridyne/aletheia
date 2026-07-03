"""Classify pytest --durations output into two actionable slow-test tags.

Usage:
    uv run pytest tests/ --tb=no --durations=20 -q 2>&1 |
      uv run python scripts/pytest_slow_classifier.py --threshold 1.0

The classifier is intentionally phase-based:

- slow_optimizable: slow test body (`call`) worth inspecting for mocks/fixtures.
- slow_structural: slow setup/teardown overhead, usually framework or fixture cost.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DURATION_RE = re.compile(
    r"^\s*(?P<seconds>\d+(?:\.\d+)?)s\s+"
    r"(?P<phase>setup|call|teardown)\s+"
    r"(?P<nodeid>tests[\\/].+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SlowRecord:
    seconds: float
    phase: str
    nodeid: str
    tag: str


def parse_duration_lines(lines: Iterable[str], *, threshold: float = 1.0) -> list[SlowRecord]:
    records: list[SlowRecord] = []
    for line in lines:
        match = DURATION_RE.match(line.strip())
        if not match:
            continue
        seconds = float(match.group("seconds"))
        if seconds < threshold:
            continue
        phase = match.group("phase").lower()
        nodeid = match.group("nodeid").replace("\\", "/")
        records.append(
            SlowRecord(
                seconds=seconds,
                phase=phase,
                nodeid=nodeid,
                tag=classify_duration_phase(phase),
            )
        )
    records.sort(key=lambda item: item.seconds, reverse=True)
    return records


def classify_duration_phase(phase: str) -> str:
    return "slow_optimizable" if phase == "call" else "slow_structural"


def summarize(records: list[SlowRecord]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {
        "slow_optimizable": {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
        "slow_structural": {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
    }
    for record in records:
        bucket = summary[record.tag]
        bucket["count"] = int(bucket["count"]) + 1
        bucket["total_seconds"] = round(float(bucket["total_seconds"]) + record.seconds, 3)
        bucket["max_seconds"] = max(float(bucket["max_seconds"]), record.seconds)
    return summary


def render_table(records: list[SlowRecord], *, top: int | None = None) -> str:
    selected = records[:top] if top else records
    lines = [
        "tag               seconds  phase     nodeid",
        "----------------  -------  --------  ----------------------------------------",
    ]
    for record in selected:
        lines.append(
            f"{record.tag:<16}  {record.seconds:>7.2f}  {record.phase:<8}  {record.nodeid}"
        )
    lines.append("")
    lines.append("summary:")
    for tag, values in summarize(records).items():
        lines.append(
            f"- {tag}: count={values['count']} total_seconds={values['total_seconds']:.2f} "
            f"max_seconds={values['max_seconds']:.2f}"
        )
    return "\n".join(lines)


def _read_lines(path: str | None) -> list[str]:
    if path:
        return Path(path).read_text(encoding="utf-8").splitlines()
    return sys.stdin.read().splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="Optional file containing pytest --durations output.")
    parser.add_argument("--threshold", type=float, default=1.0, help="Minimum seconds to include.")
    parser.add_argument("--top", type=int, default=None, help="Limit rendered records.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    records = parse_duration_lines(_read_lines(args.input), threshold=args.threshold)
    if args.json:
        print(json.dumps({"records": [asdict(r) for r in records], "summary": summarize(records)}, indent=2))
    else:
        print(render_table(records, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

