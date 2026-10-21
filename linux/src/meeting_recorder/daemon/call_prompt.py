"""Offers to record when a call is detected and the recorder is idle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..core.state_machine import State

logger = logging.getLogger(__name__)

RECORD = "record"
NOT_NOW = "not-now"
DEFAULT_MODE = "headphones"
_LABELS = {"headphones": "Headphones", "speaker": "Speaker"}


class CallPrompt:
    """Turns a call detection into one "Record this meeting?" notification at a time."""

    def __init__(self, engine, notify: Callable, schedule: Callable) -> None:
        self._engine = engine
        self._notify = notify
        self._schedule = schedule
        self._pending = False
        self._lock = threading.Lock()

    @property
    def pending(self) -> bool:
        """Whether a "Record this meeting?" prompt is on screen."""
        with self._lock:
            return self._pending

    def on_call_detected(self, source: str) -> None:
        if self._engine.state != State.IDLE:
            logger.debug("Call detected but engine already active — not asking")
            return
        with self._lock:
            if self._pending:
                logger.debug("Call detected while a prompt is still open — not asking again")
                return
            self._pending = True
        mode = self._mode()
        try:
            self._notify(
                "Record this meeting?",
                "A call may have started.",
                [(RECORD, f"Record ({_LABELS[mode]})"), (NOT_NOW, "Not now")],
                lambda name: self._on_done(name, mode),
            )
        except Exception:
            with self._lock:
                self._pending = False
            raise

    def _mode(self) -> str:
        mode = getattr(self._engine, "last_mode", None)
        return mode if mode in _LABELS else DEFAULT_MODE

    def _on_done(self, name: str, mode: str) -> None:
        with self._lock:
            self._pending = False
        if name == RECORD:
            self._schedule(self._engine.start_recording, mode)
