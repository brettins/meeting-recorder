"""Move a mapped window to the current KDE desktop and activity before it is presented."""

from __future__ import annotations

import logging
import subprocess

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)

_ACTIVITY_PROPERTY = "_KDE_NET_WM_ACTIVITIES"
_XPROP_TIMEOUT_SECONDS = 2


def current_activity() -> str | None:
    """Return the current KDE activity id, or None when the activity service is absent."""
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = conn.call_sync(
            "org.kde.ActivityManager",
            "/ActivityManager/Activities",
            "org.kde.ActivityManager.Activities",
            "CurrentActivity",
            None,
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
    except GLib.Error:
        return None
    return reply.unpack()[0] or None


def activity_command(xid: int, activity: str) -> list[str]:
    """Build the xprop call that pins a window to one activity."""
    return [
        "xprop",
        "-id",
        str(xid),
        "-f",
        _ACTIVITY_PROPERTY,
        "8s",
        "-set",
        _ACTIVITY_PROPERTY,
        activity,
    ]


def bring_to_current_workspace(window) -> None:
    """Best effort: present() then stays on the user's desktop and activity."""
    surface = window.get_surface()
    if surface is None:
        return
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ValueError, ImportError):
        return
    if not isinstance(surface, GdkX11.X11Surface):
        return
    try:
        surface.move_to_current_desktop()
        activity = current_activity()
        if activity:
            subprocess.run(
                activity_command(surface.get_xid(), activity),
                check=False,
                timeout=_XPROP_TIMEOUT_SECONDS,
                capture_output=True,
            )
    except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
        logger.debug("Could not follow the current workspace: %s", exc)
