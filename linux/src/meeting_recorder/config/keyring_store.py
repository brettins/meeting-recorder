"""
Secret Service (keyring) storage for the Gemini API key.

When a D-Bus Secret Service is available (GNOME Keyring, KWallet with the
secrets portal, KeePassXC), the API key lives there instead of in plaintext
config.json. Everything degrades gracefully: if ``secretstorage`` is not
importable or the service is unreachable, ``available()`` is False and the
chmod-600 config.json remains the storage, exactly as before.

The secretstorage module is injectable so the store is unit-testable without
a D-Bus session.
"""

from __future__ import annotations

import logging
import os
import pwd
from typing import Any

logger = logging.getLogger(__name__)

_ATTRIBUTES = {"application": "meeting-recorder", "purpose": "gemini-api-key"}
_LABEL = "Meeting Recorder — Gemini API key"

_PREVIOUS_ATTRIBUTES = {"application": "meeting-recorder", "purpose": "gemini-api-key-previous"}
_PREVIOUS_LABEL = "Meeting Recorder — Gemini API key (previous)"

_UNSET = object()


def is_sandboxed_home(env: dict[str, str], account_home: str) -> bool:
    """True if HOME has been redirected away from the login account's home.

    The Secret Service is reached over the session bus, so it is shared by
    every process in the login session no matter where HOME points. A test
    harness that redirects HOME to a scratch directory therefore still writes
    to the real keyring, and a save there overwrites the user's live key. When
    the two disagree, treat the keyring as out of bounds and let the caller
    fall back to the chmod-600 config file inside the redirected HOME.
    """
    home = env.get("HOME")
    if not home or not account_home:
        return False
    return os.path.normpath(home) != os.path.normpath(account_home)


def _account_home() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        return ""


class KeyringStore:
    """Stores one secret (the Gemini API key) in the session keyring."""

    def __init__(
        self,
        secretstorage_module: Any = _UNSET,
        env: dict[str, str] | None = None,
        account_home: Any = _UNSET,
    ) -> None:
        if secretstorage_module is _UNSET:
            try:
                import secretstorage

                secretstorage_module = secretstorage
            except ImportError:
                secretstorage_module = None
        if account_home is _UNSET:
            account_home = _account_home()
        if is_sandboxed_home(dict(os.environ) if env is None else env, account_home):
            logger.warning(
                "HOME is redirected away from %s; leaving the session keyring alone "
                "and using the config file instead.",
                account_home,
            )
            secretstorage_module = None
        self._ss = secretstorage_module

    # ------------------------------------------------------------------

    def available(self) -> bool:
        """True if the Secret Service can be reached right now.

        Deliberately does NOT unlock the collection: this is called from
        startup/save paths, and triggering a synchronous password prompt from
        a mere availability probe would be intrusive. get()/set() unlock when
        the secret is actually needed.
        """
        if self._ss is None:
            return False
        try:
            conn = self._ss.dbus_init()
            try:
                self._ss.get_default_collection(conn)
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Secret Service unavailable: %s", exc)
            return False

    def get(self) -> str | None:
        """Return the stored key, or None if absent/unavailable."""
        if self._ss is None:
            return None
        try:
            conn, collection = self._open()
            try:
                for item in collection.search_items(_ATTRIBUTES):
                    secret = item.get_secret()
                    return bytes(secret).decode("utf-8")
                return None
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not read API key from keyring: %s", exc)
            return None

    def set(self, value: str) -> bool:
        """Store *value*, replacing any previous entry. Returns success."""
        if self._ss is None:
            return False
        try:
            conn, collection = self._open()
            try:
                self._keep_previous(collection, value)
                collection.create_item(_LABEL, _ATTRIBUTES, value.encode("utf-8"), replace=True)
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not store API key in keyring: %s", exc)
            return False

    def delete(self) -> bool:
        """Remove the stored key (e.g. when the user clears it). Returns success."""
        if self._ss is None:
            return False
        try:
            conn, collection = self._open()
            try:
                self._keep_previous(collection, "")
                for item in collection.search_items(_ATTRIBUTES):
                    item.delete()
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not delete API key from keyring: %s", exc)
            return False

    def get_previous(self) -> str | None:
        """Return the key this store last replaced, or None if there is none."""
        if self._ss is None:
            return None
        try:
            conn, collection = self._open()
            try:
                for item in collection.search_items(_PREVIOUS_ATTRIBUTES):
                    return bytes(item.get_secret()).decode("utf-8")
                return None
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not read the replaced API key from keyring: %s", exc)
            return None

    # ------------------------------------------------------------------

    def _keep_previous(self, collection: Any, incoming: str) -> None:
        """Park the key that is about to be replaced under a second entry.

        A save writes over the stored key with no undo, and a key pasted by
        mistake — or by another process sharing this session bus — otherwise
        destroys the working one. Keeping one generation back costs a single
        extra keyring entry and makes that recoverable.
        """
        try:
            for item in collection.search_items(_ATTRIBUTES):
                current = bytes(item.get_secret()).decode("utf-8")
                if not current or current == incoming:
                    return
                collection.create_item(
                    _PREVIOUS_LABEL,
                    _PREVIOUS_ATTRIBUTES,
                    current.encode("utf-8"),
                    replace=True,
                )
                return
        except Exception as exc:
            logger.warning("Could not keep a copy of the replaced API key: %s", exc)

    def _open(self) -> tuple[Any, Any]:
        conn = self._ss.dbus_init()
        try:
            collection = self._ss.get_default_collection(conn)
            if collection.is_locked():
                collection.unlock()
        except Exception:
            # Close the connection on failure (e.g. the user dismissed the
            # unlock prompt) so repeated failures don't leak descriptors.
            conn.close()
            raise
        return conn, collection
