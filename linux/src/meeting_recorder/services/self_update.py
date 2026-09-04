"""
Updating a user-local install in place.

``install.sh`` puts everything under ``$HOME`` — the venv, the source, the
launcher, the desktop entry and the icons — so re-running it with
``--skip-deps`` replaces the app without touching anything root owns. That is
the whole update: no package manager, no elevation, no password. The three
steps ``--skip-deps`` omits (distro packages, the GNOME tray extension and
``/var/log/meeting-recorder``) are the only ones that call sudo, and all three
are first-install concerns.

The install records where it came from in ``install-source.json`` next to
itself, so the app can find the checkout again without the user configuring a
path. A ``.deb`` install has no such file, and updating is correctly reported
as unavailable there — that install is owned by dpkg.

Parsing and the available/current decision are pure; the two subprocess calls
take injectable runners.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

INSTALL_DIR = "~/.local/share/meeting-recorder"
SOURCE_FILENAME = "install-source.json"

UNKNOWN_REVISION = "unknown"

# git describe on a dirty tree appends this; an update from a dirty checkout is
# allowed but is never reported as "up to date", since the working tree can
# change without the revision moving.
_DIRTY_SUFFIX = "-dirty"

RunFn = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class InstallSource:
    """Where a user-local install was installed from."""

    repo: str
    script: str
    revision: str


@dataclass(frozen=True)
class UpdateStatus:
    """What the Settings page shows about updating."""

    installed: str
    available: str
    can_update: bool
    message: str


def install_dir(expanduser: Callable[[str], str] = os.path.expanduser) -> Path:
    return Path(expanduser(INSTALL_DIR))


def source_path(directory: Path | None = None) -> Path:
    return (directory or install_dir()) / SOURCE_FILENAME


def parse_install_source(text: str) -> InstallSource | None:
    """Read ``install-source.json``, or None when it is absent or malformed."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    repo = str(data.get("repo") or "").strip()
    script = str(data.get("script") or "").strip()
    if not repo or not script:
        return None
    return InstallSource(
        repo=repo,
        script=script,
        revision=str(data.get("revision") or UNKNOWN_REVISION).strip() or UNKNOWN_REVISION,
    )


def read_install_source(path: Path | None = None) -> InstallSource | None:
    """Load the install record, or None for a packaged install."""
    target = path or source_path()
    try:
        return parse_install_source(target.read_text(encoding="utf-8"))
    except OSError:
        return None


def is_dirty(revision: str) -> bool:
    return revision.endswith(_DIRTY_SUFFIX)


def decide_status(source: InstallSource | None, available: str | None) -> UpdateStatus:
    """Compare what is installed against what the checkout now holds.

    A missing record means this is not a user-local install — a ``.deb`` owns
    those files and updating them from here would fight the package manager.
    """
    if source is None:
        return UpdateStatus(
            installed=UNKNOWN_REVISION,
            available=UNKNOWN_REVISION,
            can_update=False,
            message=(
                "This copy was installed from a package, so updates come from your "
                "package manager. Run linux/install.sh to switch to a user-local "
                "install that updates without a password."
            ),
        )
    if not available or available == UNKNOWN_REVISION:
        return UpdateStatus(
            installed=source.revision,
            available=UNKNOWN_REVISION,
            can_update=False,
            message=f"Could not read the version in {source.repo}.",
        )
    if available == source.revision and not is_dirty(available):
        return UpdateStatus(
            installed=source.revision,
            available=available,
            can_update=False,
            message=f"Up to date ({source.revision}).",
        )
    if is_dirty(available) and available == source.revision:
        return UpdateStatus(
            installed=source.revision,
            available=available,
            can_update=True,
            message=(
                f"The checkout at {source.repo} has uncommitted changes. Update to pick them up."
            ),
        )
    return UpdateStatus(
        installed=source.revision,
        available=available,
        can_update=True,
        message=f"Update available: {source.revision} → {available}.",
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)


def checkout_revision(repo: str, run_fn: RunFn = _run) -> str:
    """``git describe`` of the checkout, or ``unknown`` when it cannot be read."""
    try:
        result = run_fn(["git", "-C", repo, "describe", "--tags", "--always", "--dirty"])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read the revision of %s: %s", repo, exc)
        return UNKNOWN_REVISION
    if result.returncode != 0:
        return UNKNOWN_REVISION
    return (result.stdout or "").strip() or UNKNOWN_REVISION


def run_update(source: InstallSource, run_fn: RunFn = _run) -> tuple[bool, str]:
    """Re-run the install script for this install. Returns (ok, message).

    No elevation: ``--skip-deps`` confines the script to ``$HOME``.
    """
    command = ["bash", source.script, "--skip-deps"]
    logger.info("Updating from %s: %s", source.repo, " ".join(command))
    try:
        result = run_fn(command)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not run the installer: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit status {result.returncode}"
        return False, f"The update failed: {tail}"
    return True, "Updated. Restart the app to run the new version."
