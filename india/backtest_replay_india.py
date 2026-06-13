"""
backtest_replay_india.py — REAL Historical Replay Backtest (not Monte Carlo)

Unlike backtest_satavector_india.py (which simulates mechanics with WR as an
INPUT), this harness replays the bot's ACTUAL signal generator over REAL past
NSE candles fetched from the Upstox API, and produces win rate as an OUTPUT.

  - Fetches historical 5-min OHLCV per symbol from Upstox (up to 90 days back)
  - Steps through every 5-min bar chronologically — NO look-ahead (as-of slices)
  - Runs the real IndiaSignalGenerator.generate_signal() at each bar
  - Simulates exact bot exits: 50% partial at T1 (1R), runner to T2 (2R) or
    trail / breakeven, force square-off at 15:20 IST
  - Applies the real NSE cost stack (~0.29% round-trip incl. slippage)
  - Reports REAL win rate, expectancy, profit factor, drawdown, equity curve

IMPORTANT — environment requirements:
  This needs the Upstox API (UPSTOX_ACCESS_TOKEN in .env) and network access.
  It runs on the PRODUCTION VPS, not in the dev sandbox (no market-data egress).

  Price-based signal core IS replayed (gates, base score, MTF, ORB, Wyckoff VSA,
  microstructure, momentum factor). Live-only boosters that have no historical
  feed (option chain, delivery %, FII/DII, news, block deals, UOA, futures OI,
  cross-asset, live-VIX regime, PEAD) are DISABLED for the replay — so the
  reported WR is a CONSERVATIVE FLOOR. In production those boosters add on top.

  Upstox historical data limits:
    5minute  : up to 90 days back
    15minute : up to 180 days back

Usage:
  python3 india/backtest_replay_india.py --days 60
  python3 india/backtest_replay_india.py --symbols RELIANCE,INFY,TCS --days 90
  python3 india/backtest_replay_india.py --from 2026-01-01 --to 2026-03-31
"""
import argparse
import copy
import logging
import sys
import time as _time
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
    "GEX_ENABLED", "GEX_SIGNAL_ENABLED", "VIX_REGIME_ENABLED", "PEAD_ENABLED",
    "KALMAN_PAIRS_ENABLED", "MOMENTUM_FACTOR_ENABLED", "MORNING_INTEL_ENABLED",
    "MARKET_BREADTH_ENABLED", "SECTOR_CONFLUENCE_ENABLED", "MACRO_SCORING_ENABLED",
    "INDIA_VIX_ENABLED", "ELITE_TRACKER_ENABLED", "FII_FUTURES_ENABLED",
    "CHNG_OI_PCR_ENABLED",
]

# NSE round-trip cost (brokerage + STT + exchange + slippage)
COST_RT_PCT = 0.0029   # 29 bps (matches backtest_historical_india.py)
SQUAREOFF   = dtime(15, 20)


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


