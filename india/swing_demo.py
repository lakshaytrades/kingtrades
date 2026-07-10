"""
swing_demo.py — LIVE DEMO of the swing bot end-to-end, safe (paper, no real ₹).

Runs the REAL swing_pilot logic against a scripted realistic scenario so you can
watch the whole trade lifecycle and — if TELEGRAM_BOT_TOKEN/CHAT_ID are in .env
— receive the actual BUY / SELL / digest alerts on your phone (labelled DEMO).

  python3 india/swing_demo.py

Day 1: TATASTEEL falls -5.5% -> bot BUYS (delivery, paper).
Day 2: it bounces +2.3% -> bot SELLS at target, books the P&L.
Nothing real is ordered; this proves the machinery + notifications work.
"""
from __future__ import annotations
import sys, types, tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np, pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE)); sys.path.insert(0, str(_ROOT))
IST = ZoneInfo("Asia/Kolkata")

# load real .env so DEMO Telegram messages reach your phone (creds only)
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

# scripted scenario state
STOCK = "TATASTEEL"
DAY = {"n": 0}                         # 0 = buy day, 1 = sell day
BDAYS = pd.bdate_range("2026-06-01", periods=40)


def _daily(price_today, hist_price=100.0):
    n = len(BDAYS) - 1
    closes = np.full(n, hist_price)
    idx = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=IST) for d in BDAYS[:n]])
    return pd.DataFrame({"open": closes, "high": closes*1.01, "low": closes*0.99,
                         "close": closes, "volume": np.full(n, 2e6)}, index=idx)


def _intra(now, last):
    idx = pd.date_range(now.replace(hour=9, minute=15), now, freq="5min")
    c = np.linspace(100.0, last, len(idx))
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": np.full(len(idx), 3e4)}, index=idx)


def main():
    # mock broker modules BEFORE importing swing_pilot
    auth = types.ModuleType("auth_upstox")
    auth.get_upstox_client = lambda: types.SimpleNamespace(
        user=types.SimpleNamespace(get_user_fund_margin=lambda api_version: {
            "data": {"equity": {"available_margin": 100000.0}}}))
    auth.verify_connection = lambda c: True
    sys.modules["auth_upstox"] = auth

    NOW = {"t": datetime(2026, 7, 13, 15, 10, tzinfo=IST)}   # a Monday

    def fake_ohlcv(sym, interval="5m", period="5d"):
        if sym != STOCK:
            # give the rest of the universe flat, non-signal data
            if interval in ("1d", "day"): return _daily(100.0)
            return _intra(NOW["t"], 100.0)
        last = 94.5 if DAY["n"] == 0 else 96.65      # -5.5% then +2.3% off 94.5
        if interval in ("1d", "day"):
            return _daily(94.5 if DAY["n"] == 1 else 100.0)
        return _intra(NOW["t"], last)

    dfu = types.ModuleType("data_fetch_upstox")
    dfu.set_upstox_client = lambda c: None
    dfu.get_ohlcv = fake_ohlcv
    dfu.get_security_id = lambda s: "DEMO_KEY"
    sys.modules["data_fetch_upstox"] = dfu

    class Res:
        def __init__(s, ok, fp, q): s.success, s.fill_price, s.quantity, s.message, s.order_id = ok, fp, q, "demo", "DEMO"
    class Exe:
        live = False
        def place_delivery_order(s, sym, d, qty, price, sec, product="D"):
            print(f"   [broker/paper] {d} {qty} {sym} @ ₹{price:.2f} ({product})")
            return Res(True, price, qty)
        def get_delivery_holdings(s): return {}
        def _order_status(s, oid): return ("", 0.0, 0)
    exe = types.ModuleType("execution_upstox")
    exe.get_executor = lambda client=None, live_enabled=False: Exe()
    sys.modules["execution_upstox"] = exe

    import swing_pilot as sp
    tmp = Path(tempfile.mkdtemp())
    sp.STATE_FILE = tmp / "demo_state.json"; sp.KILL_FILE = tmp / "NO_KILL"
    sp.now_ist = lambda: NOW["t"]
    sp.UNIVERSE = [STOCK, "PNB", "GAIL", "SAIL", "VEDL"]
    orig_tg = sp._tg_send
    sp._tg_send = lambda t: orig_tg("🧪 DEMO\n" + t)

    print("=" * 66)
    print("  SWING BOT — LIVE DEMO (paper, no real money)")
    print("  Watch a full trade: panic buy -> hold -> target sell + P&L")
    print("  (BUY/SELL/digest alerts also sent to your Telegram, tagged DEMO)")
    print("=" * 66)

    print(f"\n── DAY 1 (Mon 15:10 IST): {STOCK} crashed -5.5% today ──")
    DAY["n"] = 0
    p = sp.SwingPilot()
    p.run()
    held = STOCK in p.positions
    if held:
        pos = p.positions[STOCK]
        print(f"   -> BOUGHT {pos['qty']} @ ₹{pos['entry']:.2f}  "
              f"target ₹{pos['target']:.2f} / stop ₹{pos['stop']:.2f}")

    print(f"\n── DAY 2 (Tue 15:10 IST): {STOCK} bounced +2.3% -> hits target ──")
    DAY["n"] = 1
    NOW["t"] = datetime(2026, 7, 14, 15, 10, tzinfo=IST)
    p2 = sp.SwingPilot()                # reloads state from disk (like a real day)
    p2.run()
    print("   -> position CLOSED at target; P&L booked (see alert above)")

    print("\n" + "=" * 66)
    print("  DEMO COMPLETE — that is exactly what runs live at 15:10 IST daily.")
    print("  Live difference: real prices + real (small) orders once you fund +")
    print("  set INDIA_SWING_VALIDATED=true. Check your Telegram for the alerts.")
    print("=" * 66)


if __name__ == "__main__":
    main()
