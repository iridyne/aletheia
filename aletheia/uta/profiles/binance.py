"""Binance Futures Testnet UTA profile."""

from decimal import Decimal

from aletheia.uta.profiles.base import CCXTProfile, decimal_value


class BinanceTestnetProfile(CCXTProfile):
    expected_venue = "binance"
    expected_execution_mode = "binance_testnet"
    expected_environment = "testnet"
    expected_profile = "binance_testnet"
    exchange_name = "binanceusdm"
    sandbox = True

    def __init__(self, *, api_key: str, secret: str) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            options={"defaultType": "future"},
        )

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        unrealized = Decimal(0)
        saw_unrealized = False
        for raw in balances:
            info = raw.get("info") or {}
            if not isinstance(info, dict):
                continue
            initial_margin = decimal_value(info.get("totalInitialMargin"))
            maintenance_margin = decimal_value(info.get("totalMaintMargin"))
            if initial_margin is not None:
                values["margin"] = initial_margin
            if maintenance_margin is not None:
                values["maintenance_margin"] = maintenance_margin
            for asset in info.get("assets", []) or []:
                if str(asset.get("asset") or "").upper() != "USDT":
                    continue
                parsed = decimal_value(asset.get("unrealizedProfit"))
                if parsed is not None:
                    unrealized += parsed
                    saw_unrealized = True
        if saw_unrealized:
            values["unrealized_pnl"] = unrealized
        return values, normalized, reason

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_binance_funding_rows

        return normalize_binance_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )


class BinanceLiveProfile(BinanceTestnetProfile):
    expected_execution_mode = "binance_live_limited"
    expected_environment = "live"
    expected_profile = "binance_live"
    sandbox = False
