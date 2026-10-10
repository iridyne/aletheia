"""Fail-closed, broker-neutral proof for the live unlock boundary.

The preflight requests one private, read-only account proof through UTA. Core
receives normalized authority and hashed revision evidence; credential values
and raw exchange responses remain inside the Exchange implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.config.exchanges import execution_mode_venue, live_execution_mode_for_exchange
from aletheia.config.loader import load_config
from aletheia.storage.time_utils import utcnow as _now

logger = logging.getLogger("aletheia.trust.live_unlock")

PREFLIGHT_MAX_AGE_SECONDS = 300
PREFLIGHT_TIMEOUT_SECONDS = 20
ADAPTER_CAPABILITY_VERSION = "live-private-account-v1"
PERMISSION_UNKNOWN_POLICY = "deny"

_PUBLIC_EVIDENCE_FIELDS = frozenset({
    "broker", "execution_mode", "account_environment", "account_fingerprint",
    "credential_revision", "credential_generation", "uta_profile",
    "config_revision", "adapter_capability_version",
    "account_identity_source", "ok", "reason_code", "private_authenticated",
    "account_readable", "public_reachability", "environment_verified",
    "permission_status", "completeness", "fetched_at", "expires_at",
    "permission_unknown_policy", "evidence_binding",
})




def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _evidence_binding(evidence: dict[str, Any]) -> str:
    return _digest({
        "broker": evidence.get("broker"),
        "execution_mode": evidence.get("execution_mode"),
        "account_environment": evidence.get("account_environment"),
        "account_fingerprint": evidence.get("account_fingerprint"),
        "credential_generation": evidence.get("credential_generation"),
        "uta_profile": evidence.get("uta_profile"),
        "config_revision": evidence.get("config_revision"),
        "permission_status": evidence.get("permission_status"),
    })


def resolve_live_execution_identity(cfg=None) -> dict[str, Any]:
    """Resolve selected broker identity without contacting an exchange."""
    cfg = cfg or load_config()
    broker = execution_mode_venue(cfg.exchange.paper_execution_mode) or ""
    mode = live_execution_mode_for_exchange(broker)
    if mode is None:
        raise ValueError("selected_exchange_unsupported")
    if str(getattr(cfg.exchange, "mode", "") or "").lower() != "live":
        raise ValueError("exchange_mode_not_live")
    from aletheia.uta.manager import build_uta_registry, read_credential_revision

    credential_revision, refs = read_credential_revision(mode, cfg=cfg)
    accounts = build_uta_registry(cfg, execution_modes=(mode,)).configured_identities()
    if len(accounts) != 1 or (
        accounts[0].execution_mode != mode
        or accounts[0].venue != broker
        or accounts[0].environment != "live"
    ):
        raise ValueError("exchange_adapter_unavailable")
    uta_profile = accounts[0].profile
    credential_generation = accounts[0].credential_generation
    config_revision = _digest({
        "broker": broker,
        "execution_mode": mode,
        "environment": "live",
        "credential_revision": credential_revision,
        "credential_generation": credential_generation,
        "uta_profile": uta_profile,
        "paper_execution_mode": str(getattr(cfg.exchange, "paper_execution_mode", "")),
    })
    return {
        "broker": broker,
        "execution_mode": mode,
        "account_environment": "live",
        "credential_refs": list(refs),
        "credential_revision": credential_revision,
        "credential_generation": credential_generation,
        "uta_profile": uta_profile,
        "config_revision": config_revision,
        "adapter_capability_version": ADAPTER_CAPABILITY_VERSION,
    }


async def _private_account_probe(identity: dict[str, Any], cfg=None):
    """Read selected live authority only through the UTA account boundary."""
    from aletheia.uta.manager import create_uta_adapter
    from aletheia.uta.models import CapabilitySupport, ReadCompleteness

    adapter = create_uta_adapter(identity["execution_mode"], cfg)
    try:
        await adapter.connect()
        report = await adapter.probe_account_authority()
    finally:
        try:
            await adapter.close()
        except Exception:
            logger.warning("live_unlock: adapter close after the account-authority probe failed", exc_info=True)
    if (
        report.identity != adapter.identity
        or report.completeness is not ReadCompleteness.COMPLETE
        or not report.authenticated_private_readable
    ):
        raise RuntimeError(report.reason_code or "account_authority_incomplete")
    permission = {
        CapabilitySupport.SUPPORTED: "granted",
        CapabilitySupport.UNSUPPORTED: "denied",
    }.get(report.trading_permission, "unknown")
    return report.account_fingerprint, report.identity.environment, permission


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
        account_fingerprint, environment, permission = await asyncio.wait_for(
            _private_account_probe(identity, cfg), timeout=PREFLIGHT_TIMEOUT_SECONDS,
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

    fingerprint = account_fingerprint or _digest({
        "broker": identity["broker"],
        "environment": environment,
        "account_id": identity["credential_revision"],
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
        "account_identity_source": (
            "uta_account_probe" if account_fingerprint else "credential_binding"
        ),
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
    for field in (
        "broker",
        "execution_mode",
        "account_environment",
        "credential_revision",
        "credential_generation",
        "uta_profile",
        "config_revision",
    ):
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


def current_authorization_ref(cfg) -> str | None:
    """Return the opaque live_authorization_ref for the currently unlocked account.

    The ref is the sanitised `evidence_binding` hash from `unlock_evidence`,
    valid only while `evidence_matches_config` still accepts that evidence.
    Returns ``None`` when live is not authorized right now (any failure path).
    Callers stamp this on new live Proposals and re-check it at execution.
    """
    if getattr(getattr(cfg, "live", None), "state", "") != "live_limited":
        return None
    evidence = getattr(cfg.live, "unlock_evidence", None)
    valid, _ = evidence_matches_config(cfg, evidence)
    if not valid:
        return None
    ref = (evidence or {}).get("evidence_binding")
    return str(ref) if ref else None


def authorization_matches_ref(cfg, stored_ref: str | None) -> tuple[bool, str]:
    """Confirm a stored ref matches the currently authorized live scope.

    Fails when live is not currently authorized, when the stored ref is empty,
    or when the current scope's binding differs from what was captured with the
    Proposal (account, environment, execution profile, config, or expiry).
    """
    if not stored_ref:
        return False, "missing_live_authorization_ref"
    current = current_authorization_ref(cfg)
    if current is None:
        return False, "no_active_live_authorization"
    if current != stored_ref:
        return False, "live_authorization_scope_mismatch"
    return True, "ok"


def active_live_profile_conflict(cfg, broker: str, profile: str) -> bool:
    """Whether a credential profile is the currently unlocked live identity."""
    return (
        getattr(getattr(cfg, "live", None), "state", "") == "live_limited"
        and (execution_mode_venue(cfg.exchange.paper_execution_mode) or "") == broker.lower()
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
    except Exception as exc:
        logger.warning("live_unlock: revocation audit write failed (%s)", type(exc).__name__)
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
    except Exception as exc:
        logger.warning("live_unlock: revocation persistence failed (%s)", type(exc).__name__)
        # The authority is already revoked in memory; the next status/guard pass
        # retries persistence rather than allowing a live order through.
        persisted = False
    return {
        "reason_code": reason,
        "audit_written": audit_ok,
        "state_persisted": persisted,
    }
