"""
Reading ffmpeg's silencedetect output.

The recorder's ffmpeg carries a ``silencedetect`` filter, which changes nothing
about the audio and only logs a line when a quiet stretch begins or ends. Those
lines are the app's only live signal for "is anything being said", so the
parsing is kept here, pure, rather than buried in the stderr drain thread.

ffmpeg emits, on stderr::

    [silencedetect @ 0x…] silence_start: 1234.567
    [silencedetect @ 0x…] silence_end: 1290.1 | silence_duration: 55.5

Note that the *start* line is only printed once the silence has already lasted
the filter's ``d`` seconds, so a listener learns of a quiet stretch that many
seconds late — which is why the caller adds that offset back rather than
timing from the moment the line arrives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SILENCE_START = "start"
SILENCE_END = "end"

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


@dataclass(frozen=True)
class SilenceEvent:
    """One silencedetect line: quiet began, or quiet ended, at *at_seconds*."""

    kind: str
    at_seconds: float


def parse_silence_event(line: str) -> SilenceEvent | None:
    """Read one line of ffmpeg stderr, or None when it is not a silence line."""
    if "silencedetect" not in line and "silence_" not in line:
        return None
    match = _END.search(line)
    if match:
        return _event(SILENCE_END, match.group(1))
    match = _START.search(line)
    if match:
        return _event(SILENCE_START, match.group(1))
    return None


def _event(kind: str, raw: str) -> SilenceEvent | None:
    try:
        return SilenceEvent(kind, float(raw))
    except ValueError:
        return None
