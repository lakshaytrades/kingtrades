"""
live_pilot.py — ₹5,000 live pilot that trades the EXACT backtested edge.

WHAT THIS IS
------------
The honest research chain (orb_fast -> strategy_lab -> cost_model) found that on
liquid high-volatility NSE names, at your REAL Groww cost (~0.18% round-trip),
two long-only edges are profitable and walk-forward robust:
    VALIDATED config (profit_optimizer on 1-year data): fresh ORB break + rvol>=4.0,
    fixed stop 1.5xATR (risk = 1), target capped at 3R (MAX_RR), hard 15:25 square-off.
    Result: +3.9%/mo, walk-forward +3.4%, positive in EVERY window, WR 44%, DD 14%.
    High rvol = fewer/stronger trades = less cost drag = robust net profit at ~0.18% cost
    (i.e. at ₹1L+ position size). At ₹5k pilot size the cost is higher, so the pilot
    measures slippage rather than profits.
      MOM_EARLY (9:30-11:00) and MOM_LATE (11:00-14:00): both 1.5xATR stop, 3R target.
The existing main_india.py runs the OLD buggy engine, so it would NOT trade this
edge. This module reproduces the backtest signal EXACTLY on live data and trades
it, with hard pilot-grade safety.

SAFETY (read before running)
----------------------------
* PAPER BY DEFAULT. Real orders are placed ONLY if env INDIA_LIVE_TRADING_ENABLED
  is exactly 'true'. Otherwise every order is logged, nothing is sent.
* HARD ₹5,000 CAP, NO LEVERAGE. Total exposure across all positions can never
  exceed MAX_PILOT_CAPITAL; each position is capped at MAX_PILOT_CAPITAL/MAX_OPEN.
* Long-only, MIS intraday, hard square-off at 15:25 IST.
* Kill switch: create a file named KILL in the repo root -> flat + stop.
* Slippage is the real unknown the backtest can't model. The pilot's PURPOSE is to
  measure it: every entry logs the signal trigger price AND the actual fill, so you
  can compute real slippage and confirm (or kill) the edge with tiny money at risk.

USAGE
-----
  # paper (safe) — verify signals fire correctly on live data:
  python3 india/live_pilot.py
  # go live with ₹5k (only after you've seen paper signals look right):
  INDIA_LIVE_TRADING_ENABLED=true python3 india/live_pilot.py
"""

from __future__ import annotations

import logging
import os
import sys
import time as _time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

import orb_fast as of
from fetch_midcaps import HIGH_VOL_UNIVERSE

IST = ZoneInfo("Asia/Kolkata")

# Most-liquid subset — tightest spreads => lowest slippage on breakout entries.
# Trading only these gives the pilot the cleanest possible slippage measurement.
TOP_LIQUID = [
    "TATASTEEL", "TATAMOTORS", "SAIL", "PNB", "BANKBARODA", "IDFCFIRSTB",
    "ADANIPORTS", "ADANIENT", "TATAPOWER", "GAIL", "IOC", "BPCL",
    "HINDALCO", "VEDL", "ZOMATO", "IRFC", "NMDC", "CANBK",
]

# Return-vs-cost curve for the VALIDATED config (rvol>=4, SL 1.5xATR, 3:1), used at
# end-of-day to translate MEASURED slippage -> expected %/mo at real scale. Anchored on
# profit_optimizer: +3.9%/mo at 0.18% cost. Fewer trades (~39/mo) => flatter curve than
# the old high-frequency config. ESTIMATE — refine with: profit_optimizer.py at several
# --cost values for an exact curve.
_RET_COSTS = [0.0000, 0.0010, 0.0018, 0.0025, 0.0035, 0.0045]
_RET_VALS  = [12.0,   7.0,    3.9,    0.5,    -4.0,   -8.0]
TARGET_MO  = 4.0

