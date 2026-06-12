"""
test_nse_data.py — Quick diagnostic for NSE + Upstox data on VPS
Run: python3 india/test_nse_data.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

print("=" * 55)
print("NSE + UPSTOX DATA DIAGNOSTIC")
print("=" * 55)

# Test 1: upstox-python-sdk installed?
print("\n[1] Checking upstox-python-sdk package...")
try:
    import upstox_client  # noqa
    print("    ✅ upstox-python-sdk installed")
except ImportError:
    print("    ❌ upstox-python-sdk NOT installed")
    print("    FIX: pip install upstox-python-sdk")

# Test 2: Upstox credentials + API connection
print("\n[2] Checking Upstox credentials & connection...")
import os
api_key = os.getenv("UPSTOX_API_KEY", "")
tok     = os.getenv("UPSTOX_ACCESS_TOKEN", "")
print(f"    UPSTOX_API_KEY:      {'✅ ' + api_key[:6] + '***' if api_key else '❌ MISSING — add to .env'}")
print(f"    UPSTOX_ACCESS_TOKEN: {'✅ ' + tok[:8] + '***' if tok else '❌ MISSING — add to .env'}")
client = None
if tok:
    try:
        from auth_upstox import get_upstox_client, verify_connection
        client = get_upstox_client()
        ok = verify_connection(client)
        print(f"    Upstox API:          {'✅ CONNECTED' if ok else '❌ failed — token may be expired'}")
        if not ok:
            print("    FIX: run the Upstox OAuth2 flow to get a new daily token")
            print("         Then run: echo 'UPSTOX_ACCESS_TOKEN=newtoken' >> /root/kingtrades/.env")
    except Exception as e:
        print(f"    ❌ {e}")

# Test 3: Upstox OHLCV (primary data source)
print("\n[3] Testing Upstox OHLCV API (primary data source)...")
if client is not None:
    try:
        from data_fetch_upstox import set_upstox_client, get_ohlcv
        set_upstox_client(client)
        df = get_ohlcv("RELIANCE", "5m", 5)
        if df is not None and not df.empty:
            print(f"    ✅ RELIANCE: {len(df)} candles")
            print(f"    Latest: {df.index[-1]}  close=₹{df['close'].iloc[-1]:.2f}")
        else:
            print("    ⚠️  No data — market is closed (normal outside 9:15-15:30 IST)")
            print("    Re-run during market hours to confirm.")
    except Exception as e:
        print(f"    ❌ {e}")
else:
    print("    ⏭  Skipped — no Upstox client (fix Tests 1+2 first)")

# Test 4: NSE indices (VIX, Nifty)
print("\n[4] Testing NSE indices (VIX + Nifty)...")
try:
    from data_fetch_upstox import get_india_vix, get_nifty_level
    vix   = get_india_vix()
    nifty = get_nifty_level()
    print(f"    VIX:   {vix:.1f}  {'✅' if vix > 0 else '⚠️  0 — normal if market closed'}")
    print(f"    Nifty: {nifty['level']:.0f}  {'✅' if nifty['level'] > 0 else '⚠️  0 — normal if market closed'}")
except Exception as e:
    print(f"    ❌ {e}")

# Test 5: Instrument master (ISIN lookup)
print("\n[5] Testing instrument master (symbol → ISIN key)...")
try:
    from data_fetch_upstox import get_security_id
    key = get_security_id("RELIANCE")
    if key:
        print(f"    ✅ RELIANCE → {key}")
    else:
        print("    ⚠️  Not found in instrument master")
except Exception as e:
    print(f"    ❌ {e}")

print("\n" + "=" * 55)
print("WHAT YOU NEED FOR SIGNALS TO WORK:")
print("=" * 55)
print("✅ Test 1: upstox-python-sdk installed  → pip install upstox-python-sdk")
print("✅ Test 2: Upstox connected             → valid daily token in .env")
print("✅ Test 3: OHLCV data loads             → run during market hours")
print("")
print("Tests 4+5 are informational only.")
