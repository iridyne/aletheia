#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: start-release.sh [options]

Options:
  --release-root PATH  Manifest-backed release directory
                       (default: the release containing this script)
  --data-dir PATH      Absolute runtime-state override; default remains ~/.aletheia
  --foreground         Run the gateway in the foreground
  --skip-sync          Skip uv sync --locked --no-dev
  -h, --help           Show this help
EOF
}

fail() {
  printf 'Aletheia release start failed: %s\n' "$*" >&2
  exit 1
}

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
release_root="$script_root"
data_dir=""
foreground=0
skip_sync=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-root)
      [[ $# -ge 2 ]] || fail "--release-root requires a path"
      release_root="$2"
      shift 2
      ;;
    --data-dir)
      [[ $# -ge 2 ]] || fail "--data-dir requires a path"
      data_dir="$2"
      shift 2
      ;;
    --foreground)
      foreground=1
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

[[ -d "$release_root" ]] || fail "release root not found: $release_root"
release_root="$(cd "$release_root" && pwd -P)"
[[ -f "$release_root/release-manifest.json" ]] || fail "release-manifest.json is required"
[[ ! -d "$release_root/.git" ]] || fail "stable runtime refuses a Git checkout"
[[ -f "$release_root/aletheia/dashboard_static/index.html" ]] || fail "compiled dashboard is missing"

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"

python3 - "$release_root/release-manifest.json" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("application") != "aletheia":
    raise SystemExit("manifest application is not aletheia")
if manifest.get("target") != "portable-source":
    raise SystemExit("manifest target is not portable-source")
if "macos" not in manifest.get("supported_platforms", []):
    raise SystemExit("manifest does not declare macOS support")
PY

if [[ -n "$data_dir" ]]; then
  case "$data_dir" in
    /*) ;;
    *) fail "data directory must be an absolute path" ;;
  esac
  mkdir -p "$data_dir"
  data_dir="$(cd "$data_dir" && pwd -P)"
  export ALETHEIA_DATA_DIR="$data_dir"
else
  unset ALETHEIA_DATA_DIR || true
fi

cd "$release_root"
if [[ "$skip_sync" -eq 0 ]]; then
  uv sync --locked --no-dev
fi

args=(uv run --no-sync aletheia gateway start --no-browser)
if [[ "$foreground" -eq 1 ]]; then
  args+=(--foreground)
fi
exec "${args[@]}"
