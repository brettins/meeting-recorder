"""Tests for the Notion client and its pure helpers, with a fake opener."""

import json
import urllib.error

import pytest

from meeting_recorder.services.notion_service import (
    NotionClient,
    NotionError,
    encode_multipart,
    error_message,
    files_property_names,
    free_plan_hint,
    guess_content_type,
    page_date,
    page_title,
    parse_page_summary,
    title_property_name,
    upload_size_error,
)

# ── fake opener ───────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers each call from a queue, recording the requests it was given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class _BodyHTTPError(urllib.error.HTTPError):
    def __init__(self, code, payload):
        super().__init__("https://api.notion.com", code, "err", {}, None)
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


def _client(replies):
    opener = FakeOpener(replies)
    return NotionClient("secret_token", http_open=opener, sleep_fn=lambda _s: None), opener


# ── pure helpers ──────────────────────────────────────────────────────────────


class TestPageReading:
    def test_title_is_read_from_the_title_property(self):
        page = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Kickoff"}]}}}
        assert page_title(page) == "Kickoff"

    def test_title_joins_split_rich_text(self):
        page = {
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Stand-Up "}, {"plain_text": "2026-09-03"}],
                }
            }
        }
        assert page_title(page) == "Stand-Up 2026-09-03"

    def test_title_property_may_have_any_name(self):
        page = {"properties": {"Topic": {"type": "title", "title": [{"plain_text": "X"}]}}}
        assert page_title(page) == "X"

    def test_missing_title_is_empty(self):
        assert page_title({"properties": {}}) == ""
        assert page_title({}) == ""

    def test_date_is_trimmed_to_the_day(self):
        page = {"properties": {"Date": {"type": "date", "date": {"start": "2026-09-03T10:00:00Z"}}}}
        assert page_date(page) == "2026-09-03"

    def test_empty_date_property_is_none(self):
        page = {"properties": {"Date": {"type": "date", "date": None}}}
        assert page_date(page) is None

    def test_summary_falls_back_to_untitled(self):
        summary = parse_page_summary({"id": "abc", "properties": {}, "url": "https://n/abc"})
        assert summary.id == "abc"
        assert summary.title == "Untitled"
        assert summary.date is None
        assert summary.url == "https://n/abc"


class TestSchemaReading:
    def test_files_properties_are_listed_alphabetically(self):
        schema = {
            "properties": {
                "Recording": {"type": "files"},
                "Name": {"type": "title"},
                "Attachments": {"type": "files"},
            }
        }
        assert files_property_names(schema) == ["Attachments", "Recording"]

    def test_no_files_property_yields_an_empty_list(self):
        assert files_property_names({"properties": {"Name": {"type": "title"}}}) == []

    def test_title_property_is_found_by_type_not_name(self):
        schema = {"properties": {"Topic": {"type": "title"}, "Date": {"type": "date"}}}
        assert title_property_name(schema) == "Topic"

    def test_title_property_defaults_when_absent(self):
        assert title_property_name({"properties": {}}) == "Name"


class TestSizeChecks:
    def test_a_small_file_is_allowed(self):
        assert upload_size_error(1024) is None

    def test_over_twenty_mib_is_refused_with_the_size(self):
        message = upload_size_error(25 * 1024 * 1024)
        assert message is not None
        assert "25.0 MiB" in message
        assert "20 MiB" in message

    def test_free_plan_hint_only_above_five_mib(self):
        assert free_plan_hint(4 * 1024 * 1024) is None
        hint = free_plan_hint(15 * 1024 * 1024)
        assert hint is not None
        assert "5 MiB" in hint


class TestErrorMessage:
    def test_401_points_at_the_token(self):
        assert "token" in error_message(401, b"{}")

    def test_404_explains_sharing(self):
        assert "Share it with the integration" in error_message(404, b"{}")

    def test_403_names_permission(self):
        assert "permission" in error_message(403, b"{}")

    def test_other_statuses_use_notions_own_message(self):
        assert error_message(400, json.dumps({"message": "body failed validation"})) == (
            "body failed validation"
        )

    def test_unparseable_body_still_gives_a_sentence(self):
        assert error_message(500, b"<html>") == "Notion returned HTTP 500."


