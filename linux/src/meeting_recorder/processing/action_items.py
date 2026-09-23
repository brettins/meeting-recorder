"""Action lines pulled out of a meeting's notes.md, to become draft tasks."""

from __future__ import annotations

import re

HEADINGS = (
    "action items",
    "action item",
    "action points",
    "actions",
    "next steps",
    "follow-ups",
    "follow ups",
    "follow-up",
    "to-dos",
    "todos",
    "to do",
)

MAX_LENGTH = 200

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BOLD_HEADING = re.compile(r"^(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_CHECKBOX = re.compile(r"^\[([ xX])\]\s*(.*)$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")


def _heading(line: str) -> tuple[int, str] | None:
    """(level, text) when *line* is a heading; a bold-only line counts as level 7."""
    stripped = line.strip()
    atx = _ATX.match(stripped)
    if atx:
        return len(atx.group(1)), atx.group(2)
    bold = _BOLD_HEADING.match(stripped)
    if bold:
        return 7, bold.group(1)
    if stripped.endswith(":") and len(stripped) <= 60 and not _ITEM.match(line):
        return 7, stripped
    return None


def _is_action_heading(text: str) -> bool:
    words = clean(text).lower().rstrip(":").strip()
    return words in HEADINGS


def clean(text: str) -> str:
    """Markdown emphasis, code ticks and link targets stripped; spaces collapsed."""
    text = _LINK.sub(r"\1", text)
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split())


def extract_actions(notes: str) -> list[str]:
    """Open action lines: items under an action heading, and unchecked boxes anywhere.

    Nested items are left out; a ticked box is already done.
    """
    found: list[str] = []
    section_level: int | None = None
    item_indent: int | None = None

    for line in notes.splitlines():
        heading = _heading(line)
        if heading is not None:
            level, text = heading
            if _is_action_heading(text):
                section_level, item_indent = level, None
            elif section_level is not None and level <= section_level:
                section_level = None
            continue

        item = _ITEM.match(line)
        if item is None:
            continue
        indent = len(item.group(1).expandtabs(4))
        body = item.group(2).strip()
        box = _CHECKBOX.match(body)
        if box is not None:
            if box.group(1) != " ":
                continue
            body = box.group(2)
        elif section_level is None:
            continue

        if section_level is not None:
            if item_indent is None:
                item_indent = indent
            if indent > item_indent:
                continue
        elif indent > 0:
            continue

        text = clean(body)[:MAX_LENGTH].strip()
        if text and text.lower() not in (seen.lower() for seen in found):
            found.append(text)
    return found
