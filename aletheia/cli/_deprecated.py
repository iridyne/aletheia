"""Soft-deprecation notice for CLI commands superseded by the web dashboard.

These operational commands (permission / model / risk / strategy / update /
secrets / `bitget test`) all have a web-dashboard equivalent now. Human
interaction is being funnelled to the web UI; the CLI stays the *bootstrap*
surface (gateway start/restart/enable-autostart + doctor) — the things you
cannot do from a UI that does not exist yet.

This is a SOFT deprecation: the commands still work unchanged. We only print a
one-line pointer to the web location on stderr, so anyone (you, on the other
machine, six weeks from now) is nudged to the maintained surface without losing
the escape hatch. Nothing on stdout changes, so scripts and tests are unaffected.

Kept commands (NO web equivalent — deliberately not deprecated): `bitget sync`,
`knowledge reindex`, `knowledge search` are operator/debug tools with no UI twin.
"""

from __future__ import annotations

import typer


def warn_deprecated(web_location: str) -> None:
    """Print a stderr pointer to the web equivalent. stdout is untouched."""
    typer.secho(
        f"[deprecated] this CLI command is superseded by the dashboard → {web_location}. "
        f"It still works, but the web UI is the maintained surface.",
        err=True,
        fg=typer.colors.YELLOW,
    )
