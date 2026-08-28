"""Tag UI: coloured chips, the per-meeting assignment popover, and the manage dialog.

Colours are applied purely as CSS classes (``tag-chip`` plus ``tag-<palette>``);
nothing here knows what a colour actually looks like. See ``assets/style.css``.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..config.tags import (
    TAG_COLORS,
    Tag,
    add_tag,
    normalize_color,
    normalize_name,
    recolor_tag,
    remove_tag,
    rename_tag,
)


def make_tag_chip(name: str, color: str) -> Gtk.Label:
    """A small rounded label tinted with the tag's palette colour."""
    chip = Gtk.Label(label=name)
    chip.add_css_class("tag-chip")
    chip.add_css_class(f"tag-{normalize_color(color)}")
    chip.set_valign(Gtk.Align.CENTER)
    return chip


def make_tag_swatch(color: str) -> Gtk.Box:
    """A solid colour dot used in the manage dialog."""
    swatch = Gtk.Box()
    swatch.add_css_class("tag-swatch")
    swatch.add_css_class(f"tag-{normalize_color(color)}")
    swatch.set_valign(Gtk.Align.CENTER)
    return swatch


class TagAssignPopover(Gtk.Popover):
    """Checkbox list of every known tag, for assigning them to one meeting."""

    def __init__(
        self,
        registry: list[Tag],
        selected: list[str],
        on_change: Callable[[list[str]], None],
        on_create: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._registry = registry
        self._selected = list(selected)
        self._on_change = on_change
        self._on_create = on_create
        self._checks: dict[str, Gtk.CheckButton] = {}

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        if not registry:
            empty = Gtk.Label(label="No tags yet")
            empty.add_css_class("dim-label")
            box.append(empty)

        for tag in registry:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            check = Gtk.CheckButton()
            check.set_active(any(s.casefold() == tag.name.casefold() for s in self._selected))
            check.connect("toggled", self._on_toggled, tag.name)
            self._checks[tag.name] = check
            row.append(check)
            row.append(make_tag_chip(tag.name, tag.color))
            box.append(row)

        box.append(Gtk.Separator())

        self._entry = Gtk.Entry(placeholder_text="New tag…")
        self._entry.set_max_length(40)
        self._entry.connect("activate", self._on_entry_activate)
        box.append(self._entry)

        self.set_child(box)

    def _on_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        if check.get_active():
            if not any(s.casefold() == name.casefold() for s in self._selected):
                self._selected.append(name)
        else:
            self._selected = [s for s in self._selected if s.casefold() != name.casefold()]
        self._on_change(list(self._selected))

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        name = normalize_name(entry.get_text())
        if not name:
            return
        entry.set_text("")
        self._on_create(name)
        self.popdown()


class TagManageDialog(Adw.Window):
    """Add, rename, recolour and delete tags in the global registry."""

    def __init__(
        self,
        parent: Gtk.Window,
        registry: list[Tag],
        on_save: Callable[[list[Tag]], None],
    ) -> None:
        super().__init__(title="Manage Tags", transient_for=parent, modal=True)
        self.set_default_size(420, 480)
        self._tags = list(registry)
        self._on_save = on_save

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_valign(Gtk.Align.START)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(self._list)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._new_entry = Gtk.Entry(placeholder_text="New tag…", hexpand=True)
        self._new_entry.set_max_length(40)
        self._new_entry.connect("activate", lambda *_: self._add())
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add tag")
        add_btn.connect("clicked", lambda *_: self._add())
        add_row.append(self._new_entry)
        add_row.append(add_btn)
        content.append(add_row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(content)
        toolbar.set_content(scroll)
        self.set_content(toolbar)

        self._rebuild()

    def _commit(self) -> None:
        self._on_save(list(self._tags))
        self._rebuild()

    def _add(self) -> None:
        name = self._new_entry.get_text()
        if not normalize_name(name):
            return
        self._new_entry.set_text("")
        # Cycle through the palette so a new tag rarely collides with an existing colour.
        color = TAG_COLORS[len(self._tags) % len(TAG_COLORS)]
        self._tags = add_tag(self._tags, name, color)
        self._commit()

    def _rebuild(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

        if not self._tags:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            empty = Gtk.Label(label="No tags yet")
            empty.add_css_class("dim-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            row.set_child(empty)
            self._list.append(row)
            return

        for tag in self._tags:
            self._list.append(self._build_row(tag))

    def _build_row(self, tag: Tag) -> Gtk.ListBoxRow:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        row.set_margin_start(10)
        row.set_margin_end(10)

        colour_btn = Gtk.MenuButton()
        colour_btn.add_css_class("flat")
        colour_btn.set_tooltip_text("Change colour")
        colour_btn.set_child(make_tag_swatch(tag.color))
        colour_btn.set_popover(self._colour_popover(tag))
        row.append(colour_btn)

        entry = Gtk.Entry(text=tag.name, hexpand=True)
        entry.set_max_length(40)
        entry.connect("activate", self._on_rename, tag.name)
        row.append(entry)

        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.set_tooltip_text("Delete tag")
        del_btn.connect("clicked", self._on_delete, tag.name)
        row.append(del_btn)

        lb_row = Gtk.ListBoxRow()
        lb_row.set_activatable(False)
        lb_row.set_child(row)
        return lb_row

    def _colour_popover(self, tag: Tag) -> Gtk.Popover:
        pop = Gtk.Popover()
        grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        for colour in TAG_COLORS:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.set_child(make_tag_swatch(colour))
            btn.set_tooltip_text(colour.capitalize())
            btn.connect("clicked", self._on_recolor, tag.name, colour, pop)
            grid.append(btn)
        pop.set_child(grid)
        return pop

    def _on_recolor(self, _btn: Gtk.Button, name: str, colour: str, pop: Gtk.Popover) -> None:
        pop.popdown()
        self._tags = recolor_tag(self._tags, name, colour)
        self._commit()

    def _on_rename(self, entry: Gtk.Entry, old: str) -> None:
        self._tags = rename_tag(self._tags, old, entry.get_text())
        self._commit()

    def _on_delete(self, _btn: Gtk.Button, name: str) -> None:
        self._tags = remove_tag(self._tags, name)
        self._commit()
