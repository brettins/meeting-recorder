"""Loads the application stylesheet once per display."""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

logger = logging.getLogger(__name__)

STYLE_PATH = Path(__file__).resolve().parent.parent / "assets" / "style.css"


def load_stylesheet() -> None:
    """Attach the app stylesheet to the default display. Failures are non-fatal."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(STYLE_PATH))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    except Exception as exc:
        logger.warning("Could not load the application stylesheet: %s", exc)
