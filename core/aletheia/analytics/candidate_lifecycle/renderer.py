"""Human-readable rendering of lifecycle_diagnosis/v1."""
from __future__ import annotations

from typing import Any


def render_lifecycle_diagnosis(diagnosis: dict[str, Any]) -> str:
    anchor = diagnosis.get("anchor", {})
    source = diagnosis.get("source", {})
    lines = [
        f"Lifecycle diagnosis — {anchor.get('kind')} {anchor.get('id')}",
        f"source_kind: {source.get('kind')}  attention: {diagnosis.get('attention_state')}",
        f"cutoff: {diagnosis.get('evidence_context', {}).get('cutoff')}",
        "",
        "Stages:",
    ]
    for stage in diagnosis.get("stages", []):
        mark = "ok" if stage.get("status") == "available" else "MISSING"
        count = f" [{stage['count']} legs]" if stage.get("count") else ""
        lines.append(f"  {mark:7}  {stage['stage']}{count}")

    fb = diagnosis.get("first_break")
    if fb:
        lines.extend(["", f"First break ({fb['stage']}): {fb['reason']}"])

    coverage = diagnosis.get("coverage", {})
    if coverage.get("gaps"):
        lines.extend(["", "Evidence gaps:", *[f"  - {g}" for g in coverage["gaps"]]])
    if coverage.get("identity_conflicts"):
        lines.extend(["", "Identity conflicts:", *[f"  - {c}" for c in coverage["identity_conflicts"]]])

    ids = source.get("ids", {})
    if ids:
        lines.extend(["", "Resolved IDs:"])
        for k, v in ids.items():
            lines.append(f"  {k}: {v}")

    hints = diagnosis.get("next_inspection", [])
    if hints:
        lines.extend(["", "Next inspection:"])
        for h in hints:
            args = " ".join(f"{k} {v}" for k, v in h.get("args", {}).items())
            lines.append(f"  {h['command']} {args}")

    return "\n".join(lines)
