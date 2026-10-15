"""
Implementation of the Google Gemini AI provider. It handles the specific requirements of the Gemini
API, including uploading audio files, polling for processing status, and executing both
transcription and summarization prompts with appropriate model configurations.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config.defaults import (
    GEMINI_TRANSCRIPTION_PROMPT,
    SUMMARIZATION_PROMPT,
)
from ...core.retry import retry_on_transient
from ...services.recording_splitter import _run as run_command
from ..audio_parts import (
    blocked_note,
    can_halve,
    extract_parts,
    halve,
    named_speakers,
    part_prompt,
    plan_parts,
    shift_timestamps,
    strip_preamble,
)
from ..part_cache import PartCache
from ..silence_map import Part
from ..upload_audio import probe_duration

logger = logging.getLogger(__name__)

# Polling interval when waiting for Gemini file processing
_POLL_INTERVAL = 3  # seconds
_POLL_TIMEOUT = 300  # 5 minutes

# Temperature for transcription: 0 = deterministic, sticks closely to spoken words
_TRANSCRIPTION_TEMPERATURE = 0

# Gemini 3 models reason before answering, and those thinking tokens are drawn
# from the same budget as the output. Transcription is mechanical, so the
# reasoning adds nothing and is measurably expensive: an unbounded run spent
# ~2,000 thinking tokens to emit ~135 tokens of transcript, which exhausts the
# output limit on a long recording and fails the job as "truncated".
#
# A small cap rather than 0: several models (gemini-pro-latest,
# gemini-flash-lite-latest) reject a budget of 0 outright, while every model the
# app offers accepts 128. Capping keeps one code path for all of them.
_TRANSCRIPTION_THINKING_BUDGET = 128


# Request the maximum output tokens so long transcripts are never silently truncated.
# The API caps this at the model's own limit, so it is safe to set high.
_MAX_OUTPUT_TOKENS = 65_536


# Bounds on the Files API calls, which otherwise wait forever on a dead connection.
_FILE_CALL_TIMEOUT_MS = 60_000


class RecitationBlocked(RuntimeError):
    """Gemini's copyright filter refused to return the text."""


_BLOCKED = {"transcription": ("transcription", "audio"), "summarization": ("summary", "transcript")}


def _require_text(response: Any, context: str) -> str:
    """Extract text from a GenerateContentResponse, raising clearly if empty or truncated."""
    # Log token usage and finish_reason for diagnostics
    _truncation_error: RuntimeError | None = None
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage:
            logger.info(
                "%s token usage — input: %s, output: %s, total: %s",
                context,
                getattr(usage, "prompt_token_count", "?"),
                getattr(usage, "candidates_token_count", "?"),
                getattr(usage, "total_token_count", "?"),
            )
        candidate = response.candidates[0] if response.candidates else None
        if candidate:
            finish_reason = getattr(candidate, "finish_reason", None)
            logger.info("%s finish_reason: %s", context, finish_reason)
            if "RECITATION" in str(getattr(finish_reason, "name", finish_reason)):
                step, source = _BLOCKED.get(context, (context, "transcript"))
                _truncation_error = RecitationBlocked(
                    f"Gemini's copyright filter blocked the {step}. It flagged part of the "
                    f"{source} as copyrighted text, such as song lyrics or a passage read aloud. "
                    f"Try again; it does not always block the same {source}."
                )
            try:
                from google.genai import types

                if finish_reason == types.FinishReason.MAX_TOKENS:
                    _truncation_error = RuntimeError(
                        f"Gemini stopped early during {context}: the response hit the "
                        f"{_MAX_OUTPUT_TOKENS:,}-token output limit. Split the recording into "
                        "shorter parts, or pick a model with a larger output limit in "
                        "Settings \u2192 Models."
                    )
            except ImportError:
                pass
    except Exception:
        pass
    if _truncation_error:
        raise _truncation_error

    text = response.text
    if not text:
        feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(f"Gemini returned no text for {context}. prompt_feedback={feedback}")
    return str(text).strip()


