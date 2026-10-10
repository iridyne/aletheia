"""Single source of truth for execution-vs-market-data exchange alignment."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, LIVE_EXECUTION_MODES


# Derived from the exchange registry so a new venue cannot be half-registered.
# `binance_live` / `okx_live` used to be listed here: they are UTA *credential
# profile* names (uta/manager.py), not execution modes, and they leaked in from
# the OKX credential UI. `resolve_risk_policy` raises UnknownRiskExecutionMode
# for both, so persisting one as paper_execution_mode rejects every proposal.
#
# ponytail: paper modes only. The real live modes (`*_live_limited`) still
# resolve through the `configured_exchange` fallback below, which is correct
# whenever exchange.name agrees with the mode; derive them here too if that
# assumption ever needs enforcing.
_EXECUTION_MODE_EXCHANGES = {
    paper_mode: ccxt_name
    for ccxt_name, paper_mode in EXCHANGE_UNIFICATION_MAP.values()
}


@dataclass(frozen=True)
class ExchangeAlignment:
    status: str
    execution_exchange: str | None
    data_exchange: str
    diverged: bool
    paper_execution_mode: str

    def to_dict(self) -> dict:
        return asdict(self)


def execution_exchange_for_mode(
    paper_execution_mode: str, *, configured_exchange: str = "",
) -> str | None:
    """Resolve the venue that orders actually target, not a retained preference."""
    mode = str(paper_execution_mode or "").strip().lower()
    if mode not in _EXECUTION_MODE_EXCHANGES and mode not in LIVE_EXECUTION_MODES.values():
        return None
    return _EXECUTION_MODE_EXCHANGES.get(mode) or str(configured_exchange or "").strip().lower() or None


def exchange_alignment_for_values(
    *, paper_execution_mode: str, data_exchange: str, configured_exchange: str = "",
) -> ExchangeAlignment:
    execution = execution_exchange_for_mode(
        paper_execution_mode, configured_exchange=configured_exchange,
    )
    data = str(data_exchange or "").strip().lower()
    if execution is None:
        status = "not_configured" if not paper_execution_mode else "unavailable"
        diverged = False
    else:
        diverged = execution != data
        status = "diverged" if diverged else "aligned"
    return ExchangeAlignment(
        status=status,
        execution_exchange=execution,
        data_exchange=data,
        diverged=diverged,
        paper_execution_mode=str(paper_execution_mode),
    )


def market_data_exchange_for_values(
    *, data_exchange: str, paper_execution_mode: str, configured_exchange: str = "",
) -> str:
    """An explicit ``market_data.ccxt_exchange`` override, else the order venue.

    Empty means "follow the trading account", so switching
    ``exchange.paper_execution_mode`` moves market data with it.
    """
    explicit = str(data_exchange or "").strip().lower()
    if explicit:
        return explicit
    return execution_exchange_for_mode(
        paper_execution_mode, configured_exchange=configured_exchange,
    ) or "binance"


def market_data_exchange(cfg) -> str:
    """The single resolver every market-data reader uses for its primary venue."""
    return market_data_exchange_for_values(
        data_exchange=cfg.market_data.ccxt_exchange,
        paper_execution_mode=cfg.exchange.paper_execution_mode,
        configured_exchange=cfg.exchange.name,
    )


def apply_primary_exchange(cfg, exchange_id: str) -> None:
    """Point execution, market data and the Risk allowlist at one exchange.

    Market data is cleared to "follow the account" rather than copied, so the
    two can no longer drift apart after a later execution-mode change.
    """
    eid = str(exchange_id or "").strip().lower()
    _, paper_mode = EXCHANGE_UNIFICATION_MAP[eid]
    cfg.exchange.name = eid
    cfg.exchange.paper_execution_mode = paper_mode
    cfg.market_data.ccxt_exchange = ""
    cfg.market_data.ccxt_fallback = ""
    cfg.risk.allowed_exchange = eid


def exchange_alignment(cfg) -> ExchangeAlignment:
    return exchange_alignment_for_values(
        paper_execution_mode=cfg.exchange.paper_execution_mode,
        data_exchange=market_data_exchange(cfg),
        configured_exchange=cfg.exchange.name,
    )
