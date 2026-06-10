"""
test_nse_data.py — Quick diagnostic for NSE + Dhan data on VPS
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
print("NSE + DHAN DATA DIAGNOSTIC")
print("=" * 55)

# Test 1: dhanhq package installed?
print("\n[1] Checking dhanhq package...")
try:
    import dhanhq  # noqa
    print("    ✅ dhanhq installed")
except ImportError:
    print("    ❌ dhanhq NOT installed")
    print("    FIX: pip install dhanhq")

# Test 2: Dhan credentials + API connection
print("\n[2] Checking Dhan credentials & connection...")
import os
cid = os.getenv("DHAN_CLIENT_ID", "")
tok = os.getenv("DHAN_ACCESS_TOKEN", "")
print(f"    DHAN_CLIENT_ID:    {'✅ ' + cid[:6] + '***' if cid else '❌ MISSING — add to .env'}")
print(f"    DHAN_ACCESS_TOKEN: {'✅ ' + tok[:8] + '***' if tok else '❌ MISSING — add to .env'}")
client = None
if cid and tok:
    try:
        from auth_dhan import get_dhan_client, verify_connection
        client = get_dhan_client()
        ok = verify_connection(client)
        print(f"    Dhan API:          {'✅ CONNECTED' if ok else '❌ failed — token may be expired'}")
        if not ok:
            print("    FIX: get new token from dhanhq.co → My Account → API")
            print("         Then run: echo 'DHAN_ACCESS_TOKEN=newtoken' >> /root/kingtrades/.env")
    except Exception as e:
        print(f"    ❌ {e}")

# Test 3: Dhan OHLCV (primary data source after the fix)
print("\n[3] Testing Dhan OHLCV API (primary data source)...")
if client is not None:
    try:
        from data_fetch_dhan import set_dhan_client, _dhan_ohlcv, get_security_id
        set_dhan_client(client)
        df = _dhan_ohlcv("RELIANCE", 5, client)
        if df is not None and not df.empty:
            print(f"    ✅ RELIANCE: {len(df)} candles")
            print(f"    Latest: {df.index[-1].strftime('%Y-%m-%d %H:%M IST')}  close=₹{df['close'].iloc[-1]:.2f}")
        else:
            print("    ⚠️  No data — market is closed (normal outside 9:15-15:30 IST)")
            print("    Re-run during market hours to confirm.")
    except Exception as e:
        print(f"    ❌ {e}")
else:
    print("    ⏭  Skipped — no Dhan client (fix Tests 1+2 first)")

# Test 4: NSE allIndices (VIX, Nifty, sectors)
print("\n[4] Testing NSE allIndices (VIX + Nifty)...")
try:
    from data_fetch_dhan import get_india_vix, get_nifty_level
    vix   = get_india_vix()
    nifty = get_nifty_level()
    print(f"    VIX:   {vix:.1f}  {'✅' if vix > 0 else '⚠️  0 — normal if market closed'}")
    print(f"    Nifty: {nifty['level']:.0f}  {'✅' if nifty['level'] > 0 else '⚠️  0 — normal if market closed'}")
except Exception as e:
    print(f"    ❌ {e}")

# Test 5: NSE charting (will 403 on datacenter IP — that's expected now)
print("\n[5] NSE charting API check (fallback — expected to fail on datacenter)...")
try:
    import requests
    from data_fetch_dhan import _get_nse_session, _NSE_CHART_BASE, _NSE_MAIN
    sess = _get_nse_session()
    r = sess.get(
        f"{_NSE_CHART_BASE}/Charts/symbolhistoricaldata/RELIANCE",
        params={"time": "5", "type": "EQ"},
        headers={"Referer": _NSE_MAIN + "/", "Origin": _NSE_MAIN,
                 "sec-fetch-site": "same-site", "sec-fetch-mode": "cors",
                 "sec-fetch-dest": "empty"},
        timeout=12,
    )
    if r.status_code == 200 and len(r.text) > 100:
        rows = (r.json().get("grapthData") or r.json().get("graphData") or [])
        print(f"    ✅ {len(rows)} candles (NSE charting works on this IP — bonus!)")
    else:
        print(f"    ⚠️  HTTP {r.status_code} — Akamai blocked (expected on VPS/datacenter IP)")
        print("    This is OK — Dhan API handles all OHLCV now.")
except Exception as e:
    print(f"    ⚠️  {e} (expected — Dhan is primary now)")

print("\n" + "=" * 55)
print("WHAT YOU NEED FOR SIGNALS TO WORK:")
print("=" * 55)
print("✅ Test 1: dhanhq installed  → pip install dhanhq")
print("✅ Test 2: Dhan connected    → valid token in .env")
print("✅ Test 3: OHLCV data loads  → run during market hours")
print("")
print("Tests 4+5 are informational only.")
