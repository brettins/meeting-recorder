"""
Headless tests for the daemon Engine.

The RecordingController is replaced with a fake (so no recorder/GLib), the
TaskRunner with a recording stub (so pipeline submission is observed, not run),
and JobManager with a temp-dir instance. This covers the snapshot/job logic
lifted out of MainWindow — state naming, job-status-text tracking, the job-row
actions, and the API-key / duplicate guards.
"""

import json
from pathlib import Path

import pytest

from meeting_recorder.core.job_manager import JobManager
from meeting_recorder.core.state_machine import State
from meeting_recorder.daemon.engine import Engine


class FakeController:
    def __init__(self, **callbacks):
        self.cb = callbacks
        self.state = State.IDLE
        self.calls = []
        self.started_with = None
        self.started_tags = None
        self.live_title = None
        self.live_tags = None
        self.meeting_dir = ""
        self.final = None

    def start(self, cfg, mode, title, tags=None):
        self.started_with = (mode, title)
        self.started_tags = tags
        self.state = State.RECORDING
        self.cb["on_state"](State.RECORDING, "Recording…")

    def set_title(self, title):
        self.live_title = title

    def set_tags(self, tags):
        self.live_tags = tags

    def take_final_paths(self):
        final, self.final = self.final, None
        return final

    def wait_until_stopped(self, timeout=None):
        pass

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self, countdown_enabled):
        self.calls.append(("stop", countdown_enabled))

    def cancel_countdown(self):
        self.calls.append("cancel_countdown")

    def cancel_and_save(self):
        self.calls.append("cancel_and_save")

    def cancel_and_discard(self):
        self.calls.append("cancel_and_discard")


class RecordingRunner:
    """Records submissions without executing the worker (no real pipeline)."""

    def __init__(self):
        self.submissions = []

    def submit(self, fn, *args, on_done=None, on_error=None, description=""):
        self.submissions.append(description)


class FakeProcessorHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeLauncher:
    """Captures processor launches (and callbacks) without spawning a child."""

    def __init__(self):
        self.launches = []

    def launch(self, audio, transcript, notes, *, on_status, on_done, on_error):
        handle = FakeProcessorHandle()
        self.launches.append(
            {
                "audio": audio,
                "on_status": on_status,
                "on_done": on_done,
                "on_error": on_error,
                "handle": handle,
            }
        )
        return handle


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    changes = {"n": 0}
    errors = []
    outputs = []
    ctrl_holder = {}
    launcher = FakeLauncher()

    def factory(**cb):
        ctrl_holder["ctrl"] = FakeController(**cb)
        return ctrl_holder["ctrl"]

    eng = Engine(
        RecordingRunner(),
        on_change=lambda: changes.__setitem__("n", changes["n"] + 1),
        on_error=errors.append,
        on_output=outputs.append,
        job_manager=JobManager(),
        controller_factory=factory,
        processor_launcher=launcher,
    )
    eng._test = {
        "changes": changes,
        "errors": errors,
        "outputs": outputs,
        "ctrl": ctrl_holder,
        "launcher": launcher,
    }
    return eng


def _paths(tmp, name="10-00"):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "recording.mp3"), str(d / "transcript.md"), str(d / "notes.md")


def test_state_name_defaults_idle(engine):
    assert engine.state_name() == "idle"
    snap = json.loads(engine.snapshot_json())
    assert snap["state"] == "idle"
    assert snap["jobs"] == []


def test_import_existing_creates_job_and_launches_processor(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "my recording")
    snap = json.loads(engine.snapshot_json())
    assert len(snap["jobs"]) == 1
    assert snap["jobs"][0]["label"] == "my recording"
    # A processing child was launched (in-daemon threads no longer run the SDK).
    launches = engine._test["launcher"].launches
    assert len(launches) == 1
    assert launches[0]["audio"] == a


