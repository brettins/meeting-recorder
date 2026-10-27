"""Tests for the Maestro client, with a fake opener. Nothing reaches a real server."""

import json
import urllib.error

import pytest

from meeting_recorder.services.maestro_service import (
    DEFAULT_URL,
    MaestroClient,
    MaestroError,
    default_client,
    error_message,
    parse_filed,
)

REPLY = {
    "id": 650,
    "key": "SNG-1",
    "url": "/items/650",
    "created": True,
    "tasks": [
        {"id": 651, "key": "SNG-2", "title": "Ring the venue", "created": True},
        {"id": 652, "key": "SNG-3", "title": "Send the budget", "created": False},
    ],
}


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
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class _HTTPError(urllib.error.HTTPError):
    def __init__(self, code, payload):
        super().__init__("http://maestro", code, "err", {}, None)
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


def _client(replies, token=None):
    opener = FakeOpener(replies)
    client = MaestroClient(
        "http://maestro:8000/", token, http_open=opener, sleep_fn=lambda _s: None
    )
    return client, opener


class TestFiling:
    def test_it_posts_json_to_the_meetings_endpoint(self):
        client, opener = _client([REPLY])
        client.file_meeting({"meeting_id": "m", "title": "Standup"})
        request = opener.requests[0]
        assert request.full_url == "http://maestro:8000/api/meetings"
        assert request.get_method() == "POST"
        assert json.loads(request.data) == {"meeting_id": "m", "title": "Standup"}

    def test_no_token_means_no_authorization_header(self):
        client, opener = _client([REPLY])
        client.file_meeting({})
        assert not opener.requests[0].has_header("Authorization")

    def test_a_given_token_is_a_bearer_header(self):
        client, opener = _client([REPLY], token="tok-123")
        client.file_meeting({})
        assert opener.requests[0].get_header("Authorization") == "Bearer tok-123"

    def test_the_reply_is_read(self):
        client, _ = _client([REPLY])
        filed = client.file_meeting({})
        assert filed.key == "SNG-1"
        assert filed.url == "http://maestro:8000/items/650"
        assert [t.key for t in filed.tasks] == ["SNG-2", "SNG-3"]
        assert filed.new_tasks == 1

    def test_a_server_error_is_retried(self):
        client, opener = _client([_HTTPError(502, {}), REPLY])
        assert client.file_meeting({}).key == "SNG-1"
        assert len(opener.requests) == 2

    def test_a_refusal_is_not_retried(self):
        client, opener = _client([_HTTPError(401, {"detail": "That token is wrong."}), REPLY])
        with pytest.raises(MaestroError, match="has to trust this machine"):
            client.file_meeting({})
        assert len(opener.requests) == 1

    def test_a_bad_request_carries_maestros_reason(self):
        detail = "No company called Nobody. One of: Maestro, Personal."
        client, _ = _client([_HTTPError(400, {"detail": detail})])
        with pytest.raises(MaestroError, match="No company called Nobody"):
            client.file_meeting({})

    def test_an_unreachable_server_says_so(self):
        client, _ = _client([urllib.error.URLError("Name or service not known")] * 3)
        with pytest.raises(MaestroError, match="Could not reach Maestro"):
            client.file_meeting({})

    def test_a_reply_that_is_not_json_is_an_error(self):
        client, _ = _client([b"<html>proxy</html>"])
        with pytest.raises(MaestroError, match="could not read"):
            client.file_meeting({})

    def test_the_token_never_appears_in_an_error(self):
        client, _ = _client([_HTTPError(401, {})], token="tok-123")
        with pytest.raises(MaestroError) as caught:
            client.file_meeting({})
        assert "tok-123" not in str(caught.value)


class TestErrorMessage:
    def test_401_says_maestro_must_trust_this_machine(self):
        assert "has to trust this machine" in error_message(401, b"{}")

    def test_404_says_nothing_files_meetings_there(self):
        assert "files meetings" in error_message(404, b"")

    def test_an_unreadable_body_falls_back_to_the_status(self):
        assert error_message(418, b"teapot") == "Maestro returned HTTP 418."


class TestParseFiled:
    def test_an_absolute_url_is_kept(self):
        assert parse_filed({"url": "https://x/items/1"}, "http://d").url == "https://x/items/1"

    def test_malformed_tasks_are_skipped(self):
        assert parse_filed({"tasks": ["junk", {"key": "A-1"}]}, "http://d").tasks[0].key == "A-1"


class TestDefaultClient:
    def test_it_uses_the_tailnet_address(self, monkeypatch):
        monkeypatch.delenv("MAESTRO_URL", raising=False)
        monkeypatch.delenv("MAESTRO_API_TOKEN", raising=False)
        client = default_client()
        assert client._base == DEFAULT_URL
        assert client._token is None

    def test_the_environment_overrides_it(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_URL", "http://127.0.0.1:8420")
        monkeypatch.setenv("MAESTRO_API_TOKEN", "t")
        client = default_client()
        assert (client._base, client._token) == ("http://127.0.0.1:8420", "t")
