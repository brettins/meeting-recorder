"""Tests for the pure error-presentation policy."""

import pytest

from meeting_recorder.core.errors import error_presentation, error_summary


class TestErrorPresentation:
    @pytest.mark.parametrize(
        "msg",
        [
            "Gemini API key is not configured. Please open Settings.",
            "Audio device error: no default source",
            "ffmpeg not found. Please install ffmpeg.",
            "faster-whisper is not installed. Run: pip install faster-whisper",
            "Permission denied writing to /var/log",
        ],
    )
    def test_actionable_problems_get_a_dialog(self, msg):
        assert error_presentation(msg) == "dialog"

    @pytest.mark.parametrize(
        "msg",
        [
            "Gemini did not respond within 3 minutes (transcription).",
            "Cannot reach Ollama at http://localhost:11434. Make sure it is running.",
            "Failed to stop recording: timeout",
            "Ollama error: model crashed",
        ],
    )
    def test_runtime_failures_get_a_toast(self, msg):
        assert error_presentation(msg) == "toast"

    def test_classification_is_case_insensitive(self):
        assert error_presentation("GEMINI API KEY MISSING") == "dialog"


class TestErrorSummary:
    """The row subtitle shortens a failure; the Details action shows all of it."""

    def test_short_message_is_unchanged(self):
        assert error_summary("Bad API key") == "Bad API key"

    def test_only_the_first_line_is_used(self):
        assert error_summary("Upload failed\nTraceback...\n  more") == "Upload failed"

    def test_long_line_is_ellipsised_not_silently_cut(self):
        out = error_summary("x" * 200)
        assert len(out) == 80
        assert out.endswith("…")

    def test_blank_message_is_empty(self):
        assert error_summary("   \n  ") == ""

    def test_limit_is_configurable(self):
        assert error_summary("abcdef", limit=4) == "abc…"

    def test_real_gemini_error_keeps_the_useful_head(self):
        msg = (
            "Gemini stopped early during transcription: the response hit the "
            "65,536-token output limit. Split the recording into shorter parts."
        )
        assert error_summary(msg).startswith("Gemini stopped early during transcription")
