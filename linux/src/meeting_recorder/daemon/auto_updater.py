"""Daemon-side schedule for auto-update: install and restart only while nothing is running."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from ..config import settings
from ..core.state_machine import State
from ..services import auto_update
from ..utils.glib_bridge import idle_call

logger = logging.getLogger(__name__)

FIRST_CHECK_SECONDS = 60
BUSY_RETRY_SECONDS = 60
IDLE_POLL_SECONDS = 30


def is_idle(engine: Any, runner: Any, call_prompt: Any, install_manager: Any) -> bool:
    """True when a restart would lose nothing: no recording, countdown, job, prompt or install."""
    return not (
        engine.state != State.IDLE
        or engine.processing_jobs()
        or engine.has_pending_input()
        or runner.active_descriptions()
        or (call_prompt is not None and call_prompt.pending)
        or install_manager.busy()
    )


class AutoUpdater:
    """Checks for a new main and installs it only when idle, then restarts the daemon."""

    def __init__(
        self,
        is_idle: Callable[[], bool],
        restart: Callable[[], None],
        check: Callable[[], auto_update.UpdateOutcome] = auto_update.check_and_apply,
        smoke: Callable[[], tuple[bool, str]] = auto_update.smoke_test,
    ) -> None:
        self._is_idle = is_idle
        self._restart = restart
        self._check = check
        self._smoke = smoke
        self._restart_polling = False

    def start(self) -> None:
        cfg = settings.load()
        if not auto_update.enabled(cfg, auto_update.read_install_source()):
            logger.info("Auto-update off (packaged install or disabled)")
            return
        logger.info("Auto-update every %ds", auto_update.interval_seconds(cfg))
        GLib.timeout_add_seconds(FIRST_CHECK_SECONDS, self._tick)

    def _tick(self) -> bool:
        if not self._is_idle():
            GLib.timeout_add_seconds(BUSY_RETRY_SECONDS, self._tick)
            return GLib.SOURCE_REMOVE
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
        if outcome.updated and not self._restart_polling:
            self._restart_polling = True
            GLib.timeout_add_seconds(IDLE_POLL_SECONDS, self._restart_when_idle)
        GLib.timeout_add_seconds(auto_update.interval_seconds(settings.load()), self._tick)

    def _restart_when_idle(self) -> bool:
        if not self._is_idle():
            return GLib.SOURCE_CONTINUE
        self._restart_polling = False
        ok, detail = self._smoke()
        if not ok:
            logger.error(
                "Auto-update: new version failed its start check (%s); not restarting", detail
            )
            return GLib.SOURCE_REMOVE
        logger.info("Auto-update: idle, restarting the daemon")
        self._restart()
        return GLib.SOURCE_REMOVE
