"""
Tests for settings.update_fields — the keyring-free path for persisting
non-secret settings.

save() round-trips the Gemini key through the Secret Service, whose session
handshake is slow enough to freeze the UI. update_fields() exists so frequent,
non-secret writes (tags) never pay that cost or risk disturbing the key.
"""

import json

import pytest

from meeting_recorder.config import settings


@pytest.fixture
def config_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(settings, "_config_dir", lambda: tmp_path)
    return tmp_path


def write_config(tmp_path, payload):
    (tmp_path / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def read_config(tmp_path):
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


class TestUpdateFields:
    def test_merges_into_existing_config(self, config_in_tmp):
        write_config(config_in_tmp, {"output_folder": "~/meetings"})
        settings.update_fields({"tags": [{"name": "SING!", "color": "purple"}]})
        stored = read_config(config_in_tmp)
        assert stored["output_folder"] == "~/meetings"
        assert stored["tags"] == [{"name": "SING!", "color": "purple"}]

    def test_leaves_the_keyring_sentinel_untouched(self, config_in_tmp):
        write_config(config_in_tmp, {"gemini_api_key": settings.KEYRING_SENTINEL})
        settings.update_fields({"tags": []})
        assert read_config(config_in_tmp)["gemini_api_key"] == settings.KEYRING_SENTINEL

    def test_leaves_a_plaintext_key_untouched(self, config_in_tmp):
        write_config(config_in_tmp, {"gemini_api_key": "a-real-key"})
        settings.update_fields({"tags": []})
        assert read_config(config_in_tmp)["gemini_api_key"] == "a-real-key"

    def test_refuses_to_write_the_api_key(self, config_in_tmp):
        # The key belongs to save()/the keyring; this path must never set it.
        write_config(config_in_tmp, {"gemini_api_key": settings.KEYRING_SENTINEL})
        settings.update_fields({"gemini_api_key": "smuggled", "tags": []})
        assert read_config(config_in_tmp)["gemini_api_key"] == settings.KEYRING_SENTINEL

    def test_creates_the_file_when_absent(self, config_in_tmp):
        settings.update_fields({"tags": []})
        assert read_config(config_in_tmp)["tags"] == []

    def test_survives_a_malformed_config(self, config_in_tmp):
        (config_in_tmp / "config.json").write_text("not json", encoding="utf-8")
        settings.update_fields({"tags": []})
        assert read_config(config_in_tmp)["tags"] == []

    def test_does_not_touch_the_keyring(self, config_in_tmp, monkeypatch):
        def boom():
            raise AssertionError("update_fields must not open a keyring session")

        monkeypatch.setattr(settings, "_get_keyring", boom)
        settings.update_fields({"tags": []})
        assert read_config(config_in_tmp)["tags"] == []
