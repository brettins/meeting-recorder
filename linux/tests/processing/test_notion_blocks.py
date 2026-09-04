"""Tests for the Markdown → Notion block conversion."""

from meeting_recorder.processing.notion_blocks import (
    MAX_BLOCKS_PER_REQUEST,
    MAX_TEXT_LENGTH,
    chunk_blocks,
    markdown_to_blocks,
    rich_text,
    transcript_toggle,
)


def _plain(items):
    return "".join(i["text"]["content"] for i in items)


def _kinds(blocks):
    return [b["type"] for b in blocks]


def _text_of(block):
    return _plain(block[block["type"]]["rich_text"])


class TestRichText:
    def test_plain_text_has_no_annotations(self):
        items = rich_text("just words")
        assert len(items) == 1
        assert items[0]["text"]["content"] == "just words"
        assert "annotations" not in items[0]

    def test_bold_span_is_annotated_and_markers_dropped(self):
        items = rich_text("hello **world**")
        assert _plain(items) == "hello world"
        assert items[-1]["annotations"]["bold"] is True

    def test_underscore_bold_is_recognised(self):
        items = rich_text("__loud__")
        assert _plain(items) == "loud"
        assert items[0]["annotations"]["bold"] is True

    def test_italic_span_is_annotated(self):
        items = rich_text("an *aside* here")
        assert _plain(items) == "an aside here"
        assert items[1]["annotations"]["italic"] is True

    def test_bold_italic_carries_both(self):
        items = rich_text("***both***")
        assert items[0]["annotations"] == {"bold": True, "italic": True}

    def test_inline_code_is_annotated(self):
        items = rich_text("run `pytest` now")
        assert items[1]["annotations"]["code"] is True
        assert items[1]["text"]["content"] == "pytest"

    def test_unmatched_marker_stays_literal(self):
        assert _plain(rich_text("2 * 3 = 6")) == "2 * 3 = 6"

    def test_empty_string_yields_nothing(self):
        assert rich_text("") == []

    def test_long_text_is_split_under_the_ceiling(self):
        items = rich_text("word " * 1200)
        assert len(items) > 1
        assert all(len(i["text"]["content"]) <= MAX_TEXT_LENGTH for i in items)

    def test_split_preserves_the_content(self):
        source = "alpha " * 900
        assert _plain(rich_text(source)).replace(" ", "") == source.replace(" ", "")


