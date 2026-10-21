"""Tests for the auto-update check: fetch, fast-forward, reinstall."""

import subprocess

from meeting_recorder.services import auto_update
from meeting_recorder.services.auto_update import (
    UpdateOutcome,
    check_and_apply,
    enabled,
    interval_seconds,
)
from meeting_recorder.services.self_update import InstallSource

SOURCE = InstallSource(repo="/clone", script="/clone/linux/install.sh", revision="v1-3-gaaa")


def _result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class _Git:
    """Scripted git/bash: keys are the git subcommand (or 'install')."""

    def __init__(self, **replies):
        self.replies = {
            "rev-parse": _result(0, "main\n"),
            "status": _result(0, ""),
            "fetch": _result(0),
            "merge": _result(0),
            "describe": _result(0, "v1-5-gbbb\n"),
            "install": _result(0),
        }
        self.replies.update(replies)
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if command[0] == "bash":
            return self.replies["install"]
        return self.replies[command[3]]

    def ran(self, name):
        return any(c[0] == "bash" if name == "install" else c[3:4] == [name] for c in self.calls)


class TestCheckAndApply:
    def test_a_new_revision_is_installed(self):
        git = _Git()
        outcome = check_and_apply(SOURCE, git)
        assert outcome == UpdateOutcome(True, "updated v1-3-gaaa -> v1-5-gbbb")
        assert git.ran("install")
        assert ["bash", "/clone/linux/install.sh", "--skip-deps"] in git.calls

    def test_the_same_revision_is_left_alone(self):
        git = _Git(describe=_result(0, "v1-3-gaaa\n"))
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False
        assert not git.ran("install")

    def test_a_checkout_on_another_branch_is_never_touched(self):
        git = _Git(**{"rev-parse": _result(0, "feature/x\n")})
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False and "not main" in outcome.message
        assert not git.ran("fetch") and not git.ran("merge")

    def test_uncommitted_changes_stop_the_update(self):
        git = _Git(status=_result(0, " M linux/x.py\n"))
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False and "uncommitted" in outcome.message
        assert not git.ran("merge")

    def test_a_failed_fetch_changes_nothing(self):
        git = _Git(fetch=_result(1))
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False and "fetch" in outcome.message
        assert not git.ran("merge")

    def test_a_diverged_clone_does_not_install(self):
        git = _Git(merge=_result(1))
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False and "fast-forward" in outcome.message
        assert not git.ran("install")

    def test_a_failed_install_is_reported_not_counted_as_updated(self):
        git = _Git(install=_result(1, "", "pip failed"))
        outcome = check_and_apply(SOURCE, git)
        assert outcome.updated is False and "pip failed" in outcome.message

    def test_an_unreadable_revision_installs_nothing(self):
        git = _Git(describe=_result(1))
        assert check_and_apply(SOURCE, git).updated is False
        assert not git.ran("install")

    def test_git_missing_is_not_fatal(self):
        def boom(_command):
            raise FileNotFoundError("git")

        assert check_and_apply(SOURCE, boom).updated is False

    def test_a_packaged_install_has_nothing_to_update(self, monkeypatch):
        monkeypatch.setattr(auto_update, "read_install_source", lambda: None)
        assert check_and_apply(None, _Git()).updated is False


class TestConfig:
    def test_runs_only_for_a_user_local_install(self):
        assert enabled({}, SOURCE) is True
        assert enabled({}, None) is False

    def test_can_be_switched_off(self):
        assert enabled({"auto_update_enabled": False}, SOURCE) is False

    def test_interval_defaults_to_six_hours(self):
        assert interval_seconds({}) == 6 * 3600

    def test_interval_reads_hours_and_clamps_nonsense(self):
        assert interval_seconds({"auto_update_hours": 1}) == 3600
        assert interval_seconds({"auto_update_hours": "x"}) == 6 * 3600
        assert interval_seconds({"auto_update_hours": 0}) == int(
            auto_update.MIN_INTERVAL_HOURS * 3600
        )
