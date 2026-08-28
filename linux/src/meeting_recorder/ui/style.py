"""Loads the application stylesheet and keeps it in step with the system theme.

Tag chips need a different palette per theme, not one compromise palette: a chip
must be light enough to clear WCAG 1.4.11 against a dark row and dark enough to
clear it against a white one, which no single colour does. The base sheet holds
the dark palette; a second sheet overrides the colour definitions when the theme
is light, and is attached or detached as the theme changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
STYLE_PATH = _ASSETS / "style.css"
LIGHT_STYLE_PATH = _ASSETS / "style-light.css"

_BASE_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
# Must outrank the base sheet so the light palette wins where both define a colour.
_OVERRIDE_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1

_light_provider: Gtk.CssProvider | None = None
_light_attached = False


def load_stylesheet() -> None:
    """Attach the stylesheet and follow the system light/dark preference."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return

        base = Gtk.CssProvider()
        base.load_from_path(str(STYLE_PATH))
        Gtk.StyleContext.add_provider_for_display(display, base, _BASE_PRIORITY)

        global _light_provider
        _light_provider = Gtk.CssProvider()
        _light_provider.load_from_path(str(LIGHT_STYLE_PATH))

        manager = Adw.StyleManager.get_default()
        _apply_theme(display, manager)
        manager.connect("notify::dark", lambda *_: _apply_theme(display, manager))
    except Exception as exc:
        logger.warning("Could not load the application stylesheet: %s", exc)


def _apply_theme(display: Gdk.Display, manager: Adw.StyleManager) -> None:
    """Attach the light palette only while the theme is light."""
    global _light_attached
    if _light_provider is None:
        return
    want_light = not manager.get_dark()
    if want_light and not _light_attached:
        Gtk.StyleContext.add_provider_for_display(display, _light_provider, _OVERRIDE_PRIORITY)
        _light_attached = True
    elif not want_light and _light_attached:
        Gtk.StyleContext.remove_provider_for_display(display, _light_provider)
        _light_attached = False
