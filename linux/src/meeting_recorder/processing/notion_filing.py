"""
Filing a finished meeting into a Notion page.

The order matters and is the reason this is one function rather than four calls
at the UI layer: the notes and transcript go up first, because they are what
the meeting is *for*, and the audio last, because it is the part most likely to
be refused — too large for the workspace's plan, or for a single-part upload.
A refused upload leaves the notes already on the page and reports what happened
instead of unwinding, since a page with notes and no audio is worth more than
no page at all.

The client is injected, so the whole workflow is testable without a token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..services.notion_service import NotionClient, NotionError, NotionPage
from .notion_blocks import Block, chunk_blocks, markdown_to_blocks, transcript_toggle

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


@dataclass
class FilingResult:
    """What reached Notion, and what did not."""

    page: NotionPage
    notes_written: bool = False
    transcript_written: bool = False
    audio_attached: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing had to be skipped."""
        return not self.warnings


def read_text(path: Path | None) -> str:
    """Read a Markdown file, returning empty text when it is missing.

    A meeting can legitimately have notes without a transcript, or neither —
    an audio-only recording saved after a failed pipeline — and filing one of
    those must still work.
    """
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return ""


def build_body(notes: str, transcript: str, transcript_label: str = "Transcript") -> list[Block]:
    """Assemble the blocks appended to the page: notes first, transcript folded."""
    blocks: list[Block] = []
    if notes.strip():
        blocks.extend(markdown_to_blocks(notes))
    if transcript.strip():
        blocks.append(transcript_toggle(transcript, transcript_label))
    return blocks


def file_meeting(
    client: NotionClient,
    page: NotionPage,
    *,
    notes_path: Path | None = None,
    transcript_path: Path | None = None,
    audio_path: Path | None = None,
    files_property: str = "",
    transcript_label: str = "Transcript",
    on_progress: ProgressFn | None = None,
) -> FilingResult:
    """Write a meeting's notes, transcript and audio onto an existing page."""

    def progress(message: str) -> None:
        logger.info("%s", message)
        if on_progress:
            on_progress(message)

    result = FilingResult(page=page)

    notes = read_text(notes_path)
    transcript = read_text(transcript_path)
    body = build_body(notes, transcript, transcript_label)
    if body:
        progress("Writing notes to Notion…")
        client.append_blocks(page.id, chunk_blocks(body))
        result.notes_written = bool(notes.strip())
        result.transcript_written = bool(transcript.strip())
    else:
        result.warnings.append("This meeting has no notes or transcript to file.")

    if audio_path is None or not audio_path.exists():
        return result

    if not files_property:
        result.warnings.append(
            "No files property is set for this workspace, so the audio was not attached. "
            "Choose one in Settings → Notion."
        )
        return result

    progress("Uploading the recording…")
    try:
        upload_id = client.upload_file(audio_path)
        client.attach_file(page.id, files_property, upload_id)
        result.audio_attached = True
    except NotionError as exc:
        # The notes are already on the page; losing them to re-raise here would
        # be a worse outcome than filing without the audio.
        logger.warning("Could not attach the recording: %s", exc)
        result.warnings.append(f"The notes were filed, but the recording was not: {exc}")

    return result
