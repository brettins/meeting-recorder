"""Scans the output folder for meetings, reads/writes metadata, handles deletion."""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config.tags import parse_meeting_tags
from .filename import sanitize_title

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"}

# Matches flat folder names like "2026-03-01_14-30" or "2026-03-01_14-30_Some_Title"
_FOLDER_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$")


@dataclass
class Meeting:
    path: Path
    time_label: str
    date: datetime
    title: str | None
    has_notes: bool
    has_transcript: bool
    has_audio: bool
    duration_seconds: int | None  # audio duration in seconds, None if unknown
    tags: list[str] = field(default_factory=list)  # tag names from meeting.json


def find_audio_file(meeting_path: Path) -> Path | None:
    """Return the first audio file in the meeting directory, or None."""
    try:
        for f in meeting_path.iterdir():
            if f.is_file() and f.suffix in _AUDIO_EXTENSIONS:
                return f
    except OSError:
        pass
    return None


def scan_meetings(output_folder: str) -> list[Meeting]:
    """Walk the output folder and return all meetings, newest first.

    Expects a flat structure: <output_folder>/<YYYY-MM-DD_HH-MM[_title]>/
    """
    import os

    root = Path(os.path.expanduser(output_folder))
    if not root.is_dir():
        return []

    meetings: list[Meeting] = []
    for meeting_dir in _iter_dirs(root):
        match = _FOLDER_PATTERN.match(meeting_dir.name)
        if not match:
            continue
        # Skip active recordings / processing
        if (meeting_dir / ".recording").exists():
            continue

        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour, minute = int(match.group(4)), int(match.group(5))
        try:
            dt = datetime(year, month, day, hour, minute)
        except ValueError:
            continue

        meta = read_metadata(meeting_dir)
        audio_files = [
            f for f in meeting_dir.iterdir() if f.is_file() and f.suffix in _AUDIO_EXTENSIONS
        ]
        duration = meta.get("duration_seconds")
        if duration is None and audio_files:
            duration = _probe_audio_duration(audio_files[0])
            if duration is not None:
                try:
                    write_metadata(meeting_dir, {"duration_seconds": duration})
                except OSError as exc:
                    # Caching the duration is an optimisation — never fail a scan for it.
                    logger.warning("Could not cache duration for %s: %s", meeting_dir.name, exc)
        meetings.append(
            Meeting(
                path=meeting_dir,
                time_label=meeting_dir.name,
                date=dt,
                title=meta.get("title"),
                has_notes=(meeting_dir / "notes.md").exists(),
                has_transcript=(meeting_dir / "transcript.md").exists(),
                has_audio=bool(audio_files),
                duration_seconds=int(duration) if duration is not None else None,
                tags=parse_meeting_tags(meta.get("tags")),
            )
        )

    meetings.sort(key=lambda m: m.date, reverse=True)
    return meetings


def _iter_dirs(parent: Path) -> list[Path]:
    """Yield subdirectories of parent, ignoring errors."""
    try:
        return [p for p in parent.iterdir() if p.is_dir()]
    except OSError:
        return []


def _probe_audio_duration(audio_path: Path) -> int | None:
    """Get audio duration in seconds using ffprobe. Returns None on failure."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return None


def _read_metadata_checked(meeting_path: Path) -> dict[str, Any] | None:
    """Read meeting.json, distinguishing "no file" from "could not read it".

    Returns {} when there is no metadata file yet, the parsed dict when the file
    reads cleanly, and None when a file exists but is unreadable or malformed.
    Callers that are about to overwrite need that distinction; callers that only
    want values can flatten None to {}.
    """
    meta_file = meeting_path / "meeting.json"
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def read_metadata(meeting_path: Path) -> dict[str, Any]:
    """Read meeting.json from the meeting directory. Returns {} if missing/malformed."""
    return _read_metadata_checked(meeting_path) or {}


def write_metadata(meeting_path: Path, metadata: dict[str, Any]) -> None:
    """Merge *metadata* into meeting.json.

    Raises OSError rather than overwriting a meeting.json that exists but cannot
    be read. Merging onto the {} that a failed read produces would replace the
    meeting's title and duration with only the keys being written, so a
    transient read error would silently cost the user data — and tags write here
    on every edit, so that path is now well travelled.
    """
    existing = _read_metadata_checked(meeting_path)
    if existing is None:
        raise OSError(
            f"Refusing to overwrite unreadable metadata at {meeting_path / 'meeting.json'}"
        )
    existing.update(metadata)
    meta_file = meeting_path / "meeting.json"
    meta_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def delete_meetings(
    meetings: list[Meeting],
    output_folder: str = "~/meetings",
) -> tuple[list[Meeting], list[tuple[Meeting, str]]]:
    """Delete meeting directories. Returns (succeeded, failures)."""
    succeeded: list[Meeting] = []
    failures: list[tuple[Meeting, str]] = []

    for meeting in meetings:
        try:
            shutil.rmtree(meeting.path)
            succeeded.append(meeting)
        except Exception as exc:
            failures.append((meeting, str(exc)))

    return succeeded, failures


def rename_meeting_path(meeting_dir: Path, new_title: str) -> Path:
    """Rename a meeting directory to {YYYY-MM-DD_HH-MM}_{sanitized_title}. Returns new path."""
    match = _FOLDER_PATTERN.match(meeting_dir.name)
    if not match:
        raise ValueError(f"Cannot parse date-time from folder name: {meeting_dir.name}")

    date_time_part = (
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}_{match.group(4)}-{match.group(5)}"
    )
    safe_title = sanitize_title(new_title)
    new_name = f"{date_time_part}_{safe_title}"
    new_path = meeting_dir.parent / new_name

    # Handle collision
    if new_path.exists() and new_path != meeting_dir:
        counter = 2
        while True:
            candidate = meeting_dir.parent / f"{new_name}_{counter}"
            if not candidate.exists():
                new_path = candidate
                break
            counter += 1

    meeting_dir.rename(new_path)
    return new_path


def rename_meeting_dir(meeting: Meeting, new_title: str) -> Path:
    """Rename meeting folder to {HH-MM}_{sanitized_title}. Returns new path."""
    return rename_meeting_path(meeting.path, new_title)


def set_meeting_tags(meeting_path: Path, tags: list[str]) -> None:
    """Persist the tag names assigned to a meeting."""
    write_metadata(meeting_path, {"tags": parse_meeting_tags(tags)})
