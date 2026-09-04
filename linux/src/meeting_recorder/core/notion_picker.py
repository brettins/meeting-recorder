"""
Choosing which Notion page a meeting is filed into.

Filing almost always attaches to a meeting page that already exists in Notion,
so the picker's job is to get that page in front of the user without making
them scroll: match on the day the meeting was recorded, and let a search box
handle the rest. Creating a page is the exception, and is only offered
explicitly.

Pure and gi-free — ``ui/notion_dialog.py`` renders these decisions and holds no
policy of its own.
"""

from __future__ import annotations

from datetime import datetime

from ..services.notion_service import NotionPage


def normalise(text: str) -> str:
    """Lowercase and collapse whitespace, so search ignores both."""
    return " ".join(text.lower().split())


def filter_pages(pages: list[NotionPage], query: str) -> list[NotionPage]:
    """Pages whose title or date contains every word of *query*.

    Every word must match somewhere, so "sing marketing" narrows rather than
    widens — typing more can only ever shorten the list, which is what a person
    scanning a list expects.
    """
    words = normalise(query).split()
    if not words:
        return list(pages)
    matches = []
    for page in pages:
        haystack = normalise(f"{page.title} {page.date or ''}")
        if all(word in haystack for word in words):
            matches.append(page)
    return matches


def suggest_page(pages: list[NotionPage], recorded: datetime) -> NotionPage | None:
    """The page most likely to be the meeting that was just recorded.

    A page dated the same day wins. Nothing else is guessed at: a near miss
    that quietly files a meeting under the wrong page is worse than making the
    user pick.
    """
    day = recorded.strftime("%Y-%m-%d")
    for page in pages:
        if page.date == day:
            return page
    return None


def page_subtitle(page: NotionPage) -> str:
    """The dim line under a candidate page's title."""
    return page.date or "No date"


def default_new_title(meeting_title: str | None, recorded: datetime) -> str:
    """The title offered when creating a page instead of attaching to one."""
    stem = (meeting_title or "").strip()
    if stem:
        return stem
    return f"Meeting {recorded.strftime('%Y-%m-%d %H:%M')}"


def filing_summary(
    *,
    notes_written: bool,
    transcript_written: bool,
    audio_attached: bool,
    page_title: str,
) -> str:
    """One line saying what landed on the page."""
    parts = []
    if notes_written:
        parts.append("notes")
    if transcript_written:
        parts.append("transcript")
    if audio_attached:
        parts.append("recording")
    if not parts:
        return f"Nothing was filed to “{page_title}”."
    if len(parts) == 1:
        listed = parts[0]
    else:
        listed = f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Filed {listed} to “{page_title}”."
