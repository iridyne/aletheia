"""Content-addressable ArtifactStore with atomic writes and manifest generation."""

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import canonical_json, sha256_bytes, sha256_file
from aletheia.storage.models import PSArtifactRef


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactStore:
    """Content-addressable artifact store rooted under data/artifacts/."""

    def __init__(self, root: Path | None = None):
        self._root = root or (get_data_dir() / "artifacts")
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put_json(self, relative_path: str, obj: dict | list) -> PSArtifactRef:
        """Write a JSON artifact atomically. Returns ref with sha256."""
        text = canonical_json(obj)
        return self._atomic_write(relative_path, text.encode("utf-8"), "application/json")

    def put_yaml(self, relative_path: str, obj: dict | list) -> PSArtifactRef:
        """Write a YAML artifact atomically."""
        text = yaml.dump(obj, default_flow_style=False, allow_unicode=True, sort_keys=True)
        return self._atomic_write(relative_path, text.encode("utf-8"), "application/yaml")

    def put_text(self, relative_path: str, text: str, content_type: str = "text/plain") -> PSArtifactRef:
        """Write a text artifact atomically."""
        return self._atomic_write(relative_path, text.encode("utf-8"), content_type)

    def put_jsonl(self, relative_path: str, rows: list[dict]) -> PSArtifactRef:
        """Write a JSONL artifact (one JSON object per line)."""
        lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
        text = "\n".join(lines) + "\n" if lines else ""
        return self._atomic_write(relative_path, text.encode("utf-8"), "application/jsonl")

    def get_text(self, ref: PSArtifactRef) -> str:
        """Read artifact content as text."""
        path = self._root / ref.path
        return path.read_text(encoding="utf-8")

    def get_json(self, ref: PSArtifactRef) -> dict | list:
        """Read a JSON artifact."""
        return json.loads(self.get_text(ref))

    def verify(self, ref: PSArtifactRef) -> bool:
        """Verify artifact integrity by recomputing sha256."""
        path = self._root / ref.path
        if not path.exists():
            return False
        return sha256_file(path) == ref.sha256

    def exists(self, relative_path: str) -> bool:
        """Check if an artifact exists at the given path."""
        return (self._root / relative_path).exists()

    def make_manifest(
        self,
        bundle_path: str,
        refs: list[PSArtifactRef],
        metadata: dict,
    ) -> PSArtifactRef:
        """Generate a manifest.yaml listing all artifacts in a bundle."""
        manifest = {
            "bundle_path": bundle_path,
            "schema_version": "v1",
            "created_at": _utcnow_iso(),
            "machine_id": platform.node() or "unknown",
            **metadata,
            "objects": [
                {"path": r.path, "sha256": r.sha256, "content_type": r.content_type}
                for r in refs
            ],
        }
        manifest_path = f"{bundle_path}/manifest.yaml"
        return self.put_yaml(manifest_path, manifest)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _atomic_write(self, relative_path: str, data: bytes, content_type: str) -> PSArtifactRef:
        """Write data to path atomically (write .tmp then rename)."""
        target = self._root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = target.with_suffix(target.suffix + ".tmp")
        tmp_path.write_bytes(data)

        # Atomic rename (on Windows this requires removing target first if exists)
        if os.name == "nt" and target.exists():
            target.unlink()
        tmp_path.rename(target)

        file_hash = sha256_bytes(data)
        return PSArtifactRef(
            path=relative_path,
            sha256=file_hash,
            content_type=content_type,
            created_at=_utcnow_iso(),
        )


# Module-level singleton
_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """Get the module-level ArtifactStore singleton."""
    global _store
    if _store is None:
        _store = ArtifactStore()
    return _store
