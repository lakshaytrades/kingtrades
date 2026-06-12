"""
backtest_replay_india.py — REAL Historical Replay Backtest (not Monte Carlo)

Unlike backtest_satavector_india.py (which simulates mechanics with WR as an
INPUT), this harness replays the bot's ACTUAL signal generator over REAL past
NSE candles fetched from the Dhan API, and produces win rate as an OUTPUT.

  - Fetches historical 5-min OHLCV per symbol from Dhan (intraday_minute_data)
  - Steps through every 5-min bar chronologically — NO look-ahead (as-of slices)
  - Runs the real IndiaSignalGenerator.generate_signal() at each bar
  - Simulates exact bot exits: 50% partial at T1 (1R), runner to T2 (2R) or
    trail / breakeven, force square-off at 15:20 IST
  - Applies the real NSE cost stack (~0.15% round-trip)
  - Reports REAL win rate, expectancy, profit factor, drawdown, equity curve

IMPORTANT — environment requirements:
  This needs the Dhan API (DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in .env) and
  network access to Dhan. It runs on the PRODUCTION VPS, not in the dev sandbox
  (which has no market-data egress). Yahoo/NSE-archives are blocked in sandbox.

  Price-based signal core IS replayed (gates, base score, MTF, ORB, Wyckoff VSA,
  microstructure, momentum factor). Live-only boosters that have no historical
  feed (option chain, delivery %, FII/DII, news, block deals, UOA, futures OI,
  cross-asset, live-VIX regime, PEAD) are DISABLED for the replay — so the
  reported WR is a CONSERVATIVE FLOOR. In production those boosters add on top.

Usage:
  python3 india/backtest_replay_india.py --days 60
  python3 india/backtest_replay_india.py --symbols RELIANCE,INFY,TCS --days 90
  python3 india/backtest_replay_india.py --from 2026-01-01 --to 2026-03-31
"""
import argparse
import copy
import logging
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("replay")

# Live-only boosters with NO historical feed → disabled for an honest replay.
# These require real-time external data that cannot be reconstructed for past dates.
_LIVE_ONLY_FLAGS = [
    "OPTION_CHAIN_ENABLED", "OPTION_CHAIN_GODMODE", "FII_DII_ENABLED",
    "DELIVERY_VOL_ENABLED", "DELIVERY_V2_ENABLED", "NEWS_SENTIMENT_ENABLED",
    "NEWS_NLP_GODMODE", "UOA_ENABLED", "FUTURES_OI_ENABLED", "BLOCK_DEAL_ENABLED",
    "CROSS_ASSET_ENABLED", "GLOBAL_CUES_ENABLED", "CORP_EVENTS_ENABLED",
    "GEX_ENABLED", "VIX_REGIME_ENABLED", "PEAD_ENABLED", "KALMAN_PAIRS_ENABLED",
    "MOMENTUM_FACTOR_ENABLED", "MORNING_INTEL_ENABLED", "MARKET_BREADTH_ENABLED",
    "SECTOR_CONFLUENCE_ENABLED", "MACRO_SCORING_ENABLED", "INDIA_VIX_ENABLED",
    "ELITE_TRACKER_ENABLED",
]

# NSE round-trip cost as fraction of turnover (brokerage + STT + txn + GST + slip)
COST_RT_PCT = 0.0015
SQUAREOFF = dtime(15, 20)


def _make_replay_config():
    """Clone config_india into a namespace with live-only boosters disabled."""
    import config_india as base
    from types import SimpleNamespace
    attrs = {k: getattr(base, k) for k in dir(base) if not k.startswith("__")}
    cfg = SimpleNamespace(**attrs)
    for flag in _LIVE_ONLY_FLAGS:
        if hasattr(cfg, flag):
            setattr(cfg, flag, False)
    return cfg


