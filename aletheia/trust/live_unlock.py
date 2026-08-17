"""Fail-closed, broker-neutral proof for the live unlock boundary.

The preflight deliberately performs one private, read-only account request on
the configured live account.  It returns only hashed identity/revision data;
credentials and raw exchange responses never leave this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.config.exchanges import live_execution_mode_for_exchange
from aletheia.config.loader import load_config
from aletheia.config.secrets import get_secret

PREFLIGHT_MAX_AGE_SECONDS = 300
PREFLIGHT_TIMEOUT_SECONDS = 20
ADAPTER_CAPABILITY_VERSION = "live-private-account-v1"
PERMISSION_UNKNOWN_POLICY = "deny"

_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "bitget": ("api_key", "api_secret", "passphrase"),
    "okx": ("api_key", "api_secret", "passphrase"),
    "binance": ("api_key", "api_secret"),
}

_PUBLIC_EVIDENCE_FIELDS = frozenset({
    "broker", "execution_mode", "account_environment", "account_fingerprint",
    "credential_revision", "config_revision", "adapter_capability_version",
    "account_identity_source", "ok", "reason_code", "private_authenticated",
    "account_readable", "public_reachability", "environment_verified",
    "permission_status", "completeness", "fetched_at", "expires_at",
    "permission_unknown_policy", "evidence_binding",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _evidence_binding(evidence: dict[str, Any]) -> str:
    return _digest({
        "broker": evidence.get("broker"),
        "execution_mode": evidence.get("execution_mode"),
        "account_environment": evidence.get("account_environment"),
        "account_fingerprint": evidence.get("account_fingerprint"),
        "config_revision": evidence.get("config_revision"),
        "permission_status": evidence.get("permission_status"),
    })


def _credential_revision(broker: str) -> tuple[str, tuple[str, ...]]:
    fields = _CREDENTIAL_FIELDS[broker]
    refs = tuple(f"{broker}.live.{field}" for field in fields)
    values = []
    for ref in refs:
        value = get_secret(ref)
        if not value:
            raise ValueError("credentials_not_configured")
        values.append((ref, _secret_digest(value)))
    return _digest(values), refs


def resolve_live_execution_identity(cfg=None) -> dict[str, Any]:
    """Resolve selected broker identity without contacting an exchange."""
    cfg = cfg or load_config()
    broker = str(getattr(cfg.exchange, "name", "") or "").strip().lower()
    mode = live_execution_mode_for_exchange(broker)
    if mode is None:
        raise ValueError("selected_exchange_unsupported")
    if str(getattr(cfg.exchange, "mode", "") or "").lower() != "live":
        raise ValueError("exchange_mode_not_live")
    credential_revision, refs = _credential_revision(broker)
    config_revision = _digest({
        "broker": broker,
        "execution_mode": mode,
        "environment": "live",
        "credential_revision": credential_revision,
        "paper_execution_mode": str(getattr(cfg.exchange, "paper_execution_mode", "")),
        "risk_allowed_exchange": str(getattr(cfg.risk, "allowed_exchange", "")),
    })
    return {
        "broker": broker,
        "execution_mode": mode,
        "account_environment": "live",
        "credential_refs": list(refs),
        "credential_revision": credential_revision,
        "config_revision": config_revision,
        "adapter_capability_version": ADAPTER_CAPABILITY_VERSION,
    }


def _walk_values(value: Any, *, depth: int = 0):
    if depth > 4:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).lower(), item
            yield from _walk_values(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_values(item, depth=depth + 1)


def _account_id(raw: Any) -> str | None:
    keys = {"uid", "user_id", "userid", "account_id", "accountid"}
    for key, value in _walk_values(raw):
        if key in keys and value not in (None, "", 0, False):
            return str(value)
    return None


def _environment(raw: Any) -> str:
    for key, value in _walk_values(raw):
        key = key.replace("-", "_")
        if key in {
            "sandbox", "testnet", "simulated", "demo", "is_sandbox", "is_testnet",
            "is_demo", "testnet_mode",
        } and (value is True or str(value).lower() in {"true", "1", "yes"}):
            return "demo"
        if key in {"environment", "env", "account_environment", "accounttype", "account_type"}:
            text = str(value or "").lower()
            if any(token in text for token in ("demo", "testnet", "sandbox", "simulat")):
                return "demo"
            if any(token in text for token in ("live", "prod", "production")):
                return "live"
    return "live"


def _permission(raw: Any) -> str:
    for key, value in _walk_values(raw):
        normalized = key.replace("-", "_")
        if normalized not in {
            "cantrade", "can_trade", "trade", "trading", "order_permission",
            "order_permissions", "orderpermission", "tradepermission",
            "permissions", "permission", "perm", "authorities",
        }:
            continue
        if isinstance(value, dict):
            continue
        if isinstance(value, bool):
            return "granted" if value else "denied"
        values = value if isinstance(value, (list, tuple, set)) else [value]
        text = " ".join(str(item).lower() for item in values)
        if text in {"true", "1", "yes"}:
            return "granted"
        if text in {"false", "0", "no"}:
            return "denied"
        if any(token in text for token in ("trade", "trading", "write", "order")):
            return "granted"
        if any(token in text for token in ("read_only", "readonly", "read-only", "read")):
            return "denied"
    return "unknown"


async def _private_account_probe(identity: dict[str, Any]) -> tuple[Any, str | None, str, str]:
    broker = identity["broker"]
    client = None
    try:
        if broker == "bitget":
            from aletheia.exchanges.bitget.client import BitgetClient

            client = BitgetClient(mode="live")
            account = await client.get_account()
            raw = getattr(account, "raw", {}) or {}
            return raw, getattr(account, "user_id", None), _environment(raw), _permission(raw)
        if broker == "okx":
            from aletheia.exchanges.okx.client import OKXClient

            client = OKXClient(mode="live")
            balance = await client.fetch_balance()
            raw = balance.get("info", balance) if isinstance(balance, dict) else balance
            return raw, _account_id(raw), _environment(raw), _permission(raw)
        if broker == "binance":
            from aletheia.exchanges.binance.client import BinanceClient

            client = BinanceClient(mode="live")
            balance = await client.fetch_balance()
            raw = balance.get("info", balance) if isinstance(balance, dict) else balance
            return raw, _account_id(raw), _environment(raw), _permission(raw)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
    raise ValueError("selected_exchange_unsupported")


def public_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Return only stable, non-secret evidence fields for API/audit output."""
    evidence = evidence or {}
    return {
        key: evidence[key]
        for key in _PUBLIC_EVIDENCE_FIELDS
        if key in evidence
    }


