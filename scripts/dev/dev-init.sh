#!/usr/bin/env bash
# One-shot dev environment initialization. Idempotent: safe to re-run.
#
# Purpose: prepare the local checkout to run the gateway as a plain background
# binary on 8787 backed by ~/.aletheia/, WITHOUT any OS-level autostart binding
# (no LaunchAgent respawn), and install the pre-commit cheap gate. Run this once
# per fresh checkout / whenever autostart gets re-enabled by an external command.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

DATA_DIR="${ALETHEIA_DATA_DIR:-$HOME/.aletheia}"
FRONTEND_DIR="$REPO_ROOT/frontend"
STATIC_DIR="$REPO_ROOT/core/aletheia/dashboard_static"

log()  { printf '[dev-init] %s\n' "$*"; }
warn() { printf '[dev-init] WARN: %s\n' "$*" >&2; }
fail() { printf '[dev-init] ERROR: %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || fail "uv not found on PATH. Install uv first."

log "1/5  Disabling OS autostart (LaunchAgent) — dev must never be respawned by OS."
if uv run aletheia gateway disable-autostart 2>&1 | sed 's/^/    /'; then
  :
else
  warn "disable-autostart returned non-zero. Continuing — inspect above output."
fi

log "2/5  Checking frontend/node_modules."
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  warn "frontend/node_modules missing. Run: cd frontend && npm install"
  warn "Then re-run this script."
  exit 1
fi

log "3/5  Checking dashboard_static/index.html."
if [[ ! -f "$STATIC_DIR/index.html" ]]; then
  log "     Missing. Building frontend (this may take ~30s)..."
  (cd "$FRONTEND_DIR" && npm run build) || fail "Frontend build failed."
  [[ -f "$STATIC_DIR/index.html" ]] || fail "Build succeeded but index.html still missing — check frontend/vite.config."
else
  log "     Present."
fi

log "4/5  Installing the pre-commit cheap gate (scripts/hooks/pre-commit)."
if [[ -f "$REPO_ROOT/.git/hooks/pre-commit" ]] && ! cmp -s "$REPO_ROOT/scripts/hooks/pre-commit" "$REPO_ROOT/.git/hooks/pre-commit"; then
  warn "An existing .git/hooks/pre-commit differs and was NOT overwritten."
  warn "  .git/hooks is SHARED by every worktree of this repository, so copying this"
  warn "  worktree's copy can downgrade the hook for all of them (an older checkout"
  warn "  carries an older hook). Inspect the difference and copy from the MAIN"
  warn "  checkout instead: cp <main>/scripts/hooks/pre-commit .git/hooks/pre-commit"
else
  cp "$REPO_ROOT/scripts/hooks/pre-commit" "$REPO_ROOT/.git/hooks/pre-commit"
  chmod +x "$REPO_ROOT/.git/hooks/pre-commit"
  log "     Installed. Full changed-file gate: uv run python scripts/ci/dev_check.py"
fi

log "5/5  Current gateway state:"
uv run aletheia gateway status 2>&1 | sed 's/^/    /' || true

log "Done. Data dir: $DATA_DIR"
log "Logs will land in:  $DATA_DIR/logs/{gateway,errors}.log"
log ""
log "Next: 'uv run aletheia gateway start' to launch on 8787."
