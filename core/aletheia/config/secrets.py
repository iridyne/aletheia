"""Encrypted file authority with per-reference OS keyring fallback. Never logs secrets."""

import os
import stat as _stat
import threading
import time
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None

from aletheia.config.loader import get_data_dir, is_preview_mode

# ─── Cross-process secret write lock ─────────────────────────────────────────
# secrets.enc is shared by multiple Aletheia processes. An in-process
# threading.Lock only serialises writers within one process. This file-permit
# (same pattern as config/loader.py and storage/sqlite.py) serialises writers
# across processes by holding an exclusive flock/msvcrt lock on
# secrets.enc.write.lock before each read-modify-write transaction.
_WRITE_LOCK_TIMEOUT_S = 10.0
_write_lock = threading.Lock()


def _acquire_file_lock() -> "int | None":
    """Exclusively lock secrets.enc.write.lock. Returns fd, or None if OS locking unavailable."""
    if _fcntl is None and _msvcrt is None:
        return None
    lock_path = get_data_dir() / "secrets.enc.write.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise OSError(f"cannot open secret writer lock: {exc}") from exc
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
                if time.monotonic() - started >= _WRITE_LOCK_TIMEOUT_S:
                    raise TimeoutError(
                        "secrets_write_lock_timeout: another process held the secret writer permit"
                    )
                time.sleep(0.01)
    except BaseException:
        os.close(fd)
        raise


def _release_file_lock(fd: "int | None") -> None:
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

_SERVICE_NAME = "aletheia-trading-os"
_CREDENTIAL_GENERATIONS_KEY = "__aletheia_exchange_credential_generations__"
_EXCHANGE_CREDENTIAL_FIELDS = {"api_key", "api_secret", "passphrase"}

# These fragments are matched after key names are case/separator-normalized,
# so ``api_key``, ``ApiKey``, and ``api-key`` share one trust-boundary rule.
SECRET_KEY_FRAGMENTS = (
    "secret",
    "password",
    "passphrase",
    "apikey",
    "token",
    "credential",
    "privatekey",
)


def normalize_secret_key(key: object) -> str:
    """Normalize a mapping key without treating ``key`` itself as sensitive."""
    return "".join(
        character
        for character in str(key).casefold()
        if character.isascii() and character.isalnum()
    )


def is_secret_key(key: object) -> bool:
    """Return whether a mapping key can carry a credential-like value."""
    normalized = normalize_secret_key(key)
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def _exchange_credential_scope(key: str) -> str | None:
    parts = key.split(".")
    if (
        len(parts) == 3
        and parts[0] in {"binance", "okx", "bitget"}
        and parts[1] in {"demo", "testnet", "live"}
        and parts[2] in _EXCHANGE_CREDENTIAL_FIELDS
    ):
        return ".".join(parts[:2])
    return None


def _advance_credential_generation(data: dict, key: str) -> None:
    scope = _exchange_credential_scope(key)
    if scope is None:
        return
    generations = data.get(_CREDENTIAL_GENERATIONS_KEY)
    if generations is None:
        generations = {}
    if not isinstance(generations, dict):
        raise RuntimeError("invalid exchange credential generation metadata")
    previous = generations.get(scope, 0)
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise RuntimeError("invalid exchange credential generation metadata")
    generations[scope] = previous + 1
    data[_CREDENTIAL_GENERATIONS_KEY] = generations


