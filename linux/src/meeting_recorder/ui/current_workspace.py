"""Move a mapped window to the current KDE desktop and activity before it is presented."""

from __future__ import annotations

import logging
import subprocess

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ..core import activity_pin  # noqa: E402

logger = logging.getLogger(__name__)

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
            Gio.DBusCallFlags.NO_AUTO_START,
            500,
            None,
        )
    except GLib.Error:
        return None
    return reply.unpack()[0] or None


def _xprop(args: list[str]) -> str:
    done = subprocess.run(
        args, check=False, timeout=_XPROP_TIMEOUT_SECONDS, capture_output=True, text=True
    )
    return done.stdout


def pin_to_current_activity(xid: int) -> None:
    """Pin the window to the current activity unless it is on all activities or already there."""
    activity = current_activity()
    if not activity:
        return
    if activity_pin.needs_pin(_xprop(activity_pin.read_command(xid)), activity):
        _xprop(activity_pin.set_command(xid, activity))


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
        pin_to_current_activity(surface.get_xid())
    except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
        logger.debug("Could not follow the current workspace: %s", exc)
