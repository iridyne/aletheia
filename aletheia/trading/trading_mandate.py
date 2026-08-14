"""Observe-only Trading Mandate contract and deterministic fit assessment.

A Trading Mandate is provenance, not a decision policy.  This module is pure
apart from configuration resolution: fit assessment never reads the database,
market data, a model, or a network service, and its result has no authority to
change a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

TRADING_MANDATE_VERSION = "trading_mandate/v1"
TRADING_MANDATE_FIT_VERSION = "trading_mandate_fit/v1"
MANDATE_ENFORCEMENT_MODE = "observe_only"

_SUPPORTED_ASSET_CLASSES = frozenset({"crypto", "equity", "prediction"})
_PHYSICAL_MARKET_ALIASES = {
    "spot": "spot",
    "perp": "perp",
    "perpetual": "perp",
    "swap": "perp",
    "usdt_perpetual": "perp",
    "future": "perp",
    "futures": "perp",
    "usdt_futures": "perp",
}
_SUPPORTED_PHYSICAL_MARKETS = frozenset({"spot", "perp"})
_SUPPORTED_DIRECTIONS = frozenset({"long", "short"})


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        return dict(result) if isinstance(result, Mapping) else {}
    try:
        result = vars(value)
    except TypeError:
        return {}
    return dict(result) if isinstance(result, Mapping) else {}


def _normalised_collection(
    value: object,
    *,
    field_name: str,
    normalise,
    required: bool = False,
) -> tuple[str, ...] | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} must be a non-empty collection")
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (Sequence, set, frozenset)):
        raise TypeError(f"{field_name} must be a collection")
    values: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{field_name} values must be strings")
        normalized = normalise(item)
        if not normalized:
            raise ValueError(f"{field_name} values must be non-empty")
        values.add(normalized)
    if not values:
        raise ValueError(f"{field_name} must not be empty when declared")
    return tuple(sorted(values))


def _text(value: str) -> str:
    return value.strip()


def _lower_text(value: str) -> str:
    return value.strip().lower()


def normalize_physical_market_type(value: object) -> str | None:
    """Normalize Proposal/config aliases to the contract's physical market vocabulary."""
    if value is None:
        return None
    return _PHYSICAL_MARKET_ALIASES.get(str(value).strip().lower())


def normalize_symbol(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


class _MandateModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class MarketScope(_MandateModel):
    allowed_asset_classes: tuple[str, ...] = Field(min_length=1)
    allowed_physical_market_types: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "allowed_physical_market_types", "allowed_market_types"
        ),
    )
    allowed_venues: tuple[str, ...] | None = None
    allowed_symbols: tuple[str, ...] | None = None

    @field_validator("allowed_asset_classes", mode="before")
    @classmethod
    def _asset_classes(cls, value: object) -> tuple[str, ...]:
        result = _normalised_collection(
            value,
            field_name="allowed_asset_classes",
            normalise=_lower_text,
            required=True,
        )
        assert result is not None
        unsupported = sorted(set(result) - _SUPPORTED_ASSET_CLASSES)
        if unsupported:
            raise ValueError(f"unsupported asset class: {unsupported[0]}")
        return result

    @field_validator("allowed_physical_market_types", mode="before")
    @classmethod
    def _physical_markets(cls, value: object) -> tuple[str, ...]:
        def normalize(value: str) -> str:
            return _PHYSICAL_MARKET_ALIASES.get(value.strip().lower(), "")

        result = _normalised_collection(
            value,
            field_name="allowed_physical_market_types",
            normalise=normalize,
            required=True,
        )
        assert result is not None
        unsupported = sorted(set(result) - _SUPPORTED_PHYSICAL_MARKETS)
        if unsupported:
            raise ValueError(f"unsupported physical market type: {unsupported[0]}")
        return result

    @field_validator("allowed_venues", mode="before")
    @classmethod
    def _venues(cls, value: object) -> tuple[str, ...] | None:
        return _normalised_collection(
            value,
            field_name="allowed_venues",
            normalise=_lower_text,
        )

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def _symbols(cls, value: object) -> tuple[str, ...] | None:
        return _normalised_collection(
            value,
            field_name="allowed_symbols",
            normalise=lambda item: item.strip().upper(),
        )