def get_credential_generation(scope: str) -> int:
    """Return the durable non-secret generation for one venue environment."""
    if is_preview_mode():
        return 0
    path = _fallback_path()
    _ensure_private_file(path)
    if not path.exists():
        return 0
    try:
        import json

        data = json.loads(_get_fernet().decrypt(path.read_bytes()).decode())
    except Exception as exc:
        raise RuntimeError("failed to read exchange credential generation") from exc
    generations = data.get(_CREDENTIAL_GENERATIONS_KEY, {})
    if not isinstance(generations, dict):
        raise RuntimeError("invalid exchange credential generation metadata")
    value = generations.get(scope, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("invalid exchange credential generation metadata")
    return value


class PreviewSecretAccessError(RuntimeError):
    """Raised when an isolated preview tries to persist a credential."""


def _get_keyring():
    """Try to import keyring; returns None if unavailable."""
    try:
        import keyring  # pyright: ignore[reportMissingImports]
        return keyring
    except Exception:
        return None


def _fallback_path() -> Path:
    return get_data_dir() / "secrets.enc"


def _ensure_private_file(path: Path) -> None:
    """Reject links/foreign owners and tighten POSIX secret-file permissions."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if _stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"secret file must not be a symlink: {path.name}")
    if not _stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"secret file must be regular: {path.name}")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise RuntimeError(f"secret file has unexpected owner: {path.name}")
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise RuntimeError(f"cannot restrict secret file permissions: {path.name}") from exc
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise RuntimeError(f"secret file permissions are too broad: {path.name}")


def _write_private_bytes(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    _ensure_private_file(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    except BaseException:
        # fdopen owns the descriptor after entering the context; this close is
        # only for errors raised before that handoff.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    _ensure_private_file(path)


def set_secret(key: str, value: str) -> None:
    """Store a secret. Writes to encrypted file always; also tries keyring."""
    if is_preview_mode():
        raise PreviewSecretAccessError("secret writes are blocked in preview mode")
    value = value.strip()
    # Always write to the encrypted file so non-interactive sessions (services,
    # CLI running under a different Windows logon session) can read the secret
    # even when the OS keyring is inaccessible (WinError 1312).
    _file_set(key, value)
    # Best-effort keyring write for GUI credential managers; failure is silent
    # because the file copy is the reliable source of truth.
    kr = _get_keyring()
    if kr:
        try:
            kr.set_password(_SERVICE_NAME, key, value)
        except Exception:
            pass


def get_secret(key: str, *, strict: bool = False) -> str | None:
    """Retrieve one reference; strict reads distinguish unavailable from absent."""
    if is_preview_mode():
        return None
    unavailable = False
    try:
        value = _file_get(key, strict=True) if strict else _file_get(key)
    except Exception:
        if not strict:
            raise
        unavailable = True
        value = None
    if value is not None:
        return value
    kr = _get_keyring()
    if kr:
        try:
            value = kr.get_password(_SERVICE_NAME, key)
            if value is not None:
                return value
        except Exception:
            unavailable = True
    if strict and unavailable:
        raise RuntimeError("secret_store_unavailable") from None
    return None


def delete_secret(key: str) -> None:
    """Delete a secret from every Aletheia-owned credential store.

    Deletion fails closed when an available keyring cannot be inspected. The
    encrypted fallback is removed atomically before the keyring copy, so a
    keyring deletion failure leaves the configured profile and retryable
    keyring credential intact.
    """
    if is_preview_mode():
        raise PreviewSecretAccessError("secret writes are blocked in preview mode")
    kr = _get_keyring()
    existing = None
    if kr:
        try:
            existing = kr.get_password(_SERVICE_NAME, key)
        except Exception as exc:
            raise RuntimeError("failed to inspect secret in OS keyring") from exc
    _file_delete(key, force_generation=existing is not None)
    if kr and existing is not None:
        try:
            kr.delete_password(_SERVICE_NAME, key)
        except Exception as exc:
            raise RuntimeError("failed to delete secret from OS keyring") from exc


def _get_fernet():
    from cryptography.fernet import Fernet
    key_path = get_data_dir() / ".secret.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation so only one concurrent first-start process wins.
        _write_private_bytes(key_path, Fernet.generate_key(), exclusive=True)
    except FileExistsError:
        pass  # Another process created the key first; read it below.
    _ensure_private_file(key_path)
    return Fernet(key_path.read_bytes())


def _file_set(key: str, value: str) -> None:
    import json
    with _write_lock:
        fd = _acquire_file_lock()
        try:
            fernet = _get_fernet()
            path = _fallback_path()
            _ensure_private_file(path)
            data: dict = {}
            if path.exists():
                try:
                    data = json.loads(fernet.decrypt(path.read_bytes()).decode())
                except Exception as exc:
                    raise RuntimeError("failed to read encrypted secret store") from exc
            data[key] = value
            _advance_credential_generation(data, key)
            tmp = path.with_name(path.name + ".tmp")
            try:
                _write_private_bytes(tmp, fernet.encrypt(json.dumps(data).encode()))
                tmp.replace(path)
                _ensure_private_file(path)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            _release_file_lock(fd)


def _file_get(key: str, *, strict: bool = False) -> str | None:
    import json
    path = _fallback_path()
    _ensure_private_file(path)
    if not path.exists():
        return None
    fernet = _get_fernet()
    try:
        data = json.loads(fernet.decrypt(path.read_bytes()).decode())
        value = data.get(key)
        if strict and value is not None and not isinstance(value, str):
            raise ValueError("invalid secret value")
        return value
    except Exception:
        if strict:
            raise RuntimeError("secret_store_unavailable") from None
        return None


def _file_delete(key: str, *, force_generation: bool = False) -> None:
    import json
    path = _fallback_path()
    _ensure_private_file(path)
    if not path.exists() and not force_generation:
        return
    with _write_lock:
        fd = _acquire_file_lock()
        try:
            fernet = _get_fernet()
            data = {}
            if path.exists():
                try:
                    data = json.loads(fernet.decrypt(path.read_bytes()).decode())
                except Exception as exc:
                    raise RuntimeError("failed to read encrypted secret store") from exc
            existed = key in data
            if not existed and not force_generation:
                return
            data.pop(key, None)
            _advance_credential_generation(data, key)
            tmp = path.with_name(path.name + ".tmp")
            try:
                _write_private_bytes(tmp, fernet.encrypt(json.dumps(data).encode()))
                tmp.replace(path)
                _ensure_private_file(path)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            _release_file_lock(fd)


def redact(value: str | None) -> str:
    """Redact a secret for display — show only last 4 characters."""
    if not value:
        return "***"
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]
