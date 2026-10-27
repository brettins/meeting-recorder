"""Tests for following the user's desktop and activity when the window is presented."""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi")

from meeting_recorder.ui import current_workspace  # noqa: E402


def test_unrealized_window_is_left_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(current_workspace, "current_activity", lambda: calls.append("asked"))
    current_workspace.bring_to_current_workspace(SimpleNamespace(get_surface=lambda: None))
    assert calls == []


def test_non_x11_surface_is_left_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(current_workspace, "current_activity", lambda: calls.append("asked"))
    window = SimpleNamespace(get_surface=lambda: object())
    current_workspace.bring_to_current_workspace(window)
    assert calls == []


def test_pin_runs_set_when_window_is_on_another_activity(monkeypatch):
    runs = []
    monkeypatch.setattr(
        current_workspace, "current_activity", lambda: "aaaaaaaa-0000-0000-0000-000000000001"
    )
    outputs = iter(['_KDE_NET_WM_ACTIVITIES(STRING) = "bbbbbbbb-0000-0000-0000-000000000002"', ""])
    monkeypatch.setattr(
        current_workspace, "_xprop", lambda args: runs.append(args) or next(outputs)
    )
    current_workspace.pin_to_current_activity(42)
    assert ["-set" in r for r in runs] == [False, True]
    assert runs[1][-1] == "aaaaaaaa-0000-0000-0000-000000000001"


def test_pin_skips_when_already_on_current_activity(monkeypatch):
    runs = []
    current = "aaaaaaaa-0000-0000-0000-000000000001"
    monkeypatch.setattr(current_workspace, "current_activity", lambda: current)
    monkeypatch.setattr(
        current_workspace, "_xprop", lambda args: runs.append(args) or f'= "{current}"'
    )
    current_workspace.pin_to_current_activity(42)
    assert len(runs) == 1
