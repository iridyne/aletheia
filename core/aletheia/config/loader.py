"""Config loader for the current user's Aletheia runtime home."""

import hashlib
import logging
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger("aletheia.config.loader")

import yaml
from pydantic import ValidationError

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None

from aletheia.config.schema import AletheiaConfig, WebhookConfig
from aletheia.runtime_paths import preview_root, runtime_home

_PROJECT_ROOT = Path(__file__).resolve().parents[2 if getattr(sys, "frozen", False) else 3]
_PREVIEW_MODE_ENV = "ALETHEIA_PREVIEW_MODE"
_PREVIEW_DATA_DIR_ENV = "ALETHEIA_PREVIEW_DATA_DIR"


def is_preview_mode() -> bool:
    """Whether this process is an explicitly isolated development API."""
    return os.environ.get(_PREVIEW_MODE_ENV, "").strip() == "1"


def preview_data_dir() -> Path:
    """Return the only data root an isolated preview process may use.

    The CLI supplies this value.  Constraining it to this checkout prevents an
    accidental environment override from attaching a preview process to the
    primary runtime's state directory.
    """
    raw = os.environ.get(_PREVIEW_DATA_DIR_ENV, "").strip()
    if not raw:
        raise RuntimeError("preview mode requires ALETHEIA_PREVIEW_DATA_DIR")
    candidate = Path(raw).expanduser().resolve()
    allowed_root = preview_root().resolve()
    if not candidate.is_relative_to(allowed_root):
        raise RuntimeError("preview data directory must be under ~/.aletheia/instances/dev-preview")
    return candidate


def runtime_data_dir() -> Path:
    """Return the durable data root for the primary runtime.

    Release bundles and development checkouts may live anywhere while sharing one
    per-user state root. ``ALETHEIA_DATA_DIR`` remains an absolute override.
    Preview mode deliberately ignores it and keeps stricter instance isolation.
    """
    return runtime_home()


DATA_DIR = preview_data_dir() if is_preview_mode() else runtime_data_dir()
CONFIG_PATH = DATA_DIR / "config.yaml"

_DOTENV_LOADED = False

_READ_ONLY_CONFIG: ContextVar[bool] = ContextVar(
    "aletheia_read_only_config", default=False
)


@contextmanager
def read_only_config():
    """Prevent implicit config migrations from persisting in a read path."""
    token = _READ_ONLY_CONFIG.set(True)
    try:
        yield
    finally:
        _READ_ONLY_CONFIG.reset(token)


