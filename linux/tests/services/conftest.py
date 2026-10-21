import pytest

from meeting_recorder.services import self_update


@pytest.fixture(autouse=True)
def _isolated_install_dir(tmp_path, monkeypatch):
    """Keep update.lock out of the real ~/.local/share/meeting-recorder."""
    monkeypatch.setattr(self_update, "install_dir", lambda *_a, **_k: tmp_path)
