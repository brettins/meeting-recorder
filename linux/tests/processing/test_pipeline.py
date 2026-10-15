"""Tests for Pipeline cancellation and input validation (no network)."""

from pathlib import Path

import pytest

from meeting_recorder.core.task_runner import CancelToken
from meeting_recorder.processing.pipeline import Pipeline, PipelineCancelled


def _pipeline(audio: Path | None) -> Pipeline:
    return Pipeline(
        config={"transcription_service": "gemini", "summarization_service": "gemini"},
        audio_path=audio,
        transcript_path=None,
        notes_path=None,
    )


class TestPipelineGuards:
    def test_missing_audio_path_fails_fast(self):
        with pytest.raises(ValueError, match="audio path"):
            _pipeline(None).run()

    def test_cancelled_token_stops_before_any_provider_work(self, tmp_path):
        token = CancelToken()
        token.cancel()
        # Raises PipelineCancelled before any provider is created, so no
        # network/API key is needed for this test.
        with pytest.raises(PipelineCancelled):
            _pipeline(tmp_path / "recording.mp3").run(cancel_token=token)

    def test_uncancelled_token_proceeds_to_provider_creation(self, tmp_path):
        # With gemini selected and no API key, provider creation/transcription
        # is the next step after the cancel check — reaching an error that is
        # NOT PipelineCancelled proves the token check passed.
        token = CancelToken()
        with pytest.raises(Exception) as excinfo:
            _pipeline(tmp_path / "recording.mp3").run(cancel_token=token)
        assert not isinstance(excinfo.value, PipelineCancelled)


class _Provider:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def transcribe(self, audio_path, on_status=None):
        return "transcript"

    def summarize(self, text, on_status=None):
        if self.fail:
            raise RuntimeError("summary refused")
        return "notes"

    def release_saved_parts(self):
        self.events.append("released")


def _run_with_provider(monkeypatch, tmp_path, provider):
    from meeting_recorder.processing import summarization, transcription, upload_audio

    monkeypatch.setattr(transcription, "create_transcription_provider", lambda cfg: provider)
    monkeypatch.setattr(summarization, "create_summarization_provider", lambda cfg: provider)
    monkeypatch.setattr(
        upload_audio,
        "prepare_for_upload",
        lambda path, cfg, on_status=None: upload_audio.PreparedAudio(path),
    )
    pipeline = Pipeline(
        config={"auto_title": False},
        audio_path=tmp_path / "recording.mp3",
        transcript_path=tmp_path / "transcript.md",
        notes_path=tmp_path / "notes.md",
    )
    pipeline.run()


def test_saved_parts_are_released_only_after_the_results_are_written(monkeypatch, tmp_path):
    events = []
    provider = _Provider(events)
    original = Pipeline._write_results

    def write(self, transcript, notes):
        original(self, transcript, notes)
        events.append("written")

    monkeypatch.setattr(Pipeline, "_write_results", write)

    _run_with_provider(monkeypatch, tmp_path, provider)

    assert events == ["written", "released"]


def test_saved_parts_are_kept_when_the_summary_fails(monkeypatch, tmp_path):
    events = []

    with pytest.raises(RuntimeError, match="summary refused"):
        _run_with_provider(monkeypatch, tmp_path, _Provider(events, fail=True))

    assert events == []
    assert not (tmp_path / "transcript.md").exists()