def _wrap_timeout(exc: Exception, context: str, timeout_ms: int) -> Exception:
    """Convert httpx/httpcore timeout errors into a readable RuntimeError."""
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        minutes = timeout_ms // 60_000
        return RuntimeError(
            f"Gemini did not respond within {minutes} minutes ({context}). "
            "The audio may be too long, or Gemini may be overloaded. "
            "Try again, or use a shorter recording."
        )
    return exc


class GeminiProvider:
    """
    Handles both transcription (audio → text) and summarization (text → notes).

    When used for transcription: uploads audio, polls until ACTIVE, transcribes.
    When used for summarization: sends text prompt.
    The pipeline checks for the dual-call optimization.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        transcription_prompt: str = "",
        summarization_prompt: str = "",
        timeout_minutes: int = 3,
        part_cache: PartCache | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        # Fall back to built-in defaults if no custom prompt is configured.
        self._transcription_prompt = transcription_prompt or GEMINI_TRANSCRIPTION_PROMPT
        self._summarization_prompt = summarization_prompt or SUMMARIZATION_PROMPT
        self._generate_timeout_ms = timeout_minutes * 60_000
        self._part_cache = part_cache or PartCache()
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise ImportError(
                    "google-genai is not installed. Run: pip install google-genai"
                ) from None
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Transcribe an audio file, in parts when it is long."""
        parts = plan_parts(probe_duration(audio_path, run_command))
        if len(parts) < 2:
            return self._transcribe_file(audio_path, self._transcription_prompt, on_status)

        self._part_cache.prune()
        with tempfile.TemporaryDirectory(prefix="meeting-parts-") as scratch:
            try:
                files = extract_parts(audio_path, parts, Path(scratch), run_command)
            except Exception as exc:
                logger.warning("Could not cut %s into parts (%s); sending whole", audio_path, exc)
                return self._transcribe_file(audio_path, self._transcription_prompt, on_status)

            segments: list[str] = []
            speakers: list[str] = []
            saved: list[str] = []
            transcribed = 0
            for index, (part, path) in enumerate(zip(parts, files, strict=True), 1):
                label = f"Part {index} of {len(parts)}"

                def part_status(message: str, label: str = label) -> None:
                    if on_status:
                        on_status(f"{label}: {message}")

                key = self._part_cache.key(
                    path, self._model, self._transcription_prompt, f"{index}/{len(parts)}"
                )
                text = self._part_cache.get(key)
                if text is not None:
                    part_status("Using the transcript saved from the last attempt")
                    count = 1
                else:
                    prompt = part_prompt(self._transcription_prompt, index, len(parts), speakers)
                    text, count = self._transcribe_span(
                        path, part, prompt, part_status, Path(scratch)
                    )
                    if count:
                        self._part_cache.put(key, text)
                if count:
                    saved.append(key)
                    transcribed += count
                speakers = [*speakers, *(n for n in named_speakers(text) if n not in speakers)]
                segments.append(text)

        if not transcribed:
            raise RecitationBlocked(
                "Gemini's copyright filter blocked every part of the recording. "
                "Try again; it does not always block the same audio."
            )
        self._part_cache.discard(saved)
        return "\n\n".join(segments)

    def _transcribe_span(
        self,
        path: Path,
        span: Part,
        prompt: str,
        on_status: Callable[[str], None],
        scratch: Path,
    ) -> tuple[str, int]:
        """Transcribe one span; if the copyright filter blocks it, try each half separately.

        Returns the text with absolute timestamps and how many spans were transcribed.
        """
        try:
            text = self._transcribe_file(path, prompt, on_status)
        except RecitationBlocked:
            logger.warning("%s-%s blocked by the copyright filter", span.start, span.end)
            if not can_halve(span):
                return blocked_note(span), 0
            try:
                halves = halve(path, span, Path(tempfile.mkdtemp(dir=scratch)), run_command)
            except Exception as exc:
                logger.warning("Could not halve a blocked span (%s)", exc)
                return blocked_note(span), 0
            results = [
                self._transcribe_span(half_path, half, prompt, on_status, scratch)
                for half, half_path in halves
            ]
            return "\n\n".join(t for t, _ in results), sum(n for _, n in results)
        return shift_timestamps(strip_preamble(text), span.start), 1

    def _transcribe_file(
        self,
        audio_path: Path,
        prompt: str,
        on_status: Callable[[str], None] | None,
    ) -> str:
        """Upload one audio file to the Gemini Files API and transcribe it."""
        client = self._get_client()

        if on_status:
            on_status("Uploading audio to Gemini…")

        logger.info("Uploading %s to Gemini Files API", audio_path)
        uploaded = retry_on_transient(
            lambda: client.files.upload(
                file=str(audio_path),
                # mime_type must be specified explicitly; the SDK does not infer it from
                # the file extension for audio files, and omitting it causes a 400 error.
                config={
                    "mime_type": "audio/mpeg",
                    "http_options": {"timeout": self._generate_timeout_ms},
                },
            ),
            description="Gemini file upload",
        )

        try:
            # After upload, Google's servers transcode and analyse the audio. The file
            # state transitions PROCESSING → ACTIVE before it can be referenced in prompts.
            uploaded = self._wait_for_active(client, uploaded, on_status)

            if on_status:
                on_status("Transcribing with Gemini…")

            try:
                response = retry_on_transient(
                    lambda: client.models.generate_content(
                        model=self._model,
                        contents=[uploaded, prompt],
                        config={
                            "temperature": _TRANSCRIPTION_TEMPERATURE,
                            "max_output_tokens": _MAX_OUTPUT_TOKENS,
                            "thinking_config": {"thinking_budget": _TRANSCRIPTION_THINKING_BUDGET},
                            "http_options": {"timeout": self._generate_timeout_ms},
                        },
                    ),
                    description="Gemini transcription",
                )
            except Exception as exc:
                raise _wrap_timeout(exc, "transcription", self._generate_timeout_ms) from exc
            return _require_text(response, "transcription")
        finally:
            self._delete_remote(client, uploaded)

    def _delete_remote(self, client: Any, file_obj: Any) -> None:
        """Remove an uploaded file from the Files API; failure only costs storage."""
        name = getattr(file_obj, "name", None)
        if not name:
            return
        try:
            client.files.delete(
                name=name, config={"http_options": {"timeout": _FILE_CALL_TIMEOUT_MS}}
            )
        except Exception as exc:
            logger.debug("Could not delete %s from Gemini: %s", name, exc)

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Summarize transcript text using Gemini."""
        client = self._get_client()

        if on_status:
            on_status("Summarizing with Gemini…")

        try:
            prompt = self._summarization_prompt.format(transcript=transcript)
        except KeyError:
            # User removed {transcript} from their custom prompt — append it manually.
            prompt = self._summarization_prompt + f"\n\nTRANSCRIPT:\n{transcript}"
        try:
            response = retry_on_transient(
                lambda: client.models.generate_content(
                    model=self._model,
                    contents=[prompt],
                    config={
                        "max_output_tokens": _MAX_OUTPUT_TOKENS,
                        "http_options": {"timeout": self._generate_timeout_ms},
                    },
                ),
                description="Gemini summarization",
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "summarization", self._generate_timeout_ms) from exc
        return _require_text(response, "summarization")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_for_active(
        self, client: Any, file_obj: Any, on_status: Callable[[str], None] | None
    ) -> Any:
        """Poll until the uploaded file reaches ACTIVE state."""
        from google.genai import types

        deadline = time.time() + _POLL_TIMEOUT
        while True:
            state = file_obj.state

            if state == types.FileState.ACTIVE:
                return file_obj
            if state in (types.FileState.FAILED, types.FileState.STATE_UNSPECIFIED):
                raise RuntimeError(f"Gemini file processing failed (state={state})")

            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for Gemini file to become active")

            state_label = state.value if state else "unknown"
            if on_status:
                on_status(f"Waiting for Gemini to process audio… ({state_label})")

            time.sleep(_POLL_INTERVAL)
            file_obj = client.files.get(
                name=file_obj.name,
                config={"http_options": {"timeout": _FILE_CALL_TIMEOUT_MS}},
            )
