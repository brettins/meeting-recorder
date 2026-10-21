"""Pull main into the install's clone and reinstall it, so a merged change reaches the app."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .self_update import (
    UNKNOWN_REVISION,
    InstallSource,
    RunFn,
    _run,
    checkout_revision,
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


def _git(repo: str, run_fn: RunFn, *args: str) -> tuple[int, str]:
    result = run_fn(["git", "-C", repo, *args])
    return result.returncode, (result.stdout or "").strip()


def check_and_apply(source: InstallSource | None = None, run_fn: RunFn = _run) -> UpdateOutcome:
    """Fast-forward the clone to origin/main; reinstall when it differs from the installed copy."""
    source = source or read_install_source()
    if source is None:
        return UpdateOutcome(False, "not a user-local install")
    repo = source.repo
    try:
        code, branch = _git(repo, run_fn, "rev-parse", "--abbrev-ref", "HEAD")
        if code != 0 or branch != BRANCH:
            return UpdateOutcome(
                False, f"{repo} is on {branch or 'an unknown branch'}, not {BRANCH}"
            )
        code, dirty = _git(repo, run_fn, "status", "--porcelain", "--untracked-files=no")
        if code != 0 or dirty:
            return UpdateOutcome(False, f"{repo} has uncommitted changes")
        code, _ = _git(repo, run_fn, "fetch", "--quiet", "origin", BRANCH)
        if code != 0:
            return UpdateOutcome(False, "could not fetch origin")
        code, _ = _git(repo, run_fn, "merge", "--ff-only", f"origin/{BRANCH}")
        if code != 0:
            return UpdateOutcome(False, f"{repo} cannot fast-forward to origin/{BRANCH}")
    except OSError as exc:
        return UpdateOutcome(False, f"git unavailable: {exc}")

    revision = checkout_revision(repo, run_fn)
    if revision == UNKNOWN_REVISION or revision == source.revision:
        return UpdateOutcome(False, f"up to date ({source.revision})")
    ok, message = run_update(source, run_fn)
    if not ok:
        return UpdateOutcome(False, message)
    return UpdateOutcome(True, f"updated {source.revision} -> {revision}")
