"""Tests for trimming silence out of the copy that gets transcribed."""

import subprocess

from meeting_recorder.processing.upload_audio import (
    DEFAULT_MIN_SAVING_SECONDS,
    PreparedAudio,
    describe_saving,
    min_saving_seconds,
    prepare_for_upload,
    trim_enabled,
    worth_trimming,
)

HOUR = 3600.0

FFMPEG_OUTPUT = """Input #0, mp3, from 'recording.mp3':
  Duration: 02:00:00.00, start: 0.000000, bitrate: 62 kb/s
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 3600.0 | silence_duration: 3600.0
"""

QUIET_OUTPUT = """Input #0, mp3, from 'recording.mp3':
  Duration: 02:00:00.00, start: 0.000000, bitrate: 62 kb/s
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 30.0 | silence_duration: 30.0
"""


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _audio(tmp_path):
    path = tmp_path / "recording.mp3"
    path.write_bytes(b"ID3audio")
    return path


def _runner(analysis_output, trim_result=None, written=True, trimmed_seconds=600.0):
    """Answers the analysis, then the trim, then the probe of the result."""
    calls = {"n": 0, "commands": []}

    def run(command):
        calls["n"] += 1
        calls["commands"].append(command)
        if command[0] == "ffprobe":
            return _result(stdout=f"{trimmed_seconds}\n")
        if "silencedetect" in " ".join(command):
            return _result(stderr=analysis_output)
        if written:
            with open(command[-1], "wb") as handle:
                handle.write(b"trimmed")
        return trim_result or _result()

    run.calls = calls
    return run


class TestSettings:
    def test_trimming_is_on_by_default(self):
        assert trim_enabled({}) is True

    def test_trimming_can_be_switched_off(self):
        assert trim_enabled({"trim_silence_before_upload": False}) is False

    def test_the_default_threshold_is_five_minutes(self):
        assert min_saving_seconds({}) == DEFAULT_MIN_SAVING_SECONDS

    def test_the_threshold_is_read_in_minutes(self):
        assert min_saving_seconds({"trim_min_saving_minutes": 10}) == 600

    def test_zero_means_always_trim(self):
        assert min_saving_seconds({"trim_min_saving_minutes": 0}) == 0

    def test_nonsense_falls_back_to_the_default(self):
        assert min_saving_seconds({"trim_min_saving_minutes": "soon"}) == DEFAULT_MIN_SAVING_SECONDS

    def test_a_negative_threshold_becomes_zero(self):
        assert min_saving_seconds({"trim_min_saving_minutes": -5}) == 0


class TestWorthTrimming:
    def test_enough_silence_is_worth_it(self):
        assert worth_trimming(600, 300) is True

    def test_too_little_silence_is_not(self):
        assert worth_trimming(60, 300) is False

    def test_the_boundary_counts(self):
        assert worth_trimming(300, 300) is True

    def test_no_silence_is_never_worth_it(self):
        assert worth_trimming(0, 0) is False


class TestDescribeSaving:
    def test_it_reports_what_was_removed_and_what_is_left(self):
        text = describe_saving(2 * HOUR, HOUR)
        assert "Removed 1 hour of silence" in text
        assert "sending 1 hour of 2 hours" in text

    def test_a_trim_that_removed_nothing_says_so(self):
        assert "Removed 0 minutes" in describe_saving(HOUR, HOUR)


class TestPrepareForUpload:
    def test_a_disabled_trim_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        prepared = prepare_for_upload(audio, {"trim_silence_before_upload": False})
        assert prepared.path == audio
        assert prepared.temporary is False

    def test_a_worthwhile_trim_sends_a_temporary_copy(self, tmp_path):
        audio = _audio(tmp_path)
        run = _runner(FFMPEG_OUTPUT)
        prepared = prepare_for_upload(audio, {}, run_fn=run)
        assert prepared.temporary is True
        assert prepared.path != audio
        assert prepared.path.exists()
        # Measured from the trimmed file, not predicted from the analysis:
        # silenceremove shortens every pause, not only the long gaps.
        assert "sending 10 minutes of 2 hours" in prepared.note
        prepared.cleanup()

    def test_the_original_is_left_on_disk(self, tmp_path):
        audio = _audio(tmp_path)
        prepared = prepare_for_upload(audio, {}, run_fn=_runner(FFMPEG_OUTPUT))
        prepared.cleanup()
        assert audio.exists()

    def test_cleanup_removes_the_copy(self, tmp_path):
        prepared = prepare_for_upload(_audio(tmp_path), {}, run_fn=_runner(FFMPEG_OUTPUT))
        copy = prepared.path
        prepared.cleanup()
        assert not copy.exists()

    def test_cleanup_never_removes_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        PreparedAudio(audio).cleanup()
        assert audio.exists()

    def test_too_little_silence_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        run = _runner(QUIET_OUTPUT)
        prepared = prepare_for_upload(audio, {}, run_fn=run)
        assert prepared.path == audio
        assert run.calls["n"] == 1  # analysed, never trimmed

    def test_a_zero_threshold_trims_any_silence(self, tmp_path):
        audio = _audio(tmp_path)
        prepared = prepare_for_upload(
            audio, {"trim_min_saving_minutes": 0}, run_fn=_runner(QUIET_OUTPUT)
        )
        assert prepared.temporary is True
        prepared.cleanup()

    def test_an_unreadable_recording_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        prepared = prepare_for_upload(audio, {}, run_fn=_runner("garbage"))
        assert prepared.path == audio

    def test_a_failed_trim_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        run = _runner(FFMPEG_OUTPUT, trim_result=_result(returncode=1, stderr="no space"))
        prepared = prepare_for_upload(audio, {}, run_fn=run)
        assert prepared.path == audio
        assert prepared.temporary is False

    def test_a_trim_that_writes_nothing_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)
        run = _runner(FFMPEG_OUTPUT, written=False)
        prepared = prepare_for_upload(audio, {}, run_fn=run)
        assert prepared.path == audio

    def test_an_unmeasurable_copy_is_discarded(self, tmp_path):
        # A file ffprobe cannot read is not one to send in place of the
        # recording, however plausible its size.
        audio = _audio(tmp_path)
        prepared = prepare_for_upload(audio, {}, run_fn=_runner(FFMPEG_OUTPUT, trimmed_seconds=0))
        assert prepared.path == audio
        assert prepared.temporary is False

    def test_a_crashing_ffmpeg_sends_the_original(self, tmp_path):
        audio = _audio(tmp_path)

        def run(command):
            if "silencedetect" in " ".join(command):
                return _result(stderr=FFMPEG_OUTPUT)
            raise OSError("ffmpeg died")

        assert prepare_for_upload(audio, {}, run_fn=run).path == audio

    def test_progress_is_reported(self, tmp_path):
        seen = []
        prepared = prepare_for_upload(
            _audio(tmp_path), {}, on_status=seen.append, run_fn=_runner(FFMPEG_OUTPUT)
        )
        prepared.cleanup()
        assert any("Measuring silence" in m for m in seen)
        assert any("Removing silence" in m for m in seen)
        assert any("Removed" in m and "sending" in m for m in seen)
