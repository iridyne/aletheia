#!/usr/bin/env bash
set -euo pipefail

base_url="http://127.0.0.1:8787"
timeout_seconds=30

usage() {
  cat <<'EOF'
Usage: health-check.sh [--base-url URL] [--timeout-seconds N]
EOF
}

fail() {
  printf '{"ok":false,"error":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$*")" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      [[ $# -ge 2 ]] || fail "--base-url requires a value"
      base_url="${2%/}"
      shift 2
      ;;
    --timeout-seconds)
      [[ $# -ge 2 ]] || fail "--timeout-seconds requires a value"
      timeout_seconds="$2"
      shift 2
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

[[ "$timeout_seconds" =~ ^[0-9]+$ ]] || fail "timeout must be a non-negative integer"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

deadline=$((SECONDS + timeout_seconds))
last_error="gateway did not answer"
while (( SECONDS <= deadline )); do
  ready_file="$(mktemp)"
  health_file="$(mktemp)"
  if curl --fail --silent --show-error --max-time 3 "$base_url/_infra/readyz" >"$ready_file" 2>/dev/null \
    && curl --fail --silent --show-error --max-time 5 "$base_url/api/health" >"$health_file" 2>/dev/null; then
    validation_output=""
    set +e
    validation_output="$(python3 - "$base_url" "$ready_file" "$health_file" <<'PY'
import json
import pathlib
import sys

base_url, ready_path, health_path = sys.argv[1:]


def invalid(reason: str) -> None:
    print(json.dumps({"ok": False, "base_url": base_url, "error": reason}, sort_keys=True))
    raise SystemExit(2)


def unhealthy(reason: str, ready: dict, health: dict) -> None:
    print(json.dumps({
        "ok": False,
        "base_url": base_url,
        "ready": ready,
        "health": health,
        "error": reason,
    }, sort_keys=True))
    raise SystemExit(1)


try:
    ready = json.loads(pathlib.Path(ready_path).read_text(encoding="utf-8"))
    health = json.loads(pathlib.Path(health_path).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    invalid("readiness or health payload is not valid JSON")

if not isinstance(ready, dict) or not isinstance(health, dict):
    invalid("readiness and health payloads must be JSON objects")
if ready.get("ok") is not True:
    unhealthy("readiness_not_ready", ready, health)
if health.get("status") != "healthy" or health.get("issues") != []:
    unhealthy("health_not_healthy", ready, health)

print(json.dumps({"ok": True, "base_url": base_url, "ready": ready, "health": health}, sort_keys=True))
PY
)"
    validation_rc=$?
    set -e
    rm -f "$ready_file" "$health_file"
    if (( validation_rc == 0 || validation_rc == 1 )); then
      printf '%s\n' "$validation_output"
      exit "$validation_rc"
    fi
    last_error="readiness or health payload invalid"
  fi
  rm -f "$ready_file" "$health_file"
  last_error="readiness or health endpoint unavailable"
  sleep 0.5
done

python3 - "$base_url" "$timeout_seconds" "$last_error" <<'PY'
import json
import sys
print(json.dumps({
    "ok": False,
    "base_url": sys.argv[1],
    "timeout_seconds": int(sys.argv[2]),
    "error": sys.argv[3],
}, sort_keys=True))
PY
exit 1
