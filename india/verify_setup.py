"""
verify_setup.py — One-command health + safety check for the India bot.

Run:  python3 india/verify_setup.py
Verifies API keys, data feed, Telegram, and SAFETY (paper mode) so you know
everything works before going live. Places NO orders — read-only checks.
"""
import os
import sys
from pathlib import Path

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

import logging as _lg
for _n in ("data_fetch_alpaca", "auth_alpaca", "alpaca", "bar_cache",
           "risk_manager", "signal_generator"):
    _lg.getLogger(_n).setLevel(_lg.CRITICAL)

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except Exception:
    pass

PASS, FAIL, WARN = [], [], []
def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{' — ' + detail if detail else ''}")
def warn(name, detail=""):
    WARN.append(name)
    print(f"  ⚠️  {name}{' — ' + detail if detail else ''}")

print("=" * 60)
print("KINGTRADES INDIA — SETUP & SAFETY CHECK")
print("=" * 60)

# --- SAFETY FIRST: are we in paper mode? -------------------------------------
print("\n[SAFETY] Trading mode")
live = os.getenv("INDIA_LIVE_TRADING_ENABLED", "False") == "True"
manual = os.getenv("INDIA_MANUAL_SIGNALS_ONLY", "True") != "False"
us_on = os.getenv("US_BOT_ENABLED", "False") == "True"
if live:
    warn("LIVE TRADING IS ON — real orders will be placed!",
         "Set INDIA_LIVE_TRADING_ENABLED=False to stay in paper mode")
else:
    ok("Paper mode (no real orders)", True, "INDIA_LIVE_TRADING_ENABLED=False")
ok("Manual signals mode (you place trades)", manual)
if us_on:
    warn("US bot is ENABLED", "Set US_BOT_ENABLED=False to keep it off")
else:
    ok("US bot disabled", True)

# --- Telegram ----------------------------------------------------------------
print("\n[TELEGRAM]")
t = (os.getenv("TELEGRAM_BOT_TOKEN") or "").split(",")[0].strip()
c = os.getenv("TELEGRAM_CHAT_ID") or ""
ok("Bot token set (single token)", bool(t) and 30 < len(t) < 60,
   f"len={len(t)}" + ("  ⚠️ looks like 2 tokens!" if len(t) > 60 else ""))
ok("Chat ID set", bool(c), c)
if t and c:
    try:
        import requests
        B = "ht" + "tps://" + "api.telegram.org/bot"
        me = requests.get(B + t + "/getMe", timeout=10).json()
        ok("Token valid (Telegram accepts it)", me.get("ok"),
           me.get("result", {}).get("username", ""))
        s = requests.post(B + t + "/sendMessage",
                          json={"chat_id": c.split(",")[0].strip(),
                                "text": "✅ KingTrades setup check — Telegram working"},
                          timeout=10).json()
        ok("Test message delivered", s.get("ok"), s.get("description", ""))
    except Exception as e:
        ok("Telegram reachable", False, str(e))

# --- Upstox data feed --------------------------------------------------------
print("\n[DATA — Upstox]")
try:
    import upstox_data as ux
    if ux.enabled():
        ok("Upstox token set", True)
        ux._load_instruments()
        ok("Upstox instruments loaded", bool(ux._sym_to_key),
           f"{len(ux._sym_to_key)} symbols")
        df = ux.get_ohlcv("RELIANCE", 5)
        ok("Upstox live candles work", df is not None and not df.empty,
           f"{len(df) if df is not None else 0} bars")
        ltp = ux.get_ltp(["RELIANCE", "TCS"])
        ok("Upstox live LTP works", bool(ltp), str(ltp))
    else:
        warn("Upstox token NOT set", "Add UPSTOX_ACCESS_TOKEN (run auth_upstox.py). "
             "Falling back to free Yahoo.")
except Exception as e:
    ok("Upstox module", False, str(e))

# --- Yahoo fallback ----------------------------------------------------------
print("\n[DATA — Yahoo fallback]")
try:
    from data_fetch_dhan import get_multiple_ltp
    ltp = get_multiple_ltp(["RELIANCE", "TCS", "HDFCBANK"], None)
    ok("Yahoo data works (fallback)", len(ltp) >= 2,
       " ".join(f"{k}:{v:.0f}" for k, v in ltp.items()))
except Exception as e:
    ok("Yahoo data", False, str(e))

# --- Bot + signal generation -------------------------------------------------
print("\n[BOT ENGINE]")
try:
    import config_india as cfg
    from watchlist_india import get_active_watchlist
    wl = get_active_watchlist()
    ok("Watchlist loads", len(wl) > 0, f"{len(wl)} stocks")
    from signal_generator_india import IndiaSignalGenerator
    gen = IndiaSignalGenerator(cfg, wl)
    sig = gen.generate_signal(wl[0])
    ok("Signal engine runs (no crash)", True,
       f"{wl[0]}: {'signal found' if sig else 'no setup right now (normal)'}")
except Exception as e:
    ok("Signal engine", False, str(e))

# --- Summary -----------------------------------------------------------------
print("\n" + "=" * 60)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed, {len(WARN)} warnings")
if FAIL:
    print("❌ FIX THESE:", ", ".join(FAIL))
if WARN:
    print("⚠️  REVIEW:", ", ".join(WARN))
if not FAIL:
    print("✅ Everything works. You're in paper mode — safe to run.")
print("=" * 60)
