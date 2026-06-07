"""
trade_plan_broadcaster.py — Pre-trade intent broadcaster.

When a signal clears every risk gate and the bot is about to commit capital,
this module sends a single rich Telegram message that explains, in plain terms:
  • WHAT the bot is about to do (direction, entry, stop, targets, size, R:R)
  • WHY it believes in the trade (full OODA read — regime, conviction, Wyckoff,
    Elliott, smart-money, gamma, HMM, Kalman, macro/flow/sentiment)
  • The PLAN OF ACTION (entry style, stop, trailing rule, square-off)

It is intentionally separate from the post-fill `send_entry_alert` so the user
sees the bot's *intent and reasoning before* the order, not just after.

Design notes
------------
- Fully defensive: any failure here must NEVER block order execution. Every
  external call is wrapped, and the public function swallows all exceptions.
- Dedup: the same symbol+direction plan is not re-sent within DEDUP_TTL seconds
  to avoid spamming on repeated scan cycles.
- Broker-agnostic currency via broker.CURRENCY_SYMBOL (falls back to "$").
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from broker import CURRENCY_SYMBOL as _CUR
except Exception:
    _CUR = "$"

# symbol|direction -> last broadcast monotonic timestamp
_LAST_SENT: dict[str, float] = {}
DEDUP_TTL = 300  # seconds — don't repeat the same plan within 5 minutes

_SEP = "━━━━━━━━━━━━━━━━━━━━"


def _fmt(v: float, nd: int = 2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except Exception:
        return str(v)


def _ist_clock() -> str:
    try:
        from utils import format_ist_timestamp
        return format_ist_timestamp()
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M:%S")


def _get_ooda_context(symbol: str):
    """Best-effort fetch of the cached OODAContext for this symbol."""
    try:
        from ooda_engine import get_engine
        return get_engine().get_context(symbol)
    except Exception as e:
        logger.debug(f"[TradePlan] OODA context unavailable for {symbol}: {e}")
        return None


def _signed(v: float, nd: int = 1) -> str:
    """Render a score with explicit +/- sign for readability."""
    try:
        v = float(v)
    except Exception:
        return str(v)
    return f"+{v:.{nd}f}" if v >= 0 else f"{v:.{nd}f}"


def _build_plan_text(signal, ctx) -> str:
    direction = getattr(signal, "direction", "?")
    symbol = getattr(signal, "symbol", "?")
    entry = float(getattr(signal, "entry_price", 0) or 0)
    stop = float(getattr(signal, "stop_loss", 0) or 0)
    t1 = float(getattr(signal, "target_1", 0) or 0)
    t2 = float(getattr(signal, "target_2", 0) or 0)
    qty = int(getattr(signal, "quantity", 0) or 0)
    score = float(getattr(signal, "signal_score", 0) or 0)
    grade = getattr(signal, "quality_grade", "B")
    size_mult = float(getattr(signal, "size_multiplier", 1.0) or 1.0)
    rr = float(getattr(signal, "risk_reward", 0) or 0)
    patterns = getattr(signal, "patterns", None) or []

    arrow = "🟢 GOING LONG" if direction == "LONG" else "🔴 GOING SHORT"
    risk_amt = abs(entry - stop) * qty if (entry and stop and qty) else 0.0
    stop_pct = (abs(entry - stop) / entry * 100) if entry else 0.0

    lines = [
        f"🎯 *TRADE PLAN — {symbol}*",
        _SEP,
        f"{arrow}  ·  Grade *{grade}*  ·  `{score:.0f}/100`",
    ]

    # ── OODA conviction header line ──
    if ctx is not None:
        conv = float(getattr(ctx, "conviction", 0.5) or 0.5)
        urgency = getattr(ctx, "urgency", "NORMAL")
        lines.append(
            f"Conviction *{conv * 100:.0f}%*  ·  Size `{size_mult:.2f}×`  ·  {urgency}"
        )

    # ── The setup ──
    lines += [
        _SEP,
        "*THE SETUP*",
        f"  Entry  `{_CUR}{_fmt(entry)}`",
        f"  Stop   `{_CUR}{_fmt(stop)}`  ({stop_pct:.2f}%)",
        f"  T1     `{_CUR}{_fmt(t1)}`  → book 40%",
        f"  T2     `{_CUR}{_fmt(t2)}`  → book 20%",
        f"  R:R    `{rr:.1f} : 1`  ·  Qty `{qty}`  ·  Risk `{_CUR}{_fmt(risk_amt, 0)}`",
    ]

    # ── Why (OODA read) ──
    if ctx is not None:
        regime = getattr(ctx, "regime", "UNKNOWN")
        regime_conf = float(getattr(ctx, "regime_confidence", 0.5) or 0.5)
        wyckoff = getattr(ctx, "wyckoff_phase", "UNKNOWN")
        elliott = getattr(ctx, "elliott_wave", "UNKNOWN")
        vsa = getattr(ctx, "vsa_signal", "NEUTRAL")
        hmm = getattr(ctx, "hmm_state", "UNKNOWN")
        kalman = float(getattr(ctx, "kalman_trend", 0.0) or 0.0)
        smart = float(getattr(ctx, "smart_money_score", 0.0) or 0.0)
        gamma = float(getattr(ctx, "gamma_score", 0.0) or 0.0)
        macro = float(getattr(ctx, "macro_score", 0.0) or 0.0)
        flow = float(getattr(ctx, "flow_score", 0.0) or 0.0)
        sentiment = float(getattr(ctx, "sentiment_score", 0.0) or 0.0)
        squeeze = float(getattr(ctx, "short_squeeze_score", 0.0) or 0.0)

        lines += [
            _SEP,
            "*WHY — OODA READ*",
            f"  Regime    `{regime}` (conf {regime_conf:.2f})",
            f"  Wyckoff   `{wyckoff}`  ·  Elliott `{elliott}`",
            f"  VSA       `{vsa}`  ·  HMM `{hmm}`",
            f"  Smart$    `{_signed(smart)}`  ·  Gamma `{_signed(gamma)}`  ·  Squeeze `{squeeze:.0f}`",
            f"  Macro     `{_signed(macro)}`  ·  Flow `{_signed(flow)}`  ·  Sent `{_signed(sentiment)}`",
            f"  Kalman    `{_signed(kalman)}` velocity",
        ]

    if patterns:
        lines.append(f"  Patterns  `{', '.join(str(p) for p in patterns[:4])}`")

    # ── Plan of action ──
    entry_style = "Enter NOW at market" if getattr(ctx, "urgency", "NORMAL") == "URGENT" \
        else "Enter at market on confirmation"
    lines += [
        _SEP,
        "*PLAN OF ACTION*",
        f"  → {entry_style}",
        f"  → Hard stop armed at `{_CUR}{_fmt(stop)}`",
        "  → Trail after +1×ATR, tighten to +0.5×ATR",
        "  → Square-off by 3:20 PM IST (no overnight)",
        _SEP,
        f"🕐 `{_ist_clock()}`",
    ]
    return "\n".join(lines)


def broadcast_trade_plan(signal, alerter=None) -> bool:
    """
    Send a pre-trade plan message. Returns True if a message was sent.

    Safe to call from the hot execution path — never raises.
    """
    try:
        symbol = getattr(signal, "symbol", None)
        direction = getattr(signal, "direction", None)
        if not symbol or not direction:
            return False

        # Dedup: skip if we broadcast this exact plan recently
        key = f"{symbol}|{direction}"
        now = time.monotonic()
        last = _LAST_SENT.get(key, 0.0)
        if now - last < DEDUP_TTL:
            return False

        ctx = _get_ooda_context(symbol)
        text = _build_plan_text(signal, ctx)

        sent = False
        if alerter is not None and hasattr(alerter, "_send"):
            sent = alerter._send(text, parse_mode="Markdown")
        else:
            # Fallback: direct daily_intelligence sender if no alerter handed in
            try:
                from daily_intelligence import _send as _di_send
                sent = bool(_di_send(text))
            except Exception:
                sent = False

        if sent:
            _LAST_SENT[key] = now
            logger.info(f"[TradePlan] Broadcast plan for {symbol} {direction}")
        return sent
    except Exception as e:
        logger.debug(f"[TradePlan] broadcast failed (non-fatal): {e}")
        return False
