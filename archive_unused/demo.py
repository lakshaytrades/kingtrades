"""
demo.py — Live demonstration of what SataVector bot does every day.
Shows signal scanning, risk checks, and sends a sample email report.
Does NOT place any real orders.
"""

import os, sys, time, logging
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.WARNING)  # suppress noise
IST = ZoneInfo("Asia/Kolkata")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

print()
print("=" * 60)
print("  SATAVECTOR BOT — LIVE DEMO")
print("=" * 60)
print()

# ── 1. Auth status ────────────────────────────────────────────
print("[ 1 ] GROWW AUTH STATUS")
print("-" * 40)
try:
    import json
    from pathlib import Path
    f = Path("data/.token_cache.json")
    if f.exists():
        d = json.loads(f.read_text())
        tok = d.get("access_token", "")
        if tok:
            print(f"  ✅ Token loaded  ({len(tok)} chars)")
            print(f"  ✅ Source: {d.get('source', 'cache')}")
        else:
            print("  ❌ No token saved — run: python3 save_token.py")
    else:
        print("  ❌ No token file found — run: python3 save_token.py")
except Exception as e:
    print(f"  ⚠️  {e}")
print()

# ── 2. Watchlist ──────────────────────────────────────────────
print("[ 2 ] TODAY'S WATCHLIST")
print("-" * 40)
watchlist_env = os.getenv("CUSTOM_WATCHLIST", "")
if watchlist_env:
    watchlist = [s.strip() for s in watchlist_env.split(",") if s.strip()]
else:
    watchlist = ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK",
                 "SBIN","BHARTIARTL","ITC","KOTAKBANK","LT"]
for i, sym in enumerate(watchlist, 1):
    print(f"  {i:2}. {sym}")
print(f"\n  Total: {len(watchlist)} stocks monitored every 60 seconds")
print()

# ── 3. Bot schedule ───────────────────────────────────────────
print("[ 3 ] DAILY SCHEDULE (IST)")
print("-" * 40)
schedule = [
    ("08:45 AM", "Auto-login to Groww using saved token"),
    ("09:00 AM", "Morning brief email — market outlook + watchlist"),
    ("09:15 AM", "Market opens — begin scanning for signals"),
    ("09:15 AM", "Scan every stock for: RSI, MACD, VWAP, volume surge"),
    ("09:30 AM", "Opening range breakout signals evaluated"),
    ("09:15–3:20", "Place BUY/SELL orders when signal score > threshold"),
    ("09:15–3:20", "Email alert sent instantly for every trade entry/exit"),
    ("03:15 PM", "Warning if any open positions remain"),
    ("03:20 PM", "Auto square-off all positions (mandatory)"),
    ("03:30 PM", "Market closes — all positions flat"),
    ("03:45 PM", "EOD email report — P&L, all trades, win rate"),
]
for time_str, action in schedule:
    print(f"  {time_str:<12}  {action}")
print()

# ── 4. Signal logic demo ──────────────────────────────────────
print("[ 4 ] HOW A SIGNAL IS GENERATED")
print("-" * 40)
print("""
  Every 60 seconds the bot checks each stock for:

  ENTRY CONDITIONS (must have 3+ signals aligned):
  ┌─────────────────────────────────────────────────┐
  │  RSI(14) < 35 (oversold) or > 65 (overbought)  │
  │  MACD crossover in last 2 candles               │
  │  Price > VWAP (bullish) or < VWAP (bearish)     │
  │  Volume > 2x 20-period average (surge)          │
  │  5-min candle confirms 15-min trend             │
  │  15-min trend confirms 1-hour direction         │
  └─────────────────────────────────────────────────┘

  EXAMPLE — RELIANCE BUY signal at 9:32 AM:
    RSI:    38 (oversold, bouncing)          ✅
    MACD:   Bullish crossover just happened  ✅
    VWAP:   Price at VWAP (fair value entry) ✅
    Volume: 3.2x average (strong interest)  ✅
    Score:  82/100 → ENTER TRADE

  POSITION SIZING:
    Capital:   Rs.50,000 available
    Risk/trade: 0.5% = Rs.250 max loss
    ATR:        Rs.8.50 (volatility measure)
    Stop Loss:  Entry - 1.5x ATR = Rs.12.75 below entry
    Target:     Entry + 3x ATR = Rs.25.50 above entry (3:1 ratio)
    Qty:        Rs.250 / Rs.12.75 = 19 shares
""")

