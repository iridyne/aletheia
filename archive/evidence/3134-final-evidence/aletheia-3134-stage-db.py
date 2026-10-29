"""One-time trusted DB-only staging; no app imports or archive extraction API.

Usage: python stage-db.py RECEIPT_JSON NEW_TARGET_DIRECTORY
The receipt/archive must be private verified keeper artifacts. The target must
not exist. On failure leave any partial target private for diagnosis; never use
it without a successful receipt. No config/log/artifact payload is copied.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile

import yaml

CHUNK = 1024 * 1024
MAX_METADATA = 16 * CHUNK
MAX_DATABASE_BYTES = 64 * 1024**3
MAX_ARCHIVE_BYTES = 128 * 1024**3
MAX_MEMBERS = 100000
IDENTITIES = ("checkpoint_identity", "sqlite_identity", "lab_sqlite_identity", "lab_checkpoint_identity")


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def sha(handle):
    digest = hashlib.sha256()
    while chunk := handle.read(CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def safe_path(name):
    require(isinstance(name, str) and name and "\\" not in name and "\x00" not in name, "unsafe_member")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(p not in {"", ".", ".."} for p in name.rstrip("/").split("/")), "unsafe_member")
    return path


def stage(receipt_path, target):
    os.umask(0o077)
    require(receipt_path.stat().st_size <= MAX_METADATA, "receipt_too_large")
    receipt = json.loads(receipt_path.read_text())
    identities = receipt["backup_identities"]
    require(all(isinstance(identities.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", identities[k]) for k in IDENTITIES), "identity_invalid")
    require(identities["lab_checkpoint_identity"] == receipt["settled_joint_fingerprint"], "joint_identity_mismatch")
    archive = Path(receipt["archive"])
    require(archive.is_absolute() and not archive.is_symlink(), "archive_path_invalid")
    target = target.absolute()
    require(not target.exists() and not target.is_symlink(), "target_must_be_new")
    require(target.parent.is_dir() and all(not p.is_symlink() for p in (target.parent, *target.parents)), "unsafe_target_parent")
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "archive_not_regular")
        require(os.fstat(source.fileno()).st_size <= MAX_ARCHIVE_BYTES, "archive_size_limit")
        require(sha(source) == receipt["archive_sha256"], "archive_hash_mismatch")
        source.seek(0)
        with tarfile.open(fileobj=source, mode="r:gz") as bundle:
            members = {}
            roots = set()
            expanded_bytes = 0
            for member in bundle:
                path = safe_path(member.name)
                name = str(path)
                require(name not in members and len(members) < MAX_MEMBERS, "duplicate_or_excess_members")
                require(member.isdir() or member.isfile(), "nonregular_member")
                require(member.size >= 0, "invalid_member_size")
                expanded_bytes += member.size
                require(expanded_bytes <= MAX_ARCHIVE_BYTES, "expanded_archive_size_limit")
                roots.add(path.parts[0])
                members[name] = member
            require(len(roots) == 1, "multiple_archive_roots")
            root = next(iter(roots))

            def metadata(name):
                item = members.get(root + "/" + name)
                require(item is not None and item.isfile() and item.size <= MAX_METADATA, "metadata_invalid")
                with bundle.extractfile(item) as handle:
                    data = handle.read(MAX_METADATA + 1)
                require(len(data) == item.size and len(data) <= MAX_METADATA, "metadata_size_mismatch")
                return data.decode("utf-8")

            manifest = yaml.safe_load(metadata("manifest.yaml"))
            require(isinstance(manifest, dict) and manifest.get("backup_id") == root, "manifest_root_mismatch")
            require(manifest.get("profile") == "authoritative" and manifest.get("recovery_eligible") is True, "nonrecovery_archive")
            require(all(manifest.get(k) == identities[k] for k in IDENTITIES), "manifest_identity_mismatch")
            checksums = {}
            for line in metadata("checksums.txt").splitlines():
                digest, separator, name = line.partition("  ")
                require(separator and re.fullmatch(r"[0-9a-f]{64}", digest), "checksum_invalid")
                safe_path(name)
                require(name not in checksums, "duplicate_checksum")
                checksums[name] = digest
            wanted = {"app.db": "sqlite_identity", "observation-lab/lab.db": "lab_sqlite_identity"}
            selected = {}
            for name, member in members.items():
                relative = str(PurePosixPath(name).relative_to(root))
                require(not relative.endswith(("-wal", "-shm")), "unexpected_sqlite_sidecar")
                if relative.endswith(".db"):
                    require(relative in wanted and member.isfile(), "unexpected_database")
            for relative, identity in wanted.items():
                item = members.get(root + "/" + relative)
                require(item is not None and item.isfile(), "database_missing")
                require(checksums.get(relative) == identities[identity], "database_identity_mismatch")
                selected[relative] = item
            total = sum(item.size for item in selected.values())
            require(0 < total <= MAX_DATABASE_BYTES, "database_size_limit")
            require(shutil.disk_usage(target.parent).free >= total + 256 * CHUNK, "insufficient_staging_space")
            target.mkdir(mode=0o700)
            (target / "observation-lab").mkdir(mode=0o700)
            staged = []
            for relative, item in selected.items():
                output = target / relative
                require(output.resolve().is_relative_to(target.resolve()), "output_escape")
                digest = hashlib.sha256()
                count = 0
                with bundle.extractfile(item) as incoming, output.open("xb") as outgoing:
                    while chunk := incoming.read(CHUNK):
                        count += len(chunk)
                        require(count <= item.size, "database_size_mismatch")
                        outgoing.write(chunk)
                        digest.update(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                require(count == item.size and digest.hexdigest() == checksums[relative], "database_hash_mismatch")
                staged.append({"path": str(output), "sha256": digest.hexdigest(), "bytes": count})
        source.seek(0)
        require(sha(source) == receipt["archive_sha256"], "archive_changed_during_staging")
    return {"staged": staged, "database_count": len(staged), "bytes": total}


def selftest():
    with tempfile.TemporaryDirectory(prefix="aletheia-stage-db-test-") as directory:
        root = Path(directory).resolve()
        app, lab = b"synthetic-app-db", b"synthetic-lab-db"
        identities = dict(checkpoint_identity="a" * 64, sqlite_identity=hashlib.sha256(app).hexdigest(), lab_sqlite_identity=hashlib.sha256(lab).hexdigest(), lab_checkpoint_identity="b" * 64)
        manifest = dict(backup_id="backup_fixture", profile="authoritative", recovery_eligible=True, **identities)

        def fixture(name, unsafe=False):
            archive = root / (name + ".tar.gz")
            payloads = {"app.db": app, "observation-lab/lab.db": lab, "manifest.yaml": yaml.safe_dump(manifest).encode(), "checksums.txt": f"{identities['sqlite_identity']}  app.db\n{identities['lab_sqlite_identity']}  observation-lab/lab.db\n".encode(), "config.yaml": b"not copied"}
            if unsafe:
                payloads["../escape"] = b"reject"
            with tarfile.open(archive, "w:gz") as bundle:
                for relative, payload in payloads.items():
                    item = tarfile.TarInfo("backup_fixture/" + relative)
                    item.size = len(payload)
                    bundle.addfile(item, io.BytesIO(payload))
            with archive.open("rb") as handle:
                digest = sha(handle)
            receipt = root / (name + ".json")
            receipt.write_text(json.dumps(dict(archive=str(archive), archive_sha256=digest, backup_identities=identities, settled_joint_fingerprint=identities["lab_checkpoint_identity"])))
            return receipt, archive

        receipt, _ = fixture("happy")
        result = stage(receipt, root / "staged")
        assert result["database_count"] == 2
        assert sorted(str(p.relative_to(root / "staged")) for p in (root / "staged").rglob("*") if p.is_file()) == ["app.db", "observation-lab/lab.db"]
        for name, expected in (("tampered", "archive_hash_mismatch"), ("unsafe", "unsafe_member")):
            receipt, archive = fixture(name, unsafe=name == "unsafe")
            if name == "tampered":
                with archive.open("ab") as handle:
                    handle.write(b"tamper")
            try:
                stage(receipt, root / name)
            except RuntimeError as exc:
                assert str(exc) == expected, type(exc).__name__
            else:
                raise AssertionError("negative fixture accepted")
            assert not (root / name).exists()
    print(json.dumps({"synthetic_checks_passed": 3}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("target", nargs="?", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        if args.selftest:
            selftest()
        else:
            require(args.receipt is not None and args.target is not None, "arguments_required")
            print(json.dumps(stage(args.receipt, args.target), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
        raise SystemExit(1) from None
