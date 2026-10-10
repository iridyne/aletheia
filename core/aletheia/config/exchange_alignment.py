"""Single source of truth for execution-vs-market-data exchange alignment."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, execution_mode_venue


@dataclass(frozen=True)
class ExchangeAlignment:
    status: str
    execution_exchange: str | None
    data_exchange: str
    diverged: bool
    paper_execution_mode: str

    def to_dict(self) -> dict:
        return asdict(self)


def exchange_alignment_for_values(
    *, paper_execution_mode: str, data_exchange: str,
) -> ExchangeAlignment:
    execution = execution_mode_venue(paper_execution_mode)
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
    *, data_exchange: str, paper_execution_mode: str,
) -> str:
    """An explicit ``market_data.ccxt_exchange`` override, else the order venue.

    Empty means "follow the trading account", so switching
    ``exchange.paper_execution_mode`` moves market data with it.
    """
    explicit = str(data_exchange or "").strip().lower()
    if explicit:
        return explicit
    return execution_mode_venue(paper_execution_mode) or "binance"


def market_data_exchange(cfg) -> str:
    """The single resolver every market-data reader uses for its primary venue."""
    return market_data_exchange_for_values(
        data_exchange=cfg.market_data.ccxt_exchange,
        paper_execution_mode=cfg.exchange.paper_execution_mode,
    )


def apply_primary_exchange(cfg, exchange_id: str) -> None:
    """Point execution and market data at one exchange.

    Market data is cleared to "follow the account" rather than copied, so the
    two can no longer drift apart after a later execution-mode change.
    """
    eid = str(exchange_id or "").strip().lower()
    _, paper_mode = EXCHANGE_UNIFICATION_MAP[eid]
    cfg.exchange.paper_execution_mode = paper_mode
    cfg.market_data.ccxt_exchange = ""
    cfg.market_data.ccxt_fallback = ""


def exchange_alignment(cfg) -> ExchangeAlignment:
    return exchange_alignment_for_values(
        paper_execution_mode=cfg.exchange.paper_execution_mode,
        data_exchange=market_data_exchange(cfg),
    )
