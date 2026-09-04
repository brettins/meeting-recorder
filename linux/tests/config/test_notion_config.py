"""Tests for the Notion workspace registry and its id/URL handling."""

from meeting_recorder.config.notion_config import (
    CONFIG_KEY,
    NotionWorkspace,
    add_workspace,
    dump_workspaces,
    extract_database_id,
    find_workspace,
    keyring_label,
    keyring_purpose,
    load_workspaces,
    normalise_workspace,
    remove_workspace,
    update_workspace,
    workspace_id_for,
)

DB = "77ca8ecc751a45778bec0d2d894d9d6d"


def _ws(**kw):
    base = {"id": "sing", "name": "SING!", "database_id": DB, "files_property": "Recording"}
    base.update(kw)
    return NotionWorkspace(**base)


class TestExtractDatabaseId:
    def test_bare_id_passes_through(self):
        assert extract_database_id(DB) == DB

    def test_hyphenated_uuid_is_flattened(self):
        assert extract_database_id("77ca8ecc-751a-4577-8bec-0d2d894d9d6d") == DB

    def test_titled_url_yields_the_trailing_id(self):
        url = f"https://www.notion.so/myteam/Meetings-Events-{DB}"
        assert extract_database_id(url) == DB

    def test_view_parameter_is_not_mistaken_for_the_database(self):
        url = f"https://www.notion.so/myteam/Meetings-{DB}?v=1111111111111111aaaaaaaaaaaaaaaa"
        assert extract_database_id(url) == DB

    def test_trailing_slash_is_tolerated(self):
        assert extract_database_id(f"https://app.notion.com/p/{DB}/") == DB

    def test_surrounding_whitespace_is_stripped(self):
        assert extract_database_id(f"  {DB}\n") == DB

    def test_empty_input_is_empty(self):
        assert extract_database_id("   ") == ""

    def test_text_without_an_id_comes_back_unchanged(self):
        assert extract_database_id("not a link") == "not a link"


class TestWorkspaceId:
    def test_id_is_slugified_from_the_name(self):
        assert workspace_id_for("SING! Marketing") == "sing-marketing"

    def test_collision_gets_a_suffix(self):
        assert workspace_id_for("SING!", ["sing"]) == "sing-2"

    def test_repeated_collisions_keep_counting(self):
        assert workspace_id_for("SING!", ["sing", "sing-2"]) == "sing-3"

    def test_a_name_with_no_usable_characters_still_yields_an_id(self):
        assert workspace_id_for("!!!") == "workspace"

    def test_purpose_and_label_carry_the_identity(self):
        assert keyring_purpose("sing") == "notion-token-sing"
        assert "SING!" in keyring_label("SING!")


class TestNormalise:
    def test_a_complete_entry_survives(self):
        raw = {"id": "sing", "name": "SING!", "database_id": DB, "files_property": "Recording"}
        assert normalise_workspace(raw) == _ws()

    def test_a_pasted_url_is_reduced_to_an_id(self):
        raw = {"name": "SING!", "database_id": f"https://www.notion.so/x/Meetings-{DB}"}
        workspace = normalise_workspace(raw)
        assert workspace is not None
        assert workspace.database_id == DB

    def test_a_missing_id_is_derived_from_the_name(self):
        workspace = normalise_workspace({"name": "SING!", "database_id": DB})
        assert workspace is not None
        assert workspace.id == "sing"

    def test_an_entry_without_a_name_is_dropped(self):
        assert normalise_workspace({"database_id": DB}) is None

    def test_an_entry_without_a_database_is_dropped(self):
        assert normalise_workspace({"name": "SING!"}) is None

    def test_a_non_dictionary_entry_is_dropped(self):
        assert normalise_workspace("nonsense") is None
        assert normalise_workspace(None) is None


class TestLoadAndDump:
    def test_workspaces_load_in_order(self):
        config = {
            CONFIG_KEY: [
                {"id": "sing", "name": "SING!", "database_id": DB},
                {"id": "pagepack", "name": "pagepack", "database_id": DB},
            ]
        }
        assert [w.id for w in load_workspaces(config)] == ["sing", "pagepack"]

    def test_malformed_entries_are_skipped_not_fatal(self):
        config = {CONFIG_KEY: ["junk", {"name": "SING!", "database_id": DB}, {}]}
        assert [w.name for w in load_workspaces(config)] == ["SING!"]

    def test_a_missing_key_yields_nothing(self):
        assert load_workspaces({}) == []

    def test_a_non_list_value_yields_nothing(self):
        assert load_workspaces({CONFIG_KEY: "sing"}) == []

    def test_duplicate_ids_keep_only_the_first(self):
        config = {
            CONFIG_KEY: [
                {"id": "sing", "name": "SING!", "database_id": DB},
                {"id": "sing", "name": "Other", "database_id": DB},
            ]
        }
        assert [w.name for w in load_workspaces(config)] == ["SING!"]

    def test_dump_round_trips_through_load(self):
        workspaces = [_ws()]
        config = {CONFIG_KEY: dump_workspaces(workspaces)}
        assert load_workspaces(config) == workspaces


class TestMutations:
    def test_add_appends_with_a_fresh_id(self):
        result = add_workspace([_ws()], "SING!", DB)
        assert [w.id for w in result] == ["sing", "sing-2"]

    def test_add_accepts_a_url(self):
        result = add_workspace([], "SING!", f"https://www.notion.so/x/M-{DB}")
        assert result[0].database_id == DB

    def test_update_changes_the_name_but_keeps_the_id(self):
        result = update_workspace([_ws()], "sing", name="SING! Marketing")
        assert result[0].id == "sing"
        assert result[0].name == "SING! Marketing"

    def test_update_changes_the_files_property(self):
        result = update_workspace([_ws()], "sing", files_property="Audio")
        assert result[0].files_property == "Audio"

    def test_update_clears_the_files_property_when_asked(self):
        result = update_workspace([_ws()], "sing", files_property="")
        assert result[0].files_property == ""

    def test_update_leaves_other_workspaces_alone(self):
        other = _ws(id="pagepack", name="pagepack")
        result = update_workspace([_ws(), other], "sing", name="Renamed")
        assert result[1] == other

    def test_update_of_an_unknown_id_changes_nothing(self):
        original = [_ws()]
        assert update_workspace(original, "missing", name="X") == original

    def test_an_empty_name_does_not_erase_the_existing_one(self):
        result = update_workspace([_ws()], "sing", name="   ")
        assert result[0].name == "SING!"

    def test_remove_drops_only_the_named_workspace(self):
        other = _ws(id="pagepack", name="pagepack")
        assert remove_workspace([_ws(), other], "sing") == [other]

    def test_remove_of_an_unknown_id_is_a_no_op(self):
        assert remove_workspace([_ws()], "missing") == [_ws()]

    def test_find_returns_the_match_or_none(self):
        assert find_workspace([_ws()], "sing") == _ws()
        assert find_workspace([_ws()], "missing") is None
