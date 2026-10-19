"""
Which recorder controls are visible for a given lifecycle state.

The window used to destroy and rebuild its button box on every snapshot, and a
snapshot arrives once a second while recording (the timer tick). A GTK button
only emits ``clicked`` when press *and* release land on the same live widget, so
a rebuild between the two silently swallowed the click — the "the Stop button
doesn't work sometimes" bug. The window now builds every control once and only
toggles visibility, and this is the pure policy that decides which ones show.
"""

from __future__ import annotations

RECORD_HEADPHONES = "record_headphones"
RECORD_SPEAKER = "record_speaker"
USE_EXISTING = "use_existing"
PAUSE = "pause"
RESUME = "resume"
STOP = "stop"
CANCEL_SAVE = "cancel_save"
CANCEL = "cancel"
CANCEL_COUNTDOWN = "cancel_countdown"

_BY_STATE: dict[str, tuple[str, ...]] = {
    "idle": (RECORD_HEADPHONES, RECORD_SPEAKER, USE_EXISTING),
    "recording": (PAUSE, STOP, CANCEL_SAVE, CANCEL),
    "paused": (RESUME, STOP, CANCEL_SAVE, CANCEL),
    "countdown": (CANCEL_COUNTDOWN,),
}

ALL_CONTROLS: tuple[str, ...] = (
    RECORD_HEADPHONES,
    RECORD_SPEAKER,
    USE_EXISTING,
    PAUSE,
    RESUME,
    STOP,
    CANCEL_SAVE,
    CANCEL,
    CANCEL_COUNTDOWN,
)


def controls_for_state(state: str) -> tuple[str, ...]:
    """Control ids visible in *state*; an unknown state falls back to idle."""
    return _BY_STATE.get(state, _BY_STATE["idle"])


def title_editable(state: str) -> bool:
    """Whether the title/tag editors accept input in *state*.

    Editable while recording and paused so a meeting can be named and filed as
    it happens; the folder rename is queued until the recorder has released the
    directory. Locked during the stop countdown, when that rename is already in
    flight on a worker thread.
    """
    return state in ("idle", "recording", "paused")
