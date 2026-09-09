"""Tests for silence analysis, split planning, and the ffmpeg command builders."""

from meeting_recorder.processing.silence_map import (
    ANALYSIS_THRESHOLD_DB,
    DEFAULT_SPLIT_GAP_SECONDS,
    MIN_PART_SECONDS,
    SPLIT_PAD_SECONDS,
    Part,
    SilenceRun,
    build_analyse_command,
    build_extract_command,
    build_trim_command,
    describe_plan,
    parse_duration,
    parse_silence_runs,
    split_parts,
    total_silence,
)

MINUTE = 60.0
HOUR = 3600.0


def _log(*pairs, trailing_start=None):
    lines = []
    for start, end in pairs:
        lines.append(f"[silencedetect @ 0x1] silence_start: {start}")
        lines.append(f"[silencedetect @ 0x1] silence_end: {end} | silence_duration: {end - start}")
    if trailing_start is not None:
        lines.append(f"[silencedetect @ 0x1] silence_start: {trailing_start}")
    return "\n".join(lines)


class TestParseDuration:
    def test_a_duration_line_is_read(self):
        assert parse_duration("  Duration: 16:04:51.02, start: 0.000000") == 57891.02

    def test_a_missing_duration_is_zero(self):
        assert parse_duration("no duration here") == 0.0

    def test_minutes_and_seconds_only(self):
        assert parse_duration("Duration: 00:02:30.00") == 150.0


class TestParseSilenceRuns:
    def test_paired_lines_become_runs(self):
        runs = parse_silence_runs(_log((10.0, 40.0), (100.0, 160.0)))
        assert runs == [SilenceRun(10.0, 40.0), SilenceRun(100.0, 160.0)]

    def test_an_unclosed_run_is_closed_at_the_end_of_the_file(self):
        runs = parse_silence_runs(_log((10.0, 40.0), trailing_start=100.0), total_duration=500.0)
        assert runs[-1] == SilenceRun(100.0, 500.0)

    def test_an_unclosed_run_is_dropped_when_the_length_is_unknown(self):
        # An unbounded run would otherwise read as the longest gap in the file.
        runs = parse_silence_runs(_log((10.0, 40.0), trailing_start=100.0))
        assert runs == [SilenceRun(10.0, 40.0)]

    def test_an_unclosed_run_past_the_end_is_dropped(self):
        runs = parse_silence_runs(_log(trailing_start=600.0), total_duration=500.0)
        assert runs == []

    def test_a_repeated_start_keeps_the_first(self):
        text = (
            "[silencedetect @ 0x1] silence_start: 10.0\n"
            "[silencedetect @ 0x1] silence_start: 20.0\n"
            "[silencedetect @ 0x1] silence_end: 40.0 | silence_duration: 30.0\n"
        )
        assert parse_silence_runs(text) == [SilenceRun(10.0, 40.0)]

    def test_a_negative_start_is_clamped_to_zero(self):
        text = (
            "[silencedetect @ 0x1] silence_start: -0.014\n"
            "[silencedetect @ 0x1] silence_end: 30.0 | silence_duration: 30.0\n"
        )
        assert parse_silence_runs(text) == [SilenceRun(0.0, 30.0)]

    def test_unrelated_output_is_ignored(self):
        assert parse_silence_runs("Input #0, mp3\n  Duration: 00:10:00.00\n") == []

    def test_total_silence_adds_the_runs_up(self):
        assert total_silence([SilenceRun(0.0, 30.0), SilenceRun(60.0, 90.0)]) == 60.0


