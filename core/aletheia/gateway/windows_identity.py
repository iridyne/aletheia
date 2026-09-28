"""ARCHIVED (platform-specific, unmaintained) — Windows account resolution for Task Scheduler XML.

Retained, not deleted. Imported at module level by ``autostart``, ``managed_service``
and ``session_service``, so it must stay importable, but every *use* is inside a
Windows-only branch and the Linux/Fedora runtime never calls a function here.
See docs/design/history/platform_specific_gateway_services.md."""

from __future__ import annotations

import getpass
import logging
import os
import re
import subprocess

logger = logging.getLogger("aletheia.gateway.windows_identity")


def current_windows_user() -> str:
    """Return a Task Scheduler principal for the current user token.

    OpenSSH sessions on workgroup machines may expose ``USERDOMAIN=WORKGROUP``.
    WORKGROUP is network membership, not a Windows account authority, so use the
    local computer name for that case. Already-qualified domain or UPN names are
    preserved.
    """
    user = os.environ.get("USERNAME") or getpass.getuser()
    if "\\" in user or "@" in user:
        return user

    domain = os.environ.get("USERDOMAIN")
    if domain and domain.casefold() != "workgroup":
        return f"{domain}\\{user}"

    computer = os.environ.get("COMPUTERNAME")
    return f"{computer}\\{user}" if computer else user


def current_windows_user_ids() -> set[str]:
    """Return case-folded account names and the token SID used in task exports."""
    identities = {current_windows_user().casefold()}
    try:
        proc = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            match = re.search(rb"S-\d(?:-\d+)+", proc.stdout, flags=re.IGNORECASE)
            if match:
                identities.add(match.group().decode("ascii").casefold())
    except OSError:
        logger.debug("windows_identity: user-SID probe failed; no identity added", exc_info=True)
    return identities
