"""Tags survive the round trip through meeting.json."""

import json

import pytest

from meeting_recorder.utils.meeting_scanner import (
    read_metadata,
    scan_meetings,
    set_meeting_tags,
)


def make_meeting(root, name="2026-08-27_18-02", **meta):
    d = root / name
    d.mkdir(parents=True)
    (d / "recording.mp3").write_bytes(b"")
    payload = {"duration_seconds": 20, **meta}
    (d / "meeting.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


class TestScanReadsTags:
    def test_tags_are_loaded_from_metadata(self, tmp_path):
        make_meeting(tmp_path, tags=["Story City", "SING!"])
        (meeting,) = scan_meetings(str(tmp_path))
        assert meeting.tags == ["Story City", "SING!"]

    def test_a_meeting_without_tags_reads_as_untagged(self, tmp_path):
        # Recordings made before tags existed must still scan cleanly.
        make_meeting(tmp_path)
        (meeting,) = scan_meetings(str(tmp_path))
        assert meeting.tags == []

    def test_a_meeting_with_no_metadata_file_reads_as_untagged(self, tmp_path):
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        (meeting,) = scan_meetings(str(tmp_path))
        assert meeting.tags == []

    def test_malformed_tags_do_not_break_the_scan(self, tmp_path):
        make_meeting(tmp_path, tags="not-a-list")
        (meeting,) = scan_meetings(str(tmp_path))
        assert meeting.tags == []


class TestSetMeetingTags:
    def test_writes_tags_without_disturbing_other_metadata(self, tmp_path):
        d = make_meeting(tmp_path, title="Standup")
        set_meeting_tags(d, ["Story City"])
        meta = read_metadata(d)
        assert meta["tags"] == ["Story City"]
        assert meta["title"] == "Standup"
        assert meta["duration_seconds"] == 20

    def test_clearing_tags_persists_an_empty_list(self, tmp_path):
        d = make_meeting(tmp_path, tags=["Story City"])
        set_meeting_tags(d, [])
        assert read_metadata(d)["tags"] == []

    def test_duplicates_are_normalized_on_write(self, tmp_path):
        d = make_meeting(tmp_path)
        set_meeting_tags(d, ["SING!", "sing!", "  "])
        assert read_metadata(d)["tags"] == ["SING!"]


class TestWriteMetadataDoesNotClobber:
    """A failed read must never turn a merge into a truncating overwrite."""

    def test_refuses_to_overwrite_malformed_metadata(self, tmp_path):
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        (d / "meeting.json").write_text("{ not json", encoding="utf-8")

        with pytest.raises(OSError):
            set_meeting_tags(d, ["Story City"])

        # The original bytes must survive so the data can still be recovered.
        assert (d / "meeting.json").read_text(encoding="utf-8") == "{ not json"

    def test_refuses_when_metadata_is_not_an_object(self, tmp_path):
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        (d / "meeting.json").write_text('["a", "list"]', encoding="utf-8")
        with pytest.raises(OSError):
            set_meeting_tags(d, ["Story City"])

    def test_still_creates_metadata_when_none_exists(self, tmp_path):
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        set_meeting_tags(d, ["Story City"])
        assert read_metadata(d)["tags"] == ["Story City"]

    def test_an_empty_object_is_readable_not_a_failure(self, tmp_path):
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        (d / "meeting.json").write_text("{}", encoding="utf-8")
        set_meeting_tags(d, ["Story City"])
        assert read_metadata(d)["tags"] == ["Story City"]

    def test_a_scan_survives_unreadable_metadata(self, tmp_path):
        # Duration caching must not fail the whole scan.
        d = tmp_path / "2026-08-27_18-02"
        d.mkdir(parents=True)
        (d / "recording.mp3").write_bytes(b"")
        (d / "meeting.json").write_text("{ not json", encoding="utf-8")
        (meeting,) = scan_meetings(str(tmp_path))
        assert meeting.tags == []
