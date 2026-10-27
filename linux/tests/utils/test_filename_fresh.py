"""A new recording never lands in a folder that already holds one."""

from datetime import datetime

from meeting_recorder.utils.filename import fresh_dir, output_paths

WHEN = datetime(2026, 3, 1, 14, 30)


def test_an_unused_name_is_kept(tmp_path):
    assert fresh_dir(tmp_path, "2026-03-01_14-30").name == "2026-03-01_14-30"


def test_an_empty_folder_is_reused(tmp_path):
    (tmp_path / "2026-03-01_14-30").mkdir()
    assert fresh_dir(tmp_path, "2026-03-01_14-30").name == "2026-03-01_14-30"


def test_a_folder_holding_a_meeting_is_stepped_past(tmp_path):
    held = tmp_path / "2026-03-01_14-30"
    held.mkdir()
    (held / "recording.mp3").write_bytes(b"first")
    assert fresh_dir(tmp_path, "2026-03-01_14-30").name == "2026-03-01_14-30_2"


def test_the_counter_keeps_climbing(tmp_path):
    for name in ("2026-03-01_14-30", "2026-03-01_14-30_2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "recording.mp3").write_bytes(b"x")
    assert fresh_dir(tmp_path, "2026-03-01_14-30").name == "2026-03-01_14-30_3"


def test_two_recordings_in_one_minute_keep_both_recordings(tmp_path):
    first, _, _ = output_paths(str(tmp_path), "Standup", WHEN)
    first.write_bytes(b"first")
    second, _, _ = output_paths(str(tmp_path), "Standup", WHEN)
    assert second.parent != first.parent
    assert first.read_bytes() == b"first"
