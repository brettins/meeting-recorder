"""
Pure icon-selection policy (gi-free, so it is unit-testable without PyGObject).

Icon names are not portable between themes: "tag-symbolic" exists in Breeze but
not Adwaita, and Breeze draws "user-bookmarks-symbolic" as a star, which reads
as "favourite" rather than "tag". The policy is to name candidates best-first
and take the first one the running theme actually has.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

# Best-first candidates. The final entry is the guaranteed fallback.
TAG_ICONS = ("tag-symbolic", "bookmark-new-symbolic", "user-bookmarks-symbolic")
TAG_LIST_ICONS = ("view-list-symbolic", "view-list-bullet-symbolic")


def pick_icon_name(names: Sequence[str], has_icon: Callable[[str], bool]) -> str:
    """First name *has_icon* accepts, else the last candidate."""
    if not names:
        return ""
    for name in names:
        if has_icon(name):
            return name
    return names[-1]
