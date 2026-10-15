"""Saved transcripts of finished parts, so a retried job skips the parts already paid for."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_AGE_SECONDS = 7 * 24 * 3600


def default_directory() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "meeting-recorder" / "parts"


class PartCache:
    """Transcripts keyed by the audio and the request, kept until the job writes its results."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or default_directory()

    @staticmethod
    def key(audio_path: Path, *request: str) -> str:
        digest = hashlib.sha256()
        with audio_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        for item in request:
            digest.update(b"\0" + item.encode("utf-8"))
        return digest.hexdigest()

    def get(self, key: str) -> str | None:
        try:
            text = (self._directory / f"{key}.md").read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        return text or None

    def put(self, key: str, text: str) -> None:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            temporary = self._directory / f"{key}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temporary, self._directory / f"{key}.md")
        except OSError as exc:
            logger.warning("Could not save a part transcript: %s", exc)

    def discard(self, keys: Iterable[str]) -> None:
        for key in keys:
            try:
                (self._directory / f"{key}.md").unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove a saved part transcript: %s", exc)

    def prune(self, max_age_seconds: float = MAX_AGE_SECONDS) -> None:
        """Remove entries left by jobs that never finished."""
        cutoff = time.time() - max_age_seconds
        try:
            entries = list(self._directory.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
