"""Tests for pulling action lines out of meeting notes."""

from meeting_recorder.processing.action_items import clean, extract_actions

DEFAULT_PROMPT_NOTES = """\
### Summary
We planned the October show.

### Key points
- **Brett** wants the venue booked.
- Budget is tight.

## Action Items
- [ ] Brett to ring the venue about the deposit
- [ ] **Emily** to send the budget by Friday
"""


class TestUnderAHeading:
    def test_the_default_prompts_shape(self):
        assert extract_actions(DEFAULT_PROMPT_NOTES) == [
            "Brett to ring the venue about the deposit",
            "Emily to send the budget by Friday",
        ]

    def test_bullets_before_the_heading_are_not_actions(self):
        assert "Budget is tight." not in extract_actions(DEFAULT_PROMPT_NOTES)

    def test_next_steps_counts(self):
        notes = "## Next steps\n- Book the hall\n- Tell the choir\n"
        assert extract_actions(notes) == ["Book the hall", "Tell the choir"]

    def test_the_heading_is_case_blind(self):
        assert extract_actions("# ACTION ITEMS\n- Book the hall\n") == ["Book the hall"]

    def test_a_bold_line_is_a_heading(self):
        assert extract_actions("**Action items:**\n- Book the hall\n") == ["Book the hall"]

    def test_a_plain_line_with_a_colon_is_a_heading(self):
        assert extract_actions("Next steps:\n1. Book the hall\n2) Pay\n") == [
            "Book the hall",
            "Pay",
        ]

    def test_the_next_heading_ends_the_section(self):
        notes = "## Action items\n- Book the hall\n## Decisions\n- Keep the date\n"
        assert extract_actions(notes) == ["Book the hall"]

    def test_a_subheading_stays_inside_the_section(self):
        notes = "## Action items\n### Brett\n- Book the hall\n### Emily\n- Pay\n## Other\n- x\n"
        assert extract_actions(notes) == ["Book the hall", "Pay"]

    def test_a_plain_colon_line_ends_a_bold_section(self):
        notes = "**Next steps**\n- Book the hall\nDecisions:\n- Keep the date\n"
        assert extract_actions(notes) == ["Book the hall"]

    def test_nested_detail_is_left_out(self):
        notes = "## Action items\n- Book the hall\n  - the big one\n- Pay\n"
        assert extract_actions(notes) == ["Book the hall", "Pay"]

    def test_a_ticked_box_is_already_done(self):
        notes = "## Action items\n- [x] Book the hall\n- [ ] Pay\n"
        assert extract_actions(notes) == ["Pay"]

    def test_prose_under_the_heading_is_not_an_action(self):
        notes = "## Action items\nNothing firm was agreed.\n"
        assert extract_actions(notes) == []


class TestCheckboxesAnywhere:
    def test_an_open_box_outside_a_section_counts(self):
        assert extract_actions("## Summary\n- [ ] Ring Sam\n- plain\n") == ["Ring Sam"]

    def test_a_ticked_box_outside_a_section_does_not(self):
        assert extract_actions("- [X] Ring Sam\n") == []


class TestCleaning:
    def test_emphasis_and_code_go(self):
        assert clean("**Brett** to fix `deploy.sh`") == "Brett to fix deploy.sh"

    def test_a_link_keeps_its_text(self):
        assert clean("Read [the brief](https://example.com/x)") == "Read the brief"

    def test_underscores_in_words_survive(self):
        assert clean("Rename snake_case_thing") == "Rename snake_case_thing"

    def test_repeats_are_dropped(self):
        notes = "## Action items\n- Book the hall\n- book the  hall\n"
        assert extract_actions(notes) == ["Book the hall"]

    def test_a_long_line_is_cut(self):
        assert len(extract_actions("## Action items\n- " + "x" * 400)[0]) == 200

    def test_no_notes_no_actions(self):
        assert extract_actions("") == []
