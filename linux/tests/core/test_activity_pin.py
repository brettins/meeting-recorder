"""Tests for the xprop activity helpers."""

from meeting_recorder.core import activity_pin

CURRENT = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER = "bbbbbbbb-0000-0000-0000-000000000002"


def test_set_command_pins_one_activity():
    assert activity_pin.set_command(42, CURRENT) == [
        "xprop",
        "-id",
        "42",
        "-f",
        "_KDE_NET_WM_ACTIVITIES",
        "8s",
        "-set",
        "_KDE_NET_WM_ACTIVITIES",
        CURRENT,
    ]


def test_read_command_names_the_property():
    assert activity_pin.read_command(42) == ["xprop", "-id", "42", "_KDE_NET_WM_ACTIVITIES"]


def test_pins_when_window_is_on_other_activities_only():
    assert activity_pin.needs_pin(f'_KDE_NET_WM_ACTIVITIES(STRING) = "{OTHER}"', CURRENT)


def test_no_pin_when_property_missing_or_empty():
    assert not activity_pin.needs_pin("_KDE_NET_WM_ACTIVITIES:  not found.", CURRENT)
    assert not activity_pin.needs_pin('_KDE_NET_WM_ACTIVITIES(STRING) = ""', CURRENT)


def test_no_pin_when_window_is_on_all_activities():
    assert not activity_pin.needs_pin(f'= "{activity_pin.NULL_ACTIVITY}"', CURRENT)


def test_no_pin_when_current_is_among_several():
    assert not activity_pin.needs_pin(f'= "{OTHER},{CURRENT}"', CURRENT)
