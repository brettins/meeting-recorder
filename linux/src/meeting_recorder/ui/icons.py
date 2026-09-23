"""Resolves icon names against the running GTK icon theme."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from .icons_model import (
    DISPATCH_ICONS,
    NOTION_ICONS,
    SPLIT_ICONS,
    TAG_ICONS,
    TAG_LIST_ICONS,
    pick_icon_name,
)


def _has_icon(name: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    return bool(Gtk.IconTheme.get_for_display(display).has_icon(name))


def tag_icon() -> str:
    """Icon for "tag this item" — a tag shape where the theme has one."""
    return pick_icon_name(TAG_ICONS, _has_icon)


def tag_list_icon() -> str:
    """Icon for "manage the tag list"."""
    return pick_icon_name(TAG_LIST_ICONS, _has_icon)


def notion_icon() -> str:
    """Icon for "file this into Notion" — a send/export shape."""
    return pick_icon_name(NOTION_ICONS, _has_icon)


def split_icon() -> str:
    """Icon for "split this recording into separate meetings"."""
    return pick_icon_name(SPLIT_ICONS, _has_icon)


def dispatch_icon() -> str:
    """Icon for "file this into Dispatch", distinct from the Notion one."""
    return pick_icon_name(DISPATCH_ICONS, _has_icon)
