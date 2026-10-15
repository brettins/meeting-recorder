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

from meeting_recorder.processing.providers import gemini
from meeting_recorder.processing.providers.gemini import (
    GeminiProvider,
    RecitationBlocked,
    _require_text,
)

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


# --- transcribing a long recording in parts --------------------------------


def _provider(monkeypatch, tmp_path, durations, answers):
    """A provider whose cutting and Gemini calls are scripted."""
    provider = GeminiProvider(api_key="k", transcription_prompt="BASE")
    monkeypatch.setattr(gemini, "probe_duration", lambda *_: durations)

    def fake_extract(source, parts, directory, run_fn):
        paths = []
        for i, _ in enumerate(parts, 1):
            path = directory / f"part{i}.mp3"
            path.write_bytes(b"x")
            paths.append(path)
        return paths

    monkeypatch.setattr(gemini, "extract_parts", fake_extract)
    calls: list[tuple[str, str]] = []
    scripted = {name: list(replies) for name, replies in answers.items()}

    def fake_file(path, prompt, on_status):
        calls.append((path.name, prompt))
        reply = scripted[path.name].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(provider, "_transcribe_file", fake_file)
    return provider, calls


def test_a_short_recording_is_sent_whole(monkeypatch, tmp_path):
    provider = GeminiProvider(api_key="k", transcription_prompt="BASE")
    monkeypatch.setattr(gemini, "probe_duration", lambda *_: 600.0)
    seen = []
    monkeypatch.setattr(
        provider,
        "_transcribe_file",
        lambda path, prompt, status: seen.append((path, prompt)) or "T",
    )

    assert provider.transcribe(tmp_path / "a.mp3") == "T"
    assert seen == [(tmp_path / "a.mp3", "BASE")]


def test_parts_are_joined_with_shifted_timestamps(monkeypatch, tmp_path):
    answers = {
        "part1.mp3": ["Sure:\n\n[00:00:05] **Ryan:** one"],
        "part2.mp3": ["[00:00:07] **Person 1:** two"],
    }
    provider, calls = _provider(monkeypatch, tmp_path, 1500.0, answers)

    text = provider.transcribe(tmp_path / "a.mp3")

    assert text == "[00:00:05] **Ryan:** one\n\n[00:15:07] **Person 1:** two"
    assert "Ryan" in calls[1][1]
    assert "part 2 of 2" in calls[1][1]


def test_a_blocked_part_is_retried_then_succeeds(monkeypatch, tmp_path):
    answers = {
        "part1.mp3": [RecitationBlocked("no"), "[00:00:01] **A:** ok"],
        "part2.mp3": ["[00:00:01] **A:** fine"],
    }
    provider, calls = _provider(monkeypatch, tmp_path, 1500.0, answers)

    text = provider.transcribe(tmp_path / "a.mp3")

    assert "ok" in text and "fine" in text
    assert [name for name, _ in calls] == ["part1.mp3", "part1.mp3", "part2.mp3"]


def test_a_part_that_stays_blocked_leaves_a_note_and_the_rest_finishes(monkeypatch, tmp_path):
    blocked = [RecitationBlocked("no")] * 3
    answers = {"part1.mp3": blocked, "part2.mp3": ["[00:00:01] **A:** fine"]}
    provider, _ = _provider(monkeypatch, tmp_path, 1500.0, answers)

    text = provider.transcribe(tmp_path / "a.mp3")

    assert text.startswith("[00:00:00] **Note:** Gemini's copyright filter blocked 00:00:00")
    assert text.endswith("[00:15:01] **A:** fine")


def test_every_part_blocked_raises(monkeypatch, tmp_path):
    blocked = [RecitationBlocked("no")] * 3
    answers = {"part1.mp3": list(blocked), "part2.mp3": list(blocked)}
    provider, _ = _provider(monkeypatch, tmp_path, 1500.0, answers)

    with pytest.raises(RecitationBlocked):
        provider.transcribe(tmp_path / "a.mp3")


def test_other_failures_still_fail_the_job(monkeypatch, tmp_path):
    answers = {"part1.mp3": [RuntimeError("API down")], "part2.mp3": ["x"]}
    provider, _ = _provider(monkeypatch, tmp_path, 1500.0, answers)

    with pytest.raises(RuntimeError, match="API down"):
        provider.transcribe(tmp_path / "a.mp3")


def test_failing_to_cut_falls_back_to_the_whole_file(monkeypatch, tmp_path):
    provider = GeminiProvider(api_key="k", transcription_prompt="BASE")
    monkeypatch.setattr(gemini, "probe_duration", lambda *_: 5000.0)

    def broken(*_args):
        raise RuntimeError("ffmpeg could not cut part 1")

    monkeypatch.setattr(gemini, "extract_parts", broken)
    monkeypatch.setattr(provider, "_transcribe_file", lambda path, prompt, status: "WHOLE")

    assert provider.transcribe(tmp_path / "a.mp3") == "WHOLE"


def test_recitation_blocked_is_still_a_runtime_error():
    assert issubclass(RecitationBlocked, RuntimeError)
    response = _response("", out_tokens=0, finish_reason="RECITATION")
    with pytest.raises(RecitationBlocked):
        _require_text(response, "transcription")
