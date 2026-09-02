"""
The recorder window — a thin client of the daemon Engine (daemon/UI split).

The window no longer owns the recording lifecycle, job queue, or pipeline: those
live in the GTK-free daemon. This window renders a Snapshot fetched over D-Bus
(``EngineProxy``) and kept fresh by SnapshotChanged signals, and forwards button
clicks back to the engine. Errors and the "recording saved" output arrive as
Error/Output signals. File/meeting selection (which needs GTK dialogs) happens
here, then the resolved paths are handed to the engine to process.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from meeting_recorder.config import settings
from meeting_recorder.config.tags import (
    add_tag,
    color_map,
    known_tags_only,
    parse_tags,
    serialize_tags,
)
from meeting_recorder.utils.filename import output_paths
from meeting_recorder.utils.meeting_scanner import Meeting, find_audio_file

from ..core import row_model as rm
from ..core.controls import (
    CANCEL,
    CANCEL_COUNTDOWN,
    CANCEL_SAVE,
    PAUSE,
    RECORD_HEADPHONES,
    RECORD_SPEAKER,
    RESUME,
    STOP,
    USE_EXISTING,
    controls_for_state,
    title_editable,
)
from ..core.errors import error_presentation
from ..core.window_close import CLOSE_HIDE, resolve_close_action
from ..core.wire import Snapshot, snapshot_from_json
from ..utils.gtk_compat import remove_all_children
from ..utils.recording_import import resolve_existing_recording_target
from .icons import tag_icon
from .meeting_explorer import MeetingExplorer
from .meeting_row import RowListView, section_group
from .tag_widgets import TagAssignPopover, make_tag_chip

logger = logging.getLogger(__name__)

__all__ = ["MainWindow"]


def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _icon_label_button(icon_name: str, label: str) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_child(Adw.ButtonContent(icon_name=icon_name, label=label))
    return btn


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, engine, **kwargs) -> None:
        super().__init__(title="Meeting Recorder", **kwargs)
        self.set_default_size(1100, 760)
        self.set_resizable(True)

        # The daemon-side engine, reached over D-Bus. This window only renders
        # its snapshots and forwards commands.
        self._engine = engine
        self._recording_mode: str = "headphones"
        self._snapshot = Snapshot()
        # Guards the title entry against re-emitting "changed" while a snapshot
        # is being rendered into it.
        self._syncing = False

        self._build_ui()
        # Paint the current daemon state immediately, then live-update on signals.
        self.apply_snapshot_json(self._engine.get_snapshot())
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(toolbar_view)

        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)
        header.pack_end(self._build_gear_menu_button())
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self._stack)

        # View 1: Recorder
        recorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        recorder_box.set_margin_top(24)
        recorder_box.set_margin_bottom(24)
        recorder_box.set_margin_start(12)
        recorder_box.set_margin_end(12)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self._timer_label = Gtk.Label(label="00:00")
        self._timer_label.add_css_class("timer-label")
        self._timer_label.set_attributes(self._make_timer_attrs())
        vbox.append(self._timer_label)

        self._status_label = Gtk.Label(label="")
        self._status_label.set_wrap(True)
        self._status_label.set_xalign(0.5)
        self._status_label.add_css_class("dim-label")
        vbox.append(self._status_label)

        title_group = Adw.PreferencesGroup()
        self._title_row = Adw.EntryRow(title="Title (optional)")
        # Editable while recording too: the title is written to meeting.json at
        # once and the folder rename is queued until ffmpeg releases the
        # directory (core/recording_rename.py).
        self._title_row.connect("changed", self._on_title_changed)

        self._tags: list[str] = []
        self._tag_registry: list = []
        self._tag_button = Gtk.MenuButton(icon_name=tag_icon())
        self._tag_button.add_css_class("flat")
        self._tag_button.set_valign(Gtk.Align.CENTER)
        self._tag_button.set_tooltip_text("Tag this recording")
        self._title_row.add_suffix(self._tag_button)
        self._rebuild_tag_popover()

        title_group.add(self._title_row)
        self._title_entry = self._title_row

        self._tag_chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._tag_chips.set_halign(Gtk.Align.CENTER)
        self._tag_chips.set_margin_top(6)
        self._tag_chips.set_visible(False)

        vbox.append(title_group)
        vbox.append(self._tag_chips)

        self._button_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._button_box.set_halign(Gtk.Align.CENTER)
        self._build_controls()
        vbox.append(self._button_box)

        self._output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._output_box.set_visible(False)
        self._output_label = Gtk.Label(label="")
        self._output_label.set_wrap(True)
        self._output_label.set_xalign(0)
        self._output_label.add_css_class("dim-label")
        self._open_folder_btn = Gtk.Button(label="Open Output Folder")
        self._open_folder_btn.set_halign(Gtk.Align.CENTER)
        self._open_folder_btn.connect("clicked", self._on_open_folder)
        self._output_box.append(self._output_label)
        self._output_box.append(self._open_folder_btn)
        vbox.append(self._output_box)

        # The same row widget the Library uses, filtered to what is in flight —
        # a recording being processed is a state of a meeting, not a second kind
        # of list (see ui/meeting_row.py).
        self._progress_clamp, progress_list = section_group("In progress")
        self._progress_clamp.set_visible(False)
        self._progress = RowListView(progress_list, on_action=self._on_progress_action)
        vbox.append(self._progress_clamp)
        recorder_box.append(vbox)

        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_child(recorder_box)
        recorder_scroll = Gtk.ScrolledWindow()
        recorder_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        recorder_scroll.set_child(clamp)

        self._stack.add_titled_with_icon(
            recorder_scroll, "recorder", "Record", "media-record-symbolic"
        )

        # View 2: Meeting Explorer
        self._explorer = MeetingExplorer(
            on_summarize=self._on_summarize_from_explorer,
            on_job_action=self._forward_job_action,
        )
        self._stack.add_titled_with_icon(
            self._explorer, "explorer", "Library", "view-list-symbolic"
        )
        self._stack.connect("notify::visible-child-name", self._on_stack_switched)

    def _on_stack_switched(self, stack, param):
        if stack.get_visible_child_name() == "explorer":
            self._explorer.refresh()

    def _make_timer_attrs(self):
        gi.require_version("Pango", "1.0")
        from gi.repository import Pango

        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new_absolute(48 * Pango.SCALE))
        return attrs

    # ------------------------------------------------------------------
    # Snapshot rendering (state lives in the daemon)
    # ------------------------------------------------------------------

    def apply_snapshot_json(self, payload: str) -> None:
        """Signal handler: parse a daemon snapshot and render it."""
        self._apply_snapshot(snapshot_from_json(payload))

    def _apply_snapshot(self, snap: Snapshot) -> None:
        self._snapshot = snap
        self._update_ui()
        self._render_progress()
        self._explorer.set_jobs(snap.jobs, snap.recording_dir)

    def _update_ui(self) -> None:
        snap = self._snapshot
        state = snap.state

        self._timer_label.set_text(_format_time(snap.elapsed) if snap.elapsed else "00:00")
        self._status_label.set_text(snap.status or ("Ready to record" if state == "idle" else ""))
        if state in ("recording", "countdown"):
            self._output_box.set_visible(False)

        # Controls are built once and only shown/hidden. Rebuilding them here
        # destroyed whichever button was mid-click on the next timer tick, which
        # is what made Stop unreliable — see core/controls.py.
        visible = set(controls_for_state(state))
        for name, widget in self._controls.items():
            widget.set_visible(name in visible)
        self._idle_vbox.set_visible(state == "idle")
        self._active_row.set_visible(state != "idle")

        editable = title_editable(state)
        self._title_entry.set_sensitive(editable)
        self._tag_button.set_sensitive(editable)
        self._sync_title_and_tags(snap)

    def _render_progress(self) -> None:
        """The Record tab's "In progress" section: the in-flight rows only."""
        models = [
            rm.row_from_job(j)
            for j in self._snapshot.jobs
            if rm.state_for_job(j.status) != rm.READY
        ]
        self._progress.render(models, self._tag_colors())
        self._progress_clamp.set_visible(bool(models))

    def _on_progress_action(self, action: str, model: rm.RowModel) -> None:
        if model.job_id is None:
            return
        if action == rm.DETAILS:
            self.show_error(model.error_msg or "No error message was recorded for this job.")
            return
        self._forward_job_action(action, model.job_id)

    def _forward_job_action(self, action: str, job_id: int) -> None:
        if action == rm.CANCEL:
            self._engine.cancel_job(job_id)
        elif action == rm.RETRY:
            self._engine.retry_job(job_id)
        elif action == rm.DISMISS:
            self._engine.dismiss_job(job_id)

    # ------------------------------------------------------------------
    # Controls (built once; _update_ui only toggles visibility)
    # ------------------------------------------------------------------

    def _build_controls(self) -> None:
        self._controls: dict[str, Gtk.Widget] = {}

        self._idle_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        record_row.set_homogeneous(True)

        headphones_btn = _icon_label_button("media-record-symbolic", "Record (Headphones)")
        headphones_btn.set_tooltip_text("Record mic + system audio. Use when wearing headphones.")
        headphones_btn.connect("clicked", lambda *_: self.on_record_headphones_clicked())
        headphones_btn.add_css_class("suggested-action")
        headphones_btn.add_css_class("pill")
        headphones_btn.set_hexpand(True)
        record_row.append(headphones_btn)
        self._controls[RECORD_HEADPHONES] = headphones_btn

        speaker_btn = _icon_label_button("audio-input-microphone-symbolic", "Record (Speaker)")
        speaker_btn.set_tooltip_text("Record mic only. Use when on speaker to avoid echo.")
        speaker_btn.connect("clicked", lambda *_: self.on_record_speaker_clicked())
        speaker_btn.add_css_class("pill")
        speaker_btn.set_hexpand(True)
        record_row.append(speaker_btn)
        self._controls[RECORD_SPEAKER] = speaker_btn

        self._idle_vbox.append(record_row)

        existing_btn = _icon_label_button("document-open-symbolic", "Use Existing Recording")
        existing_btn.connect("clicked", lambda *_: self.on_use_existing_clicked())
        existing_btn.add_css_class("pill")
        existing_btn.set_halign(Gtk.Align.CENTER)
        self._idle_vbox.append(existing_btn)
        self._controls[USE_EXISTING] = existing_btn

        self._button_box.append(self._idle_vbox)

        self._active_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._active_row.set_halign(Gtk.Align.CENTER)
        self._button_box.append(self._active_row)

        pause_btn = _icon_label_button("media-playback-pause-symbolic", "Pause")
        pause_btn.connect("clicked", lambda *_: self.on_pause_clicked())
        pause_btn.add_css_class("pill")
        self._active_row.append(pause_btn)
        self._controls[PAUSE] = pause_btn

        resume_btn = _icon_label_button("media-playback-start-symbolic", "Resume")
        resume_btn.connect("clicked", lambda *_: self.on_resume_clicked())
        resume_btn.add_css_class("suggested-action")
        resume_btn.add_css_class("pill")
        self._active_row.append(resume_btn)
        self._controls[RESUME] = resume_btn

        stop_btn = _icon_label_button("media-playback-stop-symbolic", "Stop")
        stop_btn.connect("clicked", lambda *_: self.on_stop_clicked())
        stop_btn.add_css_class("destructive-action")
        stop_btn.add_css_class("pill")
        self._active_row.append(stop_btn)
        self._controls[STOP] = stop_btn

        save_btn = Gtk.Button(label="Cancel (save recording)")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", lambda *_: self.on_cancel_save_clicked())
        self._active_row.append(save_btn)
        self._controls[CANCEL_SAVE] = save_btn

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("pill")
        cancel_btn.connect("clicked", lambda *_: self.on_cancel_clicked())
        self._active_row.append(cancel_btn)
        self._controls[CANCEL] = cancel_btn

        countdown_btn = Gtk.Button(label="Cancel")
        countdown_btn.add_css_class("destructive-action")
        countdown_btn.add_css_class("pill")
        countdown_btn.connect("clicked", lambda *_: self.on_cancel_countdown_clicked())
        self._active_row.append(countdown_btn)
        self._controls[CANCEL_COUNTDOWN] = countdown_btn

    def show_output(self, text: str) -> None:
        """Engine Output signal: recording saved without transcription."""
        self._output_label.set_text(text)
        self._output_box.set_visible(True)

    # ------------------------------------------------------------------
    # Button handlers -> engine
    # ------------------------------------------------------------------

    def on_record_headphones_clicked(self) -> None:
        self._recording_mode = "headphones"
        self._start_recording()

    def on_record_speaker_clicked(self) -> None:
        self._recording_mode = "speaker"
        self._start_recording()

    def _start_recording(self) -> None:
        # Title and tags already live in the daemon (set as they were edited);
        # it applies whatever it holds when the recording starts.
        self._engine.start_recording(self._recording_mode)

    # -- Record-tab title & tags ----------------------------------------
    #
    # The daemon owns both, so the tray, a second window and a window reopened
    # mid-recording all agree on what the meeting is called. This side only
    # sends edits up and renders what comes back.

    def _on_title_changed(self, *_) -> None:
        if self._syncing:
            return
        self._engine.set_title(self._title_entry.get_text().strip())

    def _sync_title_and_tags(self, snap: Snapshot) -> None:
        """Render the daemon's title/tags without fighting the user's typing."""
        self._syncing = True
        try:
            if not self._title_entry.has_focus() and self._title_entry.get_text() != snap.title:
                self._title_entry.set_text(snap.title)
        finally:
            self._syncing = False

        if snap.tags != self._tags:
            self._tags = list(snap.tags)
            self._render_tag_chips()
            # Never while the popover is open: replacing it under the pointer
            # dismisses it mid-click.
            popover = self._tag_button.get_popover()
            if popover is None or not popover.get_visible():
                self._rebuild_tag_popover()

    def _render_tag_chips(self) -> None:
        remove_all_children(self._tag_chips)
        colors = self._tag_colors()
        for name in self._tags:
            if name in colors:
                self._tag_chips.append(make_tag_chip(name, colors[name]))
        self._tag_chips.set_visible(bool(self._tags))

    def _tag_colors(self) -> dict[str, str]:
        return color_map(self._tag_registry)

    def _rebuild_tag_popover(self) -> None:
        """Rebuild the popover so it reflects the current registry and selection.

        A Gtk.MenuButton with no popover renders dimmed and does nothing, so one
        is always attached rather than created on first click.
        """
        registry = parse_tags(settings.load().get("tags"))
        self._tag_registry = registry
        self._tag_button.set_popover(
            TagAssignPopover(
                registry,
                list(self._tags),
                self._on_tags_changed,
                self._on_tag_created,
            )
        )

    def _on_tags_changed(self, names: list[str]) -> None:
        self._tags = known_tags_only(names, self._tag_registry)
        self._engine.set_tags(self._tags)

    def _on_tag_created(self, name: str) -> None:
        registry = add_tag(self._tag_registry, name)
        try:
            settings.update_fields({"tags": serialize_tags(registry)})
        except OSError as exc:
            logger.warning("Could not save the new tag: %s", exc)
            return
        self._tag_registry = registry
        self._on_tags_changed([*self._tags, name])
        self._render_tag_chips()
        self._rebuild_tag_popover()

    def on_pause_clicked(self) -> None:
        self._engine.pause()

    def on_resume_clicked(self) -> None:
        self._engine.resume()

    def on_stop_clicked(self) -> None:
        self._engine.stop()

    def on_cancel_countdown_clicked(self) -> None:
        self._engine.cancel_countdown()

    def on_cancel_save_clicked(self) -> None:
        self._engine.cancel_save()

    def on_cancel_clicked(self) -> None:
        self._engine.cancel()

    # ------------------------------------------------------------------
    # Use Existing / Summarize (GTK selection here, processing in the daemon)
    # ------------------------------------------------------------------

    def on_use_existing_clicked(self) -> None:
        cfg = settings.load()
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Audio Recording")
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("Audio files")
        for pat in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac", "*.webm"):
            audio_filter.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(audio_filter)
        dialog.open(self, None, lambda dlg, res: self._on_existing_chosen(dlg, res, cfg))

    def _on_existing_chosen(self, dialog, result, cfg) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        if not gfile:
            return
        filename = gfile.get_path()
        if not filename:
            return

        output_folder = Path(os.path.expanduser(cfg.get("output_folder", "~/meetings")))
        reuse_in_place, paths = resolve_existing_recording_target(Path(filename), output_folder)
        if reuse_in_place:
            audio_path, transcript_path, notes_path = paths
        else:
            audio_path, transcript_path, notes_path = output_paths(
                cfg.get("output_folder", "~/meetings")
            )
            try:
                shutil.copy(filename, audio_path)
            except Exception as e:
                self.show_error(f"Failed to copy audio file: {e}")
                return

        self._engine.import_existing(
            str(audio_path), str(transcript_path), str(notes_path), Path(filename).name
        )

    def _on_summarize_from_explorer(self, meeting: Meeting) -> None:
        audio_path = find_audio_file(meeting.path)
        if not audio_path:
            self.show_error("No audio file found in meeting folder.")
            return
        transcript_path = meeting.path / "transcript.md"
        notes_path = meeting.path / "notes.md"
        err = self._engine.summarize_meeting(
            str(audio_path), str(transcript_path), str(notes_path), meeting.time_label
        )
        if err:
            self.show_error(err)
            return
        self._stack.set_visible_child_name("recorder")

    # ------------------------------------------------------------------
    # Error display (Engine Error signal)
    # ------------------------------------------------------------------

    def show_error(self, msg: str) -> None:
        logger.error("UI error shown: %s", msg)
        if error_presentation(msg) == "dialog":
            alert = Gtk.AlertDialog()
            alert.set_modal(True)
            alert.set_message("Meeting Recorder")
            alert.set_detail(msg)
            alert.set_buttons(["OK"])
            alert.show(self)
        else:
            toast = Adw.Toast(title=msg)
            toast.set_timeout(0)
            self._toast_overlay.add_toast(toast)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _build_gear_menu_button(self) -> Gtk.MenuButton:
        """The header-bar gear: a menu with Preferences (settings) and About."""
        menu = Gio.Menu()
        menu.append("Preferences", "gear.preferences")
        menu.append("About Meeting Recorder", "gear.about")

        actions = Gio.SimpleActionGroup()
        preferences = Gio.SimpleAction.new("preferences", None)
        preferences.connect("activate", self._on_settings_clicked)
        actions.add_action(preferences)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about_clicked)
        actions.add_action(about)
        self.insert_action_group("gear", actions)

        button = Gtk.MenuButton(icon_name="preferences-system-symbolic")
        button.set_tooltip_text("Menu")
        button.set_menu_model(menu)
        return button

    def _on_settings_clicked(self, *_) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            parent=self, on_saved=self._after_settings_saved, engine=self._engine
        )
        dialog.present()

    def _on_about_clicked(self, *_) -> None:
        from ..core import app_info

        version = app_info.resolve_version()
        if hasattr(Adw, "AboutDialog"):
            about = Adw.AboutDialog(
                application_name=app_info.APP_NAME,
                application_icon="meeting-recorder",
                developer_name=app_info.DEVELOPER_NAME,
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                issue_url=app_info.ISSUE_URL,
                developers=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present(self)
        elif hasattr(Adw, "AboutWindow"):
            about = Adw.AboutWindow(
                transient_for=self,
                application_name=app_info.APP_NAME,
                application_icon="meeting-recorder",
                developer_name=app_info.DEVELOPER_NAME,
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                issue_url=app_info.ISSUE_URL,
                developers=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present()
        else:
            about = Gtk.AboutDialog(
                transient_for=self,
                modal=True,
                program_name=app_info.APP_NAME,
                logo_icon_name="meeting-recorder",
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                authors=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present()

    def _after_settings_saved(self) -> None:
        # The daemon owns call detection; ask it to reconcile with the new config.
        self._engine.reload_config()

    # ------------------------------------------------------------------
    # Helpers / window lifecycle
    # ------------------------------------------------------------------

    def _on_open_folder(self, *_) -> None:
        folder = self._engine.output_folder() or os.path.expanduser(
            settings.load().get("output_folder", "~/meetings")
        )
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def present_window(self) -> None:
        """Show, raise and focus the window — the Engine PresentWindow signal
        and the tray (left-click / the "Open" menu item) route here.

        GTK4 removed set_skip_taskbar_hint(), present_with_time() and
        Gtk.get_current_event_time(); focus is now mediated by the compositor,
        so present() is the supported path (left-click-to-focus is best-effort
        on Wayland/GNOME)."""
        self.set_visible(True)
        self.unminimize()
        self.present()

    def open_use_existing(self) -> None:
        """Engine OpenUseExisting signal: present the window and pop the picker."""
        self.present_window()
        self.on_use_existing_clicked()

    def _on_close_request(self, *_) -> bool:
        # By default closing exits this process; the daemon keeps running
        # (recording/jobs continue) and will respawn a window on demand. When the
        # user opts into "keep window in memory", hide instead of exit so the
        # process stays resident and the next Open is an instant present.
        if resolve_close_action(settings.load()) == CLOSE_HIDE:
            self.set_visible(False)
            return True  # veto the destroy; the window lives on, hidden
        return False
