"""
llm_reasoner.py — US Momentum Alpaca AI Bot
Claude AI Final Reasoning Gate

Before executing any 95+ score trade, ask Claude to reason about the setup.
This is the final sanity check — a second pair of institutional eyes on every
elite trade before real money is committed.

Uses claude-haiku-4-5 (fast, cheap ~$0.001/call) with prompt caching.
Falls back gracefully if API unavailable — never blocks a trade on API errors.

Gate logic:
  score >= 95 AND GEMINI_API_KEY or ANTHROPIC_API_KEY set → call LLM
  LLM returns: GO / NO_GO / REDUCE_SIZE
  NO_GO  → skip the trade entirely
  REDUCE → halve the position size
  GO     → proceed as normal (default if API unavailable)
"""

import json
import logging
import os
from typing import Dict, Optional, Tuple

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)

MIN_SCORE_FOR_LLM = 95.0   # Only consult LLM for elite setups
LLM_TIMEOUT       = 8      # Seconds — don't slow down execution


class LLMReasoner:
    """
    Calls Claude (Anthropic API) to reason about a trade setup.
    Prompt-cached system context — only the trade details change per call.
    """

    SYSTEM_PROMPT = """You are a veteran US equity intraday trader with 20+ years experience.
You review trade setups and give fast, decisive verdicts.

You will receive a trade setup summary and must respond with ONLY a JSON object:
{"verdict": "GO"|"NO_GO"|"REDUCE_SIZE", "reason": "one sentence max", "confidence": 0-100}

Rules for NO_GO:
- Setup contradicts broader market direction (e.g. LONG when SPY is down >1%)
- RSI extremely overbought/oversold (>80 or <20) for momentum trades
- Trade is against the daily trend (price below 20d SMA for LONG)
- Volume is not confirming the move
- The pattern combination is contradictory (e.g. breakout signal but RSI diverging bearishly)

Rules for REDUCE_SIZE:
- Setup is good but market regime is uncertain
- Near a key resistance/support level that could reject
- Conflicting signals from different timeframes

Rules for GO:
- All factors align: direction, volume, momentum, regime, time-of-day
- Strong catalyst backing the move
- Clear invalidation level (tight SL)

Be decisive. Don't overthink. Answer in under 2 seconds mentally."""

    def __init__(self):
        self._client    = None
        self._enabled   = False
        self._calls     = 0
        self._no_go     = 0
        self._reduce    = 0
        self._init_client()

    def _init_client(self):
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                self._client  = anthropic.Anthropic(api_key=api_key)
                self._enabled = True
                logger.info(f"[{format_ist_timestamp()}] LLMReasoner: Claude API ready")
                return
        except ImportError as _e:
            logger.debug(f"[suppressed] anthropic not installed: {_e}")

        # Fallback: try Gemini via google-generativeai
        try:
            import google.generativeai as genai
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                genai.configure(api_key=api_key)
                self._gemini_model = genai.GenerativeModel("gemini-1.5-flash")
                self._enabled      = True
                self._use_gemini   = True
                logger.info(f"[{format_ist_timestamp()}] LLMReasoner: Gemini API ready")
                return
        except ImportError as _e:
            logger.debug(f"[suppressed] google-generativeai not installed: {_e}")

        logger.info(f"[{format_ist_timestamp()}] LLMReasoner: No API key — running without LLM gate")

    def evaluate(self, signal_summary: Dict) -> Tuple[str, str, float]:
        """
        Evaluate a trade setup.
        Returns: (verdict, reason, confidence)
          verdict: "GO" | "NO_GO" | "REDUCE_SIZE"
        """
        if not self._enabled:
            return "GO", "LLM not configured", 70.0

        try:
            prompt = self._build_prompt(signal_summary)
            raw    = self._call_llm(prompt)
            if not raw:
                return "GO", "LLM timeout/error — proceeding", 60.0

            data       = json.loads(raw)
            verdict    = data.get("verdict", "GO").upper()
            reason     = data.get("reason", "")
            confidence = float(data.get("confidence", 70))

            if verdict not in ("GO", "NO_GO", "REDUCE_SIZE"):
                verdict = "GO"

            self._calls += 1
            if verdict == "NO_GO":
                self._no_go += 1
                logger.info(f"[{format_ist_timestamp()}] LLM NO_GO: {signal_summary.get('symbol')} — {reason}")
            elif verdict == "REDUCE_SIZE":
                self._reduce += 1
                logger.info(f"[{format_ist_timestamp()}] LLM REDUCE: {signal_summary.get('symbol')} — {reason}")

            return verdict, reason, confidence

        except Exception as e:
            logger.debug(f"LLMReasoner error: {e}")
            return "GO", f"LLM error ({e})", 60.0

    def _build_prompt(self, s: Dict) -> str:
        return f"""TRADE SETUP:
Symbol: {s.get('symbol')}
Direction: {s.get('direction')} ({"BUY" if s.get('direction')=='LONG' else "SELL"})
Score: {s.get('score', 0):.0f}/100 (Grade: {s.get('grade', 'B')})
Entry: ${s.get('entry', 0):.2f} | SL: ${s.get('sl', 0):.2f} | T1: ${s.get('t1', 0):.2f} | T2: ${s.get('t2', 0):.2f}
R:R Ratio: {s.get('rr_ratio', 0):.1f}:1
Patterns: {', '.join(s.get('patterns', [])[:5])}
RSI: {s.get('rsi', 50):.0f} | MACD: {'bullish' if s.get('macd_bull') else 'bearish'} | ADX: {s.get('adx', 0):.0f}
Volume ratio: {s.get('volume_ratio', 1.0):.1f}x
Above VWAP: {s.get('above_vwap', False)}
SPY today: {s.get('spy_change', 0):+.1f}%
Regime: {s.get('regime', 'UNKNOWN')}
Time: {s.get('time_et', '')} ET
Catalyst: {s.get('catalyst', 'None')}
Market breadth: {s.get('breadth_score', 50):.0f}/100

Respond ONLY with JSON: {{"verdict": "GO"|"NO_GO"|"REDUCE_SIZE", "reason": "...", "confidence": 0-100}}"""

    def _call_llm(self, prompt: str) -> Optional[str]:
        import signal as _signal

        def _timeout(sig, frame):
            raise TimeoutError

        _signal.signal(_signal.SIGALRM, _timeout)
        _signal.alarm(LLM_TIMEOUT)

        try:
            if hasattr(self, "_use_gemini") and self._use_gemini:
                resp = self._gemini_model.generate_content(
                    self.SYSTEM_PROMPT + "\n\n" + prompt,
                    generation_config={"max_output_tokens": 80, "temperature": 0.1},
                )
                _signal.alarm(0)
                text = resp.text.strip()
                # Extract JSON from response
                start = text.find("{")
                end   = text.rfind("}") + 1
                return text[start:end] if start >= 0 else None

            else:
                resp = self._client.messages.create(
                    model   = "claude-haiku-4-5-20251001",
                    max_tokens = 80,
                    temperature = 0.1,
                    system  = [{"type": "text", "text": self.SYSTEM_PROMPT,
                                "cache_control": {"type": "ephemeral"}}],
                    messages = [{"role": "user", "content": prompt}],
                )
                _signal.alarm(0)
                return resp.content[0].text.strip()

        except TimeoutError:
            logger.debug("LLMReasoner: timeout")
            return None
        except Exception as e:
            logger.debug(f"LLMReasoner call error: {e}")
            return None
        finally:
            try:
                _signal.alarm(0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

    def get_stats(self) -> str:
        if self._calls == 0:
            return "LLMReasoner: 0 calls"
        no_go_rate = self._no_go / self._calls * 100
        return (f"LLMReasoner: {self._calls} calls | "
                f"NO_GO={self._no_go} ({no_go_rate:.0f}%) | "
                f"REDUCE={self._reduce}")


# ── SINGLETON ─────────────────────────────────────────────────────────────────

_reasoner: Optional[LLMReasoner] = None


def get_llm_reasoner() -> LLMReasoner:
    global _reasoner
    if _reasoner is None:
        _reasoner = LLMReasoner()
    return _reasoner
