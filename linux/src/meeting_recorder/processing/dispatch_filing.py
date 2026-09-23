"""Filing a finished meeting into Dispatch: the payload, and the sentence afterwards."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..services.dispatch_service import FiledMeeting
from .action_items import extract_actions
from .notion_filing import read_text

_FOLDER_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})")


class MeetingLike(Protocol):
    path: Path
    date: datetime
    title: str | None
    duration_seconds: int | None


class Filer(Protocol):
    def file_meeting(self, payload: dict[str, Any]) -> FiledMeeting: ...


def meeting_id_for(path: Path, date: datetime) -> str:
    """The minute the recording started, which a rename or a move leaves alone."""
    stamp = _FOLDER_STAMP.match(path.name)
    if stamp:
        year, month, day, hour, minute = stamp.groups()
        return f"{year}-{month}-{day}T{hour}:{minute}"
    return date.strftime("%Y-%m-%dT%H:%M")


def meeting_title(title: str | None, date: datetime) -> str:
    """The meeting's title, or its start time when it was never named."""
    if title and title.strip():
        return title.strip()
    return f"Meeting {date.strftime('%b %d, %Y %H:%M')}"


def build_payload(
    meeting: MeetingLike,
    *,
    notes: str,
    transcript: str,
    actions: list[str],
    company: str = "",
) -> dict[str, Any]:
    """The body ``POST /api/meetings`` takes. The date carries the local offset."""
    payload: dict[str, Any] = {
        "meeting_id": meeting_id_for(meeting.path, meeting.date),
        "title": meeting_title(meeting.title, meeting.date),
        "date": meeting.date.astimezone().isoformat(timespec="minutes"),
        "duration_seconds": int(meeting.duration_seconds or 0),
        "notes": notes,
        "transcript": transcript,
        "actions": list(actions),
    }
    if company.strip():
        payload["company"] = company.strip()
    return payload


def read_meeting(path: Path) -> tuple[str, str]:
    """(notes, transcript) for a meeting folder; missing files read as empty."""
    return read_text(path / "notes.md"), read_text(path / "transcript.md")


def proposed_actions(notes: str) -> list[str]:
    """The action lines a filing would turn into draft tasks."""
    return extract_actions(notes)


def file_meeting(
    client: Filer,
    meeting: MeetingLike,
    *,
    actions: list[str] | None = None,
    company: str = "",
) -> FiledMeeting:
    """Read the meeting from disk and file it. *actions* overrides the parsed lines."""
    notes, transcript = read_meeting(meeting.path)
    chosen = proposed_actions(notes) if actions is None else actions
    payload = build_payload(
        meeting, notes=notes, transcript=transcript, actions=chosen, company=company
    )
    return client.file_meeting(payload)


def filed_summary(filed: FiledMeeting) -> str:
    """One sentence for the notice after filing."""
    name = filed.key or "the meeting"
    new = filed.new_tasks
    tasks = "no draft tasks" if new == 0 else f"{new} draft task{'s' if new != 1 else ''}"
    if filed.created:
        return f"Filed into Dispatch as {name}, with {tasks} in triage."
    if new:
        return f"Updated {name} in Dispatch, and added {tasks} to triage."
    if filed.tasks:
        return f"Updated {name} in Dispatch. Its tasks were already there."
    return f"Updated {name} in Dispatch."
