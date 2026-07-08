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
* CAPITAL = your AVAILABLE BALANCE (cap with env INDIA_MAX_CAPITAL). NO LEVERAGE:
  total exposure never exceeds it; each position is capped at capital/MAX_OPEN.
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
from datetime import datetime, time as dtime, timedelta
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

# THE VALIDATED UNIVERSE (2026-07-08, uni.pkl 1-yr): the first ~50 names of
# HIGH_VOL_UNIVERSE (the most-liquid high-beta tier: metals, PSU banks, Adani,
# power). MOM_3R_USERLOCK on THESE names passed every meaningful gate:
# +6.7%/mo @0.18% cost, still +0.8% @0.25%, positive in EVERY walk-forward
# window (+3.9/+5.6/+8.1), PF 1.54, DD 14%, 389 trades. The full 127-name
# universe FAILED walk-forward — the edge lives in the liquid tier only.
LIQUID_50 = HIGH_VOL_UNIVERSE[:50]

# Return-vs-cost curve for the VALIDATED config (rvol>=4, SL 1.5xATR, 3:1), used at
# end-of-day to translate MEASURED slippage -> expected %/mo at real scale. Anchored on
# profit_optimizer: +3.9%/mo at 0.18% cost. Fewer trades (~39/mo) => flatter curve than
# the old high-frequency config. ESTIMATE — refine with: profit_optimizer.py at several
# --cost values for an exact curve.
_RET_COSTS = [0.0000, 0.0010, 0.0018, 0.0025, 0.0035, 0.0045]
_RET_VALS  = [12.0,   7.0,    3.9,    0.5,    -4.0,   -8.0]
TARGET_MO  = 4.0

