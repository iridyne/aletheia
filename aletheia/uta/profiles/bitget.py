"""Independent Bitget Classic-v2 and UTA-v3 profiles."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from aletheia.uta.profiles.base import CCXTProfile, decimal_value
from aletheia.uta.models import FillReport, UTAAccountIdentity


class _BitgetDemoProfile(CCXTProfile):
    expected_venue = "bitget"
    expected_execution_mode = "bitget_demo"
    expected_environment = "demo"
    exchange_name = "bitget"
    sandbox = True
    supported_market_types = ("spot", "usdt_futures")

    def __init__(self, *, api_key: str, secret: str, passphrase: str, uta: bool) -> None:
        super().__init__(
            apiKey=api_key,
            secret=secret,
            password=passphrase,
            options={"defaultType": "swap", "uta": uta},
        )

    def fill(
        self,
        raw: dict[str, object],
        markets: dict[str, Any],
        identity: UTAAccountIdentity,
        received_at: datetime,
    ) -> FillReport:
        report = super().fill(raw, markets, identity, received_at)
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        position_mode = str(
            raw.get("posMode") or info.get("posMode") or ""
        ).strip().lower()
        trade_intent = (report.trade_intent or "").strip().lower()
        if (
            report.position_side is None
            and position_mode in {"hedge_mode", "hedge", "two_way", "two-way"}
            and trade_intent in {"close", "exit", "reduce", "decrease"}
            and report.side.lower() in {"buy", "sell"}
        ):
            # Bitget Classic hedge fills keep the opening side on a close.  The
            # explicit close intent plus hedge mode makes this a canonical leg,
            # without weakening ambiguous or opening activity.
            return report.model_copy(update={
                "position_side": (
                    "long" if report.side.lower() == "buy" else "short"
                ),
            })
        return report


class BitgetClassicV2Profile(_BitgetDemoProfile):
    expected_profile = "classic_v2"
    balance_requests = (
        {"type": "spot"},
        {"type": "swap", "productType": "USDT-FUTURES"},
    )
    position_params = {"productType": "USDT-FUTURES"}
    open_order_requests = (
        {"type": "spot"},
        {"type": "swap", "productType": "USDT-FUTURES"},
    )
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(api_key=api_key, secret=secret, passphrase=passphrase, uta=False)

    def account_values(self, balances):
        values, normalized, reason = super().account_values(balances)
        mix_equity = Decimal(0)
        unrealized = Decimal(0)
        saw_mix = False
        for raw in balances:
            info = raw.get("info") or []
            for account in info if isinstance(info, list) else []:
                equity = decimal_value(account.get("usdtEquity") or account.get("accountEquity"))
                if equity is not None:
                    mix_equity += equity
                    saw_mix = True
                unrealized += decimal_value(
                    account.get("unrealizedPL") or account.get("crossedUnrealizedPL")
                ) or Decimal(0)
        if saw_mix:
            spot_usdt = Decimal(0)
            if balances:
                spot_usdt = decimal_value((balances[0].get("total") or {}).get("USDT")) or Decimal(0)
            values["equity"] = mix_equity + spot_usdt
            values["unrealized_pnl"] = unrealized
        return values, normalized, reason

    def funding_events(self, rows):
        from aletheia.uta.funding import normalize_bitget_funding_rows

        return normalize_bitget_funding_rows(
            row.get("info", row) if isinstance(row, dict) else {} for row in rows
        )


class BitgetUTAV3Profile(_BitgetDemoProfile):
    expected_profile = "uta_v3"
    balance_requests = ({"uta": True},)
    position_params = {"uta": True, "productType": "USDT-FUTURES"}
    open_order_requests = (
        {"uta": True, "type": "spot", "productType": "SPOT"},
        {"uta": True, "type": "swap", "productType": "USDT-FUTURES"},
    )
    closed_order_requests = open_order_requests
    fill_requests = open_order_requests

    def __init__(self, *, api_key: str, secret: str, passphrase: str) -> None:
        super().__init__(api_key=api_key, secret=secret, passphrase=passphrase, uta=True)

    def account_values(self, balances):
        values, normalized, _reason = super().account_values(balances)
        usd_equity = Decimal(0)
        saw_usd_value = False
        for raw in balances:
            info = raw.get("info") or []
            for asset in info if isinstance(info, list) else []:
                usd_value = decimal_value(asset.get("usdValue"))
                if usd_value is not None:
                    usd_equity += usd_value
                    saw_usd_value = True
        if saw_usd_value:
            values["equity"] = usd_equity
        return values, normalized, None if saw_usd_value else _reason

    funding_events = BitgetClassicV2Profile.funding_events


class BitgetClassicV2LiveProfile(BitgetClassicV2Profile):
    expected_execution_mode = "bitget_live_limited"
    expected_environment = "live"
    sandbox = False


class BitgetUTAV3LiveProfile(BitgetUTAV3Profile):
    expected_execution_mode = "bitget_live_limited"
    expected_environment = "live"
    sandbox = False