class TestMultipart:
    def test_body_carries_the_file_field_and_bytes(self):
        body, header = encode_multipart("recording.mp3", "audio/mpeg", b"\x00\x01")
        assert b'name="file"; filename="recording.mp3"' in body
        assert b"audio/mpeg" in body
        assert b"\x00\x01" in body
        assert header.startswith("multipart/form-data; boundary=")

    def test_boundary_in_the_header_matches_the_body(self):
        body, header = encode_multipart("a.mp3", "audio/mpeg", b"x")
        boundary = header.split("boundary=")[1]
        assert body.startswith(f"--{boundary}\r\n".encode())
        assert body.endswith(f"\r\n--{boundary}--\r\n".encode())

    def test_quotes_in_a_filename_cannot_break_the_header(self):
        body, _ = encode_multipart('we"ird.mp3', "audio/mpeg", b"x")
        assert b'filename="weird.mp3"' in body

    def test_each_body_uses_a_fresh_boundary(self):
        _, first = encode_multipart("a.mp3", "audio/mpeg", b"x")
        _, second = encode_multipart("a.mp3", "audio/mpeg", b"x")
        assert first != second


def test_content_type_is_guessed_from_the_extension(tmp_path):
    assert guess_content_type(tmp_path / "recording.mp3") == "audio/mpeg"


def test_unknown_extension_falls_back_to_a_stream(tmp_path):
    assert guess_content_type(tmp_path / "recording.zzz") == "application/octet-stream"


# ── client ────────────────────────────────────────────────────────────────────


