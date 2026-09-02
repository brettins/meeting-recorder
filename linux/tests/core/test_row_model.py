"""Tests for the shared row model behind the Library and the Record tab.

These cover the join between the daemon's jobs and the scanned meetings, and the
action policy that decides what a row in each state offers — the logic that used
to be split across two unrelated list widgets.
"""

from datetime import datetime
from pathlib import Path

from meeting_recorder.core import row_model as rm
from meeting_recorder.core.job import JobStatus
from meeting_recorder.core.wire import JobView
from meeting_recorder.utils.meeting_scanner import Meeting


def make_meeting(**overrides) -> Meeting:
    defaults = dict(
        path=Path("/meetings/2026-03-01_14-30"),
        time_label="2026-03-01_14-30",
        date=datetime(2026, 3, 1, 14, 30),
        title=None,
        has_notes=False,
        has_transcript=False,
        has_audio=True,
        duration_seconds=2880,
        tags=[],
    )
    defaults.update(overrides)
    return Meeting(**defaults)


def make_job(**overrides) -> JobView:
    defaults = dict(
        job_id=1,
        label="2026-03-01_14-30",
        status=JobStatus.PROCESSING,
        error_msg=None,
        audio_dir="/meetings/2026-03-01_14-30",
        status_text="",
    )
    defaults.update(overrides)
    return JobView(**defaults)


class TestFormatting:
    def test_duration_under_an_hour_is_minutes(self):
        assert rm.format_duration(2880) == "48m"

    def test_duration_over_an_hour_carries_both_units(self):
        assert rm.format_duration(4320) == "1h 12m"

    def test_unknown_duration_is_omitted_entirely(self):
        assert rm.format_duration(None) == ""
        assert rm.format_duration(-1) == ""

    def test_subtitle_joins_date_time_and_duration(self):
        text = rm.meeting_subtitle(datetime(2026, 3, 1, 14, 30), 2880)
        assert "Mar 01, 2026" in text
        assert "2:30 PM" in text
        assert "48m" in text

    def test_subtitle_drops_the_duration_when_unknown(self):
        text = rm.meeting_subtitle(datetime(2026, 3, 1, 14, 30), None)
        assert text.endswith("2:30 PM")


class TestMeetingActions:
    def test_untranscribed_audio_offers_transcription(self):
        actions = rm.meeting_actions(
            has_notes=False,
            has_transcript=False,
            has_audio=True,
            has_title=False,
            can_summarize=True,
        )
        assert rm.SUMMARIZE in actions
        assert rm.AI_TITLE not in actions

    def test_notes_without_a_title_offer_title_generation(self):
        actions = rm.meeting_actions(
            has_notes=True,
            has_transcript=True,
            has_audio=True,
            has_title=False,
            can_summarize=True,
        )
        assert rm.AI_TITLE in actions
        assert rm.SUMMARIZE not in actions

    def test_an_already_titled_meeting_is_not_offered_a_title(self):
        actions = rm.meeting_actions(
            has_notes=True,
            has_transcript=True,
            has_audio=True,
            has_title=True,
            can_summarize=True,
        )
        assert rm.AI_TITLE not in actions

    def test_the_management_actions_are_always_present(self):
        actions = rm.meeting_actions(
            has_notes=False,
            has_transcript=False,
            has_audio=False,
            has_title=False,
            can_summarize=False,
        )
        assert actions == (rm.TAG, rm.RENAME, rm.OPEN_FOLDER, rm.DELETE)


class TestRowActions:
    def test_a_recording_row_offers_nothing(self):
        # ffmpeg holds the folder open; renaming or deleting it would break it.
        assert rm.row_actions(rm.RECORDING, (rm.RENAME, rm.DELETE)) == ()

    def test_a_processing_row_offers_only_cancel(self):
        assert rm.row_actions(rm.PROCESSING, (rm.RENAME, rm.DELETE)) == (rm.CANCEL,)

    def test_a_failed_row_leads_with_details_then_keeps_the_meeting_actions(self):
        actions = rm.row_actions(rm.ERROR, (rm.TAG, rm.DELETE))
        assert actions[0] == rm.DETAILS
        assert actions[:3] == (rm.DETAILS, rm.RETRY, rm.DISMISS)
        assert actions[3:] == (rm.TAG, rm.DELETE)

    def test_a_settled_row_is_just_its_meeting_actions(self):
        assert rm.row_actions(rm.READY, (rm.TAG, rm.DELETE)) == (rm.TAG, rm.DELETE)