class TestSplitParts:
    def test_a_file_with_no_long_gap_stays_one_part(self):
        runs = [SilenceRun(100.0, 100.0 + 5 * MINUTE)]
        assert split_parts(runs, 2 * HOUR) == [Part(0.0, 2 * HOUR)]

    def test_a_file_with_no_silence_at_all_stays_one_part(self):
        assert split_parts([], HOUR) == [Part(0.0, HOUR)]

    def test_a_long_gap_splits_the_file_in_two(self):
        gap = SilenceRun(HOUR, HOUR + 40 * MINUTE)
        parts = split_parts([gap], 3 * HOUR)
        assert len(parts) == 2
        assert parts[0].start == 0.0
        assert parts[1].end == 3 * HOUR

    def test_the_seam_is_padded_on_both_sides(self):
        gap = SilenceRun(HOUR, HOUR + 40 * MINUTE)
        parts = split_parts([gap], 3 * HOUR)
        assert parts[0].end == HOUR + SPLIT_PAD_SECONDS
        assert parts[1].start == gap.end - SPLIT_PAD_SECONDS

    def test_two_long_gaps_make_three_parts(self):
        runs = [
            SilenceRun(HOUR, HOUR + 40 * MINUTE),
            SilenceRun(4 * HOUR, 5 * HOUR),
        ]
        assert len(split_parts(runs, 6 * HOUR)) == 3

    def test_a_gap_just_under_the_threshold_does_not_split(self):
        gap = SilenceRun(HOUR, HOUR + DEFAULT_SPLIT_GAP_SECONDS - 1)
        assert len(split_parts([gap], 3 * HOUR)) == 1

    def test_a_gap_exactly_at_the_threshold_splits(self):
        gap = SilenceRun(HOUR, HOUR + DEFAULT_SPLIT_GAP_SECONDS)
        assert len(split_parts([gap], 3 * HOUR)) == 2

    def test_the_threshold_is_configurable(self):
        gap = SilenceRun(HOUR, HOUR + 11 * MINUTE)
        assert len(split_parts([gap], 3 * HOUR, gap_seconds=10 * MINUTE)) == 2

    def test_a_scrap_shorter_than_a_minute_is_dropped(self):
        # Silence starting a few seconds in leaves a fragment, not a meeting.
        runs = [SilenceRun(10.0, HOUR)]
        parts = split_parts(runs, 2 * HOUR)
        assert len(parts) == 1
        assert parts[0].start > 0

    def test_a_file_that_is_entirely_silence_still_yields_one_part(self):
        runs = [SilenceRun(0.0, 2 * HOUR)]
        assert split_parts(runs, 2 * HOUR) == [Part(0.0, 2 * HOUR)]

    def test_a_zero_length_file_yields_nothing(self):
        assert split_parts([], 0.0) == []

    def test_parts_never_overlap_or_run_backwards(self):
        runs = [
            SilenceRun(HOUR, HOUR + 40 * MINUTE),
            SilenceRun(2 * HOUR, 3 * HOUR),
        ]
        parts = split_parts(runs, 4 * HOUR)
        for earlier, later in zip(parts, parts[1:], strict=False):
            assert earlier.end <= later.start
        assert all(p.duration >= MIN_PART_SECONDS for p in parts)


class TestDescribePlan:
    def test_an_unreadable_file_says_so(self):
        assert "could not be read" in describe_plan([], [], 0.0)

    def test_a_clean_recording_reports_no_gaps(self):
        text = describe_plan([Part(0.0, HOUR)], [], HOUR)
        assert "no long gaps" in text

    def test_a_single_part_reports_what_would_be_sent(self):
        runs = [SilenceRun(0.0, 20 * MINUTE)]
        text = describe_plan([Part(0.0, HOUR)], runs, HOUR)
        assert "stays one meeting" in text
        assert "40 minutes" in text

    def test_a_split_names_the_number_of_meetings_and_their_lengths(self):
        parts = [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR)]
        runs = [SilenceRun(HOUR, 2 * HOUR)]
        text = describe_plan(parts, runs, 3 * HOUR)
        assert "Splitting into 2 meetings" in text
        assert "1 hour, 1 hour" in text

    def test_one_gap_is_singular(self):
        parts = [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR)]
        text = describe_plan(parts, [SilenceRun(HOUR, 2 * HOUR)], 3 * HOUR)
        assert "1 gap over" in text

    def test_several_gaps_are_plural(self):
        parts = [Part(0.0, HOUR), Part(2 * HOUR, 3 * HOUR), Part(4 * HOUR, 5 * HOUR)]
        text = describe_plan(parts, [], 5 * HOUR)
        assert "2 gaps over" in text


class TestCommands:
    def test_the_analysis_decodes_to_nothing(self):
        cmd = build_analyse_command("/m/recording.mp3")
        assert cmd[-2:] == ["-f", "null"] or cmd[-3:-1] == ["-f", "null"]
        assert "-" == cmd[-1]
        assert any(f"silencedetect=n={ANALYSIS_THRESHOLD_DB}dB" in a for a in cmd)

    def test_extraction_seeks_before_the_input_and_copies(self):
        cmd = build_extract_command("/m/in.mp3", Part(60.0, 120.0), "/m/out.mp3")
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "60.000"
        assert cmd[cmd.index("-to") + 1] == "120.000"
        assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"

    def test_extraction_names_both_files(self):
        cmd = build_extract_command("/m/in.mp3", Part(0.0, 1.0), "/m/out.mp3")
        assert cmd[-1] == "/m/out.mp3"
        assert "/m/in.mp3" in cmd

    def test_trimming_removes_every_silence_not_just_the_first(self):
        cmd = build_trim_command("/m/in.mp3", "/m/out.mp3")
        filt = cmd[cmd.index("-af") + 1]
        assert "stop_periods=-1" in filt
        assert "stop_threshold=-45dB" in filt

    def test_trimming_leaves_a_beat_of_silence_in_place(self):
        filt = build_trim_command("/m/in.mp3", "/m/out.mp3")[
            build_trim_command("/m/in.mp3", "/m/out.mp3").index("-af") + 1
        ]
        assert "stop_duration=1.0" in filt

    def test_trimming_writes_an_mp3(self):
        cmd = build_trim_command("/m/in.mp3", "/m/out.mp3")
        assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"
        assert cmd[-1] == "/m/out.mp3"
