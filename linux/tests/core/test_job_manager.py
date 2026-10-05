"""Tests for JobManager persistence and startup recovery (temp dirs, no GTK)."""

import json
from pathlib import Path

import pytest

from meeting_recorder.core.job import JobStatus
from meeting_recorder.core.job_manager import (
    INTERRUPTED_MSG,
    KEEP_FINISHED,
    KEEP_FINISHED_SECONDS,
    JobManager,
    finished_to_keep,
    restore_status,
)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _mgr(tmp_path: Path, clock=None) -> JobManager:
    return JobManager(state_dir=tmp_path, clock=clock) if clock else JobManager(state_dir=tmp_path)


def _create(mgr: JobManager, label: str = "job", folder: Path | None = None):
    base = folder or Path("/m")
    return mgr.create(
        audio_path=base / "recording.mp3",
        transcript_path=base / "transcript.md",
        notes_path=base / "notes.md",
        label=label,
    )


def _meeting_dir(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / "meetings" / name
    folder.mkdir(parents=True)
    return folder


class TestRestoreStatusPolicy:
    def test_processing_becomes_interrupted_error(self):
        assert restore_status("processing") == (JobStatus.ERROR, INTERRUPTED_MSG)

    def test_error_restored_as_error_keeping_message(self):
        assert restore_status("error") == (JobStatus.ERROR, None)

    def test_done_is_restored_as_done(self):
        assert restore_status("done") == (JobStatus.DONE, None)

    def test_unknown_status_is_dropped(self):
        assert restore_status("garbage") is None


class TestPersistence:
    def test_create_persists_to_disk(self, tmp_path):
        mgr = _mgr(tmp_path)
        _create(mgr, "standup")
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["label"] == "standup"
        assert data["jobs"][0]["status"] == "processing"

    def test_mark_done_and_error_persist(self, tmp_path):
        mgr = _mgr(tmp_path)
        a = _create(mgr, "a")
        b = _create(mgr, "b")
        mgr.mark_done(a)
        mgr.mark_error(b, "boom")
        data = json.loads((tmp_path / "jobs.json").read_text())
        statuses = {j["label"]: (j["status"], j["error_msg"]) for j in data["jobs"]}
        assert statuses == {"a": ("done", None), "b": ("error", "boom")}

    def test_remove_persists(self, tmp_path):
        mgr = _mgr(tmp_path)
        job = _create(mgr)
        mgr.remove(job)
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []
        assert mgr.jobs == []

    def test_cancelled_jobs_are_not_persisted(self, tmp_path):
        mgr = _mgr(tmp_path)
        job = _create(mgr)
        job.cancelled = True
        mgr.persist()
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []

    def test_no_partial_file_on_disk(self, tmp_path):
        mgr = _mgr(tmp_path)
        _create(mgr)
        # atomic write: only the final file remains, no tmp leftovers
        assert sorted(p.name for p in tmp_path.iterdir()) == ["jobs.json"]


class TestStartupRecovery:
    def test_interrupted_processing_job_reoffered_as_error(self, tmp_path):
        first = _mgr(tmp_path)
        _create(first, "meeting A")  # stays PROCESSING — simulates a crash

        second = _mgr(tmp_path)
        restored = second.load_persisted()
        assert len(restored) == 1
        assert restored[0].status is JobStatus.ERROR
        assert restored[0].error_msg == INTERRUPTED_MSG
        assert restored[0].label == "meeting A"

    def test_error_job_restored_with_original_message(self, tmp_path):
        first = _mgr(tmp_path)
        job = _create(first, "meeting B")
        first.mark_error(job, "quota exceeded")

        restored = _mgr(tmp_path).load_persisted()
        assert restored[0].error_msg == "quota exceeded"

    def test_expired_done_jobs_are_pruned_from_disk(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        _finished(first, clock, tmp_path, "old")
        clock.now += KEEP_FINISHED_SECONDS + 1

        assert _mgr(tmp_path, clock).load_persisted() == []
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []

    def test_ids_do_not_collide_after_restore(self, tmp_path):
        first = _mgr(tmp_path)
        old = _create(first)

        second = _mgr(tmp_path)
        second.load_persisted()
        new = _create(second)
        assert new.job_id != old.job_id

    def test_missing_file_starts_empty(self, tmp_path):
        assert _mgr(tmp_path).load_persisted() == []

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "jobs.json").write_text("{not json")
        assert _mgr(tmp_path).load_persisted() == []

    def test_malformed_entry_skipped_others_restored(self, tmp_path):
        (tmp_path / "jobs.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "next_id": 5,
                    "jobs": [
                        {"status": "processing"},  # missing required fields
                        {
                            "job_id": 3,
                            "audio_path": "/m/r.mp3",
                            "transcript_path": "/m/t.md",
                            "notes_path": "/m/n.md",
                            "label": "good",
                            "status": "processing",
                            "error_msg": None,
                        },
                    ],
                }
            )
        )
        restored = _mgr(tmp_path).load_persisted()
        assert [j.label for j in restored] == ["good"]

    def test_restored_jobs_get_fresh_tokens(self, tmp_path):
        first = _mgr(tmp_path)
        _create(first)
        restored = _mgr(tmp_path).load_persisted()
        assert restored[0].token.cancelled is False


