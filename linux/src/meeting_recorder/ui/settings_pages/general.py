"""Settings → General tab: startup, call detection, and recording options."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from meeting_recorder.config.defaults import RECORDING_QUALITIES

from ...core.wire import snapshot_from_json
from ...services.self_update import (
    checkout_revision,
    decide_status,
    read_install_source,
    run_update,
)
from ...utils.autostart import is_autostart_enabled
from ...utils.glib_bridge import idle_call
from .widgets import IdComboRow, make_scroll_page

logger = logging.getLogger(__name__)


class GeneralPage:
    """Builds the General tab and writes its values back on save via ``apply()``.

    ``parent_window`` is the settings window itself — the async
    ``Gtk.FileDialog`` folder picker needs a transient parent.
    """

    def __init__(self, cfg: dict, parent_window: Gtk.Window, engine=None) -> None:
        self._cfg = cfg
        self._parent = parent_window
        # The daemon proxy, used only to restart after an update.
        self._engine = engine
        self._source = None
        self._updated = False
        self.widget = self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        scroll, box = make_scroll_page()

        general = Adw.PreferencesGroup(title="General")

        self._startup_switch = Adw.SwitchRow(title="Start at system startup")
        self._startup_switch.set_active(is_autostart_enabled())
        general.add(self._startup_switch)

        self._detection_switch = Adw.SwitchRow(
            title="Enable call detection",
            subtitle=(
                "Monitor running processes and audio streams to detect active "
                "calls and notify you to start recording. May produce false "
                "positives for other apps that use the microphone."
            ),
        )
        self._detection_switch.set_active(self._cfg.get("call_detection_enabled", True))
        general.add(self._detection_switch)
        self._detection_switch.connect("notify::active", lambda *_: self._sync_startup_switch())
        self._sync_startup_switch()

        self._low_memory_switch = Adw.SwitchRow(
            title="Low memory mode",
            subtitle=(
                "Unload the window from memory when you close it, saving RAM "
                "while idle in the tray (~20 MB vs. ~100 MB) at the cost of a "
                "brief delay when reopening. Enable on low-memory systems."
            ),
        )
        self._low_memory_switch.set_active(self._cfg.get("low_memory_mode", False))
        general.add(self._low_memory_switch)
        box.append(general)

        recording = Adw.PreferencesGroup(title="Recording")

        self._auto_title_switch = Adw.SwitchRow(
            title="Auto-title recordings",
            subtitle="Automatically generate a short title based on meeting notes.",
        )
        self._auto_title_switch.set_active(self._cfg.get("auto_title", True))
        recording.add(self._auto_title_switch)

        self._countdown_switch = Adw.SwitchRow(
            title="Processing countdown",
            subtitle=(
                "Show a 5-second countdown after stopping a recording. Cancel "
                "during it to skip transcription and save the audio only."
            ),
        )
        self._countdown_switch.set_active(self._cfg.get("processing_countdown_enabled", False))
        recording.add(self._countdown_switch)

        q_ids = list(RECORDING_QUALITIES.keys())
        q_labels = [label for label, _ in RECORDING_QUALITIES.values()]
        self._quality_combo = IdComboRow(
            "Recording quality",
            q_ids,
            q_labels,
            self._cfg.get("recording_quality", "high"),
        )
        recording.add(self._quality_combo)

        self._folder_entry = Adw.EntryRow(title="Output folder")
        self._folder_entry.set_text(self._cfg.get("output_folder", "~/meetings"))
        browse_btn = Gtk.Button(icon_name="folder-open-symbolic")
        browse_btn.add_css_class("flat")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text("Browse…")
        browse_btn.connect("clicked", self._on_browse_folder)
        self._folder_entry.add_suffix(browse_btn)
        recording.add(self._folder_entry)
        box.append(recording)

        box.append(self._build_limits())
        box.append(self._build_updates())

        return scroll

    # ------------------------------------------------------------------
    # Limits
    # ------------------------------------------------------------------

    def _build_limits(self) -> Adw.PreferencesGroup:
        """Two guards against a recording nobody stops.

        The cap is separate from the silence ladder because they fail
        differently: the cap cannot be fooled but will cut a genuinely long
        meeting, while silence is kinder but can be held open by music playing
        through the speakers. Both are adjustable, and 0 turns either off.
        """
        group = Adw.PreferencesGroup(
            title="Recording limits",
            description=(
                "A recording nobody stops runs until the disk does. The length cap "
                "always stops; silence nudges and warns first. Set either to 0 to "
                "turn it off."
            ),
        )

        self._max_minutes = Adw.SpinRow.new_with_range(0, 24 * 60, 15)
        self._max_minutes.set_title("Maximum recording length")
        self._max_minutes.set_subtitle("Minutes. Stops the recording outright.")
        self._max_minutes.set_value(self._cfg.get("max_recording_minutes", 180))
        group.add(self._max_minutes)

        self._silence_switch = Adw.SwitchRow(
            title="Stop when nothing is heard",
            subtitle=(
                "Watches the recorded audio — microphone and system sound together — "
                "so a video or music playing keeps the recording alive."
            ),
        )
        self._silence_switch.set_active(self._cfg.get("silence_auto_stop_enabled", True))
        group.add(self._silence_switch)

        self._silence_grace = Adw.SpinRow.new_with_range(0, 8 * 60, 15)
        self._silence_grace.set_title("Ignore silence for the first")
        self._silence_grace.set_subtitle("Minutes. Nothing is said or stopped before this.")
        self._silence_grace.set_value(self._cfg.get("silence_grace_minutes", 60))
        group.add(self._silence_grace)

        self._silence_nudge = Adw.SpinRow.new_with_range(1, 120, 5)
        self._silence_nudge.set_title("Notify after silence of")
        self._silence_nudge.set_subtitle("Minutes. A reminder that the recorder is running.")
        self._silence_nudge.set_value(self._cfg.get("silence_nudge_minutes", 10))
        group.add(self._silence_nudge)

        self._trim_switch = Adw.SwitchRow(
            title="Remove silence before transcribing",
            subtitle=(
                "Sends a trimmed copy so long gaps do not cost upload time or model "
                "budget. The recording in your library is never modified."
            ),
        )
        self._trim_switch.set_active(self._cfg.get("trim_silence_before_upload", True))
        group.add(self._trim_switch)

        self._silence_stop = Adw.SpinRow.new_with_range(0, 240, 5)
        self._silence_stop.set_title("Stop after silence of")
        self._silence_stop.set_subtitle("Minutes. A warning arrives five minutes before.")
        self._silence_stop.set_value(self._cfg.get("silence_stop_minutes", 30))
        group.add(self._silence_stop)

        return group

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def _build_updates(self) -> Adw.PreferencesGroup:
        """The update row for a user-local install.

        A packaged install shows why it cannot update itself rather than
        hiding the row: the answer ("your package manager owns this copy") is
        the useful part.
        """
        group = Adw.PreferencesGroup(title="Updates")
        self._update_row = Adw.ActionRow(title="Version")
        self._update_btn = Gtk.Button(label="Update")
        self._update_btn.set_valign(Gtk.Align.CENTER)
        self._update_btn.connect("clicked", self._on_update_clicked)
        self._update_row.add_suffix(self._update_btn)
        group.add(self._update_row)
        self._refresh_update_status()
        return group

    def _refresh_update_status(self) -> None:
        self._source = read_install_source()
        available = checkout_revision(self._source.repo) if self._source else None
        status = decide_status(self._source, available)
        self._update_row.set_title(f"Version {status.installed}")
        self._update_row.set_subtitle(status.message)
        self._update_btn.set_sensitive(status.can_update)
        self._update_btn.set_label("Update")

    def _on_update_clicked(self, *_) -> None:
        if self._updated:
            self._restart()
            return
        source = self._source
        if source is None:
            return
        self._update_btn.set_sensitive(False)
        self._update_row.set_subtitle("Updating…")

        def _bg() -> None:
            ok, message = run_update(source)
            idle_call(_done, ok, message)

        def _done(ok: bool, message: str) -> None:
            self._update_row.set_subtitle(message)
            if ok:
                self._updated = True
                self._update_btn.set_label("Restart now")
                self._update_btn.set_sensitive(True)
            else:
                self._update_btn.set_sensitive(True)

        threading.Thread(target=_bg, daemon=True).start()

    def _restart(self) -> None:
        """Relaunch the app, then ask the daemon to quit.

        Restarting stops the daemon, so a recording in progress would be lost;
        the snapshot is checked first and the restart refused while one is
        running. The replacement is spawned detached and waits a moment so the
        old daemon has released its D-Bus name before the new one claims it.
        """
        if self._engine is None:
            self._update_row.set_subtitle("Quit and reopen the app to finish updating.")
            return
        try:
            snapshot = snapshot_from_json(self._engine.get_snapshot())
            if snapshot.state.lower() not in ("idle", ""):
                self._update_row.set_subtitle(
                    "A recording is in progress. Finish it, then restart to update."
                )
                return
        except Exception as exc:
            logger.warning("Could not read the daemon state before restarting: %s", exc)

        launcher = shutil.which("meeting-recorder") or os.path.expanduser(
            "~/.local/bin/meeting-recorder"
        )
        try:
            subprocess.Popen(
                ["sh", "-c", f'sleep 2; exec "{launcher}"'],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._update_row.set_subtitle(f"Could not relaunch: {exc}")
            return
        self._engine.quit()

    # ------------------------------------------------------------------
    # Folder picker
    # ------------------------------------------------------------------

    def _on_browse_folder(self, *_) -> None:
        # GTK4 has no blocking FileChooserDialog.run(); Gtk.FileDialog.select_folder()
        # is async — the entry is updated in the _done callback.
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Output Folder")
        current = os.path.expanduser(self._folder_entry.get_text())
        if os.path.isdir(current):
            dialog.set_initial_folder(Gio.File.new_for_path(current))

        def _done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled or dismissed
            if folder:
                self._folder_entry.set_text(folder.get_path())

        dialog.select_folder(self._parent, None, _done)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _sync_startup_switch(self) -> None:
        """Call detection needs the tray running at login, so it holds the startup switch on."""
        needed = self._detection_switch.get_active()
        if needed:
            self._startup_switch.set_active(True)
        self._startup_switch.set_sensitive(not needed)
        self._startup_switch.set_subtitle("On while call detection is on." if needed else "")

    def apply(self, cfg: dict) -> None:
        """Write this tab's values into ``cfg`` (called by the dialog's save flow)."""
        cfg["output_folder"] = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["call_detection_enabled"] = self._detection_switch.get_active()
        cfg["low_memory_mode"] = self._low_memory_switch.get_active()
        cfg["start_at_startup"] = self._startup_switch.get_active()
        cfg["auto_title"] = self._auto_title_switch.get_active()
        cfg["processing_countdown_enabled"] = self._countdown_switch.get_active()
        cfg["max_recording_minutes"] = int(self._max_minutes.get_value())
        cfg["silence_auto_stop_enabled"] = self._silence_switch.get_active()
        cfg["silence_grace_minutes"] = int(self._silence_grace.get_value())
        cfg["silence_nudge_minutes"] = int(self._silence_nudge.get_value())
        cfg["silence_stop_minutes"] = int(self._silence_stop.get_value())
        cfg["trim_silence_before_upload"] = self._trim_switch.get_active()
