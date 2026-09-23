"""Settings → Dispatch tab: the server a meeting is filed into, and its API token."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...config.dispatch_config import (
    COMPANY_KEY,
    TOKEN_KEY,
    URL_KEY,
    forget_token,
    normalise_url,
    token_for_config,
    token_store,
    token_warning,
    url_error,
)
from .widgets import make_scroll_page

logger = logging.getLogger(__name__)


class DispatchPage:
    """The Dispatch tab. A typed token replaces the stored one; an empty field keeps it."""

    def __init__(self, cfg: dict[str, Any], store_factory: Callable[[], Any] = token_store) -> None:
        self._store_factory = store_factory
        self._has_token = bool(cfg.get(TOKEN_KEY))
        self._forget = False
        self.widget = self._build(cfg)

    def _build(self, cfg: dict[str, Any]) -> Gtk.Widget:
        scroll, box = make_scroll_page()

        group = Adw.PreferencesGroup(
            title="Dispatch",
            description=(
                "File a finished meeting into Dispatch. Its notes and transcript go onto "
                "one meeting item, and each action item becomes a draft task in triage. "
                "The token is the one set as DISPATCH_API_TOKEN on the Dispatch server."
            ),
        )
        self._url = Adw.EntryRow(title="Dispatch URL, like https://zed.tailefa891.ts.net")
        self._url.set_text(str(cfg.get(URL_KEY) or ""))
        group.add(self._url)

        self._token = Adw.PasswordEntryRow(title=self._token_title())
        group.add(self._token)

        self._company = Adw.EntryRow(title="Default company (blank files under Personal)")
        self._company.set_text(str(cfg.get(COMPANY_KEY) or ""))
        group.add(self._company)
        box.append(group)

        self._forget_btn = Gtk.Button(label="Forget the stored token")
        self._forget_btn.add_css_class("pill")
        self._forget_btn.set_halign(Gtk.Align.CENTER)
        self._forget_btn.set_visible(self._has_token)
        self._forget_btn.connect("clicked", self._on_forget_clicked)
        box.append(self._forget_btn)
        return scroll

    def _token_title(self) -> str:
        return "API token — stored, type to replace" if self._has_token else "API token"

    def _on_forget_clicked(self, *_: object) -> None:
        self._forget = True
        self._has_token = False
        self._token.set_text("")
        self._token.set_title(self._token_title())
        self._forget_btn.set_visible(False)

    def warning(self) -> str | None:
        """A problem with what was typed, to show once the dialog has saved."""
        return url_error(self._url.get_text()) or token_warning(self._token.get_text())

    def apply(self, cfg: dict[str, Any]) -> None:
        """Write the URL and company into *cfg*, and a new token to the keyring."""
        cfg[URL_KEY] = normalise_url(self._url.get_text()) or self._url.get_text().strip()
        cfg[COMPANY_KEY] = self._company.get_text().strip()
        typed = self._token.get_text().strip()
        try:
            if typed:
                cfg[TOKEN_KEY] = token_for_config(typed, self._store_factory())
            elif self._forget:
                cfg[TOKEN_KEY] = forget_token(self._store_factory())
        except Exception as exc:
            logger.warning("Could not store the Dispatch token: %s", type(exc).__name__)
