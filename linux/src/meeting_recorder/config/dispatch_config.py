"""Dispatch settings: the server URL, a default company, and where the API token lives."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

from .keyring_store import KeyringStore

URL_KEY = "dispatch_url"
TOKEN_KEY = "dispatch_api_token"
COMPANY_KEY = "dispatch_company"

KEYRING_PURPOSE = "dispatch-api-token"
KEYRING_LABEL = "Meeting Recorder — Dispatch API token"
KEYRING_SENTINEL = "@keyring"


class SecretStore(Protocol):
    def available(self) -> bool: ...
    def get(self) -> str | None: ...
    def set(self, value: str) -> bool: ...
    def delete(self) -> bool: ...


def token_store() -> KeyringStore:
    """The keyring entry holding the Dispatch token, separate from the Gemini key."""
    return KeyringStore(purpose=KEYRING_PURPOSE, label=KEYRING_LABEL)


def normalise_url(text: str) -> str:
    """A Dispatch base URL without a trailing slash, or empty when it is not one."""
    candidate = text.strip().rstrip("/")
    if not candidate:
        return ""
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    if parts.scheme.lower() == "http" and not is_local(parts.hostname or ""):
        return ""
    return candidate


def is_local(host: str) -> bool:
    """Whether a host never leaves this machine, so a token may cross it unencrypted."""
    return host.lower() in ("localhost", "127.0.0.1", "::1")


def url_error(text: str) -> str | None:
    """Why *text* cannot be used as the Dispatch URL, or None."""
    if not text.strip():
        return None
    if not normalise_url(text):
        return (
            "The Dispatch URL needs https://, like https://zed.tailefa891.ts.net. "
            "Plain http:// only works for this machine, because the token would travel unencrypted."
        )
    return None


def token_warning(token: str) -> str | None:
    """A format problem with a pasted token, or None."""
    if any(c.isspace() for c in token.strip()):
        return "The Dispatch token contains a space or line break; the paste picked up extra text."
    return None


def is_configured(config: dict[str, Any]) -> bool:
    """Whether filing can be offered: a URL and a token, without touching the keyring."""
    return bool(normalise_url(str(config.get(URL_KEY) or ""))) and bool(config.get(TOKEN_KEY))


def read_token(config: dict[str, Any], store: SecretStore) -> str:
    """The API token, resolving the keyring sentinel. Empty when there is none."""
    stored = str(config.get(TOKEN_KEY) or "")
    if stored == KEYRING_SENTINEL:
        return store.get() or ""
    return stored


def token_for_config(token: str, store: SecretStore) -> str:
    """Store a new token, in the keyring when reachable, and return config.json's value for it."""
    token = token.strip()
    if not token:
        return ""
    if store.available() and store.set(token):
        return KEYRING_SENTINEL
    return token


def forget_token(store: SecretStore) -> str:
    """Remove the token from the keyring and return the empty config value."""
    if store.available():
        store.delete()
    return ""
