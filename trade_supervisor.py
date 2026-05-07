"""
trade_supervisor.py — Free Trade Supervisor (replaces Claude supervisor)

Two-layer system, zero cost:
  Layer 1: Gemini AI  (google-generativeai, FREE tier: 1500 calls/day)
            — If GEMINI_API_KEY is set in .env
  Layer 2: Rule-based expert system (always runs as fallback)
            — 12 hard rules from 18 years of NSE trading experience
            — Zero API calls, zero cost, deterministic

Returns same interface as old claude_supervisor.py so nothing else breaks.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RULE-BASED EXPERT SYSTEM
# 12 rules distilled from 18 years of NSE intraday trading
# ─────────────────────────────────────────────────────────────────────────────

def _rule_based_review(signal_data: dict) -> dict:
    """
    Pure logic supervisor — no API, no cost, instant.
    Returns same format as Gemini/Claude review.
    """
    sym       = signal_data.get("symbol", "?")
    direction = signal_data.get("direction", "LONG")
    score     = signal_data.get("score", 0)
    entry     = signal_data.get("entry_price", 0)
    sl        = signal_data.get("stop_loss", 0)
    target    = signal_data.get("target", entry)
    rsi       = signal_data.get("rsi", 50)
    macd      = signal_data.get("macd_hist", 0)
    vwap_dev  = signal_data.get("vwap_deviation_pct", 0)
    vol_ratio = signal_data.get("volume_ratio", 1)
    mtf       = signal_data.get("mtf_alignment", "unknown")
    session   = signal_data.get("session", "NORMAL")
    nifty     = signal_data.get("nifty_trend", "neutral")

    sl_dist = abs(entry - sl)
    rr = round((abs(target - entry) / sl_dist), 2) if sl_dist > 0 else 0

    rejections = []
    warnings   = []
    confidence = 50

    # ── Rule 1: R:R must be at least 1.5 ─────────────────────────────────
    if rr < 1.5:
        rejections.append(f"R:R {rr:.1f} too low (min 1.5)")
    elif rr >= 2.5:
        confidence += 15
    elif rr >= 2.0:
        confidence += 8

    # ── Rule 2: Signal score gate ─────────────────────────────────────────
    if score < 72:
        rejections.append(f"Signal score {score:.0f} below minimum 72")
    elif score >= 90:
        confidence += 15
    elif score >= 82:
        confidence += 8

    # ── Rule 3: RSI must not be in danger zone ────────────────────────────
    if direction == "LONG":
        if rsi > 78:
            rejections.append(f"RSI {rsi:.0f} overbought for LONG (>78)")
        elif rsi > 70:
            warnings.append(f"RSI {rsi:.0f} elevated — reduce size")
        elif 40 <= rsi <= 60:
            confidence += 10   # Sweet spot: momentum without overextension
    else:  # SHORT
        if rsi < 22:
            rejections.append(f"RSI {rsi:.0f} oversold for SHORT (<22)")
        elif rsi < 30:
            warnings.append(f"RSI {rsi:.0f} low — reduce size")
        elif 40 <= rsi <= 60:
            confidence += 10

    # ── Rule 4: MACD histogram direction must match trade direction ────────
    if direction == "LONG" and macd < -0.05:
        rejections.append(f"MACD histogram {macd:.4f} negative — bearish for LONG")
    elif direction == "SHORT" and macd > 0.05:
        rejections.append(f"MACD histogram {macd:.4f} positive — bullish for SHORT")
    elif (direction == "LONG" and macd > 0.01) or (direction == "SHORT" and macd < -0.01):
        confidence += 8

    # ── Rule 5: Volume confirmation ───────────────────────────────────────
    if vol_ratio < 1.2:
        rejections.append(f"Volume ratio {vol_ratio:.1f}x too low — no institutional interest")
    elif vol_ratio >= 2.5:
        confidence += 12
    elif vol_ratio >= 1.8:
        confidence += 6

    # ── Rule 6: VWAP alignment ────────────────────────────────────────────
    if direction == "LONG" and vwap_dev < -2.5:
        rejections.append(f"Price {vwap_dev:+.1f}% too far below VWAP for LONG")
    elif direction == "SHORT" and vwap_dev > 2.5:
        rejections.append(f"Price {vwap_dev:+.1f}% too far above VWAP for SHORT")
    elif direction == "LONG" and 0 <= vwap_dev <= 1.5:
        confidence += 8   # Hugging VWAP from above — ideal LONG entry
    elif direction == "SHORT" and -1.5 <= vwap_dev <= 0:
        confidence += 8

    # ── Rule 7: Midday chop block ─────────────────────────────────────────
    if session in ("MIDDAY", "MIDDAY_CHOP"):
        rejections.append("Midday chop session — no momentum trades")

    # ── Rule 8: MTF alignment ─────────────────────────────────────────────
    if mtf in ("BEARISH", "CONFLICTING") and direction == "LONG":
        rejections.append(f"MTF alignment '{mtf}' conflicts with LONG")
    elif mtf in ("BULLISH", "CONFLICTING") and direction == "SHORT":
        rejections.append(f"MTF alignment '{mtf}' conflicts with SHORT")
    elif mtf in ("STRONG_BULLISH",) and direction == "LONG":
        confidence += 12
    elif mtf in ("STRONG_BEARISH",) and direction == "SHORT":
        confidence += 12

    # ── Rule 9: Nifty trend must not strongly oppose direction ────────────
    if nifty in ("STRONG_BEARISH", "BEARISH") and direction == "LONG":
        rejections.append("Nifty strongly bearish — avoid LONG")
    elif nifty in ("STRONG_BULLISH", "BULLISH") and direction == "SHORT":
        rejections.append("Nifty strongly bullish — avoid SHORT")
    elif (nifty in ("BULLISH", "STRONG_BULLISH") and direction == "LONG") or \
         (nifty in ("BEARISH", "STRONG_BEARISH") and direction == "SHORT"):
        confidence += 8

    # ── Rule 10: SL must be reasonable (not too tight, not too wide) ───────
    sl_pct = sl_dist / entry * 100 if entry > 0 else 0
    if sl_pct < 0.1:
        rejections.append(f"SL distance {sl_pct:.2f}% too tight — will be stopped out by noise")
    elif sl_pct > 3.0:
        rejections.append(f"SL distance {sl_pct:.2f}% too wide — risk too high per trade")

    # ── Rule 11: Entry and SL sanity check ───────────────────────────────
    if entry <= 0 or sl <= 0:
        rejections.append("Invalid entry or stop loss price")
    if direction == "LONG" and sl >= entry:
        rejections.append("SL above entry price for LONG — invalid signal")
    if direction == "SHORT" and sl <= entry:
        rejections.append("SL below entry price for SHORT — invalid signal")

    # ── Rule 12: Opening drive bonus ─────────────────────────────────────
    if session in ("OPENING_DRIVE", "POWER_HOUR"):
        confidence += 10   # Best momentum window

    # ── Final verdict ─────────────────────────────────────────────────────
    confidence = max(0, min(100, confidence))

    if rejections:
        return {
            "approved":   False,
            "confidence": confidence,
            "reason":     rejections[0],
            "risk_note":  "; ".join(rejections[1:]) if len(rejections) > 1 else None,
            "skipped":    False,
            "source":     "rules",
        }

    risk_note = warnings[0] if warnings else None
    return {
        "approved":   True,
        "confidence": confidence,
        "reason":     f"All 12 rules passed | R:R={rr:.1f} score={score:.0f} vol={vol_ratio:.1f}x",
        "risk_note":  risk_note,
        "skipped":    False,
        "source":     "rules",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI SUPERVISOR (free tier — 1500 calls/day)
# ─────────────────────────────────────────────────────────────────────────────

def _gemini_review(signal_data: dict, news_headlines: list) -> Optional[dict]:
    """
    Use Gemini (free tier) to review signal.
    Returns None on any failure so caller falls back to rules.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        )

        sym      = signal_data.get("symbol", "?")
        direction = signal_data.get("direction", "?")
        score    = signal_data.get("score", 0)
        entry    = signal_data.get("entry_price", 0)
        sl       = signal_data.get("stop_loss", 0)
        target   = signal_data.get("target", 0)
        rsi      = signal_data.get("rsi", 0)
        macd     = signal_data.get("macd_hist", 0)
        vwap_dev = signal_data.get("vwap_deviation_pct", 0)
        vol      = signal_data.get("volume_ratio", 0)
        mtf      = signal_data.get("mtf_alignment", "unknown")
        nifty    = signal_data.get("nifty_trend", "unknown")
        rr       = round(abs(target - entry) / max(abs(entry - sl), 0.01), 2)
        news_txt = "\n".join(f"- {h}" for h in news_headlines[:5]) or "No news."

        prompt = f"""NSE intraday trade signal review. Reply ONLY in JSON.

Signal: {sym} {direction} | Score:{score}/100 | R:R {rr}:1
Entry:₹{entry} SL:₹{sl} Target:₹{target}
RSI:{rsi:.0f} MACD:{macd:.4f} VWAP:{vwap_dev:+.1f}% Volume:{vol:.1f}x
MTF:{mtf} Nifty:{nifty}
News: {news_txt}

Rules: Reject if R:R<1.5, RSI overbought for direction, MACD opposing, volume<1.2x, bad news.
Approve if all indicators align, volume confirms, news neutral/positive.

JSON only: {{"approved":true/false,"confidence":0-100,"reason":"one sentence","risk_note":"one sentence or null"}}"""

        resp = model.generate_content(prompt)
        raw  = resp.text.strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
        result["skipped"] = False
        result["source"]  = "gemini"

        logger.info(
            f"[Supervisor/Gemini] {sym} {direction} → "
            f"{'APPROVED' if result.get('approved') else 'REJECTED'} "
            f"({result.get('confidence', 0)}%) — {result.get('reason', '')}"
        )
        return result

    except Exception as e:
        logger.debug(f"[Supervisor/Gemini] Failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC INTERFACE — same as old claude_supervisor.py
# ─────────────────────────────────────────────────────────────────────────────

def review_signal(signal_data: dict, news_headlines: list = None) -> dict:
    """
    Review a trade signal before execution.
    Tries Gemini first (free tier), falls back to rule-based system.

    Returns:
        {"approved": bool, "confidence": int, "reason": str,
         "risk_note": str|None, "skipped": bool, "source": str}
    """
    news = news_headlines or []

    # Layer 1: Gemini (free, ~0.1s latency)
    result = _gemini_review(signal_data, news)
    if result is not None:
        return result

    # Layer 2: Rule-based expert (instant, zero cost)
    return _rule_based_review(signal_data)


def format_telegram_review(symbol: str, direction: str, review: dict) -> str:
    """Format supervisor review as Telegram message."""
    if review.get("skipped"):
        return ""
    approved = review.get("approved", True)
    icon     = "✅" if approved else "❌"
    conf     = review.get("confidence", 0)
    reason   = review.get("reason", "")
    risk     = review.get("risk_note", "")
    source   = review.get("source", "rules")
    src_tag  = "🤖 Gemini" if source == "gemini" else "📋 Rules"

    lines = [
        f"{icon} <b>{src_tag} Supervisor — {symbol} {direction}</b>",
        f"Decision: {'APPROVED' if approved else 'REJECTED'} ({conf}% confidence)",
        f"Reason: {reason}",
    ]
    if risk:
        lines.append(f"⚠️ Risk: {risk}")
    return "\n".join(lines)
