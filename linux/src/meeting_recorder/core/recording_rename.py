"""
The queued folder rename for a title typed while the recording was running.

The meeting directory is created the moment recording starts, and ffmpeg holds
it open for the whole session (pause/resume writes further segments into it), so
a title entered mid-recording cannot move the folder there and then. The title
is written to ``meeting.json`` immediately — it survives even if nothing else
does — and the rename is deferred until the recorder has released the directory,
which is what this module applies.

``needs_rename`` is pure; ``apply_rename`` is the one IO step, kept here so both
the stop path and the cancel-and-save path go through the same code.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..utils.filename import sanitize_title

logger = logging.getLogger(__name__)

# "2026-03-01_14-30", optionally "_Title", optionally a "_2" collision suffix.
_DIR_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})(?:_(.*))?$")


def needs_rename(dir_name: str, title: str | None) -> bool:
    """Whether *dir_name* still has to be renamed to carry *title*.

    False for an empty title, an unparseable folder name, or a folder that
    already ends in the sanitized title — including the ``_2`` suffix
    ``rename_meeting_path`` adds when it dodges a collision, so re-applying the
    same title never shuffles the folder a second time.
    """
    if not title or not title.strip():
        return False
    match = _DIR_PATTERN.match(dir_name)
    if not match:
        return False
    current = match.group(2) or ""
    wanted = sanitize_title(title)
    if not wanted:
        return False
    return current != wanted and not re.fullmatch(rf"{re.escape(wanted)}_\d+", current)


def apply_rename(
    audio_path: Path,
    transcript_path: Path,
    notes_path: Path,
    title: str | None,
) -> tuple[Path, Path, Path]:
    """Move the meeting folder to carry *title*; return the rebased paths.

    Returns the paths unchanged when there is nothing to do or the rename fails
    — a folder that could not be renamed is a cosmetic problem, and failing the
    recording over it would cost the user the audio.
    """
    meeting_dir = audio_path.parent
    if not needs_rename(meeting_dir.name, title):
        return audio_path, transcript_path, notes_path

    from ..utils.meeting_scanner import rename_meeting_path

    try:
        new_dir = rename_meeting_path(meeting_dir, title or "")
    except (OSError, ValueError) as exc:
        logger.warning("Could not rename %s to %r: %s", meeting_dir.name, title, exc)
        return audio_path, transcript_path, notes_path

    logger.info("Applied queued rename: %s -> %s", meeting_dir.name, new_dir.name)
    return (
        new_dir / audio_path.name,
        new_dir / transcript_path.name,
        new_dir / notes_path.name,
    )
