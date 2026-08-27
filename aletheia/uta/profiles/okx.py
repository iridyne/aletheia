"""OKX Demo UTA profile."""

from decimal import Decimal

from aletheia.uta.models import AccountBalance
from aletheia.uta.profiles.base import CCXTProfile, decimal_value


class OKXDemoProfile(CCXTProfile):
    expected_venue = "okx"
    expected_execution_mode = "okx_demo"
    expected_environment = "demo"
    expected_profile = "okx_demo"
    exchange_name = "okx"
    sandbox = True
    open_order_requests = ({"type": "swap"}, {"type": "spot"})
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests
    supported_market_types = ("spot", "usdt_futures")

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            password=passphrase,
            options={"defaultType": "swap"},
        )

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        details = []
        total_equity = None
        unrealized = Decimal(0)
        for raw in balances:
            info = raw.get("info") or {}
            for account in info.get("data", []) if isinstance(info, dict) else []:
                parsed_equity = decimal_value(account.get("totalEq"))
                if parsed_equity is not None:
                    total_equity = parsed_equity
                initial_margin = decimal_value(account.get("imr"))
                maintenance_margin = decimal_value(account.get("mmr"))
                if initial_margin is not None:
                    values["margin"] = initial_margin
                if maintenance_margin is not None:
                    values["maintenance_margin"] = maintenance_margin
                details.extend(account.get("details") or [])
        if total_equity is not None:
            values["equity"] = total_equity
            reason = None
        for item in details:
            unrealized += decimal_value(item.get("upl")) or Decimal(0)
        values["unrealized_pnl"] = unrealized if details else None
        return values, tuple(normalized), reason

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_okx_funding_rows

        return normalize_okx_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )


class OKXLiveProfile(OKXDemoProfile):
    expected_execution_mode = "okx_live_limited"
    expected_environment = "live"
    expected_profile = "okx_live"
    sandbox = False
