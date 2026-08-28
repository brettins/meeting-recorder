"""
Discovers the Gemini models available to the configured API key.

The model catalogue moves faster than releases do: models are added, and older
ones are retired server-side and start returning 404 while still sitting in a
hardcoded list. This service asks the API what actually exists, so the settings
dropdown reflects reality instead of whatever was current at build time.

Results are cached on disk because the settings dialog is opened far more often
than the catalogue changes. Every failure path falls back to the static
``GEMINI_MODELS`` list, so the dropdown is never empty and the app still works
offline or before a key is set.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..config.defaults import GEMINI_MODELS

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60

# Models that reach generateContent but are not general-purpose text/audio
# models. Matched as substrings against the bare model id.
_EXCLUDED_SUBSTRINGS = (
    "image",
    "tts",
    "embedding",
    "vision",
    "lyria",
    "banana",
    "veo",
    "robotics",
    "computer-use",
    "deep-research",
    "antigravity",
    "customtools",
)

# Only Gemini models are offered; Gemma and other hosted families do not accept
# audio input, which is the app's primary use for the transcription dropdown.
_REQUIRED_PREFIX = "gemini-"


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "meeting-recorder" / "gemini_models.json"


def is_offered(model_id: str) -> bool:
    """True if *model_id* is a general-purpose Gemini text/audio model."""
    if not model_id.startswith(_REQUIRED_PREFIX):
        return False
    return not any(bad in model_id for bad in _EXCLUDED_SUBSTRINGS)


def _sort_key(model_id: str) -> tuple[int, str]:
    """Sort the "-latest" aliases first, then everything else alphabetically."""
    return (0 if model_id.endswith("-latest") else 1, model_id)


def filter_models(model_ids: list[str]) -> list[str]:
    """Keep only general-purpose Gemini models, in display order."""
    return sorted({m for m in model_ids if is_offered(m)}, key=_sort_key)


def read_cache(now: float | None = None, path: Path | None = None) -> list[str] | None:
    """Return cached model ids if the cache exists and is fresh, else None."""
    cache_file = path or _cache_path()
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = raw.get("fetched_at")
    models = raw.get("models")
    if not isinstance(fetched_at, int | float) or not isinstance(models, list):
        return None
    current = time.time() if now is None else now
    if current - float(fetched_at) > CACHE_TTL_SECONDS:
        return None
    ids = [m for m in models if isinstance(m, str)]
    return ids or None


def write_cache(model_ids: list[str], now: float | None = None, path: Path | None = None) -> None:
    """Persist *model_ids* with a timestamp. Failures are non-fatal."""
    cache_file = path or _cache_path()
    payload = {
        "fetched_at": time.time() if now is None else now,
        "models": model_ids,
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write the Gemini model cache: %s", exc)


def fetch_models(api_key: str, client_factory: Any = None) -> list[str]:
    """Ask the API which models support generateContent. Raises on failure."""
    if client_factory is None:
        from google import genai

        client_factory = genai.Client
    client = client_factory(api_key=api_key)
    ids: list[str] = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = str(getattr(model, "name", "")).removeprefix("models/")
        if name:
            ids.append(name)
    return filter_models(ids)


def available_models(
    api_key: str,
    *,
    force_refresh: bool = False,
    client_factory: Any = None,
) -> list[str]:
    """Best-effort list of selectable Gemini models.

    Order of preference: fresh cache, then a live fetch, then the static
    ``GEMINI_MODELS`` fallback. Never raises and never returns an empty list.
    """
    if not force_refresh:
        cached = read_cache()
        if cached:
            return cached

    if api_key:
        try:
            models = fetch_models(api_key, client_factory=client_factory)
            if models:
                write_cache(models)
                return models
        except Exception as exc:
            logger.warning("Could not fetch the Gemini model list, using the built-in one: %s", exc)

    return list(GEMINI_MODELS)


def ensure_selected(model_ids: list[str], current: str) -> list[str]:
    """Return *model_ids* guaranteed to contain *current*.

    A model the user has already configured must stay selectable even when the
    API no longer lists it — otherwise opening settings would silently switch
    them to a different model.
    """
    if not current or current in model_ids:
        return list(model_ids)
    return [current, *model_ids]