class EdgeScope(_MandateModel):
    allowed_candidate_families: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "allowed_candidate_families", "allowed_families"
        ),
    )
    allowed_directions: tuple[str, ...] | None = None
    min_intended_holding_horizon_hours: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices(
            "min_intended_holding_horizon_hours", "min_horizon_hours"
        ),
    )
    max_intended_holding_horizon_hours: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices(
            "max_intended_holding_horizon_hours", "max_horizon_hours"
        ),
    )

    @field_validator("allowed_candidate_families", mode="before")
    @classmethod
    def _families(cls, value: object) -> tuple[str, ...] | None:
        return _normalised_collection(
            value,
            field_name="allowed_candidate_families",
            normalise=_lower_text,
        )

    @field_validator("allowed_directions", mode="before")
    @classmethod
    def _directions(cls, value: object) -> tuple[str, ...] | None:
        result = _normalised_collection(
            value,
            field_name="allowed_directions",
            normalise=_lower_text,
        )
        if result is not None:
            unsupported = sorted(set(result) - _SUPPORTED_DIRECTIONS)
            if unsupported:
                raise ValueError(f"unsupported direction: {unsupported[0]}")
        return result

    @field_validator(
        "min_intended_holding_horizon_hours",
        "max_intended_holding_horizon_hours",
        mode="before",
    )
    @classmethod
    def _finite_horizon(cls, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("holding horizon must be a finite number")
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("holding horizon must be finite and positive")
        return number

    @model_validator(mode="after")
    def _horizon_order(self) -> "EdgeScope":
        if (
            self.min_intended_holding_horizon_hours is not None
            and self.max_intended_holding_horizon_hours is not None
            and self.min_intended_holding_horizon_hours
            > self.max_intended_holding_horizon_hours
        ):
            raise ValueError("minimum intended holding horizon exceeds maximum")
        return self


class DefaultBehavior(_MandateModel):
    insufficient_evidence: Literal["prefer_skip", "not_declared"] = "not_declared"
    conflicting_directional_evidence: Literal[
        "prefer_skip", "not_declared"
    ] = "not_declared"
    unavailable_cost_estimate: Literal["prefer_skip", "not_declared"] = "not_declared"


class HoldingGovernance(_MandateModel):
    expected_horizon_reached: Literal["review_required", "not_declared"] = "not_declared"
    opening_hypothesis_invalidated: Literal[
        "review_required", "not_declared"
    ] = "not_declared"


class TradingMandate(_MandateModel):
    contract_version: Literal[TRADING_MANDATE_VERSION]
    mandate_id: str = Field(min_length=1)
    mandate_version: StrictInt = Field(ge=1)
    status: Literal["active"]
    enforcement_mode: Literal[MANDATE_ENFORCEMENT_MODE]
    market_scope: MarketScope
    edge_scope: EdgeScope
    default_behavior: DefaultBehavior = Field(default_factory=DefaultBehavior)
    holding_governance: HoldingGovernance = Field(default_factory=HoldingGovernance)

    @field_validator("mandate_id", mode="before")
    @classmethod
    def _mandate_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("mandate_id must be non-empty")
        return value.strip()

    def canonical_payload(self) -> dict[str, Any]:
        """Return the complete normalized payload covered by the content hash."""
        return self.model_dump(mode="json", by_alias=False)

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload())

    def content_hash(self) -> str:
        return f"sha256:{hashlib.sha256(self.canonical_json().encode('utf-8')).hexdigest()}"

    def identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "mandate_id": self.mandate_id,
            "mandate_version": self.mandate_version,
            "content_hash": self.content_hash(),
            "enforcement_mode": self.enforcement_mode,
        }


class TradingMandateConfig(BaseModel):
    """One optional config object.

    Raw fields are retained here so a malformed enabled object can be reported as
    ``mandate_unavailable`` instead of preventing the rest of the runtime from
    loading.  ``TradingMandate`` performs the strict validation at the consumer
    boundary.  Extra fields are then rejected by that strict model.
    """

    model_config = ConfigDict(extra="allow")

    enabled: Any = False
    raw_value: Any | None = Field(default=None, exclude=True, repr=False)
    contract_version: Any = None
    mandate_id: Any = None
    mandate_version: Any = None
    status: Any = None
    enforcement_mode: Any = None
    market_scope: Any = None
    edge_scope: Any = None
    default_behavior: Any = None
    holding_governance: Any = None