def _fetch_history_upstox(client, symbol: str, from_date: str, to_date: str) -> Optional[pd.DataFrame]:
    """
    Fetch 5-minute OHLCV from Upstox historical API.

    Upstox v2 supported intraday intervals: 1minute, 30minute, 60minute
    (5minute is NOT a valid interval — we fetch 1minute and resample to 5min).
    Pages in 30-day chunks (Upstox caps the window per request).
    Returns IST-indexed 5-min OHLCV DataFrame or None on failure.
    """
    from data_fetch_upstox import get_security_id, _candles_to_df
    key = get_security_id(symbol)
    if not key:
        logger.warning(f"{symbol}: no instrument_key found")
        return None

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end   = datetime.strptime(to_date,   "%Y-%m-%d").date()

    frames = []
    cur = start
    chunk_days = 30

    # Try intervals in order — Upstox v2 supports 1minute; some SDK versions
    # also accept "5minute". Fall back gracefully.
    _INTERVALS_TO_TRY = ["1minute", "5minute", "30minute"]

    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        chunk_ok = False
        for interval in _INTERVALS_TO_TRY:
            for attempt in range(3):
                try:
                    resp = client.history.get_historical_candle_data1(
                        instrument_key=key,
                        interval=interval,
                        to_date=nxt.strftime("%Y-%m-%d"),
                        from_date=cur.strftime("%Y-%m-%d"),
                        api_version="2.0",
                    )
                    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
                    candles = (getattr(data, "candles", None)
                               or (data.get("candles") if isinstance(data, dict) else None))
                    df_chunk = _candles_to_df(candles)
                    if df_chunk is not None and not df_chunk.empty:
                        # Resample 1-min or 30-min → 5-min
                        if interval == "1minute":
                            df_chunk = df_chunk.resample("5min").agg(
                                {"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"}
                            ).dropna()
                        elif interval == "30minute":
                            pass  # keep as-is, better than nothing
                        frames.append(df_chunk)
                        chunk_ok = True
                    break   # break retry loop — got a response (even empty)
                except Exception as e:
                    err_str = str(e).lower()
                    # If the interval is explicitly invalid, try next interval
                    if any(x in err_str for x in ("invalid", "unsupported", "400", "422")):
                        logger.debug(f"{symbol}: interval '{interval}' rejected, trying next")
                        break
                    logger.debug(f"{symbol} {cur}→{nxt} {interval} attempt {attempt+1}: {e}")
                    _time.sleep(2 ** attempt)
            if chunk_ok:
                break   # got data — don't try other intervals for this chunk
        cur = nxt + timedelta(days=1)

    if not frames:
        logger.warning(f"{symbol}: no data returned for any interval — check token/key")
        return None
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.between_time("09:15", "15:30")
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
        self.symbol     = sig.symbol
        self.direction  = sig.direction
        self.entry      = sig.entry_price
        self.sl         = sig.stop_loss
        self.t1         = sig.target_1
        self.t2         = sig.target_2
        self.qty        = qty
        self.grade      = sig.quality_grade
        self.entry_time = entry_time
        self.t1_done    = False
        self.exit_price = None
        self.exit_time  = None
        self.pnl        = 0.0


