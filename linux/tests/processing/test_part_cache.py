"""Tests for the saved part transcripts."""

from __future__ import annotations

import os
import stat
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


def test_an_empty_entry_is_a_miss(tmp_path):
    cache = PartCache(tmp_path / "parts")
    cache.put("k", "")
    assert cache.get("k") is None


def test_put_leaves_no_temporary_file_and_keeps_the_text_private(tmp_path):
    cache = PartCache(tmp_path / "parts")
    cache.put("k", "text")

    assert [p.name for p in (tmp_path / "parts").iterdir()] == ["k.md"]
    assert stat.S_IMODE((tmp_path / "parts").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "parts" / "k.md").stat().st_mode) == 0o600


def test_a_half_written_entry_is_never_read(tmp_path):
    cache = PartCache(tmp_path / "parts")
    cache.put("k", "complete")
    (tmp_path / "parts" / "k.tmp").write_text("half")
    assert cache.get("k") == "complete"


def test_prune_goes_on_past_an_entry_it_cannot_read(tmp_path):
    cache = PartCache(tmp_path / "parts")
    cache.put("old", "x")
    os.utime(tmp_path / "parts" / "old.md", (1, 1))
    (tmp_path / "parts" / "a-broken-link.md").symlink_to(tmp_path / "missing")

    cache.prune(max_age_seconds=3600)

    assert cache.get("old") is None
