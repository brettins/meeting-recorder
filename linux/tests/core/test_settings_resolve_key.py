"""settings.load can skip the Secret Service, which costs tens of ms a call."""

import json

from meeting_recorder.config import settings


class _Boom:
    def get(self):
        raise AssertionError("keyring must not be read")


def test_load_without_key_skips_the_keyring(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gemini_api_key": settings.KEYRING_SENTINEL}))
    monkeypatch.setattr(settings, "_config_path", lambda: path)
    config = settings.load(_Boom(), resolve_key=False)
    assert config["gemini_api_key"] == settings.KEYRING_SENTINEL
