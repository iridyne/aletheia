"""Build an unsigned browser-first macOS Aletheia tray application.

The app bundle contains only the stable Rust tray/CLI host and one bootstrap PyInstaller
runtime. The browser dashboard remains the sole product UI. Production signing and Apple
notarization are deliberately separate release-credential steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import zipfile

DEFAULT_BUNDLE_ID = "io.aletheia.trading-os"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_executable(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"required executable is missing or symbolic: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise RuntimeError(f"application package input cannot be a symbolic link: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"application package input contains a symbolic link: {path}")


def _validate_payload(payload_dir: Path) -> dict:
    _reject_symlinks(payload_dir)
    manifest_path = payload_dir / "release-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"packaged runtime manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("target") != "packaged-runtime":
        raise RuntimeError("bootstrap payload is not a packaged-runtime manifest")
    if manifest.get("platform") != "darwin-arm64":
        raise RuntimeError(
            f"macOS app requires darwin-arm64 payload, got {manifest.get('platform')!r}"
        )
    entrypoint = payload_dir / str(manifest.get("runtime_entrypoint") or "")
    if not entrypoint.is_file():
        raise RuntimeError(f"bootstrap runtime entrypoint is missing: {entrypoint}")
    return manifest


def _info_plist(*, version: str, bundle_id: str) -> dict:
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Aletheia",
        "CFBundleExecutable": "aletheia-tray",
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Aletheia",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }


def _tray_launcher() -> str:
    return """#!/bin/sh
set -eu
CONTENTS=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RESOURCES="$CONTENTS/Resources"
HOST_ROOT=${ALETHEIA_HOST_ROOT:-"$HOME/Library/Application Support/Aletheia"}
CORE="$RESOURCES/runtime/aletheia-core/aletheia-core"
if [ ! -f "$HOST_ROOT/current.json" ]; then
  if [ -f "$RESOURCES/update-public-key.pem" ] && [ -f "$RESOURCES/update-feed-url.txt" ]; then
    "$CORE" __packaged_bootstrap --host-root "$HOST_ROOT" --source-root "$RESOURCES/runtime/aletheia-core" --public-key "$RESOURCES/update-public-key.pem" --feed-url-file "$RESOURCES/update-feed-url.txt"
  elif [ -f "$RESOURCES/update-public-key.pem" ]; then
    "$CORE" __packaged_bootstrap --host-root "$HOST_ROOT" --source-root "$RESOURCES/runtime/aletheia-core" --public-key "$RESOURCES/update-public-key.pem"
  elif [ -f "$RESOURCES/update-feed-url.txt" ]; then
    "$CORE" __packaged_bootstrap --host-root "$HOST_ROOT" --source-root "$RESOURCES/runtime/aletheia-core" --feed-url-file "$RESOURCES/update-feed-url.txt"
  else
    "$CORE" __packaged_bootstrap --host-root "$HOST_ROOT" --source-root "$RESOURCES/runtime/aletheia-core"
  fi
fi
exec "$CONTENTS/MacOS/aletheia-tray-bin" "$@"
"""


def _cli_installer() -> str:
    return """#!/bin/sh
set -eu
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN_DIR=${ALETHEIA_CLI_BIN_DIR:-"$HOME/.local/bin"}
mkdir -p "$BIN_DIR"
ln -sfn "$APP_ROOT/MacOS/aletheia" "$BIN_DIR/aletheia"
ln -sfn "$APP_ROOT/MacOS/ato" "$BIN_DIR/ato"
printf '%s\n' "Installed Aletheia CLI launchers in $BIN_DIR"
case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *) printf '%s\n' "Add $BIN_DIR to PATH to use 'aletheia' and 'ato' from a new terminal." ;;
esac
"""


def _zip_mode(path: Path) -> int:
    mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
    return stat.S_IFREG | mode


def _deterministic_zip(source_root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(source_root.parent).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _zip_mode(path) << 16
            info.create_system = 3
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def build_macos_app(
    *,
    payload_dir: Path,
    host_bin_dir: Path,
    output_dir: Path,
    version: str | None = None,
    bundle_id: str = DEFAULT_BUNDLE_ID,
    public_key: Path | None = None,
    feed_url_file: Path | None = None,
) -> dict:
    if payload_dir.is_symlink() or host_bin_dir.is_symlink():
        raise RuntimeError("application package roots cannot be symbolic links")
    payload_dir = payload_dir.resolve()
    host_bin_dir = host_bin_dir.resolve()
    output_dir = output_dir.resolve()
    manifest = _validate_payload(payload_dir)
    app_version = version or str(manifest.get("version") or "0.0.0")

    app = output_dir / "Aletheia.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    runtime_destination = resources / "runtime" / "aletheia-core"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    _copy_executable(host_bin_dir / "aletheia-tray", macos / "aletheia-tray-bin")
    launcher = macos / "aletheia-tray"
    launcher.write_text(_tray_launcher(), encoding="utf-8", newline="\n")
    launcher.chmod(0o755)
    _copy_executable(host_bin_dir / "aletheia", macos / "aletheia")
    _copy_executable(host_bin_dir / "aletheia", macos / "ato")
    shutil.copytree(payload_dir, runtime_destination, symlinks=False)

    if public_key is not None:
        if public_key.is_symlink():
            raise RuntimeError(f"update public key cannot be symbolic: {public_key}")
        public_key = public_key.resolve()
        if not public_key.is_file():
            raise RuntimeError(f"update public key is missing: {public_key}")
        shutil.copy2(public_key, resources / "update-public-key.pem")
    if feed_url_file is not None:
        if feed_url_file.is_symlink():
            raise RuntimeError(f"update feed URL file cannot be symbolic: {feed_url_file}")
        feed_url_file = feed_url_file.resolve()
        if not feed_url_file.is_file() or not feed_url_file.read_text(encoding="utf-8").strip():
            raise RuntimeError(f"update feed URL file is missing or empty: {feed_url_file}")
        shutil.copy2(feed_url_file, resources / "update-feed-url.txt")

    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(_info_plist(version=app_version, bundle_id=bundle_id), handle, sort_keys=True)
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
    installer = resources / "install-cli.sh"
    installer.write_text(_cli_installer(), encoding="utf-8", newline="\n")
    installer.chmod(0o755)

    archive = output_dir / f"Aletheia-{app_version}-darwin-arm64.zip"
    _deterministic_zip(app, archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "target": "macos-tray-app",
        "version": app_version,
        "git_sha": manifest.get("git_sha"),
        "platform": manifest.get("platform"),
        "app": str(app),
        "archive": archive.name,
        "archive_sha256": _sha256(archive),
        "archive_size_bytes": archive.stat().st_size,
        "signed": False,
        "notarized": False,
    }
    report_path = output_dir / "macos-app-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"app": app, "archive": archive, "checksum": checksum, "report": report_path}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-dir", type=Path, required=True)
    parser.add_argument("--host-bin-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--feed-url-file", type=Path)
    args = parser.parse_args()
    result = build_macos_app(
        payload_dir=args.payload_dir,
        host_bin_dir=args.host_bin_dir,
        output_dir=args.output_dir,
        version=args.version,
        bundle_id=args.bundle_id,
        public_key=args.public_key,
        feed_url_file=args.feed_url_file,
    )
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