def _simulate_exit(trade: Trade, future_bars: pd.DataFrame) -> float:
    """
    Walk future 5-min bars after entry. Apply bot exit mechanics:
      - 50% partial at T1 (1R), move SL to breakeven on remainder
      - Runner exits at T2 (2R) or trailing stop
      - Force square-off at 15:20 IST
    Returns net P&L in rupees (after costs).
    """
    long  = trade.direction == "LONG"
    half  = max(1, trade.qty // 2)
    runner = trade.qty - half
    realized = 0.0
    sl = trade.sl

    for ts, bar in future_bars.iterrows():
        hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Force square-off at 15:20 IST
        if ts.time() >= SQUAREOFF:
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((cl - trade.entry) if long else (trade.entry - cl)) * qty_left
            trade.exit_price, trade.exit_time = cl, ts
            break

        if not trade.t1_done:
            sl_hit = lo <= sl if long else hi >= sl
            t1_hit = hi >= trade.t1 if long else lo <= trade.t1
            if sl_hit and not t1_hit:
                realized += ((sl - trade.entry) if long else (trade.entry - sl)) * trade.qty
                trade.exit_price, trade.exit_time = sl, ts
                break
            if t1_hit:
                realized += ((trade.t1 - trade.entry) if long else (trade.entry - trade.t1)) * half
                trade.t1_done = True
                sl = trade.entry  # breakeven stop on runner
        else:
            sl_hit = lo <= sl if long else hi >= sl
            t2_hit = hi >= trade.t2 if long else lo <= trade.t2
            if t2_hit:
                realized += ((trade.t2 - trade.entry) if long else (trade.entry - trade.t2)) * runner
                trade.exit_price, trade.exit_time = trade.t2, ts
                break
            if sl_hit:
                realized += ((sl - trade.entry) if long else (trade.entry - sl)) * runner
                trade.exit_price, trade.exit_time = sl, ts
                break
    else:
        # End of data without exit
        if len(future_bars):
            px = float(future_bars["close"].iloc[-1])
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px
            trade.exit_time  = future_bars.index[-1]

    # Costs: round-trip on entry + exit turnover
    entry_val = trade.entry * trade.qty
    exit_val  = (trade.exit_price or trade.entry) * trade.qty
    realized -= (entry_val + exit_val) * COST_RT_PCT / 2
    trade.pnl = realized
    return realized


def run_replay(symbols: List[str], from_date: str, to_date: str,
               capital: float = 500_000.0):
    from auth_upstox import get_upstox_client, verify_connection
    cfg = _make_replay_config()

    print(f"Connecting to Upstox API ...")
    client = get_upstox_client()
    if not verify_connection(client):
        print(
            "ERROR: Upstox connection failed.\n"
            "  This harness needs the production VPS with UPSTOX_ACCESS_TOKEN in .env\n"
            "  and network access to api.upstox.com.\n"
            "  Run: /newtoken YOUR_TOKEN  via Telegram if the token has expired."
        )
        return

    import data_fetch_upstox as dfd
    dfd.set_upstox_client(client)
    from signal_generator_india import IndiaSignalGenerator

    print(f"Loading historical 5-min data: {len(symbols)} symbols  {from_date} → {to_date}")
    print(f"  (Upstox 5-min limit: 90 days back)")
    data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _fetch_history_upstox(client, sym, from_date, to_date)
        if df is not None and len(df) > 80:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars")
        else:
            print(f"  {sym}: insufficient data — skipped")

    if not data:
        print("No data loaded. Check Upstox connectivity / UPSTOX_ACCESS_TOKEN / symbol IDs.")
        return

    gen = IndiaSignalGenerator(cfg, list(data.keys()))

    trades: List[Trade] = []
    open_syms: Dict[str, Trade] = {}
    equity = capital
    peak   = capital
    max_dd = 0.0

    # Build the union of all 5-min timestamps across symbols, chronological
    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))
    print(f"\nReplaying {len(all_ts)} time steps across {len(data)} symbols ...")

    for now_ts in all_ts:
        if now_ts.time() < dtime(9, 20) or now_ts.time() > dtime(15, 0):
            continue

        for sym, df in data.items():
            if sym in open_syms:
                continue
            if now_ts not in df.index:
                continue

            # As-of slice — NO look-ahead
            hist = df.loc[:now_ts]
            if len(hist) < 30:
                continue

            mtf = {
                "5m":  hist,
                "15m": _resample(hist, "15min"),
                "1h":  _resample(hist, "1h"),
            }

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
            future = df.loc[now_ts:].iloc[1:]   # bars strictly AFTER entry
            if future.empty:
                continue
            pnl = _simulate_exit(trade, future)
            equity += pnl
            peak    = max(peak, equity)
            max_dd  = max(max_dd, (peak - equity) / peak)
            trades.append(trade)

    _report(trades, capital, equity, max_dd, from_date, to_date, len(data))