# ── 5. Sample trade email ────────────────────────────────────
print("[ 5 ] SAMPLE TRADE ALERT EMAIL")
print("-" * 40)
print("""
  Subject: TRADE ENTRY — RELIANCE (BUY (LONG))

  Qty: 19 shares @ Rs.1,284.50
  Capital deployed: Rs.24,405 (5x MIS leverage)
  Margin used: Rs.4,881 (balance Rs.50,000)
  SL: Rs.1,271.75  |  Target: Rs.1,310.00
  Risk: Rs.242 (0.99%)
  Score: 82  |  Order: ORD123456789
""")
print("""
  Subject: TRADE EXIT — RELIANCE (PROFIT: +Rs.484)

  Reason: Target hit
  Entry: Rs.1,284.50  |  Exit: Rs.1,310.00
  Qty: 19  |  P&L: +Rs.484
  Order: ORD123456790
""")

# ── 6. EOD report preview ────────────────────────────────────
print("[ 6 ] SAMPLE EOD EMAIL REPORT (3:45 PM)")
print("-" * 40)
print("""
  ╔══════════════════════════════════════╗
  ║  SataVector — Daily P&L Report       ║
  ║  Thursday, 8 May 2026                ║
  ╠══════════════════════════════════════╣
  ║  Total P&L:    +Rs.1,247             ║
  ║  Trades today: 4                     ║
  ║  Winners:      3  (75% win rate)     ║
  ║  Losers:       1                     ║
  ║  Capital used: Rs.48,200             ║
  ╠══════════════════════════════════════╣
  ║  TRADE LOG                           ║
  ║  RELIANCE  BUY  +Rs.484  Target hit  ║
  ║  TCS       BUY  +Rs.312  Target hit  ║
  ║  INFY      SELL -Rs.198  SL hit      ║
  ║  HDFCBANK  BUY  +Rs.649  Target hit  ║
  ╚══════════════════════════════════════╝
""")

# ── 7. Send test email ────────────────────────────────────────
print("[ 7 ] SENDING TEST EMAIL NOW...")
print("-" * 40)
try:
    from email_reporter import send_alert
    send_alert(
        "SataVector Bot — Demo Complete, Bot is Live!",
        """Your SataVector bot is set up and ready.

WHAT HAPPENS TOMORROW (market day):
• 8:45 AM IST — Bot logs into Groww automatically
• 9:00 AM IST — You receive morning brief email
• 9:15 AM IST — Bot starts scanning 10 NSE stocks
• Every trade — instant email with entry/exit details
• 3:45 PM IST — Full P&L report in your inbox

WATCHLIST: RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, SBIN, BHARTIARTL, ITC, KOTAKBANK, LT

RISK CONTROLS:
• Max 0.5% capital per trade
• Stop loss on every trade (ATR-based)
• Auto square-off at 3:20 PM
• Daily loss limit: 2% of capital

Bot is running 24/7 on Hostinger Mumbai VPS.
You just wake up and read what it did.

— SataVector"""
    )
    print("  ✅ Test email sent to l60116246@gmail.com")
    print("     Check your inbox now!")
except Exception as e:
    print(f"  ❌ Email failed: {e}")
    print("     Check GMAIL_APP_PASSWORD in .env")

print()
print("=" * 60)
print("  DEMO COMPLETE")
print("  Bot is LIVE — market opens 9:15 AM IST")
print("=" * 60)
print()
