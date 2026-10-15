"""Tests for cutting a recording into parts and joining the transcripts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_recorder.processing.audio_parts import (
    blocked_note,
    extract_parts,
    named_speakers,
    part_prompt,
    plan_parts,
    shift_timestamps,
    strip_preamble,
)
from meeting_recorder.processing.silence_map import Part


def test_short_or_unknown_recording_is_one_span():
    assert plan_parts(0.0) == [Part(0.0, 0.0)]
    assert plan_parts(19 * 60) == [Part(0.0, 19 * 60)]


def test_long_recording_is_cut_every_fifteen_minutes():
    parts = plan_parts(10334)
    assert len(parts) == 12
    assert parts[0] == Part(0.0, 900.0)
    assert parts[-1] == Part(900.0 * 11, 10334)
    assert all(a.end == b.start for a, b in zip(parts, parts[1:], strict=False))


def test_a_sliver_at_the_end_joins_the_previous_part():
    parts = plan_parts(2 * 900 + 20 + 900)
    assert len(parts) == 3
    parts = plan_parts(3 * 900 + 30)
    assert len(parts) == 3
    assert parts[-1].end == 3 * 900 + 30


def test_shift_timestamps_carries_into_hours():
    text = "[00:14:58] **A:** hi\n[00:15:03] **B:** there"
    assert shift_timestamps(text, 3600) == "[01:14:58] **A:** hi\n[01:15:03] **B:** there"


def test_shift_timestamps_leaves_other_brackets_alone():
    assert shift_timestamps("[inaudible] and [1:2]", 60) == "[inaudible] and [1:2]"


def test_strip_preamble_drops_the_chatty_line():
    text = "Sure, here's the transcription:\n\n[00:00:00] **A:** hi"
    assert strip_preamble(text) == "[00:00:00] **A:** hi"


def test_strip_preamble_keeps_text_that_starts_with_a_turn():
    assert strip_preamble("[00:00:00] **A:** hi") == "[00:00:00] **A:** hi"


def test_strip_preamble_keeps_text_without_any_stamp():
    assert strip_preamble("just words") == "just words"


def test_named_speakers_skips_person_labels_and_dedupes():
    text = "[00:00:00] **Ryan:** a\n[00:00:01] **Person 2:** b\n[00:00:02] **Ryan:** c\n**Dana:** d"
    assert named_speakers(text) == ["Ryan", "Dana"]


def test_part_prompt_names_the_part_and_earlier_speakers():
    prompt = part_prompt("BASE", 2, 4, ["Ryan", "Dana"])
    assert prompt.startswith("BASE")
    assert "part 2 of 4" in prompt
    assert "Ryan, Dana" in prompt


def test_part_prompt_without_speakers_omits_the_sentence():
    assert "already identified" not in part_prompt("BASE", 1, 4, [])


def test_blocked_note_carries_the_start_stamp():
    note = blocked_note(Part(1800.0, 2700.0))
    assert note.startswith("[00:30:00]")
    assert "00:30:00 to 00:45:00" in note


def _ffmpeg_that_writes(returncode: int = 0, write: bool = True):
    calls: list[list[str]] = []

    def run(command: list[str]):
        calls.append(command)
        if write:
            Path(command[-1]).write_bytes(b"audio")
        return SimpleNamespace(returncode=returncode, stderr="boom", stdout="")

    return run, calls


def test_extract_parts_cuts_each_span(tmp_path: Path):
    run, calls = _ffmpeg_that_writes()
    parts = [Part(0.0, 900.0), Part(900.0, 1200.0)]

    paths = extract_parts(tmp_path / "rec.mp3", parts, tmp_path, run)

    assert [p.name for p in paths] == ["part001.mp3", "part002.mp3"]
    assert "-c" in calls[0] and "copy" in calls[0]
    assert "900.000" in calls[1]


def test_extract_parts_raises_when_ffmpeg_fails(tmp_path: Path):
    run, _ = _ffmpeg_that_writes(returncode=1, write=False)
    with pytest.raises(RuntimeError, match="could not cut part 1: boom"):
        extract_parts(tmp_path / "rec.mp3", [Part(0.0, 900.0)], tmp_path, run)
