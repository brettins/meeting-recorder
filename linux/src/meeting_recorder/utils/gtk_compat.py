"""
Small GTK4 container helpers.

GTK4 removed ``Gtk.Container`` and its ``get_children()`` / ``add()`` / ``remove()``
child-management API in favour of the per-widget sibling list
(``get_first_child()`` / ``get_next_sibling()``). These helpers reproduce the two
GTK3 idioms the UI relied on: iterating a widget's children and clearing a box.
"""

from __future__ import annotations

from collections.abc import Iterator

from gi.repository import Gtk


def iter_children(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """Yield each direct child of ``widget`` (GTK4 replacement for get_children()).

    The next sibling is captured before yielding so the caller may remove the
    current child mid-iteration without breaking the walk.
    """
    child = widget.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        yield child
        child = nxt


def remove_all_children(box: Gtk.Widget) -> None:
    """Remove every child of ``box`` (GTK4 has no container-level clear)."""
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()


def set_tooltip(widget: Gtk.Widget, text: str | None) -> None:
    """Set a tooltip without Gtk's set_tooltip_text, which costs about 5 ms a call."""
    if getattr(widget, "_tip_text", None) == text and hasattr(widget, "_tip_text"):
        return
    widget._tip_text = text
    widget.set_has_tooltip(text is not None)
    if not getattr(widget, "_tip_connected", False):
        widget._tip_connected = True

        def _query(w, _x, _y, _keyboard, tooltip):
            if w._tip_text is None:
                return False
            tooltip.set_text(w._tip_text)
            return True

        widget.connect("query-tooltip", _query)
    if text:
        widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [text])
