"""
test_nse_data.py — Quick diagnostic for NSE data on VPS
Run: python3 india/test_nse_data.py
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 50)
print("NSE DATA DIAGNOSTIC")
print("=" * 50)

# Test 1: allIndices (VIX, Nifty)
print("\n[1] Testing NSE allIndices (VIX + Nifty)...")
try:
    from data_fetch_dhan import get_india_vix, get_nifty_level
    vix = get_india_vix()
    nifty = get_nifty_level()
    print(f"    VIX: {vix:.1f}  {'✅ OK' if vix > 0 else '❌ FAILED'}")
    print(f"    Nifty: {nifty['level']:.0f}  {'✅ OK' if nifty['level'] > 0 else '❌ FAILED'}")
except Exception as e:
    print(f"    ❌ FAILED: {e}")

# Test 2: NSE charting session warmup
print("\n[2] Testing NSE session warmup (cookies)...")
try:
    from data_fetch_dhan import _get_nse_session, _NSE_MAIN, _NSE_CHART_BASE
    sess = _get_nse_session()
    cookies = dict(sess.cookies)
    print(f"    Cookies obtained: {len(cookies)}  {'✅ OK' if cookies else '⚠️ no cookies'}")
    for k in list(cookies.keys())[:5]:
        print(f"    {k}: {str(cookies[k])[:20]}...")
except Exception as e:
    print(f"    ❌ FAILED: {e}")

# Test 3: NSE charting API for RELIANCE
print("\n[3] Testing NSE charting API (RELIANCE 5m candles)...")
try:
    import requests
    from data_fetch_dhan import _get_nse_session, _NSE_CHART_BASE, _NSE_MAIN
    sess = _get_nse_session()
    url = f"{_NSE_CHART_BASE}/Charts/symbolhistoricaldata/RELIANCE"
    params = {"time": "5", "type": "EQ"}
    hdrs = {
        "Referer":        _NSE_MAIN + "/",
        "Origin":         _NSE_MAIN,
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    r = sess.get(url, params=params, headers=hdrs, timeout=15)
    print(f"    HTTP status: {r.status_code}")
    print(f"    Response length: {len(r.text)} chars")
    if r.status_code == 200 and len(r.text) > 50:
        try:
            data = r.json()
            rows = data.get("grapthData") or data.get("graphData") or data.get("data") or []
            print(f"    Candles returned: {len(rows)}  {'✅ OK' if rows else '❌ empty'}")
            if rows:
                print(f"    Last candle: {rows[-1]}")
        except Exception as je:
            print(f"    ❌ JSON parse failed: {je}")
            print(f"    Raw: {r.text[:200]}")
    else:
        print(f"    ❌ FAILED — raw: {r.text[:200]}")
except Exception as e:
    print(f"    ❌ FAILED: {e}")

# Test 4: Full get_ohlcv pipeline
print("\n[4] Testing full get_ohlcv pipeline (RELIANCE 5m)...")
try:
    from data_fetch_dhan import get_ohlcv
    df = get_ohlcv("RELIANCE", interval="5m", period="5d")
    if df is not None and not df.empty:
        print(f"    Rows: {len(df)}  ✅ OK")
        print(f"    Latest close: ₹{df['close'].iloc[-1]:.2f}")
        print(f"    Time range: {df.index[0]} → {df.index[-1]}")
    else:
        print("    ❌ FAILED — returned None or empty")
except Exception as e:
    print(f"    ❌ FAILED: {e}")

# Test 5: Dhan credentials check
print("\n[5] Checking Dhan credentials...")
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    import os
    cid = os.getenv("DHAN_CLIENT_ID", "")
    tok = os.getenv("DHAN_ACCESS_TOKEN", "")
    print(f"    DHAN_CLIENT_ID:    {'✅ set (' + cid[:6] + '***)' if cid else '❌ MISSING'}")
    print(f"    DHAN_ACCESS_TOKEN: {'✅ set (' + tok[:8] + '***)' if tok else '❌ MISSING'}")
    if cid and tok:
        from auth_dhan import get_dhan_client, verify_connection
        client = get_dhan_client()
        ok = verify_connection(client)
        print(f"    Dhan connection: {'✅ CONNECTED' if ok else '❌ connection failed'}")
except Exception as e:
    print(f"    ❌ FAILED: {e}")

print("\n" + "=" * 50)
print("SUMMARY")
print("=" * 50)
print("If Test 3 shows 0 candles → NSE charting blocked on this IP")
print("If Test 5 shows MISSING   → Add Dhan keys to .env (enables Dhan fallback)")
print("If Test 4 works           → Bot will generate signals normally")
