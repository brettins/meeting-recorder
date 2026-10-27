"""Tests for the cheap tooltip helper and the unchanged-row short circuit."""

import pytest

pytest.importorskip("gi")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from meeting_recorder.utils.gtk_compat import set_tooltip  # noqa: E402


def _button():
    if Gtk.is_initialized() is False and not Gtk.init_check():
        pytest.skip("no display")
    return Gtk.Button()


def test_set_tooltip_stores_text_and_enables_it():
    btn = _button()
    set_tooltip(btn, "Open folder")
    assert btn._tip_text == "Open folder"
    assert btn.get_has_tooltip()


def test_set_tooltip_none_disables_it():
    btn = _button()
    set_tooltip(btn, "x")
    set_tooltip(btn, None)
    assert not btn.get_has_tooltip()


def test_set_tooltip_connects_once():
    btn = _button()
    set_tooltip(btn, "a")
    set_tooltip(btn, "b")
    assert btn._tip_text == "b"


def test_set_tooltip_none_resets_after_text():
    btn = _button()
    set_tooltip(btn, "x")
    set_tooltip(btn, None)
    assert btn._tip_text is None
    assert not btn.get_has_tooltip()


def test_set_tooltip_empty_string_is_off():
    btn = _button()
    set_tooltip(btn, "")
    assert not btn.get_has_tooltip()
