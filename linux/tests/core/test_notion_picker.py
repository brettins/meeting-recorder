"""Tests for the Notion page-picker policy."""

from datetime import datetime

from meeting_recorder.core.notion_picker import (
    default_new_title,
    filing_summary,
    filter_pages,
    normalise,
    page_subtitle,
    suggest_page,
)
from meeting_recorder.services.notion_service import NotionPage


def page(title, date=None, ident="p"):
    return NotionPage(id=ident, title=title, date=date, url=f"https://n/{ident}")


PAGES = [
    page("Weekly Stand-Up", "2026-09-03", "p1"),
    page("SING! Marketing meeting", "2026-09-03", "p2"),
    page("Advisory Board", "2026-08-27", "p3"),
    page("Untitled", None, "p4"),
]


class TestNormalise:
    def test_case_and_spacing_are_flattened(self):
        assert normalise("  SING!   Marketing ") == "sing! marketing"

    def test_empty_stays_empty(self):
        assert normalise("   ") == ""


class TestFilterPages:
    def test_an_empty_query_keeps_everything(self):
        assert filter_pages(PAGES, "") == PAGES
        assert filter_pages(PAGES, "   ") == PAGES

    def test_a_word_matches_the_title_case_insensitively(self):
        assert [p.id for p in filter_pages(PAGES, "marketing")] == ["p2"]

    def test_every_word_must_match(self):
        assert [p.id for p in filter_pages(PAGES, "sing marketing")] == ["p2"]
        assert filter_pages(PAGES, "sing advisory") == []

    def test_word_order_does_not_matter(self):
        assert [p.id for p in filter_pages(PAGES, "marketing sing")] == ["p2"]

    def test_the_date_is_searchable(self):
        assert [p.id for p in filter_pages(PAGES, "2026-08")] == ["p3"]

    def test_a_page_without_a_date_is_not_a_crash(self):
        assert [p.id for p in filter_pages(PAGES, "untitled")] == ["p4"]

    def test_no_match_yields_nothing(self):
        assert filter_pages(PAGES, "zzz") == []


class TestSuggestPage:
    def test_a_page_from_the_same_day_is_suggested(self):
        suggested = suggest_page(PAGES, datetime(2026, 9, 3, 13, 38))
        assert suggested is not None
        assert suggested.id == "p1"

    def test_the_first_match_wins_when_a_day_has_several(self):
        pages = [page("B", "2026-09-03", "b"), page("A", "2026-09-03", "a")]
        suggested = suggest_page(pages, datetime(2026, 9, 3))
        assert suggested is not None
        assert suggested.id == "b"

    def test_a_different_day_suggests_nothing(self):
        assert suggest_page(PAGES, datetime(2026, 1, 1)) is None

    def test_an_empty_database_suggests_nothing(self):
        assert suggest_page([], datetime(2026, 9, 3)) is None


class TestLabels:
    def test_subtitle_is_the_date(self):
        assert page_subtitle(page("X", "2026-09-03")) == "2026-09-03"

    def test_subtitle_says_so_when_there_is_no_date(self):
        assert page_subtitle(page("X")) == "No date"

    def test_a_titled_meeting_offers_its_own_title(self):
        assert default_new_title("SING! marketing", datetime(2026, 9, 3, 13, 38)) == (
            "SING! marketing"
        )

    def test_an_untitled_meeting_offers_its_timestamp(self):
        assert default_new_title(None, datetime(2026, 9, 3, 13, 38)) == "Meeting 2026-09-03 13:38"

    def test_a_blank_title_is_treated_as_none(self):
        assert default_new_title("   ", datetime(2026, 9, 3, 13, 38)).startswith("Meeting ")


class TestFilingSummary:
    def test_all_three_are_listed(self):
        summary = filing_summary(
            notes_written=True,
            transcript_written=True,
            audio_attached=True,
            page_title="SING!",
        )
        assert summary == "Filed notes, transcript and recording to “SING!”."

    def test_two_are_joined_with_and(self):
        summary = filing_summary(
            notes_written=True,
            transcript_written=True,
            audio_attached=False,
            page_title="SING!",
        )
        assert summary == "Filed notes and transcript to “SING!”."

    def test_one_stands_alone(self):
        summary = filing_summary(
            notes_written=True,
            transcript_written=False,
            audio_attached=False,
            page_title="SING!",
        )
        assert summary == "Filed notes to “SING!”."

    def test_nothing_filed_says_so(self):
        summary = filing_summary(
            notes_written=False,
            transcript_written=False,
            audio_attached=False,
            page_title="SING!",
        )
        assert summary == "Nothing was filed to “SING!”."
