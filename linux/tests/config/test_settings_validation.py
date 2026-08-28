"""Tests for the pure gemini_key_warning helper."""

from meeting_recorder.config.settings import gemini_key_warning

LEGACY_KEY = "AIza" + "x" * 35
# Google also issues longer keys with no "AIza" prefix. A prefix check used to
# reject these, warning users that a working key was malformed.
MODERN_KEY = "x" * 53


class TestGeminiKeyWarning:
    def test_no_warning_for_legacy_aiza_key(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": LEGACY_KEY}
        assert gemini_key_warning(cfg) is None

    def test_no_warning_for_modern_key_without_aiza_prefix(self):
        # Regression: a valid non-"AIza" key must not be reported as malformed.
        cfg = {"transcription_service": "gemini", "gemini_api_key": MODERN_KEY}
        assert gemini_key_warning(cfg) is None

    def test_warns_when_gemini_selected_but_key_empty(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": ""}
        assert "no API key" in (gemini_key_warning(cfg) or "")

    def test_warns_on_embedded_whitespace(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": "abcdefghij klmnopqrstuvwxyz"}
        warning = gemini_key_warning(cfg)
        assert warning is not None and "space" in warning

    def test_warns_on_too_short_key(self):
        cfg = {"summarization_service": "gemini", "gemini_api_key": "AIzaShort"}
        warning = gemini_key_warning(cfg)
        assert warning is not None and "too short" in warning

    def test_no_warning_when_gemini_not_used(self):
        cfg = {
            "transcription_service": "whisper",
            "summarization_service": "ollama",
            "gemini_api_key": "",
        }
        assert gemini_key_warning(cfg) is None

    def test_defaults_treat_missing_services_as_gemini(self):
        # An empty config means gemini/gemini (the shipped defaults).
        assert gemini_key_warning({}) is not None

    def test_key_is_stripped_before_checking(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": f"  {LEGACY_KEY}  "}
        assert gemini_key_warning(cfg) is None
