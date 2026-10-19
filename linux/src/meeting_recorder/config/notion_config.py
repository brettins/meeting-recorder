"""
Notion workspace configuration.

A Notion integration token is scoped to one workspace, so filing meetings into
more than one workspace means holding more than one token. The non-secret half
of each workspace — a name, the database to file into, which files property
takes the audio — lives in config.json under ``notion_workspaces``; the token
lives in the Secret Service keyring keyed by the workspace's id.

Everything here is pure and gi-free. Malformed entries are dropped rather than
raised on: a hand-edited config file must not stop the app from starting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CONFIG_KEY = "notion_workspaces"

KEYRING_PURPOSE_PREFIX = "notion-token"

_ID_SAFE = re.compile(r"[^a-z0-9]+")

# A Notion id is 32 hex characters, shown either bare or hyphenated as a UUID.
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@dataclass(frozen=True)
class NotionWorkspace:
    """One Notion workspace this app can file meetings into."""

    id: str
    name: str
    database_id: str
    files_property: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "database_id": self.database_id,
            "files_property": self.files_property,
        }


def keyring_purpose(workspace_id: str) -> str:
    """The keyring attribute identifying one workspace's integration token."""
    return f"{KEYRING_PURPOSE_PREFIX}-{workspace_id}"


def keyring_label(name: str) -> str:
    """Human-readable keyring label, so the entry is recognisable in Seahorse."""
    return f"Meeting Recorder — Notion token ({name})"


def workspace_id_for(name: str, taken: list[str] | None = None) -> str:
    """Derive a stable, filesystem-and-keyring-safe id from a workspace *name*.

    The id keys the token in the keyring, so it must not change when the name
    is edited — callers keep the original id and only pass a name here when
    first adding a workspace. A collision gets a numeric suffix.
    """
    base = _ID_SAFE.sub("-", name.strip().lower()).strip("-") or "workspace"
    existing = set(taken or [])
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def extract_database_id(text: str) -> str:
    """Pull a database id out of a pasted Notion URL, or return it unchanged.

    Users copy a link, not an id. The id is the trailing hex run of the last
    path segment; the ``?v=`` view id that follows is another 32-hex value, so
    the query string is cut before searching. Text with no id in it comes back
    untouched, for the caller to reject with its own message.
    """
    candidate = text.strip()
    if not candidate:
        return ""
    segment = candidate.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    uuid = _UUID.search(segment)
    if uuid:
        return uuid.group(0).replace("-", "")
    hexes = _HEX32.findall(segment)
    return hexes[-1] if hexes else candidate


def normalise_workspace(raw: Any, taken: list[str] | None = None) -> NotionWorkspace | None:
    """Build a workspace from a config entry, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    database_id = extract_database_id(str(raw.get("database_id") or ""))
    if not name or not database_id:
        return None
    identifier = str(raw.get("id") or "").strip() or workspace_id_for(name, taken)
    return NotionWorkspace(
        id=identifier,
        name=name,
        database_id=database_id,
        files_property=str(raw.get("files_property") or "").strip(),
    )


def load_workspaces(config: dict[str, Any]) -> list[NotionWorkspace]:
    """Read every usable workspace out of *config*, in order, ids deduplicated."""
    entries = config.get(CONFIG_KEY)
    if not isinstance(entries, list):
        return []
    workspaces: list[NotionWorkspace] = []
    taken: list[str] = []
    for raw in entries:
        workspace = normalise_workspace(raw, taken)
        if workspace is None:
            continue
        if workspace.id in taken:
            continue
        taken.append(workspace.id)
        workspaces.append(workspace)
    return workspaces


def dump_workspaces(workspaces: list[NotionWorkspace]) -> list[dict[str, str]]:
    """Serialise workspaces for writing back to config.json."""
    return [w.to_dict() for w in workspaces]


def add_workspace(
    workspaces: list[NotionWorkspace], name: str, database_id: str, files_property: str = ""
) -> list[NotionWorkspace]:
    """Append a new workspace, giving it an id no existing workspace holds."""
    identifier = workspace_id_for(name, [w.id for w in workspaces])
    added = NotionWorkspace(
        id=identifier,
        name=name.strip(),
        database_id=extract_database_id(database_id),
        files_property=files_property.strip(),
    )
    return [*workspaces, added]


def update_workspace(
    workspaces: list[NotionWorkspace], workspace_id: str, **changes: str
) -> list[NotionWorkspace]:
    """Replace the named workspace's editable fields, keeping its id.

    The id is deliberately not editable: it keys the token in the keyring, and
    renaming a workspace must not orphan the token that belongs to it.
    """
    updated: list[NotionWorkspace] = []
    for workspace in workspaces:
        if workspace.id != workspace_id:
            updated.append(workspace)
            continue
        database = changes.get("database_id")
        updated.append(
            NotionWorkspace(
                id=workspace.id,
                name=changes.get("name", workspace.name).strip() or workspace.name,
                database_id=(extract_database_id(database) if database else workspace.database_id),
                files_property=changes.get("files_property", workspace.files_property).strip(),
            )
        )
    return updated


def remove_workspace(workspaces: list[NotionWorkspace], workspace_id: str) -> list[NotionWorkspace]:
    """Drop the named workspace. The caller deletes its keyring token."""
    return [w for w in workspaces if w.id != workspace_id]


def find_workspace(workspaces: list[NotionWorkspace], workspace_id: str) -> NotionWorkspace | None:
    """Look one workspace up by id."""
    for workspace in workspaces:
        if workspace.id == workspace_id:
            return workspace
    return None
