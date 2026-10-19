"""Tests for splitting one long recording into the meetings it contains."""

import subprocess
from datetime import datetime

from meeting_recorder.processing.silence_map import Part
from meeting_recorder.services.recording_splitter import (
    analyse,
    parse_folder_start,
    part_folder_name,
    split_recording,
)

HOUR = 3600.0


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


FFMPEG_OUTPUT = """Input #0, mp3, from 'recording.mp3':
  Duration: 03:00:00.00, start: 0.000000, bitrate: 62 kb/s
[silencedetect @ 0x1] silence_start: 3600.0
[silencedetect @ 0x1] silence_end: 7200.0 | silence_duration: 3600.0
"""


def _meeting(tmp_path, name="2026-09-08_18-55_SING!_Events_meeting"):
    directory = tmp_path / name
    directory.mkdir(parents=True)
    (directory / "recording.mp3").write_bytes(b"ID3audio")
    (directory / "meeting.json").write_text('{"title": "SING! Events meeting"}')
    return directory


class TestParseFolderStart:
    def test_a_titled_folder_yields_both(self):
        started, title = parse_folder_start("2026-09-08_18-55_SING!_Events_meeting")
        assert started == datetime(2026, 9, 8, 18, 55)
        assert title == "SING! Events meeting"

    def test_an_untitled_folder_yields_only_the_time(self):
        started, title = parse_folder_start("2026-09-08_18-55")
        assert started == datetime(2026, 9, 8, 18, 55)
        assert title == ""

    def test_a_folder_that_breaks_the_convention_yields_nothing(self):
        assert parse_folder_start("some-other-folder") == (None, "")

    def test_an_impossible_date_yields_nothing(self):
        assert parse_folder_start("2026-13-45_99-99_x") == (None, "")


class TestPartFolderName:
    def test_the_part_is_dated_from_where_it_starts(self):
        name = part_folder_name(
            datetime(2026, 9, 8, 18, 55), Part(2 * HOUR, 3 * HOUR), "Events", 2, 3
        )
        assert name.startswith("2026-09-08_20-55_")

    def test_the_part_number_is_in_the_title(self):
        name = part_folder_name(datetime(2026, 9, 8, 18, 55), Part(0.0, HOUR), "Events", 1, 3)
        assert "part_1_of_3" in name

    def test_an_untitled_recording_still_names_its_parts(self):
        name = part_folder_name(datetime(2026, 9, 8, 18, 55), Part(0.0, HOUR), "", 1, 2)
        assert name == "2026-09-08_18-55_Part_1_of_2"

    def test_the_first_part_keeps_the_original_start_time(self):
        name = part_folder_name(datetime(2026, 9, 8, 18, 55), Part(0.0, HOUR), "Events", 1, 2)
        assert name.startswith("2026-09-08_18-55_")


class TestAnalyse:
    def test_a_missing_file_is_reported(self, tmp_path):
        result = analyse(tmp_path / "absent.mp3")
        assert "missing from disk" in result.error
        assert result.would_split is False

    def test_the_duration_and_runs_are_read(self, tmp_path):
        audio = _meeting(tmp_path) / "recording.mp3"
        result = analyse(audio, lambda _c: _result(stderr=FFMPEG_OUTPUT))
        assert result.duration == 3 * HOUR
        assert len(result.runs) == 1
        assert len(result.seams) == 1
        assert result.would_split is True

    def test_a_file_ffmpeg_cannot_measure_is_reported(self, tmp_path):
        audio = _meeting(tmp_path) / "recording.mp3"
        result = analyse(audio, lambda _c: _result(stderr="garbage"))
        assert "how long" in result.error

    def test_output_is_used_even_when_ffmpeg_exits_non_zero(self, tmp_path):
        # A truncated file still yields a usable analysis.
        audio = _meeting(tmp_path) / "recording.mp3"
        result = analyse(audio, lambda _c: _result(returncode=1, stderr=FFMPEG_OUTPUT))
        assert result.duration == 3 * HOUR

    def test_a_missing_ffmpeg_is_reported(self, tmp_path):
        audio = _meeting(tmp_path) / "recording.mp3"

        def run(_cmd):
            raise FileNotFoundError("ffmpeg")

        assert "Could not read the recording" in analyse(audio, run).error

    def test_the_summary_describes_the_split(self, tmp_path):
        audio = _meeting(tmp_path) / "recording.mp3"
        result = analyse(audio, lambda _c: _result(stderr=FFMPEG_OUTPUT))
        assert "Suggested split into 2 meetings" in result.summary()

    def test_the_summary_reports_an_error_instead(self, tmp_path):
        assert "missing from disk" in analyse(tmp_path / "absent.mp3").summary()


