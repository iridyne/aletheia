"""Position reconciler — discover, classify, and (later) adopt account positions.

Aletheia normally manages only positions it opened (proposal → risk → order →
position). But the exchange account may hold positions Aletheia did NOT open:
pre-existing positions at takeover, or anything opened outside the system. Those
were invisible to management — `exchange_sync` cached them read-only and only a
bare drift *count* reached the UI.

This module joins the cached exchange position view against the local positions
table and classifies every position so the rest of the system (UI, scheduler,
operator commands) can act on the gaps:

  - managed  : tracked locally AND present on the exchange — already under control.
  - external : on the exchange, NOT tracked locally — an adoption candidate.
  - orphaned : open locally, NOT on the exchange — a reconcile-close candidate
               (our books say open; the exchange says gone).

Classification is PURE and READ-ONLY (no exchange call, no DB write) — it reads the
exchange cache and the local positions table. Adoption and orphan-close (which DO
write local rows) live in later phases and are clearly separated.

Match identity is (canonical symbol, normalized market type, direction), never the
symbol alone. The account may hold spot and futures exposure, plus both hedge-mode
directions, at the same time. Quantity is compared only inside that exact identity.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from aletheia.data.symbols import to_canonical
from aletheia.portfolio import exchange_sync
from aletheia.storage.repositories import list_all_open_positions, list_positions

logger = logging.getLogger("aletheia.position_reconciler")

# Relative quantity tolerance when matching an exchange position to a local one.
# The exchange aggregates fills, so local-vs-exchange size can drift a little.
_QTY_REL_TOL = 0.02  # 2%

_LIVE_EXECUTION_MODES = {
    "bitget": "bitget_live_limited",
    "okx": "okx_live_limited",
    "binance": "binance_live_limited",
}


def _execution_mode_for(account_mode: str) -> str | None:
    """Resolve execution_mode from account mode, respecting paper_execution_mode config."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    if account_mode == "live":
        return _LIVE_EXECUTION_MODES.get((cfg.exchange.name or "").lower())
    if account_mode == "demo":
        return cfg.exchange.paper_execution_mode
    return None


def _norm_direction(side: str) -> str:
    """Normalise a side/direction to 'long' or 'short'."""
    s = (side or "").lower()
    if s in ("long", "buy"):
        return "long"
    if s in ("short", "sell"):
        return "short"
    return s


def _normalize_market_type(market_type: object) -> str | None:
    value = str(market_type or "").strip().lower()
    if value == "spot":
        return "spot"
    if value in {"perp", "futures", "swap", "usdt_futures"}:
        return "usdt_futures"
    return None


def _base_key(symbol: object, direction: object) -> tuple[str, str]:
    return (to_canonical(str(symbol or "")), _norm_direction(str(direction or "")))


def _key(
    symbol: object,
    market_type: object,
    direction: object,
) -> tuple[str, str, str] | None:
    """Return canonical instrument identity, or None when market type is unknown."""
    normalized_market = _normalize_market_type(market_type)
    if normalized_market is None:
        return None
    canonical_symbol, normalized_direction = _base_key(symbol, direction)
    return (canonical_symbol, normalized_market, normalized_direction)


class PositionAdoptionIntegrityError(ValueError):
    """Typed, sanitized rejection for an incomplete exchange Position projection."""

    def __init__(
        self,
        *,
        code: str,
        field: str,
        symbol: str,
        market_type: str,
        direction: str,
    ) -> None:
        self.code = code
        self.field = field
        self.identity = {
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
        }
        super().__init__(
            f"{code}: field={field} identity="
            f"{symbol}|{market_type}|{direction}"
        )


@dataclass(frozen=True)
class ReconcileIntegrityIssue:
    reason: str
    source: str
    symbol: str
    market_type: str
    direction: str
    row_count: int = 1

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "source": self.source,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "direction": self.direction,
            "row_count": self.row_count,
        }


@dataclass
class ReconcileItem:
    """One classified position. `local`/`exchange` carry the source rows when present."""

    symbol: str
    direction: str
    classification: str  # "managed" | "external" | "orphaned"
    local: Optional[dict] = None
    exchange: Optional[dict] = None
    qty_drift: Optional[float] = None  # exchange_qty - local_qty, when both present

    def to_dict(self) -> dict:
        # market_type lets the frontend render the symbol per the spot/derivative
        # convention (perp → no slash, spot → slash). Prefer the local row's type;
        # fall back to the exchange row's when this position is exchange-only.
        market_type = None
        if self.local:
            market_type = self.local.get("market_type")
        if not market_type and self.exchange:
            market_type = self.exchange.get("market_type")
        normalized_market = _normalize_market_type(market_type)
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "classification": self.classification,
            "market_type": normalized_market or market_type,
            "local": self.local,
            "exchange": self.exchange,
            "qty_drift": self.qty_drift,
        }


@dataclass
class ReconcileResult:
    managed: list[ReconcileItem] = field(default_factory=list)
    external: list[ReconcileItem] = field(default_factory=list)
    orphaned: list[ReconcileItem] = field(default_factory=list)
    integrity_issues: list[ReconcileIntegrityIssue] = field(default_factory=list)
    sync_status: str = "unconfigured"
    view_evidence: dict | None = None

    def to_dict(self) -> dict:
        return {
            "sync_status": self.sync_status,
            "view_evidence": self.view_evidence,
            "counts": {
                "managed": len(self.managed),
                "external": len(self.external),
                "orphaned": len(self.orphaned),
                "degraded": len(self.integrity_issues),
            },
            "managed": [i.to_dict() for i in self.managed],
            "external": [i.to_dict() for i in self.external],
            "orphaned": [i.to_dict() for i in self.orphaned],
            "integrity_issues": [issue.to_dict() for issue in self.integrity_issues],
        }


