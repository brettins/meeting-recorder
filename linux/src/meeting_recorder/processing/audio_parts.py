"""Cutting a long recording into parts for transcription, and joining the transcripts back up."""

from __future__ import annotations

import re
from pathlib import Path

from ..services.recording_splitter import RunFn
from .silence_map import Part, build_extract_command

PART_SECONDS = 15 * 60
WHOLE_FILE_MAX_SECONDS = 20 * 60
MIN_TAIL_SECONDS = 60

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2}):(\d{2})\]")
_SPEAKER = re.compile(r"\*\*([^*:]+):\*\*")
_UNNAMED = re.compile(r"Person \d+")


def plan_parts(duration: float, part_seconds: int = PART_SECONDS) -> list[Part]:
    """Spans of *part_seconds* covering *duration*; one span when it is short or unknown."""
    if duration <= WHOLE_FILE_MAX_SECONDS:
        return [Part(0.0, max(duration, 0.0))]
    count = int(duration // part_seconds) + (1 if duration % part_seconds else 0)
    if count > 1 and duration - (count - 1) * part_seconds < MIN_TAIL_SECONDS:
        count -= 1
    return [
        Part(float(i * part_seconds), duration if i == count - 1 else float((i + 1) * part_seconds))
        for i in range(count)
    ]


def extract_parts(source: Path, parts: list[Part], directory: Path, run_fn: RunFn) -> list[Path]:
    """Cut each span out of *source* into *directory*; raises RuntimeError if ffmpeg fails."""
    paths: list[Path] = []
    for index, part in enumerate(parts, 1):
        destination = directory / f"part{index:03d}{source.suffix or '.mp3'}"
        result = run_fn(build_extract_command(source, part, destination))
        if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
            detail = (result.stderr or "").strip().splitlines()
            raise RuntimeError(f"ffmpeg could not cut part {index}: {detail[-1] if detail else ''}")
        paths.append(destination)
    return paths


def format_clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def shift_timestamps(text: str, offset_seconds: float) -> str:
    """Move every [HH:MM:SS] stamp in *text* forward by *offset_seconds*."""

    def shift(match: re.Match[str]) -> str:
        h, m, s = (int(g) for g in match.groups())
        return f"[{format_clock(h * 3600 + m * 60 + s + offset_seconds)}]"

    return _TIMESTAMP.sub(shift, text)


def strip_preamble(text: str) -> str:
    """Drop a chatty line ("Sure, here is the transcript:") ahead of the first stamped turn."""
    lines = text.strip().splitlines()
    for index, line in enumerate(lines[:3]):
        if line.startswith("["):
            return "\n".join(lines[index:]).strip()
    return text.strip()


def named_speakers(text: str) -> list[str]:
    """Speaker labels in *text* that are real names rather than "Person N", in order."""
    seen: list[str] = []
    for name in _SPEAKER.findall(text):
        name = name.strip()
        if name and not _UNNAMED.fullmatch(name) and name not in seen:
            seen.append(name)
    return seen


def part_prompt(base: str, index: int, count: int, speakers: list[str]) -> str:
    """The transcription prompt, told which part of the recording this is."""
    extra = (
        f"This audio is part {index} of {count} of one longer recording, "
        "and its timestamps start again at [00:00:00]."
    )
    if speakers:
        extra += (
            f" Speakers already identified in earlier parts: {', '.join(speakers)}. "
            "Use the same names for the same voices."
        )
    return f"{base}\n\n{extra}"


def blocked_note(part: Part) -> str:
    """The transcript line standing in for a part Gemini's copyright filter would not return."""
    return (
        f"[{format_clock(part.start)}] **Note:** Gemini's copyright filter blocked "
        f"{format_clock(part.start)} to {format_clock(part.end)} of this recording, "
        "so that stretch is not transcribed."
    )
