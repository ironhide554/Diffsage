"""Thin Gemini wrapper: structured output, client-side rate limiting, retries."""

from __future__ import annotations

import os
import threading
import time
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

RETRYABLE = {429, 500, 502, 503, 504}
# gemini-2.5-flash only allowed ~20 requests/day on the free tier and its -lite sibling is closed to
# new users, so default to the current Flash-Lite, which answers our structured output correctly.
DEFAULT_MODEL = "gemini-3.5-flash-lite"


class QuotaExceeded(RuntimeError):
    """The daily request quota is used up; retrying within this run cannot help."""


def _is_daily_quota(exc: errors.APIError) -> bool:
    return getattr(exc, "code", None) == 429 and "PerDay" in str(exc)


# Shared across client instances so concurrent PR reviews respect one rate limit.
_throttle_lock = threading.Lock()
_last_call = 0.0


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        rpm: int | None = None,
        max_retries: int = 4,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (get a free key at aistudio.google.com/apikey).")
        self._client = genai.Client(api_key=key)
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        rpm = rpm or int(os.environ.get("GEMINI_RPM", "12"))
        self._min_interval = 60.0 / max(rpm, 1)
        self._max_retries = max_retries
        self.calls = 0

    def _throttle(self) -> None:
        """Keep under the free-tier requests-per-minute limit."""
        global _last_call
        with _throttle_lock:
            wait = _last_call + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()

    def generate(self, system: str, prompt: str, schema: type[T]) -> T | None:
        """Return a validated instance of `schema`, or None if the model never produced one."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.1,
        )
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                self.calls += 1
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
            except errors.APIError as exc:
                if _is_daily_quota(exc):
                    raise QuotaExceeded(
                        f"Daily Gemini quota for {self.model} is used up (free tier is small). "
                        "Wait for the reset, set GEMINI_MODEL to another model, or use a paid key."
                    ) from exc
                code = getattr(exc, "code", None)
                if code in RETRYABLE and attempt < self._max_retries:
                    time.sleep(min(2 ** (attempt + 1), 30))
                    continue
                raise
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, schema):
                return parsed
            try:
                return schema.model_validate_json(response.text or "")
            except (ValidationError, ValueError):
                if attempt < self._max_retries:
                    time.sleep(min(2**attempt, 8))
                    continue
        return None
