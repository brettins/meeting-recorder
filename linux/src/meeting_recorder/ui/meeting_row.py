"""
The one meeting row, used by both the Library and the Record tab.

Previously the Record tab had a "Background Jobs" panel with its own row shape
and the Library had a separate meeting list, so a recording changed appearance
as it moved between them. A job is a *state* of a meeting, not another kind of
object, so both views now render ``core/row_model.py:RowModel`` through this
widget and differ only in which models they supply.

Rows are **updated in place**, never rebuilt: a snapshot arrives once a second
while recording, and destroying a button between a mouse press and its release
swallows the click (the same defect that made the Stop button intermittent). The
action buttons are therefore only reconstructed when the action *tuple* changes.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gtk, Pango

from ..core.row_model import (
    AI_TITLE,
    CANCEL,
    DELETE,
    DETAILS,
    DISMISS,
    ERROR,
    OPEN_FOLDER,
    PROCESSING,
    RECORDING,
    RENAME,
    RETRY,
    ROW_STATE_CSS,
    SUMMARIZE,
    TAG,
    RowModel,
)
from ..utils.gtk_compat import remove_all_children
from .icons import tag_icon
from .tag_widgets import make_tag_chip

# id -> (kind, icon-or-label, tooltip). "icon" buttons are the quiet per-meeting
# affordances; "label" buttons are the loud job-state ones.
_ACTION_SPECS: dict[str, tuple[str, str, str]] = {
    CANCEL: ("label", "Cancel", "Stop processing this recording"),
    RETRY: ("label", "Retry", "Run the pipeline again"),
    DETAILS: ("label", "Details", "Show the full error"),
    DISMISS: ("icon", "window-close-symbolic", "Dismiss"),
    AI_TITLE: ("icon", "starred-symbolic", "Generate a title from meeting notes"),
    SUMMARIZE: ("icon", "system-run-symbolic", "Transcribe and summarize this recording"),
    RENAME: ("icon", "document-edit-symbolic", "Rename meeting"),
    OPEN_FOLDER: ("icon", "folder-open-symbolic", "Open folder"),
    DELETE: ("icon", "user-trash-symbolic", "Delete this meeting"),
}


class MeetingRow(Gtk.ListBoxRow):
    """One meeting, in whatever state it happens to be."""

    def __init__(
        self,
        model: RowModel,
        *,
        on_action: Callable[[str, RowModel], None],
        colors: dict[str, str],
        show_check: bool = False,
        on_check_toggled: Callable[[], None] | None = None,
        tag_popover_factory: Callable[[RowModel], Gtk.Popover] | None = None,
    ) -> None:
        super().__init__()
        self.set_activatable(False)
        self.model = model
        self._on_action = on_action
        self._colors = colors
        self._show_check = show_check
        self._tag_popover_factory = tag_popover_factory
        self._rendered_actions: tuple[str, ...] | None = None
        self._rendered_tags: list[str] | None = None
        self._state_css: str | None = None

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_child(box)

        self.check = Gtk.CheckButton()
        self.check.set_valign(Gtk.Align.CENTER)
        if on_check_toggled is not None:
            self.check.connect("toggled", lambda *_: on_check_toggled())
        box.append(self.check)

        # State indicator: a spinner while something is happening, an icon once
        # it has. Both live in the row; only their visibility changes.
        self._spinner = Gtk.Spinner()
        self._spinner.set_valign(Gtk.Align.CENTER)
        box.append(self._spinner)

        self._state_icon = Gtk.Image()
        self._state_icon.set_valign(Gtk.Align.CENTER)
        box.append(self._state_icon)

        self._title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._title_box.set_hexpand(True)
        box.append(self._title_box)

        self._primary = Gtk.Label(xalign=0)
        self._primary.set_ellipsize(Pango.EllipsizeMode.END)
        self._title_box.append(self._primary)

        self._secondary = Gtk.Label(xalign=0)
        self._secondary.set_ellipsize(Pango.EllipsizeMode.END)
        self._secondary.add_css_class("caption")
        self._secondary.add_css_class("dim-label")
        self._title_box.append(self._secondary)

        self._chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._chips.set_margin_top(2)
        self._title_box.append(self._chips)

        self._actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._actions.set_valign(Gtk.Align.CENTER)
        box.append(self._actions)

        self.update(model, colors)

    # ------------------------------------------------------------------

    @property
    def primary_label(self) -> Gtk.Label:
        """The title label — the explorer attaches its double-click gesture here."""
        return self._primary

    @property
    def title_box(self) -> Gtk.Box:
        """Container the inline rename entry is prepended to."""
        return self._title_box

    def update(self, model: RowModel, colors: dict[str, str]) -> None:
        """Re-render for a new model, touching only what actually changed."""
        self.model = model
        self._colors = colors

        self._primary.set_text(model.title)
        self._secondary.set_text(model.subtitle)
        if model.error_msg:
            self.set_tooltip_text(model.error_msg)
        else:
            self.set_tooltip_text(None)

        busy = model.state in (RECORDING, PROCESSING)
        self._spinner.set_visible(busy)
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()

        self._state_icon.set_visible(not busy)
        if model.state == ERROR:
            self._state_icon.set_from_icon_name("dialog-error-symbolic")
        else:
            self._state_icon.set_from_icon_name("emblem-documents-symbolic")

        # Hidden rather than removed: a row that can't be selected still has to
        # line its title up with the rows above and below it.
        self.check.set_visible(self._show_check)
        self.check.set_opacity(1.0 if model.selectable else 0.0)
        self.check.set_sensitive(model.selectable)
        if not model.selectable and self.check.get_active():
            self.check.set_active(False)

        self._apply_state_class(model.state)

        if model.tags != self._rendered_tags:
            self._rendered_tags = list(model.tags)
            remove_all_children(self._chips)
            for name in model.tags:
                if name in colors:
                    self._chips.append(make_tag_chip(name, colors[name]))
            self._chips.set_visible(bool(model.tags))

        if model.actions != self._rendered_actions:
            self._rendered_actions = model.actions
            self._rebuild_actions(model)

    def _apply_state_class(self, state: str) -> None:
        """Colour the status line for the row's state, and only that line.

        The state class goes on the subtitle rather than the row: the subtitle is
        the text that actually says "Transcribing…" or "Error: …", and tinting a
        whole row would drown the tag chips it also carries.
        """
        css = ROW_STATE_CSS.get(state)
        if css == self._state_css:
            return
        if self._state_css:
            self._secondary.remove_css_class(self._state_css)
        if css:
            self._secondary.add_css_class(css)
            self._secondary.remove_css_class("dim-label")
        else:
            self._secondary.add_css_class("dim-label")
        self._state_css = css

    def _rebuild_actions(self, model: RowModel) -> None:
        remove_all_children(self._actions)
        for action in model.actions:
            if action == TAG:
                btn = Gtk.MenuButton(icon_name=tag_icon())
                btn.set_tooltip_text("Tag this meeting")
                if self._tag_popover_factory is not None:
                    # Attached up front: a Gtk.MenuButton with nothing to show
                    # renders as a dimmed, dead button.
                    btn.set_popover(self._tag_popover_factory(model))
            else:
                kind, art, tooltip = _ACTION_SPECS[action]
                btn = Gtk.Button(label=art) if kind == "label" else Gtk.Button(icon_name=art)
                btn.set_tooltip_text(tooltip)
                btn.connect("clicked", lambda *_, a=action: self._on_action(a, self.model))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            self._actions.append(btn)

    def set_action_sensitive(self, action: str, sensitive: bool) -> None:
        """Grey out one action (e.g. Transcribe, once it has been clicked)."""
        for i, name in enumerate(self._rendered_actions or ()):
            if name == action:
                child = _nth_child(self._actions, i)
                if child is not None:
                    child.set_sensitive(sensitive)

    def show_busy_action(self, busy: bool) -> None:
        """Swap the action area for a spinner while a row-local task runs."""
        if busy:
            remove_all_children(self._actions)
            self._rendered_actions = None
            spinner = Gtk.Spinner()
            spinner.start()
            spinner.set_valign(Gtk.Align.CENTER)
            self._actions.append(spinner)
        else:
            self._rebuild_actions(self.model)
            self._rendered_actions = self.model.actions


def _nth_child(box: Gtk.Box, index: int) -> Gtk.Widget | None:
    child = box.get_first_child()
    for _ in range(index):
        if child is None:
            return None
        child = child.get_next_sibling()
    return child


class RowListView:
    """Reconciles a Gtk.ListBox against an ordered list of RowModels.

    Rows are matched by ``key`` (the meeting directory) and updated in place, so
    the once-a-second snapshot refresh never destroys a button the user is in the
    middle of clicking. Widgets are only created, removed or re-ordered when the
    set or order of meetings actually changes.
    """

    def __init__(
        self,
        list_box: Gtk.ListBox,
        *,
        on_action: Callable[[str, RowModel], None],
        show_check: bool = False,
        on_check_toggled: Callable[[], None] | None = None,
        tag_popover_factory: Callable[[RowModel], Gtk.Popover] | None = None,
        on_row_created: Callable[[MeetingRow], None] | None = None,
    ) -> None:
        self._list_box = list_box
        self._on_action = on_action
        self._show_check = show_check
        self._on_check_toggled = on_check_toggled
        self._tag_popover_factory = tag_popover_factory
        self._on_row_created = on_row_created
        self._rows: dict[str, MeetingRow] = {}
        self._order: list[str] = []

    @property
    def rows(self) -> list[MeetingRow]:
        return [self._rows[key] for key in self._order if key in self._rows]

    def row(self, key: str) -> MeetingRow | None:
        return self._rows.get(key)

    def render(self, models: list[RowModel], colors: dict[str, str]) -> None:
        keys = [m.key for m in models]
        for key in [k for k in self._rows if k not in set(keys)]:
            self._list_box.remove(self._rows.pop(key))

        for model in models:
            existing = self._rows.get(model.key)
            if existing is None:
                row = MeetingRow(
                    model,
                    on_action=self._on_action,
                    colors=colors,
                    show_check=self._show_check,
                    on_check_toggled=self._on_check_toggled,
                    tag_popover_factory=self._tag_popover_factory,
                )
                self._rows[model.key] = row
                self._list_box.append(row)
                if self._on_row_created is not None:
                    self._on_row_created(row)
            else:
                existing.update(model, colors)

        if keys != self._order:
            self._reorder(keys)
        self._order = keys
        self._list_box.set_visible(bool(keys))

    def _reorder(self, keys: list[str]) -> None:
        # Detaching and re-attaching keeps the widget objects (and their signal
        # connections) alive; only their position in the box changes.
        for row in list(self._rows.values()):
            self._list_box.remove(row)
        for key in keys:
            self._list_box.append(self._rows[key])

    def remove(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row is not None:
            self._list_box.remove(row)
        if key in self._order:
            self._order.remove(key)
        self._list_box.set_visible(bool(self._rows))

    def clear(self) -> None:
        for row in list(self._rows.values()):
            self._list_box.remove(row)
        self._rows.clear()
        self._order.clear()


def section_group(title: str) -> tuple[Adw.Clamp, Gtk.ListBox]:
    """A titled, boxed list — the container both views drop MeetingRows into."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    heading = Gtk.Label(label=title, xalign=0)
    heading.add_css_class("heading")
    heading.set_margin_start(4)
    box.append(heading)

    list_box = Gtk.ListBox()
    list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    list_box.add_css_class("boxed-list")
    list_box.set_valign(Gtk.Align.START)
    box.append(list_box)

    clamp = Adw.Clamp(maximum_size=760)
    clamp.set_child(box)
    return clamp, list_box
