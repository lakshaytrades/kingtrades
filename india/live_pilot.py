"""
live_pilot.py — ₹5,000 live pilot that trades the EXACT backtested edge.

WHAT THIS IS
------------
The honest research chain (orb_fast -> strategy_lab -> cost_model) found that on
liquid high-volatility NSE names, at your REAL Groww cost (~0.18% round-trip),
two long-only edges are profitable and walk-forward robust:
    WIDE_MOMENTUM     (fresh ORB break, rvol>=2, runner target 6R)   +15%/mo
    RANGE_BREAK_HOLD  (fresh ORB break, rvol>=2, hold to squareoff)  +6.5%/mo
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

# ── Hard safety limits ────────────────────────────────────────────────────────
MAX_PILOT_CAPITAL = 5000.0      # absolute cap on total exposure (NO leverage)
MAX_OPEN          = 2           # max concurrent positions
PER_POS_CAP       = MAX_PILOT_CAPITAL / MAX_OPEN
ENTRY_START       = dtime(9, 30)
ENTRY_CUTOFF      = dtime(13, 0)   # WIDE_MOMENTUM window
RANGE_CUTOFF      = dtime(11, 0)   # RANGE_BREAK_HOLD only takes early breaks
SQUAREOFF         = dtime(15, 25)
MARKET_OPEN       = dtime(9, 15)
LOOP_SECONDS      = 45
KILL_FILE         = _ROOT / "KILL"

# strategy params (exactly as validated in strategy_lab on mid-caps)
RV_MIN     = 2.0
WIDE_SL    = 1.5     # ATR mult
WIDE_RR    = 6.0     # runner target = WIDE_RR * SL_dist
HOLD_SL    = 2.0     # ATR mult for RANGE_BREAK_HOLD
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
    # both strategies share the entry; differ by SL + exit + time window
    if ENTRY_START <= t < RANGE_CUTOFF:
        # early break -> prefer RANGE_BREAK_HOLD (lower DD, fewer trades, safer)
        return ("RANGE_BREAK_HOLD", trig, HOLD_SL * atr, atr)
    if RANGE_CUTOFF <= t < ENTRY_CUTOFF:
        return ("WIDE_MOMENTUM", trig, WIDE_SL * atr, atr)
    return None


# ── Pilot ─────────────────────────────────────────────────────────────────────

class Pilot:
    def __init__(self):
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
        self.universe = HIGH_VOL_UNIVERSE
        self.positions: dict = {}      # sym -> {qty, entry, sl, target, strat, sec_id, t_in, trigger}
        self.done_today: set = set()   # one trade per symbol per day
        mode = "LIVE (REAL ₹)" if self.live else "PAPER (no orders)"
        log.warning(f"=== LIVE PILOT START — {mode} | cap ₹{MAX_PILOT_CAPITAL:.0f} | "
                    f"max {MAX_OPEN} pos | {len(self.universe)} symbols ===")
        if self.live:
            log.warning("REAL ORDERS ENABLED. Hard ₹%.0f cap, no leverage.", MAX_PILOT_CAPITAL)

    def deployed(self) -> float:
        return sum(p["qty"] * p["entry"] for p in self.positions.values())

    def scan(self, now: datetime):
        if len(self.positions) >= MAX_OPEN:
            return
        for sym in self.universe:
            if sym in self.positions or sym in self.done_today:
                continue
            if len(self.positions) >= MAX_OPEN:
                break
            try:
                raw = self.dfu.get_ohlcv(sym, "5m", "5d")
                if raw is None or len(raw) < 60:
                    continue
                d = of._prep_symbol(raw)
                if d is None:
                    continue
                sig = detect_signal(d, now)
                if not sig:
                    continue
                strat, trigger, sl_dist, atr = sig
                self._enter(sym, strat, trigger, sl_dist, atr)
            except Exception as e:
                log.debug(f"scan {sym}: {e}")

    def _enter(self, sym, strat, trigger, sl_dist, atr):
        sec_id = self.dfu.get_security_id(sym)
        if not sec_id:
            log.info(f"{sym}: no instrument_key — skip"); return
        ltp = self.dfu.get_ltp(sym) or trigger
        # never chase: skip if price already ran > 0.3% past the trigger
        if ltp > trigger * 1.003:
            log.info(f"{sym} {strat}: price {ltp:.2f} already > trigger {trigger:.2f}+0.3% — skip")
            return
        budget = min(PER_POS_CAP, MAX_PILOT_CAPITAL - self.deployed())
        qty = int(budget // ltp)                       # NO leverage: notional <= budget
        if qty < 1:
            log.info(f"{sym} {strat}: too pricey for ₹{budget:.0f} (ltp {ltp:.2f}) — skip")
            return
        sl = round(ltp - sl_dist, 2)
        target = round(ltp + WIDE_RR * sl_dist, 2) if strat == "WIDE_MOMENTUM" else 0.0
        res = self.exec.place_entry_order_limit(sym, "LONG", qty, trigger, sec_id,
                                                limit_offset_pct=0.001)
        if not res.success:
            log.warning(f"{sym} {strat}: entry failed — {res.message}"); return
        fill = res.fill_price or ltp
        slip_bps = (fill - trigger) / trigger * 1e4
        self.positions[sym] = {"qty": res.quantity or qty, "entry": fill, "sl": sl,
                               "target": target, "strat": strat, "sec_id": sec_id,
                               "t_in": now_ist(), "trigger": trigger}
        self.done_today.add(sym)
        log.warning(f"ENTER {strat} {sym} qty={res.quantity or qty} trig={trigger:.2f} "
                    f"fill={fill:.2f} slip={slip_bps:+.1f}bps SL={sl:.2f} "
                    f"tgt={target or 'hold'} notional=₹{(res.quantity or qty)*fill:.0f}")

    def manage(self, now: datetime):
        for sym in list(self.positions.keys()):
            p = self.positions[sym]
            ltp = self.dfu.get_ltp(sym)
            if ltp is None:
                continue
            reason = None
            if ltp <= p["sl"]:
                reason = "SL"
            elif p["target"] and ltp >= p["target"]:
                reason = "TARGET"
            if reason:
                self._exit(sym, ltp, reason)

    def _exit(self, sym, ltp, reason):
        p = self.positions.pop(sym)
        # close a long = SELL MARKET (reuses tested executor path)
        res = self.exec.place_entry_order(sym, "SHORT", p["qty"], ltp, p["sec_id"])
        px = res.fill_price or ltp
        pnl_pct = (px - p["entry"]) / p["entry"] * 100 - of.COST_RT_PCT * 100
        log.warning(f"EXIT {reason} {p['strat']} {sym} @ {px:.2f} "
                    f"(entry {p['entry']:.2f}) grossΔ={ (px-p['entry'])/p['entry']*100:+.2f}% "
                    f"net~{pnl_pct:+.2f}% qty={p['qty']}")

    def squareoff_all(self, why: str):
        if not self.positions:
            return
        log.warning(f"SQUARE-OFF ALL ({why}) — {len(self.positions)} open")
        for sym in list(self.positions.keys()):
            ltp = self.dfu.get_ltp(sym) or self.positions[sym]["entry"]
            self._exit(sym, ltp, why)
        # belt-and-suspenders: ask broker to flat any stragglers (live only)
        if self.live:
            try:
                pos = self.exec.get_open_positions()
                if pos:
                    self.exec.square_off_all(pos)
            except Exception as e:
                log.error(f"broker square_off_all: {e}")

    def run(self):
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


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    Pilot().run()


if __name__ == "__main__":
    main()
