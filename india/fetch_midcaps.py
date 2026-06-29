"""
fetch_midcaps.py — fetch a HIGH-VOLATILITY NSE universe into midcap_cache.pkl.

WHY
---
Every strategy lost on Nifty-50 large-caps because their intraday moves (~0.5-1%)
are too small to clear the 0.45% round-trip cost. Cost sensitivity proved the
signal only turns positive at ~0% cost. The one path the data leaves open is to
trade names that MOVE more, so the same fixed cost is a smaller fraction of each
move. This script fetches a liquid high-beta / mid-cap universe (routinely 2-4%
intraday range) so orb_fast.py / strategy_lab.py can be re-tested on it:

  python3 india/fetch_midcaps.py --days 90
  python3 india/orb_fast.py     --cache midcap_cache.pkl
  python3 india/strategy_lab.py --cache midcap_cache.pkl

Names that don't resolve in the Upstox instrument map are skipped (reported).
Same 5-min OHLCV pipeline as the optimizer (bk._fetch). Nothing here trades.
"""

from __future__ import annotations

import argparse
import datetime
import pickle
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

OUT_FILE = _HERE / "midcap_cache.pkl"

# Liquid, high-beta / high-range NSE names. These move 2-4%+ intraday — a far
# better move-to-cost ratio than Nifty-50 large-caps. All cash-segment equity
# (MIS intraday eligible). Curated for liquidity so fills are realistic.
HIGH_VOL_UNIVERSE = [
    # metals / PSU / high-beta cyclicals
    "TATASTEEL", "TATAMOTORS", "HINDALCO", "VEDL", "JSWSTEEL", "SAIL", "NMDC",
    "NATIONALUM", "JINDALSTEL", "HINDCOPPER",
    # PSU banks / NBFC (very volatile intraday)
    "PNB", "BANKBARODA", "CANBK", "UNIONBANK", "IDFCFIRSTB", "BANDHANBNK",
    "AUBANK", "YESBANK", "RBLBANK", "FEDERALBNK",
    # Adani / power / infra
    "ADANIENT", "ADANIPORTS", "ADANIPOWER", "TATAPOWER", "NHPC", "SJVN",
    "RVNL", "IRFC", "IRCTC", "BHEL", "BEL", "HAL",
    # new-age / high-beta
    "ZOMATO", "PAYTM", "POLICYBZR", "NYKAA", "DELHIVERY", "JIOFIN", "IDEA",
    # autos / auto-ancillary
    "ASHOKLEY", "MOTHERSON", "BALKRISIND", "TVSMOTOR", "BHARATFORG",
    # IT mid / others with range
    "PERSISTENT", "COFORGE", "LTIM", "DIXON", "DLF", "GODREJPROP", "INDHOTEL",
    "LICHSGFIN", "GAIL", "BPCL", "IOC", "OFSS", "POLYCAB",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", type=str, default=str(OUT_FILE))
    args = ap.parse_args()

    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk

    to_dt = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=args.days)
    fs, ts = from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")

    print("=" * 70)
    print("  Fetching HIGH-VOLATILITY universe for re-test")
    print(f"  {len(HIGH_VOL_UNIVERSE)} symbols | {fs} -> {ts} ({args.days}d)")
    print("=" * 70)

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed. Refresh the token and retry.")
        sys.exit(1)
    dfu.set_upstox_client(client)

    data, skipped = {}, []
    t0 = time.time()
    for i, sym in enumerate(HIGH_VOL_UNIVERSE, 1):
        try:
            df = bk._fetch(client, sym, fs, ts)
            if df is not None and len(df) > 50:
                data[sym] = df
            else:
                skipped.append(sym)
        except Exception as e:
            skipped.append(f"{sym}({str(e)[:30]})")
        if i % 5 == 0:
            eta = (time.time() - t0) / i * (len(HIGH_VOL_UNIVERSE) - i) / 60
            print(f"  {i}/{len(HIGH_VOL_UNIVERSE)}  ok={len(data)}  ETA:{eta:.0f}m", flush=True)

    if not data:
        print("ERROR: no symbols fetched (instrument map / token issue)."); sys.exit(1)

    out = Path(args.out)
    with open(out, "wb") as f:
        pickle.dump(data, f, protocol=4)

    print("=" * 70)
    print(f"  Saved {len(data)} symbols -> {out}  ({(time.time()-t0)/60:.1f}m)")
    if skipped:
        print(f"  Skipped ({len(skipped)}): {', '.join(skipped[:20])}"
              + (" ..." if len(skipped) > 20 else ""))
    print("\n  Next:")
    print(f"    python3 india/orb_fast.py     --cache {out.name}")
    print(f"    python3 india/strategy_lab.py --cache {out.name}")
    print("=" * 70)


if __name__ == "__main__":
    main()
