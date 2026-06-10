"""
daily_intelligence.py — Daily Intelligence Reports for KingTrades

Sends three things every day automatically:

  9:25 AM ET  — MORNING BRIEF
    "Good morning. Today: VIX 14, SPY up 0.3%. Market is BULLISH.
     Watching: NVDA, AAPL, GOOG. Target today: $500."

  Every trade — WHY EXPLANATION
    "BOUGHT 10 NVDA at $875.
     WHY: RSI=65 (strong momentum), MACD just crossed up, volume 2.3×
     normal (institutions buying), ML says 72% win probability.
     Stop $862, Target $910 (4% up, 2.9:1 reward/risk)"

  4:30 PM ET  — EOD REPORT
    "Today: 3 trades, 2 wins (+$450), 1 loss (-$110). Net: +$340.
     Monthly progress: $340 / $3,000 target = 11%. On track.
     Errors: 5 signals filtered (normal). 1 news blackout (protected you)."

Runs via crontab — no Claude Code needed:
  25 9 * * 1-5   cd /root/kingtrades && python3 daily_intelligence.py morning
  30 16 * * 1-5  cd /root/kingtrades && python3 daily_intelligence.py eod

Also imported by main.py to log WHY for each trade.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)

DECISIONS_FILE = DATA_DIR / "trade_decisions.json"  # WHY records per trade
MONTHLY_FILE   = DATA_DIR / "monthly_pnl.json"       # running monthly P&L

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send(msg: str):
    # US bot disabled by owner — never send unless explicitly re-enabled
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return
    try:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except Exception:
            pass
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat  = os.getenv("TELEGRAM_CHAT_ID",   "")
        if not token or not chat:
            print(msg)
            return True
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        return bool(getattr(resp, "ok", False))
    except Exception as e:
        logger.debug(f"telegram: {e}")
        return False


# ── Plain English WHY Explainer ───────────────────────────────────────────────

def explain_trade(signal) -> str:
    """
    Convert a signal object into plain English explanation of WHY the bot took it.
    Called immediately after order placement.

    Returns a multi-line string ready to send to Telegram.
    """
    sym   = getattr(signal, "symbol",       "?")
    dir_  = getattr(signal, "direction",    "LONG")
    score = getattr(signal, "signal_score", 0)
    entry = getattr(signal, "entry_price",  0)
    sl    = getattr(signal, "stop_loss",    0)
    t1    = getattr(signal, "target_1",     0)
    qty   = getattr(signal, "quantity",     0)
    grade = getattr(signal, "quality_grade","B")
    patterns = getattr(signal, "patterns",  []) or []
    rationale = getattr(signal, "rationale","") or ""

    # Get indicators from signal if available
    ind = getattr(signal, "indicators", None)
    rsi  = float(getattr(ind, "rsi",          50) or 50) if ind else 50.0
    macd = float(getattr(ind, "macd_hist",     0)  or 0)  if ind else 0.0
    vr   = float(getattr(ind, "volume_ratio",  1)  or 1)  if ind else 1.0
    adx  = float(getattr(ind, "adx",          25)  or 25) if ind else 25.0
    vwap = float(getattr(ind, "vwap",          0)  or 0)  if ind else 0.0
    atr  = float(getattr(ind, "atr",           0)  or 0)  if ind else 0.0
    rr   = abs(t1 - entry) / max(abs(entry - sl), 0.01) if sl and t1 and entry else 0

    # Build reason list in plain English
    reasons = []

    # RSI interpretation
    if dir_ == "LONG":
        if 50 < rsi < 70:
            reasons.append(f"RSI={rsi:.0f} — momentum building, not yet overbought")
        elif rsi >= 70:
            reasons.append(f"RSI={rsi:.0f} — strong overbought momentum (breakout mode)")
        elif 35 < rsi <= 50:
            reasons.append(f"RSI={rsi:.0f} — recovering from oversold, turning up")
    else:
        if 30 < rsi < 50:
            reasons.append(f"RSI={rsi:.0f} — momentum falling, not yet oversold")
        elif rsi <= 30:
            reasons.append(f"RSI={rsi:.0f} — strong oversold momentum (breakdown mode)")

    # MACD
    if macd > 0 and dir_ == "LONG":
        reasons.append(f"MACD histogram positive ({macd:+.3f}) — buyers stronger than sellers")
    elif macd < 0 and dir_ == "SHORT":
        reasons.append(f"MACD histogram negative ({macd:+.3f}) — sellers in control")

    # Volume
    if vr >= 2.5:
        reasons.append(f"Volume {vr:.1f}× normal — institutional money moving in")
    elif vr >= 1.5:
        reasons.append(f"Volume {vr:.1f}× normal — above-average interest")
    elif vr >= 1.2:
        reasons.append(f"Volume {vr:.1f}× normal — mild accumulation")

    # ADX trend strength
    if adx >= 30:
        reasons.append(f"ADX={adx:.0f} — strong trend, momentum carries")
    elif adx >= 20:
        reasons.append(f"ADX={adx:.0f} — trending market, setup valid")

    # VWAP
    if vwap > 0 and entry > 0:
        dist_pct = (entry - vwap) / vwap * 100
        if dir_ == "LONG" and 0 < dist_pct < 0.5:
            reasons.append(f"Price just above VWAP +{dist_pct:.2f}% — institutional fair value reclaim")
        elif dir_ == "SHORT" and -0.5 < dist_pct < 0:
            reasons.append(f"Price just below VWAP {dist_pct:.2f}% — rejected at fair value")

    # Patterns
    for p in patterns[:2]:
        readable = p.replace("_", " ").title()
        reasons.append(f"Chart pattern: {readable}")

    # ML score
    if score >= 85:
        reasons.append(f"Score {score:.0f}/100 — bot's highest conviction (top 15%)")
    elif score >= 75:
        reasons.append(f"Score {score:.0f}/100 — high conviction signal")
    elif score >= 65:
        reasons.append(f"Score {score:.0f}/100 — solid setup")

    # Grade
    grade_explain = {
        "A+": "Elite setup — maximum size",
        "A":  "Strong setup — full size",
        "B+": "Good setup — normal size",
        "B":  "Decent setup — normal size",
        "C":  "Marginal setup — reduced size",
    }.get(grade, "")

    dir_word = "BOUGHT" if dir_ == "LONG" else "SHORTED"
    action_emoji = "🟢" if dir_ == "LONG" else "🔴"

    lines = [
        f"🇺🇸 {action_emoji} *[US BOT] {dir_word} {qty} {sym} @ ${entry:.2f}*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "*WHY the bot took this trade:*",
    ]
    for i, r in enumerate(reasons, 1):
        lines.append(f"  {i}. {r}")

    if not reasons:
        lines.append(f"  • {rationale or 'Multi-factor momentum signal confirmed'}")

    lines += [
        "",
        f"*Plan:*",
        f"  Stop loss:  `${sl:.2f}`  ({abs(entry-sl)/entry*100:.1f}% away)",
        f"  Target:     `${t1:.2f}`  ({abs(t1-entry)/entry*100:.1f}% profit)",
        f"  Risk/Reward: `{rr:.1f}:1`",
        f"  Grade: `{grade}` — {grade_explain}",
    ]

    # Save decision record for EOD report
    _save_decision(sym, dir_, entry, sl, t1, reasons, score, grade)

    return "\n".join(lines)


def _save_decision(symbol, direction, entry, sl, target, reasons, score, grade):
    """Persist WHY record so EOD report can reference it."""
    try:
        decisions = []
        if DECISIONS_FILE.exists():
            try:
                decisions = json.loads(DECISIONS_FILE.read_text())
            except Exception:
                decisions = []
        decisions.append({
            "time": datetime.now(ET).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop": sl,
            "target": target,
            "score": score,
            "grade": grade,
            "reasons": reasons,
            "pnl": None,  # filled on close
        })
        # Keep last 100 decisions
        decisions = decisions[-100:]
        DECISIONS_FILE.write_text(json.dumps(decisions, indent=2))
    except Exception as e:
        logger.debug(f"save_decision: {e}")


def record_trade_close(symbol: str, pnl: float):
    """Call when a position closes — updates the WHY record with actual P&L."""
    try:
        if not DECISIONS_FILE.exists():
            return
        decisions = json.loads(DECISIONS_FILE.read_text())
        # Update most recent matching symbol with no pnl yet
        for d in reversed(decisions):
            if d.get("symbol") == symbol and d.get("pnl") is None:
                d["pnl"] = pnl
                break
        DECISIONS_FILE.write_text(json.dumps(decisions, indent=2))
    except Exception as e:
        logger.debug(f"record_trade_close: {e}")


# ── Target Tracking ───────────────────────────────────────────────────────────

def _get_monthly_target() -> float:
    try:
        import config as c
        cap = float(getattr(c, "MAX_DAILY_CAPITAL", 0) or 0)
        if cap <= 0:
            # Try Alpaca account equity
            try:
                from alpaca.trading.client import TradingClient
                from dotenv import load_dotenv
                load_dotenv(BASE_DIR / ".env")
                client = TradingClient(
                    os.getenv("ALPACA_API_KEY",""),
                    os.getenv("ALPACA_SECRET_KEY",""),
                    paper=True
                )
                cap = float(client.get_account().equity)
            except Exception:
                cap = 93000.0  # fallback from known paper balance
        pct = float(getattr(c, "MONTHLY_TARGET_PCT", 20.0))
        return round(cap * pct / 100, 2)
    except Exception:
        return 18600.0  # 20% of $93k


def _get_monthly_pnl() -> float:
    """Sum P&L from trade decisions this calendar month."""
    try:
        if not DECISIONS_FILE.exists():
            return 0.0
        decisions = json.loads(DECISIONS_FILE.read_text())
        month_start = date.today().replace(day=1).isoformat()
        total = 0.0
        for d in decisions:
            if d.get("time", "") >= month_start and d.get("pnl") is not None:
                total += d["pnl"]
        return total
    except Exception:
        return 0.0


def _target_bar(pct: float, width: int = 20) -> str:
    """Visual progress bar. pct 0-100."""
    filled = min(int(width * pct / 100), width)
    bar = "█" * filled + "░" * (width - filled)
    return f"`[{bar}]`"


# ── Log Scanning ──────────────────────────────────────────────────────────────

def _scan_today_errors() -> List[str]:
    """Return plain-English descriptions of today's notable log events."""
    explanations = []
    try:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        log_candidates = [
            LOG_DIR / "bot_output.log",
            LOG_DIR / f"trading_{today}.log",
        ]
        lines = []
        for p in log_candidates:
            if p.exists():
                import subprocess
                r = subprocess.run(["tail", "-n", "2000", str(p)],
                                   capture_output=True, text=True)
                lines = r.stdout.splitlines()
                break

        # Count events and errors
        score_filtered = sum(1 for l in lines if "Final score" in l and "skipping" in l)
        news_blackout  = sum(1 for l in lines if "NEWS BLACKOUT" in l)
        restarts       = sum(1 for l in lines if "Bot starting" in l)
        api_errors     = sum(1 for l in lines if "403" in l or "401" in l or "rate limit" in l.lower())

        if score_filtered > 0:
            explanations.append(
                f"{score_filtered} signals scanned but score too low — bot was selective (normal)"
            )
        if news_blackout > 0:
            explanations.append(
                f"{news_blackout} trades paused for news event — bot protected your capital"
            )
        if restarts > 1:
            explanations.append(
                f"Bot restarted {restarts-1}× today — watchdog handled it automatically"
            )
        if api_errors > 0:
            explanations.append(
                f"{api_errors} API errors — may affect data quality (check Alpaca status)"
            )
    except Exception:
        pass
    return explanations


