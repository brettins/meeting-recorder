"""Posting a finished meeting's summary to a Maestro item so it is readable outside the app."""

from __future__ import annotations

import argparse
import json
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
    tags = meta.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    body = notes.strip()
    if len(body) > MAX_NOTES_CHARS:
        body = body[:MAX_NOTES_CHARS].rstrip() + "\n\n…notes continue in the folder."
    return f"**{title}** · {' · '.join(parts)}\n\n`{meeting_dir}`\n\n{body}\n"


def eligible(meeting_dir: Path, key: str) -> bool:
    """Whether a folder has notes not yet posted to *key* and a meeting.json that can record it."""
    if not (meeting_dir / "notes.md").exists():
        return False
    meta_file = meeting_dir / "meeting.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(meta, dict) or meta.get(POSTED_KEY) == key:
            return False
    return True


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
    if not force and not eligible(meeting_dir, key):
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
            timeout=15,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
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


def pending(root: Path, config: dict[str, Any]) -> list[Path]:
    """Meetings under *root* with notes that have not been posted to the configured key."""
    key = notes_key(config)
    waiting: list[Path] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            if eligible(folder, key):
                waiting.append(folder)
        except Exception as exc:
            logger.warning("Skipped %s: %s", folder.name, exc)
    return waiting


def backfill(root: Path, config: dict[str, Any], *, run_fn: RunFn = subprocess.run) -> list[Path]:
    """Post every meeting under *root* that has notes and was not posted, oldest first."""
    posted: list[Path] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            if post_notes(folder, config, run_fn=run_fn):
                posted.append(folder)
        except Exception as exc:
            logger.warning("Skipped %s: %s", folder.name, exc)
    return posted


def main(argv: list[str] | None = None) -> int:
    from ..config import settings

    parser = argparse.ArgumentParser(description="Post past meeting notes to Maestro.")
    parser.add_argument("--backfill", action="store_true", help="post every unposted meeting")
    parser.add_argument("--dry-run", action="store_true", help="list what --backfill would post")
    parser.add_argument("--key", help="Maestro item to use instead of maestro_notes_key")
    args = parser.parse_args(argv)
    if args.dry_run and not args.backfill:
        parser.error("--dry-run needs --backfill")
    config = settings.load()
    if args.key:
        config["maestro_notes_key"] = args.key.strip()
    if not notes_key(config):
        print("Set maestro_notes_key in the config, or pass --key.", file=sys.stderr)
        return 1
    if not args.backfill:
        parser.print_help()
        return 0
    root = Path(config.get("output_folder", "~/meetings")).expanduser()
    if args.dry_run:
        waiting = pending(root, config)
        print(f"{len(waiting)} meetings would be posted to {notes_key(config)}:")
        for folder in waiting:
            print(f"  {folder.name}")
        return 0
    posted = backfill(root, config)
    print(f"Posted {len(posted)} meetings to {notes_key(config)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
