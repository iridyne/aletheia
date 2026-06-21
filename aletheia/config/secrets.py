"""Secrets handling: OS keyring first, encrypted file fallback. Never logs secrets."""

from pathlib import Path

from aletheia.config.loader import get_data_dir

_SERVICE_NAME = "aletheia-trading-os"


def _get_keyring():
    """Try to import keyring; returns None if unavailable."""
    try:
        import keyring
        return keyring
    except Exception:
        return None


def _fallback_path() -> Path:
    return get_data_dir() / "secrets.enc"


def set_secret(key: str, value: str) -> None:
    """Store a secret. Tries keyring first, falls back to encrypted file."""
    value = value.strip()
    kr = _get_keyring()
    if kr:
        kr.set_password(_SERVICE_NAME, key, value)
        return
    # Fallback: simple Fernet-encrypted JSON file
    _file_set(key, value)


def get_secret(key: str) -> str | None:
    """Retrieve a secret. Returns None if not found."""
    kr = _get_keyring()
    if kr:
        try:
            return kr.get_password(_SERVICE_NAME, key)
        except Exception:
            pass
    return _file_get(key)


def _get_fernet():
    from cryptography.fernet import Fernet
    key_path = get_data_dir() / ".secret.key"
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(Fernet.generate_key())
    return Fernet(key_path.read_bytes())


def _file_set(key: str, value: str) -> None:
    import json
    fernet = _get_fernet()
    path = _fallback_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(fernet.decrypt(path.read_bytes()).decode())
        except Exception:
            data = {}
    data[key] = value
    path.write_bytes(fernet.encrypt(json.dumps(data).encode()))


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


def redact(value: str | None) -> str:
    """Redact a secret for display — show only last 4 characters."""
    if not value:
        return "***"
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]