@dataclass(frozen=True)
class MandateResolution:
    status: Literal["active", "not_configured", "mandate_unavailable"]
    mandate: TradingMandate | None = None
    reason_codes: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()

    @property
    def identity(self) -> dict[str, Any] | None:
        return self.mandate.identity() if self.mandate is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "identity": self.identity,
            "reason_codes": list(self.reason_codes),
            "validation_errors": list(self.validation_errors),
        }


def _config_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, TradingMandateConfig):
        if value.raw_value is not None:
            return None
        return value.model_dump(exclude={"raw_value"})
    if isinstance(value, Mapping):
        return dict(value)
    config_value = getattr(value, "trading_mandate", None)
    if config_value is not None:
        return _config_mapping(config_value)
    return None


def resolve_trading_mandate(config_or_value: object) -> MandateResolution:
    """Resolve the one configured mandate without inventing defaults."""
    if isinstance(config_or_value, TradingMandate):
        return MandateResolution("active", mandate=config_or_value)
    if isinstance(config_or_value, MandateResolution):
        return config_or_value
    value = (
        getattr(config_or_value, "trading_mandate", None)
        if not isinstance(config_or_value, (Mapping, TradingMandateConfig))
        else config_or_value
    )
    if value is None:
        return MandateResolution("not_configured", reason_codes=("not_configured",))
    if isinstance(value, TradingMandateConfig) and not value.enabled:
        return MandateResolution("not_configured", reason_codes=("not_configured",))
    raw = _config_mapping(value)
    if raw is None:
        return MandateResolution(
            "mandate_unavailable", reason_codes=("mandate_unavailable",)
        )
    if raw.get("enabled") is False:
        return MandateResolution("not_configured", reason_codes=("not_configured",))
    if raw.get("enabled") is not True:
        return MandateResolution(
            "mandate_unavailable", reason_codes=("mandate_unavailable",)
        )
    raw.pop("enabled", None)
    # TradingMandateConfig is intentionally permissive so malformed input can be
    # reported. Its absent declared fields appear as None in model_dump(); omit
    # only those known placeholders so an unknown ``typo: null`` remains invalid.
    known_fields = {
        "contract_version",
        "mandate_id",
        "mandate_version",
        "status",
        "enforcement_mode",
        "market_scope",
        "edge_scope",
        "default_behavior",
        "holding_governance",
    }
    raw = {
        key: value
        for key, value in raw.items()
        if value is not None or key not in known_fields
    }
    try:
        mandate = TradingMandate.model_validate(raw)
    except ValidationError as exc:
        fields = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ())) or "mandate"
            fields.append(location)
        return MandateResolution(
            "mandate_unavailable",
            reason_codes=("mandate_unavailable",),
            validation_errors=tuple(sorted(set(fields))),
        )
    return MandateResolution("active", mandate=mandate)


# Compatibility spelling for callers that describe activation as configuration.
get_configured_trading_mandate = resolve_trading_mandate
load_trading_mandate = resolve_trading_mandate


def canonical_mandate_payload(mandate: TradingMandate) -> dict[str, Any]:
    return mandate.canonical_payload()


def canonical_mandate_json(mandate: TradingMandate) -> str:
    return mandate.canonical_json()


def compute_mandate_content_hash(mandate: TradingMandate) -> str:
    return mandate.content_hash()


def mandate_identity(mandate: TradingMandate) -> dict[str, Any]:
    return mandate.identity()


