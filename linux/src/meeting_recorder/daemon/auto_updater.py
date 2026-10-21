"""Daemon-side schedule for auto-update: check periodically, restart once the app is idle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from gi.repository import GLib

from ..config import settings
from ..services import auto_update
from ..utils.glib_bridge import idle_call

logger = logging.getLogger(__name__)

FIRST_CHECK_SECONDS = 60
IDLE_POLL_SECONDS = 30


class AutoUpdater:
    """Checks for a new main, installs it, and asks for a restart while nothing is running."""

    def __init__(
        self,
        is_idle: Callable[[], bool],
        restart: Callable[[], None],
        check: Callable[[], auto_update.UpdateOutcome] = auto_update.check_and_apply,
    ) -> None:
        self._is_idle = is_idle
        self._restart = restart
        self._check = check
        self._pending_restart = False

    def start(self) -> None:
        cfg = settings.load()
        if not auto_update.enabled(cfg, auto_update.read_install_source()):
            logger.info("Auto-update off (packaged install or disabled)")
            return
        logger.info("Auto-update every %ds", auto_update.interval_seconds(cfg))
        GLib.timeout_add_seconds(FIRST_CHECK_SECONDS, self._tick)

    def _tick(self) -> bool:
        threading.Thread(target=self._run_check, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _run_check(self) -> None:
        try:
            outcome = self._check()
        except Exception as exc:
            logger.warning("Auto-update check failed: %s", exc)
            outcome = auto_update.UpdateOutcome(False, "check failed")
        idle_call(self._on_outcome, outcome)

    def _on_outcome(self, outcome: auto_update.UpdateOutcome) -> None:
        logger.info("Auto-update: %s", outcome.message)
        if outcome.updated:
            self._pending_restart = True
            GLib.timeout_add_seconds(IDLE_POLL_SECONDS, self._restart_when_idle)
        GLib.timeout_add_seconds(auto_update.interval_seconds(settings.load()), self._tick)

    def _restart_when_idle(self) -> bool:
        if not self._is_idle():
            return GLib.SOURCE_CONTINUE
        logger.info("Auto-update: idle, restarting the daemon")
        self._restart()
        return GLib.SOURCE_REMOVE
