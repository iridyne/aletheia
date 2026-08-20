"""Secrets handling: OS keyring first, encrypted file fallback. Never logs secrets."""

import os
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


def get_secret(key: str) -> str | None:
    """Retrieve a secret. Returns None if not found."""
    if is_preview_mode():
        return None
    kr = _get_keyring()
    if kr:
        try:
            value = kr.get_password(_SERVICE_NAME, key)
            if value is not None:
                return value
        except Exception:
            pass
    return _file_get(key)


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
    _file_delete(key)
    if kr and existing is not None:
        try:
            kr.delete_password(_SERVICE_NAME, key)
        except Exception as exc:
            raise RuntimeError("failed to delete secret from OS keyring") from exc


def _get_fernet():
    from cryptography.fernet import Fernet
    key_path = get_data_dir() / ".secret.key"
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation so only one concurrent first-start process wins.
            with open(key_path, "xb") as f:
                f.write(Fernet.generate_key())
        except FileExistsError:
            pass  # Another process created the key first; read it below.
    return Fernet(key_path.read_bytes())


def _file_set(key: str, value: str) -> None:
    import json
    with _write_lock:
        fd = _acquire_file_lock()
        try:
            fernet = _get_fernet()
            path = _fallback_path()
            data: dict = {}
            if path.exists():
                try:
                    data = json.loads(fernet.decrypt(path.read_bytes()).decode())
                except Exception as exc:
                    raise RuntimeError("failed to read encrypted secret store") from exc
            data[key] = value
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_bytes(fernet.encrypt(json.dumps(data).encode()))
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            _release_file_lock(fd)


def _file_get(key: str) -> str | None:
    import json
    path = _fallback_path()
    if not path.exists():
        return None
    fernet = _get_fernet()
    try:
        data = json.loads(fernet.decrypt(path.read_bytes()).decode())
        return data.get(key)
    except Exception:
        return None


def _file_delete(key: str) -> None:
    import json
    path = _fallback_path()
    if not path.exists():
        return
    with _write_lock:
        fd = _acquire_file_lock()
        try:
            fernet = _get_fernet()
            if not path.exists():
                return  # re-check under lock
            try:
                data = json.loads(fernet.decrypt(path.read_bytes()).decode())
            except Exception as exc:
                raise RuntimeError("failed to read encrypted secret store") from exc
            if key not in data:
                return
            del data[key]
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_bytes(fernet.encrypt(json.dumps(data).encode()))
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            _release_file_lock(fd)


def migrate_keyring_to_file() -> int:
    """Copy all secrets from the OS keyring into the encrypted file store.

    Called once at gateway startup so that secrets saved via the Web UI
    (which runs inside the gateway's interactive Windows session and can
    reach the keyring) are also available to CLI / service processes that
    run under a different logon session (WinError 1312) and can only reach
    the file store.  Returns the number of secrets migrated.
    """
    if is_preview_mode():
        return 0
    import json
    kr = _get_keyring()
    if kr is None:
        return 0
    try:
        # Windows Credential Manager exposes enumerate via win32cred;
        # fall back to a known-key list when enumeration is unavailable.
        import keyring.backends.Windows as _wb  # pyright: ignore[reportMissingImports]
        import win32cred  # type: ignore[import]
        creds = win32cred.CredEnumerate(None, 0) or []
        our_creds = [
            c for c in creds
            if c.get("TargetName", "").startswith(_SERVICE_NAME + "/")
        ]
        pairs = [
            (c["TargetName"].removeprefix(_SERVICE_NAME + "/"),
             c["CredentialBlob"].decode("utf-16-le").rstrip("\x00"))
            for c in our_creds
            if c.get("CredentialBlob")
        ]
    except Exception:
        # Enumeration unavailable — read using known key list instead.
        _KNOWN_KEYS = [
            "bitget.api_key", "bitget.api_secret", "bitget.passphrase",
            "bitget.demo.api_key", "bitget.demo.api_secret", "bitget.demo.passphrase",
            "bitget.live.api_key", "bitget.live.api_secret", "bitget.live.passphrase",
            "binance.api_key", "binance.api_secret",
            "binance.testnet.api_key", "binance.testnet.api_secret",
            "okx.api_key", "okx.api_secret", "okx.passphrase",
            "okx.demo.api_key", "okx.demo.api_secret", "okx.demo.passphrase",
            "model.databricks.api_key", "model.anthropic.api_key",
            "model.openai.api_key", "coinalyze_api_key",
        ]
        pairs = []
        for k in _KNOWN_KEYS:
            try:
                v = kr.get_password(_SERVICE_NAME, k)
                if v:
                    pairs.append((k, v))
            except Exception:
                pass

    migrated = 0
    for key, value in pairs:
        if not value:
            continue
        # Only write if the file store doesn't already have this value.
        if _file_get(key) != value:
            _file_set(key, value)
            migrated += 1
    return migrated


def redact(value: str | None) -> str:
    """Redact a secret for display — show only last 4 characters."""
    if not value:
        return "***"
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]
