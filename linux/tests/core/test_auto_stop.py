"""Tests for the recording auto-stop ladder: nudge, warn, stop, and the hard cap."""

from meeting_recorder.core.auto_stop import (
    NONE,
    NUDGE,
    STAGE_NOTHING,
    STAGE_NUDGED,
    STAGE_STOPPED,
    STAGE_WARNED,
    STOP,
    WARN,
    AutoStopConfig,
    config_from,
    decide,
    format_duration,
)

HOUR = 3600
MINUTE = 60

CFG = AutoStopConfig(
    max_minutes=180,
    silence_enabled=True,
    grace_minutes=60,
    nudge_minutes=10,
    stop_minutes=30,
    warn_lead_minutes=5,
)


class TestHardCap:
    def test_a_recording_under_the_cap_is_left_alone(self):
        assert decide(2 * HOUR, 0, CFG).action == NONE

    def test_the_cap_stops_the_recording(self):
        decision = decide(3 * HOUR, 0, CFG)
        assert decision.action == STOP
        assert "3 hours" in decision.message

    def test_the_cap_fires_exactly_on_the_boundary(self):
        assert decide(3 * HOUR - 1, 0, CFG).action == NONE
        assert decide(3 * HOUR, 0, CFG).action == STOP

    def test_the_cap_outranks_the_silence_ladder(self):
        assert decide(3 * HOUR, 12 * MINUTE, CFG).action == STOP

    def test_a_zero_cap_never_stops(self):
        never = AutoStopConfig(max_minutes=0, silence_enabled=False)
        assert decide(100 * HOUR, 0, never).action == NONE

    def test_the_cap_is_announced_only_once(self):
        # Stopping takes time and ticks keep arriving; a cap that re-fired
        # would notify — and ask the engine to stop — every second.
        assert decide(3 * HOUR + 60, 0, CFG, announced_stage=STAGE_STOPPED).action == NONE

    def test_the_stop_message_points_at_the_setting(self):
        assert "Settings" in decide(3 * HOUR, 0, CFG).message


class TestGracePeriod:
    def test_silence_before_the_grace_period_is_ignored(self):
        assert decide(30 * MINUTE, 40 * MINUTE, CFG).action == NONE

    def test_the_ladder_starts_once_the_grace_period_has_passed(self):
        assert decide(HOUR, 10 * MINUTE, CFG).action == NUDGE

    def test_a_zero_grace_period_starts_immediately(self):
        eager = AutoStopConfig(grace_minutes=0)
        assert decide(10 * MINUTE, 10 * MINUTE, eager).action == NUDGE


