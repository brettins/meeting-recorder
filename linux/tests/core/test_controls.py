"""Tests for the recorder-control visibility policy.

This policy exists so the window can build its buttons once and only toggle
them. Rebuilding the button box on every snapshot — one a second, from the timer
tick — destroyed whichever button was between its press and release, which is
what made the Stop button fail intermittently.
"""

from meeting_recorder.core.controls import (
    ALL_CONTROLS,
    CANCEL,
    CANCEL_COUNTDOWN,
    CANCEL_SAVE,
    PAUSE,
    RECORD_HEADPHONES,
    RECORD_SPEAKER,
    RESUME,
    STOP,
    USE_EXISTING,
    controls_for_state,
    title_editable,
)


def test_idle_offers_the_three_ways_to_start():
    assert controls_for_state("idle") == (RECORD_HEADPHONES, RECORD_SPEAKER, USE_EXISTING)


def test_recording_offers_pause_and_stop():
    controls = controls_for_state("recording")
    assert controls == (PAUSE, STOP, CANCEL_SAVE, CANCEL)
    assert RESUME not in controls


def test_paused_swaps_pause_for_resume():
    controls = controls_for_state("paused")
    assert controls == (RESUME, STOP, CANCEL_SAVE, CANCEL)
    assert PAUSE not in controls


def test_stop_is_offered_whenever_a_recording_can_be_stopped():
    for state in ("recording", "paused"):
        assert STOP in controls_for_state(state)


def test_countdown_offers_only_its_own_cancel():
    assert controls_for_state("countdown") == (CANCEL_COUNTDOWN,)


def test_unknown_state_falls_back_to_idle():
    assert controls_for_state("garbage") == controls_for_state("idle")
    assert controls_for_state("") == controls_for_state("idle")


def test_every_control_is_reachable_from_some_state():
    seen = set()
    for state in ("idle", "recording", "paused", "countdown"):
        seen.update(controls_for_state(state))
    assert seen == set(ALL_CONTROLS)


def test_title_is_editable_while_recording_but_not_during_the_countdown():
    assert title_editable("idle")
    assert title_editable("recording")
    assert title_editable("paused")
    # The queued rename is already running on a worker thread by then.
    assert not title_editable("countdown")