# ── Hard safety limits ────────────────────────────────────────────────────────
MAX_PILOT_CAPITAL = 5000.0      # absolute cap on total exposure (NO leverage)
MAX_OPEN          = 2           # max concurrent positions
PER_POS_CAP       = MAX_PILOT_CAPITAL / MAX_OPEN
MAX_DAILY_LOSS    = 300.0       # ₹: stop ALL new entries for the day if realized loss hits this
STALE_FEED_CYCLES = 4           # force-flatten an open position if its price is unavailable this many loops
ENTRY_START       = dtime(9, 30)
ENTRY_CUTOFF      = dtime(14, 0)   # matches the validated backtest window (9:30-14:00)
RANGE_CUTOFF      = dtime(11, 0)   # split point between the two entry-time labels
SQUAREOFF         = dtime(15, 25)
MARKET_OPEN       = dtime(9, 15)
LOOP_SECONDS      = 45
KILL_FILE         = _ROOT / "KILL"

# ── Professional, disciplined exit rules ──────────────────────────────────────
# Every trade has: a fixed stop (the "1" of risk), a capped target at MAX_RR x risk,
# and a hard 15:25 square-off backstop. No open-ended holds, no 6:1 moonshots.
# Validated by profit_optimizer.py on 1-year data: rvol>=4.0 + SL 1.5xATR + 3:1 was
# the best ROBUST config (+3.9%/mo, walk-forward +3.4%, positive in EVERY window,
# WR 44%, DD 14%, PF 1.16). High rvol = fewer/stronger trades = less cost drag.
RV_MIN     = 4.0     # selectivity: only strong volume breakouts (validated sweet spot)
WIDE_SL    = 1.5     # ATR mult — stop
HOLD_SL    = 1.5     # ATR mult — stop (unified to the validated 1.5xATR)
MAX_RR     = 3.0     # reward capped at 3x risk for EVERY trade (risk 1 : reward <= 3)
TRIGGER_BUF = of.TRIGGER_BUF

# ── Logging ───────────────────────────────────────────────────────────────────
(_ROOT / "logs").mkdir(exist_ok=True)
_logfile = _ROOT / "logs" / f"live_pilot_{datetime.now(IST):%Y-%m-%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="[IST %(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(_logfile), logging.StreamHandler()],
)
logging.Formatter.converter = lambda *a: datetime.now(IST).timetuple()
log = logging.getLogger("pilot")


def now_ist() -> datetime:
    return datetime.now(IST)


def market_phase(t: dtime) -> str:
    if t < MARKET_OPEN:           return "pre"
    if t < ENTRY_START:           return "opening"     # 9:15-9:30 build ORB
    if t < SQUAREOFF:             return "trading"
    if t < dtime(15, 30):         return "squareoff"
    return "closed"


# ── Signal detection on the latest CLOSED 5-min bar ───────────────────────────

def _last_closed_idx(d, now: datetime) -> int | None:
    """Index of the most recent FULLY CLOSED 5-min bar (avoids the forming bar)."""
    if len(d) < 60:
        return None
    import pandas as pd
    # bar at start T is complete once now >= T + 5min
    cutoff = now - pd.Timedelta(minutes=5)
    closed = d.index[d.index <= cutoff]
    if len(closed) == 0:
        return None
    return d.index.get_loc(closed[-1])


def detect_signal(d, now: datetime):
    """Return (strategy, entry_trigger, sl_dist) if the last closed bar is a fresh
    ORB breakout meeting either strategy's rules; else None. Mirrors strategy_lab."""
    i = _last_closed_idx(d, now)
    if i is None or i < 55:
        return None
    row, prev = d.iloc[i], d.iloc[i - 1]
    t = d.index[i].time()
    c, pc = float(row["close"]), float(prev["close"])
    orb_h = float(row.get("orb_high", 0) or 0)
    rvol  = float(row.get("rvol", 0) or 0)
    atr   = float(row.get("atr", 0) or 0)
    if orb_h <= 0 or atr <= 0:
        return None
    trig = orb_h * TRIGGER_BUF
    fresh = (c > trig) and (pc <= trig)            # first close above the ORB high
    if not (fresh and rvol >= RV_MIN):
        return None
    # same entry + same 1.5xATR stop + same 3R target; only the time-label differs
    if ENTRY_START <= t < RANGE_CUTOFF:
        return ("MOM_EARLY", trig, HOLD_SL * atr, atr)     # 1.5xATR stop
    if RANGE_CUTOFF <= t < ENTRY_CUTOFF:
        return ("MOM_LATE", trig, WIDE_SL * atr, atr)      # 1.5xATR stop
    return None


