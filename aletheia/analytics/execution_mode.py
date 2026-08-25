"""Central resolver + cohort identity for `execution_mode`.

Every profitability, insight, family-scorecard, and System Operator surface
must be scoped to exactly one explicitly-resolved execution mode.  Missing
or unreadable configuration must be **unavailable** — never a silent
all-mode aggregate and never a silent ``paper_internal`` fallback (#1939).

Resolution rules:
    * ``resolve_selected_execution_mode()`` returns the currently-selected
      mode (``paper_internal`` / ``okx_demo`` / ``okx_live`` / ...), or
      ``None`` if the config is unreadable.
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
    """Return the configured execution_mode, or ``None`` if unresolvable.

    A missing/unreadable config, or an empty ``paper_execution_mode``,
    yields ``None`` so the caller can render an unavailable/fail-closed
    response.  Never silently returns ``paper_internal``.
    """
    try:
        from aletheia.config.loader import load_config
        mode = str(
            getattr(load_config().exchange, "paper_execution_mode", "") or ""
        ).strip()
        return mode or None
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
