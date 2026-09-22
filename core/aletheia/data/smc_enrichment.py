"""Bounded pure-Python SMC enrichments for canonical market structure."""

from __future__ import annotations

from typing import Sequence

from aletheia.data.market_structure import (
    CONTRACT_VERSION,
    FVG_ALGORITHM_VERSION,
    StructureZone,
    _float_identity,
    _sha256,
)
from aletheia.data.providers import Candle
from aletheia.data.symbols import InstrumentId


# Semantic reference only: joshyattridge/smart-money-concepts
# commit 1b62fd6c41e1f508e7ed76831a039fa4c82d42f6, smc.py::fvg, MIT.
def derive_fvg_zones(
    candles: Sequence[Candle],
    *,
    instrument: InstrumentId,
    timeframe: str,
    tf_ms: int,
) -> tuple[StructureZone, ...]:
    zones: list[StructureZone] = []
    for index in range(1, len(candles) - 1):
        previous, middle, third = (
            candles[index - 1],
            candles[index],
            candles[index + 1],
        )
        if previous.high < third.low and middle.close > middle.open:
            direction = "bullish"
            top, bottom = third.low, previous.high
        elif previous.low > third.high and middle.close < middle.open:
            direction = "bearish"
            top, bottom = previous.low, third.high
        else:
            continue
        known_at_ms = third.timestamp + tf_ms
        mitigated_at_ms = None
        for later in candles[index + 2:]:
            mitigated = (
                direction == "bullish" and later.low <= top
            ) or (
                direction == "bearish" and later.high >= bottom
            )
            if mitigated:
                mitigated_at_ms = later.timestamp + tf_ms
                break
        zone_id = _sha256({
            "bottom": _float_identity(bottom),
            "contract_version": CONTRACT_VERSION,
            "direction": direction,
            "fvg_algorithm_version": FVG_ALGORITHM_VERSION,
            "instrument_id": str(instrument),
            "known_at_ms": known_at_ms,
            "origin_at_ms": middle.timestamp,
            "timeframe": timeframe,
            "top": _float_identity(top),
        })
        zones.append(StructureZone(
            zone_id,
            "FVG",
            direction,
            top,
            bottom,
            middle.timestamp,
            known_at_ms,
            "mitigated" if mitigated_at_ms is not None else "open",
            mitigated_at_ms,
            FVG_ALGORITHM_VERSION,
        ))
    return tuple(zones)
