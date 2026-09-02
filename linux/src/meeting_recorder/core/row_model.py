"""
The single render model behind every meeting row, in the Library and on the
Record tab alike.

There used to be two unrelated list widgets: a "Background Jobs" panel that knew
only about pipeline jobs, and a meeting list that knew only about folders on
disk. They showed the same meetings in two shapes, and a recording moved from
one to the other as it finished. A job is not a different kind of thing from a
meeting — it is a *state* a meeting is in, so both views now render ``RowModel``
through the same widget and differ only in which models they are handed.

Everything here is pure: the join between the daemon's jobs and the scanned
meetings, the subtitle text, and the action policy. ``ui/meeting_row.py`` turns
a ``RowModel`` into widgets and does nothing else.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .job import JobStatus

# Job-state actions.
CANCEL = "cancel"
RETRY = "retry"
DISMISS = "dismiss"
DETAILS = "details"
# Meeting actions.
AI_TITLE = "ai_title"
SUMMARIZE = "summarize"
TAG = "tag"
RENAME = "rename"
OPEN_FOLDER = "open_folder"
DELETE = "delete"

# Row states, in the order a meeting passes through them.
RECORDING = "recording"
PROCESSING = "processing"
ERROR = "error"
READY = "ready"

# Row state -> style class for the status line. These are libadwaita's own
# semantic classes, so the state reads correctly in both themes and no new
# colour value enters the app; assets/style.css stays the only place literal
# colours are defined.
ROW_STATE_CSS: dict[str, str] = {
    RECORDING: "accent",
    PROCESSING: "accent",
    ERROR: "error",
}


@dataclass
class RowModel:
    """Everything one row draws. ``key`` is the meeting directory path."""

    key: str
    title: str
    subtitle: str
    state: str = READY
    tags: list[str] = field(default_factory=list)
    job_id: int | None = None
    error_msg: str | None = None
    actions: tuple[str, ...] = ()
    selectable: bool = False


def format_duration(seconds: int | None) -> str:
    """ "48m" / "1h 12m" / "" when the duration isn't known yet."""
    if seconds is None or seconds < 0:
        return ""
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 60}m"


def meeting_subtitle(when: datetime, duration_seconds: int | None) -> str:
    """The static "Sep 01  ·  2:15 PM  ·  48m" line."""
    parts = [when.strftime("%b %d, %Y"), when.strftime("%I:%M %p").lstrip("0")]
    duration = format_duration(duration_seconds)
    if duration:
        parts.append(duration)
    return "  ·  ".join(parts)


def meeting_actions(
    *,
    has_notes: bool,
    has_transcript: bool,
    has_audio: bool,
    has_title: bool,
    can_summarize: bool,
) -> tuple[str, ...]:
    """Which meeting actions a settled row offers.

    "Generate title" only makes sense once there are notes to generate it from
    and while the meeting is still unnamed; "Transcribe" only when there is
    audio and nothing has been made of it yet.
    """
    actions: list[str] = []
    if has_notes and not has_title:
        actions.append(AI_TITLE)
    if can_summarize and has_audio and not has_transcript and not has_notes:
        actions.append(SUMMARIZE)
    actions.extend((TAG, RENAME, OPEN_FOLDER, DELETE))
    return tuple(actions)


def row_actions(state: str, settled_actions: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Merge the row's job state with the meeting actions underneath it.

    A row being recorded or processed offers only the one action that applies to
    that state, so a half-written folder can't be renamed or deleted out from
    under ffmpeg or the pipeline. A failed row leads with "Details": the subtitle
    holds one line, and API errors put the part that says what to do at the end.
    """
    if state == RECORDING:
        return ()
    if state == PROCESSING:
        return (CANCEL,)
    if state == ERROR:
        return (DETAILS, RETRY, DISMISS, *settled_actions)
    return settled_actions


def state_for_job(status: JobStatus | None) -> str:
    """The row state a job's status puts its meeting in."""
    if status is JobStatus.PROCESSING:
        return PROCESSING
    if status is JobStatus.ERROR:
        return ERROR
    return READY


def index_jobs_by_dir(jobs: Iterable) -> dict[str, object]:
    """Map meeting directory -> JobView, so a scanned meeting finds its job.

    Later jobs win: retrying a meeting creates a fresh row in the daemon's queue
    and the newest one is the state the user is watching.
    """
    return {job.audio_dir: job for job in jobs if getattr(job, "audio_dir", "")}


def row_from_job(job) -> RowModel:
    """A Record-tab row built from a job alone — no folder scan involved."""
    state = state_for_job(job.status)
    return RowModel(
        key=job.audio_dir,
        title=job.label,
        subtitle=_job_subtitle(job, state),
        state=state,
        job_id=job.job_id,
        error_msg=job.error_msg,
        actions=row_actions(state),
    )


def row_from_meeting(meeting, job=None, *, is_recording: bool = False) -> RowModel:
    """A Library row: a scanned meeting, in whatever state its job puts it."""
    state = RECORDING if is_recording else state_for_job(job.status if job else None)
    settled = meeting_actions(
        has_notes=meeting.has_notes,
        has_transcript=meeting.has_transcript,
        has_audio=meeting.has_audio,
        has_title=meeting.title is not None,
        can_summarize=True,
    )
    # A duration probed from a half-written file is noise, so a row that is
    # still recording shows the clock instead of a length.
    duration = None if state == RECORDING else meeting.duration_seconds
    subtitle = meeting_subtitle(meeting.date, duration)
    if state == RECORDING:
        subtitle = f"Recording…  ·  {subtitle}"
    elif job is not None and state != READY:
        subtitle = f"{_job_subtitle(job, state)}  ·  {subtitle}"
    return RowModel(
        key=str(meeting.path),
        title=meeting.title or meeting.time_label,
        subtitle=subtitle,
        state=state,
        tags=list(meeting.tags),
        job_id=job.job_id if job else None,
        error_msg=job.error_msg if job else None,
        actions=row_actions(state, settled),
        selectable=state not in (RECORDING, PROCESSING),
    )


def _job_subtitle(job, state: str) -> str:
    if state == ERROR:
        from .errors import error_summary

        return f"Error: {error_summary(job.error_msg or 'Error')}"
    return getattr(job, "status_text", "") or "Processing…"
