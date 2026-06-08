"""Optional Git versioning for the data workspace (guide §2.2, §9.4).

Git is OPTIONAL and OFF by default (`runtime.git_enabled`). When enabled, it
versions data/knowledge, data/playbooks and data/reports. It must NEVER commit
secrets.enc, app.db, the pid file, or raw logs (guide §9.4). Every git operation
degrades gracefully — a missing git binary or any failure returns a result dict
rather than raising.
"""

import logging
import shutil
import subprocess

from aletheia.config.loader import load_config, get_data_dir
from aletheia.gateway.pidfile import _no_window_kwargs

logger = logging.getLogger("aletheia.git")

# Directories that ARE versioned (relative to data dir).
VERSIONED_DIRS = ["knowledge", "playbooks", "reports"]

# Never commit these (guide §9.4). Used to write .gitignore.
_GITIGNORE = """\
# Aletheia: never version secrets, state DB, or runtime artifacts (guide §9.4)
secrets.enc
app.db
app.db-*
gateway.pid
config.yaml
logs/
exports/
"""


def _git(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        **_no_window_kwargs(),
    )


def is_enabled() -> bool:
    return load_config().runtime.git_enabled


def _ensure_repo(data_dir) -> None:
    """Init the repo and write .gitignore if not already present."""
    # Create data dir first so subsequent writes never hit FileNotFoundError.
    data_dir.mkdir(parents=True, exist_ok=True)
    # .gitignore must exist before git init so the first `git add` never
    # accidentally stages secrets.enc, app.db, or logs/.
    gitignore = data_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE, encoding="utf-8")
    if not (data_dir / ".git").exists():
        _git("init", cwd=data_dir)
        # Local identity so commits work even without global git config.
        _git("config", "user.email", "aletheia@localhost", cwd=data_dir)
        _git("config", "user.name", "Aletheia", cwd=data_dir)


def commit_all(message: str) -> dict:
    """Stage the versioned dirs and commit. Returns {committed, ...}.

    No-op (committed=False, reason='git_disabled') when git_enabled is false.
    """
    if not is_enabled():
        return {"committed": False, "reason": "git_disabled"}
    if shutil.which("git") is None:
        return {"committed": False, "reason": "git_unavailable"}

    data_dir = get_data_dir()
    try:
        _ensure_repo(data_dir)
        # Stage only the versioned dirs that exist.
        staged_any = False
        for d in VERSIONED_DIRS:
            if (data_dir / d).exists():
                _git("add", d, cwd=data_dir)
                staged_any = True
        if not staged_any:
            return {"committed": False, "reason": "nothing_to_stage"}

        status = _git("status", "--porcelain", cwd=data_dir)
        if not status.stdout.strip():
            return {"committed": False, "reason": "nothing_to_commit"}

        result = _git("commit", "-m", message, cwd=data_dir)
        if result.returncode != 0:
            return {"committed": False, "reason": "commit_failed", "detail": result.stderr.strip()}
        return {"committed": True, "message": message}
    except Exception as e:  # never raise into callers
        logger.warning("git commit_all failed: %s", e)
        return {"committed": False, "reason": "exception", "detail": str(e)}