def _load_dotenv() -> None:
    """Load .env from the runtime data directory once, preserving existing variables."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_file = DATA_DIR / ".env"
    if not env_file.exists():
        return
    from dotenv import load_dotenv
    load_dotenv(env_file, override=False)


def get_data_dir() -> Path:
    return DATA_DIR


# ensure_data_dirs tracks the last DATA_DIR it ran for; re-runs only when
# DATA_DIR changes (e.g. in tests with monkeypatched tmp paths).
_dirs_ensured_for: Path | None = None


def ensure_data_dirs() -> None:
    """Create the full data directory tree (once per unique DATA_DIR)."""
    global _dirs_ensured_for
    if _dirs_ensured_for == DATA_DIR:
        return
    if not is_preview_mode():
        from aletheia.runtime_home_migration import assert_runtime_home_ready

        assert_runtime_home_ready(
            project_root=_PROJECT_ROOT,
            target=DATA_DIR,
            override_active=bool(os.environ.get("ALETHEIA_DATA_DIR", "").strip()),
        )
    dirs = [
        DATA_DIR,
        DATA_DIR / "logs",
        DATA_DIR / "knowledge" / "evidence",
        DATA_DIR / "knowledge" / "research_notes",
        DATA_DIR / "knowledge" / "strategies",
        DATA_DIR / "knowledge" / "agent_memory",
        DATA_DIR / "knowledge" / "daily_reviews",
        DATA_DIR / "knowledge" / "dreaming_logs",
        DATA_DIR / "knowledge" / "post_trade_reviews",
        DATA_DIR / "knowledge" / "deprecated",
        DATA_DIR / "playbooks",
        DATA_DIR / "reports" / "daily",
        DATA_DIR / "reports" / "post_trade",
        DATA_DIR / "reports" / "system_health",
        DATA_DIR / "reports" / "trade_review",
        DATA_DIR / "reports" / "risk",
        DATA_DIR / "reports" / "agent_performance",
        DATA_DIR / "exports",
        # Persistence / Sync / Recovery workspace directories
        DATA_DIR / "events",
        DATA_DIR / "backups",
        DATA_DIR / "artifacts" / "evidence",
        DATA_DIR / "artifacts" / "claims",
        DATA_DIR / "artifacts" / "market_snapshots",
        DATA_DIR / "artifacts" / "analyses",
        DATA_DIR / "artifacts" / "opportunities",
        DATA_DIR / "artifacts" / "strategy_specs",
        DATA_DIR / "artifacts" / "validation_reports",
        DATA_DIR / "artifacts" / "proposals",
        DATA_DIR / "artifacts" / "paper_runs",
        DATA_DIR / "artifacts" / "live_runs",
        DATA_DIR / "artifacts" / "reconciliation_reports",
        DATA_DIR / "artifacts" / "reviews",
        DATA_DIR / "artifacts" / "lessons",
        DATA_DIR / "artifacts" / "skills",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    _dirs_ensured_for = DATA_DIR


_MODE_MIGRATION = {
    "ReadOnly": "Observe", "Research": "Observe",
    "PaperAuto": "Paper",
    "AllowEditing": "Full", "Auto": "Full",
}

_OPERATOR_MODE_MIGRATION = {
    "Observe": "ReadOnly",
    "Paper": "PaperAuto",
    "Full": "Auto",
}

_REMOVED_AGENT_CONFIG_KEYS = frozenset(
    {
        "ai_entry_hypothesis_enabled",
        "ai_entry_hypothesis_activated_at",
    }
)


def _migrate_removed_agent_config_keys(raw: dict) -> bool:
    """Remove only agent keys removed by the grounded-entry migration."""
    agent = raw.get("agent")
    if not isinstance(agent, dict):
        return False
    removed = _REMOVED_AGENT_CONFIG_KEYS.intersection(agent)
    for key in removed:
        del agent[key]
    return bool(removed)













def _audit_webhook_config_migration() -> None:
    """Best-effort audit without copying webhook credentials into the event."""
    try:
        from aletheia.files.jsonl import write_audit

        write_audit({
            "actor": "system",
            "source": "config_loader",
            "event_type": "webhook_config_migrated",
            "entity_type": "webhook_config",
            "entity_id": "webhook",
            "message": "Disabled a legacy enabled webhook with an invalid URL.",
        })
    except Exception:
        logger.warning("config_loader: webhook migration audit event failed after config change", exc_info=True)






class ConfigConflictError(RuntimeError):
    """Raised by save_config when a CAS check detects concurrent modification."""


# ─── Cross-process config write lock ─────────────────────────────────────────
# config.yaml is shared by multiple Aletheia processes. A process-local
# threading.Lock only serialises writers within one process. This file-permit
# (analogous to storage/sqlite.py's *.write.lock) serialises writers across
# processes by holding an exclusive flock/msvcrt lock on config.yaml.write.lock
# before the read-modify-write transaction begins.
_CONFIG_WRITE_LOCK_TIMEOUT_S = 10.0


def _acquire_config_file_lock() -> "int | None":
    """Open and exclusively lock config.yaml.write.lock. Returns fd, or None on Windows without msvcrt."""
    if _fcntl is None and _msvcrt is None:
        return None
    lock_path = CONFIG_PATH.with_suffix(".write.lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise OSError(f"cannot open config writer lock: {exc}") from exc
    started = time.monotonic()
    try:
        if _msvcrt is not None and os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if _fcntl is not None:
                    _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                else:
                    _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
                return fd
            except (BlockingIOError, OSError):
                if time.monotonic() - started >= _CONFIG_WRITE_LOCK_TIMEOUT_S:
                    # raise without closing — except BaseException below will close fd
                    raise TimeoutError(
                        "config_write_lock_timeout: another process held the config writer permit"
                    )
                time.sleep(0.01)
    except BaseException:
        os.close(fd)
        raise


def _release_config_file_lock(fd: "int | None") -> None:
    if fd is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        elif _msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def config_file_hash() -> "str | None":
    """SHA-256 hex digest of current config.yaml. Returns None if file absent."""
    try:
        return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    except OSError:
        return None


# ─── In-memory config cache ───────────────────────────────────────────────────
# Avoids repeated disk reads on every API request.  TTL = 5 s; invalidated
# immediately by save_config() so mutations are always reflected.
# Cache key includes CONFIG_PATH so monkeypatched test paths don't collide.
_config_write_lock = threading.Lock()
_config_cache: "tuple[float, Path, AletheiaConfig] | None" = None
_CONFIG_CACHE_TTL = 5.0


def _invalidate_config_cache() -> None:
    global _config_cache
    _config_cache = None

def _normalize_candidate_selection(cfg: AletheiaConfig) -> bool:
    """Derive Demo selection identity from current semantics, not admission."""
    if (DATA_DIR / "candidate_selection_authority_transition.json").exists():
        return False  # Recover the old journal before migrating its policy.
    from aletheia.universe.candidate_eligibility import canonical_fingerprint
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.config.schema import UniverseConfig

    epoch = canonical_fingerprint(candidate_selection_policy_identity(cfg))
    if (cfg.universe.candidate_selection_policy == "candidate_selection_utility"
            and cfg.universe.candidate_selection_policy_epoch == epoch):
        return False
    cfg.universe = UniverseConfig.model_validate({
        **cfg.universe.model_dump(),
        "candidate_selection_policy": "candidate_selection_utility",
        "candidate_selection_policy_epoch": epoch,
    })
    return True


def load_config(
    *, persist_migrations: bool = True, use_cache: bool = True
) -> AletheiaConfig:
    """Load config from disk or return defaults.  Hot path uses in-memory cache.

    #147 fix: return a DEEP COPY of the cached config, not the cached object
    itself. Route handlers routinely do
        cfg = load_config(); cfg.risk.foo = new_value; save_config(cfg)
    — if save_config rejects (validation error, disk failure), the caller's
    mutated cfg is the SAME OBJECT that was in the cache, so any concurrent
    load_config call inside the 5s TTL sees the phantom mutation. Deep-copy
    on the read side makes mutations local to each caller; the cache stays
    the source of truth on disk.
    """
    global _config_cache
    persist_migrations = persist_migrations and not _READ_ONLY_CONFIG.get()
    if not is_preview_mode():
        from aletheia.runtime_home_migration import assert_runtime_home_ready

        assert_runtime_home_ready(
            project_root=_PROJECT_ROOT,
            target=DATA_DIR,
            override_active=bool(os.environ.get("ALETHEIA_DATA_DIR", "").strip()),
        )
    if use_cache and _config_cache is not None:
        ts, cached_path, cfg = _config_cache
        if time.monotonic() - ts < _CONFIG_CACHE_TTL and cached_path == CONFIG_PATH:
            return cfg.model_copy(deep=True)
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        market_data = raw.get("market_data")
        if isinstance(market_data, dict) and "coinalyze_api_key" in market_data:
            raise ValueError(
                "market_data.coinalyze_api_key is retired; remove this field from "
                "config.yaml. Its value is not shown."
            )
        removed_agent_keys = _migrate_removed_agent_config_keys(raw)
        trading = raw.get("trading")
        removed_confidence_key = isinstance(trading, dict) and "min_confidence_to_trade" in trading
        if removed_confidence_key:
            del trading["min_confidence_to_trade"]
        serenity = raw.get("serenity")
        removed_serenity_stop = isinstance(serenity, dict) and "stop_loss_pct" in serenity
        if removed_serenity_stop:
            del serenity["stop_loss_pct"]
        # The Strategy Factory subsystem was removed with Position C (2026-07-02);
        # its config class and API surface are now deleted too. `AletheiaConfig` is
        # extra="forbid", so an existing config.yaml carrying this section would
        # fail to LOAD without this migration — a removed section needs a drop, not
        # an assumption that unknown keys are ignored.
        removed_strategy_factory = "strategy_factory" in raw
        if removed_strategy_factory:
            del raw["strategy_factory"]
        exchange = raw.get("exchange")
        removed_uta_boundary = isinstance(exchange, dict) and "uta_read_boundaries" in exchange
        if removed_uta_boundary:
            del exchange["uta_read_boundaries"]
        # Bitget has one account profile (uta_v3); the switch that selected between
        # two was removed (#3806). The key must be dropped, not assumed ignorable.
        removed_bitget_profile = isinstance(exchange, dict) and "bitget_profile" in exchange
        if isinstance(exchange, dict):
            exchange.pop("bitget_profile", None)
        runtime = raw.get("runtime")
        retired_runtime_keys = {"runtime_continuity_enabled", "runtime_continuity_lock_ttl_sec", "runtime_continuity_shadow_port_offset"}
        removed_runtime_keys = retired_runtime_keys.intersection(runtime) if isinstance(runtime, dict) else set()
        for key in removed_runtime_keys:
            del runtime[key]
        operator = raw.get("operator")
        operator_section_present = isinstance(operator, dict)
        removed_operator_loop = operator_section_present and "loop_enabled" in operator
        if removed_operator_loop:
            del operator["loop_enabled"]
        webhook = raw.get("webhook")
        webhook_migrated = (
            isinstance(webhook, dict)
            and bool(webhook.get("enabled"))
            and not WebhookConfig.url_is_usable(webhook.get("url"))
        )
        if webhook_migrated:
            webhook["enabled"] = False
        # Reject unknown persisted keys recursively before any authority can
        # start or a later save can erase the malformed source key.  Pydantic's
        # runtime override propagates through nested models while leaving typed
        # dynamic mappings (providers, quotas, etc.) intact.
        try:
            cfg = AletheiaConfig.model_validate(raw, extra="forbid")
        except ValidationError as exc:
            if any(error.get("type") == "extra_forbidden" for error in exc.errors()):
                safe_errors = []
                for error in exc.errors():
                    safe_error = {
                        key: value
                        for key, value in error.items()
                        if key not in {"input", "ctx"}
                    }
                    context = error.get("ctx")
                    if isinstance(context, dict):
                        safe_error["ctx"] = {
                            key: (
                                ValueError("invalid configuration value")
                                if key == "error"
                                else value
                                if isinstance(value, (int, float, bool, type(None)))
                                else "[redacted]"
                            )
                            for key, value in context.items()
                        }
                    safe_errors.append(safe_error)
                # Keep the normal Pydantic type/paths while hiding rejected
                # values from startup, Doctor, and audit projections.
                raise ValidationError.from_exception_data(
                    "AletheiaConfig", safe_errors, hide_input=True
                ) from None
            raise
        candidate_selection_migrated = _normalize_candidate_selection(cfg)
        changed = (webhook_migrated or removed_agent_keys or removed_confidence_key
                   or removed_serenity_stop or removed_strategy_factory
                   or removed_uta_boundary or removed_bitget_profile or removed_runtime_keys or candidate_selection_migrated
                   or removed_operator_loop)
        # If RiskConfig migration bumped schema_version (e.g. v3→v4), write back
        # so the corrected values are persisted and the migration does not re-run.
        raw_risk_version = int((raw.get("risk") or {}).get("schema_version", 0))
        if cfg.risk.schema_version != raw_risk_version:
            changed = True
        if cfg.agent.permission_mode in _MODE_MIGRATION:
            cfg.agent.permission_mode = _MODE_MIGRATION[cfg.agent.permission_mode]
            changed = True
        if cfg.operator.permission_mode in _OPERATOR_MODE_MIGRATION:
            cfg.operator.permission_mode = _OPERATOR_MODE_MIGRATION[cfg.operator.permission_mode]
            changed = True
        if not operator_section_present:
            # Historical configurations did not authorize the shell. Retiring
            # the Python owner must not silently activate a replacement.
            cfg.operator.enabled = False
            changed = True
        if changed and persist_migrations:
            save_config(cfg)
            if webhook_migrated:
                _audit_webhook_config_migration()
        if changed and not persist_migrations:
            return cfg.model_copy(deep=True)
        if use_cache:
            _config_cache = (time.monotonic(), CONFIG_PATH, cfg)
        return cfg.model_copy(deep=True)
    cfg = AletheiaConfig()
    _normalize_candidate_selection(cfg)
    if use_cache:
        _config_cache = (time.monotonic(), CONFIG_PATH, cfg)
    return cfg.model_copy(deep=True)


def _save_config_locked(cfg: AletheiaConfig) -> None:
    """Persist config while the process-wide config write lock is held."""
    _invalidate_config_cache()
    ensure_data_dirs()
    _normalize_candidate_selection(cfg)
    cfg.runtime.set_pause_scope(cfg.exchange.mode)
    data = AletheiaConfig.model_validate(cfg.model_dump(mode="python")).model_dump(
        mode="json"
    )
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
        payload = content.encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(tmp_fd, payload[offset:])
            if written <= 0:
                raise OSError("config write made no progress")
            offset += written
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
        os.replace(tmp_path, str(CONFIG_PATH))
        _sync_config_directory()
    except BaseException:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                logger.warning("config_loader: failed to close temp config fd during rollback; descriptor may leak", exc_info=True)
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("config_loader: failed to remove temp config file during rollback; orphaned temp file may remain", exc_info=True)
        raise


def _sync_config_directory() -> None:
    """Sync the replacement directory on POSIX; Windows lacks directory fsync."""
    try:
        directory_fd = os.open(str(CONFIG_PATH.parent), os.O_RDONLY)
    except OSError:
        # File fsync above remains the portable Windows fallback. On POSIX a
        # directory-sync failure is surfaced instead of claiming durability.
        if os.name == "nt":
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            if os.name != "nt":
                raise
    finally:
        os.close(directory_fd)


def save_config(
    cfg: AletheiaConfig,
    *,
    expected_hash: "str | None" = None,
) -> None:
    """Persist config to disk (atomic write + cross-process lock).

    Pass expected_hash (from config_file_hash()) to enable CAS: raises
    ConfigConflictError if the file was modified by another writer since load.
    """
    with _config_write_lock:
        file_fd = _acquire_config_file_lock()
        try:
            if expected_hash is not None:
                current = config_file_hash()
                if current != expected_hash:
                    raise ConfigConflictError(
                        f"config modified by another writer "
                        f"(expected {expected_hash[:8]}, got {current and current[:8]})"
                    )
            _save_config_locked(cfg)
        finally:
            _release_config_file_lock(file_fd)
    _emit_consistency_events(cfg)


def mutate_config(mutator):
    """Apply one targeted mutation under the cross-process write lock.

    Acquires the filesystem permit before reading so the mutation base is always
    the latest committed revision — not a process-local cached snapshot.
    """
    with _config_write_lock:
        file_fd = _acquire_config_file_lock()
        try:
            _invalidate_config_cache()
            cfg = load_config(persist_migrations=False)
            changed = bool(mutator(cfg))
            if changed:
                _save_config_locked(cfg)
        finally:
            _release_config_file_lock(file_fd)
    if changed:
        _emit_consistency_events(cfg)
    return changed


def _emit_consistency_events(cfg: AletheiaConfig) -> None:
    try:
        from aletheia.diagnostics.config_consistency import check_config_consistency
        from aletheia.infra.events import emit_infra_event
        for finding in check_config_consistency(cfg):
            emit_infra_event(
                severity="warning",
                source="config_consistency",
                code=finding.field_path,
                message=finding.message,
                action=finding.fix_hint,
                dedupe_sec=300,
            )
    except Exception:  # noqa: BLE001 — advisory; must never break save
        logger.warning("config_loader: consistency diagnostics could not be emitted after save", exc_info=True)


def bootstrap() -> AletheiaConfig:
    """Ensure data dirs exist and config is written. Returns loaded config."""
    _load_dotenv()
    ensure_data_dirs()
    # Seed starter playbooks on first run (guide §22.3 / §8)
    try:
        from aletheia.trading.playbooks import seed_playbooks
        seed_playbooks(DATA_DIR)
    except Exception as e:
        import logging
        logging.getLogger("aletheia.config").warning("bootstrap: seed_playbooks failed: %s", e)
    if not CONFIG_PATH.exists():
        cfg = AletheiaConfig()
        save_config(cfg)
        return cfg
    return load_config()
