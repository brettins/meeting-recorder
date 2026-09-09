"""Tests for parsing ffmpeg's silencedetect output."""

from meeting_recorder.audio.silence_events import (
    SILENCE_END,
    SILENCE_START,
    parse_silence_event,
)

START = "[silencedetect @ 0x7fac480047c0] silence_start: 50145.566292"
END = "[silencedetect @ 0x7fac480047c0] silence_end: 50472.067417 | silence_duration: 326.501125"


class TestParseSilenceEvent:
    def test_a_start_line_is_read(self):
        event = parse_silence_event(START)
        assert event is not None
        assert event.kind == SILENCE_START
        assert event.at_seconds == 50145.566292

    def test_an_end_line_is_read(self):
        event = parse_silence_event(END)
        assert event is not None
        assert event.kind == SILENCE_END
        assert event.at_seconds == 50472.067417

    def test_an_end_line_is_not_mistaken_for_a_start(self):
        # "silence_end: … | silence_duration: …" contains no silence_start, but
        # a naive substring check on "silence_" would match either way.
        assert parse_silence_event(END).kind == SILENCE_END

    def test_a_start_at_zero_is_read(self):
        event = parse_silence_event("[silencedetect @ 0x1] silence_start: 0")
        assert event is not None
        assert event.at_seconds == 0.0

    def test_a_negative_start_is_tolerated(self):
        # ffmpeg reports a negative timestamp when silence precedes the first
        # frame; it is a real event, not a parse failure.
        event = parse_silence_event("[silencedetect @ 0x1] silence_start: -0.0139")
        assert event is not None
        assert event.at_seconds < 0

    def test_an_unrelated_line_is_ignored(self):
        assert parse_silence_event("[mp3 @ 0x1] Header missing") is None

    def test_an_empty_line_is_ignored(self):
        assert parse_silence_event("") is None

    def test_a_malformed_timestamp_is_ignored(self):
        assert parse_silence_event("[silencedetect @ 0x1] silence_start: soon") is None

    def test_extra_whitespace_is_tolerated(self):
        event = parse_silence_event("[silencedetect @ 0x1] silence_start:   12.5")
        assert event is not None
        assert event.at_seconds == 12.5
