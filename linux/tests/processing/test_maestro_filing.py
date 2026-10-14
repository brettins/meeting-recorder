"""Tests for building and sending a meeting to Maestro, with a fake client."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from meeting_recorder.processing.maestro_filing import (
    build_payload,
    file_meeting,
    filed_summary,
    meeting_id_for,
    meeting_title,
)
from meeting_recorder.services.maestro_service import FiledMeeting, FiledTask

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
    def test_it_carries_everything_maestro_takes(self, meeting):
        payload = build_payload(
            meeting, notes="n", transcript="t", actions=["Ring"], company="Maestro"
        )
        assert payload["meeting_id"] == "2026-09-23T14:30"
        assert payload["title"] == "Standup"
        assert payload["duration_seconds"] == 900
        assert payload["notes"] == "n"
        assert payload["transcript"] == "t"
        assert payload["actions"] == ["Ring"]
        assert payload["company"] == "Maestro"

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
            "Filed into Maestro as DSP-9, with 2 draft tasks in Sort."
        )

    def test_one_task_is_singular(self):
        assert "1 draft task in" in filed_summary(_filed(True, True))

    def test_no_actions(self):
        assert "no draft tasks" in filed_summary(_filed(True))

    def test_refiling_with_a_new_line(self):
        assert filed_summary(_filed(False, False, True)) == (
            "Updated DSP-9 in Maestro, and added 1 draft task to Sort."
        )

    def test_refiling_with_nothing_new(self):
        assert "already there" in filed_summary(_filed(False, False))

    def test_refiling_a_meeting_without_tasks(self):
        assert filed_summary(_filed(False)) == "Updated DSP-9 in Maestro."


class TestAStableMeetingId:
    """Two meetings in one minute must not file over each other."""

    def _folder(self, root, name):
        path = root / name
        path.mkdir(parents=True)
        return path

    def test_the_first_filing_takes_the_minute(self, tmp_path):
        from datetime import datetime

        from meeting_recorder.processing.maestro_filing import stable_meeting_id

        path = self._folder(tmp_path, "2026-03-01_14-30")
        assert stable_meeting_id(path, datetime(2026, 3, 1, 14, 30)) == "2026-03-01T14:30"

    def test_a_second_meeting_in_the_same_minute_gets_its_own(self, tmp_path):
        from datetime import datetime

        from meeting_recorder.processing.maestro_filing import ID_KEY, stable_meeting_id
        from meeting_recorder.utils.meeting_scanner import write_metadata

        first = self._folder(tmp_path, "2026-03-01_14-30")
        write_metadata(first, {ID_KEY: "2026-03-01T14:30"})
        second = self._folder(tmp_path, "2026-03-01_14-30_2")
        assert stable_meeting_id(second, datetime(2026, 3, 1, 14, 30)) == "2026-03-01T14:30~2"

    def test_an_id_once_given_survives_a_rename(self, tmp_path):
        from datetime import datetime

        from meeting_recorder.processing.maestro_filing import ID_KEY, stable_meeting_id
        from meeting_recorder.utils.meeting_scanner import write_metadata

        path = self._folder(tmp_path, "2026-03-01_14-30_Budget")
        write_metadata(path, {ID_KEY: "2026-03-01T14:30~2"})
        assert stable_meeting_id(path, datetime(2026, 3, 1, 14, 30)) == "2026-03-01T14:30~2"

    def test_filing_writes_the_id_down(self, tmp_path):
        from datetime import datetime

        from meeting_recorder.processing.maestro_filing import ID_KEY, file_meeting
        from meeting_recorder.services.maestro_service import FiledMeeting
        from meeting_recorder.utils.meeting_scanner import read_metadata

        path = self._folder(tmp_path, "2026-03-01_14-30")
        (path / "notes.md").write_text("Notes", encoding="utf-8")

        class Meeting:
            pass

        meeting = Meeting()
        meeting.path = path
        meeting.date = datetime(2026, 3, 1, 14, 30)
        meeting.title = None
        meeting.duration_seconds = 60

        class Client:
            def file_meeting(self, payload):
                self.payload = payload
                return FiledMeeting(key="DSP-1", url="", created=True)

        file_meeting(Client(), meeting)
        assert read_metadata(path)[ID_KEY] == "2026-03-01T14:30"
