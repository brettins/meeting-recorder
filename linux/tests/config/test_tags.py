"""Tests for the tag registry and per-meeting tag assignment."""

from meeting_recorder.config.tags import (
    DEFAULT_TAG_COLOR,
    MAX_TAG_NAME_LENGTH,
    TAG_COLORS,
    UNTAGGED,
    Tag,
    add_tag,
    color_map,
    known_tags_only,
    matches_filter,
    normalize_color,
    normalize_name,
    parse_meeting_tags,
    parse_tags,
    recolor_tag,
    remove_tag,
    rename_tag,
    serialize_tags,
)


class TestNormalize:
    def test_unknown_colour_falls_back_to_the_default(self):
        assert normalize_color("chartreuse") == DEFAULT_TAG_COLOR
        assert normalize_color(None) == DEFAULT_TAG_COLOR
        assert normalize_color(42) == DEFAULT_TAG_COLOR

    def test_known_colours_pass_through(self):
        for colour in TAG_COLORS:
            assert normalize_color(colour) == colour

    def test_name_whitespace_is_collapsed(self):
        assert normalize_name("  Story   City  ") == "Story City"

    def test_name_is_length_capped(self):
        assert len(normalize_name("x" * 200)) == MAX_TAG_NAME_LENGTH

    def test_non_string_name_is_rejected(self):
        assert normalize_name(None) == ""
        assert normalize_name(7) == ""


class TestParseTags:
    def test_reads_a_well_formed_registry(self):
        tags = parse_tags([{"name": "Story City", "color": "blue"}])
        assert tags == [Tag(name="Story City", color="blue")]

    def test_missing_registry_is_empty(self):
        # Existing configs predate tags entirely and must still load.
        assert parse_tags(None) == []
        assert parse_tags("nonsense") == []

    def test_malformed_entries_are_skipped_not_fatal(self):
        tags = parse_tags([{"name": ""}, "junk", 5, {"name": "SING!"}])
        assert tags == [Tag(name="SING!", color=DEFAULT_TAG_COLOR)]

    def test_duplicate_names_are_dropped_case_insensitively(self):
        tags = parse_tags([{"name": "pagepack"}, {"name": "PagePack"}])
        assert [t.name for t in tags] == ["pagepack"]

    def test_round_trips_through_serialize(self):
        raw = [{"name": "Story City", "color": "blue"}]
        assert serialize_tags(parse_tags(raw)) == raw


class TestRegistryEdits:
    def test_add(self):
        tags = add_tag([], "SING!", "purple")
        assert tags == [Tag(name="SING!", color="purple")]

    def test_add_rejects_duplicates_case_insensitively(self):
        tags = add_tag([Tag("SING!", "purple")], "sing!", "red")
        assert len(tags) == 1

    def test_add_ignores_a_blank_name(self):
        assert add_tag([], "   ") == []

    def test_add_normalizes_an_unknown_colour(self):
        assert add_tag([], "x", "not-a-colour")[0].color == DEFAULT_TAG_COLOR

    def test_remove(self):
        assert remove_tag([Tag("SING!", "purple")], "SING!") == []

    def test_rename_keeps_the_colour(self):
        out = rename_tag([Tag("old", "red")], "old", "new")
        assert out == [Tag(name="new", color="red")]

    def test_rename_onto_an_existing_name_is_a_no_op(self):
        tags = [Tag("a", "red"), Tag("b", "blue")]
        assert rename_tag(tags, "a", "b") == tags

    def test_rename_to_blank_is_a_no_op(self):
        tags = [Tag("a", "red")]
        assert rename_tag(tags, "a", "  ") == tags

    def test_recolor(self):
        assert recolor_tag([Tag("a", "red")], "a", "green") == [Tag("a", "green")]

    def test_color_map(self):
        assert color_map([Tag("a", "red"), Tag("b", "blue")]) == {"a": "red", "b": "blue"}


class TestMeetingTags:
    def test_parses_a_list_of_names(self):
        assert parse_meeting_tags(["Story City", "SING!"]) == ["Story City", "SING!"]

    def test_missing_or_malformed_is_empty(self):
        assert parse_meeting_tags(None) == []
        assert parse_meeting_tags("Story City") == []

    def test_deduplicates_case_insensitively_keeping_first(self):
        assert parse_meeting_tags(["SING!", "sing!"]) == ["SING!"]

    def test_drops_blanks(self):
        assert parse_meeting_tags(["", "  ", "SING!"]) == ["SING!"]

    def test_known_tags_only_drops_deleted_tags(self):
        registry = [Tag("SING!", "purple")]
        assert known_tags_only(["SING!", "Deleted"], registry) == ["SING!"]


class TestMatchesFilter:
    def test_none_shows_everything(self):
        assert matches_filter([], None)
        assert matches_filter(["SING!"], None)

    def test_untagged_matches_only_meetings_with_no_tags(self):
        assert matches_filter([], UNTAGGED)
        assert not matches_filter(["SING!"], UNTAGGED)

    def test_named_filter_matches_case_insensitively(self):
        assert matches_filter(["Story City"], "story city")
        assert not matches_filter(["Story City"], "SING!")