class TestMalformedStateFiles:
    def test_non_dict_json_starts_empty(self, tmp_path):
        (tmp_path / "jobs.json").write_text('["not", "an", "object"]')
        assert _mgr(tmp_path).load_persisted() == []

    def test_malformed_next_id_tolerated(self, tmp_path):
        (tmp_path / "jobs.json").write_text(
            json.dumps({"version": 1, "next_id": "garbage", "jobs": []})
        )
        mgr = _mgr(tmp_path)
        assert mgr.load_persisted() == []
        job = _create(mgr)  # id allocation must still work
        assert job.job_id == 0


def _finished(mgr: JobManager, clock: Clock, tmp_path: Path, name: str):
    job = _create(mgr, name, _meeting_dir(tmp_path, name))
    mgr.mark_done(job)
    return job


class TestFinishedJobsStayOnScreen:
    def test_a_finished_job_survives_a_restart(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        _finished(first, clock, tmp_path, "standup")

        restored = _mgr(tmp_path, clock).load_persisted()
        assert [(j.label, j.status) for j in restored] == [("standup", JobStatus.DONE)]

    def test_finished_at_is_written_when_the_job_finishes(self, tmp_path):
        clock = Clock(5000.0)
        mgr = _mgr(tmp_path, clock)
        _finished(mgr, clock, tmp_path, "a")
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"][0]["finished_at"] == 5000.0

    def test_a_job_finished_days_ago_is_dropped_on_start(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        _finished(first, clock, tmp_path, "old")
        clock.now += KEEP_FINISHED_SECONDS + 1
        assert _mgr(tmp_path, clock).load_persisted() == []

    def test_a_job_just_inside_the_window_is_kept(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        _finished(first, clock, tmp_path, "edge")
        clock.now += KEEP_FINISHED_SECONDS
        assert len(_mgr(tmp_path, clock).load_persisted()) == 1

    def test_only_the_newest_few_are_kept(self, tmp_path):
        clock = Clock()
        mgr = _mgr(tmp_path, clock)
        for n in range(KEEP_FINISHED + 3):
            clock.now += 60
            _finished(mgr, clock, tmp_path, f"m{n}")
        labels = [j.label for j in mgr.jobs]
        assert labels == [f"m{n}" for n in range(3, KEEP_FINISHED + 3)]
        assert [j.label for j in _mgr(tmp_path, clock).load_persisted()] == labels

    def test_in_flight_and_failed_jobs_never_count_against_the_limit(self, tmp_path):
        clock = Clock()
        mgr = _mgr(tmp_path, clock)
        busy = _create(mgr, "busy", _meeting_dir(tmp_path, "busy"))
        broken = _create(mgr, "broken", _meeting_dir(tmp_path, "broken"))
        mgr.mark_error(broken, "quota")
        for n in range(KEEP_FINISHED + 2):
            clock.now += 60
            _finished(mgr, clock, tmp_path, f"m{n}")
        assert busy in mgr.jobs and broken in mgr.jobs
        assert len([j for j in mgr.jobs if j.status is JobStatus.DONE]) == KEEP_FINISHED

    def test_a_finished_job_whose_folder_was_deleted_is_dropped(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        job = _finished(first, clock, tmp_path, "gone")
        job.audio_path.parent.rmdir()
        assert _mgr(tmp_path, clock).load_persisted() == []

    def test_a_finished_entry_with_no_timestamp_is_dropped(self, tmp_path):
        folder = _meeting_dir(tmp_path, "legacy")
        (tmp_path / "jobs.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "next_id": 1,
                    "jobs": [
                        {
                            "job_id": 0,
                            "audio_path": str(folder / "recording.mp3"),
                            "transcript_path": str(folder / "transcript.md"),
                            "notes_path": str(folder / "notes.md"),
                            "label": "legacy",
                            "status": "done",
                            "error_msg": None,
                        }
                    ],
                }
            )
        )
        assert _mgr(tmp_path).load_persisted() == []

    def test_a_dismissed_job_stays_gone_after_a_restart(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        job = _finished(first, clock, tmp_path, "dismissed")
        first.remove(job)
        assert _mgr(tmp_path, clock).load_persisted() == []

    def test_retrying_a_job_clears_its_finish_time(self, tmp_path):
        clock = Clock()
        mgr = _mgr(tmp_path, clock)
        job = _finished(mgr, clock, tmp_path, "again")
        mgr.mark_processing(job)
        assert job.finished_at is None

    def test_ids_do_not_collide_with_a_restored_finished_job(self, tmp_path):
        clock = Clock()
        first = _mgr(tmp_path, clock)
        old = _finished(first, clock, tmp_path, "old")
        second = _mgr(tmp_path, clock)
        second.load_persisted()
        assert _create(second).job_id != old.job_id


class TestFinishedToKeepPolicy:
    @staticmethod
    def _job(status, finished_at):
        from meeting_recorder.core.job import Job

        return Job(
            1, Path("/a"), Path("/t"), Path("/n"), "x", status=status, finished_at=finished_at
        )

    def test_only_done_jobs_are_candidates(self):
        jobs = [self._job(JobStatus.ERROR, 100.0), self._job(JobStatus.PROCESSING, None)]
        assert finished_to_keep(jobs, 200.0) == []

    @pytest.mark.parametrize(
        "age,kept", [(0, True), (KEEP_FINISHED_SECONDS, True), (KEEP_FINISHED_SECONDS + 1, False)]
    )
    def test_the_age_limit(self, age, kept):
        job = self._job(JobStatus.DONE, 1000.0)
        assert bool(finished_to_keep([job], 1000.0 + age)) is kept
