"""Offers to record when a call is detected and the recorder is idle."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..core.state_machine import State

logger = logging.getLogger(__name__)

RECORD = "record"
NOT_NOW = "not-now"
DEFAULT_MODE = "headphones"


class CallPrompt:
    """Turns a call detection into a "Record this meeting?" notification."""

    def __init__(self, engine, notify: Callable, schedule: Callable) -> None:
        self._engine = engine
        self._notify = notify
        self._schedule = schedule

    def on_call_detected(self, source: str) -> None:
        if self._engine.state != State.IDLE:
            logger.debug("Call detected but engine already active — not asking")
            return
        self._notify(
            "Record this meeting?",
            "A call may have started.",
            [(RECORD, "Record"), (NOT_NOW, "Not now")],
            self._on_action,
        )

    def _on_action(self, name: str) -> None:
        if name == RECORD:
            self._schedule(self._engine.start_recording, DEFAULT_MODE)
