"""Single source of truth for execution-vs-market-data exchange alignment."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP


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
    if mode == "paper_internal":
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
        status = "local_paper"
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


def exchange_alignment(cfg) -> ExchangeAlignment:
    return exchange_alignment_for_values(
        paper_execution_mode=cfg.exchange.paper_execution_mode,
        data_exchange=cfg.market_data.ccxt_exchange,
        configured_exchange=cfg.exchange.name,
    )