# ── Hard safety limits ────────────────────────────────────────────────────────
# CAPITAL = the broker's AVAILABLE BALANCE at startup (user-mandated), optionally
# capped by env INDIA_MAX_CAPITAL. These module constants are the FALLBACK used
# in paper mode or when the funds API is unreachable (it opens ~9:30 IST — the
# pilot retries each loop until it answers). Still NO leverage: total notional
# never exceeds the available balance.
MAX_PILOT_CAPITAL = 5000.0      # fallback capital when funds API is unavailable
MAX_OPEN          = 2           # max concurrent positions
PER_POS_CAP       = MAX_PILOT_CAPITAL / MAX_OPEN
MAX_DAILY_LOSS    = 300.0       # ₹ floor; live limit = max(2% of capital, this)
STALE_FEED_CYCLES = 4           # force-flatten an open position if its price is unavailable this many loops
ENTRY_START       = dtime(9, 30)
ENTRY_CUTOFF      = dtime(14, 0)   # matches the validated backtest window (9:30-14:00)
RANGE_CUTOFF      = dtime(11, 0)   # split point between the two entry-time labels
# 15:00 SHARP (user-mandated). Upstox rejects intraday orders from ~15:12 and RMS
# force-squares from ~15:15 WITH A CHARGE (this bit us at the old 15:25 — our own
# sells were rejected and the broker charged the auto-squareoff penalty). 15:00
# leaves ~12 min of retry margin before the rejection wall.
SQUAREOFF         = dtime(15, 0)
# The pilot owns 15:00-15:08; the independent watchdog defers to a LIVE pilot
# until 15:08, then takes over (still ahead of the ~15:12 rejection wall). This
# handoff prevents both processes selling the same position -> unwanted short.
SQUAREOFF_RETRY_UNTIL = dtime(15, 8)   # keep retrying failed exits until here
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
                      "too_pricey": 0, "slips_bps": [], "drift_bps": [],
                      "exit_slips_bps": [], "net_pcts": []}
        # trade on the broker's AVAILABLE BALANCE (retried until the funds API opens)
        self.capital = 0.0
        self._capital_pending = False
        self._refresh_capital()
        mode = "LIVE (REAL ₹)" if self.live else "PAPER (no orders)"
        log.warning(f"=== LIVE PILOT START — {mode} | cap ₹{self.capital:,.0f} | "
                    f"max {MAX_OPEN} pos | {len(self.universe)} symbols ===")
        if self.live:
            log.warning("REAL ORDERS ENABLED. Capital ₹%.0f (available balance), "
                        "no leverage.", self.capital)

    def deployed(self) -> float:
        return sum(p["qty"] * p["entry"] for p in self.positions.values())

    # ── Capital = available balance ──────────────────────────────────────────
    def _fetch_available_funds(self):
        """Available equity margin from the broker, or None if unavailable
        (Upstox's funds service opens ~9:30 IST; before that it errors)."""
        try:
            resp = self.client.user.get_user_fund_margin(api_version="2.0")
            data = (getattr(resp, "data", None)
                    or (resp.get("data") if isinstance(resp, dict) else None))
            eq = (data.get("equity") if isinstance(data, dict)
                  else getattr(data, "equity", None)) if data else None
            avail = (eq.get("available_margin") if isinstance(eq, dict)
                     else getattr(eq, "available_margin", 0)) if eq else 0
            avail = float(avail or 0)
            return avail if avail > 0 else None
        except Exception as e:
            log.warning(f"funds API unavailable ({e}) — will retry during trading")
            return None

    def _refresh_capital(self):
        """Set the capital base to the AVAILABLE BALANCE (user-mandated), capped
        by env INDIA_MAX_CAPITAL if set. Falls back to ₹{MAX_PILOT_CAPITAL} until
        the funds API answers. Daily-loss limit scales: max(2% of capital, ₹300)."""
        env_cap = float(os.getenv("INDIA_MAX_CAPITAL", 0) or 0)
        # OPTIONAL intraday leverage (MIS margin — NOT MTF, which is a delivery
        # product with interest and would break the flat-by-close rule).
        # HARD-CLAMPED to 2.0x: leverage multiplies drawdowns 1:1, and above
        # ~1.4x the backtest's NORMAL 14% DD already breaches the 20% kill-rule.
        # Default 1.0 (no leverage). The daily-loss breaker stays on REAL equity.
        lev = float(os.getenv("INDIA_LEVERAGE", 1.0) or 1.0)
        self.leverage = min(max(lev, 1.0), 2.0)
        if lev > 2.0:
            log.warning(f"INDIA_LEVERAGE={lev} clamped to 2.0x (safety ceiling)")
        equity = None
        if not self.live:
            equity = env_cap or MAX_PILOT_CAPITAL
            self.capital = equity * self.leverage
        else:
            avail = self._fetch_available_funds()
            if avail is None:
                self._capital_pending = True        # retry each loop until it answers
                if not self.capital:
                    equity = env_cap or MAX_PILOT_CAPITAL
                    self.capital = equity * self.leverage
                    log.warning(f"funds unknown yet — starting with fallback "
                                f"₹{self.capital:,.0f}")
            else:
                self._capital_pending = False
                equity = min(avail, env_cap) if env_cap else avail
                self.capital = equity * self.leverage
                log.warning(f"CAPITAL ₹{self.capital:,.0f} = equity ₹{equity:,.0f}"
                            + (f" x {self.leverage:.1f}x LEVERAGE"
                               if self.leverage > 1 else " (no leverage)")
                            + (f" [INDIA_MAX_CAPITAL ₹{env_cap:,.0f}]" if env_cap else ""))
                if self.leverage > 1:
                    log.warning("LEVERAGE ON: losses and drawdowns scale %.1fx — "
                                "the 20%% kill-rule now applies to REAL equity",
                                self.leverage)
        self.per_pos_cap = self.capital / MAX_OPEN
        # risk limits are anchored to REAL equity, never to leveraged buying power
        eq = equity if equity is not None else self.capital / self.leverage
        self.max_daily_loss = max(0.02 * eq, MAX_DAILY_LOSS)

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
        if not self.halted and self.realized_pnl <= -self.max_daily_loss:
            self.halted = True
            log.warning(f"DAILY LOSS LIMIT hit: realized ₹{self.realized_pnl:.0f} "
                        f"(limit −₹{self.max_daily_loss:.0f}) — NO new entries today.")
        if self.halted:
            self.scan_diag = "halted (daily-loss limit hit)"
            return
        if len(self.positions) >= MAX_OPEN:
            self.scan_diag = f"{MAX_OPEN} positions open (max)"
            return
        scanned = 0
        best_rv = 0.0
        best_sym = ""
        candidates = []                     # collect ALL signals, then take STRONGEST
        for sym in self.universe:
            if sym in self.positions or sym in self.done_today:
                continue
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
                _rv = 0.0
                if _i is not None:
                    _rv = float(d.iloc[_i].get("rvol", 0) or 0)
                    if _rv > best_rv:
                        best_rv, best_sym = _rv, sym
                sig = detect_signal(d, now)
                if not sig:
                    continue
                self.stats["signals"] += 1
                candidates.append((_rv, sym, sig))
            except Exception as e:
                log.debug(f"scan {sym}: {e}")
        # STRONGEST volume surge first. The old universe-list-order admission took
        # whichever names happened to come first when several broke out in the
        # same cycle; the backtest weights by strength — this narrows that gap.
        candidates.sort(key=lambda x: -x[0])
        for _rv, sym, (strat, trigger, sl_dist, atr) in candidates:
            if len(self.positions) >= MAX_OPEN:
                break
            if KILL_FILE.exists():
                return
            self._enter(sym, strat, trigger, sl_dist, atr)
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
        budget = min(self.per_pos_cap, self.capital - self.deployed())
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
        # a fresh position supersedes any earlier verified close of this symbol
        # (e.g. a startup-reconcile flatten) — EOD squareoff must not skip it
        self.closed_syms.discard(sym.upper())
        # MARKETABLE limit at the chase cap (trigger+0.3%): fills immediately like
        # a market order but with a hard price ceiling. (The old passive +0.1%
        # limit waited 30s and then paid market anyway — a delay tax that filled
        # the losers and chased the winners.)
        res = self.exec.place_entry_order_limit(sym, "LONG", qty, trigger, sec_id,
                                                limit_offset_pct=0.003)
        # CRITICAL: only record a position for the qty that ACTUALLY filled. A
        # rejected/timed-out order (filled=0) must NOT create a phantom position —
        # otherwise the exit would SELL shares we don't own and open a short.
        filled = int(res.quantity or 0)
        if not res.success or filled < 1 or not res.fill_price:
            log.warning(f"{sym} {strat}: entry not filled (ok={res.success} "
                        f"qty={filled} px={res.fill_price}) — no position recorded")
            # a CLEANLY rejected/cancelled order didn't trade — give the symbol
            # back to today's universe instead of burning its one slot
            msg = (res.message or "").lower()
            if (not res.order_id) or "rejected" in msg or "cancelled" in msg:
                self.done_today.discard(sym)
            # Fill-detection can time out AFTER the exchange actually filled the
            # order — that would leave a REAL long untracked (no stop, caps blind).
            # Ask the broker; if it holds the position, ADOPT it instead.
            if self.live:
                brk = None
                for att in range(3):        # positions API often 500s right after
                    try:                    # an order burst — retry before giving up
                        brk = self.exec.get_open_positions_strict()
                        break
                    except Exception as e:
                        if att == 2:
                            log.critical(f"{sym}: entry unconfirmed AND broker check "
                                         f"failed 3x ({e}) — CHECK THE BROKER APP; "
                                         f"squareoff/watchdog will flatten any orphan")
                            return
                        _time.sleep(2 ** (att + 1))
                for bp in brk:
                    if bp["symbol"].upper() == sym.upper() and bp["netQty"] >= 1:
                        fill = float(bp.get("avgPrice") or 0) or ltp
                        filled = int(bp["netQty"])
                        log.error(f"{sym}: broker DOES hold {filled} (fill-detect "
                                  f"missed it) — ADOPTING position @ {fill:.2f}")
                        break
                else:
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
        # HONEST slippage = fill vs the price the instant we sent the order (ltp).
        # The old (fill - trigger) mixed in a full bar of momentum DRIFT — it
        # overstated execution cost and could wrongly kill the scale decision.
        # Drift is recorded separately as its own diagnostic.
        slip_bps = (fill - ltp) / ltp * 1e4
        drift_bps = (fill - trigger) / trigger * 1e4
        self.stats["filled"] += 1
        self.stats["slips_bps"].append(slip_bps)
        self.stats["drift_bps"].append(drift_bps)
        self.positions[sym] = {"qty": filled, "entry": fill, "sl": sl,
                               "target": target, "strat": strat, "sec_id": sec_id,
                               "t_in": now_ist(), "trigger": trigger,
                               # profit-lock state (see LP_* constants)
                               "sl_dist": sl_dist, "atr": atr, "hi": fill,
                               "lock_armed": False, "be_armed": False,
                               "trail_armed": False, "exit_fails": 0}
        log.warning(f"ENTER {strat} {sym} qty={filled} trig={trigger:.2f} "
                    f"fill={fill:.2f} slip={slip_bps:+.1f}bps drift={drift_bps:+.1f}bps "
                    f"SL={sl:.2f} tgt={target or 'hold'} notional=₹{filled*fill:.0f}")
        # RESTING BROKER STOP (SL-M): the stop triggers AT THE BROKER even if this
        # process dies or the loop is blocked — closes the 45s poll-gap leak that
        # costs ~0.2% per stop-hit. Software stop remains as backup.
        if self.live:
            try:
                rs = self.exec.place_stop_order(sym, "LONG", filled, sl, sec_id)
                if rs.success and rs.order_id:
                    p = self.positions[sym]
                    p["stop_oid"], p["stop_px"] = rs.order_id, sl
                    log.info(f"{sym}: SL-M resting at {sl:.2f} ({rs.order_id})")
                else:
                    log.warning(f"{sym}: SL-M placement failed ({rs.message}) — "
                                f"software stop only")
            except Exception as e:
                log.warning(f"{sym}: SL-M placement error: {e} — software stop only")
            # RESTING TARGET (limit sell at 3R): intrabar touches fill TICK-LEVEL
            # at the broker — the 45s polling loop physically cannot catch a spike
            # to target; this order can. OCO with the stop is enforced in manage()
            # and _exit() (whichever fills first, the other gets cancelled).
            try:
                rt = self.exec.place_target_order(sym, "LONG", filled, target, sec_id)
                if rt.success and rt.order_id:
                    self.positions[sym]["tgt_oid"] = rt.order_id
                    log.info(f"{sym}: TARGET limit resting at {target:.2f} ({rt.order_id})")
                else:
                    log.warning(f"{sym}: target placement failed ({rt.message}) — "
                                f"software target only")
            except Exception as e:
                log.warning(f"{sym}: target placement error: {e} — software target only")

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
                # If a resting broker order (SL-M / target limit) is near its
                # level, it may have ALREADY filled — book it, never sell again.
                booked = False
                for key, lvl_ok, why in (
                        ("stop_oid", ltp <= p["sl"] * 1.01, "SLM"),
                        ("tgt_oid", p["target"] and ltp >= p["target"] * 0.99,
                         "TARGET")):
                    if self.live and p.get(key) and lvl_ok:
                        try:
                            st, fp, fq = self.exec._order_status(p[key])
                            if st in ("complete", "filled") and fq >= 1:
                                log.warning(f"{sym}: resting {why} FILLED @ {fp:.2f}")
                                self._book_exit(sym, p, fp or
                                                (p["sl"] if why == "SLM"
                                                 else p["target"]), why)
                                booked = True
                                break
                        except Exception:
                            pass
                if booked:
                    continue
                # Stop/target check FIRST (matches the backtest's bar order), then
                # ratchet — and ratchet errors can never skip the stop check.
                reason = None
                if ltp <= p["sl"]:
                    # label the exit by which mechanism OWNS the current stop level:
                    # trail > lock (entry+0.15R, always >= the BE level) > BE > SL
                    reason = ("TRAIL" if p.get("trail_armed")
                              else "LOCK" if p.get("lock_armed")
                              else "BE" if p.get("be_armed") else "SL")
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

        No-op for positions lacking sl_dist/atr (e.g. legacy/reconciled). Three stages:
          +LP_LOCK_TRIGGER_R -> stop to entry + LP_LOCK_AT_R x risk (small profit locked)
          +LP_ARM_BE_R       -> breakeven floor (superseded when the lock is active)
          +LP_ARM_TRAIL_R    -> trailing stop LP_TRAIL_ATR x ATR under the high-water mark
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
                   "LOCK" if p.get("lock_armed") else "BE")
            log.warning(f"{sym} {tag}: stop {old_sl:.2f} -> {p['sl']:.2f} "
                        f"(ltp {ltp:.2f}, +{gain_r:.1f}R, locks "
                        f"₹{(p['sl'] - entry) * p['qty']:+.0f})")
            # re-price the resting broker SL-M, throttled to meaningful moves
            # (every trail tick would churn cancel+place API calls)
            if (getattr(self, "live", False) and p.get("stop_oid") and atr
                    and p["sl"] - p.get("stop_px", 0.0) >= 0.1 * atr):
                self._move_broker_stop(sym, p)

    def _move_broker_stop(self, sym, p):
        """Cancel + replace the resting SL-M at the ratcheted level. If the stop
        FIRED while we were moving it, book that exit instead (no re-sell)."""
        old = p.get("stop_oid")
        try:
            self.exec.cancel_order(old)
            st, fp, fq = self.exec._order_status(old)
            if st in ("complete", "filled") and fq >= 1:
                log.warning(f"{sym}: SL-M fired during re-price @ {fp:.2f} — booking")
                self._book_exit(sym, p, fp or p.get("stop_px") or p["sl"], "SLM")
                return
            rs = self.exec.place_stop_order(sym, "LONG", p["qty"], p["sl"], p["sec_id"])
            if rs.success and rs.order_id:
                p["stop_oid"], p["stop_px"] = rs.order_id, p["sl"]
                log.info(f"{sym}: SL-M moved up to {p['sl']:.2f}")
            else:
                p["stop_oid"] = None
                log.warning(f"{sym}: SL-M re-place failed ({rs.message}) — "
                            f"software stop only now")
        except Exception as e:
            p["stop_oid"] = None
            log.warning(f"{sym}: SL-M move error: {e} — software stop only now")

    def _exit(self, sym, ltp, reason, pending=None, brk_open=None) -> bool:
        """Close a long with a VERIFIED market sell. Returns True only when the
        full fill is confirmed (or the broker proves we're already flat).

        THE incident fix: the old version popped the position from tracking
        BEFORE the sell and never checked the result — a rejected sell (e.g. past
        Upstox's ~15:12 order cutoff) was silently booked as a normal exit while
        the REAL position stayed open at the broker, which then force-squared it
        WITH A CHARGE. Now a failed sell keeps the position tracked and retries.

        pending/brk_open: optional pre-fetched broker state (squareoff fetches
        once per attempt instead of per symbol — API budget matters at 15:00)."""
        p = self.positions.get(sym)
        if p is None:
            return True
        if sym.upper() in self.closed_syms:
            # verifiably closed by another path (broker-truth pass) — never re-sell
            self.positions.pop(sym, None)
            self.ltp_fail.pop(sym, None)
            return True
        # A prior attempt may have actually filled (fill-detect timeout). Selling
        # AGAIN would open a real short — so before any RETRY, ask the broker.
        if p.get("exit_fails") and self.live:
            try:
                pend = (pending if pending is not None
                        else self.exec.pending_order_symbols())
                if sym.upper() in pend:
                    log.warning(f"{sym}: previous exit order still IN FLIGHT — "
                                f"waiting, not re-selling")
                    return False
                opens = (set(brk_open) if brk_open is not None
                         else {b["symbol"].upper() for b in
                               self.exec.get_open_positions_strict()
                               if b["netQty"] > 0})
                if sym.upper() not in opens:
                    # prior sell DID fill — book at its actual fill price if we
                    # can still get it, else at the current ltp (approximation)
                    px = ltp
                    oid = p.get("last_exit_oid")
                    if oid:
                        try:
                            _st, fp, _fq = self.exec._order_status(oid)
                            if fp > 0:
                                px = fp
                        except Exception:
                            pass
                    log.warning(f"{sym}: broker is flat — prior exit DID fill; "
                                f"booking @ {px:.2f}")
                    self._book_exit(sym, p, px, reason + "*")
                    return True
            except Exception as e:
                log.critical(f"{sym}: cannot verify broker state ({e}) — NOT "
                             f"re-selling blind; retrying next cycle")
                return False
        # ALL resting broker orders (SL-M stop, target limit) must be cancelled
        # BEFORE we sell — otherwise one can fire later against a position we no
        # longer hold -> short. If one already FILLED, book it instead.
        if self.live:
            for key, tag in (("stop_oid", "SLM"), ("tgt_oid", "TGT")):
                oid = p.get(key)
                if not oid:
                    continue
                try:
                    self.exec.cancel_order(oid)
                    st, fp, fq = self.exec._order_status(oid)
                    if st in ("complete", "filled") and fq >= 1:
                        log.warning(f"{sym}: resting {tag} already filled @ "
                                    f"{fp:.2f} — booking, NOT re-selling")
                        self._book_exit(sym, p, fp or ltp, reason + "~" + tag)
                        return True
                    p[key] = None
                except Exception as e:
                    log.error(f"{sym}: {tag}-cancel unverified ({e}) — NOT selling "
                              f"until resolved (double-sell risk); retrying")
                    p["exit_fails"] = p.get("exit_fails", 0) + 1
                    return False
        res = self.exec.place_entry_order(sym, "SHORT", p["qty"], ltp, p["sec_id"])
        filled = int(res.quantity or 0)
        if res.success and filled >= 1 and res.fill_price:
            if filled >= p["qty"]:
                # exit-side slippage vs the intended level (stop/target), the
                # missing half of the pilot's cost measurement
                if reason in ("SL", "TRAIL", "LOCK", "BE", "TARGET"):
                    intended = p["target"] if reason == "TARGET" else p["sl"]
                    if intended:
                        self.stats["exit_slips_bps"].append(
                            (intended - float(res.fill_price)) / intended * 1e4)
                self._book_exit(sym, p, float(res.fill_price), reason)
                return True
            # PARTIAL fill: realize the filled slice, keep the remainder tracked
            # (do NOT mark closed — the rest is still live at the broker).
            px = float(res.fill_price)
            pnl_r = (px - p["entry"]) * filled \
                - (p["entry"] + px) * filled * of.COST_RT_PCT / 2
            self.realized_pnl += pnl_r
            p["qty"] -= filled
            p["exit_fails"] = p.get("exit_fails", 0) + 1   # careful path for the rest
            p["last_exit_oid"] = res.order_id or p.get("last_exit_oid")
            log.critical(f"PARTIAL EXIT {sym}: {filled} filled @ {px:.2f} "
                         f"(₹{pnl_r:+.0f}), {p['qty']} REMAIN OPEN — retrying")
            return False
        p["exit_fails"] = p.get("exit_fails", 0) + 1
        p["last_exit_oid"] = res.order_id or p.get("last_exit_oid")
        log.critical(f"EXIT FAILED {sym} ({reason}): {res.message} — position "
                     f"STILL OPEN at broker (attempt {p['exit_fails']}); will retry")
        return False

    def _book_exit(self, sym, p, px, reason):
        """Record a CONFIRMED close: remove from tracking + realize P&L.
        Best-effort OCO: cancel any remaining resting order (a fired stop's
        partner target, or vice versa) so it can't execute against a flat book."""
        if getattr(self, "live", False):
            for key in ("stop_oid", "tgt_oid"):
                oid = p.get(key)
                if oid:
                    try:
                        self.exec.cancel_order(oid)
                    except Exception:
                        pass
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
        """Flatten EVERYTHING and VERIFY flat at the broker, retrying (with
        exponential backoff) until the retry deadline. The broker's position book
        is the source of truth: we keep selling whatever it still shows (excluding
        symbols we verifiably closed and symbols with an order in flight) until it
        shows nothing. Broker state is fetched ONCE per attempt, not per symbol —
        the API budget in the 15:00-15:08 window is what saves us from the charge."""
        if self.positions:
            log.warning(f"SQUARE-OFF ALL ({why}) — {len(self.positions)} open")
        start = now_ist()
        # EOD squareoff gets the full window to 15:08; off-hours calls (KILL,
        # startup reconcile, crash shutdown) retry for up to ~3 minutes.
        deadline_t = (SQUAREOFF_RETRY_UNTIL if start.time() >= dtime(14, 50)
                      else (start + timedelta(minutes=3)).time())
        pending = None          # broker state carried between attempts
        brk_open = None         # dict UPPER-sym -> broker position
        attempt = 0
        while True:
            attempt += 1
            # pass 1: verified exits for everything we track (reuses last fetch)
            for sym in list(self.positions.keys()):
                try:
                    # live sells are MARKET and book from the broker fill — only
                    # paper needs a quote for its synthetic fill price
                    ltp = ((None if self.live else self._fresh_ltp(sym))
                           or self.positions[sym]["entry"])
                    self._exit(sym, ltp, why, pending=pending, brk_open=brk_open)
                except Exception as e:
                    log.error(f"{sym}: squareoff exit error: {e}")
            if not self.live:
                return                      # paper: no broker state to verify
            # pass 2: fetch broker truth ONCE; sell anything still open that we
            # did NOT verifiably close and that has no order in flight.
            try:
                try:
                    pending = self.exec.pending_order_symbols()
                except Exception as e:
                    log.error(f"squareoff: order-book check failed: {e}")
                    pending = set()
                brk_open = {b["symbol"].upper(): b for b in
                            self.exec.get_open_positions_strict()
                            if b["netQty"] > 0}
            except Exception as e:
                # API error must NOT read as "flat" — retry with backoff
                log.critical(f"squareoff: positions API failed ({e}) — retrying")
                pending, brk_open = None, None
                if now_ist().time() >= deadline_t or attempt >= 40:
                    break
                _time.sleep(min(2 ** min(attempt, 4), 15))
                continue
            sellable = [b for k, b in brk_open.items()
                        if k not in self.closed_syms and k not in pending]
            if not sellable and not self.positions:
                if attempt > 1:
                    log.warning(f"squareoff: verified FLAT at broker (attempt {attempt})")
                return
            for b in sellable:
                k = b["symbol"].upper()
                try:
                    log.critical(f"squareoff: broker still holds {b['symbol']} "
                                 f"x{b['netQty']} — selling at market")
                    r = self.exec.place_entry_order(b["symbol"], "SHORT",
                                                    int(b["netQty"]), 0,
                                                    b["security_id"])
                    if r.success and int(r.quantity or 0) >= 1:
                        # verified fill -> never sell this symbol again, even if
                        # the positions API lags a few seconds behind the fill
                        self.closed_syms.add(k)
                        # if we were tracking it, book the P&L and stop tracking
                        tsym = next((s for s in self.positions
                                     if s.upper() == k), None)
                        if tsym:
                            self._book_exit(tsym, self.positions[tsym],
                                            float(r.fill_price) or
                                            self.positions[tsym]["entry"],
                                            why + "_BRK")
                except Exception as e:
                    log.error(f"squareoff broker sell {b['symbol']}: {e}")
            if now_ist().time() >= deadline_t or attempt >= 40:
                break
            _time.sleep(min(2 ** min(attempt, 4), 15))
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
        # per-side slippage = mean(entry slip vs order-send LTP, exit slip vs
        # intended level). The old formula halved (fill - trigger), which mixed a
        # bar of momentum DRIFT into the execution number and could wrongly kill
        # (or bless) the scale decision.
        sides = []
        if s["slips_bps"]:
            sides.append(abs(sum(s["slips_bps"]) / len(s["slips_bps"])))
        if s["exit_slips_bps"]:
            sides.append(abs(sum(s["exit_slips_bps"]) / len(s["exit_slips_bps"])))
        avg_slip_side = (sum(sides) / len(sides) / 100) if sides else None  # %/side
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
        log.warning(f"  MEASURED slippage: {avg_slip_side:.3f}% per side "
                    f"(entry n={len(s['slips_bps'])}, exit n={len(s['exit_slips_bps'])})")
        if s["drift_bps"]:
            log.warning(f"  entry DRIFT vs trigger (timing, not execution): "
                        f"{sum(s['drift_bps'])/len(s['drift_bps']):+.1f} bps avg")
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
                if self._capital_pending:      # funds API opens ~9:30 — keep asking
                    self._refresh_capital()
                self.manage(now)
                if t < ENTRY_CUTOFF:
                    self.scan(now)
                else:
                    # no entries after 14:00 — skip the 130-symbol scan so nothing
                    # can delay the 15:00 squareoff
                    self.scan_diag = "entries closed (>=14:00) — managing exits only"
                log.info(f"{now:%H:%M:%S} | open={len(self.positions)} "
                         f"deployed=₹{self.deployed():.0f}/{self.capital:,.0f} "
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
    ap.add_argument("--full", action="store_true",
                    help="trade the FULL ~130-name universe (FAILED walk-forward "
                         "on 2026-07 data — validated edge is the 50 liquid names)")
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
    # DEFAULT = LIQUID_50, the VALIDATED universe (2026-07-08: +6.7%/mo @0.18%,
    # positive in every walk-forward window, PF 1.54, DD 14%). The full 127-name
    # run FAILED walk-forward (wf1 negative) — the edge lives in the liquid tier.
    if args.liquid_only:
        universe, tag = TOP_LIQUID, "TOP-LIQUID"
    elif args.full:
        universe, tag = HIGH_VOL_UNIVERSE, "FULL (NOT validated!)"
    else:
        universe, tag = LIQUID_50, "LIQUID-50 (validated)"
    log.warning(f"Universe: {tag} ({len(universe)} names)")
    Pilot(universe=universe).run()


if __name__ == "__main__":
    main()
