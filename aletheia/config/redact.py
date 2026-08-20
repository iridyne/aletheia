"""Config credential redaction — shared by backup and export paths."""

import copy


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
    """Mask credential-bearing config.yaml fields (proxy creds, legacy coinalyze key)."""
    d = copy.deepcopy(data) if isinstance(data, dict) else {}
    net = d.get("network")
    if isinstance(net, dict) and net.get("proxy_url"):
        net["proxy_url"] = _redact_proxy_url_str(net["proxy_url"])
    md = d.get("market_data")
    if isinstance(md, dict) and md.get("coinalyze_api_key"):
        md["coinalyze_api_key"] = "***REDACTED***"
    return d
