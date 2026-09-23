"""Dispatch REST client: files a finished meeting through ``POST /api/meetings``."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.retry import retry_on_transient

logger = logging.getLogger(__name__)

MEETINGS_PATH = "/api/meetings"

_TIMEOUT = 30


class DispatchError(Exception):
    """A Dispatch request failed in a way the user needs to hear about."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class FiledTask:
    key: str
    title: str
    created: bool


@dataclass(frozen=True)
class FiledMeeting:
    key: str
    url: str
    created: bool
    tasks: list[FiledTask] = field(default_factory=list)

    @property
    def new_tasks(self) -> int:
        return sum(1 for task in self.tasks if task.created)


def error_message(status: int, body: bytes | str) -> str:
    """A Dispatch error response as a sentence worth showing."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    detail = ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
            detail = parsed["detail"]
    except ValueError:
        detail = ""
    if status == 401:
        return "Dispatch refused the API token. Check it in Settings → Dispatch."
    if status == 503:
        return (
            "Dispatch has no API token set, so it accepts no filing. Set DISPATCH_API_TOKEN there."
        )
    if status == 404:
        return "Nothing at that address files meetings. Check the URL in Settings → Dispatch."
    return detail or f"Dispatch returned HTTP {status}."


def parse_filed(body: dict[str, Any], base_url: str) -> FiledMeeting:
    """Read Dispatch's reply into what the row's notice reports."""
    tasks = [
        FiledTask(
            key=str(task.get("key") or ""),
            title=str(task.get("title") or ""),
            created=bool(task.get("created")),
        )
        for task in body.get("tasks") or []
        if isinstance(task, dict)
    ]
    path = str(body.get("url") or "")
    return FiledMeeting(
        key=str(body.get("key") or ""),
        url=f"{base_url}{path}" if path.startswith("/") else path,
        created=bool(body.get("created")),
        tasks=tasks,
    )


class DispatchClient:
    """Talks to one Dispatch server with one API token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        http_open: Callable[..., Any] = urllib.request.urlopen,
        timeout: int = _TIMEOUT,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._open = http_open
        self._timeout = timeout
        self._sleep = sleep_fn

    def file_meeting(self, payload: dict[str, Any]) -> FiledMeeting:
        """Send one meeting. Safe to repeat: Dispatch updates on the same meeting_id."""
        body = self._post(MEETINGS_PATH, payload)
        return parse_filed(body, self._base)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        def call() -> bytes:
            try:
                with self._open(request, timeout=self._timeout) as response:
                    raw: bytes = response.read()
                    return raw
            except urllib.error.HTTPError as exc:
                raise DispatchError(error_message(exc.code, exc.read()), exc.code) from exc
            except urllib.error.URLError as exc:
                raise DispatchError(f"Could not reach Dispatch: {exc.reason}") from exc

        raw = retry_on_transient(call, sleep_fn=self._sleep, description=f"Dispatch POST {url}")
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise DispatchError("Dispatch sent a reply this app could not read.") from exc
        if not isinstance(parsed, dict):
            raise DispatchError("Dispatch sent a reply this app could not read.")
        return parsed
