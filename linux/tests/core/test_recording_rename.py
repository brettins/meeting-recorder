"""Tests for the rename queued by a title typed while recording."""

from meeting_recorder.core.recording_rename import apply_rename, needs_rename


class TestNeedsRename:
    def test_untitled_folder_with_a_title_needs_renaming(self):
        assert needs_rename("2026-03-01_14-30", "Standup")

    def test_no_title_means_nothing_to_do(self):
        assert not needs_rename("2026-03-01_14-30", None)
        assert not needs_rename("2026-03-01_14-30", "")
        assert not needs_rename("2026-03-01_14-30", "   ")

    def test_a_title_that_sanitizes_to_nothing_is_not_a_rename(self):
        assert not needs_rename("2026-03-01_14-30", "///")

    def test_folder_already_carrying_the_title_is_left_alone(self):
        assert not needs_rename("2026-03-01_14-30_Standup", "Standup")

    def test_title_is_compared_after_sanitizing(self):
        assert not needs_rename("2026-03-01_14-30_Weekly_Sync", "Weekly Sync")

    def test_collision_suffix_is_not_renamed_again(self):
        # rename_meeting_path appends _2 when the target exists; re-applying the
        # same title must not shuffle the folder into _2_2.
        assert not needs_rename("2026-03-01_14-30_Standup_2", "Standup")

    def test_changed_title_still_renames_over_a_collision_suffix(self):
        assert needs_rename("2026-03-01_14-30_Standup_2", "Retro")

    def test_unparseable_folder_name_is_left_alone(self):
        assert not needs_rename("not-a-meeting", "Standup")


class TestApplyRename:
    def _meeting(self, tmp_path, name="2026-03-01_14-30"):
        meeting_dir = tmp_path / name
        meeting_dir.mkdir()
        audio = meeting_dir / "recording.mp3"
        audio.write_bytes(b"audio")
        return audio, meeting_dir / "transcript.md", meeting_dir / "notes.md"

    def test_moves_the_folder_and_rebases_every_path(self, tmp_path):
        audio, transcript, notes = self._meeting(tmp_path)

        new_audio, new_transcript, new_notes = apply_rename(audio, transcript, notes, "Weekly Sync")

        assert new_audio.parent.name == "2026-03-01_14-30_Weekly_Sync"
        assert new_audio.name == "recording.mp3"
        assert new_transcript.parent == new_audio.parent
        assert new_notes.parent == new_audio.parent
        assert new_audio.exists()
        assert not audio.exists()

    def test_no_title_leaves_the_paths_untouched(self, tmp_path):
        audio, transcript, notes = self._meeting(tmp_path)

        assert apply_rename(audio, transcript, notes, None) == (audio, transcript, notes)
        assert audio.exists()

    def test_already_titled_folder_is_not_moved(self, tmp_path):
        audio, transcript, notes = self._meeting(tmp_path, "2026-03-01_14-30_Standup")

        assert apply_rename(audio, transcript, notes, "Standup") == (audio, transcript, notes)

    def test_a_collision_gets_a_suffix_rather_than_clobbering(self, tmp_path):
        (tmp_path / "2026-03-01_14-30_Standup").mkdir()
        audio, transcript, notes = self._meeting(tmp_path)

        new_audio, _, _ = apply_rename(audio, transcript, notes, "Standup")

        assert new_audio.parent.name == "2026-03-01_14-30_Standup_2"

    def test_a_failed_rename_keeps_the_original_paths(self, tmp_path, monkeypatch):
        # Losing the audio over a cosmetic folder name would be a bad trade.
        audio, transcript, notes = self._meeting(tmp_path)

        def boom(*_args, **_kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr("meeting_recorder.utils.meeting_scanner.rename_meeting_path", boom)

        assert apply_rename(audio, transcript, notes, "Standup") == (audio, transcript, notes)
        assert audio.exists()
