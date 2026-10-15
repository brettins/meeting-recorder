"""Tests for core/edit_sync.py."""

from __future__ import annotations

import pytest

from meeting_recorder.core.edit_sync import (
    EditTracker,
    remote_tags_to_show,
    remote_title_to_show,
)


def test_nothing_is_overwritten_while_an_edit_is_unacknowledged():
    assert remote_title_to_show("abc", "ab", unacked_edits=1) is None
    assert remote_title_to_show("abc", "", unacked_edits=2) is None


def test_the_daemon_clearing_the_title_is_shown_once_every_edit_is_acknowledged():
    assert remote_title_to_show("old name", "", unacked_edits=0) == ""


def test_a_title_the_daemon_stripped_is_not_a_difference():
    assert remote_title_to_show("foo ", "foo", unacked_edits=0) is None
    assert remote_title_to_show(" foo", "foo", unacked_edits=0) is None


def test_an_entry_already_in_step_is_left_alone():
    assert remote_title_to_show("same", "same", unacked_edits=0) is None


def test_a_title_changed_elsewhere_is_shown():
    assert remote_title_to_show("mine", "theirs", unacked_edits=0) == "theirs"


def test_tags_wait_for_acknowledgement_too():
    assert remote_tags_to_show(["a", "b"], ["a"], unacked_edits=1) is None
    assert remote_tags_to_show(["a", "b"], ["a"], unacked_edits=0) == ["a"]
    assert remote_tags_to_show(["a"], ["a"], unacked_edits=0) is None


def _type_through_a_laggy_daemon(keystrokes: list[str]) -> str:
    """Replay keystrokes whose snapshots and replies all arrive after the last key."""
    text = ""
    sent: list[str] = []
    unacked = 0
    for key in keystrokes:
        text = text[:-1] if key == "<bs>" else text + key
        sent.append(text.strip())
        unacked += 1
    for title in sent:
        shown = remote_title_to_show(text, title, unacked)
        text = text if shown is None else shown
        unacked -= 1
    return text


def test_typing_then_backspacing_through_late_echoes_keeps_the_final_text():
    assert _type_through_a_laggy_daemon(["a", "b", "<bs>"]) == "a"


def test_a_typed_space_survives_the_daemon_stripping_it():
    assert _type_through_a_laggy_daemon(["f", "o", "o", " "]) == "foo "


def test_tracker_settles_once_the_last_reply_arrives():
    tracker = EditTracker()
    replies = []
    settled = []

    for value in ("a", "ab"):
        tracker.send(lambda v, done: replies.append(done), value, lambda: settled.append(1))
    assert tracker.pending == 2

    replies[0]()
    assert tracker.pending == 1 and settled == []
    replies[1]()
    assert tracker.pending == 0 and settled == [1]


def test_tracker_does_not_stick_when_sending_fails():
    tracker = EditTracker()

    def failing(_value, _done):
        raise ValueError("cannot build the call")

    with pytest.raises(ValueError):
        tracker.send(failing, "a", lambda: None)

    assert tracker.pending == 0
    assert remote_title_to_show("a", "", tracker.pending) == ""


def test_tracker_ignores_a_reply_it_was_not_waiting_for():
    tracker = EditTracker()
    replies = []
    tracker.send(lambda v, done: replies.append(done), "a", lambda: None)
    replies[0]()
    replies[0]()
    assert tracker.pending == 0
