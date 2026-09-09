"""
Cutting one over-long recording into the meetings it actually contains.

A recorder left running overnight produces a single file holding several
meetings with dead hours between them. Splitting it turns those hours back
into ordinary library entries: each part becomes its own meeting folder, dated
from where it starts in the original, and can then be transcribed, titled and
filed on its own.

The original is never touched. Splitting writes new folders beside it and
leaves the source where it is, because the analysis is a guess about where one
conversation ended and another began, and a wrong guess must cost nothing.

``processing/silence_map.py`` decides *where* to cut; this module carries it
out. The ffmpeg runner is injectable, so the bookkeeping is testable without
touching audio.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..processing.silence_map import (
    DEFAULT_SPLIT_GAP_SECONDS,
    Part,
    SilenceRun,
    build_analyse_command,
    build_extract_command,
    describe_plan,
    parse_duration,
    parse_silence_runs,
    split_parts,
)
from ..utils.filename import sanitize_title

logger = logging.getLogger(__name__)

RunFn = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

# Folder names are "YYYY-MM-DD_HH-MM" with an optional "_title" after them.
_FOLDER_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_(.*))?$")


@dataclass(frozen=True)
class Analysis:
    """What one pass of silencedetect found in a recording."""

    duration: float = 0.0
    runs: list[SilenceRun] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    error: str = ""

    @property
    def would_split(self) -> bool:
        return len(self.parts) > 1

    def summary(self, gap_seconds: float = DEFAULT_SPLIT_GAP_SECONDS) -> str:
        if self.error:
            return self.error
        return describe_plan(self.parts, self.runs, self.duration, gap_seconds=gap_seconds)


def parse_folder_start(folder_name: str) -> tuple[datetime | None, str]:
    """Read the timestamp and title out of a meeting folder's name.

    Returns ``(None, "")`` for a folder that does not follow the convention,
    which is the signal to fall back on the file's modification time rather
    than invent a date.
    """
    match = _FOLDER_STAMP.match(folder_name)
    if not match:
        return None, ""
    year, month, day, hour, minute, title = match.groups()
    try:
        started = datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None, ""
    return started, (title or "").replace("_", " ").strip()


def part_folder_name(started: datetime, part: Part, title: str, index: int, count: int) -> str:
    """Name one part's folder, dated from where it begins in the original.

    The part number is kept in the title rather than the timestamp so the
    parts sort in order and read as what they are — pieces of one recording —
    in a library listing.
    """
    when = started + timedelta(seconds=part.start)
    stem = when.strftime("%Y-%m-%d_%H-%M")
    label = f"{title} (part {index} of {count})" if title else f"Part {index} of {count}"
    return f"{stem}_{sanitize_title(label)}"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)


def analyse(
    audio_path: Path,
    run_fn: RunFn = _run,
    gap_seconds: float = DEFAULT_SPLIT_GAP_SECONDS,
) -> Analysis:
    """Measure a recording's silence and work out where it would be cut."""
    if not audio_path.exists():
        return Analysis(error="That recording is missing from disk.")
    try:
        result = run_fn(build_analyse_command(audio_path))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not analyse %s: %s", audio_path, exc)
        return Analysis(error=f"Could not read the recording: {exc}")

    # silencedetect and the Duration header both go to stderr; ffmpeg exits
    # non-zero on a truncated file it nonetheless read, so the output is used
    # whenever there is any.
    text = (result.stderr or "") + (result.stdout or "")
    duration = parse_duration(text)
    if duration <= 0:
        return Analysis(error="Could not work out how long that recording is.")
    runs = parse_silence_runs(text, duration)
    return Analysis(duration=duration, runs=runs, parts=split_parts(runs, duration, gap_seconds))


def split_recording(
    audio_path: Path,
    parts: list[Part],
    *,
    output_folder: Path,
    run_fn: RunFn = _run,
) -> tuple[list[Path], list[str]]:
    """Write each part as its own meeting folder. Returns (folders, errors).

    A part that fails to extract is reported and skipped rather than aborting
    the rest: three of four meetings recovered beats none.
    """
    source_dir = audio_path.parent
    started, title = parse_folder_start(source_dir.name)
    if started is None:
        started = datetime.fromtimestamp(audio_path.stat().st_mtime)
        title = ""

    folders: list[Path] = []
    errors: list[str] = []
    for index, part in enumerate(parts, start=1):
        name = part_folder_name(started, part, title, index, len(parts))
        destination = output_folder / name
        try:
            destination.mkdir(parents=True, exist_ok=True)
            command = build_extract_command(audio_path, part, destination / audio_path.name)
            result = run_fn(command)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"Part {index}: {exc}")
            continue
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            errors.append(f"Part {index}: {detail[-1] if detail else 'ffmpeg failed'}")
            continue
        _copy_metadata(source_dir, destination)
        folders.append(destination)
    return folders, errors


def _copy_metadata(source_dir: Path, destination: Path) -> None:
    """Carry meeting.json across so tags and the title survive the split."""
    meta = source_dir / "meeting.json"
    if not meta.exists():
        return
    try:
        shutil.copy2(meta, destination / "meeting.json")
    except OSError as exc:
        logger.warning("Could not copy meeting.json into %s: %s", destination, exc)