def classify_positions(mode: Optional[str] = None) -> ReconcileResult:
    """Classify every position on the account as managed / external / orphaned.

    Pure read-only join of the cached exchange position view against the local
    open positions table. When the exchange cache is unavailable (unconfigured /
    error / never-synced), every local open position is reported as `managed` and
    `sync_status` reflects why — we never infer `orphaned` from a missing cache,
    because "no exchange data" must not look like "the exchange has no positions".
    """
    from aletheia.config.loader import load_config

    cfg = load_config()
    account_mode = mode or cfg.exchange.mode
    target_execution_mode = _execution_mode_for(account_mode)
    local_open = list_all_open_positions()
    in_scope = [
        position for position in local_open
        if position.get("execution_mode") == target_execution_mode
    ]
    result = ReconcileResult()

    # Local simulation has no exchange-side position view by design. Treat every
    # local row as managed and never let a stale/unrelated broker cache close it.
    if target_execution_mode == "paper_internal":
        for position in in_scope:
            result.managed.append(
                ReconcileItem(
                    symbol=to_canonical(position["symbol"]),
                    direction=_norm_direction(position["direction"]),
                    classification="managed",
                    local=position,
                )
            )
        result.sync_status = "local_only"
        return result

    # Demo reconciliation prefers the unified (UTA) state, but only the active
    # broker's rows. Live accounts are not represented by the current UTA adapters
    # (all registered adapters are demo/testnet), so a demo aggregate must never be
    # used to classify live positions.
    cached = None
    # View-completeness gate (P0, 2026-07-02): only trust the unified view for
    # classification when the ACTIVE broker is actually in it. A unified cache can
    # be FRESH yet MISSING the active broker (e.g. OKX API 50001 → UTA drops okx,
    # leaving only bitget). Classifying OKX locals against that incomplete view
    # orphans them → thrash. This is the "fresh but incomplete" variant the earlier
    # stale-cache fix did not cover. See dynamic_sizing_operator_persona_design.md §4.1.
    if account_mode == "demo":
        unified = exchange_sync.get_unified_cached_state()
        connected = {
            account.get("broker_id")
            for account in ((unified or {}).get("accounts") or [])
            if account.get("broker_id")
        }
        view_complete = target_execution_mode in connected
        view_reason = (
            f"{target_execution_mode} connected"
            if view_complete
            else f"{target_execution_mode} not connected (have {sorted(connected) or 'none'})"
        )
        if unified is not None and view_complete:
            age = unified.get("age_seconds")
            unified_positions = unified.get("positions") or []
            # Broker identity is mandatory once more than one account shares this
            # cache. An old/partial row without broker_id cannot safely prove either
            # presence or absence for the active account, so degrade instead of
            # inferring an orphan.
            positions_identified = True
            for position in unified_positions:
                try:
                    quantity = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    if position.get("broker_id") in {None, target_execution_mode}:
                        positions_identified = False
                        break
                    continue
                if not math.isfinite(quantity) or quantity < 0:
                    if position.get("broker_id") in {None, target_execution_mode}:
                        positions_identified = False
                        break
                    continue
                if quantity > 0 and not position.get("broker_id"):
                    positions_identified = False
                    break
            if age is not None and age < 600 and positions_identified:
                cached = {
                    "positions": [
                        position for position in unified_positions
                        if position.get("broker_id") == target_execution_mode
                    ],
                    "age_seconds": age,
                    "fetched_at": unified.get("fetched_at", ""),
                }
            elif not positions_identified:
                view_reason = "unified positions missing broker_id"
        if not view_complete or cached is None:
            logger.warning(
                "Reconcile: active-broker view unusable (%s) — not classifying "
                "against an incomplete or ambiguous unified view.", view_reason,
            )
    if cached is None:
        # No fresh multi-broker (unified) view. The legacy per-mode cache is
        # written ONLY by the Bitget sync, so falling back to it is safe only when
        # the active paper broker IS Bitget. On a non-Bitget account (e.g.
        # okx_demo) that cache is fresh-but-EMPTY of this account's positions, so
        # classifying against it marks every real position `orphaned` →
        # reconcile-close (local only) → the position survives on the exchange →
        # re-adopt next cycle → an endless false-orphan thrash (41% of positions
        # closed reconciled_gone, 56% re-adopted, strategy attribution destroyed).
        # For a non-Bitget account with no usable unified view, leave `cached`
        # None so the guard below reports all-managed and bails — never infer
        # orphans from a wrong-broker cache. Live keeps the legacy path (live
        # reconcile is alert-only and Bitget-based today). See
        # docs/research/audits/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
        # The legacy cache is Bitget-only. It is valid for Bitget demo/live, but
        # never for OKX/Binance or any future broker account.
        if (target_execution_mode or "").startswith("bitget"):
            cached = exchange_sync.get_cached_state(account_mode)
    sync_status = exchange_sync.status_for(cached)

    result.sync_status = sync_status
    if target_execution_mode:
        from aletheia.portfolio.disappearance_confirmation import build_view_evidence
        result.view_evidence = build_view_evidence(
            cached,
            broker_id=target_execution_mode,
            account_environment=account_mode,
            execution_mode=target_execution_mode,
            complete=(cached is not None and sync_status == "ok"),
        )

    # Without a usable exchange view we cannot classify externals/orphans honestly.
    # Report locals as managed and bail — the caller sees sync_status and knows why.
    if cached is None or sync_status != "ok":
        for p in in_scope:
            result.managed.append(
                ReconcileItem(
                    symbol=to_canonical(p["symbol"]),
                    direction=_norm_direction(p["direction"]),
                    classification="managed",
                    local=p,
                )
            )
        return result

    exchange_positions = cached.get("positions") or []
    expected_fingerprint = str(
        (result.view_evidence or {}).get("account_fingerprint") or ""
    )
    local_groups: dict[tuple[str, str, str], list[dict]] = {}
    exchange_groups: dict[tuple[str, str, str], list[dict]] = {}
    blocked_bases: set[tuple[str, str]] = set()
    blocked_keys: set[tuple[str, str, str]] = set()

    def add_issue(
        *,
        reason: str,
        source: str,
        row: dict,
        row_count: int = 1,
    ) -> None:
        raw_direction = row.get("direction") or row.get("side") or ""
        result.integrity_issues.append(ReconcileIntegrityIssue(
            reason=reason,
            source=source,
            symbol=to_canonical(str(row.get("symbol") or "")),
            market_type=str(row.get("market_type") or "unknown"),
            direction=_norm_direction(str(raw_direction)),
            row_count=row_count,
        ))

    def account_matches(row: dict, source: str) -> bool:
        fingerprint = ""
        if source == "local":
            raw_identity = row.get("reconciliation_identity")
            if raw_identity:
                try:
                    identity = json.loads(raw_identity)
                except (TypeError, json.JSONDecodeError):
                    add_issue(
                        reason="invalid_local_reconciliation_identity",
                        source=source,
                        row=row,
                    )
                    return False
                if not isinstance(identity, dict):
                    add_issue(
                        reason="invalid_local_reconciliation_identity",
                        source=source,
                        row=row,
                    )
                    return False
                fingerprint = str(identity.get("account_fingerprint") or "")
        else:
            fingerprint = str(row.get("account_fingerprint") or "")
        if fingerprint and expected_fingerprint and fingerprint != expected_fingerprint:
            add_issue(
                reason=f"{source}_account_fingerprint_mismatch",
                source=source,
                row=row,
            )
            return False
        return True

    def index_rows(rows: list[dict], source: str) -> dict[tuple[str, str, str], list[dict]]:
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for row in rows:
            raw_direction = row.get("direction") or row.get("side") or ""
            base = _base_key(row.get("symbol"), raw_direction)
            normalized_market = _normalize_market_type(row.get("market_type"))
            if normalized_market is None:
                add_issue(
                    reason=f"unknown_{source}_market_type",
                    source=source,
                    row=row,
                )
                blocked_bases.add(base)
                continue
            if not base[0] or base[1] not in {"long", "short"}:
                add_issue(
                    reason=f"invalid_{source}_position_identity",
                    source=source,
                    row=row,
                )
                blocked_bases.add(base)
                continue
            identity = (base[0], normalized_market, base[1])
            if source == "local":
                try:
                    local_quantity = float(row.get("quantity"))
                except (TypeError, ValueError):
                    local_quantity = math.nan
                if not math.isfinite(local_quantity) or local_quantity <= 0:
                    add_issue(
                        reason="invalid_local_quantity",
                        source=source,
                        row=row,
                    )
                    blocked_keys.add(identity)
                    continue
            if not account_matches(row, source):
                blocked_keys.add(identity)
                continue
            grouped.setdefault(identity, []).append(row)
        return grouped

    def block_row(row: dict) -> None:
        raw_direction = row.get("direction") or row.get("side") or ""
        base = _base_key(row.get("symbol"), raw_direction)
        identity = _key(row.get("symbol"), row.get("market_type"), raw_direction)
        if identity is None or not base[0] or base[1] not in {"long", "short"}:
            blocked_bases.add(base)
        else:
            blocked_keys.add(identity)

    local_groups = index_rows(in_scope, "local")
    usable_exchange_rows: list[dict] = []
    for exchange_row in exchange_positions:
        try:
            quantity = float(exchange_row.get("quantity") or 0)
        except (TypeError, ValueError):
            add_issue(
                reason="invalid_exchange_quantity",
                source="exchange",
                row=exchange_row,
            )
            block_row(exchange_row)
            continue
        if quantity == 0:
            continue
        if not math.isfinite(quantity) or quantity < 0:
            add_issue(
                reason="invalid_exchange_quantity",
                source="exchange",
                row=exchange_row,
            )
            block_row(exchange_row)
            continue
        usable_exchange_rows.append(exchange_row)
    exchange_groups = index_rows(usable_exchange_rows, "exchange")

    ambiguous_keys: set[tuple[str, str, str]] = set()
    for source, groups in (("local", local_groups), ("exchange", exchange_groups)):
        for identity, rows in groups.items():
            if len(rows) <= 1:
                continue
            ambiguous_keys.add(identity)
            add_issue(
                reason=f"duplicate_{source}_identity",
                source=source,
                row=rows[0],
                row_count=len(rows),
            )

    result.integrity_issues.sort(
        key=lambda issue: (
            issue.symbol,
            issue.direction,
            issue.market_type,
            issue.source,
            issue.reason,
        )
    )

    for identity in sorted(set(local_groups) | set(exchange_groups)):
        symbol, market_type, direction = identity
        if (
            identity in ambiguous_keys
            or identity in blocked_keys
            or (symbol, direction) in blocked_bases
        ):
            continue
        local_rows = local_groups.get(identity, [])
        exchange_rows = exchange_groups.get(identity, [])
        local_row = local_rows[0] if local_rows else None
        exchange_row = exchange_rows[0] if exchange_rows else None
        if local_row is not None and exchange_row is not None:
            result.managed.append(ReconcileItem(
                symbol=symbol,
                direction=direction,
                classification="managed",
                local=local_row,
                exchange=exchange_row,
                qty_drift=round(
                    float(exchange_row.get("quantity") or 0)
                    - float(local_row.get("quantity") or 0),
                    8,
                ),
            ))
        elif exchange_row is not None:
            result.external.append(ReconcileItem(
                symbol=symbol,
                direction=direction,
                classification="external",
                exchange=exchange_row,
            ))
        elif local_row is not None:
            result.orphaned.append(ReconcileItem(
                symbol=symbol,
                direction=direction,
                classification="orphaned",
                local=local_row,
            ))

    return result