class TestMarkdownToBlocks:
    def test_headings_map_to_their_level(self):
        blocks = markdown_to_blocks("# One\n## Two\n### Three")
        assert _kinds(blocks) == ["heading_1", "heading_2", "heading_3"]
        assert _text_of(blocks[0]) == "One"

    def test_deep_headings_clamp_to_three(self):
        blocks = markdown_to_blocks("###### Six")
        assert blocks[0]["type"] == "heading_3"

    def test_bullets_become_list_items(self):
        blocks = markdown_to_blocks("- first\n* second\n+ third")
        assert _kinds(blocks) == ["bulleted_list_item"] * 3
        assert _text_of(blocks[1]) == "second"

    def test_numbered_items_become_numbered_list(self):
        blocks = markdown_to_blocks("1. first\n2) second")
        assert _kinds(blocks) == ["numbered_list_item"] * 2

    def test_checkboxes_become_to_do_with_state(self):
        blocks = markdown_to_blocks("- [ ] open\n- [x] done")
        assert _kinds(blocks) == ["to_do", "to_do"]
        assert blocks[0]["to_do"]["checked"] is False
        assert blocks[1]["to_do"]["checked"] is True
        assert _text_of(blocks[1]) == "done"

    def test_checkbox_wins_over_plain_bullet(self):
        blocks = markdown_to_blocks("- [ ] a task")
        assert blocks[0]["type"] == "to_do"

    def test_quote_and_divider(self):
        blocks = markdown_to_blocks("> quoted\n\n---")
        assert _kinds(blocks) == ["quote", "divider"]
        assert _text_of(blocks[0]) == "quoted"

    def test_paragraph_is_the_fallback(self):
        blocks = markdown_to_blocks("ordinary prose")
        assert blocks[0]["type"] == "paragraph"

    def test_blank_lines_produce_nothing(self):
        assert markdown_to_blocks("\n\n\n") == []

    def test_empty_document_is_empty(self):
        assert markdown_to_blocks("") == []

    def test_fenced_code_keeps_its_language(self):
        blocks = markdown_to_blocks("```python\nx = 1\n```")
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "python"
        assert _plain(blocks[0]["code"]["rich_text"]) == "x = 1"

    def test_code_language_alias_is_resolved(self):
        blocks = markdown_to_blocks("```js\nlet a\n```")
        assert blocks[0]["code"]["language"] == "javascript"

    def test_unknown_code_language_falls_back(self):
        blocks = markdown_to_blocks("```klingon\nqapla\n```")
        assert blocks[0]["code"]["language"] == "plain text"

    def test_unclosed_fence_still_yields_a_code_block(self):
        blocks = markdown_to_blocks("```\nno end")
        assert _kinds(blocks) == ["code"]
        assert _plain(blocks[0]["code"]["rich_text"]) == "no end"

    def test_markers_inside_a_fence_are_not_parsed(self):
        blocks = markdown_to_blocks("```\n# not a heading\n- not a bullet\n```")
        assert _kinds(blocks) == ["code"]

    def test_crlf_input_is_handled(self):
        blocks = markdown_to_blocks("# One\r\n- two")
        assert _kinds(blocks) == ["heading_1", "bulleted_list_item"]

    def test_a_real_notes_document(self):
        notes = (
            "### Meeting Summary\n"
            "The team met to review progress.\n\n"
            "### Key Discussion Points\n"
            "*   **Dean:** Reported closing out navigation tasks.\n"
            "*   **Leo:** Blocked by environment configuration.\n\n"
            "### Action Items\n"
            "- [ ] **Leo** to check availability.\n"
            "- [x] **Dean** to file a ticket.\n"
        )
        blocks = markdown_to_blocks(notes)
        assert _kinds(blocks) == [
            "heading_3",
            "paragraph",
            "heading_3",
            "bulleted_list_item",
            "bulleted_list_item",
            "heading_3",
            "to_do",
            "to_do",
        ]
        assert blocks[-1]["to_do"]["checked"] is True
        assert _text_of(blocks[3]) == "Dean: Reported closing out navigation tasks."


class TestTranscriptToggle:
    def test_toggle_is_labelled_and_collapsed_by_default(self):
        block = transcript_toggle("one\n\ntwo")
        assert block["type"] == "toggle"
        assert _plain(block["toggle"]["rich_text"]) == "Transcript"

    def test_custom_label(self):
        block = transcript_toggle("body", label="Full transcript")
        assert _plain(block["toggle"]["rich_text"]) == "Full transcript"

    def test_short_paragraphs_are_packed_together(self):
        block = transcript_toggle("\n\n".join(f"turn {i}" for i in range(50)))
        assert len(block["toggle"]["children"]) == 1

    def test_children_stay_within_the_request_ceiling(self):
        long_turn = "word " * 500
        block = transcript_toggle("\n\n".join([long_turn] * 400))
        assert len(block["toggle"]["children"]) <= MAX_BLOCKS_PER_REQUEST

    def test_empty_transcript_yields_no_children(self):
        assert transcript_toggle("")["toggle"]["children"] == []

    def test_transcript_markers_are_not_interpreted(self):
        block = transcript_toggle("- not a bullet")
        child = block["toggle"]["children"][0]
        assert child["type"] == "paragraph"
        assert _plain(child["paragraph"]["rich_text"]) == "- not a bullet"


class TestChunkBlocks:
    def test_short_list_is_one_batch(self):
        assert len(chunk_blocks([{"a": 1}] * 10)) == 1

    def test_long_list_is_split_at_the_ceiling(self):
        batches = chunk_blocks([{"a": 1}] * 250)
        assert [len(b) for b in batches] == [100, 100, 50]

    def test_empty_list_yields_no_batches(self):
        assert chunk_blocks([]) == []
