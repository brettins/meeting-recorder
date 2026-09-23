"""Tests for building and sending a meeting to Dispatch, with a fake client."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from meeting_recorder.processing.dispatch_filing import (
    build_payload,
    file_meeting,
    filed_summary,
    meeting_id_for,
    meeting_title,
)
from meeting_recorder.services.dispatch_service import FiledMeeting, FiledTask

NOTES = "### Summary\nWe met.\n\n## Action Items\n- [ ] Ring the venue\n- [ ] Send the budget\n"


@dataclass
class FakeMeeting:
    path: Path
    date: datetime
    title: str | None = "Standup"
    duration_seconds: int | None = 900


class FakeClient:
    def __init__(self):
        self.payloads = []

    def file_meeting(self, payload):
        self.payloads.append(payload)
        return FiledMeeting(key="DSP-9", url="http://d/items/9", created=True)


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "2026-09-23_14-30_Standup"
    folder.mkdir()
    (folder / "notes.md").write_text(NOTES)
    (folder / "transcript.md").write_text("**Speaker 1:** hello\n")
    return FakeMeeting(path=folder, date=datetime(2026, 9, 23, 14, 30))


class TestMeetingId:
    def test_it_is_the_minute_from_the_folder(self):
        path = Path("/m/2026-09-23_14-30_Standup")
        assert meeting_id_for(path, datetime(2000, 1, 1)) == "2026-09-23T14:30"

    def test_a_rename_keeps_it(self):
        before = meeting_id_for(Path("/m/2026-09-23_14-30"), datetime(2026, 9, 23))
        after = meeting_id_for(Path("/m/2026-09-23_14-30_Budget_review"), datetime(2026, 9, 23))
        assert before == after

    def test_an_odd_folder_falls_back_to_the_date(self):
        assert (
            meeting_id_for(Path("/m/imported"), datetime(2026, 9, 23, 9, 5)) == "2026-09-23T09:05"
        )


class TestPayload:
    def test_it_carries_everything_dispatch_takes(self, meeting):
        payload = build_payload(
            meeting, notes="n", transcript="t", actions=["Ring"], company="Dispatch"
        )
        assert payload["meeting_id"] == "2026-09-23T14:30"
        assert payload["title"] == "Standup"
        assert payload["duration_seconds"] == 900
        assert payload["notes"] == "n"
        assert payload["transcript"] == "t"
        assert payload["actions"] == ["Ring"]
        assert payload["company"] == "Dispatch"

    def test_the_date_carries_an_offset(self, meeting):
        payload = build_payload(meeting, notes="", transcript="", actions=[])
        assert datetime.fromisoformat(payload["date"]).tzinfo is not None

    def test_no_company_is_left_out(self, meeting):
        assert "company" not in build_payload(meeting, notes="", transcript="", actions=[])

    def test_an_unknown_duration_is_zero(self, meeting):
        meeting.duration_seconds = None
        assert build_payload(meeting, notes="", transcript="", actions=[])["duration_seconds"] == 0

    def test_an_unnamed_meeting_is_named_by_its_time(self):
        assert meeting_title(None, datetime(2026, 9, 23, 14, 30)) == "Meeting Sep 23, 2026 14:30"


class TestFiling:
    def test_the_notes_action_lines_are_sent(self, meeting):
        client = FakeClient()
        file_meeting(client, meeting)
        assert client.payloads[0]["actions"] == ["Ring the venue", "Send the budget"]
        assert client.payloads[0]["transcript"] == "**Speaker 1:** hello\n"

    def test_chosen_lines_replace_the_parsed_ones(self, meeting):
        client = FakeClient()
        file_meeting(client, meeting, actions=["Send the budget"])
        assert client.payloads[0]["actions"] == ["Send the budget"]

    def test_a_meeting_without_notes_still_files(self, meeting):
        (meeting.path / "notes.md").unlink()
        client = FakeClient()
        file_meeting(client, meeting)
        assert client.payloads[0]["notes"] == ""
        assert client.payloads[0]["actions"] == []


def _filed(created, *flags):
    tasks = [FiledTask(key=f"D-{n}", title="t", created=flag) for n, flag in enumerate(flags)]
    return FiledMeeting(key="DSP-9", url="", created=created, tasks=tasks)


class TestSummary:
    def test_a_first_filing(self):
        assert filed_summary(_filed(True, True, True)) == (
            "Filed into Dispatch as DSP-9, with 2 draft tasks in triage."
        )

    def test_one_task_is_singular(self):
        assert "1 draft task in" in filed_summary(_filed(True, True))

    def test_no_actions(self):
        assert "no draft tasks" in filed_summary(_filed(True))

    def test_refiling_with_a_new_line(self):
        assert filed_summary(_filed(False, False, True)) == (
            "Updated DSP-9 in Dispatch, and added 1 draft task to triage."
        )

    def test_refiling_with_nothing_new(self):
        assert "already there" in filed_summary(_filed(False, False))

    def test_refiling_a_meeting_without_tasks(self):
        assert filed_summary(_filed(False)) == "Updated DSP-9 in Dispatch."
