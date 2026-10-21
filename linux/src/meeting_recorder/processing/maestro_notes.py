"""Posting a finished meeting's summary to a Maestro item so it is readable outside the app."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.auto_stop import format_duration
from ..utils.meeting_scanner import read_metadata, write_metadata

logger = logging.getLogger(__name__)

MAX_NOTES_CHARS = 30000
POSTED_KEY = "maestro_posted"

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


def notes_key(config: dict[str, Any]) -> str:
    return str(config.get("maestro_notes_key") or "").strip()


def format_comment(meeting_dir: Path, notes: str) -> str:
    """The comment body: a header line, the folder, then the notes."""
    meta = read_metadata(meeting_dir)
    title = meta.get("title") or meeting_dir.name
    parts = [meeting_dir.name[:16].replace("_", " ")]
    duration = meta.get("duration_seconds")
    if isinstance(duration, int | float) and duration > 0:
        parts.append(format_duration(int(duration)))
    parts.extend(meta.get("tags") or [])
    body = notes.strip()
    if len(body) > MAX_NOTES_CHARS:
        body = body[:MAX_NOTES_CHARS].rstrip() + "\n\n…notes continue in the folder."
    return f"**{title}** · {' · '.join(parts)}\n\n`{meeting_dir}`\n\n{body}\n"


def post_notes(
    meeting_dir: Path,
    config: dict[str, Any],
    *,
    run_fn: RunFn = subprocess.run,
    force: bool = False,
) -> bool:
    """Comment the meeting's notes on the configured Maestro item; True once posted."""
    key = notes_key(config)
    notes_file = meeting_dir / "notes.md"
    if not key or not notes_file.exists():
        return False
    if not force and read_metadata(meeting_dir).get(POSTED_KEY) == key:
        return False
    if shutil.which("maestro") is None:
        logger.warning("maestro command not found; notes for %s not posted", meeting_dir.name)
        return False
    try:
        result = run_fn(
            ["maestro", "comment", key, "-"],
            input=format_comment(meeting_dir, notes_file.read_text(encoding="utf-8")),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not post notes for %s: %s", meeting_dir.name, exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "Maestro refused notes for %s: %s", meeting_dir.name, (result.stderr or "").strip()
        )
        return False
    try:
        write_metadata(meeting_dir, {POSTED_KEY: key})
    except OSError as exc:
        logger.warning("Posted notes for %s but could not record it: %s", meeting_dir.name, exc)
    logger.info("Posted notes for %s to %s", meeting_dir.name, key)
    return True


def backfill(root: Path, config: dict[str, Any], *, run_fn: RunFn = subprocess.run) -> list[Path]:
    """Post every meeting under *root* that has notes and was not posted, oldest first."""
    posted: list[Path] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if post_notes(folder, config, run_fn=run_fn):
            posted.append(folder)
    return posted


def main(argv: list[str] | None = None) -> int:
    from ..config import settings

    parser = argparse.ArgumentParser(description="Post past meeting notes to Maestro.")
    parser.add_argument("--backfill", action="store_true", help="post every unposted meeting")
    args = parser.parse_args(argv)
    config = settings.load()
    if not notes_key(config):
        print("Set maestro_notes_key in the config first.", file=sys.stderr)
        return 1
    if not args.backfill:
        parser.print_help()
        return 0
    root = Path(config.get("output_folder", "~/meetings")).expanduser()
    posted = backfill(root, config)
    print(f"Posted {len(posted)} meetings to {notes_key(config)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
