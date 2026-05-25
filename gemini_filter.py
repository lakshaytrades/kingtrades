"""
gemini_filter.py — AI-powered news sentiment filter for trade signals.

Scores a trade signal against recent news using Gemini AI (or Claude as fallback).
Runs EARLY in signal generation pipeline to add/subtract up to 15 points from
the signal score before any score gates are applied.

Usage:
    from gemini_filter import get_gemini_filter

    filter_ = get_gemini_filter()
    delta, reason = filter_.score_signal("RELIANCE", "LONG", 72.5)
    adjusted_score = 72.5 + delta
"""

import json
import os
import time
from typing import Optional


class GeminiNewsFilter:
    """
    Scores trade signals against recent news sentiment via Gemini AI.

    Returns a delta between -15 and +15:
      - Negative delta: bad news detected for this trade direction → penalise.
      - Positive delta: news tailwind detected → reward.
      - Zero: neutral or unavailable.

    Results are cached per symbol for 5 minutes to avoid redundant API calls.
    All exceptions are caught and return (0.0, "api error") so this module
    NEVER blocks a trade.
    """

    # Cache entry: (delta, reason, timestamp)
    _cache: dict[str, tuple[float, str, float]] = {}
    _CACHE_TTL_SECONDS = 300  # 5 minutes
    _API_TIMEOUT_SECONDS = 4

    def __init__(self) -> None:
        self._gemini_client = None
        self._anthropic_client = None
        self._backend: str = "none"
        self._init_clients()

    # ------------------------------------------------------------------ #
    #  Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def _init_clients(self) -> None:
        """Try Gemini first, fall back to Anthropic Claude."""
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            try:
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=gemini_key)
                self._gemini_client = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    generation_config={"max_output_tokens": 100, "temperature": 0.1},
                )
                self._backend = "gemini"
                return
            except Exception:
                pass  # fall through to Anthropic

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            try:
                import anthropic  # type: ignore
                self._anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
                self._backend = "anthropic"
                return
            except Exception:
                pass

        self._backend = "none"

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def score_signal(
        self, symbol: str, direction: str, signal_score: float
    ) -> tuple[float, str]:
        """
        Score a trade signal against recent news.

        Args:
            symbol:       Stock ticker, e.g. "RELIANCE" or "INFY".
            direction:    "LONG" or "SHORT".
            signal_score: Current composite signal score (used for context only).

        Returns:
            (score_delta, reason)
              score_delta: float in [-15, +15] — add to signal_score.
              reason:      One-sentence human-readable explanation.
        """
        if self._backend == "none":
            return (0.0, "no api key")

        # ── Cache lookup ──────────────────────────────────────────────── #
        cache_key = f"{symbol.upper()}:{direction.upper()}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # ── Call AI backend ───────────────────────────────────────────── #
        try:
            if self._backend == "gemini":
                delta, reason = self._call_gemini(symbol, direction)
            else:
                delta, reason = self._call_anthropic(symbol, direction)
        except TimeoutError:
            return (0.0, "timeout")
        except Exception:
            return (0.0, "api error")

        # Clamp delta to [-15, +15]
        delta = max(-15.0, min(15.0, float(delta)))

        self._set_cache(cache_key, delta, reason)
        return (delta, reason)

    # ------------------------------------------------------------------ #
    #  Backend calls                                                        #
    # ------------------------------------------------------------------ #

    def _build_prompt(self, symbol: str, direction: str) -> str:
        return (
            f"Symbol: {symbol}. Proposed trade: {direction}. "
            f"Is there any recent news that would HURT this trade? "
            f"Reply with ONLY valid JSON, no markdown: "
            f'{{ "delta": <integer -15 to 15>, "reason": "<one sentence>" }} '
            f"Negative delta = bad news for this trade. Positive = tailwind."
        )

    def _parse_response(self, raw: str) -> tuple[float, str]:
        """Parse AI JSON response. Returns (delta, reason) or raises ValueError."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        data = json.loads(text)
        delta = float(data["delta"])
        reason = str(data.get("reason", "")).strip()
        if not reason:
            reason = "no reason provided"
        return (delta, reason)

    def _call_gemini(self, symbol: str, direction: str) -> tuple[float, str]:
        """Call Gemini API with a hard timeout enforced via threading."""
        import concurrent.futures

        prompt = self._build_prompt(symbol, direction)

        def _request() -> tuple[float, str]:
            response = self._gemini_client.generate_content(prompt)
            return self._parse_response(response.text)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_request)
            try:
                return future.result(timeout=self._API_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                raise TimeoutError("Gemini API call timed out")
            except Exception as exc:
                raise exc

    def _call_anthropic(self, symbol: str, direction: str) -> tuple[float, str]:
        """Call Anthropic Claude API with a hard timeout enforced via threading."""
        import concurrent.futures

        prompt = self._build_prompt(symbol, direction)

        def _request() -> tuple[float, str]:
            message = self._anthropic_client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            return self._parse_response(raw)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_request)
            try:
                return future.result(timeout=self._API_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                raise TimeoutError("Anthropic API call timed out")
            except Exception as exc:
                raise exc

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                        #
    # ------------------------------------------------------------------ #

    def _get_cached(self, key: str) -> Optional[tuple[float, str]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        delta, reason, ts = entry
        if time.monotonic() - ts > self._CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return (delta, reason)

    def _set_cache(self, key: str, delta: float, reason: str) -> None:
        self._cache[key] = (delta, reason, time.monotonic())


# ────────────────────────────────────────────────────────────────────────── #
#  Singleton                                                                   #
# ────────────────────────────────────────────────────────────────────────── #

_instance: Optional[GeminiNewsFilter] = None


def get_gemini_filter() -> GeminiNewsFilter:
    """Return the module-level singleton GeminiNewsFilter instance."""
    global _instance
    if _instance is None:
        _instance = GeminiNewsFilter()
    return _instance
