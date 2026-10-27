"""MeetingRow.update skips unchanged models and keeps row-local state."""

import pytest

pytest.importorskip("gi")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from meeting_recorder.core.row_model import RowModel  # noqa: E402
from meeting_recorder.ui.meeting_row import MeetingRow  # noqa: E402


def _row(tags=None):
    if not Gtk.init_check():
        pytest.skip("no display")
    Adw.init()
    model = RowModel(key="k", title="T", subtitle="S", tags=list(tags or []))
    return MeetingRow(model, on_action=lambda *_: None, colors={"a": "red"}), model


def test_identical_update_keeps_the_busy_spinner():
    row, model = _row()
    row.show_busy_action(True)
    row.update(model, {"a": "red"})
    first = row._actions.get_first_child()
    assert isinstance(first, Gtk.Spinner)


def test_changed_title_redraws():
    row, model = _row()
    row.update(RowModel(key="k", title="New", subtitle="S"), {"a": "red"})
    assert row.primary_label.get_text() == "New"


def test_in_place_tag_mutation_is_still_seen():
    row, model = _row(tags=["a"])
    model.tags.append("b")
    row.update(model, {"a": "red", "b": "blue"})
    assert row._rendered_tags == ["a", "b"]
