"""The "File into Dispatch" window: confirm the company and the draft tasks, then file."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..config.dispatch_config import (
    COMPANY_KEY,
    URL_KEY,
    normalise_url,
    read_token,
    token_store,
)
from ..processing.dispatch_filing import (
    file_meeting,
    filed_summary,
    meeting_title,
    proposed_actions,
    read_meeting,
)
from ..services.dispatch_service import DispatchClient

logger = logging.getLogger(__name__)


class DispatchFileDialog(Adw.Window):
    """File one meeting into Dispatch, its action lines as draft tasks."""

    def __init__(
        self,
        parent: Gtk.Window | None,
        meeting: Any,
        config: dict[str, Any],
        *,
        store_factory: Callable[[], Any] = token_store,
        client_factory: Callable[[str, str], DispatchClient] = DispatchClient,
        on_finished: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(title="File into Dispatch", transient_for=parent, modal=True)
        self.set_default_size(520, 560)

        self._meeting = meeting
        self._config = config
        self._store_factory = store_factory
        self._client_factory = client_factory
        self._on_finished = on_finished
        self._checks: list[tuple[Gtk.CheckButton, str]] = []
        self._busy = False

        self._build()

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self._file_btn = Gtk.Button(label="File")
        self._file_btn.add_css_class("suggested-action")
        self._file_btn.connect("clicked", self._on_file_clicked)
        header.pack_end(self._file_btn)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        scroll.set_child(box)
        toolbar.set_content(scroll)

        meeting_group = Adw.PreferencesGroup(
            description="Filing again updates this meeting in Dispatch rather than adding another."
        )
        name = meeting_title(getattr(self._meeting, "title", None), self._meeting.date)
        meeting_group.add(Adw.ActionRow(title=GLib.markup_escape_text(name)))
        self._company = Adw.EntryRow(title="Company (blank files under Personal)")
        self._company.set_text(str(self._config.get(COMPANY_KEY) or ""))
        meeting_group.add(self._company)
        box.append(meeting_group)

        actions = proposed_actions(read_meeting(self._meeting.path)[0])
        tasks_group = Adw.PreferencesGroup(
            title="Draft tasks",
            description=(
                "Each ticked line becomes a task in Dispatch's triage."
                if actions
                else "The notes have no action items, so only the meeting is filed."
            ),
        )
        for line in actions:
            check = Gtk.CheckButton()
            check.set_active(True)
            check.set_valign(Gtk.Align.CENTER)
            row = Adw.ActionRow(title=GLib.markup_escape_text(line))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            tasks_group.add(row)
            self._checks.append((check, line))
        box.append(tasks_group)

        self._status = Gtk.Label(xalign=0)
        self._status.add_css_class("dim-label")
        self._status.set_wrap(True)
        box.append(self._status)

    def _chosen(self) -> list[str]:
        return [line for check, line in self._checks if check.get_active()]

    def _on_file_clicked(self, *_: object) -> None:
        if self._busy:
            return
        self._busy = True
        self._file_btn.set_sensitive(False)
        self._status.set_text("Filing…")
        base_url = normalise_url(str(self._config.get(URL_KEY) or ""))
        company = self._company.get_text().strip()
        actions = self._chosen()
        meeting = self._meeting
        config = self._config

        def _bg() -> None:
            try:
                token = read_token(config, self._store_factory())
                if not token:
                    raise RuntimeError(
                        "No Dispatch token is stored. Add it in Settings → Dispatch."
                    )
                client = self._client_factory(base_url, token)
                filed = file_meeting(client, meeting, actions=actions, company=company)
                GLib.idle_add(self._on_filed, filed_summary(filed), None)
            except Exception as exc:
                logger.warning("Could not file into Dispatch: %s", type(exc).__name__)
                GLib.idle_add(self._on_filed, "", str(exc))

        threading.Thread(target=_bg, daemon=True).start()

    def _on_filed(self, summary: str, error: str | None) -> bool:
        self._busy = False
        if error:
            self._file_btn.set_sensitive(True)
            self._status.set_text(error)
            return False
        if self._on_finished is not None:
            self._on_finished(summary)
        self.close()
        return False
