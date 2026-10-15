"""When the window may overwrite its title and tag editors with what the daemon reports."""

from __future__ import annotations


def remote_title_to_show(entry_text: str, remote_title: str, unacked_edits: int) -> str | None:
    """The text to put in the title entry, or None to leave what the user has typed alone.

    While an edit is still unacknowledged the daemon's snapshots describe an older entry.
    The daemon strips the title, so a trailing space the user just typed is not a difference.
    """
    if unacked_edits > 0:
        return None
    if remote_title == entry_text.strip():
        return None
    return remote_title


def remote_tags_to_show(
    local_tags: list[str], remote_tags: list[str], unacked_edits: int
) -> list[str] | None:
    """The tags to show, or None while the user's own change is still on its way."""
    if unacked_edits > 0 or remote_tags == local_tags:
        return None
    return list(remote_tags)