def _result_failure(
    identity: dict[str, Any] | None,
    reason_code: str,
    *,
    now: datetime,
    account_environment: str | None = None,
    permission_status: str = "unknown",
    private_authenticated: bool = False,
    account_readable: bool = False,
) -> dict[str, Any]:
    result = dict(identity or {})
    result.update({
        "ok": False,
        "reason_code": reason_code,
        "private_authenticated": private_authenticated,
        "public_reachability": "not_used_as_authority",
        "account_readable": account_readable,
        "account_environment": account_environment or result.get("account_environment", "live"),
        "environment_verified": False,
        "permission_status": permission_status,
        "permission_unknown_policy": PERMISSION_UNKNOWN_POLICY,
        "completeness": "incomplete",
        "fetched_at": _iso(now),
        "expires_at": _iso(now),
        "adapter_capability_version": ADAPTER_CAPABILITY_VERSION,
    })
    return result


async def run_live_preflight(cfg=None) -> dict[str, Any]:
    """Authenticate the selected live account with one private read-only request."""
    now = _now()
    try:
        identity = resolve_live_execution_identity(cfg)
    except ValueError as exc:
        return _result_failure(None, str(exc), now=now)
    try:
        raw, account_id, environment, permission = await asyncio.wait_for(
            _private_account_probe(identity), timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Only stable categories cross the trust boundary.  Raw exchange errors can
        # contain URLs, request headers, or credential fragments.
        name = type(exc).__name__.lower()
        reason = "connection_timeout" if isinstance(exc, asyncio.TimeoutError) or "timeout" in name else "connection_failed"
        if isinstance(exc, RuntimeError) and "not configured" in str(exc).lower():
            reason = "credentials_not_configured"
        return _result_failure(identity, reason, now=now)

    if environment != "live":
        return _result_failure(
            identity,
            "environment_mismatch",
            now=now,
            account_environment=environment,
            permission_status=permission,
            private_authenticated=True,
            account_readable=True,
        )
    if permission == "denied":
        return _result_failure(
            identity,
            "permission_denied",
            now=now,
            account_environment=environment,
            permission_status=permission,
            private_authenticated=True,
            account_readable=True,
        )
    if permission == "unknown" and PERMISSION_UNKNOWN_POLICY == "deny":
        result = _result_failure(
            identity,
            "permission_unknown",
            now=now,
            account_environment=environment,
            permission_status=permission,
            private_authenticated=True,
            account_readable=True,
        )
        result.update({"private_authenticated": True, "account_readable": True})
        return result

    fingerprint = _digest({
        "broker": identity["broker"],
        "environment": environment,
        "account_id": account_id or identity["credential_revision"],
    })
    result = dict(identity)
    result.update({
        "ok": True,
        "reason_code": "ok",
        "account_fingerprint": fingerprint,
        "private_authenticated": True,
        "public_reachability": "not_used_as_authority",
        "account_readable": True,
        "environment_verified": True,
        "permission_status": permission,
        "permission_unknown_policy": PERMISSION_UNKNOWN_POLICY,
        "completeness": "complete",
        "account_identity_source": "exchange_user_id" if account_id else "credential_binding",
        "fetched_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=PREFLIGHT_MAX_AGE_SECONDS)),
    })
    result["evidence_binding"] = _evidence_binding(result)
    return result


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def evidence_matches_config(cfg, evidence: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(evidence, dict) or not evidence.get("ok"):
        return False, "missing_unlock_evidence"
    try:
        identity = resolve_live_execution_identity(cfg)
    except ValueError as exc:
        return False, str(exc)
    for field in ("broker", "execution_mode", "account_environment", "credential_revision", "config_revision"):
        if evidence.get(field) != identity.get(field):
            return False, f"{field}_mismatch"
    now = _now()
    fetched = _parse_time(evidence.get("fetched_at"))
    expires = _parse_time(evidence.get("expires_at"))
    if fetched is None or expires is None or fetched > now or expires <= now:
        return False, "stale_unlock_evidence"
    if (now - fetched).total_seconds() > PREFLIGHT_MAX_AGE_SECONDS:
        return False, "stale_unlock_evidence"
    if evidence.get("permission_status") != "granted":
        return False, "permission_not_granted"
    binding = evidence.get("evidence_binding")
    if not binding:
        return False, "missing_evidence_binding"
    if binding != _evidence_binding(evidence):
        return False, "account_fingerprint_mismatch"
    return True, "ok"


def active_live_profile_conflict(cfg, broker: str, profile: str) -> bool:
    """Whether a credential profile is the currently unlocked live identity."""
    return (
        getattr(getattr(cfg, "live", None), "state", "") == "live_limited"
        and str(getattr(getattr(cfg, "exchange", None), "name", "")).lower() == broker.lower()
        and profile == "live"
    )


def revoke_stale_live_unlock(cfg) -> dict[str, Any] | None:
    """Revoke live authority once its selected-account evidence is invalid."""
    if getattr(getattr(cfg, "live", None), "state", "") != "live_limited":
        return None
    valid, reason = evidence_matches_config(cfg, getattr(cfg.live, "unlock_evidence", None))
    if valid:
        return None

    old_permission_mode = getattr(getattr(cfg, "operator", None), "permission_mode", "ReadOnly")
    identity = public_evidence(getattr(cfg.live, "unlock_evidence", None))
    audit_ok = False
    try:
        from aletheia.files.jsonl import write_audit

        audit_ok = bool(write_audit({
            "actor": "system",
            "source": "live_trust",
            "event_type": "live_unlock_revoked",
            "entity_type": "live_state",
            "before": {
                "state": "live_limited",
                "operator_permission_mode": old_permission_mode,
                "live_execution_identity": identity,
            },
            "after": {
                "state": "live_disabled",
                "operator_permission_mode": "ReadOnly",
                "live_execution_identity": None,
            },
            "live_execution_identity": identity,
            "message": (
                "Live unlock revoked because selected-account evidence is invalid: "
                f"{reason}"
            ),
        }))
    except Exception:
        audit_ok = False

    cfg.live.state = "live_disabled"
    cfg.live.unlock_evidence = None
    if hasattr(cfg, "operator"):
        cfg.operator.permission_mode = "ReadOnly"
    persisted = False
    try:
        from aletheia.config.loader import save_config

        save_config(cfg)
        persisted = True
    except Exception:
        # The authority is already revoked in memory; the next status/guard pass
        # retries persistence rather than allowing a live order through.
        persisted = False
    return {
        "reason_code": reason,
        "audit_written": audit_ok,
        "state_persisted": persisted,
    }
