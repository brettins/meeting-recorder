"""
Wrappers around 'notify-send' (libnotify) for desktop notifications: plain ones, and ones with
action buttons whose choice is reported back to a callback.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)


def notify(
    summary: str,
    body: str = "",
    app_name: str = "Meeting Recorder",
    icon: str = "meeting-recorder",
) -> None:
    """Send a desktop notification using notify-send (libnotify)."""
    cmd = [
        "notify-send",
        "--app-name",
        app_name,
        "--icon",
        icon,
        summary,
    ]
    if body:
        cmd.append(body)

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.warning("notify-send not found; notifications unavailable")
    except Exception as exc:
        logger.warning("Failed to send notification: %s", exc)


def notify_with_actions(
    summary: str,
    body: str,
    actions: Sequence[tuple[str, str]],
    on_action: Callable[[str], None],
    app_name: str = "Meeting Recorder",
    icon: str = "meeting-recorder",
) -> None:
    """Show a silent, resident notification with (name, label) buttons; call on_action(name) on a click."""
    cmd = [
        "notify-send",
        "--app-name",
        app_name,
        "--icon",
        icon,
        "--expire-time=0",
        "--hint=boolean:resident:true",
        "--hint=boolean:suppress-sound:true",
    ]
    for name, label in actions:
        cmd.append(f"--action={name}={label}")
    cmd += [summary, body]

    def _wait() -> None:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            chosen = (proc.communicate()[0] or "").strip()
        except FileNotFoundError:
            logger.warning("notify-send not found; notifications unavailable")
            return
        except Exception as exc:
            logger.warning("Failed to send notification: %s", exc)
            return
        if chosen in {name for name, _ in actions}:
            on_action(chosen)

    threading.Thread(target=_wait, name="notify-actions", daemon=True).start()
