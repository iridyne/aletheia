"""The margin mode the entry path actually submits, per execution route (#3729 §8).

One declared value, read by both the leverage resolver (which must prove safety
under the mode it assumes) and the submission path (which must send that same
mode). A route with no declared mode has no qualified safety model, so leveraged
entry on it is unavailable -- never assumed, and never resolved with an
isolated-position estimate while the venue posts cross margin.
"""

from __future__ import annotations

from typing import Literal

MarginMode = Literal["isolated", "cross"]

# V1 qualifies ISOLATED, the only mode whose position-local liquidation model
# (`calc_liquidation_price` with the exact venue tier maintenance-margin rate)
# matches what the adapter submits. `bitget_demo` is the one registered Entry
# Plan route (`ENTRY_EXECUTION_MINIMUM_PERMISSION`), so it is the one route that
# needs a declared mode today.
#
# A route that is not listed here has no qualified mode: the resolver and the
# submission path both fail closed with MARGIN_MODE_UNAVAILABLE rather than
# falling back to "policy cap = execution leverage" or to a cross-margin guess.
SUBMISSION_MARGIN_MODE: dict[str, MarginMode] = {
    "bitget_demo": "isolated",
}


def submission_margin_mode(execution_mode: str) -> MarginMode | None:
    """The qualified submission mode for one execution route, or None."""
    return SUBMISSION_MARGIN_MODE.get(str(execution_mode or "").strip())