# ── Pilot ─────────────────────────────────────────────────────────────────────

class Pilot:
    def __init__(self, universe=None):
        from auth_upstox import get_upstox_client, verify_connection
        import data_fetch_upstox as dfu
        from execution_upstox import get_executor

        self.live = os.getenv("INDIA_LIVE_TRADING_ENABLED", "").strip().lower() == "true"
        self.client = get_upstox_client()
        if not self.client or not verify_connection(self.client):
            log.error("Upstox connection failed — refresh token (get_upstox_token.py).")
            sys.exit(1)
        dfu.set_upstox_client(self.client)
        self.dfu = dfu
        self.exec = get_executor(self.client, live_enabled=self.live)
        self.universe = universe or HIGH_VOL_UNIVERSE
        self.positions: dict = {}      # sym -> {qty, entry, sl, target, strat, sec_id, t_in, trigger}
        self.done_today: set = set()   # one trade per symbol per day
        # ── safety state ──
        self.realized_pnl = 0.0        # ₹ realized today; drives the daily-loss circuit breaker
        self.halted = False            # True once MAX_DAILY_LOSS hit -> no new entries
        self.ltp_fail: dict = {}       # sym -> consecutive price-fetch failures (stale-feed guard)
        # slippage / fill-quality measurement — the whole point of the pilot
        self.stats = {"signals": 0, "filled": 0, "chased_skip": 0,
                      "too_pricey": 0, "slips_bps": [], "net_pcts": []}
        mode = "LIVE (REAL ₹)" if self.live else "PAPER (no orders)"
        log.warning(f"=== LIVE PILOT START — {mode} | cap ₹{MAX_PILOT_CAPITAL:.0f} | "
                    f"max {MAX_OPEN} pos | {len(self.universe)} symbols ===")
        if self.live:
            log.warning("REAL ORDERS ENABLED. Hard ₹%.0f cap, no leverage.", MAX_PILOT_CAPITAL)

    def deployed(self) -> float:
        return sum(p["qty"] * p["entry"] for p in self.positions.values())

    # ── Fresh-data helpers ───────────────────────────────────────────────────
    # data_fetch_upstox caches OHLCV ~290s and LTP ~30s — far too long for a
    # 5-min-bar strategy on a 45s loop. We clear the per-symbol cache entry before
    # each read so the pilot always acts on fresh bars/prices. (We don't change the
    # global TTLs — other modules rely on them.)
    def _fresh_ohlcv(self, sym):
        try:
            self.dfu._ohlcv_cache.pop(f"{sym}_5m", None)
        except Exception:
            pass
        return self.dfu.get_ohlcv(sym, "5m", "5d")

    def _fresh_ltp(self, sym):
        try:
            self.dfu._quote_cache.pop(sym, None)
            self.dfu._quote_cache_ts.pop(sym, None)
        except Exception:
            pass
        return self.dfu.get_ltp(sym)

    def scan(self, now: datetime):
        # Daily-loss circuit breaker: once realized loss hits the limit, take NO new
        # entries for the rest of the day (existing positions keep their own stops).
        if not self.halted and self.realized_pnl <= -MAX_DAILY_LOSS:
            self.halted = True
            log.warning(f"DAILY LOSS LIMIT hit: realized ₹{self.realized_pnl:.0f} "
                        f"(limit −₹{MAX_DAILY_LOSS:.0f}) — NO new entries today.")
        if self.halted:
            return
        if len(self.positions) >= MAX_OPEN:
            return
        for sym in self.universe:
            if sym in self.positions or sym in self.done_today:
                continue
            if len(self.positions) >= MAX_OPEN:
                break
            if KILL_FILE.exists():          # stop entering the instant KILL appears
                return
            try:
                raw = self._fresh_ohlcv(sym)       # fix: bypass 290s OHLCV cache
                if raw is None or len(raw) < 60:
                    continue
                d = of._prep_symbol(raw)
                if d is None:
                    continue
                sig = detect_signal(d, now)
                if not sig:
                    continue
                self.stats["signals"] += 1
                strat, trigger, sl_dist, atr = sig
                self._enter(sym, strat, trigger, sl_dist, atr)
            except Exception as e:
                log.debug(f"scan {sym}: {e}")

    def _enter(self, sym, strat, trigger, sl_dist, atr):
        sec_id = self.dfu.get_security_id(sym)
        if not sec_id:
            log.info(f"{sym}: no instrument_key — skip"); return
        ltp = self._fresh_ltp(sym) or trigger          # fresh price for sizing/SL
        # never chase: skip if price already ran > 0.3% past the trigger
        if ltp > trigger * 1.003:
            self.stats["chased_skip"] += 1
            log.info(f"{sym} {strat}: price {ltp:.2f} already > trigger {trigger:.2f}+0.3% — skip")
            return
        budget = min(PER_POS_CAP, MAX_PILOT_CAPITAL - self.deployed())
        qty = int(budget // ltp)                       # NO leverage: notional <= budget
        if qty < 1:
            self.stats["too_pricey"] += 1
            log.info(f"{sym} {strat}: too pricey for ₹{budget:.0f} (ltp {ltp:.2f}) — skip")
            return
        # skip if the 3:1 target can't clear the round-trip cost (matches the backtest's
        # _mk filter) — avoids taking a trade that can't profit even if it hits target.
        if (MAX_RR * sl_dist) / ltp < of.COST_RT_PCT:
            self.stats["too_pricey"] += 1
            log.info(f"{sym} {strat}: 3R target too small to clear costs — skip")
            return
        # mark done_today BEFORE the (blocking) order call so a retry can't double-fire
        self.done_today.add(sym)
        res = self.exec.place_entry_order_limit(sym, "LONG", qty, trigger, sec_id,
                                                limit_offset_pct=0.001)
        # CRITICAL: only record a position for the qty that ACTUALLY filled. A
        # rejected/timed-out order (filled=0) must NOT create a phantom position —
        # otherwise the exit would SELL shares we don't own and open a short.
        filled = int(res.quantity or 0)
        if not res.success or filled < 1 or not res.fill_price:
            log.warning(f"{sym} {strat}: entry not filled (ok={res.success} "
                        f"qty={filled} px={res.fill_price}) — no position recorded")
            return
        fill = res.fill_price
        # SL and 3:1 target anchored to the ACTUAL fill so real risk:reward is exactly
        # 1:MAX_RR. (Previously computed from pre-entry ltp, so slippage skewed the ratio.)
        sl = round(fill - sl_dist, 2)
        target = round(fill + MAX_RR * sl_dist, 2)
        slip_bps = (fill - trigger) / trigger * 1e4
        self.stats["filled"] += 1
        self.stats["slips_bps"].append(slip_bps)
        self.positions[sym] = {"qty": filled, "entry": fill, "sl": sl,
                               "target": target, "strat": strat, "sec_id": sec_id,
                               "t_in": now_ist(), "trigger": trigger}
        log.warning(f"ENTER {strat} {sym} qty={filled} trig={trigger:.2f} "
                    f"fill={fill:.2f} slip={slip_bps:+.1f}bps SL={sl:.2f} "
                    f"tgt={target or 'hold'} notional=₹{filled*fill:.0f}")

    def manage(self, now: datetime):
        # Per-position try/except: an API error on ONE symbol must never crash the
        # loop or prevent the OTHER positions' stops from being checked.
        for sym in list(self.positions.keys()):
            try:
                p = self.positions.get(sym)
                if p is None:
                    continue
                ltp = self._fresh_ltp(sym)             # fresh price for stop checks
                if ltp is None:
                    # Stale-feed guard: never sit BLIND on an open position. After a few
                    # failed price reads we can't enforce the stop, so flatten it.
                    self.ltp_fail[sym] = self.ltp_fail.get(sym, 0) + 1
                    if self.ltp_fail[sym] >= STALE_FEED_CYCLES:
                        log.error(f"{sym}: price unavailable {self.ltp_fail[sym]}x — "
                                  f"FORCE-FLATTEN (cannot manage blind)")
                        self._exit(sym, p["entry"], "STALE_FEED")   # market sell; px is log-only
                    else:
                        log.warning(f"{sym}: LTP unavailable ({self.ltp_fail[sym]}/"
                                    f"{STALE_FEED_CYCLES}) — stop not checked this cycle")
                    continue
                self.ltp_fail[sym] = 0                 # feed recovered
                reason = None
                if ltp <= p["sl"]:
                    reason = "SL"
                elif p["target"] and ltp >= p["target"]:
                    reason = "TARGET"
                if reason:
                    self._exit(sym, ltp, reason)
            except Exception as e:
                log.error(f"{sym}: manage() error: {e} — position still OPEN, retry next cycle")

    def _exit(self, sym, ltp, reason):
        p = self.positions.pop(sym)
        self.ltp_fail.pop(sym, None)
        # close a long = SELL MARKET (reuses tested executor path)
        res = self.exec.place_entry_order(sym, "SHORT", p["qty"], ltp, p["sec_id"])
        px = res.fill_price or ltp
        pnl_pct = (px - p["entry"]) / p["entry"] * 100 - of.COST_RT_PCT * 100
        # realized ₹ P&L (net of round-trip cost) — feeds the daily-loss breaker
        pnl_r = (px - p["entry"]) * p["qty"] - (p["entry"] + px) * p["qty"] * of.COST_RT_PCT / 2
        self.realized_pnl += pnl_r
        self.stats["net_pcts"].append(pnl_pct)
        log.warning(f"EXIT {reason} {p['strat']} {sym} @ {px:.2f} "
                    f"(entry {p['entry']:.2f}) net~{pnl_pct:+.2f}% (₹{pnl_r:+.0f}) "
                    f"qty={p['qty']} | day P&L ₹{self.realized_pnl:+.0f}")

    def squareoff_all(self, why: str):
        if not self.positions:
            # still run the broker straggler check below in case our dict drifted
            closed_syms = set()
        else:
            log.warning(f"SQUARE-OFF ALL ({why}) — {len(self.positions)} open")
            closed_syms = set()
            for sym in list(self.positions.keys()):
                try:
                    ltp = self._fresh_ltp(sym) or self.positions[sym]["entry"]
                    self._exit(sym, ltp, why)
                    closed_syms.add(sym)
                except Exception as e:
                    log.error(f"{sym}: squareoff exit error: {e} — broker check will catch it")
        # belt-and-suspenders (live only): flatten any broker positions we did NOT
        # just close ourselves. CRITICAL: exclude closed_syms so we never SELL the
        # same symbol twice (which would open an unwanted short).
        if self.live:
            try:
                pos = [p for p in self.exec.get_open_positions()
                       if p.get("symbol") not in closed_syms]
                if pos:
                    log.warning(f"broker straggler flatten: {[p.get('symbol') for p in pos]}")
                    self.exec.square_off_all(pos)
            except Exception as e:
                log.error(f"broker square_off_all: {e}")

    def report(self):
        """End-of-day: translate MEASURED slippage into expected %/mo at real scale.

        This is the deliverable — it answers 'does my real execution let intraday
        hit the +4%/mo target if I scale to ₹1L-₹2L positions?'
        """
        import cost_model as cm
        s = self.stats
        slips = s["slips_bps"]
        avg_slip_side = (sum(slips) / len(slips) / 2 / 100) if slips else None  # %/side
        fill_rate = (s["filled"] / s["signals"] * 100) if s["signals"] else 0.0
        net = s["net_pcts"]
        log.warning("=" * 64)
        log.warning("  PILOT END-OF-DAY REPORT")
        log.warning("=" * 64)
        log.warning(f"  signals={s['signals']} filled={s['filled']} "
                    f"chased-skip={s['chased_skip']} too-pricey={s['too_pricey']}")
        log.warning(f"  fill rate: {fill_rate:.0f}%  (missed fills hurt live returns "
                    f"vs backtest — watch this)")
        if net:
            log.warning(f"  closed trades: {len(net)}  avg net/trade: "
                        f"{sum(net)/len(net):+.2f}%  total: {sum(net):+.2f}%")
        if avg_slip_side is None:
            log.warning("  No fills yet — need live trades to measure slippage.")
            log.warning("=" * 64); return
        log.warning(f"  MEASURED slippage: {avg_slip_side:.3f}% per side")
        # translate to expected %/mo at scale, for a few position sizes
        log.warning("  -> expected %/mo at real scale (RANGE_BREAK_HOLD 1-yr curve):")
        for size in (50_000, 100_000, 200_000):
            cost_pct = cm.round_trip_cost(size, avg_slip_side)["total_pct"] / 100.0
            exp = float(__import__("numpy").interp(cost_pct, _RET_COSTS, _RET_VALS))
            verdict = "HITS TARGET" if exp >= TARGET_MO else "below target"
            log.warning(f"     ₹{size:>7,} pos -> cost {cost_pct*100:.3f}%  "
                        f"-> {exp:+.1f}%/mo  [{verdict}]")
        log.warning("  Decision: if ≥₹1L positions show 'HITS TARGET' AND fill-rate is")
        log.warning("  healthy (>60%), the edge is real at scale — then build to ₹2-4L")
        log.warning("  risk capital. If 'below target' everywhere, intraday won't hit +4%.")
        log.warning("=" * 64)

    def run(self):
        try:
            while True:
                if KILL_FILE.exists():
                    log.warning("KILL file detected — squaring off and stopping.")
                    self.squareoff_all("KILL"); break
                now = now_ist()
                phase = market_phase(now.time())
                if phase == "closed":
                    log.info("Market closed. Pilot done for the day."); break
                if phase in ("pre", "opening"):
                    log.info(f"{phase} ({now:%H:%M}) — waiting for 9:30 trading window.")
                    _time.sleep(LOOP_SECONDS); continue
                if phase == "squareoff":
                    self.squareoff_all("15:25 squareoff")
                    log.info("Squared off. Stopping for the day."); break
                # trading phase
                self.manage(now)
                self.scan(now)
                log.info(f"{now:%H:%M:%S} | open={len(self.positions)} "
                         f"deployed=₹{self.deployed():.0f}/{MAX_PILOT_CAPITAL:.0f} "
                         f"| {', '.join(self.positions) or 'flat'}")
                _time.sleep(LOOP_SECONDS)
        finally:
            self.report()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-universe", action="store_true",
                    help="trade all high-vol names (default: ~18 most-liquid only)")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    universe = HIGH_VOL_UNIVERSE if args.full_universe else TOP_LIQUID
    log.warning(f"Universe: {'FULL' if args.full_universe else 'TOP-LIQUID'} "
                f"({len(universe)} names) — liquid names = lower slippage = cleaner read")
    Pilot(universe=universe).run()


if __name__ == "__main__":
    main()
