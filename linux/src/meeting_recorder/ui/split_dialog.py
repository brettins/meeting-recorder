"""
The "Split recording" window: show what would happen, then do it or don't.

Splitting rewrites how a recording appears in the library, and the decision
rests on a guess about where one conversation ended and another began. So
nothing happens until the plan has been shown in the terms the user will see
afterwards — how many meetings, how long each one is, how much audio would
actually be sent — and they have said yes.

Leaving it alone is a first-class outcome, not a cancel: "Keep as one meeting"
sends the recording for processing exactly as it is.

The decisions live in ``processing/silence_map.py`` and
``services/recording_splitter.py``; this module is widgets and threads.
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

from ..core.auto_stop import format_duration
from ..processing.silence_map import parts_from_seams
from ..services.recording_splitter import Analysis, analyse, split_recording
from ..utils.glib_bridge import idle_call

logger = logging.getLogger(__name__)


class SplitDialog(Adw.Window):
    """Analyse one recording for long gaps and offer to split it."""

    def __init__(
        self,
        parent: Gtk.Window,
        meeting,
        *,
        output_folder: Path,
        on_split: Callable[[list[Path]], None] | None = None,
        on_keep: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(title="Split recording", transient_for=parent, modal=True)
        self.set_default_size(520, 460)

        self._meeting = meeting
        self._output_folder = output_folder
        self._on_split = on_split
        self._on_keep = on_keep
        self._analysis: Analysis | None = None
        self._seam_switches: list[Adw.SwitchRow] = []
        self._busy = False

        self._build()
        self._analyse()

    # ------------------------------------------------------------------

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)

        self._split_btn = Gtk.Button(label="Split")
        self._split_btn.add_css_class("suggested-action")
        self._split_btn.set_sensitive(False)
        self._split_btn.connect("clicked", self._on_split_clicked)
        header.pack_end(self._split_btn)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        toolbar.set_content(box)

        self._status = Gtk.Label(xalign=0)
        self._status.set_wrap(True)
        box.append(self._status)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._parts = Adw.PreferencesGroup(
            title="Suggested split points",
            description="Turn off any you want to keep inside one meeting.",
        )
        scroll.set_child(self._parts)
        box.append(scroll)

        self._keep_btn = Gtk.Button(label="Keep as one meeting and process it")
        self._keep_btn.set_halign(Gtk.Align.CENTER)
        self._keep_btn.set_sensitive(False)
        self._keep_btn.connect("clicked", self._on_keep_clicked)
        box.append(self._keep_btn)

    # ------------------------------------------------------------------

    def _audio_path(self) -> Path | None:
        for name in ("recording.mp3", "recording.m4a"):
            candidate = self._meeting.path / name
            if candidate.exists():
                return candidate
        return None

    def _analyse(self) -> None:
        audio = self._audio_path()
        if audio is None:
            self._status.set_text("This meeting has no recording to split.")
            return
        self._status.set_text("Looking for long gaps… this reads the whole recording.")

        def _bg() -> None:
            result = analyse(audio)
            idle_call(self._on_analysed, result)

        threading.Thread(target=_bg, daemon=True).start()

    def _on_analysed(self, analysis: Analysis) -> None:
        self._analysis = analysis
        self._status.set_text(analysis.summary())
        if analysis.error:
            return
        self._keep_btn.set_sensitive(True)
        for seam in analysis.seams:
            row = Adw.SwitchRow(
                title=f"Cut at {_clock(seam.start)}",
                subtitle=(
                    f"{format_duration(int(seam.duration))} of quiet, until {_clock(seam.end)}"
                ),
            )
            row.set_active(True)
            row.connect("notify::active", lambda *_: self._refresh_split_button())
            self._seam_switches.append(row)
            self._parts.add(row)
        self._refresh_split_button()

    def _chosen_parts(self):
        if self._analysis is None:
            return []
        seams = [
            seam
            for seam, row in zip(self._analysis.seams, self._seam_switches, strict=True)
            if row.get_active()
        ]
        return parts_from_seams(seams, self._analysis.duration)

    def _refresh_split_button(self) -> None:
        parts = self._chosen_parts()
        if len(parts) > 1:
            self._split_btn.set_label(f"Split into {len(parts)} meetings")
            self._split_btn.set_sensitive(True)
        else:
            self._split_btn.set_label("Nothing to split")
            self._split_btn.set_sensitive(False)

    # ------------------------------------------------------------------

    def _on_keep_clicked(self, *_) -> None:
        if self._on_keep is not None:
            self._on_keep()
        self.close()

    def _on_split_clicked(self, *_) -> None:
        audio = self._audio_path()
        if audio is None or self._analysis is None or self._busy:
            return
        self._busy = True
        self._split_btn.set_sensitive(False)
        self._keep_btn.set_sensitive(False)
        self._status.set_text("Splitting… the original is kept.")
        parts = self._chosen_parts()
        output_folder = self._output_folder

        def _bg() -> None:
            folders, errors = split_recording(audio, parts, output_folder=output_folder)
            idle_call(self._on_split_done, folders, errors)

        threading.Thread(target=_bg, daemon=True).start()

    def _on_split_done(self, folders: list[Path], errors: list[str]) -> None:
        self._busy = False
        if errors and not folders:
            self._status.set_text(" ".join(errors))
            self._split_btn.set_sensitive(True)
            self._keep_btn.set_sensitive(True)
            return
        if self._on_split is not None:
            self._on_split(folders)
        self.close()


def _clock(seconds: float) -> str:
    """Offset into the recording, as h:mm:ss."""
    total = int(max(seconds, 0))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
