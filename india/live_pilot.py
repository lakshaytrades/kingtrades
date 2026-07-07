"""
live_pilot.py — ₹5,000 live pilot that trades the EXACT backtested edge.

WHAT THIS IS
------------
The honest research chain (orb_fast -> strategy_lab -> cost_model) found that on
liquid high-volatility NSE names, at your REAL Groww cost (~0.18% round-trip),
two long-only edges are profitable and walk-forward robust:
    VALIDATED config (profit_optimizer on 1-year data): fresh ORB break + rvol>=4.0,
    fixed stop 1.5xATR (risk = 1), target capped at 3R (MAX_RR), hard 15:00 square-off.
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
* Long-only, MIS intraday, hard square-off at 15:00 IST (before Upstox RMS).
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
# 15:00 SHARP (user-mandated). Upstox rejects intraday orders from ~15:12 and RMS
# force-squares from ~15:15 WITH A CHARGE (this bit us at the old 15:25 — our own
# sells were rejected and the broker charged the auto-squareoff penalty). 15:00
# leaves ~12 min of retry margin before the rejection wall.
SQUAREOFF         = dtime(15, 0)
SQUAREOFF_RETRY_UNTIL = dtime(15, 10)  # keep retrying failed exits until here
NEAR_CLOSE        = dtime(14, 45)  # from here: tighter loop so 15:00 can't be missed
MARKET_OPEN       = dtime(9, 15)
LOOP_SECONDS      = 45
LOOP_SECONDS_FAST = 15             # loop cadence after NEAR_CLOSE
KILL_FILE         = _ROOT / "KILL"

# ── Professional, disciplined exit rules ──────────────────────────────────────
# Every trade has: a fixed stop (the "1" of risk), a capped target at MAX_RR x risk,
# and a hard 15:00 square-off backstop. No open-ended holds, no 6:1 moonshots.
# Validated by profit_optimizer.py on 1-year data: rvol>=4.0 + SL 1.5xATR + 3:1 was
# the best ROBUST config (+3.9%/mo, walk-forward +3.4%, positive in EVERY window,
# WR 44%, DD 14%, PF 1.16). High rvol = fewer/stronger trades = less cost drag.
RV_MIN     = 4.0     # selectivity: only strong volume breakouts (validated sweet spot)
WIDE_SL    = 1.5     # ATR mult — stop
HOLD_SL    = 1.5     # ATR mult — stop (unified to the validated 1.5xATR)
MAX_RR     = 3.0     # reward capped at 3x risk for EVERY trade (risk 1 : reward <= 3)
TRIGGER_BUF = of.TRIGGER_BUF

# ── LATE profit-lock (trailing stop + breakeven move) ─────────────────────────
# The manage() loop polls every LOOP_SECONDS (well under the "check every ~30 min"
# ask) and ratchets each winner's stop UP so profit gets locked in — while never
# touching a trade that hasn't clearly won yet.
#   * At +LP_ARM_BE_R risk in profit -> move the stop to the entry (breakeven): the
#     trade can no longer become a loss.
#   * At +LP_ARM_TRAIL_R -> switch on a trailing stop that follows LP_TRAIL_ATR x ATR
#     under the highest price seen; it only ever tightens, never loosens.
#   * The +MAX_RR (3:1) hard target still stands — a fast spike to 3R still books there.
# Armed LATE on purpose: an early breakeven (+1R) was tested and REDUCED returns because
# it killed mid-trades that would have run to 3R. Sub-1.5R trades here are untouched, so
# the runner edge is preserved; only clearly-winning trades get their profit protected.
LP_ARM_BE_R    = 1.5   # move SL -> entry once profit reaches this many R
LP_ARM_TRAIL_R = 2.0   # arm the trailing stop once profit reaches this many R
LP_TRAIL_ATR   = 1.5   # trail this many ATR under the high-water mark once armed
# EARLY profit-lock (user-mandated): once a trade is up 0.75R, the stop moves to
# entry + 0.15R — a decent winner can never round-trip into a loss; it exits with
# at least a small profit. NOTE: earlier locks were validated to REDUCE returns
# (they cut runners before the 3R target); this is the user's explicit call and is
# mirrored in the backtest (MOM_3R_USERLOCK) so the trade-off can be measured.
LP_LOCK_TRIGGER_R = 0.75  # arm the early lock at +0.75R ("if it gets up .7 or .8")
LP_LOCK_AT_R      = 0.15  # lock the stop at entry + 0.15R ("lock .1 or .2")

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
        self.closed_syms: set = set()  # UPPER syms with VERIFIED closes (guards double-sell)
        self.scan_diag = "starting…"   # last scan summary, shown in the heartbeat
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
            self.scan_diag = "halted (daily-loss limit hit)"
            return
        if len(self.positions) >= MAX_OPEN:
            self.scan_diag = f"{MAX_OPEN} positions open (max)"
            return
        scanned = 0
        best_rv = 0.0
        best_sym = ""
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
                scanned += 1
                # diagnostic: track the strongest volume surge seen (proves it's scanning
                # and shows how close anything came to the rvol>=RV_MIN trigger)
                _i = _last_closed_idx(d, now)
                if _i is not None:
                    _rv = float(d.iloc[_i].get("rvol", 0) or 0)
                    if _rv > best_rv:
                        best_rv, best_sym = _rv, sym
                sig = detect_signal(d, now)
                if not sig:
                    continue
                self.stats["signals"] += 1
                strat, trigger, sl_dist, atr = sig
                self._enter(sym, strat, trigger, sl_dist, atr)
            except Exception as e:
                log.debug(f"scan {sym}: {e}")
        self.scan_diag = (f"scanned {scanned}/{len(self.universe)} | "
                          f"strongest rvol {best_rv:.1f} ({best_sym or '-'}) [need ≥{RV_MIN}]")

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
            # Fill-detection can time out AFTER the exchange actually filled the
            # order — that would leave a REAL long untracked (no stop, caps blind).
            # Ask the broker; if it holds the position, ADOPT it instead.
            if self.live:
                try:
                    for bp in self.exec.get_open_positions_strict():
                        if bp["symbol"].upper() == sym.upper() and bp["netQty"] >= 1:
                            fill = float(bp.get("avgPrice") or 0) or ltp
                            filled = int(bp["netQty"])
                            log.error(f"{sym}: broker DOES hold {filled} (fill-detect "
                                      f"missed it) — ADOPTING position @ {fill:.2f}")
                            break
                    else:
                        return
                except Exception as e:
                    log.critical(f"{sym}: entry unconfirmed AND broker check failed "
                                 f"({e}) — CHECK THE BROKER APP; squareoff/watchdog "
                                 f"will flatten any orphan at EOD")
                    return
            else:
                return
        if res.success and int(res.quantity or 0) >= 1 and res.fill_price:
            fill = res.fill_price          # normal confirmed fill
        # else: fill/filled were set by the broker-adoption path above
        # SL and 3:1 target anchored to the ACTUAL fill so real risk:reward is exactly
        # 1:MAX_RR. (Previously computed from pre-entry ltp, so slippage skewed the ratio.)
        sl = round(fill - sl_dist, 2)
        target = round(fill + MAX_RR * sl_dist, 2)
        slip_bps = (fill - trigger) / trigger * 1e4
        self.stats["filled"] += 1
        self.stats["slips_bps"].append(slip_bps)
        self.positions[sym] = {"qty": filled, "entry": fill, "sl": sl,
                               "target": target, "strat": strat, "sec_id": sec_id,
                               "t_in": now_ist(), "trigger": trigger,
                               # profit-lock state (see LP_* constants)
                               "sl_dist": sl_dist, "atr": atr, "hi": fill,
                               "lock_armed": False, "be_armed": False,
                               "trail_armed": False, "exit_fails": 0}
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
                # Stop/target check FIRST (matches the backtest's bar order), then
                # ratchet — and ratchet errors can never skip the stop check.
                reason = None
                if ltp <= p["sl"]:
                    # label the exit by which mechanism set the stop, for the EOD log
                    reason = ("TRAIL" if p.get("trail_armed")
                              else "BE" if p.get("be_armed")
                              else "LOCK" if p.get("lock_armed") else "SL")
                elif p["target"] and ltp >= p["target"]:
                    reason = "TARGET"
                if reason:
                    self._exit(sym, ltp, reason)
                else:
                    try:
                        self._ratchet_stop(sym, p, ltp)   # move SL up as it wins
                    except Exception as e:
                        log.error(f"{sym}: ratchet error: {e} (stop check unaffected)")
            except Exception as e:
                log.error(f"{sym}: manage() error: {e} — position still OPEN, retry next cycle")

    def _ratchet_stop(self, sym, p, ltp):
        """Move the stop UP (never down) as an in-profit trade runs, to lock gains.

        No-op for positions lacking sl_dist/atr (e.g. legacy/reconciled). Two stages,
        both armed LATE so sub-1.5R trades are untouched and the runner edge is kept:
          +LP_ARM_BE_R    -> stop to entry (breakeven)
          +LP_ARM_TRAIL_R -> trailing stop LP_TRAIL_ATR x ATR under the high-water mark
        """
        sl_dist = p.get("sl_dist")
        atr = p.get("atr")
        if not sl_dist or sl_dist <= 0:
            return
        entry = p["entry"]
        p["hi"] = max(p.get("hi", entry), ltp)         # running high-water mark
        gain_r = (ltp - entry) / sl_dist               # profit in units of initial risk
        old_sl = p["sl"]
        new_sl = old_sl
        # stage 0: EARLY lock (user-mandated) — at +0.75R guarantee a small profit
        if not p.get("lock_armed") and gain_r >= LP_LOCK_TRIGGER_R:
            new_sl = max(new_sl, entry + LP_LOCK_AT_R * sl_dist)
            p["lock_armed"] = True
        # stage 1: breakeven (superseded by the lock, kept as a max() floor)
        if not p.get("be_armed") and gain_r >= LP_ARM_BE_R:
            new_sl = max(new_sl, entry)
            p["be_armed"] = True
        # stage 2: trailing stop (needs a valid ATR for the trail width)
        if atr and atr > 0:
            if not p.get("trail_armed") and gain_r >= LP_ARM_TRAIL_R:
                p["trail_armed"] = True
            if p.get("trail_armed"):
                new_sl = max(new_sl, p["hi"] - LP_TRAIL_ATR * atr)
        if new_sl > old_sl + 1e-9:                     # only ever tighten
            p["sl"] = round(new_sl, 2)
            tag = ("TRAIL" if p.get("trail_armed") else
                   "BE" if p.get("be_armed") else "LOCK")
            log.warning(f"{sym} {tag}: stop {old_sl:.2f} -> {p['sl']:.2f} "
                        f"(ltp {ltp:.2f}, +{gain_r:.1f}R, locks "
                        f"₹{(p['sl'] - entry) * p['qty']:+.0f})")

    def _exit(self, sym, ltp, reason) -> bool:
        """Close a long with a VERIFIED market sell. Returns True only when the
        fill is confirmed (or the broker proves we're already flat).

        THE incident fix: the old version popped the position from tracking
        BEFORE the sell and never checked the result — a rejected sell (e.g. past
        Upstox's ~15:12 order cutoff) was silently booked as a normal exit while
        the REAL position stayed open at the broker, which then force-squared it
        WITH A CHARGE. Now a failed sell keeps the position tracked and retries."""
        p = self.positions.get(sym)
        if p is None:
            return True
        # A prior attempt may have actually filled (fill-detect timeout). Selling
        # AGAIN would open a real short — so before any RETRY, ask the broker.
        if p.get("exit_fails") and self.live:
            try:
                if sym.upper() in self.exec.pending_order_symbols():
                    log.warning(f"{sym}: previous exit order still PENDING — "
                                f"waiting, not re-selling")
                    return False
                brk = {b["symbol"].upper() for b in
                       self.exec.get_open_positions_strict() if b["netQty"] > 0}
                if sym.upper() not in brk:
                    log.warning(f"{sym}: broker is flat — prior exit DID fill; booking")
                    self._book_exit(sym, p, ltp, reason + "*")
                    return True
            except Exception as e:
                log.critical(f"{sym}: cannot verify broker state ({e}) — NOT "
                             f"re-selling blind; retrying next cycle")
                return False
        res = self.exec.place_entry_order(sym, "SHORT", p["qty"], ltp, p["sec_id"])
        filled = int(res.quantity or 0)
        if res.success and filled >= 1 and res.fill_price:
            self._book_exit(sym, p, float(res.fill_price), reason)
            return True
        p["exit_fails"] = p.get("exit_fails", 0) + 1
        log.critical(f"EXIT FAILED {sym} ({reason}): {res.message} — position "
                     f"STILL OPEN at broker (attempt {p['exit_fails']}); will retry")
        return False

    def _book_exit(self, sym, p, px, reason):
        """Record a CONFIRMED close: remove from tracking + realize P&L."""
        self.positions.pop(sym, None)
        self.ltp_fail.pop(sym, None)
        self.closed_syms.add(sym.upper())
        pnl_pct = (px - p["entry"]) / p["entry"] * 100 - of.COST_RT_PCT * 100
        # realized ₹ P&L (net of round-trip cost) — feeds the daily-loss breaker
        pnl_r = (px - p["entry"]) * p["qty"] - (p["entry"] + px) * p["qty"] * of.COST_RT_PCT / 2
        self.realized_pnl += pnl_r
        self.stats["net_pcts"].append(pnl_pct)
        log.warning(f"EXIT {reason} {p['strat']} {sym} @ {px:.2f} "
                    f"(entry {p['entry']:.2f}) net~{pnl_pct:+.2f}% (₹{pnl_r:+.0f}) "
                    f"qty={p['qty']} | day P&L ₹{self.realized_pnl:+.0f}")

    def squareoff_all(self, why: str):
        """Flatten EVERYTHING and VERIFY flat at the broker, retrying until
        SQUAREOFF_RETRY_UNTIL. The broker's position book is the source of truth:
        we keep selling whatever it still shows (excluding symbols we verifiably
        closed and symbols with a sell already pending) until it shows nothing."""
        if self.positions:
            log.warning(f"SQUARE-OFF ALL ({why}) — {len(self.positions)} open")
        for attempt in range(1, 7):
            # pass 1: verified exits for everything we track
            for sym in list(self.positions.keys()):
                try:
                    ltp = self._fresh_ltp(sym) or self.positions[sym]["entry"]
                    self._exit(sym, ltp, why)
                except Exception as e:
                    log.error(f"{sym}: squareoff exit error: {e}")
            if not self.live:
                return                      # paper: no broker state to verify
            # pass 2: broker truth — anything still open that we did NOT verifiably
            # close and that has no sell already pending gets sold at market.
            try:
                pending = set()
                try:
                    pending = self.exec.pending_order_symbols()
                except Exception as e:
                    log.error(f"squareoff: order-book check failed: {e}")
                brk = [b for b in self.exec.get_open_positions_strict()
                       if b["netQty"] > 0
                       and b["symbol"].upper() not in self.closed_syms
                       and b["symbol"].upper() not in pending]
            except Exception as e:
                # API error must NOT read as "flat" — retry
                log.critical(f"squareoff: positions API failed ({e}) — retrying")
                if now_ist().time() >= SQUAREOFF_RETRY_UNTIL:
                    break
                _time.sleep(5)
                continue
            if not brk and not self.positions:
                if attempt > 1:
                    log.warning(f"squareoff: verified FLAT at broker (attempt {attempt})")
                return
            for b in brk:
                try:
                    log.critical(f"squareoff: broker still holds {b['symbol']} "
                                 f"x{b['netQty']} — selling at market")
                    r = self.exec.place_entry_order(b["symbol"], "SHORT",
                                                    int(b["netQty"]), 0,
                                                    b["security_id"])
                    if r.success and int(r.quantity or 0) >= 1:
                        # verified fill -> never sell this symbol again, even if
                        # the positions API lags a few seconds behind the fill
                        self.closed_syms.add(b["symbol"].upper())
                except Exception as e:
                    log.error(f"squareoff broker sell {b['symbol']}: {e}")
            if now_ist().time() >= SQUAREOFF_RETRY_UNTIL:
                break
            _time.sleep(5)
        if self.live:
            log.critical("SQUAREOFF INCOMPLETE — positions may remain at the broker! "
                         "The 15:03 watchdog is the backstop; CHECK THE BROKER APP NOW.")

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

    def _reconcile_on_start(self):
        """Clean slate on startup: flatten any pre-existing intraday positions so a
        restart can't orphan positions (unmanaged, no stop) or stack past MAX_OPEN /
        the ₹cap. Runs only in live mode; reuses the verify-flat squareoff loop."""
        if not self.live:
            return
        try:
            pos = self.exec.get_open_positions_strict()
            if pos:
                syms = [p.get("symbol") for p in pos]
                log.warning(f"STARTUP RECONCILE — found {len(pos)} pre-existing intraday "
                            f"position(s) {syms} from a prior run. Flattening for a clean "
                            f"slate so caps are respected.")
                self.squareoff_all("STARTUP_RECONCILE")
        except Exception as e:
            log.error(f"startup reconcile failed: {e} — check the broker app manually")

    def run(self):
        self._reconcile_on_start()
        try:
            while True:
                if KILL_FILE.exists():
                    log.warning("KILL file detected — squaring off and stopping.")
                    self.squareoff_all("KILL"); break
                now = now_ist()
                t = now.time()
                phase = market_phase(t)
                if phase == "closed":
                    if self.positions:
                        log.critical("market closed with tracked positions — "
                                     "attempting squareoff anyway")
                        self.squareoff_all("LATE_CLOSE")
                    log.info("Market closed. Pilot done for the day."); break
                if phase in ("pre", "opening"):
                    log.info(f"{phase} ({now:%H:%M}) — waiting for 9:30 trading window.")
                    _time.sleep(LOOP_SECONDS); continue
                if phase == "squareoff":
                    self.squareoff_all(f"{SQUAREOFF:%H:%M} squareoff")
                    log.info("Squared off. Stopping for the day."); break
                # trading phase
                self.manage(now)
                if t < ENTRY_CUTOFF:
                    self.scan(now)
                else:
                    # no entries after 14:00 — skip the 130-symbol scan so nothing
                    # can delay the 15:00 squareoff
                    self.scan_diag = "entries closed (>=14:00) — managing exits only"
                log.info(f"{now:%H:%M:%S} | open={len(self.positions)} "
                         f"deployed=₹{self.deployed():.0f}/{MAX_PILOT_CAPITAL:.0f} "
                         f"| {', '.join(self.positions) or 'flat'} | {self.scan_diag}")
                # NEVER sleep past the squareoff deadline; tighter cadence near close
                base = LOOP_SECONDS if t < NEAR_CLOSE else LOOP_SECONDS_FAST
                until_sq = (datetime.combine(now.date(), SQUAREOFF, tzinfo=IST)
                            - now).total_seconds()
                _time.sleep(max(1.0, min(base, until_sq)) if until_sq > 0 else 1.0)
        finally:
            try:
                if self.positions:
                    log.critical("shutdown with open positions — emergency squareoff")
                    self.squareoff_all("SHUTDOWN")
            except Exception as e:
                log.critical(f"emergency squareoff failed: {e} — CHECK THE BROKER APP")
            self.report()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--liquid-only", action="store_true",
                    help="trade only the ~18 most-liquid names (far fewer trades, lower slippage)")
    args = ap.parse_args()
    # SINGLE-INSTANCE LOCK: cron + Telegram /run + manual can all launch the pilot;
    # two live instances double the caps AND cross-flatten each other's positions
    # (instance B's startup reconcile sells A's holdings -> A later shorts them).
    import fcntl
    (_ROOT / "logs").mkdir(exist_ok=True)
    _lock = open(_ROOT / "logs" / ".live_pilot.lock", "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("another live_pilot is already running — exiting (single-instance lock)")
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    # DEFAULT = the FULL validated universe (matches the +3.9%/mo, ~39 trades/mo backtest).
    # --liquid-only trades the narrow subset (fewer trades, cleaner slippage) if wanted.
    universe = TOP_LIQUID if args.liquid_only else HIGH_VOL_UNIVERSE
    log.warning(f"Universe: {'TOP-LIQUID' if args.liquid_only else 'FULL'} "
                f"({len(universe)} names)")
    Pilot(universe=universe).run()


if __name__ == "__main__":
    main()
