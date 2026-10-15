"""Settings → Notion tab: the workspaces a meeting can be filed into."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...config.keyring_store import KeyringStore
from ...config.notion_config import (
    CONFIG_KEY,
    NotionWorkspace,
    dump_workspaces,
    extract_database_id,
    keyring_label,
    keyring_purpose,
    load_workspaces,
    workspace_id_for,
)
from .widgets import make_scroll_page

logger = logging.getLogger(__name__)

_TOKEN_PLACEHOLDER = "••••••••  (stored in the keyring)"


class _WorkspaceRow(Adw.ExpanderRow):
    """One workspace, editable in place."""

    def __init__(self, workspace: NotionWorkspace, has_token: bool, on_remove) -> None:
        super().__init__(title=workspace.name or "New workspace")
        self.workspace_id = workspace.id
        self._on_remove = on_remove

        self.name_entry = Adw.EntryRow(title="Workspace name")
        self.name_entry.set_text(workspace.name)
        self.name_entry.connect("changed", self._on_name_changed)
        self.add_row(self.name_entry)

        self.database_entry = Adw.EntryRow(title="Meetings database link or ID")
        self.database_entry.set_text(workspace.database_id)
        self.add_row(self.database_entry)

        self.token_entry = Adw.PasswordEntryRow(title="Integration token")
        # An existing token is never read back into the field: it stays in the
        # keyring, and an empty field on save means "leave it alone".
        if has_token:
            self.token_entry.set_text("")
            self.token_entry.set_title(f"Integration token — {_TOKEN_PLACEHOLDER}")
        self.add_row(self.token_entry)

        self.files_entry = Adw.EntryRow(title="Files property for the recording")
        self.files_entry.set_text(workspace.files_property)
        self.add_row(self.files_entry)

        remove = Gtk.Button(label="Remove this workspace")
        remove.add_css_class("destructive-action")
        remove.set_margin_top(6)
        remove.set_margin_bottom(6)
        remove.set_margin_start(12)
        remove.set_margin_end(12)
        remove.connect("clicked", lambda *_: self._on_remove(self))
        holder = Adw.ActionRow()
        holder.set_child(remove)
        self.add_row(holder)

    def _on_name_changed(self, *_) -> None:
        self.set_title(self.name_entry.get_text().strip() or "New workspace")

    def collect(self, taken: list[str]) -> NotionWorkspace | None:
        """Read the fields back, or None when the row is too empty to keep."""
        name = self.name_entry.get_text().strip()
        database_id = extract_database_id(self.database_entry.get_text())
        if not name or not database_id:
            return None
        identifier = self.workspace_id or workspace_id_for(name, taken)
        return NotionWorkspace(
            id=identifier,
            name=name,
            database_id=database_id,
            files_property=self.files_entry.get_text().strip(),
        )

    def take_token(self) -> str:
        """The newly typed token, if any. An untouched field returns empty."""
        return self.token_entry.get_text().strip()


class NotionPage:
    """Builds the Notion tab and writes workspaces back on save.

    Tokens do not travel through the config dict: each is written straight to
    the Secret Service under its own workspace id, because config.json is not a
    place a credential belongs and because one token per workspace is what a
    Notion integration actually is.
    """

    def __init__(self, cfg: dict, keyring_factory=KeyringStore) -> None:
        self._keyring_factory = keyring_factory
        self._rows: list[_WorkspaceRow] = []
        self._removed: list[str] = []
        self.widget = self._build(cfg)

    # ------------------------------------------------------------------

    def _build(self, cfg: dict) -> Gtk.Widget:
        scroll, box = make_scroll_page()

        intro = Adw.PreferencesGroup(
            title="Notion",
            description=(
                "File a finished meeting's notes, transcript and recording into a "
                "Notion page. A token belongs to one workspace, so add one entry per "
                "workspace: create an internal integration in that workspace's "
                "Settings → Connections, then share the meetings database with it."
            ),
        )
        box.append(intro)

        self._group = Adw.PreferencesGroup(title="Workspaces")
        for workspace in load_workspaces(cfg):
            self._add_row(workspace)
        box.append(self._group)

        add_button = Gtk.Button(label="Add a workspace")
        add_button.add_css_class("pill")
        add_button.set_halign(Gtk.Align.CENTER)
        add_button.connect("clicked", self._on_add_clicked)
        box.append(add_button)

        return scroll

    def _add_row(self, workspace: NotionWorkspace) -> None:
        row = _WorkspaceRow(workspace, self._has_token(workspace.id), self._on_remove_row)
        self._rows.append(row)
        self._group.add(row)

    def _on_add_clicked(self, *_) -> None:
        blank = NotionWorkspace(id="", name="", database_id="", files_property="Recording")
        self._add_row(blank)
        self._rows[-1].set_expanded(True)

    def _on_remove_row(self, row: _WorkspaceRow) -> None:
        if row.workspace_id:
            self._removed.append(row.workspace_id)
        self._rows.remove(row)
        self._group.remove(row)

    # ------------------------------------------------------------------

    def _store_for(self, workspace_id: str, name: str) -> KeyringStore:
        return self._keyring_factory(
            purpose=keyring_purpose(workspace_id), label=keyring_label(name)
        )

    def _has_token(self, workspace_id: str) -> bool:
        if not workspace_id:
            return False
        try:
            return bool(self._store_for(workspace_id, "").get())
        except Exception as exc:
            logger.warning("Could not read the Notion token for %s: %s", workspace_id, exc)
            return False

    # ------------------------------------------------------------------

    def apply(self, cfg: dict) -> None:
        """Write the workspaces into *cfg* and their tokens into the keyring."""
        workspaces: list[NotionWorkspace] = []
        taken: list[str] = []
        for row in self._rows:
            workspace = row.collect(taken)
            if workspace is None:
                continue
            taken.append(workspace.id)
            workspaces.append(workspace)
            token = row.take_token()
            if token:
                self._store_for(workspace.id, workspace.name).set(token)
        cfg[CONFIG_KEY] = dump_workspaces(workspaces)

        for workspace_id in self._removed:
            try:
                self._store_for(workspace_id, "").delete()
            except Exception as exc:
                logger.warning("Could not delete the Notion token for %s: %s", workspace_id, exc)
        self._removed.clear()
