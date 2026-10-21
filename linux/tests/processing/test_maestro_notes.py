"""Tests for posting finished meeting notes to a Maestro item."""

import json
import subprocess

from meeting_recorder.processing import maestro_notes
from meeting_recorder.processing.maestro_notes import (
    POSTED_KEY,
    backfill,
    format_comment,
    notes_key,
    pending,
    post_notes,
)

CONFIG = {"maestro_notes_key": "BRT-46"}


def _meeting(root, name="2026-10-21_09-37_Sync", notes="## Summary\nShipped it.", meta=None):
    folder = root / name
    folder.mkdir()
    (folder / "meeting.json").write_text(
        json.dumps(
            meta
            if meta is not None
            else {"title": "Sync", "duration_seconds": 2832, "tags": ["pagepack"]}
        )
    )
    if notes is not None:
        (folder / "notes.md").write_text(notes)
    return folder


class _Recorder:
    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


class TestFormatComment:
    def test_carries_title_when_duration_tags_path_and_notes(self, tmp_path):
        text = format_comment(_meeting(tmp_path), "Shipped it.")
        assert text.startswith("**Sync** · 2026-10-21 09-37 · 47 minutes · pagepack")
        assert "`" + str(tmp_path / "2026-10-21_09-37_Sync") + "`" in text
        assert text.rstrip().endswith("Shipped it.")

    def test_a_meeting_without_metadata_falls_back_to_its_folder_name(self, tmp_path):
        folder = _meeting(tmp_path, meta={})
        assert format_comment(folder, "x").startswith("**2026-10-21_09-37_Sync**")

    def test_very_long_notes_are_cut_with_a_pointer(self, tmp_path):
        text = format_comment(_meeting(tmp_path), "a" * 40000)
        assert "notes continue in the folder" in text
        assert len(text) < 31000

    def test_odd_tags_do_not_raise(self, tmp_path):
        folder = _meeting(tmp_path, meta={"title": "T", "tags": [1, "x"]})
        assert "1 · x" in format_comment(folder, "n")
        assert format_comment(_meeting(tmp_path, "2026-10-21_10-00_X", meta={"tags": 5}), "n")


class TestPostNotes:
    def test_posts_to_the_configured_item_and_records_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        folder = _meeting(tmp_path)
        run = _Recorder()
        assert post_notes(folder, CONFIG, run_fn=run) is True
        command, kwargs = run.calls[0]
        assert command == ["maestro", "comment", "BRT-46", "-"]
        assert "Shipped it." in kwargs["input"]
        assert json.loads((folder / "meeting.json").read_text())[POSTED_KEY] == "BRT-46"

    def test_a_posted_meeting_is_not_posted_twice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        folder = _meeting(tmp_path)
        run = _Recorder()
        post_notes(folder, CONFIG, run_fn=run)
        assert post_notes(folder, CONFIG, run_fn=run) is False
        assert len(run.calls) == 1

    def test_without_a_key_nothing_runs(self, tmp_path):
        run = _Recorder()
        assert post_notes(_meeting(tmp_path), {}, run_fn=run) is False
        assert run.calls == []

    def test_without_notes_nothing_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        run = _Recorder()
        assert post_notes(_meeting(tmp_path, notes=None), CONFIG, run_fn=run) is False
        assert run.calls == []

    def test_a_refusal_is_reported_and_not_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        folder = _meeting(tmp_path)
        assert post_notes(folder, CONFIG, run_fn=_Recorder(1, "maestro: 404")) is False
        assert POSTED_KEY not in json.loads((folder / "meeting.json").read_text())

    def test_a_missing_maestro_command_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: None)
        assert post_notes(_meeting(tmp_path), CONFIG, run_fn=_Recorder()) is False

    def test_a_failing_subprocess_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")

        def boom(*_a, **_k):
            raise subprocess.TimeoutExpired("maestro", 60)

        assert post_notes(_meeting(tmp_path), CONFIG, run_fn=boom) is False


class TestBackfill:
    def test_one_bad_meeting_does_not_stop_the_rest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        bad = _meeting(tmp_path, "2026-10-20_09-37_Bad")
        bad.joinpath("notes.md").write_bytes(b"\xff\xfe")
        good = _meeting(tmp_path, "2026-10-21_09-37_Good")
        assert backfill(tmp_path, CONFIG, run_fn=_Recorder()) == [good]

    def test_posts_each_unposted_meeting_oldest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maestro_notes.shutil, "which", lambda _n: "/bin/maestro")
        b = _meeting(tmp_path, "2026-10-21_09-37_B")
        a = _meeting(tmp_path, "2026-10-20_09-37_A")
        _meeting(tmp_path, "2026-10-19_09-37_C", notes=None)
        assert backfill(tmp_path, CONFIG, run_fn=_Recorder()) == [a, b]


def test_notes_key_is_trimmed():
    assert notes_key({"maestro_notes_key": " BRT-46 "}) == "BRT-46"
    assert notes_key({}) == ""


class TestPending:
    def test_lists_only_meetings_with_notes_that_are_not_posted(self, tmp_path):
        waiting = _meeting(tmp_path, "2026-10-20_09-37_A")
        posted = _meeting(tmp_path, "2026-10-21_09-37_B")
        (posted / "meeting.json").write_text(json.dumps({POSTED_KEY: "BRT-46"}))
        _meeting(tmp_path, "2026-10-19_09-37_C", notes=None)
        assert pending(tmp_path, CONFIG) == [waiting]
