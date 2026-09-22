"""Acquire the public price catalog; Core validates and owns pricing evidence."""

from typing import Any

import httpx

from aletheia.economics.pricing_sync import LITELLM_PRICE_MAP_URL


def fetch_price_map() -> tuple[dict[str, Any], bytes]:
    response = httpx.get(LITELLM_PRICE_MAP_URL, timeout=5, follow_redirects=True)
    response.raise_for_status()
    return response.json(), response.content
