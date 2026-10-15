"""
The "File to Notion" window: pick the page a meeting belongs to, then file it.

Filing almost always attaches to a meeting page someone already made in Notion,
so this opens on the list of pages in the configured database with the one
dated the same day already selected. Creating a page is behind its own button,
because a duplicate page is worse than a moment spent choosing.

All the decisions live in ``core/notion_picker.py``; this module is widgets,
threads and callbacks.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..config.keyring_store import KeyringStore
from ..config.notion_config import NotionWorkspace, keyring_purpose
from ..core.notion_picker import (
    default_new_title,
    filing_summary,
    filter_pages,
    page_subtitle,
    suggest_page,
)
from ..processing.notion_filing import file_meeting
from ..services.notion_service import NotionClient, NotionError, NotionPage, free_plan_hint
from ..utils.gtk_compat import remove_all_children

logger = logging.getLogger(__name__)


def _idle(fn: Callable, *args: object) -> None:
    from gi.repository import GLib

    GLib.idle_add(fn, *args, priority=GLib.PRIORITY_DEFAULT)


class NotionFileDialog(Adw.Window):
    """Pick a Notion page for one meeting and file its contents into it."""

    def __init__(
        self,
        parent: Gtk.Window,
        meeting,
        workspaces: list[NotionWorkspace],
        *,
        keyring_factory: Callable[..., KeyringStore] = KeyringStore,
        client_factory: Callable[[str], NotionClient] = NotionClient,
        on_finished: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(title="File to Notion", transient_for=parent, modal=True)
        self.set_default_size(560, 620)

        self._meeting = meeting
        self._workspaces = workspaces
        self._keyring_factory = keyring_factory
        self._client_factory = client_factory
        self._on_finished = on_finished

        self._pages: list[NotionPage] = []
        self._selected: NotionPage | None = None
        self._data_source_id = ""
        self._busy = False

        self._build()
        self._load_pages()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)

        self._file_btn = Gtk.Button(label="File here")
        self._file_btn.add_css_class("suggested-action")
        self._file_btn.set_sensitive(False)
        self._file_btn.connect("clicked", self._on_file_clicked)
        header.pack_end(self._file_btn)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        toolbar.set_content(box)

        if len(self._workspaces) > 1:
            names = Gtk.StringList.new([w.name for w in self._workspaces])
            self._workspace_combo = Adw.ComboRow(title="Workspace")
            self._workspace_combo.set_model(names)
            self._workspace_combo.connect("notify::selected", lambda *_: self._load_pages())
            group = Adw.PreferencesGroup()
            group.add(self._workspace_combo)
            box.append(group)
        else:
            self._workspace_combo = None

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search meeting pages…")
        self._search.connect("search-changed", lambda *_: self._render_pages())
        box.append(self._search)

        self._status = Gtk.Label(xalign=0)
        self._status.add_css_class("dim-label")
        self._status.set_wrap(True)
        box.append(self._status)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("boxed-list")
        self._list.connect("row-selected", self._on_row_selected)
        scroll.set_child(self._list)
        box.append(scroll)

        self._new_title = Adw.EntryRow(title="New page title")
        self._new_title.set_text(
            default_new_title(getattr(self._meeting, "title", None), self._meeting.date)
        )
        self._new_group = Adw.PreferencesGroup()
        self._new_group.add(self._new_title)
        self._new_group.set_visible(False)
        box.append(self._new_group)

        self._create_btn = Gtk.Button(label="Create a new page instead")
        self._create_btn.set_halign(Gtk.Align.CENTER)
        self._create_btn.connect("clicked", self._on_create_toggled)
        box.append(self._create_btn)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @property
    def _workspace(self) -> NotionWorkspace | None:
        if not self._workspaces:
            return None
        if self._workspace_combo is None:
            return self._workspaces[0]
        index = self._workspace_combo.get_selected()
        if 0 <= index < len(self._workspaces):
            return self._workspaces[index]
        return self._workspaces[0]

    def _token(self, workspace: NotionWorkspace) -> str:
        store = self._keyring_factory(purpose=keyring_purpose(workspace.id))
        return store.get() or ""

    def _load_pages(self) -> None:
        workspace = self._workspace
        if workspace is None:
            self._set_status("No Notion workspace is configured. Add one in Settings → Notion.")
            return
        self._set_status(f"Loading pages from {workspace.name}…")
        self._list.set_sensitive(False)

        def _bg() -> None:
            try:
                token = self._token(workspace)
                if not token:
                    raise NotionError(
                        f"No integration token is stored for {workspace.name}. "
                        "Add it in Settings → Notion."
                    )
                client = self._client_factory(token)
                data_source_id = client.resolve_data_source(workspace.database_id)
                pages = client.recent_pages(data_source_id)
                _idle(self._on_pages_loaded, data_source_id, pages, None)
            except Exception as exc:
                logger.warning("Could not load Notion pages: %s", exc)
                _idle(self._on_pages_loaded, "", [], str(exc))

        threading.Thread(target=_bg, daemon=True).start()

    def _on_pages_loaded(self, data_source_id: str, pages: list, error: str | None) -> bool:
        self._list.set_sensitive(True)
        if error:
            self._set_status(error)
            return False
        self._data_source_id = data_source_id
        self._pages = list(pages)
        self._render_pages()
        hint = self._audio_hint()
        self._set_status(hint or f"{len(self._pages)} pages")
        return False

    def _audio_hint(self) -> str | None:
        audio = self._audio_path()
        if audio is None:
            return None
        return free_plan_hint(audio.stat().st_size)

    # ------------------------------------------------------------------
    # Page list
    # ------------------------------------------------------------------

    def _render_pages(self) -> None:
        remove_all_children(self._list)
        matches = filter_pages(self._pages, self._search.get_text())
        suggestion = suggest_page(matches, self._meeting.date)
        selected_row = None
        for page in matches:
            row = Adw.ActionRow(title=page.title, subtitle=page_subtitle(page))
            row.page = page
            if suggestion is not None and page.id == suggestion.id:
                row.add_suffix(Gtk.Label(label="same day"))
                selected_row = row
            self._list.append(row)
        if selected_row is not None:
            self._list.select_row(selected_row)

    def _on_row_selected(self, _list, row) -> None:
        self._selected = getattr(row, "page", None) if row is not None else None
        self._file_btn.set_sensitive(self._selected is not None and not self._busy)

    def _on_create_toggled(self, *_) -> None:
        creating = not self._new_group.get_visible()
        self._new_group.set_visible(creating)
        self._create_btn.set_label(
            "Choose an existing page instead" if creating else "Create a new page instead"
        )
        self._file_btn.set_label("Create and file" if creating else "File here")
        self._file_btn.set_sensitive(creating or self._selected is not None)

    # ------------------------------------------------------------------
    # Filing
    # ------------------------------------------------------------------

    def _audio_path(self) -> Path | None:
        for name in ("recording.mp3", "recording.m4a"):
            candidate = self._meeting.path / name
            if candidate.exists():
                return candidate
        return None

    def _on_file_clicked(self, *_) -> None:
        workspace = self._workspace
        if workspace is None or self._busy:
            return
        creating = self._new_group.get_visible()
        if not creating and self._selected is None:
            return

        self._busy = True
        self._file_btn.set_sensitive(False)
        self._set_status("Filing…")

        target = self._selected
        new_title = self._new_title.get_text().strip()
        meeting_path = self._meeting.path
        audio = self._audio_path()

        def _bg() -> None:
            try:
                client = self._client_factory(self._token(workspace))
                page = (
                    client.create_page(self._data_source_id, new_title or "Untitled meeting")
                    if creating
                    else target
                )
                result = file_meeting(
                    client,
                    page,
                    notes_path=meeting_path / "notes.md",
                    transcript_path=meeting_path / "transcript.md",
                    audio_path=audio,
                    files_property=workspace.files_property,
                    on_progress=lambda message: _idle(self._set_status, message),
                )
                _idle(self._on_filed, result, None)
            except Exception as exc:
                logger.warning("Could not file to Notion: %s", exc)
                _idle(self._on_filed, None, str(exc))

        threading.Thread(target=_bg, daemon=True).start()

    def _on_filed(self, result, error: str | None) -> bool:
        self._busy = False
        if error:
            self._file_btn.set_sensitive(True)
            self._set_status(error)
            return False
        summary = filing_summary(
            notes_written=result.notes_written,
            transcript_written=result.transcript_written,
            audio_attached=result.audio_attached,
            page_title=result.page.title,
        )
        if result.warnings:
            summary = f"{summary} {' '.join(result.warnings)}"
        if self._on_finished is not None:
            self._on_finished(summary)
        self.close()
        return False

    def _set_status(self, message: str) -> bool:
        self._status.set_text(message)
        return False
