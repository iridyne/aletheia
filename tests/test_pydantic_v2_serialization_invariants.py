from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
import pytest

from aletheia.model_provider.base import model_json_schema_payload


class _SchemaLike:
    def model_json_schema(self) -> dict:
        return {"type": "object"}


def _iter_python_files() -> Iterator[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    yield from (repo_root / "aletheia").rglob("*.py")


def test_no_v1_model_compatibility_fallbacks() -> None:
    patterns: list[tuple[str, str]] = [
        (r"hasattr\([^\n]*model_dump", "legacy hasattr(model_dump) compatibility branch"),
        (r"\.model_dump\(\) if .* else dict\(", "model_dump with dict fallback"),
        (r"hasattr\([^\n]*model_json_schema", "legacy hasattr(model_json_schema) fallback"),
    ]

    hits: list[tuple[Path, str, str]] = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for pattern, message in patterns:
            if re.search(pattern, text):
                hits.append((path, pattern, message))

    assert not hits, "Pydantic v1 fallback patterns remain: " + ", ".join(
        f"{path}:{message}" for path, _, message in hits
    )


def test_model_json_schema_payload_requires_schema_like_object() -> None:
    assert model_json_schema_payload(_SchemaLike()) == {"type": "object"}

    class _Broken:
        pass

    with pytest.raises(AttributeError):
        model_json_schema_payload(_Broken())
