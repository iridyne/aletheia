"""Resolve the current Windows account for Task Scheduler XML."""

from __future__ import annotations

import getpass
import os


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
