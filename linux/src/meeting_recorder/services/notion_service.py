"""
Notion REST client for filing a finished meeting into a Notion page.

No SDK: the app already talks to Gemini and Ollama over urllib, and Notion's
surface here is four calls. The opener is injectable so every path is testable
without a token or a network.

The decision-shaped parts — reading a page summary out of a response, building
the multipart body, turning a failure into something a person can act on — are
module-level functions rather than methods, so they can be tested on their own.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.retry import retry_on_transient

logger = logging.getLogger(__name__)

API_ROOT = "https://api.notion.com/v1"

# Data sources — and so the query endpoint used here — arrived in this version.
NOTION_VERSION = "2025-09-03"

# Notion refuses a single-part upload above this; larger files need the
# multipart flow, which this client does not implement.
SINGLE_PART_LIMIT = 20 * 1024 * 1024

# What a free workspace accepts per file. Exceeding it fails at Notion with a
# message that does not mention the plan, so it is checked here instead.
FREE_PLAN_FILE_LIMIT = 5 * 1024 * 1024

_TIMEOUT = 60


class NotionError(Exception):
    """A Notion request failed in a way the user needs to hear about."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        # retry_on_transient reads this to tell a 500 apart from a 400.
        self.status_code = status_code


@dataclass(frozen=True)
class NotionPage:
    """One candidate page in the meetings database."""

    id: str
    title: str
    date: str | None
    url: str


def page_title(page: dict[str, Any]) -> str:
    """Read a page's title, whatever the title property happens to be called."""
    for value in (page.get("properties") or {}).values():
        if isinstance(value, dict) and value.get("type") == "title":
            parts = value.get("title") or []
            text = "".join(p.get("plain_text", "") for p in parts if isinstance(p, dict))
            return text.strip()
    return ""


def page_date(page: dict[str, Any]) -> str | None:
    """Read the first date property off a page, or None when it has none."""
    for value in (page.get("properties") or {}).values():
        if isinstance(value, dict) and value.get("type") == "date":
            date = value.get("date")
            if isinstance(date, dict) and date.get("start"):
                start = date["start"]
                return str(start)[:10]
    return None


def parse_page_summary(page: dict[str, Any]) -> NotionPage:
    """Reduce a page response to what the picker needs to show."""
    return NotionPage(
        id=str(page.get("id", "")),
        title=page_title(page) or "Untitled",
        date=page_date(page),
        url=str(page.get("url", "")),
    )


def files_property_names(schema: dict[str, Any]) -> list[str]:
    """Names of every ``files`` property in a data-source schema.

    Which one holds the audio is the user's choice, so the caller offers these
    rather than guessing at a name like "Recording".
    """
    props = schema.get("properties") or {}
    return sorted(
        name
        for name, value in props.items()
        if isinstance(value, dict) and value.get("type") == "files"
    )


def title_property_name(schema: dict[str, Any]) -> str:
    """Name of the data source's title property, which a new page must set."""
    props = schema.get("properties") or {}
    for name, value in props.items():
        if isinstance(value, dict) and value.get("type") == "title":
            return str(name)
    return "Name"


def upload_size_error(size: int) -> str | None:
    """Return why *size* cannot be uploaded, or None when it can be."""
    if size > SINGLE_PART_LIMIT:
        mib = size / (1024 * 1024)
        return (
            f"The recording is {mib:.1f} MiB. Notion accepts at most 20 MiB in one piece, "
            "and this app does not split larger uploads. File the notes without the audio, "
            "or record at a lower quality."
        )
    return None


def free_plan_hint(size: int) -> str | None:
    """Return a warning if *size* would only fit on a paid workspace."""
    if size > FREE_PLAN_FILE_LIMIT:
        mib = size / (1024 * 1024)
        return (
            f"The recording is {mib:.1f} MiB. A free Notion workspace accepts 5 MiB per file, "
            "so this will only attach on a paid plan."
        )
    return None


def error_message(status: int, body: bytes | str) -> str:
    """Turn a Notion error response into a sentence worth showing the user."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    detail = ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            detail = str(parsed.get("message") or "")
    except Exception:
        detail = ""
    if status == 401:
        return "Notion rejected the integration token. Check it in Settings → Notion."
    if status == 404:
        return (
            "Notion cannot see that database. Share it with the integration from the "
            "database's ••• menu → Connections."
        )
    if status == 403:
        return "The integration does not have permission for that page or database."
    return detail or f"Notion returned HTTP {status}."


def encode_multipart(filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body holding one file field named ``file``.

    Returns the body and the Content-Type header value carrying the boundary.
    """
    boundary = f"----MeetingRecorder{secrets.token_hex(16)}"
    safe = filename.replace('"', "")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