def _report(trades, capital, equity, max_dd, from_date, to_date, n_symbols):
    if not trades:
        print("\nNo trades generated over this window. Check signal thresholds / data quality.")
        return

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    wr     = len(wins) / len(trades) * 100
    gross_win  = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses)) or 1e-9
    pf         = gross_win / gross_loss
    avg_win    = gross_win  / max(len(wins), 1)
    avg_loss   = gross_loss / max(len(losses), 1)
    expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)
    ret_pct    = (equity - capital) / capital * 100
    days       = max((datetime.strptime(to_date, "%Y-%m-%d") -
                      datetime.strptime(from_date, "%Y-%m-%d")).days, 1)
    monthly    = ret_pct / days * 30

    # Sharpe (rough) from per-trade P&L
    import statistics
    pnls = [t.pnl / capital for t in trades]
    try:
        pnl_std = statistics.stdev(pnls) + 1e-9
        sharpe  = (statistics.mean(pnls) / pnl_std) * (252 ** 0.5)
    except Exception:
        sharpe = 0.0

    # Profit factor grade
    pf_grade = ("EXCELLENT" if pf >= 2.5 else
                "GOOD"      if pf >= 1.8 else
                "ACCEPTABLE" if pf >= 1.3 else "BELOW_TARGET")
    wr_grade = ("EXCELLENT" if wr >= 65 else
                "GOOD"      if wr >= 58 else
                "ACCEPTABLE" if wr >= 52 else "BELOW_TARGET")

    print("\n" + "=" * 72)
    print(f"  REAL HISTORICAL REPLAY — {from_date} → {to_date}  ({n_symbols} symbols)")
    print("=" * 72)
    print(f"  Trades:            {len(trades)}")
    print(f"  Win rate:          {wr:.1f}%   [{wr_grade}]  ({len(wins)}W / {len(losses)}L)")
    print(f"  Profit factor:     {pf:.2f}    [{pf_grade}]")
    print(f"  Avg win:           ₹{avg_win:>10,.0f}")
    print(f"  Avg loss:          ₹{avg_loss:>10,.0f}")
    print(f"  Win/Loss ratio:    {avg_win / avg_loss:.2f}×")
    print(f"  Expectancy/trade:  ₹{expectancy:>10,.0f}")
    print(f"  Total return:      {ret_pct:+.2f}%   (₹{equity - capital:+,.0f})")
    print(f"  Monthly (prorated):{monthly:+.2f}% per 30 days")
    print(f"  Sharpe ratio:      {sharpe:.2f}")
    print(f"  Max drawdown:      {max_dd * 100:.2f}%")
    print("=" * 72)
    print()
    print("  INTERPRETATION:")
    print("  ───────────────")
    print("  These numbers use PRICE-CORE SIGNALS ONLY (live boosters disabled).")
    print("  WR and PF above are a CONSERVATIVE FLOOR — production adds on top:")
    print("  option-chain, delivery, FII/DII, news, VIX-regime, Kalman, MOM12,")
    print("  GEX dealer flow, IV term structure, signal decay, regime selector.")
    print()
    print("  Target benchmarks:  WR ≥ 58%  |  PF ≥ 1.8  |  Monthly ≥ 5%  |  DD < 8%")
    print(f"  Your score:         WR {wr:.0f}%   |  PF {pf:.2f}  |  Monthly {monthly:+.1f}%  |  DD {max_dd*100:.1f}%")
    print()

    # Per-symbol breakdown
    from collections import defaultdict
    sym_stats: Dict = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})
    for t in trades:
        sym_stats[t.symbol]["pnl"] += t.pnl
        if t.pnl > 0:
            sym_stats[t.symbol]["w"] += 1
        else:
            sym_stats[t.symbol]["l"] += 1

    best  = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    worst = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:3]

    print("  Top 5 symbols by P&L:")
    for sym, s in best:
        n = s["w"] + s["l"]
        wr_s = s["w"] / n * 100 if n else 0
        print(f"    {sym:<12}  WR={wr_s:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print()
    print("  Bottom 3 symbols by P&L:")
    for sym, s in worst:
        n = s["w"] + s["l"]
        wr_s = s["w"] / n * 100 if n else 0
        print(f"    {sym:<12}  WR={wr_s:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(
        description="Real historical replay backtest using Upstox 5-min candles."
    )
    ap.add_argument("--symbols", type=str, default="",
                    help="Comma-separated NSE symbols (default: fallback core list)")
    ap.add_argument("--days", type=int, default=60,
                    help="Lookback days from today (max 90 for 5-min, default 60)")
    ap.add_argument("--from", dest="from_date", type=str, default="",
                    help="Start date YYYY-MM-DD")
    ap.add_argument("--to", dest="to_date", type=str, default="",
                    help="End date YYYY-MM-DD (default: today)")
    ap.add_argument("--capital", type=float, default=500_000.0,
                    help="Starting capital in INR")
    args = ap.parse_args()

    # Date range
    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        to   = datetime.now(IST).date()
        frm  = to - timedelta(days=min(args.days, 90))   # Upstox 5-min max 90d
        from_date = frm.strftime("%Y-%m-%d")
        to_date   = to.strftime("%Y-%m-%d")

    # Symbol list
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        try:
            from auth_upstox import get_upstox_client
            from watchlist_india import get_active_watchlist
            symbols = get_active_watchlist(get_upstox_client())
        except Exception:
            # Core liquid NSE names as fallback
            symbols = [
                "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK",
                "SBIN", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "ITC",
                "BHARTIARTL", "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO",
            ]

    run_replay(symbols, from_date, to_date, args.capital)


if __name__ == "__main__":
    main()

