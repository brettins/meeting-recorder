"""Tests for the auto-update idle gate and schedule."""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi")

from meeting_recorder.core.state_machine import State
from meeting_recorder.daemon import auto_updater
from meeting_recorder.daemon.auto_updater import AutoUpdater, is_idle
from meeting_recorder.services.auto_update import UpdateOutcome


def _parts(**over):
    engine = SimpleNamespace(
        state=State.IDLE,
        processing_jobs=lambda: [],
        has_pending_input=lambda: False,
    )
    runner = SimpleNamespace(active_descriptions=lambda: [])
    prompt = SimpleNamespace(pending=False)
    installs = SimpleNamespace(busy=lambda: False)
    parts = {"engine": engine, "runner": runner, "call_prompt": prompt, "install_manager": installs}
    parts.update(over)
    return parts


class TestIsIdle:
    def test_nothing_running_is_idle(self):
        assert is_idle(**_parts()) is True

    def test_no_call_prompt_yet_is_idle(self):
        assert is_idle(**_parts(call_prompt=None)) is True

    def test_each_busy_state_blocks(self):
        for state in (State.RECORDING, State.PAUSED, State.COUNTDOWN):
            engine = SimpleNamespace(
                state=state, processing_jobs=lambda: [], has_pending_input=lambda: False
            )
            assert is_idle(**_parts(engine=engine)) is False

    def test_a_processing_job_blocks(self):
        engine = SimpleNamespace(
            state=State.IDLE, processing_jobs=lambda: [object()], has_pending_input=lambda: False
        )
        assert is_idle(**_parts(engine=engine)) is False

    def test_a_typed_title_blocks(self):
        engine = SimpleNamespace(
            state=State.IDLE, processing_jobs=lambda: [], has_pending_input=lambda: True
        )
        assert is_idle(**_parts(engine=engine)) is False

    def test_a_background_task_blocks(self):
        runner = SimpleNamespace(active_descriptions=lambda: ["stop recorder"])
        assert is_idle(**_parts(runner=runner)) is False

    def test_an_open_call_prompt_blocks(self):
        assert is_idle(**_parts(call_prompt=SimpleNamespace(pending=True))) is False

    def test_a_model_install_blocks(self):
        assert is_idle(**_parts(install_manager=SimpleNamespace(busy=lambda: True))) is False


class _Timers:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(
            auto_updater.GLib,
            "timeout_add_seconds",
            lambda seconds, fn: self.calls.append((seconds, fn)) or 1,
        )


class TestSchedule:
    def test_a_check_never_starts_while_busy(self, monkeypatch):
        timers = _Timers(monkeypatch)
        ran = []
        updater = AutoUpdater(lambda: False, lambda: None, check=lambda: ran.append(1))
        updater._tick()
        assert ran == []
        assert timers.calls[0][0] == auto_updater.BUSY_RETRY_SECONDS

    def test_the_restart_waits_for_idle(self, monkeypatch):
        _Timers(monkeypatch)
        idle = {"now": False}
        restarted = []
        updater = AutoUpdater(
            lambda: idle["now"], lambda: restarted.append(1), smoke=lambda: (True, "ok")
        )
        assert updater._restart_when_idle() is auto_updater.GLib.SOURCE_CONTINUE
        assert restarted == []
        idle["now"] = True
        assert updater._restart_when_idle() is auto_updater.GLib.SOURCE_REMOVE
        assert restarted == [1]

    def test_a_failing_start_check_blocks_the_restart(self, monkeypatch):
        _Timers(monkeypatch)
        restarted = []
        updater = AutoUpdater(
            lambda: True, lambda: restarted.append(1), smoke=lambda: (False, "ImportError: x")
        )
        assert updater._restart_when_idle() is auto_updater.GLib.SOURCE_REMOVE
        assert restarted == []

    def test_a_passing_start_check_restarts(self, monkeypatch):
        _Timers(monkeypatch)
        restarted = []
        updater = AutoUpdater(lambda: True, lambda: restarted.append(1), smoke=lambda: (True, "ok"))
        updater._restart_when_idle()
        assert restarted == [1]

    def test_a_failed_update_never_schedules_a_restart(self, monkeypatch):
        timers = _Timers(monkeypatch)
        updater = AutoUpdater(lambda: True, lambda: None)
        updater._on_outcome(UpdateOutcome(False, "pip failed"))
        assert all(fn != updater._restart_when_idle for _s, fn in timers.calls)

    def test_two_updates_start_one_restart_poller(self, monkeypatch):
        timers = _Timers(monkeypatch)
        updater = AutoUpdater(lambda: True, lambda: None)
        updater._on_outcome(UpdateOutcome(True, "a"))
        updater._on_outcome(UpdateOutcome(True, "b"))
        pollers = [fn for _s, fn in timers.calls if fn == updater._restart_when_idle]
        assert len(pollers) == 1
