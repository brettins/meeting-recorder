"""Meeting Explorer — browse, manage, and AI-title recorded meetings.

Rows come from ``ui/meeting_row.py``, the same widget the Record tab uses, so a
meeting being recorded, one being transcribed, one that failed and one that is
finished are four states of one list rather than four list designs. The daemon's
job snapshot is pushed in through ``set_jobs``; the folder scan and the job state
are joined by ``core/row_model.py``.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from meeting_recorder.config import settings
from meeting_recorder.config.defaults import TITLE_PROMPT
from meeting_recorder.config.tags import (
    UNTAGGED,
    Tag,
    color_map,
    known_tags_only,
    matches_filter,
    parse_tags,
    remove_from_assignments,
    rename_in_assignments,
    serialize_tags,
)
from meeting_recorder.config.tags import (
    add_tag as tags_add,
)
from meeting_recorder.utils.meeting_scanner import (
    Meeting,
    delete_meetings,
    rename_meeting_dir,
    scan_meetings,
    set_meeting_tags,
    write_metadata,
)

from ..core import row_model as rm
from ..utils.glib_bridge import idle_call
from .icons import tag_list_icon
from .meeting_row import MeetingRow, RowListView
from .tag_widgets import TagAssignPopover, TagManageDialog

logger = logging.getLogger(__name__)


class MeetingExplorer(Gtk.Box):
    """Scrollable meeting list with AI title generation and multi-select delete."""

    def __init__(self, on_summarize=None, on_job_action=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._on_summarize_callback = on_summarize
        # (action, job_id, error_msg) -> the window forwards it to the daemon.
        self._on_job_action = on_job_action

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.add_css_class("toolbar")
        toolbar.set_margin_top(12)
        toolbar.set_margin_bottom(8)
        toolbar.set_margin_start(16)
        toolbar.set_margin_end(16)

        self._delete_btn = Gtk.Button(label="Delete Selected")
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.set_sensitive(False)
        self._delete_btn.connect("clicked", self._on_delete_clicked)
        toolbar.append(self._delete_btn)

        # Spacer — expands to push the refresh button to the trailing edge.
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        self._filter_ids: list[str | None] = [None]
        self._suppress_filter_signal = False
        self._filter_drop = Gtk.DropDown.new_from_strings(["All meetings"])
        self._filter_drop.set_tooltip_text("Filter meetings by tag")
        self._filter_drop.connect("notify::selected", self._on_filter_changed)
        toolbar.append(self._filter_drop)

        manage_btn = Gtk.Button(icon_name=tag_list_icon())
        manage_btn.add_css_class("flat")
        manage_btn.set_tooltip_text("Manage tags")
        manage_btn.connect("clicked", self._on_manage_tags)
        toolbar.append(manage_btn)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        toolbar.append(refresh_btn)

        self.append(toolbar)

        # Error label (for delete failures etc.)
        self._error_label = Gtk.Label(xalign=0)
        # libadwaita's semantic class, so the colour comes from the theme rather
        # than a literal in this file.
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_margin_start(16)
        self._error_label.set_margin_end(16)
        self._error_label.set_visible(False)
        self.append(self._error_label)

        # Scrollable meeting list — a libadwaita boxed list, centred via a clamp.
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_valign(Gtk.Align.START)

        list_clamp = Adw.Clamp(maximum_size=760)
        list_clamp.set_margin_top(4)
        list_clamp.set_margin_bottom(16)
        list_clamp.set_margin_start(12)
        list_clamp.set_margin_end(12)
        list_clamp.set_child(self._list_box)

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_propagate_natural_height(True)
        self._scroll.set_vexpand(True)
        self._scroll.set_child(list_clamp)
        self.append(self._scroll)

        self._registry: list[Tag] = []
        self._colors: dict[str, str] = {}
        self._all_meetings: list[Meeting] = []
        self._by_key: dict[str, Meeting] = {}
        self._jobs: list = []
        self._recording_dir = ""
        self._job_signature: tuple = ()

        self._list = RowListView(
            self._list_box,
            on_action=self._on_row_action,
            show_check=True,
            on_check_toggled=self._update_delete_sensitivity,
            tag_popover_factory=self._build_tag_popover,
            on_row_created=self._attach_row_gestures,
        )

        self._empty_label = Gtk.Label(label="No meetings found")
        self._empty_label.set_vexpand(True)
        self._empty_label.set_valign(Gtk.Align.CENTER)
        self._empty_label.set_opacity(0.5)
        self._empty_label.set_visible(False)
        self.append(self._empty_label)

    # -- Data ------------------------------------------------------------

    def refresh(self) -> None:
        """Rescan the output folder and rebuild the meeting list."""
        self._error_label.set_visible(False)

        cfg = settings.load()
        output_folder = cfg.get("output_folder", "~/meetings")
        self._registry = parse_tags(cfg.get("tags"))
        self._colors = color_map(self._registry)
        self._rebuild_filter()
        self._all_meetings = scan_meetings(output_folder)
        self._by_key = {str(m.path): m for m in self._all_meetings}
        self._apply_filter()

    def set_jobs(self, jobs: list, recording_dir: str = "") -> None:
        """Push the daemon's job snapshot in so rows can show their live state.

        A job finishing changes what is on disk (a transcript appears, a folder
        may have been auto-titled), so the folder is rescanned when the job set
        changes — but not on the once-a-second timer ticks in between.
        """
        self._jobs = list(jobs)
        signature = tuple(sorted((j.job_id, j.status.value, j.audio_dir) for j in jobs))
        rescan = signature != self._job_signature or recording_dir != self._recording_dir
        self._job_signature = signature
        self._recording_dir = recording_dir
        if rescan and self.get_mapped():
            self.refresh()
        else:
            self._apply_filter()

    def _apply_filter(self) -> None:
        """Re-render rows for the active tag filter.

        Deliberately touches neither disk nor the keyring: settings.load()
        resolves the API key through the Secret Service, whose session handshake
        takes seconds, and the filter changes far too often to pay that.
        """
        active = self._active_filter()
        meetings = [m for m in self._all_meetings if matches_filter(m.tags, active)]
        jobs_by_dir = rm.index_jobs_by_dir(self._jobs)

        models = [
            rm.row_from_meeting(
                m,
                jobs_by_dir.get(str(m.path)),
                is_recording=str(m.path) == self._recording_dir,
            )
            for m in meetings
        ]
        self._list.render(models, self._colors)

        self._empty_label.set_label(
            "No meetings found" if active is None else "No meetings with this tag"
        )
        self._empty_label.set_visible(not models)
        self._update_delete_sensitivity()

    # -- Row plumbing ----------------------------------------------------

    def _attach_row_gestures(self, row: MeetingRow) -> None:
        """Double-click the title to edit it inline (GTK4 has no EventBox)."""
        gesture = Gtk.GestureClick()
        gesture.connect(
            "pressed",
            lambda _g, n_press, _x, _y, r=row: self._start_inline_edit(r) if n_press == 2 else None,
        )
        row.primary_label.add_controller(gesture)

    def _meeting_for(self, model: rm.RowModel) -> Meeting | None:
        return self._by_key.get(model.key)

    def _on_row_action(self, action: str, model: rm.RowModel) -> None:
        if action in (rm.CANCEL, rm.RETRY, rm.DISMISS):
            if self._on_job_action and model.job_id is not None:
                self._on_job_action(action, model.job_id)
            return
        if action == rm.DETAILS:
            self._show_details(model)
            return

        meeting = self._meeting_for(model)
        if meeting is None:
            return
        row = self._list.row(model.key)
        if action == rm.OPEN_FOLDER:
            self._open_folder(meeting)
        elif action == rm.DELETE:
            self._confirm_and_delete([meeting])
        elif action == rm.RENAME and row is not None:
            self._start_inline_edit(row)
        elif action == rm.AI_TITLE and row is not None:
            self._on_ai_title_clicked(row, meeting)
        elif action == rm.SUMMARIZE:
            if row is not None:
                row.set_action_sensitive(rm.SUMMARIZE, False)
            if self._on_summarize_callback:
                self._on_summarize_callback(meeting)

    def _show_details(self, model: rm.RowModel) -> None:
        """Show the entire error message, with a way to copy it."""
        message = model.error_msg or "No error message was recorded for this job."
        alert = Gtk.AlertDialog()
        alert.set_modal(True)
        alert.set_message(f"{model.title} failed")
        alert.set_detail(message)
        alert.set_buttons(["Copy", "Close"])
        alert.set_default_button(1)
        alert.set_cancel_button(1)

        def on_choice(dialog, result):
            try:
                choice = dialog.choose_finish(result)
            except GLib.Error:
                return  # dismissed
            if choice == 0:
                display = Gdk.Display.get_default()
                if display is not None:
                    display.get_clipboard().set(message)

        root = self.get_root()
        alert.choose(root if isinstance(root, Gtk.Window) else None, None, on_choice)

    # -- Tags -----------------------------------------------------------

    def _rebuild_filter(self) -> None:
        """Repopulate the filter dropdown, preserving the active selection."""
        previous = self._active_filter()
        ids: list[str | None] = [None]
        labels = ["All meetings"]
        for tag in self._registry:
            ids.append(tag.name)
            labels.append(tag.name)
        ids.append(UNTAGGED)
        labels.append("Untagged")

        self._suppress_filter_signal = True
        try:
            self._filter_ids = ids
            self._filter_drop.set_model(Gtk.StringList.new(labels))
            self._filter_drop.set_selected(ids.index(previous) if previous in ids else 0)
        finally:
            self._suppress_filter_signal = False

    def _active_filter(self) -> str | None:
        i = self._filter_drop.get_selected()
        if 0 <= i < len(self._filter_ids):
            return self._filter_ids[i]
        return None

    def _on_filter_changed(self, *_) -> None:
        if not self._suppress_filter_signal:
            self._apply_filter()

    def _build_tag_popover(self, model: rm.RowModel) -> TagAssignPopover:
        return TagAssignPopover(
            self._registry,
            list(model.tags),
            lambda names, key=model.key: self._on_tags_assigned(key, names),
            lambda name, key=model.key: self._on_tag_created(key, name),
        )

    def _on_tags_assigned(self, key: str, names: list[str]) -> None:
        meeting = self._by_key.get(key)
        if meeting is None:
            return
        names = known_tags_only(names, self._registry)
        try:
            set_meeting_tags(meeting.path, names)
        except OSError as exc:
            self._show_error(f"Could not save tags: {exc}")
            return
        meeting.tags = list(names)
        self._apply_filter()

    def _on_tag_created(self, key: str, name: str) -> None:
        """Create a tag from the assignment popover and apply it immediately."""
        registry = tags_add(self._registry, name)
        if not self._save_registry(registry):
            return
        meeting = self._by_key.get(key)
        if meeting is not None:
            self._on_tags_assigned(key, [*meeting.tags, name])
        self._rebuild_filter()

    def _on_manage_tags(self, *_) -> None:
        window = self.get_root()
        dialog = TagManageDialog(
            window,
            self._registry,
            self._on_registry_saved,
            on_rename=self._on_tag_renamed,
            on_delete=self._on_tag_deleted,
        )
        dialog.present()

    def _on_registry_saved(self, registry: list[Tag]) -> None:
        if not self._save_registry(registry):
            return
        self._rebuild_filter()
        # The popovers cache the old registry, so rows are rebuilt from scratch.
        self._list.clear()
        self._apply_filter()

    def _save_registry(self, registry: list[Tag]) -> bool:
        """Persist the tag registry. Returns False (and reports) on failure."""
        try:
            settings.update_fields({"tags": serialize_tags(registry)})
        except OSError as exc:
            self._show_error(f"Could not save tags: {exc}")
            return False
        self._registry = registry
        self._colors = color_map(registry)
        return True

    def _on_tag_renamed(self, old: str, new: str) -> None:
        self._sweep_assignments(lambda names: rename_in_assignments(names, old, new))

    def _on_tag_deleted(self, name: str) -> None:
        self._sweep_assignments(lambda names: remove_from_assignments(names, name))

    def _sweep_assignments(self, update: Callable[[list[str]], list[str]]) -> None:
        """Apply a registry change to every meeting that carries the tag.

        Without this a renamed or deleted tag is orphaned: the old name stays in
        meeting.json, matches nothing in the registry, and so stops rendering
        while still being written back on every subsequent save.
        """
        for meeting in self._all_meetings:
            updated = update(list(meeting.tags))
            if updated == meeting.tags:
                continue
            try:
                set_meeting_tags(meeting.path, updated)
            except OSError as exc:
                self._show_error(f"Could not update tags for {meeting.path.name}: {exc}")
                continue
            meeting.tags = updated

    def _show_error(self, message: str) -> None:
        self._error_label.set_text(message)
        self._error_label.set_visible(True)

    def _update_delete_sensitivity(self) -> None:
        self._delete_btn.set_sensitive(any(r.check.get_active() for r in self._list.rows))

    def _open_folder(self, meeting: Meeting) -> None:
        try:
            subprocess.Popen(["xdg-open", str(meeting.path)])
        except Exception:
            pass

    # -- Inline title editing --------------------------------------------------

    def _start_inline_edit(self, row: MeetingRow) -> None:
        """Replace the title label with an editable entry."""
        meeting = self._meeting_for(row.model)
        if meeting is None:
            return
        primary_label = row.primary_label
        title_box = row.title_box

        # If already editing, do nothing
        if not primary_label.get_visible():
            return

        # Save scroll position — grab_focus() on a newly added widget
        # causes the ScrolledWindow to jump to the top before layout is done.
        vadj = self._scroll.get_vadjustment()
        saved_scroll = vadj.get_value()

        # Hide the label and prepend an Entry at the top of the title box.
        primary_label.set_visible(False)

        entry = Gtk.Entry()
        entry.set_text(meeting.title or meeting.time_label)
        entry.set_hexpand(True)
        title_box.prepend(entry)
        entry.grab_focus()
        entry.select_region(0, -1)

        # Restore scroll position after layout settles
        GLib.idle_add(lambda: vadj.set_value(saved_scroll))

        # GTK4 event controllers can't be cleanly disconnected mid-callback
        # (unlike GTK3 signal ids), so a guard flag prevents the focus-leave and
        # activate/Escape paths from both tearing down the entry.
        committing = {"done": False}

        def _commit(*_):
            if committing["done"]:
                return
            committing["done"] = True
            new_title = entry.get_text().strip()
            title_box.remove(entry)
            primary_label.set_visible(True)

            if not new_title or new_title == (meeting.title or meeting.time_label):
                return  # no change

            # Rename in background
            def _bg():
                try:
                    write_metadata(meeting.path, {"title": new_title})
                    new_path = rename_meeting_dir(meeting, new_title)
                    old_key = str(meeting.path)
                    meeting.path = new_path
                    meeting.title = new_title
                    meeting.time_label = new_path.name
                    idle_call(self._after_rename, old_key, meeting)
                except Exception as exc:
                    logger.warning("Inline rename failed: %s", exc)

            threading.Thread(target=_bg, daemon=True).start()

        def _cancel(*_):
            if committing["done"]:
                return
            committing["done"] = True
            title_box.remove(entry)
            primary_label.set_visible(True)

        def _on_key_pressed(controller, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                _cancel()
                return True
            return False

        entry.connect("activate", _commit)
        key_ctl = Gtk.EventControllerKey()
        key_ctl.connect("key-pressed", _on_key_pressed)
        entry.add_controller(key_ctl)
        focus_ctl = Gtk.EventControllerFocus()
        focus_ctl.connect("leave", _commit)
        entry.add_controller(focus_ctl)

    def _after_rename(self, old_key: str, meeting: Meeting) -> None:
        """Re-key the row: its identity is the directory, which just moved."""
        self._by_key.pop(old_key, None)
        self._by_key[str(meeting.path)] = meeting
        self._list.remove(old_key)
        self._apply_filter()

    # -- Delete ----------------------------------------------------------------

    def _on_delete_clicked(self, *_) -> None:
        selected = [
            self._by_key[r.model.key]
            for r in self._list.rows
            if r.check.get_active() and r.model.key in self._by_key
        ]
        if selected:
            self._confirm_and_delete(selected)

    def _confirm_and_delete(self, meetings: list[Meeting]) -> None:
        count = len(meetings)
        # GTK4 has no blocking dialog; Gtk.AlertDialog.choose() is async, so the
        # delete proceeds in the _on_choice callback below.
        alert = Gtk.AlertDialog()
        alert.set_modal(True)
        alert.set_message(f"Delete {count} meeting{'s' if count != 1 else ''}?")
        alert.set_detail("This cannot be undone.")
        alert.set_buttons(["Cancel", "Delete"])
        alert.set_cancel_button(0)
        alert.set_default_button(0)

        def _on_choice(dlg, result):
            try:
                idx = dlg.choose_finish(result)
            except GLib.Error:
                return  # dismissed
            if idx != 1:
                return  # Cancel

            def _bg():
                cfg = settings.load()
                output_folder = cfg.get("output_folder", "~/meetings")
                succeeded, failures = delete_meetings(meetings, output_folder)
                idle_call(_done, succeeded, failures)

            def _done(succeeded, failures):
                for meeting in succeeded:
                    key = str(meeting.path)
                    self._by_key.pop(key, None)
                    if meeting in self._all_meetings:
                        self._all_meetings.remove(meeting)
                    self._list.remove(key)
                if failures:
                    msgs = [f"{m.time_label}: {err}" for m, err in failures]
                    self._show_error(f"Failed to delete: {'; '.join(msgs)}")
                self._apply_filter()

            threading.Thread(target=_bg, daemon=True).start()

        alert.choose(self.get_root(), None, _on_choice)

    # -- AI Title Generation ---------------------------------------------------

    def _on_ai_title_clicked(self, row: MeetingRow, meeting: Meeting) -> None:
        row.show_busy_action(True)

        def _bg():
            try:
                notes_path = meeting.path / "notes.md"
                if not notes_path.exists():
                    raise RuntimeError("notes.md not found")

                notes_text = notes_path.read_text(encoding="utf-8")
                cfg = settings.load()

                service = cfg.get("summarization_service", "gemini")
                if service == "gemini" and not cfg.get("gemini_api_key"):
                    raise RuntimeError("Gemini API key is not configured. Please open Settings.")

                # Construct provider directly with title prompt
                provider = self._build_title_provider(cfg)
                title = provider.summarize(notes_text)

                # Clean up the title
                title = title.strip().strip('"').strip("'").strip()
                if not title:
                    raise RuntimeError("LLM returned empty title")

                # Write metadata BEFORE rename (path must still be valid)
                write_metadata(
                    meeting.path,
                    {"title": title, "generated_at": datetime.now().isoformat()},
                )

                old_key = str(meeting.path)
                new_path = rename_meeting_dir(meeting, title)
                meeting.path = new_path
                meeting.title = title
                meeting.time_label = new_path.name

                idle_call(_done, old_key, None)

            except Exception as exc:
                idle_call(_done, str(meeting.path), str(exc))

        def _done(old_key, error):
            row.show_busy_action(False)
            if error:
                self._show_error(f"Could not generate a title: {error}")
            else:
                self._after_rename(old_key, meeting)

        threading.Thread(target=_bg, daemon=True).start()

    @staticmethod
    def _build_title_provider(config: dict):
        """Construct a summarization provider with the title-generation prompt."""
        from meeting_recorder.processing.summarization import create_summarization_provider

        return create_summarization_provider(
            {
                **config,
                "summarization_prompt": config.get("title_prompt") or TITLE_PROMPT,
            }
        )
