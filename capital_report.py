"""
capital_report.py — Unified daily capital report for both bots.

Reads live balances and sends one Telegram message showing:
  US Bot  → Alpaca account balance + day P&L
  India Bot → Dhan/paper balance + day P&L
  Combined total

Run at:
  4:35 PM ET → after US market close
  4:05 PM IST → after India market close
  (both via crontab — see crontab entries at bottom of file)
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [REPORT] %(message)s")

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
ET  = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")


# ── Telegram ──────────────────────────────────────────────────────────────────

def _tg(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(f"[TELEGRAM] {msg}")
        return
    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "chat_id": chat,
            "text": msg,
            "parse_mode": "Markdown",
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, timeout=10
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


# ── US Bot — Alpaca balance ───────────────────────────────────────────────────

def _get_us_capital() -> dict:
    """Fetch live Alpaca balance. Returns dict with capital, pnl, trades, positions."""
    result = {"capital": 0.0, "pnl": 0.0, "trades": 0, "positions": 0, "source": "log"}
    try:
        api_key    = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID", "")
        api_secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY", "")
        paper      = os.getenv("ALPACA_PAPER", "true").lower() == "true"
        base_url   = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        if api_key and api_secret:
            import urllib.request
            req = urllib.request.Request(
                f"{base_url}/v2/account",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                result["capital"] = float(data.get("equity", 0) or 0)
                result["source"] = "live"
    except Exception as e:
        logger.debug(f"alpaca balance: {e}")

    # Parse today's P&L and trade count from log
    try:
        log_path = LOG_DIR / "bot_output.log"
        if log_path.exists():
            today = datetime.now(ET).strftime("%Y-%m-%d")
            lines = log_path.read_text(errors="ignore").splitlines()
            today_lines = [l for l in lines if today in l]
            for line in reversed(today_lines):
                # P&L line: "Daily P&L: $+123.45"
                m = re.search(r"Daily P&L.*?\$([+-]?[\d,.]+)", line)
                if m:
                    result["pnl"] = float(m.group(1).replace(",", ""))
                    break
            # Count closed trades
            result["trades"] = sum(1 for l in today_lines
                                   if ("WIN" in l or "LOSS" in l) and "P&L" in l)
            # Count WIN/LOSS for win rate
            wins = sum(1 for l in today_lines if "WIN" in l and "P&L" in l)
            result["wins"] = wins
            result["losses"] = result["trades"] - wins
    except Exception as e:
        logger.debug(f"us log parse: {e}")

    return result


# ── India Bot — Dhan/paper balance ────────────────────────────────────────────

def _get_india_capital() -> dict:
    """Fetch India bot capital. Tries Dhan API, falls back to log parse."""
    result = {"capital": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0,
              "source": "log", "mode": "paper"}

    # Try Dhan API
    try:
        client_id    = os.getenv("DHAN_CLIENT_ID", "")
        access_token = os.getenv("DHAN_ACCESS_TOKEN", "")
        if client_id and access_token:
            import urllib.request
            req = urllib.request.Request(
                "https://api.dhan.co/fundlimit",
                headers={
                    "access-token": access_token,
                    "client-id": client_id,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                result["capital"] = float(data.get("availabelBalance", 0) or 0)
                result["source"] = "live"
                result["mode"] = "live"
    except Exception as e:
        logger.debug(f"dhan balance: {e}")

    # Parse from India log regardless (for P&L and trade count)
    try:
        log_path = LOG_DIR / "india_output.log"
        if log_path.exists():
            today = datetime.now(IST).strftime("%Y-%m-%d")
            lines = log_path.read_text(errors="ignore").splitlines()
            today_lines = [l for l in lines if today in l]

            for line in reversed(today_lines):
                # "Day P&L: `₹+1234.56`"
                m = re.search(r"P&L.*?₹([+-]?[\d,.]+)", line)
                if m:
                    result["pnl"] = float(m.group(1).replace(",", ""))
                    break

            for line in reversed(today_lines):
                # "Available balance: ₹500,000.00"
                m = re.search(r"balance.*?₹([\d,.]+)", line, re.IGNORECASE)
                if m and result["capital"] == 0:
                    result["capital"] = float(m.group(1).replace(",", ""))
                    break

            result["trades"] = sum(1 for l in today_lines
                                   if ("WIN" in l or "LOSS" in l) and "P&L" in l)
            result["wins"]   = sum(1 for l in today_lines if "WIN" in l and "P&L" in l)
            result["losses"] = result["trades"] - result["wins"]
    except Exception as e:
        logger.debug(f"india log parse: {e}")

    # If still no capital, use config default
    if result["capital"] == 0:
        try:
            sys_path_bak = __import__("sys").path.copy()
            __import__("sys").path.insert(0, str(BASE_DIR / "india"))
            import importlib
            cfg = importlib.import_module("config_india")
            result["capital"] = float(getattr(cfg, "INITIAL_CAPITAL", 500000))
        except Exception:
            result["capital"] = 500000.0

    return result


# ── Format & Send ─────────────────────────────────────────────────────────────

def send_capital_report(market: str = "BOTH"):
    # US bot disabled by owner — never send unless explicitly re-enabled
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return
    """
    Send unified capital report.
    market: "US", "INDIA", or "BOTH"
    """
    now_et  = datetime.now(ET)
    now_ist = datetime.now(IST)
    date_str = now_et.strftime("%d %b %Y")

    us     = _get_us_capital()
    india  = _get_india_capital()

    def _wr(w, l):
        t = w + l
        return f"{w/t:.0%}" if t >= 3 else "—"

    def _pnl_emoji(pnl):
        return "✅" if pnl > 0 else ("❌" if pnl < 0 else "⏸")

    us_wr     = _wr(us.get("wins", 0), us.get("losses", 0))
    india_wr  = _wr(india.get("wins", 0), india.get("losses", 0))
    us_mode   = "LIVE" if os.getenv("LIVE_TRADING_ENABLED", "").lower() == "true" else "PAPER"
    india_mode = india.get("mode", "paper").upper()

    lines = [
        f"📊 *KingTrades — Daily Capital Report*",
        f"📅 {date_str}",
        f"",
        f"🇺🇸 *US Bot (Alpaca {us_mode})*",
        f"  Capital:  `${us['capital']:>12,.2f}`",
        f"  Day P&L:  `${us['pnl']:>+11,.2f}` {_pnl_emoji(us['pnl'])}",
        f"  Trades:   {us['trades']} (WR {us_wr})",
        f"",
        f"🇮🇳 *India Bot (Dhan {india_mode})*",
        f"  Capital:  `₹{india['capital']:>12,.2f}`",
        f"  Day P&L:  `₹{india['pnl']:>+11,.2f}` {_pnl_emoji(india['pnl'])}",
        f"  Trades:   {india['trades']} (WR {india_wr})",
        f"",
    ]

    # Combined total in USD (rough INR→USD at 84)
    inr_to_usd = india["capital"] / 84
    combined = us["capital"] + inr_to_usd
    combined_pnl = us["pnl"] + india["pnl"] / 84
    lines += [
        f"💰 *Combined (est.)*",
        f"  Total:    `≈${combined:>10,.0f}`",
        f"  Day P&L:  `≈${combined_pnl:>+9,.0f}`",
        f"",
        f"_Bots running autonomously — next check tomorrow_",
    ]

    msg = "\n".join(lines)
    _tg(msg)
    logger.info(f"Capital report sent | US: ${us['capital']:,.0f} | India: ₹{india['capital']:,.0f}")
    return msg


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    market = sys.argv[1].upper() if len(sys.argv) > 1 else "BOTH"
    send_capital_report(market)
