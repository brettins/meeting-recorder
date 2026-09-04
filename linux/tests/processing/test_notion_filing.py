"""Tests for the meeting → Notion filing workflow, with a fake client."""

import pytest

from meeting_recorder.processing.notion_filing import (
    build_body,
    file_meeting,
    read_text,
)
from meeting_recorder.services.notion_service import NotionError, NotionPage

PAGE = NotionPage(id="page1", title="SING! marketing", date="2026-09-03", url="https://n/page1")


class FakeClient:
    """Records what filing asked of Notion; optionally fails the upload."""

    def __init__(self, upload_error=None):
        self.appended = []
        self.uploaded = []
        self.attached = []
        self._upload_error = upload_error

    def append_blocks(self, page_id, batches):
        self.appended.append((page_id, batches))

    def upload_file(self, path):
        if self._upload_error:
            raise self._upload_error
        self.uploaded.append(path)
        return "upload1"

    def attach_file(self, page_id, property_name, upload_id, filename):
        self.attached.append((page_id, property_name, upload_id, filename))


@pytest.fixture
def meeting(tmp_path):
    (tmp_path / "notes.md").write_text("### Summary\nWe met.\n")
    (tmp_path / "transcript.md").write_text("Alice: hello\n\nBob: hi\n")
    (tmp_path / "recording.mp3").write_bytes(b"ID3audio")
    return tmp_path


def _file(client, meeting, **kw):
    options = {
        "notes_path": meeting / "notes.md",
        "transcript_path": meeting / "transcript.md",
        "audio_path": meeting / "recording.mp3",
        "files_property": "Recording",
    }
    options.update(kw)
    return file_meeting(client, PAGE, **options)


class TestReadText:
    def test_missing_file_reads_as_empty(self, tmp_path):
        assert read_text(tmp_path / "absent.md") == ""

    def test_none_reads_as_empty(self):
        assert read_text(None) == ""

    def test_existing_file_is_returned(self, tmp_path):
        (tmp_path / "notes.md").write_text("hello")
        assert read_text(tmp_path / "notes.md") == "hello"


class TestBuildBody:
    def test_notes_come_before_the_transcript(self):
        body = build_body("# Notes", "spoken words")
        assert body[0]["type"] == "heading_1"
        assert body[-1]["type"] == "toggle"

    def test_transcript_is_folded_into_a_toggle(self):
        body = build_body("", "spoken words")
        assert [b["type"] for b in body] == ["toggle"]

    def test_notes_alone_produce_no_toggle(self):
        body = build_body("# Notes", "")
        assert all(b["type"] != "toggle" for b in body)

    def test_whitespace_only_content_produces_nothing(self):
        assert build_body("   \n", "\n\n") == []

    def test_toggle_label_is_configurable(self):
        body = build_body("", "words", transcript_label="Full transcript")
        label = body[0]["toggle"]["rich_text"][0]["text"]["content"]
        assert label == "Full transcript"


class TestFileMeeting:
    def test_everything_reaches_notion(self, meeting):
        client = FakeClient()
        result = _file(client, meeting)
        assert result.ok
        assert result.notes_written and result.transcript_written and result.audio_attached
        assert client.appended[0][0] == "page1"
        assert client.uploaded == [meeting / "recording.mp3"]
        assert client.attached == [("page1", "Recording", "upload1", "recording.mp3")]

    def test_notes_are_written_before_the_upload(self, meeting):
        order = []
        client = FakeClient()
        client.append_blocks = lambda *a: order.append("append")
        client.upload_file = lambda p: (order.append("upload"), "u1")[1]
        client.attach_file = lambda *a: order.append("attach")
        _file(client, meeting)
        assert order == ["append", "upload", "attach"]

    def test_a_meeting_without_audio_still_files(self, meeting):
        client = FakeClient()
        result = _file(client, meeting, audio_path=None)
        assert result.ok
        assert result.notes_written
        assert client.uploaded == []

    def test_a_missing_audio_file_is_not_an_error(self, meeting):
        client = FakeClient()
        result = _file(client, meeting, audio_path=meeting / "absent.mp3")
        assert result.ok
        assert result.audio_attached is False

    def test_a_meeting_without_notes_files_the_transcript(self, meeting):
        client = FakeClient()
        result = _file(client, meeting, notes_path=None)
        assert result.notes_written is False
        assert result.transcript_written is True

    def test_an_empty_meeting_warns_and_writes_nothing(self, tmp_path):
        client = FakeClient()
        result = file_meeting(client, PAGE, notes_path=None, transcript_path=None)
        assert client.appended == []
        assert result.ok is False
        assert "no notes or transcript" in result.warnings[0]

    def test_audio_without_a_files_property_warns(self, meeting):
        client = FakeClient()
        result = _file(client, meeting, files_property="")
        assert result.notes_written is True
        assert result.audio_attached is False
        assert "Settings → Notion" in result.warnings[0]
        assert client.uploaded == []

    def test_a_refused_upload_keeps_the_notes_and_reports(self, meeting):
        client = FakeClient(upload_error=NotionError("file too large"))
        result = _file(client, meeting)
        assert result.notes_written is True
        assert result.audio_attached is False
        assert "file too large" in result.warnings[0]

    def test_progress_is_reported_for_each_stage(self, meeting):
        seen = []
        _file(FakeClient(), meeting, on_progress=seen.append)
        assert any("notes" in m for m in seen)
        assert any("recording" in m for m in seen)

    def test_the_result_carries_the_page_it_wrote_to(self, meeting):
        assert _file(FakeClient(), meeting).page is PAGE
