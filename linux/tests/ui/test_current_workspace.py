"""Tests for following the user's desktop and activity when the window is presented."""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi")

from meeting_recorder.ui import current_workspace  # noqa: E402


def test_activity_command_pins_one_activity():
    cmd = current_workspace.activity_command(42, "abc-123")
    assert cmd == [
        "xprop",
        "-id",
        "42",
        "-f",
        "_KDE_NET_WM_ACTIVITIES",
        "8s",
        "-set",
        "_KDE_NET_WM_ACTIVITIES",
        "abc-123",
    ]


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
