"""Canonical, data-root-bound Knowledge document access."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from aletheia.config.loader import get_data_dir
from aletheia.knowledge.frontmatter import parse_frontmatter_text


CONTENT_FORMAT = "knowledge-markdown/v1"


@dataclass(frozen=True)
class KnowledgeDocument:
    text: str
    metadata: dict[str, Any]
    state: str
    resolved_path: Path | None = None


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relative_knowledge_path(path: Path) -> str:
    """Return one portable path rooted at the selected data directory."""
    root = get_data_dir().resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Knowledge path is outside the selected data root") from exc
    if not relative.parts or relative.parts[0].lower() != "knowledge":
        raise ValueError("Knowledge path is outside the Knowledge root")
    return relative.as_posix()


def resolve_knowledge_path(file_path: str | None) -> Path | None:
    """Resolve relative or legacy paths only inside the selected Knowledge root."""
    raw = str(file_path or "").strip()
    if not raw:
        return None
    root = get_data_dir().resolve()
    knowledge_root = (root / "knowledge").resolve()
    relative: Path | None = None
    for path_type in (PureWindowsPath, PurePosixPath):
        try:
            parts = path_type(raw).parts
        except Exception:
            continue
        lowered = [part.lower() for part in parts]
        if "knowledge" not in lowered:
            continue
        index = lowered.index("knowledge")
        relative = Path(*parts[index:])
        break
    if relative is None:
        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            return None
        relative = candidate_path
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(knowledge_root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None

def _json_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize YAML scalar types into a stable JSON-compatible snapshot."""
    return json.loads(json.dumps(metadata, default=str))


def canonicalize_written_document(path: Path) -> dict[str, Any]:
    """Return fields persisted for a newly written canonical Markdown document."""
    text = path.read_text(encoding="utf-8")
    metadata, _ = parse_frontmatter_text(text)
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "file_path": relative_knowledge_path(path),
        "content": text,
        "content_format": CONTENT_FORMAT,
        "content_sha256": content_sha256(text),
        "metadata_json": json.dumps(
            _json_metadata(metadata),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        "symbol": _normalise_symbol(metadata.get("symbol")),
        "direction": _normalise_token(metadata.get("direction")),
        "scanner_family": _normalise_token(metadata.get("scanner_family")),
        "outcome": _normalise_token(metadata.get("outcome")),
    }


def read_knowledge_document(row: dict[str, Any]) -> KnowledgeDocument:
    """Read one verified document without trusting a foreign absolute path."""
    inline = str(row.get("content") or "")
    inline_format = str(row.get("content_format") or "")
    inline_hash = str(row.get("content_sha256") or "")
    inline_canonical = inline_format == CONTENT_FORMAT
    if inline_canonical and (
        not inline_hash or content_sha256(inline) != inline_hash
    ):
        return KnowledgeDocument("", {}, "integrity_conflict")

    path = resolve_knowledge_path(row.get("file_path"))
    file_text = ""
    if path is not None:
        try:
            file_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            path = None

    if inline_canonical:
        if file_text and content_sha256(file_text) != inline_hash:
            return KnowledgeDocument("", {}, "integrity_conflict", path)
        metadata, _ = parse_frontmatter_text(inline)
        expected_metadata = _metadata_from_json(row.get("metadata_json"))
        if expected_metadata is None or _json_metadata(metadata) != expected_metadata:
            return KnowledgeDocument("", {}, "integrity_conflict", path)
        return KnowledgeDocument(
            inline,
            metadata if isinstance(metadata, dict) else {},
            "available" if file_text else "available_inline",
            path,
        )

    # Legacy rows are accepted only from the exact current-root relative location.
    # Body-only inline copies remain readable prose but cannot invent metadata.
    if file_text:
        metadata, _ = parse_frontmatter_text(file_text)
        return KnowledgeDocument(
            file_text,
            metadata if isinstance(metadata, dict) else {},
            "legacy_current_root",
            path,
        )
    if inline:
        metadata, _ = parse_frontmatter_text(inline)
        state = "legacy_inline_document" if metadata else "legacy_body_only"
        return KnowledgeDocument(
            inline, metadata if isinstance(metadata, dict) else {}, state
        )
    return KnowledgeDocument("", {}, "unavailable")


def _metadata_from_json(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _normalise_symbol(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _normalise_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