def _fact(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _proposal_snapshot(proposal: Mapping[str, Any]) -> dict[str, Any]:
    raw = proposal.get("attribution_snapshot")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _proposal_venue(
    proposal: Mapping[str, Any], opportunity: Mapping[str, Any] | None = None
) -> str | None:
    direct = _fact(proposal, "venue", "exchange") or _fact(
        opportunity or {}, "venue", "exchange"
    )
    if direct:
        return _lower_text(str(direct))
    snapshot = _proposal_snapshot(proposal)
    context = snapshot.get("market_context")
    instrument = context.get("instrument") if isinstance(context, Mapping) else None
    if isinstance(instrument, Mapping):
        value = instrument.get("exchange") or instrument.get("venue")
        if value:
            return _lower_text(str(value))
    return None


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _source_identities(
    opportunity: Mapping[str, Any],
    decision: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "opportunity_id": _fact(opportunity, "id") or _fact(proposal, "opportunity_id"),
        "decision_id": _fact(decision, "id"),
        "proposal_id": _fact(proposal, "id"),
    }


def _check(
    dimension: str,
    status: str,
    observed: Any,
    declared: Any,
    reason: str,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "status": status,
        "observed": observed,
        "declared": declared,
        "reason": reason,
    }


def _unavailable_fit(
    status: Literal["not_configured", "mandate_unavailable"],
    *,
    identity: dict[str, Any] | None = None,
    opportunity: Mapping[str, Any] | None = None,
    decision: Mapping[str, Any] | None = None,
    proposal: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    opp = opportunity or {}
    dec = decision or {}
    prop = proposal or {}
    return {
        "contract_version": TRADING_MANDATE_FIT_VERSION,
        "status": status,
        "mandate_identity": identity,
        "checks": [],
        "reasons": [reason or status],
        "source_identities": _source_identities(opp, dec, prop),
        "declared_policy": None,
        "behavior_changed": False,
    }


def _coerce_fit_inputs(
    opportunity: object | None,
    decision: object | None,
    proposal: object | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return _mapping(opportunity), _mapping(decision), _mapping(proposal)


def assess_trading_mandate_fit(
    *,
    mandate: TradingMandate | MandateResolution | Mapping[str, Any] | None,
    opportunity: object | None,
    decision: object | None,
    proposal: object | None,
    mandate_status: Literal["not_configured", "mandate_unavailable"] | None = None,
) -> dict[str, Any]:
    """Assess exact persisted facts against one mandate without side effects.

    The function deliberately accepts mappings/records rather than fetching them.
    Callers must provide the exact Opportunity, Decision, and Proposal ancestry;
    absent facts are never reconstructed from timestamps or approximate symbols.
    """
    opp, dec, prop = _coerce_fit_inputs(opportunity, decision, proposal)
    identity: dict[str, Any] | None = None
    if isinstance(mandate, MandateResolution):
        if mandate.status != "active" or mandate.mandate is None:
            return _unavailable_fit(
                mandate.status,
                identity=mandate.identity,
                opportunity=opp,
                decision=dec,
                proposal=prop,
            )
        mandate = mandate.mandate
    elif mandate_status is not None:
        return _unavailable_fit(
            mandate_status,
            opportunity=opp,
            decision=dec,
            proposal=prop,
        )
    elif mandate is None:
        return _unavailable_fit(
            "not_configured",
            opportunity=opp,
            decision=dec,
            proposal=prop,
        )
    elif isinstance(mandate, TradingMandateConfig):
        resolution = resolve_trading_mandate(mandate)
        if resolution.status != "active" or resolution.mandate is None:
            return _unavailable_fit(
                resolution.status,
                identity=resolution.identity,
                opportunity=opp,
                decision=dec,
                proposal=prop,
            )
        mandate = resolution.mandate
    elif not isinstance(mandate, TradingMandate):
        try:
            mandate = TradingMandate.model_validate(mandate)
        except ValidationError:
            return _unavailable_fit(
                "mandate_unavailable",
                opportunity=opp,
                decision=dec,
                proposal=prop,
            )
    identity = mandate.identity()

    checks: list[dict[str, Any]] = []
    reasons: list[str] = []

    asset_class = _fact(opp, "asset_class")
    asset_class = _lower_text(str(asset_class)) if asset_class is not None else None
    if asset_class is None:
        checks.append(
            _check(
                "asset_class",
                "not_assessable",
                None,
                list(mandate.market_scope.allowed_asset_classes),
                "asset_class_unavailable",
            )
        )
        reasons.append("asset_class_unavailable")
    elif asset_class in mandate.market_scope.allowed_asset_classes:
        checks.append(
            _check(
                "asset_class",
                "matched",
                asset_class,
                list(mandate.market_scope.allowed_asset_classes),
                "asset_class_allowed",
            )
        )
    else:
        checks.append(
            _check(
                "asset_class",
                "mismatch",
                asset_class,
                list(mandate.market_scope.allowed_asset_classes),
                "asset_class_disallowed",
            )
        )
        reasons.append("asset_class_disallowed")

    raw_market = _fact(prop, "market_type") or _fact(dec, "market_type")
    market_type = normalize_physical_market_type(raw_market)
    if market_type is None:
        checks.append(
            _check(
                "market_type",
                "not_assessable",
                None,
                list(mandate.market_scope.allowed_physical_market_types),
                "market_type_unavailable",
            )
        )
        reasons.append("market_type_unavailable")
    elif market_type in mandate.market_scope.allowed_physical_market_types:
        checks.append(
            _check(
                "market_type",
                "matched",
                market_type,
                list(mandate.market_scope.allowed_physical_market_types),
                "market_type_allowed",
            )
        )
    else:
        checks.append(
            _check(
                "market_type",
                "mismatch",
                market_type,
                list(mandate.market_scope.allowed_physical_market_types),
                "market_type_disallowed",
            )
        )
        reasons.append("market_type_disallowed")

    optional_declarations = (
        (
            "venue",
            mandate.market_scope.allowed_venues,
            _proposal_venue(prop, opp),
            "venue_allowed",
            "venue_disallowed",
            "venue_unavailable",
        ),
        (
            "symbol",
            mandate.market_scope.allowed_symbols,
            normalize_symbol(_fact(prop, "symbol")),
            "symbol_allowed",
            "symbol_disallowed",
            "symbol_unavailable",
        ),
        (
            "candidate_family",
            mandate.edge_scope.allowed_candidate_families,
            (
                _lower_text(str(_fact(opp, "scanner_family")))
                if _fact(opp, "scanner_family") is not None
                else None
            ),
            "candidate_family_allowed",
            "candidate_family_disallowed",
            "candidate_family_unavailable",
        ),
        (
            "direction",
            mandate.edge_scope.allowed_directions,
            (
                _lower_text(str(_fact(prop, "direction") or _fact(dec, "direction")))
                if (_fact(prop, "direction") or _fact(dec, "direction")) is not None
                else None
            ),
            "direction_allowed",
            "direction_disallowed",
            "direction_unavailable",
        ),
    )
    for dimension, declared, observed, matched_reason, mismatch_reason, missing_reason in optional_declarations:
        if declared is None:
            checks.append(_check(dimension, "not_declared", observed, None, f"{dimension}_not_declared"))
        elif observed is None:
            checks.append(
                _check(dimension, "not_assessable", None, list(declared), missing_reason)
            )
            reasons.append(missing_reason)
        elif observed in declared:
            checks.append(
                _check(dimension, "matched", observed, list(declared), matched_reason)
            )
        else:
            checks.append(
                _check(dimension, "mismatch", observed, list(declared), mismatch_reason)
            )
            reasons.append(mismatch_reason)

    min_horizon = mandate.edge_scope.min_intended_holding_horizon_hours
    max_horizon = mandate.edge_scope.max_intended_holding_horizon_hours
    horizon_declared = (
        {"min_hours": min_horizon, "max_hours": max_horizon}
        if min_horizon is not None or max_horizon is not None
        else None
    )
    raw_horizon = _fact(prop, "expected_horizon_hours")
    if raw_horizon is None:
        raw_horizon = _fact(dec, "expected_horizon_hours")
    observed_horizon = _finite(raw_horizon)
    if horizon_declared is None:
        checks.append(
            _check("holding_horizon", "not_declared", observed_horizon, None, "holding_horizon_not_declared")
        )
    elif observed_horizon is None or observed_horizon <= 0:
        checks.append(
            _check("holding_horizon", "not_assessable", None, horizon_declared, "holding_horizon_unavailable")
        )
        reasons.append("holding_horizon_unavailable")
    elif (
        (min_horizon is not None and observed_horizon < min_horizon)
        or (max_horizon is not None and observed_horizon > max_horizon)
    ):
        checks.append(
            _check("holding_horizon", "mismatch", observed_horizon, horizon_declared, "holding_horizon_incompatible")
        )
        reasons.append("holding_horizon_incompatible")
    else:
        checks.append(
            _check("holding_horizon", "matched", observed_horizon, horizon_declared, "holding_horizon_compatible")
        )

    statuses = [check["status"] for check in checks]
    if "mismatch" in statuses:
        status = "out_of_scope"
    elif "not_assessable" in statuses:
        status = "not_assessable"
    else:
        status = "in_scope"
    return {
        "contract_version": TRADING_MANDATE_FIT_VERSION,
        "status": status,
        "mandate_identity": identity,
        "checks": checks,
        "reasons": list(dict.fromkeys(reasons)),
        "source_identities": _source_identities(opp, dec, prop),
        "declared_policy": {
            "default_behavior": mandate.default_behavior.model_dump(mode="json"),
            "holding_governance": mandate.holding_governance.model_dump(mode="json"),
        },
        "behavior_changed": False,
    }


def build_trading_mandate_provenance(
    *,
    configured: MandateResolution | TradingMandate | Mapping[str, Any] | None,
    opportunity: object | None,
    decision: object | None,
    proposal: object | None,
) -> dict[str, Any]:
    """Build the exact evidence pair frozen into a new native Paper Proposal."""
    if isinstance(configured, MandateResolution):
        resolution = configured
    elif isinstance(configured, TradingMandate):
        resolution = MandateResolution("active", mandate=configured)
    elif configured is None:
        resolution = MandateResolution("not_configured", reason_codes=("not_configured",))
    else:
        resolution = resolve_trading_mandate(configured)
    if resolution.status == "active" and resolution.mandate is not None:
        fit = assess_trading_mandate_fit(
            mandate=resolution.mandate,
            opportunity=opportunity,
            decision=decision,
            proposal=proposal,
        )
    else:
        fit = assess_trading_mandate_fit(
            mandate=None,
            opportunity=opportunity,
            decision=decision,
            proposal=proposal,
            mandate_status=resolution.status,
        )
        fit["reasons"] = list(resolution.reason_codes) or [resolution.status]
        fit["validation_errors"] = list(resolution.validation_errors)
    return {
        "trading_mandate_identity": resolution.identity,
        "trading_mandate_fit": fit,
    }


def validate_frozen_trading_mandate_provenance(snapshot: Mapping[str, Any]) -> None:
    """Validate an already-frozen pair without consulting current configuration."""
    has_identity = "trading_mandate_identity" in snapshot
    has_fit = "trading_mandate_fit" in snapshot
    if has_identity != has_fit:
        raise ValueError("trading mandate provenance must freeze identity and fit together")
    if not has_identity:
        return
    identity = snapshot.get("trading_mandate_identity")
    fit = snapshot.get("trading_mandate_fit")
    if identity is not None and not isinstance(identity, Mapping):
        raise ValueError("trading mandate identity is malformed")
    if not isinstance(fit, Mapping):
        raise ValueError("trading mandate fit is malformed")
    if fit.get("contract_version") != TRADING_MANDATE_FIT_VERSION:
        raise ValueError("trading mandate fit contract is invalid or unsupported")
    if fit.get("behavior_changed") is not False:
        raise ValueError("trading mandate fit cannot claim behavior changed")
    if fit.get("status") not in {
        "in_scope",
        "out_of_scope",
        "not_assessable",
        "not_configured",
        "mandate_unavailable",
    }:
        raise ValueError("trading mandate fit status is invalid or unsupported")
    identity_required = fit.get("status") in {
        "in_scope",
        "out_of_scope",
        "not_assessable",
    }
    if identity_required and not isinstance(identity, Mapping):
        raise ValueError("available trading mandate fit requires an identity")
    fit_identity = fit.get("mandate_identity")
    if (dict(identity) if isinstance(identity, Mapping) else identity) != (
        dict(fit_identity) if isinstance(fit_identity, Mapping) else fit_identity
    ):
        raise ValueError("trading mandate identity conflicts with frozen fit")


def freeze_trading_mandate_provenance(
    snapshot: dict[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    """Freeze once; equal content is idempotent and conflicting content fails closed."""
    candidate = {
        "trading_mandate_identity": evidence.get("trading_mandate_identity"),
        "trading_mandate_fit": evidence.get("trading_mandate_fit"),
    }
    current_present = any(key in snapshot for key in candidate)
    if current_present:
        validate_frozen_trading_mandate_provenance(snapshot)
        current = {
            "trading_mandate_identity": snapshot.get("trading_mandate_identity"),
            "trading_mandate_fit": snapshot.get("trading_mandate_fit"),
        }
        if _canonical_json(current) != _canonical_json(candidate):
            raise ValueError("trading mandate provenance is immutable")
        return
    validate_frozen_trading_mandate_provenance(candidate)
    snapshot.update(candidate)