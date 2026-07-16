"""Temporary source export through the existing backend CI artifact channel."""

from __future__ import annotations

import tarfile
from pathlib import Path


def test_export_source_for_agent_checkout() -> None:
    root = Path(__file__).resolve().parents[2]
    output = root / "test-results" / "aletheia-source.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)

    excluded = {".git", ".venv", "node_modules", "test-results", "dist", "build"}
    with tarfile.open(output, "w:gz") as archive:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part in excluded for part in relative.parts):
                continue
            archive.add(path, arcname=relative, recursive=False)

    assert output.exists()
    assert output.stat().st_size > 0
