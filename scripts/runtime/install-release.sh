#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-release.sh --archive PATH [options]

Options:
  --checksum PATH       SHA-256 sidecar (default: ARCHIVE.sha256)
  --releases-root PATH  Immutable release directory
                        (default: ~/Library/Application Support/Aletheia/releases)
  --no-activate         Install without updating the sibling current symlink
  --skip-checksum       Allow installation without a SHA-256 sidecar
  --skip-sync           Do not create/synchronize the locked production environment
  -h, --help            Show this help
EOF
}

fail() {
  printf 'Aletheia release install failed: %s\n' "$*" >&2
  exit 1
}

archive=""
checksum=""
releases_root="${ALETHEIA_RELEASES_ROOT:-$HOME/Library/Application Support/Aletheia/releases}"
activate=1
skip_checksum=0
skip_sync=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      [[ $# -ge 2 ]] || fail "--archive requires a path"
      archive="$2"
      shift 2
      ;;
    --checksum)
      [[ $# -ge 2 ]] || fail "--checksum requires a path"
      checksum="$2"
      shift 2
      ;;
    --releases-root)
      [[ $# -ge 2 ]] || fail "--releases-root requires a path"
      releases_root="$2"
      shift 2
      ;;
    --no-activate)
      activate=0
      shift
      ;;
    --skip-checksum)
      skip_checksum=1
      shift
      ;;
    --skip-sync)
      skip_sync=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$archive" ]] || fail "--archive is required"
[[ -f "$archive" ]] || fail "archive not found: $archive"
case "$releases_root" in
  /*) ;;
  *) fail "releases root must be an absolute path" ;;
esac

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v unzip >/dev/null 2>&1 || fail "unzip is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"

archive="$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"
if [[ -z "$checksum" ]]; then
  checksum="${archive}.sha256"
fi

if [[ "$skip_checksum" -eq 0 ]]; then
  [[ -f "$checksum" ]] || fail "checksum sidecar not found: $checksum"
  expected="$(awk 'NF {print $1; exit}' "$checksum")"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || fail "checksum sidecar is malformed"
  actual="$(python3 - "$archive" <<'PY'
import hashlib
import sys

path = sys.argv[1]
digest = hashlib.sha256()
with open(path, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
)"
  actual_normalized="$(printf '%s' "$actual" | tr '[:upper:]' '[:lower:]')"
  expected_normalized="$(printf '%s' "$expected" | tr '[:upper:]' '[:lower:]')"
  [[ "$actual_normalized" == "$expected_normalized" ]] || fail "archive SHA-256 does not match sidecar"
fi

mkdir -p "$releases_root"
staging="$(mktemp -d "$releases_root/.staging.XXXXXX")"
cleanup() {
  if [[ -n "${staging:-}" && -d "$staging" ]]; then
    rm -rf "$staging"
  fi
}
trap cleanup EXIT

unzip -q "$archive" -d "$staging"
[[ -f "$staging/release-manifest.json" ]] || fail "release-manifest.json is missing"
[[ ! -d "$staging/.git" ]] || fail "release archive must not contain Git metadata"

metadata="$(python3 - "$staging" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
manifest_path = root / "release-manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("application") != "aletheia":
    raise SystemExit("manifest application is not aletheia")
if manifest.get("target") != "portable-source":
    raise SystemExit("manifest target is not portable-source")
sha = str(manifest.get("git_sha", ""))
if len(sha) < 7 or not sha.isalnum():
    raise SystemExit("manifest git_sha is invalid")
for row in manifest.get("files", []):
    relative = pathlib.PurePosixPath(str(row["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"unsafe manifest path: {relative}")
    path = root.joinpath(*relative.parts)
    payload = path.read_bytes()
    if len(payload) != int(row["size_bytes"]):
        raise SystemExit(f"size mismatch: {relative}")
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise SystemExit(f"hash mismatch: {relative}")
print(f"{sha}\t{manifest.get('version', '')}")
PY
)" || fail "release manifest verification failed"

IFS=$'\t' read -r git_sha version <<< "$metadata"
short_sha="${git_sha:0:12}"
destination="$releases_root/$short_sha"

if [[ -e "$destination" ]]; then
  [[ -f "$destination/release-manifest.json" ]] || fail "existing release directory is invalid: $destination"
  installed_sha="$(python3 - "$destination/release-manifest.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["git_sha"])
PY
)"
  [[ "$installed_sha" == "$git_sha" ]] || fail "existing release directory belongs to a different commit"
else
  chmod +x "$staging/scripts/runtime/"*.sh
  mv "$staging" "$destination"
  staging=""
fi

if [[ "$skip_sync" -eq 0 ]]; then
  (
    cd "$destination"
    uv sync --locked --no-dev
  )
fi

current="$(dirname "$releases_root")/current"
if [[ "$activate" -eq 1 ]]; then
  python3 - "$destination" "$current" <<'PY'
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).resolve()
target = pathlib.Path(sys.argv[2])
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists() and not target.is_symlink():
    raise SystemExit(f"refusing to replace non-symlink current path: {target}")
temporary = target.with_name(f".{target.name}.{os.getpid()}")
try:
    temporary.unlink(missing_ok=True)
    os.symlink(source, temporary)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
PY
fi

python3 - "$destination" "$current" "$git_sha" "$version" "$activate" <<'PY'
import json
import sys
print(json.dumps({
    "ok": True,
    "release_root": sys.argv[1],
    "current": sys.argv[2] if sys.argv[5] == "1" else None,
    "git_sha": sys.argv[3],
    "version": sys.argv[4],
}, sort_keys=True))
PY
