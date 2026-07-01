"""Shared utilities for all exchange brokers (OKX, Bitget, Binance).

Functions here are exchange-agnostic — they must work for any venue.
"""

import json


def clean_error_message(raw: str) -> str:
    """Extract a human-readable message from a raw CCXT/exchange error string.

    Handles three common shapes:
    - OKX API JSON:     'OKX: okx {"code":"1","data":[{"sMsg":"<text>"}],...}'
                        → 'OKX: <text>'
    - Bitget API JSON:  'bitget {"code":"...","msg":"<text>",...}'
                        → 'Bitget: <text>'
    - Binance API JSON: 'binance {"code":-1121,"msg":"Invalid symbol."}'
                        → 'Binance: Invalid symbol.'
    - CCXT symbol miss: 'okx does not have market symbol WLD/USDT:USDT'
                        → 'Symbol WLD/USDT not available on OKX'
    - Anything else:    returned stripped and unchanged.
    """
    if not raw:
        return raw

    # Try to extract a friendly message from an embedded JSON payload.
    json_start = raw.find("{")
    if json_start != -1:
        try:
            payload = json.loads(raw[json_start:])

            # OKX shape: data[0].sMsg
            data = payload.get("data") or []
            if data and isinstance(data, list):
                smsg = (data[0].get("sMsg") or "").strip()
                if smsg:
                    return f"OKX: {smsg}"

            # Bitget / Binance / generic shape: top-level "msg"
            msg = (payload.get("msg") or payload.get("message") or "").strip()
            if msg:
                # Prefix with the exchange name extracted from the raw prefix
                prefix = raw[:json_start].strip().rstrip(":").split()[-1].capitalize()
                return f"{prefix}: {msg}"

        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            pass

    # CCXT "does not have market symbol X" → friendlier wording
    if "does not have market symbol" in raw:
        parts = raw.split("does not have market symbol")
        sym_part = parts[-1].strip().split(":")[0].strip()  # strip settle suffix
        exchange = raw.split("does not have")[0].strip().split()[-1].capitalize()
        return f"Symbol {sym_part} not available on {exchange}"

    return raw.strip()
