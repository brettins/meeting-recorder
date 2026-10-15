"""
Finding the silence in a finished recording, and deciding what to do about it.

Two problems share one measurement. A recorder left running overnight holds
two meetings with a dead stretch between them, and wants **splitting**. And
almost any recording carries long quiet passages that cost upload time and
model budget for nothing, and wants **trimming** before it is sent. Both start
from the same pass of ffmpeg's ``silencedetect`` over the file.

The split is deliberately conservative. A gap has to be long enough that it
cannot be a pause in a conversation — half an hour by default — because
splitting a meeting that merely went quiet would scatter one discussion across
two sets of notes, which is worse than leaving a long recording alone. Trimming
is the opposite: it never changes what was said, only how much dead air travels
with it, so it applies to everything.

Everything here is pure except the two command builders' callers: analysis
parses text, planning is arithmetic, and the ffmpeg invocations are returned as
argument lists for someone else to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core.auto_stop import format_duration

# Quiet enough to be nobody talking, for long enough to be worth noticing.
ANALYSIS_THRESHOLD_DB = -45
ANALYSIS_MIN_SILENCE_SECONDS = 20

# A gap this long is a seam between two meetings rather than a pause in one.
DEFAULT_SPLIT_GAP_SECONDS = 30 * 60

# Keep a little audio either side of a seam so a word at the edge is not clipped.
SPLIT_PAD_SECONDS = 2.0

# A fragment shorter than this is a stray noise, not a meeting.
MIN_PART_SECONDS = 60.0

# Trimming leaves this much of each silence in place, so speech does not butt
# together and the transcript keeps some sense of pacing.
TRIM_KEEP_SECONDS = 1.0
TRIM_MIN_SILENCE_SECONDS = 3.0

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


@dataclass(frozen=True)
class SilenceRun:
    """One quiet stretch, in seconds from the start of the file."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)


@dataclass(frozen=True)
class Part:
    """One span of a recording that should become a meeting of its own."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)


def build_analyse_command(path: Path | str) -> list[str]:
    """ffmpeg invocation that reports the file's silence and decodes nothing."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        f"silencedetect=n={ANALYSIS_THRESHOLD_DB}dB:d={ANALYSIS_MIN_SILENCE_SECONDS}",
        "-f",
        "null",
        "-",
    ]


def parse_duration(text: str) -> float:
    """Read the ``Duration: HH:MM:SS.xx`` line ffmpeg prints, or 0.0."""
    match = _DURATION.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_silence_runs(text: str, total_duration: float = 0.0) -> list[SilenceRun]:
    """Pair up silencedetect's start and end lines into runs.

    A file that ends while still quiet produces a start with no end; that run
    is closed at *total_duration* when one is known, and dropped when it is not
    — an unbounded run would otherwise look like the longest gap in the file.
    """
    runs: list[SilenceRun] = []
    open_start: float | None = None
    for line in text.splitlines():
        end = _END.search(line)
        if end:
            if open_start is not None:
                runs.append(SilenceRun(open_start, float(end.group(1))))
                open_start = None
            continue
        start = _START.search(line)
        if start:
            # Two starts without an end between them: keep the first, which is
            # when the quiet actually began.
            if open_start is None:
                open_start = max(float(start.group(1)), 0.0)
    if open_start is not None and total_duration > open_start:
        runs.append(SilenceRun(open_start, total_duration))
    return runs


def total_silence(runs: list[SilenceRun]) -> float:
    return sum(run.duration for run in runs)


def split_parts(
    runs: list[SilenceRun],
    total_duration: float,
    gap_seconds: float = DEFAULT_SPLIT_GAP_SECONDS,
) -> list[Part]:
    """The spans a recording should be cut into, or one span if it should not.

    Returns a single part covering the whole file when nothing is long enough
    to be a seam, so a caller can treat "no split" and "split" the same way.
    """
    if total_duration <= 0:
        return []
    seams = [run for run in runs if run.duration >= gap_seconds]
    if not seams:
        return [Part(0.0, total_duration)]

    parts: list[Part] = []
    cursor = 0.0
    for seam in seams:
        end = min(seam.start + SPLIT_PAD_SECONDS, total_duration)
        if end - cursor >= MIN_PART_SECONDS:
            parts.append(Part(cursor, end))
        cursor = max(seam.end - SPLIT_PAD_SECONDS, cursor)
    if total_duration - cursor >= MIN_PART_SECONDS:
        parts.append(Part(cursor, total_duration))
    return parts or [Part(0.0, total_duration)]


def describe_plan(
    parts: list[Part],
    runs: list[SilenceRun],
    total_duration: float,
    *,
    gap_seconds: float = DEFAULT_SPLIT_GAP_SECONDS,
) -> str:
    """The sentence shown before anything is changed.

    Says what will happen in terms of what the user will see afterwards — how
    many meetings, how long each is — rather than in terms of silence runs.
    """
    if total_duration <= 0:
        return "This recording could not be read."
    silence = total_silence(runs)
    kept = format_duration(int(total_duration - silence))
    if len(parts) <= 1:
        if silence <= 0:
            return f"{format_duration(int(total_duration))} of audio, no long gaps."
        return (
            f"{format_duration(int(total_duration))} long, of which "
            f"{format_duration(int(silence))} is silence. No gap is longer than "
            f"{format_duration(int(gap_seconds))}, so this stays one meeting. "
            f"About {kept} would be sent for transcription."
        )
    lengths = ", ".join(format_duration(int(p.duration)) for p in parts)
    return (
        f"{format_duration(int(total_duration))} long, with "
        f"{len(parts) - 1} gap{'s' if len(parts) != 2 else ''} over "
        f"{format_duration(int(gap_seconds))}. Splitting into {len(parts)} meetings "
        f"of {lengths}. About {kept} would be sent for transcription."
    )


def build_probe_command(path: Path | str) -> list[str]:
    """ffprobe invocation that reports one number: the file's length."""
    return [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]


def build_extract_command(
    source: Path | str, part: Part, destination: Path | str, *, to_end: bool = False
) -> list[str]:
    """Copy one span out of the recording without re-encoding it.

    ``-c copy`` keeps the original audio exactly, so splitting costs seconds
    rather than a full re-encode and loses no quality. The seek goes before
    ``-i`` so ffmpeg jumps rather than decoding up to the start point. With
    ``to_end`` the cut runs to the end of the file, whatever its header says.
    """
    stop = [] if to_end else ["-to", f"{part.end:.3f}"]
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{part.start:.3f}",
        *stop,
        "-i",
        str(source),
        "-c",
        "copy",
        str(destination),
    ]


def build_trim_command(source: Path | str, destination: Path | str) -> list[str]:
    """Strip long silences out of a copy that is about to be uploaded.

    ``stop_periods=-1`` applies the rule to every silence in the file rather
    than only the leading one, and ``stop_duration`` leaves a beat in place so
    the speech does not run together.
    """
    trim = (
        f"silenceremove=stop_periods=-1"
        f":stop_duration={TRIM_KEEP_SECONDS}"
        f":stop_threshold={ANALYSIS_THRESHOLD_DB}dB"
        f":detection=rms"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        trim,
        "-c:a",
        "libmp3lame",
        "-q:a",
        "5",
        str(destination),
    ]