class TestStateForJob:
    def test_no_job_is_ready(self):
        assert rm.state_for_job(None) == rm.READY

    def test_done_is_ready(self):
        assert rm.state_for_job(JobStatus.DONE) == rm.READY

    def test_processing_and_error_map_across(self):
        assert rm.state_for_job(JobStatus.PROCESSING) == rm.PROCESSING
        assert rm.state_for_job(JobStatus.ERROR) == rm.ERROR


class TestJoin:
    def test_jobs_are_indexed_by_their_meeting_directory(self):
        job = make_job()
        assert rm.index_jobs_by_dir([job]) == {"/meetings/2026-03-01_14-30": job}

    def test_a_job_with_no_directory_is_skipped(self):
        assert rm.index_jobs_by_dir([make_job(audio_dir="")]) == {}

    def test_the_later_job_for_a_directory_wins(self):
        first = make_job(job_id=1)
        second = make_job(job_id=2, status=JobStatus.ERROR)
        assert rm.index_jobs_by_dir([first, second])["/meetings/2026-03-01_14-30"] is second


class TestRowFromJob:
    def test_a_processing_job_becomes_a_spinning_row(self):
        model = rm.row_from_job(make_job(status_text="Transcribing…"))
        assert model.state == rm.PROCESSING
        assert model.subtitle == "Transcribing…"
        assert model.actions == (rm.CANCEL,)
        assert model.job_id == 1

    def test_a_job_with_no_progress_text_still_says_something(self):
        assert rm.row_from_job(make_job()).subtitle == "Processing…"

    def test_a_failed_job_summarises_its_error(self):
        model = rm.row_from_job(
            make_job(status=JobStatus.ERROR, error_msg="Bad key\nCheck Settings")
        )
        assert model.state == rm.ERROR
        assert model.subtitle.startswith("Error: Bad key")
        assert model.error_msg == "Bad key\nCheck Settings"
        assert rm.DETAILS in model.actions


class TestRowFromMeeting:
    def test_a_settled_meeting_keeps_its_own_actions_and_is_selectable(self):
        model = rm.row_from_meeting(make_meeting(has_notes=True, has_transcript=True))
        assert model.state == rm.READY
        assert model.selectable
        assert rm.DELETE in model.actions
        assert "48m" in model.subtitle

    def test_the_title_wins_over_the_folder_name(self):
        model = rm.row_from_meeting(make_meeting(title="Weekly Sync"))
        assert model.title == "Weekly Sync"

    def test_an_untitled_meeting_falls_back_to_its_time_label(self):
        assert rm.row_from_meeting(make_meeting()).title == "2026-03-01_14-30"

    def test_a_processing_meeting_shows_progress_ahead_of_its_date(self):
        model = rm.row_from_meeting(make_meeting(), make_job(status_text="Transcribing…"))
        assert model.state == rm.PROCESSING
        assert model.subtitle.startswith("Transcribing…")
        assert "48m" in model.subtitle
        assert model.actions == (rm.CANCEL,)
        assert not model.selectable

    def test_a_recording_meeting_says_so_and_offers_nothing(self):
        model = rm.row_from_meeting(make_meeting(), is_recording=True)
        assert model.state == rm.RECORDING
        assert model.subtitle.startswith("Recording…")
        assert model.actions == ()
        assert not model.selectable

    def test_a_recording_row_hides_the_half_written_duration(self):
        # ffprobe on a file ffmpeg is still writing reports nonsense (often 0m).
        model = rm.row_from_meeting(make_meeting(duration_seconds=0), is_recording=True)
        assert "0m" not in model.subtitle
        assert model.subtitle.endswith("2:30 PM")

    def test_recording_beats_a_stale_job_on_the_same_folder(self):
        model = rm.row_from_meeting(make_meeting(), make_job(), is_recording=True)
        assert model.state == rm.RECORDING

    def test_a_done_job_leaves_the_meeting_looking_settled(self):
        model = rm.row_from_meeting(
            make_meeting(has_notes=True, has_transcript=True),
            make_job(status=JobStatus.DONE),
        )
        assert model.state == rm.READY
        assert "Processing" not in model.subtitle
        assert rm.DELETE in model.actions

    def test_the_key_is_the_meeting_directory_so_it_joins_to_its_job(self):
        meeting = make_meeting()
        model = rm.row_from_meeting(meeting)
        assert model.key == str(meeting.path)
        assert model.key in rm.index_jobs_by_dir([make_job()])

    def test_tags_are_carried_onto_the_row(self):
        model = rm.row_from_meeting(make_meeting(tags=["Work", "Urgent"]))
        assert model.tags == ["Work", "Urgent"]
