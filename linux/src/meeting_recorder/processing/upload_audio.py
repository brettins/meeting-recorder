"""
Stripping dead air out of a recording before it is transcribed.

A recording is mostly silence far more often than not — the 16-hour one in
this library was 87% quiet — and every silent minute costs upload time, model
budget, and output tokens that a long transcription cannot spare. Removing it
changes nothing about what was said.

The trimmed audio is a **temporary copy**. The original stays in the library
untouched, because it is the recording; this is only what gets sent. If
anything about the trim goes wrong the original is used instead, since a
larger upload is a far better outcome than a failed job.

Trimming is skipped when it would not pay for itself: re-encoding a file to
save thirty seconds is worse than sending it as it is.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.auto_stop import format_duration
from ..services.recording_splitter import RunFn, analyse
from .silence_map import build_probe_command, build_trim_command

logger = logging.getLogger(__name__)

DEFAULT_MIN_SAVING_SECONDS = 60

StatusFn = Callable[[str], None]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)


@dataclass
class PreparedAudio:
    """The file to send, and whether it is a temporary copy to delete after."""

    path: Path
    temporary: bool = False
    note: str = ""

    def cleanup(self) -> None:
        if not self.temporary:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove the trimmed copy %s: %s", self.path, exc)


def trim_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("trim_silence_before_upload", True))


def min_saving_seconds(config: dict[str, Any]) -> int:
    raw = config.get("trim_min_saving_minutes", DEFAULT_MIN_SAVING_SECONDS // 60)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = DEFAULT_MIN_SAVING_SECONDS // 60
    return max(minutes, 0) * 60


def worth_trimming(silence_seconds: float, threshold_seconds: float) -> bool:
    """Whether removing *silence_seconds* justifies a re-encode."""
    return silence_seconds > 0 and silence_seconds >= threshold_seconds


def describe_saving(original: float, trimmed: float) -> str:
    """The status line, measured from the trimmed file rather than predicted.

    silenceremove shortens every pause, not only the long gaps the analysis
    counted, so the file that comes out is shorter than the analysis implies.
    Reporting the prediction would overstate what is being sent.
    """
    removed = max(original - trimmed, 0)
    return (
        f"Removed {format_duration(int(removed))} of silence; "
        f"sending {format_duration(int(trimmed))} of {format_duration(int(original))}."
    )


def probe_duration(path: Path, run_fn: RunFn) -> float:
    """Length of a file in seconds, or 0.0 when it cannot be read."""
    try:
        result = run_fn(build_probe_command(path))
    except Exception as exc:
        logger.warning("Could not measure %s: %s", path, exc)
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return 0.0


def prepare_for_upload(
    audio_path: Path,
    config: dict[str, Any],
    *,
    on_status: StatusFn | None = None,
    run_fn: RunFn = _run,
) -> PreparedAudio:
    """Return the audio to transcribe, trimmed when that is worth doing."""
    if not trim_enabled(config):
        return PreparedAudio(audio_path)

    def status(message: str) -> None:
        logger.info("%s", message)
        if on_status:
            on_status(message)

    status("Measuring silence…")
    analysis = analyse(audio_path, run_fn)
    if analysis.error:
        logger.warning("Skipping the trim: %s", analysis.error)
        return PreparedAudio(audio_path)

    silence = sum(run.duration for run in analysis.runs)
    if not worth_trimming(silence, min_saving_seconds(config)):
        return PreparedAudio(audio_path)

    status("Removing silence…")
    handle = tempfile.NamedTemporaryFile(
        prefix="meeting-trimmed-", suffix=audio_path.suffix or ".mp3", delete=False
    )
    destination = Path(handle.name)
    handle.close()
    try:
        result = run_fn(build_trim_command(audio_path, destination))
    except Exception as exc:
        logger.warning("Could not trim %s: %s", audio_path, exc)
        destination.unlink(missing_ok=True)
        return PreparedAudio(audio_path)

    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        detail = (result.stderr or "").strip().splitlines()
        logger.warning("Trim failed, sending the original: %s", detail[-1] if detail else "")
        destination.unlink(missing_ok=True)
        return PreparedAudio(audio_path)

    trimmed = probe_duration(destination, run_fn)
    if trimmed <= 0:
        # The copy is unreadable to ffprobe, which is reason enough to distrust
        # it: send the recording itself rather than something unverified.
        logger.warning("Trimmed copy could not be measured; sending the original")
        destination.unlink(missing_ok=True)
        return PreparedAudio(audio_path)

    note = describe_saving(analysis.duration, trimmed)
    status(note)
    return PreparedAudio(destination, temporary=True, note=note)
