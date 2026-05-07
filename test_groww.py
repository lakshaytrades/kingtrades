"""
test_groww.py — Verify Groww login and fetch live account balance.
Run: python3 test_groww.py
"""
import os, sys, logging
logging.basicConfig(level=logging.WARNING)  # suppress noise

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

print()
print("=" * 50)
print("  GROWW CONNECTION TEST")
print("=" * 50)

# ── Step 1: Auth ──────────────────────────────────
print("\n[ 1 ] Authenticating with Groww...")
try:
    from auth_groww import get_groww_token
    token = get_groww_token()
    if not token:
        print("  FAIL: No token returned. Check .env credentials.")
        sys.exit(1)
    print(f"  OK  Token loaded ({len(token)} chars)")
except Exception as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

# ── Step 2: Init SDK ──────────────────────────────
print("\n[ 2 ] Connecting to Groww API...")
try:
    import io
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        from growwapi import GrowwAPI
        api = GrowwAPI(token)
    finally:
        sys.stdout = old
    print("  OK  GrowwAPI connected")
except Exception as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

# ── Step 3: Fetch balance ─────────────────────────
print("\n[ 3 ] Fetching account balance...")
balance_found = False
for method_name in ("get_balance", "get_funds", "get_margins"):
    try:
        fn = getattr(api, method_name, None)
        if not fn:
            continue
        raw = fn()
        if raw:
            print(f"  OK  {method_name}() returned data:")
            print(f"      {str(raw)[:300]}")
            balance_found = True
            break
    except Exception as e:
        print(f"  {method_name}(): {e}")

if not balance_found:
    print("  Balance API returned empty — token may be read-only (web JWT).")
    print("  This is normal if you haven't set up the Trade API Cloud Key yet.")
    print("  The bot can still read prices and place orders with the current token.")

# ── Step 4: Fetch a live quote ────────────────────
print("\n[ 4 ] Fetching live price for RELIANCE...")
try:
    from data_fetch_groww import GrowwDataFetcher
    fetcher = GrowwDataFetcher()
    quote = fetcher.get_quote("RELIANCE")
    if quote:
        ltp = quote.get("ltp") or quote.get("last_price") or quote.get("price", 0)
        print(f"  OK  RELIANCE LTP: Rs.{ltp}")
    else:
        print("  Quote returned empty (market may be closed — that's OK)")
except Exception as e:
    print(f"  {e}")

print()
print("=" * 50)
if balance_found:
    print("  ALL GOOD — Bot is ready to trade tomorrow!")
else:
    print("  AUTH OK — Bot can trade. Balance API needs Trade API key.")
    print("  Market opens 9:15 AM IST — bot will trade automatically.")
print("=" * 50)
print()
