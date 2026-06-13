"""Best-effort Windows toast notifications. Never raises."""

import logging
import platform
import subprocess

logger = logging.getLogger("aletheia.notifications")

_IS_WINDOWS = platform.system() == "Windows"


def send_windows_toast(title: str, message: str) -> None:
    """Send a Windows toast notification via PowerShell. No-op on non-Windows."""
    if not _IS_WINDOWS:
        return
    try:
        safe_title = title.replace("'", "''")
        safe_message = message.replace("'", "''")
        script = (
            f"$ErrorActionPreference='SilentlyContinue'; "
            f"if (Get-Module -ListAvailable -Name BurntToast) {{ "
            f"  Import-Module BurntToast; "
            f"  New-BurntToastNotification -Text '{safe_title}','{safe_message}' "
            f"}} else {{ "
            f"  [void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]; "
            f"  [void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]; "
            f"  $xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); "
            f"  $xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{safe_title}</text><text>{safe_message}</text></binding></visual></toast>'); "
            f"  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            f"  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Aletheia').Show($toast) "
            f"}}"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.debug("Toast notification failed (non-critical): %s", e)
