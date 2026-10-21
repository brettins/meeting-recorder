"""Tests for the user-local self-update: source record, status policy, runners."""

import json
import subprocess

from meeting_recorder.services.self_update import (
    UNKNOWN_REVISION,
    InstallSource,
    checkout_revision,
    decide_status,
    is_dirty,
    parse_install_source,
    read_install_source,
    run_update,
    source_path,
)

SOURCE = InstallSource(
    repo="/home/b/dev/mr", script="/home/b/dev/mr/linux/install.sh", revision="v1.2-3-gabc"
)


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestParseInstallSource:
    def test_a_complete_record_parses(self):
        text = json.dumps({"repo": "/r", "script": "/r/linux/install.sh", "revision": "v1"})
        assert parse_install_source(text) == InstallSource("/r", "/r/linux/install.sh", "v1")

    def test_a_missing_revision_becomes_unknown(self):
        text = json.dumps({"repo": "/r", "script": "/s"})
        parsed = parse_install_source(text)
        assert parsed is not None
        assert parsed.revision == UNKNOWN_REVISION

    def test_a_record_without_a_repo_is_rejected(self):
        assert parse_install_source(json.dumps({"script": "/s"})) is None

    def test_a_record_without_a_script_is_rejected(self):
        assert parse_install_source(json.dumps({"repo": "/r"})) is None

    def test_malformed_json_is_rejected(self):
        assert parse_install_source("{not json") is None

    def test_a_json_list_is_rejected(self):
        assert parse_install_source("[]") is None


class TestReadInstallSource:
    def test_a_written_record_reads_back(self, tmp_path):
        path = tmp_path / "install-source.json"
        path.write_text(json.dumps({"repo": "/r", "script": "/s", "revision": "v1"}))
        source = read_install_source(path)
        assert source is not None
        assert source.repo == "/r"

    def test_a_missing_file_reads_as_none(self, tmp_path):
        assert read_install_source(tmp_path / "absent.json") is None

    def test_the_default_path_sits_inside_the_install(self, tmp_path):
        assert source_path(tmp_path).name == "install-source.json"


class TestDecideStatus:
    def test_a_packaged_install_cannot_update_itself(self):
        status = decide_status(None, "v2")
        assert status.can_update is False
        assert "package manager" in status.message

    def test_matching_revisions_are_up_to_date(self):
        status = decide_status(SOURCE, "v1.2-3-gabc")
        assert status.can_update is False
        assert status.message.startswith("Up to date")

    def test_a_newer_checkout_offers_an_update(self):
        status = decide_status(SOURCE, "v1.3-0-gdef")
        assert status.can_update is True
        assert "v1.2-3-gabc → v1.3-0-gdef" in status.message

    def test_a_dirty_checkout_at_the_same_revision_still_offers_an_update(self):
        dirty = InstallSource(SOURCE.repo, SOURCE.script, "v1.2-3-gabc-dirty")
        status = decide_status(dirty, "v1.2-3-gabc-dirty")
        assert status.can_update is True
        assert "uncommitted changes" in status.message

    def test_an_unreadable_checkout_cannot_update(self):
        status = decide_status(SOURCE, UNKNOWN_REVISION)
        assert status.can_update is False
        assert SOURCE.repo in status.message

    def test_an_empty_revision_cannot_update(self):
        assert decide_status(SOURCE, "").can_update is False

    def test_the_installed_revision_is_always_reported(self):
        assert decide_status(SOURCE, "v9").installed == "v1.2-3-gabc"

    def test_dirty_detection(self):
        assert is_dirty("v1-dirty") is True
        assert is_dirty("v1") is False


class TestCheckoutRevision:
    def test_the_described_revision_is_returned(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return _result(stdout="v1.3-0-gdef\n")

        assert checkout_revision("/r", run) == "v1.3-0-gdef"
        assert calls[0] == ["git", "-C", "/r", "describe", "--tags", "--always", "--dirty"]

    def test_a_failing_git_yields_unknown(self):
        assert checkout_revision("/r", lambda _c: _result(returncode=128)) == UNKNOWN_REVISION

    def test_empty_output_yields_unknown(self):
        assert checkout_revision("/r", lambda _c: _result(stdout="  \n")) == UNKNOWN_REVISION

    def test_a_missing_git_yields_unknown(self):
        def run(_cmd):
            raise FileNotFoundError("git")

        assert checkout_revision("/r", run) == UNKNOWN_REVISION


class TestRunUpdate:
    def test_the_installer_runs_with_skip_deps_and_no_elevation(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return _result()

        ok, message = run_update(SOURCE, run)
        assert ok is True
        assert calls == [["bash", SOURCE.script, "--skip-deps"]]
        assert "sudo" not in " ".join(calls[0])
        assert "pkexec" not in " ".join(calls[0])
        assert "Restart" in message

    def test_a_failing_installer_reports_its_last_line(self):
        ok, message = run_update(
            SOURCE, lambda _c: _result(returncode=1, stderr="pip failed\nno space")
        )
        assert ok is False
        assert "no space" in message

    def test_a_failure_with_no_output_reports_the_exit_status(self):
        ok, message = run_update(SOURCE, lambda _c: _result(returncode=3))
        assert ok is False
        assert "exit status 3" in message

    def test_a_missing_installer_is_reported(self):
        def run(_cmd):
            raise FileNotFoundError("bash")

        ok, message = run_update(SOURCE, run)
        assert ok is False
        assert "Could not run the installer" in message


class TestUpdateLock:
    def test_a_second_update_is_refused_while_one_runs(self, tmp_path):
        import fcntl

        held = open(tmp_path / "update.lock", "w")
        fcntl.flock(held, fcntl.LOCK_EX)
        ran = []
        ok, message = run_update(SOURCE, lambda c: ran.append(c) or _result(), lock_dir=tmp_path)
        held.close()
        assert ok is False and "already running" in message
        assert ran == []

    def test_the_lock_is_released_after_an_update(self, tmp_path):
        assert run_update(SOURCE, lambda _c: _result(), lock_dir=tmp_path)[0] is True
        assert run_update(SOURCE, lambda _c: _result(), lock_dir=tmp_path)[0] is True