class TestLadder:
    def test_a_short_quiet_stretch_says_nothing(self):
        assert decide(2 * HOUR, 5 * MINUTE, CFG).action == NONE

    def test_ten_minutes_of_quiet_nudges(self):
        decision = decide(2 * HOUR, 10 * MINUTE, CFG)
        assert decision.action == NUDGE
        assert "Nobody has spoken for 10 minutes" in decision.message
        assert decision.stage == STAGE_NUDGED

    def test_the_nudge_is_not_repeated(self):
        assert decide(2 * HOUR, 12 * MINUTE, CFG, announced_stage=STAGE_NUDGED).action == NONE

    def test_the_warning_lands_five_minutes_before_the_stop(self):
        decision = decide(2 * HOUR, 25 * MINUTE, CFG, announced_stage=STAGE_NUDGED)
        assert decision.action == WARN
        assert "will stop in about 5 minutes" in decision.message
        assert decision.stage == STAGE_WARNED

    def test_the_warning_is_not_repeated(self):
        assert decide(2 * HOUR, 27 * MINUTE, CFG, announced_stage=STAGE_WARNED).action == NONE

    def test_the_stop_fires_at_the_threshold(self):
        decision = decide(2 * HOUR, 30 * MINUTE, CFG, announced_stage=STAGE_WARNED)
        assert decision.action == STOP
        assert "sent for processing" in decision.message
        assert decision.stage == STAGE_STOPPED

    def test_the_stop_is_not_repeated(self):
        assert decide(2 * HOUR, 40 * MINUTE, CFG, announced_stage=STAGE_STOPPED).action == NONE

    def test_a_stretch_that_jumps_straight_past_the_nudge_still_warns(self):
        # A window the app was not watching can leave the ladder several
        # stages behind; the next tick must not replay the earlier rungs.
        assert decide(2 * HOUR, 26 * MINUTE, CFG).action == WARN

    def test_sound_resets_the_ladder(self):
        assert decide(2 * HOUR, 0, CFG, announced_stage=STAGE_NOTHING).action == NONE

    def test_the_ladder_can_be_switched_off_entirely(self):
        quiet = AutoStopConfig(max_minutes=0, silence_enabled=False)
        assert decide(5 * HOUR, 2 * HOUR, quiet).action == NONE

    def test_the_cap_still_applies_with_the_ladder_off(self):
        quiet = AutoStopConfig(max_minutes=180, silence_enabled=False)
        assert decide(3 * HOUR, 0, quiet).action == STOP

    def test_a_zero_stop_threshold_never_stops_but_still_nudges(self):
        nudge_only = AutoStopConfig(
            max_minutes=0, stop_minutes=0, grace_minutes=0, nudge_minutes=10
        )
        assert decide(5 * HOUR, 4 * HOUR, nudge_only).action == NUDGE
        assert decide(5 * HOUR, 4 * HOUR, nudge_only, announced_stage=STAGE_NUDGED).action == NONE

    def test_a_warn_lead_longer_than_the_stop_makes_the_warning_come_first(self):
        odd = AutoStopConfig(
            grace_minutes=0, nudge_minutes=99, stop_minutes=30, warn_lead_minutes=60
        )
        assert odd.warn_minutes == 0
        assert decide(2 * HOUR, MINUTE, odd).action == WARN


class TestConfigFrom:
    def test_defaults_apply_to_an_empty_config(self):
        cfg = config_from({})
        assert cfg == AutoStopConfig()
        assert cfg.max_minutes == 180
        assert cfg.stop_minutes == 30

    def test_stored_values_are_read(self):
        cfg = config_from(
            {
                "max_recording_minutes": 90,
                "silence_auto_stop_enabled": False,
                "silence_grace_minutes": 30,
                "silence_nudge_minutes": 5,
                "silence_stop_minutes": 20,
                "silence_warn_lead_minutes": 3,
            }
        )
        assert cfg == AutoStopConfig(90, False, 30, 5, 20, 3)

    def test_zero_is_kept_as_off(self):
        assert config_from({"max_recording_minutes": 0}).max_minutes == 0

    def test_a_negative_value_is_clamped_to_off(self):
        assert config_from({"silence_stop_minutes": -5}).stop_minutes == 0

    def test_a_numeric_string_is_accepted(self):
        assert config_from({"max_recording_minutes": "90"}).max_minutes == 90

    def test_nonsense_falls_back_to_the_default(self):
        assert config_from({"max_recording_minutes": "soon"}).max_minutes == 180
        assert config_from({"max_recording_minutes": None}).max_minutes == 180
        assert config_from({"max_recording_minutes": True}).max_minutes == 180

    def test_the_warning_threshold_is_derived_not_stored(self):
        assert (
            config_from({"silence_stop_minutes": 30, "silence_warn_lead_minutes": 5}).warn_minutes
            == 25
        )


class TestFormatDuration:
    def test_minutes_only(self):
        assert format_duration(10 * MINUTE) == "10 minutes"

    def test_a_single_minute_is_singular(self):
        assert format_duration(MINUTE) == "1 minute"

    def test_whole_hours_omit_the_minutes(self):
        assert format_duration(3 * HOUR) == "3 hours"

    def test_a_single_hour_is_singular(self):
        assert format_duration(HOUR) == "1 hour"

    def test_hours_and_minutes_are_both_shown(self):
        assert format_duration(HOUR + 30 * MINUTE) == "1 hour 30 minutes"

    def test_zero_reads_as_zero_minutes(self):
        assert format_duration(0) == "0 minutes"

    def test_seconds_are_rounded_down(self):
        assert format_duration(119) == "1 minute"
