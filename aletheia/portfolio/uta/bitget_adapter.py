"""Bitget Demo IBroker adapter — wraps BitgetClient for UTA read queries."""

import logging

from aletheia.portfolio.uta.broker_protocol import AccountInfo, BrokerPosition

logger = logging.getLogger("aletheia.uta.bitget_adapter")


class BitgetDemoAdapter:
    broker_id = "bitget_demo"
    display_name = "Bitget Demo"

    def __init__(self):
        from aletheia.exchanges.bitget.client import BitgetClient
        from aletheia.config.loader import load_config
        cfg = load_config()
        self._client = BitgetClient(mode=cfg.exchange.mode)
        self._client._get_creds()  # raises RuntimeError when not configured

    async def get_account(self) -> AccountInfo:
        client = self._client
        coin_usdt: dict[str, float] = {}

        try:
            balances = await client.get_balances()
            for b in balances:
                coin = (b.currency or "").upper()
                qty = float(b.available) + float(b.frozen)
                if qty <= 0:
                    continue
                if coin == "USDT":
                    coin_usdt["USDT"] = coin_usdt.get("USDT", 0) + qty
                else:
                    try:
                        ticker = await client.get_ticker(f"{coin}USDT", market_type="spot")
                        coin_usdt[coin] = coin_usdt.get(coin, 0) + qty * ticker.last_price
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("bitget spot balances skipped: %s", e)

        usdt_avail = 0.0
        usdt_frozen = 0.0
        futures_upnl = 0.0
        equity_from_mix = None
        try:
            mix_acct = await client.get_mix_account("USDT-FUTURES")
            if mix_acct:
                equity_from_mix = float(mix_acct.get("usdtEquity") or mix_acct.get("accountEquity") or 0)
                usdt_avail = float(mix_acct.get("available") or 0)
                usdt_frozen = float(mix_acct.get("locked") or 0)
                futures_upnl = float(mix_acct.get("unrealizedPL") or mix_acct.get("crossedUnrealizedPL") or 0)
                if not coin_usdt and equity_from_mix > 0:
                    coin_usdt["USDT"] = equity_from_mix
        except Exception as e:
            logger.debug("bitget mix account skipped: %s", e)

        equity = equity_from_mix if equity_from_mix is not None else sum(coin_usdt.values())
        total = equity if equity > 0 else 1.0
        breakdown = [
            {"coin": coin, "usdt_value": round(val, 4), "pct": round(val / total * 100, 2)}
            for coin, val in sorted(coin_usdt.items(), key=lambda x: -x[1])
            if val > 0
        ]

        return AccountInfo(
            equity_usdt=round(equity, 4),
            available_usdt=round(usdt_avail, 4),
            frozen_usdt=round(usdt_frozen, 4),
            unrealized_pnl=round(futures_upnl, 4),
            asset_breakdown=breakdown,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        try:
            futures = await self._client.get_positions()
            return [
                BrokerPosition(
                    symbol=p.symbol,
                    side=p.side,
                    quantity=float(p.quantity),
                    entry_price=float(p.entry_price),
                    unrealized_pnl=float(p.unrealized_pnl or 0),
                    leverage=int(p.leverage or 1),
                    mark_price=float(p.mark_price or 0),
                )
                for p in futures
            ]
        except Exception as e:
            logger.debug("bitget get_positions failed: %s", e)
            return []

    async def test_connection(self) -> bool:
        try:
            result = await self._client.test_connection()
            return result.get("connected", False)
        except Exception:
            return False

    async def close(self) -> None:
        pass  # BitgetClient has no persistent session to close