def detect_reconcile_thrash(window_minutes: int = 30) -> dict:
    """Detect systemic administrative-close → reopen churn, read-only.

    Native and adopted reopens are both evidence.  Matching is scoped to execution
    mode, broker/account environment, market type, canonical symbol and direction;
    explicit unequal account fingerprints never match.  The source label explains
    how the position reappeared but is not part of the incident definition.
    """
    from collections import Counter
    from datetime import datetime, timezone, timedelta
    import json
    from aletheia.analytics.outcome_integrity import classify_closure

    out = {
        "detector_available": False,
        "window_minutes": window_minutes, "reconciled_gone_recent": 0,
        "adopted_recent": 0, "administrative_close_recent": 0,
        "reopened_recent": 0, "reopened_by_source": {},
        "churned_symbols": [], "churned_identities": [],
        "churn_count": 0, "is_thrashing": False,
    }
    try:
        from aletheia.storage.sqlite import get_db
        cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        cutoff = cutoff_dt.isoformat()
        db = get_db()
        closed = db.execute(
            "SELECT * FROM positions WHERE status='closed' AND closed_at >= ?",
            (cutoff,),
        ).fetchall()
        reopened = db.execute(
            # Opening is an event, not the row's current lifecycle state. A reopened
            # row may already have closed again by the time the detector runs.
            "SELECT * FROM positions WHERE opened_at >= ?",
            (cutoff,),
        ).fetchall()
    except Exception:
        return out
    out["detector_available"] = True

    def parse_time(value) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def scoped(row) -> dict:
        raw = row["reconciliation_identity"] if "reconciliation_identity" in row.keys() else None
        try:
            identity = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            identity = {}
        mode = str(row["execution_mode"] or "unknown")
        environment = str(identity.get("account_environment") or (
            "demo" if "demo" in mode or mode == "paper_internal" else "live"
        ))
        return {
            "execution_mode": mode,
            "broker_id": str(identity.get("broker_id") or mode),
            "account_environment": environment,
            "account_fingerprint": str(identity.get("account_fingerprint") or ""),
            "symbol": str(row["symbol"]),
            "market_type": str(row["market_type"] or "unknown"),
            "direction": _norm_direction(row["direction"] or ""),
        }

    administrative = [dict(row) for row in closed if classify_closure(dict(row)).category
                      in {"administrative", "maintenance"}]
    out["reconciled_gone_recent"] = sum(
        1 for row in closed if row["close_reason"] == "reconciled_gone"
    )
    out["adopted_recent"] = sum(1 for row in reopened if row["source"] == "adopted")
    out["administrative_close_recent"] = len(administrative)
    out["reopened_recent"] = len(reopened)

    matched: dict[tuple, dict] = {}
    for closed_row in administrative:
        closed_at = parse_time(closed_row.get("closed_at"))
        if closed_at is None:
            continue
        close_scope = scoped(closed_row)
        base_key = tuple(close_scope[key] for key in (
            "execution_mode", "broker_id", "account_environment", "symbol",
            "market_type", "direction",
        ))
        for reopened_row_raw in reopened:
            reopened_row = dict(reopened_row_raw)
            if reopened_row.get("id") == closed_row.get("id"):
                continue
            opened_at = parse_time(reopened_row.get("opened_at"))
            if opened_at is None or opened_at < closed_at:
                continue
            reopen_scope = scoped(reopened_row)
            reopen_key = tuple(reopen_scope[key] for key in (
                "execution_mode", "broker_id", "account_environment", "symbol",
                "market_type", "direction",
            ))
            if reopen_key != base_key:
                continue
            close_account = close_scope["account_fingerprint"]
            reopen_account = reopen_scope["account_fingerprint"]
            if close_account and reopen_account and close_account != reopen_account:
                continue
            account_scope = close_account or reopen_account or "legacy-unspecified"
            identity_key = "|".join((*base_key, account_scope))
            match_key = (*base_key, account_scope)
            matched[match_key] = {
                **reopen_scope,
                "account_fingerprint": account_scope,
                "identity_key": identity_key,
                "closed_position_id": closed_row.get("id"),
                "reopened_position_id": reopened_row.get("id"),
                "closed_at": closed_row.get("closed_at"),
                "reopened_at": reopened_row.get("opened_at"),
                "close_category": classify_closure(closed_row).category,
                "reopen_source": str(reopened_row.get("source") or "unknown"),
            }

    identities = sorted(matched.values(), key=lambda item: item["identity_key"])
    sources = Counter(item["reopen_source"] for item in identities)
    out["churned_identities"] = identities
    out["reopened_by_source"] = dict(sorted(sources.items()))
    out["churned_symbols"] = sorted({
        f"{item['symbol']}:{item['direction']}" for item in identities
    })
    out["churn_count"] = len(identities)
    out["is_thrashing"] = len(identities) >= 2
    return out