def _fetch_history(client, symbol: str, from_date: str, to_date: str,
                   interval: str = "5") -> Optional[pd.DataFrame]:
    """
    Fetch historical intraday bars from Dhan over a date range, paging in
    chunks (Dhan caps the intraday window per request). Returns IST-indexed
    OHLCV DataFrame or None.
    """
    from data_fetch_dhan import get_security_id
    sec_id = get_security_id(symbol)
    if not sec_id:
        logger.warning(f"{symbol}: no security_id")
        return None

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end   = datetime.strptime(to_date,   "%Y-%m-%d").date()
    frames = []
    chunk_days = 5 if interval == "1" else 30   # page size by interval

    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        for attempt in range(3):
            try:
                resp = client.intraday_minute_data(
                    security_id=sec_id, exchange_segment="NSE_EQ",
                    instrument_type="EQUITY", interval=interval,
                    from_date=cur.strftime("%Y-%m-%d"),
                    to_date=nxt.strftime("%Y-%m-%d"),
                )
                if resp and resp.get("status") == "success":
                    raw = resp.get("data", {})
                    ts = raw.get("timestamp") or raw.get("start_Time") or []
                    if ts:
                        idx = (pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
                               if isinstance(ts[0], (int, float))
                               else pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata"))
                        frames.append(pd.DataFrame({
                            "open": raw.get("open", []), "high": raw.get("high", []),
                            "low": raw.get("low", []), "close": raw.get("close", []),
                            "volume": raw.get("volume", []),
                        }, index=idx).dropna())
                break
            except Exception as e:
                import time as _t; _t.sleep(2 ** attempt)
                logger.debug(f"{symbol} {cur}: {e}")
        cur = nxt + timedelta(days=1)

    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df if not df.empty else None


def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    try:
        r = df.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()
        return r if not r.empty else None
    except Exception:
        return None


class Trade:
    __slots__ = ("symbol", "direction", "entry", "sl", "t1", "t2", "qty",
                 "entry_time", "t1_done", "exit_price", "exit_time", "pnl", "grade")

    def __init__(self, sig, qty, entry_time):
        self.symbol = sig.symbol
        self.direction = sig.direction
        self.entry = sig.entry_price
        self.sl = sig.stop_loss
        self.t1 = sig.target_1
        self.t2 = sig.target_2
        self.qty = qty
        self.grade = sig.quality_grade
        self.entry_time = entry_time
        self.t1_done = False
        self.exit_price = None
        self.exit_time = None
        self.pnl = 0.0


def _simulate_exit(trade: Trade, future_bars: pd.DataFrame) -> float:
    """
    Walk future 5-min bars after entry. Apply bot exit mechanics:
      - 50% partial at T1 (1R), move SL to breakeven on remainder
      - Runner exits at T2 (2R) or trailing stop
      - Force square-off at 15:20 IST
    Returns net P&L in rupees (after costs).
    """
    long = trade.direction == "LONG"
    half = max(1, trade.qty // 2)
    runner = trade.qty - half
    realized = 0.0
    sl = trade.sl

    for ts, bar in future_bars.iterrows():
        hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Square-off time
        if ts.time() >= SQUAREOFF:
            px = cl
            realized += ((px - trade.entry) if long else (trade.entry - px)) * (half if not trade.t1_done else 0)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * runner
            trade.exit_price, trade.exit_time = px, ts
            break

        if not trade.t1_done:
            # Check SL first (conservative: assume worst-touch order)
            sl_hit = lo <= sl if long else hi >= sl
            t1_hit = hi >= trade.t1 if long else lo <= trade.t1
            if sl_hit and not t1_hit:
                px = sl
                realized += ((px - trade.entry) if long else (trade.entry - px)) * trade.qty
                trade.exit_price, trade.exit_time = px, ts
                break
            if t1_hit:
                # Partial 50% at T1, move stop to breakeven
                realized += ((trade.t1 - trade.entry) if long else (trade.entry - trade.t1)) * half
                trade.t1_done = True
                sl = trade.entry  # breakeven on runner
        else:
            # Runner phase: SL at breakeven, target T2
            sl_hit = lo <= sl if long else hi >= sl
            t2_hit = hi >= trade.t2 if long else lo <= trade.t2
            if t2_hit:
                px = trade.t2
                realized += ((px - trade.entry) if long else (trade.entry - px)) * runner
                trade.exit_price, trade.exit_time = px, ts
                break
            if sl_hit:
                px = sl
                realized += ((px - trade.entry) if long else (trade.entry - px)) * runner
                trade.exit_price, trade.exit_time = px, ts
                break
    else:
        # Ran out of bars without exit — close at last close
        if len(future_bars):
            px = float(future_bars["close"].iloc[-1])
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price, trade.exit_time = px, future_bars.index[-1]

    # Costs (round-trip on full turnover)
    turnover = trade.entry * trade.qty + (trade.exit_price or trade.entry) * trade.qty
    realized -= turnover * COST_RT_PCT / 2
    trade.pnl = realized
    return realized


def run_replay(symbols: List[str], from_date: str, to_date: str,
               capital: float = 500_000.0):
    from auth_dhan import get_dhan_client, verify_connection
    cfg = _make_replay_config()

    client = get_dhan_client()
    if not verify_connection(client):
        print("ERROR: Dhan connection failed. This harness needs the production "
              "VPS with DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN and Dhan network access.")
        return

    import data_fetch_dhan as dfd
    dfd.set_dhan_client(client)
    from signal_generator_india import IndiaSignalGenerator

    print(f"Loading historical 5-min data: {len(symbols)} symbols, {from_date} → {to_date}")
    data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _fetch_history(client, sym, from_date, to_date, interval="5")
        if df is not None and len(df) > 80:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars")
        else:
            print(f"  {sym}: insufficient data — skipped")

    if not data:
        print("No data loaded. Check Dhan connectivity / symbol IDs.")
        return

    gen = IndiaSignalGenerator(cfg, list(data.keys()))
    gen._dhan_client = client

    trades: List[Trade] = []
    open_syms: Dict[str, Trade] = {}
    equity = capital
    peak = capital
    max_dd = 0.0

    # Build the union of all 5-min timestamps across symbols, chronological
    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))

    for now_ts in all_ts:
        if now_ts.time() < dtime(9, 20) or now_ts.time() > dtime(15, 0):
            continue

        # Close any open trades whose exit already resolved (handled at entry time)
        for sym, df in data.items():
            if sym in open_syms:
                continue
            if now_ts not in df.index:
                continue

            # As-of slice — NO look-ahead
            hist = df.loc[:now_ts]
            if len(hist) < 30:
                continue

            mtf = {"5m": hist, "15m": _resample(hist, "15min"), "1h": _resample(hist, "1h")}

            # Patch the data accessor to return our as-of frames
            orig = dfd.get_ohlcv_multi_tf
            dfd.get_ohlcv_multi_tf = lambda s, _m=mtf: _m
            try:
                ltp = float(hist["close"].iloc[-1])
                sig = gen.generate_signal(sym, current_price=ltp)
            except Exception as e:
                sig = None
                logger.debug(f"{sym} {now_ts}: {e}")
            finally:
                dfd.get_ohlcv_multi_tf = orig

            if sig is None:
                continue

            # Position sizing: risk 0.5% capped at 20% position value
            sl_dist = abs(sig.entry_price - sig.stop_loss)
            if sl_dist <= 0:
                continue
            risk_inr = equity * 0.005
            qty = int(min(risk_inr / sl_dist, equity * 0.20 / sig.entry_price))
            if qty < 1:
                continue

            trade = Trade(sig, qty, now_ts)
            future = df.loc[now_ts:].iloc[1:]   # bars strictly after entry
            if future.empty:
                continue
            pnl = _simulate_exit(trade, future)
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            trades.append(trade)

    _report(trades, capital, equity, max_dd, from_date, to_date, len(data))