def _get_today_decisions() -> List[Dict]:
    try:
        if not DECISIONS_FILE.exists():
            return []
        decisions = json.loads(DECISIONS_FILE.read_text())
        today = date.today().isoformat()
        return [d for d in decisions if d.get("time", "").startswith(today)]
    except Exception:
        return []


# ── Morning Brief ─────────────────────────────────────────────────────────────

def send_morning_brief():
    """9:25 AM ET — pre-market intelligence message."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass

    now = datetime.now(ET).strftime("%a %b %d")
    monthly_target = _get_monthly_target()
    monthly_pnl    = _get_monthly_pnl()
    month_pct      = min(100, monthly_pnl / monthly_target * 100) if monthly_target else 0

    # Market context
    vix_str = spy_str = regime_str = ""
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX").history(period="2d")["Close"].iloc[-1]
        spy = yf.Ticker("SPY").history(period="2d")
        spy_price = spy["Close"].iloc[-1]
        spy_prev  = spy["Close"].iloc[-2]
        spy_chg   = (spy_price / spy_prev - 1) * 100
        vix_str   = f"VIX `{vix:.1f}` {'⚠️ HIGH' if vix > 25 else ('🟡 ELEVATED' if vix > 18 else '✅ CALM')}"
        spy_str   = f"SPY `${spy_price:.0f}` `{spy_chg:+.2f}%`"
        regime_str = "🟢 BULLISH" if spy_chg > 0.3 and vix < 20 else \
                     "🔴 BEARISH" if spy_chg < -0.5 else "🟡 NEUTRAL"
    except Exception:
        vix_str = spy_str = "Loading..."
        regime_str = "🟡 NEUTRAL"

    msg = (
        f"🇺🇸 ☀️ *US BOT — Morning Brief*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {now} | NYSE/NASDAQ | Alpaca\n"
        f"{'─' * 30}\n"
        f"  {spy_str}\n"
        f"  {vix_str}\n"
        f"  Regime: {regime_str}\n"
        f"{'─' * 30}\n"
        f"  Monthly target: `${monthly_target:,.0f}`\n"
        f"  Earned so far:  `${monthly_pnl:+,.0f}`\n"
        f"  Progress: {_target_bar(month_pct)} `{month_pct:.0f}%`\n"
        f"{'─' * 30}\n"
        f"  Bot is *ACTIVE* — scanning every 60s\n"
        f"  You will be notified on every trade\n"
        f"  No action needed from you today\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    _send(msg)
    logger.info("Morning brief sent")


# ── EOD Report ────────────────────────────────────────────────────────────────

def send_eod_report():
    """4:30 PM ET — end-of-day full report."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass

    today_decisions = _get_today_decisions()
    monthly_target  = _get_monthly_target()
    monthly_pnl     = _get_monthly_pnl()
    month_pct       = min(100, monthly_pnl / monthly_target * 100) if monthly_target else 0

    # Today's stats
    closed = [d for d in today_decisions if d.get("pnl") is not None]
    open_  = [d for d in today_decisions if d.get("pnl") is None]
    wins   = [d for d in closed if d["pnl"] > 0]
    losses = [d for d in closed if d["pnl"] <= 0]
    today_pnl  = sum(d["pnl"] for d in closed)
    avg_win    = sum(d["pnl"] for d in wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(d["pnl"] for d in losses) / len(losses) if losses else 0
    win_rate   = len(wins) / len(closed) * 100 if closed else 0

    # Trade list with WHY
    trade_lines = []
    for d in closed[-8:]:
        pnl     = d["pnl"]
        pnl_str = f"+${pnl:.0f}" if pnl > 0 else f"-${abs(pnl):.0f}"
        emoji   = "✅" if pnl > 0 else "❌"
        reason  = d["reasons"][0] if d.get("reasons") else "momentum signal"
        trade_lines.append(
            f"  {emoji} {d['direction']} {d['symbol']} @ ${d['entry']:.2f} → `{pnl_str}`"
            f"\n       _Why: {reason}_"
        )

    if open_:
        for d in open_:
            trade_lines.append(
                f"  🔵 {d['direction']} {d['symbol']} — still open"
            )

    # Errors/events
    errors = _scan_today_errors()

    # On-track assessment
    days_in_month   = date.today().day
    days_total      = 30
    expected_by_now = monthly_target * days_in_month / days_total
    if monthly_pnl >= expected_by_now * 0.9:
        track_str = "✅ ON TRACK"
    elif monthly_pnl >= expected_by_now * 0.6:
        track_str = "🟡 SLIGHTLY BEHIND"
    else:
        track_str = "🔴 BEHIND — bot is adjusting thresholds"

    now = datetime.now(ET).strftime("%a %b %d")
    pnl_emoji = "🟢" if today_pnl >= 0 else "🔴"

    msg_parts = [
        f"🇺🇸 📋 *US BOT — EOD Report*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {now} | NYSE/NASDAQ | Alpaca",
        f"{'─' * 30}",
        f"  {pnl_emoji} Today P&L: `${today_pnl:+,.0f}`",
        f"  Trades: `{len(closed)}` closed",
    ]

    if closed:
        msg_parts += [
            f"  Wins: `{len(wins)}`  Losses: `{len(losses)}`  Win rate: `{win_rate:.0f}%`",
            f"  Avg win: `+${avg_win:.0f}`  Avg loss: `${avg_loss:.0f}`",
        ]

    msg_parts += [
        f"{'─' * 30}",
        f"  *Monthly Target: ${monthly_target:,.0f}*",
        f"  Earned: `${monthly_pnl:+,.0f}` ({month_pct:.0f}%)",
        f"  {_target_bar(month_pct)} {track_str}",
    ]

    if trade_lines:
        msg_parts += [f"{'─' * 30}", "*Trades taken today:*"] + trade_lines

    if errors:
        msg_parts += [f"{'─' * 30}", "*What happened in background:*"]
        for e in errors:
            msg_parts.append(f"  • {e}")

    if not closed and not open_:
        msg_parts += [
            f"{'─' * 30}",
            "  No trades today — conditions weren't right",
            "  Bot was scanning but no setup met the standard",
            "  _This is normal — protecting capital beats forcing trades_",
        ]

    msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    msg_parts.append("_US Bot resets for tomorrow. No action needed._")

    _send("\n".join(msg_parts))
    logger.info("EOD report sent")


# ── CLI Entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eod"
    logging.basicConfig(level=logging.INFO)
    if cmd == "morning":
        send_morning_brief()
    elif cmd == "eod":
        send_eod_report()
    else:
        print(f"Usage: python3 daily_intelligence.py [morning|eod]")
