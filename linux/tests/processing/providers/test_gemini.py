"""
Tests for the Gemini provider's response-extraction helper ``_require_text``.

The module imports the ``google.genai`` SDK lazily (inside functions), so this helper is
importable and testable without the SDK installed. These tests pin two behaviours:

- a normal (``STOP``) response is returned verbatim even when its output-token count is
  large — no "near 8192 limit" truncation *warning* is emitted (that stale heuristic was
  removed; current models all allow 65,536 output tokens and the app requests the full
  budget), and
- a genuine ``MAX_TOKENS`` finish still raises the hard truncation error.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from meeting_recorder.processing.providers.gemini import _require_text

_LOGGER_NAME = "meeting_recorder.processing.providers.gemini"


def _response(
    text: str,
    *,
    out_tokens: int = 100,
    finish_reason: object = "STOP",
    prompt_feedback: object = None,
) -> SimpleNamespace:
    usage = SimpleNamespace(
        prompt_token_count=1000,
        candidates_token_count=out_tokens,
        total_token_count=1000 + out_tokens,
    )
    candidate = SimpleNamespace(finish_reason=finish_reason)
    return SimpleNamespace(
        usage_metadata=usage,
        candidates=[candidate],
        text=text,
        prompt_feedback=prompt_feedback,
    )


def test_large_output_returns_text_without_truncation_warning(caplog):
    """A 13k-token STOP response is complete — return it, don't warn about a 8192 limit."""
    response = _response("  transcript body  ", out_tokens=13191)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = _require_text(response, "transcription")

    assert result == "transcript body"
    # The removed heuristic used to log these; they must not reappear.
    assert "8192" not in caplog.text
    assert "truncat" not in caplog.text.lower()


def test_empty_text_raises():
    response = _response("", out_tokens=0)
    with pytest.raises(RuntimeError, match="returned no text"):
        _require_text(response, "transcription")


def test_max_tokens_finish_reason_still_raises():
    """The legitimate hard-truncation error (finish_reason == MAX_TOKENS) is preserved."""
    pytest.importorskip("google.genai")
    from google.genai import types

    response = _response("partial…", finish_reason=types.FinishReason.MAX_TOKENS)
    with pytest.raises(RuntimeError, match="hit the .*output limit"):
        _require_text(response, "transcription")


def test_truncation_message_names_no_retired_models():
    """The advice must not point at models the API has withdrawn."""
    pytest.importorskip("google.genai")
    from google.genai import types

    response = _response("partial…", finish_reason=types.FinishReason.MAX_TOKENS)
    with pytest.raises(RuntimeError) as exc:
        _require_text(response, "transcription")
    text = str(exc.value)
    for retired in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"):
        assert retired not in text, f"error text still recommends retired {retired}"
    assert "Settings" in text  # points somewhere the user can act


@pytest.mark.parametrize("reason", ["RECITATION", SimpleNamespace(name="RECITATION")])
def test_recitation_says_the_copyright_filter_blocked_it(reason):
    response = _response("", out_tokens=0, finish_reason=reason)
    with pytest.raises(RuntimeError, match="copyright filter blocked the transcription") as exc:
        _require_text(response, "transcription")
    assert "Try again" in str(exc.value)


def test_recitation_with_the_sdk_enum_raises_even_with_partial_text():
    pytest.importorskip("google.genai")
    from google.genai import types

    response = _response("partial…", finish_reason=types.FinishReason.RECITATION)
    with pytest.raises(RuntimeError, match="part of the audio"):
        _require_text(response, "transcription")


def test_recitation_in_a_summary_names_the_transcript():
    response = _response("", out_tokens=0, finish_reason="RECITATION")
    with pytest.raises(
        RuntimeError, match="blocked the summary. It flagged part of the transcript"
    ):
        _require_text(response, "summarization")


# --- the whole-file path ------------------------------------------------------


class _Files:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def upload(self, *, file, config):
        self.calls.append(("upload", file, config))
        return SimpleNamespace(name="files/1", state=self.states.pop(0))

    def get(self, *, name, config=None):
        self.calls.append(("get", name, config))
        return SimpleNamespace(name=name, state=self.states.pop(0))

    def delete(self, *, name, config=None):
        self.calls.append(("delete", name, config))


class _Models:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(config)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _whole_file_provider(states, outcome):
    from meeting_recorder.processing.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="k", model="m", timeout_minutes=5)
    files, models = _Files(states), _Models(outcome)
    provider._client = SimpleNamespace(files=files, models=models)
    return provider, files, models


def _states():
    pytest.importorskip("google.genai")
    from google.genai import types

    return types.FileState


def test_whole_file_upload_poll_and_delete_all_carry_a_timeout(monkeypatch, tmp_path):
    state = _states()
    monkeypatch.setattr("time.sleep", lambda _s: None)
    provider, files, models = _whole_file_provider(
        [state.PROCESSING, state.ACTIVE], _response("  the words  ")
    )

    text = provider.transcribe(tmp_path / "a.mp3")

    assert text == "the words"
    kinds = [call[0] for call in files.calls]
    assert kinds == ["upload", "get", "delete"]
    upload_config = files.calls[0][2]
    assert upload_config["mime_type"] == "audio/mpeg"
    assert upload_config["http_options"]["timeout"] == 5 * 60_000
    assert files.calls[1][2]["http_options"]["timeout"] == 60_000
    assert files.calls[2][2]["http_options"]["timeout"] == 60_000
    assert models.calls[0]["http_options"]["timeout"] == 5 * 60_000


def test_the_uploaded_file_is_deleted_when_transcription_fails(tmp_path):
    state = _states()
    provider, files, _ = _whole_file_provider([state.ACTIVE], _response("", out_tokens=0))

    with pytest.raises(RuntimeError, match="returned no text"):
        provider.transcribe(tmp_path / "a.mp3")

    assert [call[0] for call in files.calls] == ["upload", "delete"]


def test_a_blocked_transcript_keeps_the_clear_copyright_filter_message(tmp_path):
    state = _states()
    provider, files, _ = _whole_file_provider(
        [state.ACTIVE], _response("", out_tokens=0, finish_reason="RECITATION")
    )

    with pytest.raises(RuntimeError, match="copyright filter blocked the transcription"):
        provider.transcribe(tmp_path / "a.mp3")

    assert files.calls[-1][0] == "delete"


def test_a_failed_delete_does_not_fail_the_transcript(tmp_path):
    state = _states()
    provider, files, _ = _whole_file_provider([state.ACTIVE], _response("ok"))

    def refuse(*, name, config=None):
        raise OSError("offline")

    files.delete = refuse

    assert provider.transcribe(tmp_path / "a.mp3") == "ok"