def guess_content_type(path: Path) -> str:
    """Best-effort MIME type for an upload, defaulting to a generic stream."""
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class NotionClient:
    """Talks to one Notion workspace using one integration token."""

    def __init__(
        self,
        token: str,
        http_open: Callable[..., Any] = urllib.request.urlopen,
        timeout: int = _TIMEOUT,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._open = http_open
        self._timeout = timeout
        self._sleep = sleep_fn

    # ------------------------------------------------------------------

    def resolve_data_source(self, database_id: str) -> str:
        """Return the data-source id backing *database_id*.

        Notion split databases into data sources; the query endpoint addresses
        the data source, while the id a user can copy out of a URL is the
        database. A database with several data sources resolves to the first,
        which is the one the UI shows by default.
        """
        body = self._request("GET", f"/databases/{database_id}")
        sources = body.get("data_sources") or []
        if sources and isinstance(sources[0], dict) and sources[0].get("id"):
            return str(sources[0]["id"])
        # Older workspaces answer without the list; the ids coincide there.
        return database_id

    def data_source_schema(self, data_source_id: str) -> dict[str, Any]:
        """Fetch a data source, whose ``properties`` describe the columns."""
        return self._request("GET", f"/data_sources/{data_source_id}")

    def recent_pages(self, data_source_id: str, limit: int = 50) -> list[NotionPage]:
        """List pages in a data source, most recently edited first."""
        payload: dict[str, Any] = {"page_size": min(limit, 100)}
        body = self._request("POST", f"/data_sources/{data_source_id}/query", payload)
        results = body.get("results") or []
        return [parse_page_summary(p) for p in results if isinstance(p, dict)]

    def create_page(
        self,
        data_source_id: str,
        title: str,
        title_property: str = "Name",
        date_property: str | None = None,
        date: str | None = None,
    ) -> NotionPage:
        """Create a page in the data source, titled *title*."""
        properties: dict[str, Any] = {
            title_property: {"title": [{"type": "text", "text": {"content": title}}]}
        }
        if date_property and date:
            properties[date_property] = {"date": {"start": date}}
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        return parse_page_summary(self._request("POST", "/pages", payload))

    def append_blocks(self, page_id: str, batches: list[list[dict[str, Any]]]) -> None:
        """Append each batch of blocks to *page_id*, in order."""
        for batch in batches:
            if batch:
                self._request("PATCH", f"/blocks/{page_id}/children", {"children": batch})

    def upload_file(self, path: Path) -> str:
        """Upload *path* in one piece and return the file-upload id."""
        data = path.read_bytes()
        blocked = upload_size_error(len(data))
        if blocked:
            raise NotionError(blocked)
        content_type = guess_content_type(path)
        created = self._request(
            "POST",
            "/file_uploads",
            {"mode": "single_part", "filename": path.name, "content_type": content_type},
        )
        upload_id = str(created.get("id") or "")
        if not upload_id:
            raise NotionError("Notion did not return an upload target for the recording.")
        # The create response carries the send URL; it is also derivable, so a
        # reply without it is not a reason to lose the recording.
        upload_url = str(created.get("upload_url") or "")
        if not upload_url:
            upload_url = f"{API_ROOT}/file_uploads/{upload_id}/send"
        body, header = encode_multipart(path.name, content_type, data)
        self._send(upload_url, "POST", body, {"Content-Type": header})
        return upload_id

    def attach_file(self, page_id: str, property_name: str, upload_id: str) -> None:
        """Point a ``files`` property at an already-uploaded file.

        Notion names the entry after the uploaded file itself, so no name is
        sent: a ``name`` alongside a ``file_upload`` is not part of the
        documented shape.
        """
        payload = {
            "properties": {
                property_name: {
                    "type": "files",
                    "files": [{"type": "file_upload", "file_upload": {"id": upload_id}}],
                }
            }
        }
        self._request("PATCH", f"/pages/{page_id}", payload)

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
        }

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        raw = self._send(f"{API_ROOT}{path}", method, data, headers)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NotionError(f"Notion sent a reply this app could not read: {exc}") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _send(self, url: str, method: str, data: bytes | None, headers: dict[str, str]) -> bytes:
        merged = dict(self._headers())
        merged.update(headers)
        request = urllib.request.Request(url, data=data, headers=merged, method=method)

        def call() -> bytes:
            try:
                with self._open(request, timeout=self._timeout) as response:
                    body: bytes = response.read()
                    return body
            except urllib.error.HTTPError as exc:
                raise NotionError(error_message(exc.code, exc.read()), exc.code) from exc
            except urllib.error.URLError as exc:
                raise NotionError(f"Could not reach Notion: {exc.reason}") from exc

        result: bytes = retry_on_transient(
            call, sleep_fn=self._sleep, description=f"Notion {method} {url}"
        )
        return result
