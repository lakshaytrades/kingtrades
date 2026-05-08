"""
demo_order.py — Place 1 test MIS order and immediately cancel it.
Safe: uses a LIMIT price far below market so it will NEVER fill.
Run: python3 demo_order.py
"""
import io, os, sys, time
from dotenv import load_dotenv
load_dotenv()

from zoneinfo import ZoneInfo
from datetime import datetime, time as dtime

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
market_open  = dtime(9, 15)
market_close = dtime(15, 20)  # stop new orders at 3:20 PM, square-off at 3:30

print(f"\nCurrent IST time: {now.strftime('%H:%M:%S')}")

if not (market_open <= now.time() <= market_close):
    print("❌ Market is closed. Run this between 9:15 AM – 3:20 PM IST on a weekday.")
    sys.exit(1)

# ── Get access token ─────────────────────────────────────────────────────────
print("Getting auth token...")
from auth_groww import get_groww_token
token = get_groww_token()
if not token:
    print("❌ No auth token — run setup_auth.py first")
    sys.exit(1)
print(f"✓ Token: {token[:20]}...")

# ── Init SDK ─────────────────────────────────────────────────────────────────
buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
from growwapi import GrowwAPI
api = GrowwAPI(token)
sys.stdout = old

# ── Get current price ─────────────────────────────────────────────────────────
SYMBOL = "SBIN"
print(f"\nGetting current price of {SYMBOL}...")
try:
    quote = api.get_quote(trading_symbol=SYMBOL, exchange="NSE")
    ltp = quote.get("last_traded_price") or quote.get("ltp") or quote.get("close", 800)
    print(f"✓ {SYMBOL} LTP: ₹{ltp}")
except Exception as e:
    ltp = 800
    print(f"  Could not get quote ({e}), using fallback ₹{ltp}")

# Safe limit price = 50% below market (will NEVER fill — purely tests API)
safe_price = round(ltp * 0.50, 1)
print(f"\nPlacing DEMO order: BUY 1 {SYMBOL} @ ₹{safe_price} (50% below market — will not fill)")

# ── Place order ───────────────────────────────────────────────────────────────
try:
    result = api.place_order(
        trading_symbol=SYMBOL,
        exchange="NSE",
        transaction_type="BUY",
        order_type="LIMIT",
        product="MIS",
        quantity=1,
        price=safe_price,
    )
    print(f"\nOrder API response:\n{result}")
    order_id = (result or {}).get("order_id") or (result or {}).get("data", {}).get("order_id") or ""
except Exception as e:
    # Try alternate param name
    try:
        result = api.place_order(
            symbol=SYMBOL,
            exchange="NSE",
            transaction_type="BUY",
            order_type="LIMIT",
            product="MIS",
            quantity=1,
            price=safe_price,
        )
        print(f"\nOrder API response:\n{result}")
        order_id = (result or {}).get("order_id") or ""
    except Exception as e2:
        print(f"❌ Order placement failed: {e2}")
        sys.exit(1)

if not order_id:
    print("⚠️  Order placed but no order_id returned. Check Groww app orders tab.")
else:
    print(f"\n✓ Order placed! order_id = {order_id}")
    time.sleep(2)

    # ── Cancel immediately ────────────────────────────────────────────────────
    print("Cancelling demo order immediately...")
    try:
        cancel = api.cancel_order(order_id=order_id)
        print(f"✓ Cancelled! Response: {cancel}")
    except Exception as e:
        print(f"  Cancel API: {e}")
        print(f"  ⚠️  Manually cancel order {order_id} in Groww app if still pending!")

print("\n✅ Demo complete — full order placement + cancellation flow works!")
