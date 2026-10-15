"""
Wrappers around 'notify-send' (libnotify) for desktop notifications: plain ones, and ones with
action buttons whose choice is reported back to a callback.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence

from ..config.defaults import APP_NAME

logger = logging.getLogger(__name__)


def notify(
    summary: str,
    body: str = "",
    app_name: str = APP_NAME,
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


PROMPT_TIMEOUT_MS = 10 * 60 * 1000


def notify_with_actions(
    summary: str,
    body: str,
    actions: Sequence[tuple[str, str]],
    on_done: Callable[[str], None],
    app_name: str = APP_NAME,
    icon: str = "meeting-recorder",
) -> None:
    """Show a silent notification with (name, label) buttons; call on_done(name) when it ends.

    on_done gets the clicked action's name, or "" if it was dismissed, expired or failed. When
    notify-send rejects --action (libnotify before 0.8) a plain notification is shown instead.
    """
    cmd = [
        "notify-send",
        "--app-name",
        app_name,
        "--icon",
        icon,
        f"--expire-time={PROMPT_TIMEOUT_MS}",
        "--hint=boolean:resident:true",
        "--hint=boolean:suppress-sound:true",
    ]
    for name, label in actions:
        cmd.append(f"--action={name}={label}")
    cmd += [summary, body]

    def _wait() -> None:
        chosen = ""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            try:
                out = (proc.communicate(timeout=PROMPT_TIMEOUT_MS / 1000 + 30)[0] or "").strip()
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                logger.info("Notification outlived its timeout; closed it")
                on_done("")
                return
            if proc.returncode != 0:
                logger.warning(
                    "notify-send exited %s with actions; showing a plain notification",
                    proc.returncode,
                )
                notify(summary, f"{body} Open {app_name} to start recording.", app_name, icon)
            elif out in {name for name, _ in actions}:
                chosen = out
        except FileNotFoundError:
            logger.warning("notify-send not found; notifications unavailable")
        except Exception as exc:
            logger.warning("Failed to send notification: %s", exc)
        on_done(chosen)

    threading.Thread(target=_wait, name="notify-actions", daemon=True).start()
