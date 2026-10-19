"""
When a recording should nudge, warn, or stop itself.

A recording that nobody stops runs until the disk does — one in this library
reached 16 hours, of which 87% was silence. Two guards answer that:

* A **hard cap** stops outright. It cannot be fooled: no signal analysis, just
  wall-clock time.
* **Silence escalates** rather than acting at once. Quiet is ambiguous — a
  presentation someone is only listening to looks like a meeting that ended an
  hour ago — so the first quiet stretch is a nudge, a longer one says a stop is
  coming, and only then does it stop. Any sound resets the ladder to the
  beginning, so a meeting that goes quiet and resumes is never interrupted.

Silence means the *mixed* stream is quiet, so the microphone and the system
audio both are. Music through the speakers suppresses the ladder, which is
correct: something is still being recorded.

Nothing here is announced twice. The caller keeps the highest stage it has
already shown and passes it back, so a single quiet stretch produces one nudge
and one warning, not one per second.

Pure and gi-free. ``core/recording_controller.py`` supplies the numbers and
carries out the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NONE = "none"
NUDGE = "nudge"
WARN = "warn"
STOP = "stop"

# Stages of one quiet stretch, in the order they are announced. The caller
# stores the last one it acted on; sound resets it to NOTHING.
STAGE_NOTHING = 0
STAGE_NUDGED = 1
STAGE_WARNED = 2
STAGE_STOPPED = 3

_STAGE_FOR = {NONE: STAGE_NOTHING, NUDGE: STAGE_NUDGED, WARN: STAGE_WARNED, STOP: STAGE_STOPPED}

DEFAULT_MAX_MINUTES = 180
DEFAULT_SILENCE_GRACE_MINUTES = 60
DEFAULT_SILENCE_NUDGE_MINUTES = 10
DEFAULT_SILENCE_STOP_MINUTES = 30
DEFAULT_SILENCE_WARN_LEAD_MINUTES = 5

# What counts as quiet, in dBFS, and how long ffmpeg's silencedetect must see
# it before it reports anything. The threshold sits below room tone but above
# the noise floor of a muted line.
SILENCE_THRESHOLD_DB = -45
SILENCE_DETECT_SECONDS = 15


@dataclass(frozen=True)
class AutoStopConfig:
    """The user's settings for both guards."""

    max_minutes: int = DEFAULT_MAX_MINUTES
    silence_enabled: bool = True
    grace_minutes: int = DEFAULT_SILENCE_GRACE_MINUTES
    nudge_minutes: int = DEFAULT_SILENCE_NUDGE_MINUTES
    stop_minutes: int = DEFAULT_SILENCE_STOP_MINUTES
    warn_lead_minutes: int = DEFAULT_SILENCE_WARN_LEAD_MINUTES

    @property
    def warn_minutes(self) -> int:
        """Silence at which the "stopping soon" warning is due.

        Derived rather than configured so it can never be ordered after the
        stop it is warning about; a lead longer than the stop threshold simply
        makes the warning the first thing that happens.
        """
        return max(self.stop_minutes - self.warn_lead_minutes, 0)


@dataclass(frozen=True)
class AutoStopDecision:
    """What to do, the sentence to show for it, and the stage it represents."""

    action: str = NONE
    message: str = ""

    @property
    def stage(self) -> int:
        return _STAGE_FOR.get(self.action, STAGE_NOTHING)


def _positive_int(value: Any, fallback: int) -> int:
    """Read a config number, falling back on anything unusable.

    A hand-edited config must not be able to disable a guard by accident, so
    only a genuine number is accepted; a negative one is clamped to zero, which
    is the documented "off".
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(number, 0)


def config_from(config: dict[str, Any]) -> AutoStopConfig:
    """Build the settings from a raw config dict."""
    return AutoStopConfig(
        max_minutes=_positive_int(config.get("max_recording_minutes"), DEFAULT_MAX_MINUTES),
        silence_enabled=bool(config.get("silence_auto_stop_enabled", True)),
        grace_minutes=_positive_int(
            config.get("silence_grace_minutes"), DEFAULT_SILENCE_GRACE_MINUTES
        ),
        nudge_minutes=_positive_int(
            config.get("silence_nudge_minutes"), DEFAULT_SILENCE_NUDGE_MINUTES
        ),
        stop_minutes=_positive_int(
            config.get("silence_stop_minutes"), DEFAULT_SILENCE_STOP_MINUTES
        ),
        warn_lead_minutes=_positive_int(
            config.get("silence_warn_lead_minutes"), DEFAULT_SILENCE_WARN_LEAD_MINUTES
        ),
    )


def format_duration(seconds: int) -> str:
    """ "3 hours" / "1 hour 30 minutes" / "10 minutes"."""
    hours, minutes = divmod(max(seconds, 0) // 60, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes or not hours:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    return " ".join(parts)


def decide(
    elapsed_seconds: int,
    silence_seconds: int,
    config: AutoStopConfig,
    *,
    announced_stage: int = STAGE_NOTHING,
) -> AutoStopDecision:
    """Decide what a recording at *elapsed_seconds* should do now.

    *silence_seconds* is how long the current unbroken quiet stretch has run,
    or 0 when there is sound. *announced_stage* is the highest stage already
    acted on for this stretch; the caller resets it to ``STAGE_NOTHING`` the
    moment sound returns.
    """
    limit = config.max_minutes * 60
    if limit and elapsed_seconds >= limit:
        # Stopping is not instant — the recorder has to be told, ffmpeg has to
        # exit and the segments have to be merged — and ticks keep arriving
        # meanwhile, so the cap has to be announced once like every other rung.
        if announced_stage >= STAGE_STOPPED:
            return AutoStopDecision()
        return AutoStopDecision(
            STOP,
            f"Recording stopped automatically after {format_duration(limit)}. "
            "The limit is in Settings → General.",
        )

    if not config.silence_enabled:
        return AutoStopDecision()
    if elapsed_seconds < config.grace_minutes * 60:
        return AutoStopDecision()

    quiet = format_duration(silence_seconds)

    if config.stop_minutes and silence_seconds >= config.stop_minutes * 60:
        if announced_stage < STAGE_STOPPED:
            return AutoStopDecision(
                STOP,
                f"No sound for {quiet}. Recording stopped and sent for processing.",
            )
        return AutoStopDecision()

    # With no stop threshold there is nothing to warn about, so the ladder
    # stops at the nudge rather than warning about a stop that never comes.
    if (
        config.stop_minutes
        and silence_seconds >= config.warn_minutes * 60
        and announced_stage < STAGE_WARNED
    ):
        remaining = config.stop_minutes * 60 - silence_seconds
        return AutoStopDecision(
            WARN,
            f"No sound for {quiet}. Recording will stop in about "
            f"{format_duration(remaining)} unless something is heard.",
        )

    if silence_seconds >= config.nudge_minutes * 60 and announced_stage < STAGE_NUDGED:
        return AutoStopDecision(
            NUDGE,
            f"Nobody has spoken for {quiet} and the recorder is still running.",
        )

    return AutoStopDecision()
