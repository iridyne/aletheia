"""Cash ledger and affordability view for the local ``paper_internal`` account."""

from aletheia.storage import repositories as repo
from aletheia.storage.models import PaperAccountEvent

INITIAL_PAPER_CASH_USDT = 200.0


def _open_local_positions() -> list[dict]:
    return [
        position for position in repo.list_all_open_positions()
        if position.get("execution_mode") == "paper_internal"
    ]


def _unsettled_local_closes() -> list[dict]:
    """Closed local positions whose net account result is still unknown."""
    from aletheia.portfolio.pnl import _realized_economics_value
    from aletheia.storage.sqlite import get_db

    return [
        position
        for row in get_db().execute(
            "SELECT * FROM positions "
            "WHERE status='closed' AND execution_mode='paper_internal' "
            "ORDER BY rowid DESC"
        )
        for position in [dict(row)]
        if _realized_economics_value(position) is None
    ]


def _reserved_margin_usdt(positions: list[dict]) -> float:
    total = 0.0
    for position in positions:
        leverage = max(float(position.get("leverage") or 1), 1.0)
        total += float(position["quantity"]) * float(position["entry_price"]) / leverage
    return round(total, 8)


def _write_event(*, action: str, amount_usdt: float, actor: str, source: str,
                 position_id: str | None = None, note: str = "") -> PaperAccountEvent:
    # SQLite is the single serialization authority for the ledger.  This helper
    # may be called from an existing Risk/entry/close transaction; transaction()
    # joins that writer instead of taking a process lock in the opposite order.
    with repo.transaction(immediate=True):
        before = repo.get_paper_account_cash_balance()
        after = round(before + amount_usdt, 8)
        if after < -1e-8:
            raise ValueError("paper account cash balance cannot be negative")
        event = PaperAccountEvent(
            action=action, amount_usdt=round(amount_usdt, 8),
            cash_balance_before_usdt=round(before, 8),
            cash_balance_after_usdt=max(0.0, after), actor=actor, source=source,
            position_id=position_id, note=note,
        )
        repo.insert_paper_account_event(event)
    return event


def ensure_paper_account() -> None:
    """Create exactly one starting-funds event, without reviving deliberate zero cash."""
    if repo.has_paper_account_event():
        return
    with repo.transaction(immediate=True):
        # Recheck after owning SQLite's writer slot so concurrent cold readers
        # cannot both seed the account.
        if not repo.has_paper_account_event():
            _write_event(
                action="initial_funding", amount_usdt=INITIAL_PAPER_CASH_USDT,
                actor="system", source="paper_account_bootstrap",
                note="Initial local paper trading balance",
            )


def get_paper_account(event_limit: int = 20) -> dict:
    ensure_paper_account()
    positions = _open_local_positions()
    cash = repo.get_paper_account_cash_balance()
    reserved = _reserved_margin_usdt(positions)
    unrealized = sum(float(position.get("unrealized_pnl") or 0.0) for position in positions)
    unsettled = _unsettled_local_closes()
    economics_complete = not unsettled
    return {
        "cash_balance_usdt": round(cash, 8),
        "reserved_margin_usdt": reserved,
        # A missing close result makes the spendable account value unknown.
        # Return zero availability so Risk fails closed until the economics are
        # reconciled; keep the ledger cash visible but label its completeness.
        "available_balance_usdt": (
            round(max(0.0, cash - reserved), 8)
            if economics_complete
            else 0.0
        ),
        "equity_usdt": (
            round(cash + unrealized, 8) if economics_complete else None
        ),
        "economics_complete": economics_complete,
        "unsettled_closed_positions_count": len(unsettled),
        "open_positions_count": len(positions),
        "events": repo.list_paper_account_events(limit=event_limit),
    }


def adjust_cash(amount_usdt: float, *, note: str = "", actor: str = "human",
                source: str = "dashboard") -> dict:
    if amount_usdt == 0:
        raise ValueError("paper account adjustment must be non-zero")
    ensure_paper_account()
    action = "manual_credit" if amount_usdt > 0 else "manual_debit"
    _write_event(
        action=action, amount_usdt=amount_usdt, actor=actor,
        source=source, note=note,
    )
    return get_paper_account()


def set_cash_balance(amount_usdt: float, *, note: str = "", actor: str = "human",
                     source: str = "dashboard") -> dict:
    if amount_usdt < 0:
        raise ValueError("paper account cash balance must be non-negative")
    ensure_paper_account()
    with repo.transaction(immediate=True):
        delta = round(amount_usdt - repo.get_paper_account_cash_balance(), 8)
        _write_event(action="manual_set_balance", amount_usdt=delta, actor=actor,
                     source=source, note=note)
    return get_paper_account()


def record_position_opened(position_id: str) -> None:
    ensure_paper_account()
    with repo.transaction(immediate=True):
        if not repo.has_paper_account_position_event("position_opened", position_id):
            _write_event(action="position_opened", amount_usdt=0.0, actor="system",
                         source="paper_broker", position_id=position_id,
                         note="Local paper position opened")


def settle_position_closed(position_id: str, realized_pnl: float) -> None:
    """Record local realized PnL once after the normal close path succeeds."""
    ensure_paper_account()
    with repo.transaction(immediate=True):
        if not repo.has_paper_account_position_event("position_closed", position_id):
            _write_event(action="position_closed", amount_usdt=realized_pnl, actor="system",
                         source="portfolio_pnl", position_id=position_id,
                         note="Local paper position realized PnL")
