"""Tests for the saved part transcripts."""

from __future__ import annotations

import os
from pathlib import Path

from meeting_recorder.processing.part_cache import PartCache


def _audio(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "part.mp3"
    path.write_bytes(content)
    return path


def test_the_key_follows_the_audio_bytes_and_the_request(tmp_path):
    a = _audio(tmp_path, b"one")
    base = PartCache.key(a, "model", "prompt", "1/3")
    assert PartCache.key(a, "model", "prompt", "1/3") == base
    assert PartCache.key(a, "other", "prompt", "1/3") != base
    assert PartCache.key(a, "model", "prompt", "2/3") != base
    assert PartCache.key(_audio(tmp_path, b"two"), "model", "prompt", "1/3") != base


def test_put_get_discard(tmp_path):
    cache = PartCache(tmp_path / "parts")
    assert cache.get("k") is None
    cache.put("k", "text")
    assert cache.get("k") == "text"
    cache.discard(["k", "never-saved"])
    assert cache.get("k") is None


def test_prune_removes_only_old_entries(tmp_path):
    cache = PartCache(tmp_path / "parts")
    cache.put("old", "x")
    cache.put("new", "y")
    old = tmp_path / "parts" / "old.md"
    os.utime(old, (1, 1))

    cache.prune(max_age_seconds=3600)

    assert cache.get("old") is None
    assert cache.get("new") == "y"


def test_an_unwritable_directory_is_not_an_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = PartCache(blocker / "parts")
    cache.put("k", "text")
    assert cache.get("k") is None
    cache.prune()