class TestClientRequests:
    def test_token_and_version_are_sent(self):
        client, opener = _client([{"data_sources": [{"id": "ds1"}]}])
        client.resolve_data_source("db1")
        headers = opener.requests[0].headers
        assert headers["Authorization"] == "Bearer secret_token"
        assert headers["Notion-version"] == "2025-09-03"

    def test_data_source_is_resolved_from_the_database(self):
        client, _ = _client([{"data_sources": [{"id": "ds1"}, {"id": "ds2"}]}])
        assert client.resolve_data_source("db1") == "ds1"

    def test_database_without_data_sources_resolves_to_itself(self):
        client, _ = _client([{}])
        assert client.resolve_data_source("db1") == "db1"

    def test_recent_pages_are_summarised(self):
        reply = {
            "results": [
                {
                    "id": "p1",
                    "url": "https://n/p1",
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "Marketing"}]},
                        "Date": {"type": "date", "date": {"start": "2026-09-02"}},
                    },
                }
            ]
        }
        client, opener = _client([reply])
        pages = client.recent_pages("ds1")
        assert [p.title for p in pages] == ["Marketing"]
        assert pages[0].date == "2026-09-02"
        assert opener.requests[0].full_url.endswith("/data_sources/ds1/query")

    def test_recent_pages_tolerates_an_empty_database(self):
        client, _ = _client([{"results": []}])
        assert client.recent_pages("ds1") == []

    def test_create_page_sets_the_title_property(self):
        client, opener = _client([{"id": "new", "properties": {}, "url": "https://n/new"}])
        client.create_page("ds1", "SING! marketing", title_property="Topic")
        body = json.loads(opener.requests[0].data)
        assert body["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
        assert body["properties"]["Topic"]["title"][0]["text"]["content"] == "SING! marketing"

    def test_create_page_sets_a_date_when_asked(self):
        client, opener = _client([{"id": "new", "properties": {}}])
        client.create_page("ds1", "T", date_property="Date", date="2026-09-03")
        body = json.loads(opener.requests[0].data)
        assert body["properties"]["Date"] == {"date": {"start": "2026-09-03"}}

    def test_create_page_omits_the_date_when_there_is_none(self):
        client, opener = _client([{"id": "new", "properties": {}}])
        client.create_page("ds1", "T", date_property="Date")
        assert "Date" not in json.loads(opener.requests[0].data)["properties"]

    def test_append_sends_one_request_per_batch(self):
        client, opener = _client([{}, {}])
        client.append_blocks("page1", [[{"a": 1}], [{"b": 2}]])
        assert len(opener.requests) == 2
        assert opener.requests[0].full_url.endswith("/blocks/page1/children")
        assert json.loads(opener.requests[1].data) == {"children": [{"b": 2}]}

    def test_append_skips_empty_batches(self):
        client, opener = _client([{}])
        client.append_blocks("page1", [[], [{"a": 1}]])
        assert len(opener.requests) == 1

    def test_attach_file_points_the_property_at_the_upload(self):
        client, opener = _client([{}])
        client.attach_file("page1", "Recording", "up1")
        body = json.loads(opener.requests[0].data)
        prop = body["properties"]["Recording"]
        assert prop["type"] == "files"
        assert prop["files"] == [{"type": "file_upload", "file_upload": {"id": "up1"}}]


class TestUpload:
    def test_upload_creates_then_sends(self, tmp_path):
        audio = tmp_path / "recording.mp3"
        audio.write_bytes(b"ID3audio")
        client, opener = _client([{"id": "up1", "upload_url": "https://upload/1"}, b""])
        assert client.upload_file(audio) == "up1"
        created = json.loads(opener.requests[0].data)
        assert created == {
            "mode": "single_part",
            "filename": "recording.mp3",
            "content_type": "audio/mpeg",
        }
        assert opener.requests[1].full_url == "https://upload/1"
        assert b"ID3audio" in opener.requests[1].data

    def test_oversized_file_never_reaches_the_network(self, tmp_path):
        audio = tmp_path / "big.mp3"
        audio.write_bytes(b"x" * (21 * 1024 * 1024))
        client, opener = _client([])
        with pytest.raises(NotionError, match="20 MiB"):
            client.upload_file(audio)
        assert opener.requests == []

    def test_a_reply_without_an_upload_url_falls_back_to_the_send_path(self, tmp_path):
        audio = tmp_path / "recording.mp3"
        audio.write_bytes(b"x")
        client, opener = _client([{"id": "up1"}, b""])
        assert client.upload_file(audio) == "up1"
        assert opener.requests[1].full_url.endswith("/file_uploads/up1/send")

    def test_a_reply_without_an_id_is_reported(self, tmp_path):
        audio = tmp_path / "recording.mp3"
        audio.write_bytes(b"x")
        client, _ = _client([{"upload_url": "https://upload/1"}])
        with pytest.raises(NotionError, match="upload target"):
            client.upload_file(audio)


class TestFailures:
    def test_bad_token_is_translated(self):
        client, _ = _client([_BodyHTTPError(401, {})])
        with pytest.raises(NotionError, match="token"):
            client.resolve_data_source("db1")

    def test_unshared_database_is_translated(self):
        client, _ = _client([_BodyHTTPError(404, {})])
        with pytest.raises(NotionError, match="Share it with the integration"):
            client.resolve_data_source("db1")

    def test_validation_error_uses_notions_message(self):
        client, _ = _client([_BodyHTTPError(400, {"message": "body.parent should be defined"})])
        with pytest.raises(NotionError, match="body.parent"):
            client.create_page("ds1", "T")

    def test_server_error_is_retried_then_succeeds(self):
        client, opener = _client([_BodyHTTPError(500, {}), {"data_sources": [{"id": "ds1"}]}])
        assert client.resolve_data_source("db1") == "ds1"
        assert len(opener.requests) == 2

    def test_client_error_is_not_retried(self):
        client, opener = _client([_BodyHTTPError(400, {"message": "nope"})])
        with pytest.raises(NotionError):
            client.resolve_data_source("db1")
        assert len(opener.requests) == 1

    def test_unreachable_host_is_reported(self):
        client, _ = _client([urllib.error.URLError("name resolution failed")])
        with pytest.raises(NotionError, match="Could not reach Notion"):
            client.resolve_data_source("db1")

    def test_unreadable_reply_is_reported(self):
        client, _ = _client([b"<html>not json"])
        with pytest.raises(NotionError, match="could not read"):
            client.resolve_data_source("db1")

    def test_empty_reply_is_treated_as_an_empty_object(self):
        client, _ = _client([b""])
        assert client.resolve_data_source("db1") == "db1"
