"""Pull main into the install's clone and reinstall it, so a merged change reaches the app."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .self_update import (
    UNKNOWN_REVISION,
    InstallSource,
    RunFn,
    checkout_revision,
    install_dir,
    read_install_source,
    run_update,
)

logger = logging.getLogger(__name__)

BRANCH = "main"
DEFAULT_INTERVAL_HOURS = 6.0
MIN_INTERVAL_HOURS = 0.005


@dataclass(frozen=True)
class UpdateOutcome:
    """Whether a new version was installed, and the line to log."""

    updated: bool
    message: str


def enabled(config: dict[str, Any], source: InstallSource | None) -> bool:
    """Auto-update runs only for a user-local install, which records its source."""
    return source is not None and bool(config.get("auto_update_enabled", True))


def interval_seconds(config: dict[str, Any]) -> int:
    try:
        hours = float(config.get("auto_update_hours", DEFAULT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_INTERVAL_HOURS
    return int(max(hours, MIN_INTERVAL_HOURS) * 3600)


GIT_TIMEOUT_SECONDS = 120


def _has_ssh_override(command: list[str]) -> bool:
    """Whether the clone already configures its own ssh command (a deploy key, say)."""
    if "GIT_SSH_COMMAND" in os.environ or len(command) < 3 or command[1] != "-C":
        return "GIT_SSH_COMMAND" in os.environ
    probe = subprocess.run(
        ["git", "-C", command[2], "config", "--get", "core.sshCommand"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    return probe.returncode == 0


def _run_git(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run git without ever prompting for a passphrase or password."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not _has_ssh_override(command):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    env.pop("SSH_ASKPASS", None)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )


SMOKE_TIMEOUT_SECONDS = 60


def smoke_test(run_fn: Any = subprocess.run) -> tuple[bool, str]:
    """Import the installed daemon module in a fresh interpreter before restarting onto it."""
    root = install_dir()
    env = {**os.environ, "PYTHONPATH": str(root / "linux" / "src")}
    try:
        result = run_fn(
            [str(root / "venv" / "bin" / "python"), "-c", "import meeting_recorder.daemon.app"],
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run the smoke test: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return False, detail[-1] if detail else f"exit status {result.returncode}"
    return True, "ok"


def _git(repo: str, run_fn: RunFn, *args: str) -> tuple[int, str]:
    result = run_fn(["git", "-C", repo, *args])
    return result.returncode, (result.stdout or "").strip()


def check_and_apply(
    source: InstallSource | None = None, run_fn: RunFn | None = None
) -> UpdateOutcome:
    """Fast-forward the clone to origin/main; reinstall when it differs from the installed copy."""
    source = source or read_install_source()
    if source is None:
        return UpdateOutcome(False, "not a user-local install")
    repo = source.repo
    git_fn = run_fn or _run_git
    try:
        code, branch = _git(repo, git_fn, "rev-parse", "--abbrev-ref", "HEAD")
        if code != 0 or branch != BRANCH:
            return UpdateOutcome(
                False, f"{repo} is on {branch or 'an unknown branch'}, not {BRANCH}"
            )
        code, dirty = _git(repo, git_fn, "status", "--porcelain", "--untracked-files=no")
        if code != 0 or dirty:
            return UpdateOutcome(False, f"{repo} has uncommitted changes")
        code, _ = _git(repo, git_fn, "fetch", "--quiet", "origin", BRANCH)
        if code != 0:
            return UpdateOutcome(False, "could not fetch origin")
        code, _ = _git(repo, git_fn, "merge", "--ff-only", f"origin/{BRANCH}")
        if code != 0:
            return UpdateOutcome(False, f"{repo} cannot fast-forward to origin/{BRANCH}")
    except (OSError, subprocess.SubprocessError) as exc:
        return UpdateOutcome(False, f"git unavailable: {exc}")

    revision = checkout_revision(repo, git_fn)
    if revision == UNKNOWN_REVISION or revision == source.revision:
        return UpdateOutcome(False, f"up to date ({source.revision})")
    ok, message = run_update(source, run_fn) if run_fn else run_update(source)
    if not ok:
        return UpdateOutcome(False, message)
    return UpdateOutcome(True, f"updated {source.revision} -> {revision}")
