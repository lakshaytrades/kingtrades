"""
claude_supervisor.py — Claude AI Trade Supervisor

Claude reviews every trade signal BEFORE execution.
It reads the full signal context + recent news and gives a final
YES / NO with reasoning sent to Telegram.

Required .env:
  ANTHROPIC_API_KEY = your key from console.anthropic.com
  (free to get, pay per use — each review costs ~$0.001)

If ANTHROPIC_API_KEY is not set, supervisor is skipped and bot
trades using rule-based signals only (no degradation).
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _build_prompt(signal_data: dict, news_headlines: list[str]) -> str:
    sym      = signal_data.get("symbol", "?")
    direction = signal_data.get("direction", "?")
    score    = signal_data.get("score", 0)
    entry    = signal_data.get("entry_price", 0)
    sl       = signal_data.get("stop_loss", 0)
    target   = signal_data.get("target", 0)
    rsi      = signal_data.get("rsi", 0)
    macd     = signal_data.get("macd_hist", 0)
    vwap_dev = signal_data.get("vwap_deviation_pct", 0)
    vol_ratio = signal_data.get("volume_ratio", 0)
    patterns = signal_data.get("patterns", [])
    mtf      = signal_data.get("mtf_alignment", "unknown")
    session  = signal_data.get("session", "unknown")
    nifty_trend = signal_data.get("nifty_trend", "unknown")

    risk_reward = round((target - entry) / max(entry - sl, 0.01), 2) if direction == "LONG" else round((entry - target) / max(sl - entry, 0.01), 2)

    news_block = "\n".join(f"  - {h}" for h in news_headlines[:5]) if news_headlines else "  No recent news found."

    return f"""You are supervising a live NSE intraday trading bot. Evaluate this trade signal and decide YES or NO.

TRADE SIGNAL
============
Symbol:      {sym}
Direction:   {direction}
Entry:       ₹{entry}
Stop Loss:   ₹{sl}
Target:      ₹{target}
R:R Ratio:   {risk_reward:.1f}:1
Signal Score: {score}/100

TECHNICAL INDICATORS
====================
RSI(14):         {rsi:.1f}
MACD Histogram:  {macd:.4f}
VWAP Deviation:  {vwap_dev:+.2f}%
Volume Ratio:    {vol_ratio:.1f}x average
MTF Alignment:   {mtf}
Session:         {session}
Nifty Trend:     {nifty_trend}

PATTERNS DETECTED
=================
{chr(10).join(f'  - {p}' for p in patterns) if patterns else '  None'}

RECENT NEWS FOR {sym}
=====================
{news_block}

DECISION RULES (NSE intraday, 18-year expert system):
- Approve LONG if: RSI 30-55, MACD turning positive, price near/above VWAP, volume surge, no negative news
- Approve SHORT if: RSI 45-70, MACD turning negative, price below VWAP, volume surge, no positive news
- REJECT if: R:R < 1.5, contradictory timeframes, major news risk, low volume, midday chop
- REJECT if: news contains earnings, RBI decision, FII selling (for LONG), or major negative event

Respond ONLY in this exact JSON format:
{{"approved": true/false, "confidence": 0-100, "reason": "one sentence", "risk_note": "one sentence or null"}}"""


def review_signal(signal_data: dict, news_headlines: list[str] = None) -> dict:
    """
    Ask Claude to review a trade signal before execution.

    Returns:
        {"approved": bool, "confidence": int, "reason": str, "risk_note": str, "skipped": bool}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"approved": True, "confidence": 0, "reason": "Supervisor not configured", "skipped": True}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = _build_prompt(signal_data, news_headlines or [])

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",   # Fast + cheap — perfect for real-time decisions
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        # Extract JSON even if Claude adds extra text
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        result = json.loads(raw[start:end])

        logger.info(
            f"[Claude Supervisor] {signal_data.get('symbol')} {signal_data.get('direction')} "
            f"→ {'✅ APPROVED' if result.get('approved') else '❌ REJECTED'} "
            f"({result.get('confidence', 0)}%) — {result.get('reason', '')}"
        )
        result["skipped"] = False
        return result

    except Exception as e:
        logger.warning(f"[Claude Supervisor] API error — allowing trade: {e}")
        return {"approved": True, "confidence": 0, "reason": f"Supervisor error: {e}", "skipped": True}


def format_telegram_review(symbol: str, direction: str, review: dict) -> str:
    """Format Claude's review as a Telegram message."""
    if review.get("skipped"):
        return ""
    approved = review.get("approved", True)
    icon     = "✅" if approved else "❌"
    conf     = review.get("confidence", 0)
    reason   = review.get("reason", "")
    risk     = review.get("risk_note", "")
    lines = [
        f"{icon} <b>Claude Supervisor — {symbol} {direction}</b>",
        f"Decision: {'APPROVED' if approved else 'REJECTED'} ({conf}% confidence)",
        f"Reason: {reason}",
    ]
    if risk:
        lines.append(f"⚠️ Risk: {risk}")
    return "\n".join(lines)
