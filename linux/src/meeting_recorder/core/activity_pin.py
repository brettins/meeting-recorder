"""Pure helpers for pinning a window to the current KDE activity through xprop."""

from __future__ import annotations

import re

ACTIVITY_PROPERTY = "_KDE_NET_WM_ACTIVITIES"
NULL_ACTIVITY = "00000000-0000-0000-0000-000000000000"
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def read_command(xid: int) -> list[str]:
    """Build the xprop call that reads a window's activity list."""
    return ["xprop", "-id", str(xid), ACTIVITY_PROPERTY]


def set_command(xid: int, activity: str) -> list[str]:
    """Build the xprop call that pins a window to one activity."""
    return [
        "xprop",
        "-id",
        str(xid),
        "-f",
        ACTIVITY_PROPERTY,
        "8s",
        "-set",
        ACTIVITY_PROPERTY,
        activity,
    ]


def needs_pin(xprop_output: str, current: str) -> bool:
    """True when the window is restricted to activities that exclude the current one."""
    listed = {found.lower() for found in _UUID.findall(xprop_output)}
    if not listed or NULL_ACTIVITY in listed:
        return False
    return current.lower() not in listed
