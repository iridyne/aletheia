"""Config credential redaction — shared by backup and export paths."""

import copy
from collections.abc import Mapping

from aletheia.config.secrets import is_secret_key, normalize_secret_key


def _redact_proxy_url_str(url: str) -> str:
    if not url:
        return url
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(str(url))
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    except Exception:
        return "***"
    return url


def redact_config_dict(data) -> dict:
    """Recursively redact credentials for backup/sync without mutating the input.

    Token rate limits, the require-token switch and observed stock symbols are
    public configuration, not credentials despite the classifier substring.
    """
    def scrub(value):
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                normalized = normalize_secret_key(key)
                public = (
                    (normalized == "inferencetokensperminute" and type(item) is int)
                    or (normalized == "requiretoken" and type(item) is bool)
                    or (normalized == "observedstocktokens" and isinstance(item, list)
                        and all(isinstance(symbol, str) for symbol in item))
                )
                result[key] = "" if is_secret_key(key) and not public else scrub(item)
            return result
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return copy.deepcopy(value)

    d = scrub(data) if isinstance(data, Mapping) else {}
    net = d.get("network")
    if isinstance(net, dict) and net.get("proxy_url"):
        net["proxy_url"] = _redact_proxy_url_str(net["proxy_url"])
    return d
