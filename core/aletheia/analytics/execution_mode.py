"""Central resolver + cohort identity for `execution_mode`.

Every profitability, insight, family-scorecard, and System Operator surface
must be scoped to exactly one explicitly-resolved execution mode.  Missing
or unreadable configuration must be **unavailable** — never a silent
all-mode aggregate and never an implicit execution account (#1939).

Resolution rules:
    * ``resolve_selected_execution_mode()`` returns the currently-selected
      external demo/testnet/live mode, or ``None`` if unconfigured, retired
      or unreadable.
    * ``execution_mode_identity(mode)`` returns the metadata block that
      every profitability response/report/context should include so the
      resolved cohort is auditable next to the metrics.

Callers must fail-closed on ``None``: analytics helpers keep working
(they just receive ``execution_mode=None`` which means "all modes" in
raw storage terms), but a UI/API/CLI/Operator boundary that cannot
resolve a selected mode MUST NOT emit an all-mode aggregate under a
current-mode identity.
"""
from __future__ import annotations

from typing import Any


def resolve_selected_execution_mode() -> str | None:
    """Return the effective selected execution mode, or ``None`` if unresolvable.

    A valid live unlock selects the broker's ``*_live_limited`` identity so
    current-account analytics follow the same authority as new Proposals.
    Missing/unreadable config or an unsupported account mode yields ``None``; invalid
    live evidence falls back to the configured paper/demo mode.
    """
    try:
        from aletheia.config.exchanges import selected_live_execution_mode
        from aletheia.config.loader import load_config, read_only_config
        from aletheia.uta.manager import UTA_EXECUTION_MODES

        with read_only_config():
            cfg = load_config()
        paper_mode = str(
            getattr(cfg.exchange, "paper_execution_mode", "") or ""
        ).strip()
        if paper_mode not in UTA_EXECUTION_MODES:
            paper_mode = ""
        if (
            str(getattr(cfg.exchange, "mode", "") or "").strip().lower() == "live"
            and getattr(getattr(cfg, "live", None), "state", "") == "live_limited"
        ):
            from aletheia.trust.live_unlock import current_authorization_ref

            if current_authorization_ref(cfg) is not None:
                return selected_live_execution_mode(cfg) or paper_mode or None
        return paper_mode or None
    except Exception:
        return None


def execution_mode_identity(execution_mode: str | None) -> dict[str, Any]:
    """Return the cohort-identity block for a profitability payload.

    ``execution_mode_status`` is ``"resolved"`` when a concrete mode was
    threaded through, and ``"unavailable"`` otherwise.  Consumers with
    behavior authority (System Operator, Go-Live, dashboards labelled
    with a current-mode identity) must treat ``"unavailable"`` as a
    fail-closed signal, not as an all-mode aggregate.
    """
    return {
        "execution_mode": execution_mode,
        "execution_mode_status": "resolved" if execution_mode else "unavailable",
    }