def _adoption_error(
    *,
    field: str,
    symbol: str,
    market_type: str,
    direction: str,
) -> PositionAdoptionIntegrityError:
    return PositionAdoptionIntegrityError(
        code="invalid_position_economics",
        field=field,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
    )


def _validated_adoption_leverage(
    exchange_pos: dict,
    *,
    symbol: str,
    market_type: str,
    direction: str,
) -> tuple[int, str]:
    raw = exchange_pos.get("leverage")
    reported_source = str(exchange_pos.get("leverage_source") or "")
    if market_type == "spot":
        if raw not in (None, ""):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise _adoption_error(
                    field="leverage",
                    symbol=symbol,
                    market_type=market_type,
                    direction=direction,
                ) from None
            if not math.isfinite(value) or value != 1.0:
                raise _adoption_error(
                    field="leverage",
                    symbol=symbol,
                    market_type=market_type,
                    direction=direction,
                )
        return 1, "not_applicable_spot"

    if reported_source in {"unavailable", "invalid"}:
        raise _adoption_error(
            field="leverage",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    if isinstance(raw, bool):
        raise _adoption_error(
            field="leverage",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise _adoption_error(
            field="leverage",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        ) from None
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        raise _adoption_error(
            field="leverage",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    return int(value), "exchange_reported"


def _reconciliation_identity(
    exchange_pos: dict,
    *,
    account_mode: str,
    execution_mode: str,
    symbol: str,
    market_type: str,
    direction: str,
) -> dict:
    from aletheia.portfolio.disappearance_confirmation import (
        logical_account_fingerprint,
    )

    broker_id = str(exchange_pos.get("broker_id") or execution_mode)
    if broker_id != execution_mode:
        raise _adoption_error(
            field="broker_id",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    supplied_environment = str(
        exchange_pos.get("account_environment") or account_mode
    )
    if supplied_environment != account_mode:
        raise _adoption_error(
            field="account_environment",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    account_fingerprint = logical_account_fingerprint(
        broker_id=broker_id,
        account_environment=account_mode,
        execution_mode=execution_mode,
    )
    supplied_fingerprint = str(exchange_pos.get("account_fingerprint") or "")
    if supplied_fingerprint and supplied_fingerprint != account_fingerprint:
        raise _adoption_error(
            field="account_fingerprint",
            symbol=symbol,
            market_type=market_type,
            direction=direction,
        )
    return {
        "execution_mode": execution_mode,
        "broker_id": broker_id,
        "account_environment": account_mode,
        "account_fingerprint": account_fingerprint,
        "symbol": symbol,
        "market_type": market_type,
        "direction": direction,
    }


def _position_exposure_field_category(position: dict) -> str | None:
    market_type = _normalize_market_type(position.get("market_type"))
    if market_type is None:
        return "market_type_unknown"
    for field_name in ("quantity", "entry_price"):
        try:
            value = float(position.get(field_name))
        except (TypeError, ValueError):
            return f"{field_name}_invalid"
        if not math.isfinite(value) or value <= 0:
            return f"{field_name}_invalid"
    current_price = position.get("current_price")
    if current_price is not None:
        try:
            current_value = float(current_price)
        except (TypeError, ValueError):
            return "current_price_invalid"
        if not math.isfinite(current_value) or current_value <= 0:
            return "current_price_invalid"
    raw_leverage = position.get("leverage")
    if market_type == "spot" and raw_leverage is None:
        return None
    if raw_leverage is None:
        return "leverage_unavailable"
    try:
        leverage = float(raw_leverage)
    except (TypeError, ValueError):
        return "leverage_invalid"
    if (
        not math.isfinite(leverage)
        or leverage <= 0
        or not leverage.is_integer()
        or (market_type == "spot" and leverage != 1.0)
    ):
        return "leverage_invalid"
    return None


def report_invalid_open_position_exposures(
    execution_mode: str | None = None,
) -> list[dict]:
    """Read-only, sanitized inventory of open Position exposure defects."""
    rows = list_all_open_positions()
    report = []
    for position in rows:
        if execution_mode and position.get("execution_mode") != execution_mode:
            continue
        category = _position_exposure_field_category(position)
        if category is None:
            continue
        report.append({
            "field_category": category,
            "market_type": str(position.get("market_type") or "unknown"),
            "position_id": str(position.get("id") or "unknown"),
            "symbol": to_canonical(str(position.get("symbol") or "")),
        })
    return sorted(report, key=lambda item: item["position_id"])


def _merged_json(raw: object, updates: dict) -> str:
    try:
        current = json.loads(str(raw)) if raw else {}
    except (TypeError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(updates)
    return json.dumps(current, sort_keys=True, separators=(",", ":"))


def _reconcile_managed_leverage(
    result: ReconcileResult,
    *,
    account_mode: str,
) -> tuple[list[dict], list[dict]]:
    """Fill only missing/invalid futures leverage on an exact managed identity."""
    from aletheia.files.jsonl import write_audit
    from aletheia.storage import repositories as repo

    repairs = []
    degraded = []
    for item in result.managed:
        local = item.local or {}
        exchange = item.exchange or {}
        market_type = _normalize_market_type(local.get("market_type"))
        if market_type != "usdt_futures":
            continue
        category = _position_exposure_field_category(local)
        if category not in {"leverage_unavailable", "leverage_invalid"}:
            continue
        try:
            leverage, leverage_source = _validated_adoption_leverage(
                exchange,
                symbol=item.symbol,
                market_type=market_type,
                direction=item.direction,
            )
            execution_mode = str(local.get("execution_mode") or "")
            identity = _reconciliation_identity(
                exchange,
                account_mode=account_mode,
                execution_mode=execution_mode,
                symbol=item.symbol,
                market_type=market_type,
                direction=item.direction,
            )
        except PositionAdoptionIntegrityError as exc:
            degraded.append({
                "code": exc.code,
                "field_category": exc.field,
                "position_id": str(local.get("id") or "unknown"),
                **exc.identity,
            })
            continue
        position_id = str(local["id"])
        identity["position_id"] = position_id
        evidence = {
            "leverage": {
                "source": leverage_source,
                "status": "verified",
                "value": leverage,
            },
            "projection_source": "exact_managed_exchange_view",
        }
        repo.update_position(
            position_id,
            leverage=leverage,
            reconciliation_identity=_merged_json(
                local.get("reconciliation_identity"),
                identity,
            ),
            reconciliation_evidence=_merged_json(
                local.get("reconciliation_evidence"),
                evidence,
            ),
        )
        repair = {
            "leverage": leverage,
            "position_id": position_id,
            "source": leverage_source,
        }
        repairs.append(repair)
        try:
            write_audit({
                "actor": "system",
                "source": "position_reconciler",
                "event_type": "position_leverage_recovered",
                "entity_type": "position",
                "entity_id": position_id,
                "before": {"leverage": local.get("leverage")},
                "after": repair,
                "message": "Recovered validated leverage from exact managed exchange view",
            })
        except Exception:
            logger.warning(
                "Position leverage repair audit unavailable for %s",
                position_id,
                exc_info=True,
            )
    return repairs, degraded


def _recover_strategy_link(
    symbol: str,
    market_type: str,
    direction: str,
    entry_price: float,
    execution_mode: str,
) -> tuple[str | None, str | None]:
    """Recover (strategy_id, opportunity_id) for an external about to be adopted,
    IFF it tightly matches a very recently thrash-closed local position.

    P0 stops the systemic false-orphan thrash at the source, but any residual
    transient orphan→re-adopt would still sever attribution (adopted rows get
    strategy_id=NULL). This recovers it, but ONLY on a tight match — same symbol +
    direction, close_reason='reconciled_gone' (a bookkeeping close, NOT a real
    stop_loss/take_profit exit), closed within 15 min, entry within 0.5% — so a
    genuinely-new external position is never mis-attributed. Returns (None, None)
    when there is no confident match.
    """
    from datetime import datetime, timezone, timedelta
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        rows = list_positions(status="closed")
    except Exception:
        return None, None
    best = None
    for r in rows:
        if (to_canonical(str(r.get("symbol") or "")) != symbol
                or _normalize_market_type(r.get("market_type")) != market_type
                or _norm_direction(r.get("direction") or "") != direction
                or r.get("execution_mode") != execution_mode
                or r.get("close_reason") != "reconciled_gone"
                or not r.get("strategy_id")):
            continue
        closed_at = r.get("closed_at") or ""
        if closed_at < cutoff:
            continue
        ep = float(r.get("entry_price") or 0)
        if ep <= 0 or abs(ep - entry_price) / ep > 0.005:
            continue
        if best is None or closed_at > (best.get("closed_at") or ""):
            best = r
    if best is not None:
        return best.get("strategy_id"), best.get("opportunity_id")
    return None, None


def adopt_position(exchange_pos: dict, account_mode: str) -> dict:
    """Adopt one external exchange position into the local books under management.

    Builds a synthetic-but-honest causal chain — proposal → order(filled) → position
    — all tagged `source="adopted"`, in a single transaction. This is the deliberate
    amendment to trust §1.1 (see docs/governance/decisions/DECISIONS.md 2026-06-12): an external position
    DOES enter the local table, but only with a complete, truthfully-labelled chain so
    the position→proposal→order links stay non-null and auditable. The synthetic
    proposal/order assert "Aletheia adopted a pre-existing position", NOT "Aletheia
    decided to open this trade" — the `adopted` source and rationale make that explicit.

    The position's `execution_mode` is derived from the account mode (demo →
    bitget_demo, live → bitget_live_limited). That single mapping is what makes
    position_manager auto-manage adopted DEMO positions and SKIP adopted LIVE ones —
    live management stays human-only by construction.

    Entry price and quantity come from the exchange (the truth); no exit/PnL is
    realized here. Reconciliation never places or cancels exchange orders;
    protective-order placement belongs to Gateway-owned execution flows. Returns
    the created ids. Raises ValueError on bad input.
    """
    from aletheia.storage.models import TradeProposal, Order, Position
    from aletheia.storage.repositories import (
        transaction, insert_trade_proposal, insert_order, insert_position, update_position,
    )
    from aletheia.files.jsonl import write_audit

    execution_mode = _execution_mode_for(account_mode)
    if execution_mode is None:
        raise ValueError(f"adopt_position: unknown account_mode {account_mode!r}")

    symbol = to_canonical(exchange_pos.get("symbol", ""))
    direction = _norm_direction(exchange_pos.get("side", ""))
    quantity = float(exchange_pos.get("quantity") or 0)
    entry_price = float(exchange_pos.get("entry_price") or 0)
    if not symbol or direction not in ("long", "short"):
        raise ValueError(f"adopt_position: bad symbol/direction ({symbol!r}, {direction!r})")
    if quantity <= 0 or entry_price <= 0:
        # Never synthesize a chain off a missing size/entry — that would fabricate
        # the very causal facts this design exists to keep honest.
        raise ValueError(
            f"adopt_position: refusing to adopt {symbol} {direction} with "
            f"quantity={quantity} entry_price={entry_price} (need both > 0)"
        )

    market_type = _normalize_market_type(exchange_pos.get("market_type"))
    if market_type is None:
        raise _adoption_error(
            field="market_type",
            symbol=symbol,
            market_type=str(exchange_pos.get("market_type") or "unknown"),
            direction=direction,
        )
    leverage, leverage_source = _validated_adoption_leverage(
        exchange_pos,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
    )
    reconciliation_identity = _reconciliation_identity(
        exchange_pos,
        account_mode=account_mode,
        execution_mode=execution_mode,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
    )

    # Attribution recovery (P2-2): if this external matches a position we just
    # thrash-closed (reconciled_gone), inherit its strategy_id/opportunity_id so a
    # transient orphan→re-adopt keeps trade attribution intact. Tight match only
    # (see _recover_strategy_link) — a genuinely-new external stays unattributed.
    recovered_strategy_id, recovered_opp_id = _recover_strategy_link(
        symbol,
        market_type,
        direction,
        entry_price,
        execution_mode,
    )
    now = datetime.now(timezone.utc).isoformat()
    # Prefer exchange-provided open time for accurate max_hold calculation
    ctime_ms = exchange_pos.get("cTime") or exchange_pos.get("created_at")
    if ctime_ms:
        try:
            opened_at = datetime.fromtimestamp(int(ctime_ms) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            opened_at = now
    else:
        opened_at = now
    rationale = (
        f"Adopted pre-existing {execution_mode} position {symbol} {direction} "
        f"qty={quantity} @ {entry_price} into management (source=adopted). Not a "
        f"newly-decided trade; brought under management at takeover/reconcile."
    )

    proposal = TradeProposal(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=entry_price,
        leverage=leverage,
        execution_mode=execution_mode,
        agent_rationale=rationale,
        status="executed",
        source="adopted",
        # Recovered attribution: keeps the learning signal alive through a
        # transient orphan→re-adopt cycle (#189). None for genuinely-new external.
        strategy_id=recovered_strategy_id,
        opportunity_id=recovered_opp_id,
    )
    order = Order(
        proposal_id=proposal.id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=entry_price,
        execution_mode=execution_mode,
        status="filled",
        source="adopted",
        filled_at=now,
        # position_id backfilled below after Position is constructed (#189).
    )
    # Compute default protective stop and take-profit at adoption time so the
    # position row is never left with NULL stop/tp. The values are derived from
    # the config defaults (same percentages position_manager uses at runtime) but
    # are written once and locked — if the user later changes the defaults the
    # adopted position keeps its original levels, preventing silent SL drift.
    from aletheia.config.loader import load_config as _load_cfg
    _cfg = _load_cfg()
    _sl_pct = _cfg.trading.default_stop_loss_pct   # e.g. 2.0
    _tp_pct = _cfg.trading.default_take_profit_pct  # e.g. 4.0
    _is_long = direction in ("long", "buy")
    _adopted_stop = (
        round(entry_price * (1 - _sl_pct / 100.0), 8) if _is_long
        else round(entry_price * (1 + _sl_pct / 100.0), 8)
    ) if _sl_pct else None
    _adopted_tp = (
        round(entry_price * (1 + _tp_pct / 100.0), 8) if _is_long
        else round(entry_price * (1 - _tp_pct / 100.0), 8)
    ) if _tp_pct else None

    position = Position(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=float(exchange_pos.get("mark_price") or 0) or None,
        unrealized_pnl=float(exchange_pos.get("unrealized_pnl") or 0),
        execution_mode=execution_mode,
        leverage=leverage,
        proposal_id=proposal.id,
        order_id=order.id,
        # Recovered attribution (None for a genuinely-new external — the honest default).
        strategy_id=recovered_strategy_id,
        opportunity_id=recovered_opp_id,
        status="open",
        source="adopted",
        opened_at=opened_at,
        stop_price=_adopted_stop,
        take_profit_price=_adopted_tp,
        # Adopted positions are price-action holdings by definition (the exchange
        # already opened them outside of any Serenity research thesis), so stamp
        # the short_term school explicitly. The Pydantic default would also pick
        # this up, but the explicit assignment keeps intent visible at the call
        # site and satisfies the SDD propagation rule (see spec_serenity_holding
        # SH-S05: ALL FOUR brokers + reconciler must propagate trade_school).
        trade_school="short_term",
    )
    reconciliation_identity["position_id"] = position.id
    position.reconciliation_identity = json.dumps(
        reconciliation_identity,
        sort_keys=True,
        separators=(",", ":"),
    )
    position.reconciliation_evidence = json.dumps(
        {
            "leverage": {
                "source": leverage_source,
                "status": "verified",
                "value": leverage,
            },
            "projection_source": "external_position_adoption",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    # Backfill position_id on the order now that position.id is known (#189).
    order.position_id = position.id

    with transaction():
        insert_trade_proposal(proposal)
        insert_order(order)
        insert_position(position)
        update_position(
            position.id,
            stop_source="adopted_default_pct",
            exit_plan_version=1,
            expected_horizon_hours=24.0,
        )

    write_audit({
        "actor": "system",
        "source": "position_reconciler",
        "event_type": "position_adopted",
        "entity_type": "position",
        "entity_id": position.id,
        "after": {
            "symbol": symbol, "direction": direction, "quantity": quantity,
            "entry_price": entry_price, "execution_mode": execution_mode,
            "market_type": market_type, "leverage": leverage,
            "leverage_source": leverage_source,
            "account_fingerprint": reconciliation_identity["account_fingerprint"],
            "source": "adopted", "proposal_id": proposal.id, "order_id": order.id,
            "stop_price": _adopted_stop, "take_profit_price": _adopted_tp,
            "stop_source": "adopted_default_pct",
            # P1-2 (2026-06-29, OpenAlice TaG borrow): chain the reconcile event
            # into the proposal hash chain. proposal.commit_hash + parent_hash
            # were filled in-place by insert_trade_proposal() inside the
            # transaction block above, so they're available verbatim here. See
            # PL-ADOPT-09 and
            # docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md.
            "commit_hash": proposal.commit_hash,
            "parent_hash": proposal.parent_hash,
        },
        "message": rationale,
    })
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            title=f"Position Adopted: {symbol}",
            message=f"{direction} qty={quantity} @ {entry_price:.4f} ({execution_mode})",
            level="info",
            event_type="trade_notification",
            entity_type="position",
            entity_id=position.id,
            error_log=False,
        )
    except Exception:
        pass

    return {
        "position_id": position.id,
        "proposal_id": proposal.id,
        "order_id": order.id,
        "execution_mode": execution_mode,
        "symbol": symbol,
        "direction": direction,
    }


def run_reconcile(mode: str | None = None) -> dict:
    """Classify, record disappearance evidence, and optionally adopt demo externals.

    Behavior depends on the exchange mode:
      - demo: auto-adopt externals (if enabled); missing locals become durable
        candidates but are not closed before verified-exit/accounting phases land.
      - live: classify only + alert (never autonomous adoption or close).

    Returns a summary dict suitable for agent-run outputs / API response.
    """
    from aletheia.config.loader import load_config
    from aletheia.files.jsonl import write_audit

    cfg = load_config()
    account_mode = mode or cfg.exchange.mode

    result = classify_positions(account_mode)
    summary: dict = {
        "account_mode": account_mode,
        "sync_status": result.sync_status,
        "counts": {
            "managed": len(result.managed),
            "external": len(result.external),
            "orphaned": len(result.orphaned),
            "degraded": len(result.integrity_issues),
        },
        "integrity_issues": [issue.to_dict() for issue in result.integrity_issues],
        "degraded_positions": [],
        "leverage_repairs": [],
        "adopted": [],
        "closed_orphans": [],
        "deferred_orphans": [],
        "disappearance": {
            "created": 0, "confirmed": 0, "observations": [], "resolved": [],
        },
        "settlement": [],
        "errors": [],
    }

    # Persisted circuit breaker: a systemic administrative-close → reopen pattern
    # creates one durable incident.  A clean poll records recovery evidence but
    # never self-clears the incident; explicit resolution is required after the
    # spaced clean-observation gate.  Classification above stays read-only, while
    # demo adoption/orphan-close below remain suspended for the whole incident.
    thrash = detect_reconcile_thrash()
    if not thrash.get("detector_available", True):
        summary["recovery_state"] = "detector_unavailable"
        summary["errors"].append("reconciliation integrity detector unavailable")
        if account_mode == "demo":
            summary["suspended"] = True
            return summary
    incident = None
    incident_created = False
    recovery_state = None
    try:
        from aletheia.portfolio.reconciliation_integrity import (
            clean_observation_gate,
            get_active_reconciliation_integrity_incident,
            record_reconciliation_clean_observation,
            record_reconciliation_integrity_incident,
        )
        if thrash["is_thrashing"]:
            incident, incident_created = record_reconciliation_integrity_incident(thrash)
            recovery_state = "churn_present"
        else:
            incident = get_active_reconciliation_integrity_incident()
            if incident is not None:
                incident = record_reconciliation_clean_observation(incident["id"])
                gate = clean_observation_gate(incident)
                recovery_state = "clean_gate_met" if gate["met"] else "observing_clean"
    except Exception as exc:
        # Failing to establish durable circuit-breaker state must not permit demo
        # adoption/close actions.  Surface the storage failure and fail closed.
        logger.exception("Reconcile integrity incident state unavailable")
        if account_mode == "demo":
            summary["suspended"] = True
            summary["recovery_state"] = "incident_state_unavailable"
            summary["errors"].append(f"integrity incident state: {exc}")
            return summary

    if incident is not None:
        summary["incident"] = incident
        summary["thrash"] = thrash
        summary["recovery_state"] = recovery_state
        if account_mode == "demo":
            summary["suspended"] = True

    if thrash["is_thrashing"]:
        logger.warning(
            "Reconcile integrity incident active: churned=%s (administrative=%d, "
            "reopened=%d, sources=%s). Demo orphan-close + auto-adopt suspended.",
            thrash["churned_symbols"], thrash["administrative_close_recent"],
            thrash["reopened_recent"], thrash["reopened_by_source"],
        )
        if incident_created:
            write_audit({
                "actor": "system", "source": "position_reconciler",
                "event_type": "reconcile_integrity_incident_opened",
                "entity_type": "reconciliation_integrity_incident",
                "entity_id": incident["id"],
                "message": (
                    f"Persisted reconcile-integrity incident after "
                    f"{thrash['churn_count']} scoped close/reopen identities"
                ),
                "after": {
                    "churned_identities": thrash["churned_identities"],
                    "reopened_by_source": thrash["reopened_by_source"],
                },
            })
            try:
                from aletheia.notifications import notify_operator
                notify_operator(
                    title="Reconcile integrity incident: position churn detected",
                    message=(f"{thrash['churn_count']} scoped positions closed then "
                             f"reopened ({', '.join(thrash['churned_symbols'][:5])}). "
                             f"Demo reconcile actions and behavior-changing Learning "
                             f"remain frozen until clean evidence and explicit resolution."),
                    level="warning", event_type="self_heal", entity_type="portfolio",
                    error_log=False,
                )
            except Exception:
                pass

    if incident is None and account_mode == "demo":
        repairs, leverage_degraded = _reconcile_managed_leverage(
            result,
            account_mode=account_mode,
        )
        summary["leverage_repairs"] = repairs
        summary["degraded_positions"].extend(leverage_degraded)

    if result.integrity_issues or summary["degraded_positions"]:
        logger.warning(
            "Reconcile degraded %d identity rows and %d position projections",
            len(result.integrity_issues),
            len(summary["degraded_positions"]),
        )
        try:
            from aletheia.notifications import notify_operator
            notify_operator(
                title="Position reconciliation degraded",
                message=(
                    f"identity_issues={len(result.integrity_issues)} "
                    f"position_projection_issues={len(summary['degraded_positions'])}"
                ),
                level="warning",
                event_type="self_heal",
                entity_type="portfolio",
                error_log=False,
            )
        except Exception:
            pass

    # Evidence collection is state bookkeeping, not a destructive reconcile action.
    # A complete view miss creates/advances a durable candidate; a verified exchange
    # reappearance resolves it. Even a confirmed candidate is only handed to the
    # later verified-exit phase — T05 never calls the fallback-price close path.
    try:
        from aletheia.portfolio.disappearance_confirmation import (
            process_disappearance_evidence,
        )
        summary["disappearance"] = process_disappearance_evidence(
            orphaned=result.orphaned,
            managed=result.managed,
            view_evidence=result.view_evidence,
        )
        summary["deferred_orphans"] = [
            row["position_id"]
            for row in summary["disappearance"]["observations"]
            if row["ready_for_verified_exit"]
        ]
    except Exception as exc:
        logger.exception("Reconcile disappearance evidence unavailable")
        summary["errors"].append(f"disappearance evidence: {exc}")
        if account_mode == "demo":
            summary["suspended"] = True
            summary["recovery_state"] = "disappearance_evidence_unavailable"
            return summary

    ready_candidate_ids = [
        row["candidate_id"]
        for row in summary["disappearance"]["observations"]
        if row["ready_for_verified_exit"]
    ]
    # Verified settlement is a read-only exchange query followed by local state
    # convergence. It never adopts an external live Position or sends an order,
    # and therefore applies to both demo and live accounts once independent
    # disappearance evidence is ready. An active churn incident still suspends
    # all automatic convergence to avoid close/reopen thrash.
    if ready_candidate_ids and incident is None:
        try:
            from aletheia.portfolio.disappearance_settlement import (
                settle_ready_disappearance_candidates,
            )
            summary["settlement"] = settle_ready_disappearance_candidates(
                ready_candidate_ids
            )
        except Exception as exc:
            logger.exception("Verified disappearance settlement unavailable")
            summary["errors"].append(f"verified settlement: {exc}")
            summary["suspended"] = True
            summary["recovery_state"] = "verified_settlement_unavailable"
            return summary

    if account_mode != "demo":
        if result.external or result.orphaned:
            logger.warning(
                "Live reconcile: %d external, %d orphaned — no adoption or "
                "exchange mutation; ready disappearances use verified local settlement",
                len(result.external), len(result.orphaned),
            )
        return summary

    if incident is not None:
        return summary

    # Demo mode: auto-adopt only. Missing-local evidence is handled above.
    if cfg.trading.auto_adopt_enabled:
        for item in result.external:
            try:
                adopted = adopt_position(item.exchange, account_mode)
                summary["adopted"].append(adopted)
            except PositionAdoptionIntegrityError as exc:
                degraded = {
                    "code": exc.code,
                    "field_category": exc.field,
                    "position_id": "not_created",
                    **exc.identity,
                }
                summary["degraded_positions"].append(degraded)
                logger.warning("Reconcile adoption degraded: %s", exc)
                try:
                    from aletheia.notifications import notify_operator
                    notify_operator(
                        title="External position not adopted",
                        message=(
                            f"{exc.identity['symbol']} {exc.identity['market_type']} "
                            f"{exc.identity['direction']}: unavailable {exc.field}"
                        ),
                        level="warning",
                        event_type="self_heal",
                        entity_type="portfolio",
                        error_log=False,
                    )
                except Exception:
                    pass
            except Exception as e:
                err = f"adopt {item.symbol} {item.direction}: {e}"
                logger.warning("Reconcile adopt failed: %s", err)
                summary["errors"].append(err)

    # Do not close candidates here. T06 must recover broker-neutral exit evidence,
    # and T07 must split verified strategy settlement from administrative state
    # convergence. Calling reconcile_close_orphan here would fabricate economics.

    if summary["adopted"] or summary["closed_orphans"]:
        write_audit({
            "actor": "system",
            "source": "position_reconciler",
            "event_type": "reconcile_cycle",
            "entity_type": "portfolio",
            "message": (
                f"Reconcile: adopted={len(summary['adopted'])}, "
                f"closed_orphans={len(summary['closed_orphans'])}, "
                f"errors={len(summary['errors'])}"
            ),
            "after": {
                "adopted_ids": [a["position_id"] for a in summary["adopted"]],
                "closed_ids": [c["position_id"] for c in summary["closed_orphans"]],
            },
        })

    return summary
