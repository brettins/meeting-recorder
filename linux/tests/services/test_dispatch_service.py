"""Tests for the Dispatch client, with a fake opener. Nothing reaches a real server."""

import json
import urllib.error

import pytest

from meeting_recorder.services.dispatch_service import (
    DispatchClient,
    DispatchError,
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
        super().__init__("http://dispatch", code, "err", {}, None)
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


def _client(replies):
    opener = FakeOpener(replies)
    client = DispatchClient(
        "http://dispatch:8000/", "tok-123", http_open=opener, sleep_fn=lambda _s: None
    )
    return client, opener


class TestFiling:
    def test_it_posts_json_to_the_meetings_endpoint(self):
        client, opener = _client([REPLY])
        client.file_meeting({"meeting_id": "m", "title": "Standup"})
        request = opener.requests[0]
        assert request.full_url == "http://dispatch:8000/api/meetings"
        assert request.get_method() == "POST"
        assert json.loads(request.data) == {"meeting_id": "m", "title": "Standup"}

    def test_the_token_is_a_bearer_header(self):
        client, opener = _client([REPLY])
        client.file_meeting({})
        assert opener.requests[0].get_header("Authorization") == "Bearer tok-123"

    def test_the_reply_is_read(self):
        client, _ = _client([REPLY])
        filed = client.file_meeting({})
        assert filed.key == "SNG-1"
        assert filed.url == "http://dispatch:8000/items/650"
        assert [t.key for t in filed.tasks] == ["SNG-2", "SNG-3"]
        assert filed.new_tasks == 1

    def test_a_server_error_is_retried(self):
        client, opener = _client([_HTTPError(502, {}), REPLY])
        assert client.file_meeting({}).key == "SNG-1"
        assert len(opener.requests) == 2

    def test_a_refused_token_is_not_retried(self):
        client, opener = _client([_HTTPError(401, {"detail": "That token is wrong."}), REPLY])
        with pytest.raises(DispatchError, match="Settings → Dispatch"):
            client.file_meeting({})
        assert len(opener.requests) == 1

    def test_a_bad_request_carries_dispatchs_reason(self):
        detail = "No company called Nobody. One of: Dispatch, Personal."
        client, _ = _client([_HTTPError(400, {"detail": detail})])
        with pytest.raises(DispatchError, match="No company called Nobody"):
            client.file_meeting({})

    def test_an_unreachable_server_says_so(self):
        client, _ = _client([urllib.error.URLError("Name or service not known")] * 3)
        with pytest.raises(DispatchError, match="Could not reach Dispatch"):
            client.file_meeting({})

    def test_a_reply_that_is_not_json_is_an_error(self):
        client, _ = _client([b"<html>proxy</html>"])
        with pytest.raises(DispatchError, match="could not read"):
            client.file_meeting({})

    def test_the_token_never_appears_in_an_error(self):
        client, _ = _client([_HTTPError(401, {})])
        with pytest.raises(DispatchError) as caught:
            client.file_meeting({})
        assert "tok-123" not in str(caught.value)


class TestErrorMessage:
    def test_401_points_at_settings(self):
        assert "Settings → Dispatch" in error_message(401, b"{}")

    def test_503_says_the_server_has_no_token(self):
        assert "DISPATCH_API_TOKEN" in error_message(503, b"{}")

    def test_404_says_the_url_is_wrong(self):
        assert "URL" in error_message(404, b"")

    def test_an_unreadable_body_falls_back_to_the_status(self):
        assert error_message(418, b"teapot") == "Dispatch returned HTTP 418."


class TestParseFiled:
    def test_an_absolute_url_is_kept(self):
        assert parse_filed({"url": "https://x/items/1"}, "http://d").url == "https://x/items/1"

    def test_malformed_tasks_are_skipped(self):
        assert parse_filed({"tasks": ["junk", {"key": "A-1"}]}, "http://d").tasks[0].key == "A-1"
