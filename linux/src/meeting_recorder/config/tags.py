"""
The tag registry: the set of tags a user has defined, and their colours.

Tags live in two places. The *registry* (name + colour) is global and lives in
``config.json``, because a tag's colour must be the same everywhere it appears.
The *assignment* (which tags a meeting has) is per-meeting and lives in that
meeting's ``meeting.json``, so it travels with the recording folder.

Colours are stored as palette *names*, never as literal colour values, so the
stylesheet stays the single place that decides what "blue" actually renders as.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Palette names, each backed by a .tag-<name> rule in assets/style.css. These
# map onto libadwaita's named palette so tags follow the system theme.
TAG_COLORS = ("blue", "green", "yellow", "orange", "red", "purple", "brown")

DEFAULT_TAG_COLOR = "blue"

# No tags ship by default — the set is entirely user-defined.
DEFAULT_TAGS: list[dict[str, str]] = []

MAX_TAG_NAME_LENGTH = 40


@dataclass(frozen=True)
class Tag:
    name: str
    color: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "color": self.color}


def normalize_color(color: Any) -> str:
    """Coerce *color* to a known palette name."""
    if isinstance(color, str) and color in TAG_COLORS:
        return color
    return DEFAULT_TAG_COLOR


def normalize_name(name: Any) -> str:
    """Trim and length-cap a tag name. Returns "" for anything unusable."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split())[:MAX_TAG_NAME_LENGTH]


def parse_tags(raw: Any) -> list[Tag]:
    """Read the registry out of config, dropping anything malformed.

    Tolerant by design: a hand-edited or older config must never stop the app
    from starting, so unusable entries are skipped rather than raising.
    """
    if not isinstance(raw, list):
        return []
    tags: list[Tag] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = normalize_name(entry.get("name"))
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        tags.append(Tag(name=name, color=normalize_color(entry.get("color"))))
    return tags


def serialize_tags(tags: list[Tag]) -> list[dict[str, str]]:
    return [t.to_dict() for t in tags]


def color_map(tags: list[Tag]) -> dict[str, str]:
    """Map tag name -> palette name, for rendering."""
    return {t.name: t.color for t in tags}


def add_tag(tags: list[Tag], name: str, color: str = DEFAULT_TAG_COLOR) -> list[Tag]:
    """Append a tag. A duplicate name (case-insensitive) is a no-op."""
    clean = normalize_name(name)
    if not clean:
        return list(tags)
    if any(t.name.casefold() == clean.casefold() for t in tags):
        return list(tags)
    return [*tags, Tag(name=clean, color=normalize_color(color))]


def remove_tag(tags: list[Tag], name: str) -> list[Tag]:
    return [t for t in tags if t.name != name]


def rename_tag(tags: list[Tag], old: str, new: str) -> list[Tag]:
    """Rename a tag in the registry. Collides silently rather than merging."""
    clean = normalize_name(new)
    if not clean:
        return list(tags)
    if any(t.name.casefold() == clean.casefold() and t.name != old for t in tags):
        return list(tags)
    return [Tag(name=clean, color=t.color) if t.name == old else t for t in tags]


def recolor_tag(tags: list[Tag], name: str, color: str) -> list[Tag]:
    return [Tag(name=t.name, color=normalize_color(color)) if t.name == name else t for t in tags]


def parse_meeting_tags(raw: Any) -> list[str]:
    """Read a meeting's assigned tag names out of its metadata."""
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        name = normalize_name(entry)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def known_tags_only(names: list[str], registry: list[Tag]) -> list[str]:
    """Drop assignments whose tag has been deleted from the registry."""
    known = {t.name.casefold() for t in registry}
    return [n for n in names if n.casefold() in known]


def rename_in_assignments(names: list[str], old: str, new: str) -> list[str]:
    """Apply a registry rename to one meeting's assigned tag names.

    Renaming a tag has to reach the meetings that carry it, or their assignment
    is orphaned: the old name stays on disk, no longer matches any registry
    entry, and stops rendering — while still being written back on every save.
    """
    clean = normalize_name(new)
    if not clean:
        return list(names)
    renamed = [clean if n.casefold() == old.casefold() else n for n in names]
    return parse_meeting_tags(renamed)


def remove_from_assignments(names: list[str], removed: str) -> list[str]:
    """Drop a deleted registry tag from one meeting's assigned tag names."""
    return [n for n in names if n.casefold() != removed.casefold()]


def matches_filter(meeting_tags: list[str], selected: str | None) -> bool:
    """True if a meeting should be shown under the active tag filter.

    ``None`` means "All meetings"; the sentinel ``UNTAGGED`` matches meetings
    with no tags at all.
    """
    if selected is None:
        return True
    if selected == UNTAGGED:
        return not meeting_tags
    return any(t.casefold() == selected.casefold() for t in meeting_tags)


UNTAGGED = "\x00untagged"
