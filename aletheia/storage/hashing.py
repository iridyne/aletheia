"""Content-addressable hashing utilities for the persistence layer."""

import hashlib
import json
from pathlib import Path


def canonical_json(obj: dict | list) -> str:
    """Produce a deterministic JSON string (sorted keys, no extra whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: dict | list) -> str:
    """Return hex SHA-256 of the canonical JSON representation."""
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def sha256_file(path: Path | str) -> str:
    """Return hex SHA-256 of a file's contents (streaming, memory-safe)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def content_hash(text: str) -> str:
    """Return hex SHA-256 of a UTF-8 text string."""
    return sha256_bytes(text.encode("utf-8"))