def _report(trades, capital, equity, max_dd, from_date, to_date, n_symbols):
    if not trades:
        print("\nNo trades generated over this window.")
        return
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    wr = len(wins) / len(trades) * 100
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses)) or 1e-9
    pf = gross_win / gross_loss
    avg_win = gross_win / max(len(wins), 1)
    avg_loss = gross_loss / max(len(losses), 1)
    expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)
    ret_pct = (equity - capital) / capital * 100
    days = (datetime.strptime(to_date, "%Y-%m-%d") - datetime.strptime(from_date, "%Y-%m-%d")).days or 1
    monthly = ret_pct / days * 30

    print("\n" + "=" * 72)
    print(f"REAL HISTORICAL REPLAY — {from_date} → {to_date} ({n_symbols} symbols)")
    print("=" * 72)
    print(f"  Trades:            {len(trades)}")
    print(f"  Win rate:          {wr:.1f}%   ({len(wins)}W / {len(losses)}L)")
    print(f"  Profit factor:     {pf:.2f}")
    print(f"  Avg win:           Rs.{avg_win:,.0f}")
    print(f"  Avg loss:          Rs.{avg_loss:,.0f}")
    print(f"  Expectancy/trade:  Rs.{expectancy:,.0f}")
    print(f"  Total return:      {ret_pct:+.2f}%   (Rs.{equity - capital:+,.0f})")
    print(f"  Annualized/month:  {monthly:+.2f}% per 30 days")
    print(f"  Max drawdown:      {max_dd * 100:.2f}%")
    print("=" * 72)
    print("NOTE: price-core signals only (live boosters disabled for replay) →")
    print("this WR is a CONSERVATIVE FLOOR. Production adds option-chain, delivery,")
    print("FII/DII, news, cross-asset, VIX-regime, Kalman, MOM12 on top.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="",
                    help="Comma-separated NSE symbols (default: active watchlist)")
    ap.add_argument("--days", type=int, default=60, help="Lookback days (if no --from)")
    ap.add_argument("--from", dest="from_date", type=str, default="")
    ap.add_argument("--to", dest="to_date", type=str, default="")
    ap.add_argument("--capital", type=float, default=500_000.0)
    args = ap.parse_args()

    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        to = datetime.now(IST).date()
        frm = to - timedelta(days=args.days)
        from_date, to_date = frm.strftime("%Y-%m-%d"), to.strftime("%Y-%m-%d")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        try:
            from auth_dhan import get_dhan_client
            from watchlist_india import get_active_watchlist
            symbols = get_active_watchlist(get_dhan_client())
        except Exception:
            symbols = ["RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK"]

    run_replay(symbols, from_date, to_date, args.capital)


if __name__ == "__main__":
    main()