class TestSplitRecording:
    def test_each_part_becomes_its_own_folder(self, tmp_path):
        directory = _meeting(tmp_path)
        parts = [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR)]
        folders, errors = split_recording(
            directory / "recording.mp3",
            parts,
            output_folder=tmp_path,
            run_fn=lambda _c: _result(),
        )
        assert errors == []
        assert len(folders) == 2
        assert all(f.exists() for f in folders)
        assert folders[0].name.startswith("2026-09-08_18-55_")
        assert folders[1].name.startswith("2026-09-08_20-55_")

    def test_the_original_is_left_alone(self, tmp_path):
        directory = _meeting(tmp_path)
        split_recording(
            directory / "recording.mp3",
            [Part(0.0, HOUR)],
            output_folder=tmp_path,
            run_fn=lambda _c: _result(),
        )
        assert (directory / "recording.mp3").exists()

    def test_metadata_travels_with_each_part(self, tmp_path):
        directory = _meeting(tmp_path)
        folders, _ = split_recording(
            directory / "recording.mp3",
            [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR)],
            output_folder=tmp_path,
            run_fn=lambda _c: _result(),
        )
        for folder in folders:
            assert (folder / "meeting.json").read_text() == '{"title": "SING! Events meeting"}'

    def test_a_missing_metadata_file_is_not_fatal(self, tmp_path):
        directory = _meeting(tmp_path)
        (directory / "meeting.json").unlink()
        folders, errors = split_recording(
            directory / "recording.mp3",
            [Part(0.0, HOUR)],
            output_folder=tmp_path,
            run_fn=lambda _c: _result(),
        )
        assert errors == []
        assert len(folders) == 1

    def test_the_extract_command_carries_the_part_bounds(self, tmp_path):
        directory = _meeting(tmp_path)
        seen = []
        split_recording(
            directory / "recording.mp3",
            [Part(60.0, 120.0)],
            output_folder=tmp_path,
            run_fn=lambda cmd: (seen.append(cmd), _result())[1],
        )
        assert seen[0][seen[0].index("-ss") + 1] == "60.000"
        assert seen[0][seen[0].index("-to") + 1] == "120.000"

    def test_a_failed_part_is_reported_and_the_rest_continue(self, tmp_path):
        directory = _meeting(tmp_path)
        calls = {"n": 0}

        def run(_cmd):
            calls["n"] += 1
            return _result(returncode=1, stderr="disk full") if calls["n"] == 1 else _result()

        folders, errors = split_recording(
            directory / "recording.mp3",
            [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR)],
            output_folder=tmp_path,
            run_fn=run,
        )
        assert len(folders) == 1
        assert "disk full" in errors[0]

    def test_a_folder_that_breaks_the_convention_falls_back_to_the_file_time(self, tmp_path):
        directory = _meeting(tmp_path, name="not-a-meeting-folder")
        folders, errors = split_recording(
            directory / "recording.mp3",
            [Part(0.0, HOUR)],
            output_folder=tmp_path,
            run_fn=lambda _c: _result(),
        )
        assert errors == []
        assert folders[0].name.endswith("Part_1_of_1")
