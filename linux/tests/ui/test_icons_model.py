"""Tests for cross-theme icon selection."""

from meeting_recorder.ui.icons_model import TAG_ICONS, TAG_LIST_ICONS, pick_icon_name


def theme_with(*available):
    return lambda name: name in available


class TestPickIconName:
    def test_prefers_the_first_available_candidate(self):
        assert pick_icon_name(["a", "b"], theme_with("a", "b")) == "a"

    def test_falls_through_to_a_later_candidate(self):
        assert pick_icon_name(["a", "b"], theme_with("b")) == "b"

    def test_returns_the_last_candidate_when_nothing_is_available(self):
        # Better a known-missing icon than an empty button.
        assert pick_icon_name(["a", "b"], theme_with()) == "b"

    def test_empty_candidate_list_is_safe(self):
        assert pick_icon_name([], theme_with()) == ""


class TestTagIconCandidates:
    def test_breeze_gets_a_real_tag_icon(self):
        # Breeze has tag-symbolic; it must win over the star-shaped bookmark.
        assert pick_icon_name(TAG_ICONS, theme_with("tag-symbolic", "user-bookmarks-symbolic")) == (
            "tag-symbolic"
        )

    def test_adwaita_falls_back_to_bookmark_new(self):
        # Adwaita has no tag-symbolic.
        available = theme_with("bookmark-new-symbolic", "user-bookmarks-symbolic")
        assert pick_icon_name(TAG_ICONS, available) == "bookmark-new-symbolic"

    def test_tag_list_prefers_view_list(self):
        assert pick_icon_name(TAG_LIST_ICONS, theme_with("view-list-symbolic")) == (
            "view-list-symbolic"
        )
