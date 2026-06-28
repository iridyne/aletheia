"""Auto-restart git hooks — make "code landed on disk" trigger a gateway restart.

The gateway runs out of a live git checkout (local-first; no CI/container deploy),
so a `git pull`/merge/rebase on the MAIN working tree leaves the running process on
stale code until someone restarts it — the `code_updated_pending_restart` drift
banner. These hooks close that gap deterministically: the instant new code lands,
they POST to the running gateway's /api/gateway/restart so the supervisor respawns
on fresh code. With them installed, boot-commit ≈ HEAD almost always, and the drift
detector demotes from "thing you babysit" to "pure backstop".

Why a git hook rather than the once-per-hour drift self-heal: the hook fires on the
real *deploy intent* (a commit landing on the checkout the gateway runs from), not on
a polling heuristic — no false alarms mid-edit, no restart-budget games during active
development, exactly one restart per code-landing event.

Design invariants:
  - Installed into the SHARED hooks dir (`git rev-parse --git-common-dir`/hooks), so a
    single install covers every worktree.
  - The hook self-guards to fire ONLY in the main working tree (git-dir ==
    git-common-dir). A commit/checkout/rebase inside a dev worktree must NOT restart
    the production gateway — worktrees share the hooks dir, so this guard is load-bearing.
  - Fire-and-forget curl, short timeout, backgrounded, always `exit 0`. A hook must
    never block or fail the git command. If the gateway isn't running, curl fails
    silently and nothing happens (correct: don't auto-start on a checkout).
  - Idempotent: a hook is (re)written only when missing or when our managed body has
    changed (e.g. the dashboard port was edited). A FOREIGN pre-existing hook (no
    managed marker) is never clobbered — it is reported instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Bump when the rendered hook body changes so existing installs self-update.
HOOK_VERSION = "1"
_BEGIN = f"# >>> aletheia-auto-restart v{HOOK_VERSION} (managed by `aletheia doctor` — edits overwritten) >>>"
_END = "# <<< aletheia-auto-restart <<<"
_MARKER_PREFIX = "# >>> aletheia-auto-restart"  # version-agnostic: detects ANY managed install

# Each hook may need a one-line guard before the shared restart body.
#   post-checkout $3 == 1  → branch switch (not a `git checkout -- file` restore)
#   post-merge / post-rewrite → always a topology change worth restarting on
_HOOK_GUARDS: dict[str, str] = {
    "post-merge": "",
    "post-checkout": '[ "$3" = "1" ] || exit 0   # only branch switches, not file restores',
    "post-rewrite": "",
}


def _git(repo_dir: Path, *args: str) -> str | None:
    """Run a git command in repo_dir; return stripped stdout, or None on failure."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _repo_dir() -> Path:
    # aletheia/gateway/git_hooks.py → parents[2] == the checkout root (main tree or
    # worktree). --git-common-dir below resolves the SHARED .git from either.
    return Path(__file__).resolve().parents[2]


def _hooks_dir(repo_dir: Path) -> Path | None:
    """The shared hooks directory, or None if repo_dir is not inside a git repo."""
    common = _git(repo_dir, "rev-parse", "--git-common-dir")
    if not common:
        return None
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (repo_dir / common_path).resolve()
    return common_path / "hooks"


def _render_hook(hook_name: str, port: int) -> str:
    guard = _HOOK_GUARDS.get(hook_name, "")
    guard_line = f"{guard}\n" if guard else ""
    return (
        "#!/bin/sh\n"
        f"{_BEGIN}\n"
        "# Restart the running gateway when new code lands on the MAIN working tree.\n"
        f"{guard_line}"
        'gd=$(cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd)\n'
        'cd_=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd)\n'
        '[ -n "$gd" ] && [ "$gd" = "$cd_" ] || exit 0   # not the main work tree (worktree/bare) → skip\n'
        f'curl -fsS -m 3 -X POST "http://127.0.0.1:{port}/api/gateway/restart" \\\n'
        "  -H 'Content-Type: application/json' \\\n"
        '  -d \'{"reason":"git_hook"}\' >/dev/null 2>&1 &\n'
        "exit 0\n"
        f"{_END}\n"
    )


def _resolve_port(port: int | None) -> int:
    if port is not None:
        return port
    try:
        from aletheia.config.loader import load_config
        return load_config().app.dashboard_port
    except Exception:
        return 8787


def git_hooks_status(repo_dir: Path | None = None) -> dict:
    """Read-only: which auto-restart hooks are installed / missing / foreign.

    Pure probe — touches nothing. Returns {installed, missing, foreign, hooks_dir,
    error}. `foreign` = a hook file exists but was not written by us (lacks our
    marker) — we never overwrite those.
    """
    repo_dir = repo_dir or _repo_dir()
    hooks_dir = _hooks_dir(repo_dir)
    if hooks_dir is None:
        return {"installed": [], "missing": list(_HOOK_GUARDS),
                "foreign": [], "hooks_dir": None, "error": "not a git repository"}
    installed, missing, foreign = [], [], []
    for name in _HOOK_GUARDS:
        path = hooks_dir / name
        if not path.exists():
            missing.append(name)
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            foreign.append(name)
            continue
        (installed if _MARKER_PREFIX in body else foreign).append(name)
    return {"installed": installed, "missing": missing, "foreign": foreign,
            "hooks_dir": str(hooks_dir), "error": None}


def ensure_git_hooks(repo_dir: Path | None = None, port: int | None = None) -> list[str]:
    """Install/refresh the auto-restart hooks. Idempotent; best-effort; never raises.

    Returns a list of human-readable actions taken (empty when everything was already
    current). Safe to call on every gateway boot and from `doctor --fix`.
    """
    repo_dir = repo_dir or _repo_dir()
    actions: list[str] = []
    try:
        hooks_dir = _hooks_dir(repo_dir)
        if hooks_dir is None:
            return []  # not a git repo — nothing to wire (e.g. a tarball deploy)
        hooks_dir.mkdir(parents=True, exist_ok=True)
        resolved_port = _resolve_port(port)
        for name in _HOOK_GUARDS:
            path = hooks_dir / name
            desired = _render_hook(name, resolved_port)
            if path.exists():
                try:
                    existing = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    existing = ""
                if _MARKER_PREFIX not in existing:
                    actions.append(f"{name}: foreign hook present — left untouched")
                    continue
                if existing == desired:
                    continue  # already current
                verb = "updated"
            else:
                verb = "installed"
            try:
                path.write_text(desired, encoding="utf-8", newline="\n")
                os.chmod(path, 0o755)  # POSIX; harmless on Windows (git uses the shebang)
                actions.append(f"{verb} {name} auto-restart hook")
            except Exception as e:
                actions.append(f"{name}: install failed — {e}")
    except Exception as e:
        actions.append(f"git-hook install error: {e}")
    return actions