def test_processing_done_marks_job_done_and_adopts_paths(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = engine._job_manager.jobs[0].job_id
    # Simulate the child finishing with auto-title-renamed paths.
    renamed = str(tmp_path / "10-00_Retro" / "recording.mp3")
    engine._test["launcher"].launches[0]["on_done"]([renamed, t, n])
    from meeting_recorder.core.job import JobStatus

    job = engine._job_manager.jobs[0]
    assert job.status is JobStatus.DONE
    assert str(job.audio_path) == renamed
    assert job_id not in engine._processors


def test_cancel_job_kills_running_processor(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job = engine._job_manager.jobs[0]
    handle = engine._test["launcher"].launches[0]["handle"]
    engine.cancel_job(job.job_id)
    assert handle.cancelled is True
    assert job.job_id not in engine._processors
    assert engine._job_manager.jobs == []


def test_processing_error_marks_job_error(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    engine._test["launcher"].launches[0]["on_error"]("bad key")
    from meeting_recorder.core.job import JobStatus

    job = engine._job_manager.jobs[0]
    assert job.status is JobStatus.ERROR
    assert (job.error_msg or "") == "bad key"


def test_status_text_appears_in_snapshot(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = json.loads(engine.snapshot_json())["jobs"][0]["job_id"]
    engine._set_job_status_text(job_id, "Transcribing…")
    snap = json.loads(engine.snapshot_json())
    assert snap["jobs"][0]["status_text"] == "Transcribing…"


def test_dismiss_removes_job_and_status_text(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = json.loads(engine.snapshot_json())["jobs"][0]["job_id"]
    engine._set_job_status_text(job_id, "x")
    engine.dismiss_job(job_id)
    assert json.loads(engine.snapshot_json())["jobs"] == []
    assert job_id not in engine._job_status_text


def test_cancel_job_marks_cancelled_and_removes(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job = engine._job_manager.jobs[0]
    engine.cancel_job(job.job_id)
    assert job.cancelled is True
    assert job.token.cancelled is True
    assert engine._job_manager.jobs == []


def test_summarize_duplicate_guard(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    assert engine.summarize_meeting(a, t, n, "m") is None
    # Same audio still processing → rejected.
    assert engine.summarize_meeting(a, t, n, "m") == "This meeting is already being processed."


def test_job_folder_returns_parent(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = engine._job_manager.jobs[0].job_id
    assert engine.job_folder(job_id) == str(Path(a).parent)
    assert engine.job_folder(9999) is None


def test_start_recording_blocks_without_api_key(engine, monkeypatch):
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.load", lambda: {"output_folder": "~/m"}
    )
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.api_key_error", lambda cfg: "No API key set."
    )
    engine.start_recording("headphones")
    assert engine._test["errors"] == ["No API key set."]
    assert engine._test["ctrl"]["ctrl"].started_with is None


def test_start_recording_starts_with_key(engine, monkeypatch):
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.load", lambda: {"output_folder": "~/m"}
    )
    monkeypatch.setattr("meeting_recorder.daemon.engine.settings.api_key_error", lambda cfg: None)
    engine.set_title("Weekly sync")
    engine.start_recording("speaker")
    assert engine._test["ctrl"]["ctrl"].started_with == ("speaker", "Weekly sync")


def _allow_start(monkeypatch):
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.load", lambda: {"output_folder": "~/m"}
    )
    monkeypatch.setattr("meeting_recorder.daemon.engine.settings.api_key_error", lambda cfg: None)


def test_set_tags_are_passed_to_the_controller(engine, monkeypatch):
    """Tags chosen on the Record tab must reach the recording that starts."""
    _allow_start(monkeypatch)
    engine.set_tags(["Story City", "SING!"])
    engine.start_recording("headphones")
    assert engine._test["ctrl"]["ctrl"].started_tags == ["Story City", "SING!"]


def test_tags_default_to_none_when_unset(engine, monkeypatch):
    _allow_start(monkeypatch)
    engine.start_recording("headphones")
    assert engine._test["ctrl"]["ctrl"].started_tags is None


def test_empty_tag_list_is_normalised_to_none(engine, monkeypatch):
    # An empty selection must not be stored as [] and re-sent on a tray start.
    _allow_start(monkeypatch)
    engine.set_tags([])
    engine.start_recording("headphones")
    assert engine._test["ctrl"]["ctrl"].started_tags is None


# ---------------------------------------------------------------------------
# Live title/tag editing and the queued folder rename
# ---------------------------------------------------------------------------


def test_title_and_tags_are_forwarded_to_the_controller_live(engine):
    """A mid-recording edit has to reach the running recording, not just the
    next one — the controller writes it to meeting.json there and then."""
    engine.set_title("Weekly Sync")
    engine.set_tags(["Story City"])

    ctrl = engine._test["ctrl"]["ctrl"]
    assert ctrl.live_title == "Weekly Sync"
    assert ctrl.live_tags == ["Story City"]


def test_the_snapshot_carries_the_pending_title_and_tags(engine):
    engine.set_title("Weekly Sync")
    engine.set_tags(["Story City", "SING!"])

    snap = json.loads(engine.snapshot_json())
    assert snap["title"] == "Weekly Sync"
    assert snap["tags"] == ["Story City", "SING!"]


def test_a_blank_title_clears_rather_than_storing_whitespace(engine):
    engine.set_title("   ")
    assert json.loads(engine.snapshot_json())["title"] == ""
    assert engine._test["ctrl"]["ctrl"].live_title is None


def test_editing_the_title_notifies_listeners(engine):
    before = engine._test["changes"]["n"]
    engine.set_title("Weekly Sync")
    assert engine._test["changes"]["n"] > before


def test_the_snapshot_reports_the_folder_being_recorded_into(engine):
    engine._test["ctrl"]["ctrl"].meeting_dir = "/meetings/2026-03-01_14-30"
    assert json.loads(engine.snapshot_json())["recording_dir"] == "/meetings/2026-03-01_14-30"


def test_title_and_tags_reset_once_the_recording_ends(engine):
    engine.set_title("Weekly Sync")
    engine.set_tags(["Story City"])

    # The controller reports IDLE when the recording is committed or cancelled.
    engine._on_state(State.IDLE, "")

    snap = json.loads(engine.snapshot_json())
    assert snap["title"] == ""
    assert snap["tags"] == []


def test_a_renamed_recording_folder_is_adopted_by_its_job(engine, tmp_path):
    """The stop worker renames the folder after ffmpeg exits, so the job's paths
    are stale by the time processing starts and must be replaced."""
    from meeting_recorder.core.recording_controller import PendingRecording

    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job = engine._job_manager.jobs[0]

    renamed = tmp_path / "2026-03-01_14-30_Weekly_Sync"
    engine._adopt_recording_paths(
        job,
        PendingRecording(
            audio_path=renamed / "recording.mp3",
            transcript_path=renamed / "transcript.md",
            notes_path=renamed / "notes.md",
            label="2026-03-01_14-30 Weekly Sync",
            title="Weekly Sync",
        ),
    )

    assert job.audio_path == renamed / "recording.mp3"
    assert job.transcript_path == renamed / "transcript.md"
    assert job.notes_path == renamed / "notes.md"
    assert job.label == "2026-03-01_14-30 Weekly Sync"


def test_committing_a_recording_clears_the_recording_status(engine, monkeypatch):
    """Stopping reports IDLE with no message; the old status must not survive it.

    Falling back to the previous status left "Recording… (headphones mode)" on
    screen after the recording had already stopped.
    """
    _allow_start(monkeypatch)
    engine.start_recording("headphones")
    assert "Recording" in json.loads(engine.snapshot_json())["status"]

    engine._on_state(State.IDLE, "")

    assert json.loads(engine.snapshot_json())["status"] == "Ready to record"


def test_an_explicit_idle_message_is_still_shown(engine):
    engine._on_state(State.IDLE, "Recording discarded.")
    assert json.loads(engine.snapshot_json())["status"] == "Recording discarded."


def test_a_blank_status_mid_recording_keeps_the_previous_one(engine, monkeypatch):
    _allow_start(monkeypatch)
    engine.start_recording("headphones")
    before = json.loads(engine.snapshot_json())["status"]

    engine._on_state(State.RECORDING, "")

    assert json.loads(engine.snapshot_json())["status"] == before
