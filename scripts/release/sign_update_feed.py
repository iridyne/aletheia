"""Create an Ed25519-signed packaged-runtime update feed.

The private key is an operator/CI secret and must never be committed. The installed tray
receives only the matching public PEM. The signature covers the exact UTF-8
``signed_payload`` string stored in the envelope, avoiding JSON canonicalisation
ambiguity between producer and client.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(archive: Path) -> dict[str, Any]:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            candidates = [
                member
                for member in bundle.getmembers()
                if member.isfile() and Path(member.name).name == "release-manifest.json"
            ]
            if len(candidates) != 1:
                raise ValueError("archive must contain exactly one release-manifest.json")
            stream = bundle.extractfile(candidates[0])
            if stream is None:
                raise ValueError("release manifest could not be read")
            manifest = json.loads(stream.read())
    except (tarfile.TarError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid packaged runtime archive: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("target") != "packaged-runtime":
        raise ValueError("archive is not a packaged-runtime payload")
    return manifest


def _private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("update signing key must be Ed25519")
    return key


def signed_feed(
    *,
    archive: Path,
    artifact_url: str,
    private_key_path: Path,
    release_id: str | None = None,
    health_soak_sec: int = 30,
    published_at: str | None = None,
) -> dict[str, Any]:
    archive = archive.resolve()
    manifest = _load_manifest(archive)
    platform_id = str(manifest["platform"])
    version = str(manifest["version"])
    git_sha = str(manifest["git_sha"])
    release_id = release_id or f"{version}+{git_sha[:12]}"
    if not release_id or any(ch in release_id for ch in "/\\") or release_id in {".", ".."}:
        raise ValueError("release id must be a path-safe identifier")
    payload = {
        "schema_version": 1,
        "release_id": release_id,
        "version": version,
        "git_sha": git_sha,
        "published_at": published_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "health_soak_sec": max(0, min(int(health_soak_sec), 120)),
        "artifacts": {
            platform_id: {
                "url": artifact_url,
                "sha256": _sha256(archive),
                "size_bytes": archive.stat().st_size,
                "archive_format": "tar.gz",
            }
        },
    }
    signed_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = _private_key(private_key_path).sign(signed_payload.encode("utf-8"))
    return {
        "schema_version": 1,
        "signed_payload": signed_payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--health-soak-sec", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    envelope = signed_feed(
        archive=args.archive,
        artifact_url=args.artifact_url,
        private_key_path=args.private_key,
        release_id=args.release_id,
        health_soak_sec=args.health_soak_sec,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
