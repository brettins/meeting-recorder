"""notify_with_actions builds a silent, resident notify-send call and reports the chosen action."""

import subprocess

from meeting_recorder.ui import notifications


class _Proc:
    def __init__(self, out, code=0):
        self._out = out
        self.returncode = code

    def communicate(self):
        return self._out, ""


class _Inline:
    def __init__(self, target, **_kw):
        self._target = target

    def start(self):
        self._target()


def _run(monkeypatch, out, code=0):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd) or _Proc(out, code))
    monkeypatch.setattr(notifications.threading, "Thread", _Inline)
    chosen = []
    notifications.notify_with_actions(
        "Record this meeting?", "body", [("record", "Record"), ("not-now", "Not now")], chosen.append
    )
    return calls[0], chosen


def test_the_call_is_silent_resident_and_carries_both_buttons(monkeypatch):
    cmd, _ = _run(monkeypatch, "")
    assert "--hint=boolean:suppress-sound:true" in cmd and "--hint=boolean:resident:true" in cmd
    assert "--expire-time=600000" in cmd
    assert "--action=record=Record" in cmd and "--action=not-now=Not now" in cmd


def test_the_chosen_action_is_reported(monkeypatch):
    assert _run(monkeypatch, "record\n")[1] == ["record"]


def test_a_dismissed_notification_ends_with_an_empty_choice(monkeypatch):
    assert _run(monkeypatch, "")[1] == [""]


def test_a_notify_send_without_actions_falls_back_to_a_plain_notification(monkeypatch):
    plain = []
    monkeypatch.setattr(notifications, "notify", lambda *args: plain.append(args))
    _cmd, chosen = _run(monkeypatch, "", code=1)
    assert chosen == [""] and plain and plain[0][0] == "Record this meeting?"
