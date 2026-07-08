"""
swing_pilot.py — once-a-day SWING trader. Short holds only, FLAT BY FRIDAY,
never over a weekend. Delivery (CNC) positions, config-driven, paper-first.

HOW IT DIFFERS FROM live_pilot.py
---------------------------------
The intraday bot loops every 45s and is flat by 15:00. A swing bot instead runs
ONCE per day near the close (~15:10 IST via cron) and HOLDS positions across
days — so it must (a) remember positions between runs (state file on disk) and
(b) use DELIVERY orders, not MIS (which auto-squares intraday).

Each daily run:
  1. load held positions from swing_state.json
  2. MANAGE: for each holding, exit today if target/stop hit, max-hold reached,
     or it's FRIDAY (hard no-weekend rule) — verified delivery sell
  3. SCAN: today's daily-bar signal per symbol; enter (delivery buy) if a slot
     is free and it's NOT Friday (a Friday entry would carry the weekend)
  4. save state

SAFETY / GATE
-------------
* PAPER BY DEFAULT. Real orders need BOTH env INDIA_LIVE_TRADING_ENABLED=true
  AND INDIA_SWING_VALIDATED=true. The second gate exists because NO swing
  strategy has passed new_edge_lab yet — this bot is READY, not cleared. Do not
  set INDIA_SWING_VALIDATED=true until a config passes the honest lab.
* Capital = available balance / MAX_POS, NO leverage (delivery = full payment).
* Kill switch: a file named KILL in the repo root -> exit all + stop.
* Every fill is verified (placement != fill); a failed sell keeps the position.

CONFIG
------
Pick the strategy via env INDIA_SWING_STRAT (default 'mean_rev'). Params live in
STRATS below and MUST come from a new_edge_lab passer before going live.

  python3 india/swing_pilot.py                 # PAPER, mean_rev
  INDIA_SWING_STRAT=drift python3 india/swing_pilot.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time as _time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

from fetch_midcaps import HIGH_VOL_UNIVERSE

IST = ZoneInfo("Asia/Kolkata")
KILL_FILE = _ROOT / "KILL"
STATE_FILE = _ROOT / "swing_state.json"
UNIVERSE = HIGH_VOL_UNIVERSE[:50]        # the liquid tier

# ── strategy configs (params to be set from a new_edge_lab PASSER) ────────────
STRATS = {
    # VALIDATED (new_edge_lab 2026-07-08, 127 names, 1yr, delivery costs):
    # buy a -5% down-day's close, exit +2% bounce / -5% stop / Friday deadline.
    # +1.49%/mo, 60% WR, 4.9% DD, PF 1.53, PASSED strict walk-forward
    # (+0.3/+3.9/+3.1% across the three thirds). The -4% variant only broke even
    # (+0.05%/mo) — the -5% selectivity is what makes it real.
    "mean_rev": {"kind": "revert", "day_ret_max": -0.05, "target": 0.02,
                 "stop": -0.05, "max_hold": 4},
    # NOT validated (drift went -2.8%/mo) — kept for research only, do not deploy.
    "drift":    {"kind": "drift", "day_ret_min": 0.05, "vol_mult": 2.0,
                 "target": 0.06, "stop": -0.04, "max_hold": 3},
}

MAX_POS   = 3            # concurrent swing positions
FALLBACK_CAPITAL = 5000.0

(_ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[IST %(asctime)s] [%(levelname)s] [SWING] %(message)s",
    handlers=[logging.FileHandler(_ROOT / "logs" /
                                  f"swing_{datetime.now(IST):%Y-%m-%d}.log"),
              logging.StreamHandler()],
)
logging.Formatter.converter = lambda *a: datetime.now(IST).timetuple()
log = logging.getLogger("swing")


def now_ist():
    return datetime.now(IST)


def _daily_bars(dfu, sym):
    """Last ~40 sessions of daily OHLCV, aggregated from the 5-min feed."""
    raw = dfu.get_ohlcv(sym, "5m", "40d")
    if raw is None or len(raw) < 60:
        return None
    g = raw.groupby(raw.index.normalize())
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                     "low": g["low"].min(), "close": g["close"].last(),
                     "volume": g["volume"].sum()}).dropna()
    return d if len(d) >= 25 else None


class SwingPilot:
    def __init__(self):
        from auth_upstox import get_upstox_client, verify_connection
        import data_fetch_upstox as dfu
        from execution_upstox import get_executor

        self.strat_name = os.getenv("INDIA_SWING_STRAT", "mean_rev").strip()
        if self.strat_name not in STRATS:
            log.error(f"unknown INDIA_SWING_STRAT={self.strat_name}"); sys.exit(1)
        self.cfg = STRATS[self.strat_name]
        live_flag = os.getenv("INDIA_LIVE_TRADING_ENABLED", "").lower() == "true"
        validated = os.getenv("INDIA_SWING_VALIDATED", "").lower() == "true"
        self.live = live_flag and validated
        if live_flag and not validated:
            log.warning("LIVE requested but INDIA_SWING_VALIDATED != true — staying "
                        "PAPER. No swing strategy has passed new_edge_lab yet.")

        self.client = get_upstox_client()
        if not self.client or not verify_connection(self.client):
            log.error("Upstox connection failed — refresh token."); sys.exit(1)
        dfu.set_upstox_client(self.client)
        self.dfu = dfu
        self.exec = get_executor(self.client, live_enabled=self.live)
        self.positions = self._load_state()
        self.capital = self._capital()
        mode = "LIVE (REAL ₹)" if self.live else "PAPER (no orders)"
        log.warning(f"=== SWING PILOT — {mode} | strat={self.strat_name} | "
                    f"cap ₹{self.capital:,.0f} | {len(self.positions)} held ===")

    # ── state persistence (once-a-day bot must remember across runs) ──────────
    def _load_state(self) -> dict:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text())
        except Exception as e:
            log.error(f"state load failed: {e} — starting empty")
        return {}

    def _save_state(self):
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.positions, indent=2, default=str))
            tmp.replace(STATE_FILE)
        except Exception as e:
            log.error(f"state save failed: {e}")

    def _capital(self) -> float:
        env_cap = float(os.getenv("INDIA_MAX_CAPITAL", 0) or 0)
        if not self.live:
            return env_cap or FALLBACK_CAPITAL
        try:
            resp = self.client.user.get_user_fund_margin(api_version="2.0")
            data = (getattr(resp, "data", None)
                    or (resp.get("data") if isinstance(resp, dict) else None))
            eq = (data.get("equity") if isinstance(data, dict)
                  else getattr(data, "equity", None)) if data else None
            avail = float((eq.get("available_margin") if isinstance(eq, dict)
                           else getattr(eq, "available_margin", 0)) or 0)
            cap = min(avail, env_cap) if env_cap else avail
            return cap if cap > 0 else (env_cap or FALLBACK_CAPITAL)
        except Exception as e:
            log.warning(f"funds API unavailable ({e}) — fallback capital")
            return env_cap or FALLBACK_CAPITAL

    def deployed(self) -> float:
        return sum(p["qty"] * p["entry"] for p in self.positions.values())

    # ── manage held positions (exit today?) ───────────────────────────────────
    def manage(self, is_friday: bool):
        for sym in list(self.positions.keys()):
            p = self.positions[sym]
            d = _daily_bars(self.dfu, sym)
            if d is None:
                log.warning(f"{sym}: no data to evaluate — HOLDING")
                continue
            close = float(d["close"].iloc[-1])
            ret = close / p["entry"] - 1.0
            held = int((now_ist().date()
                        - datetime.fromisoformat(p["entry_date"]).date()).days)
            reason = None
            if is_friday:
                reason = "FRIDAY_FLAT"        # hard no-weekend rule
            elif ret >= self.cfg["target"]:
                reason = "TARGET"
            elif ret <= self.cfg["stop"]:
                reason = "STOP"
            elif held >= self.cfg["max_hold"]:
                reason = "MAX_HOLD"
            if reason:
                self._exit(sym, close, reason)

    def _exit(self, sym, px, reason):
        p = self.positions[sym]
        res = self.exec.place_delivery_order(sym, "SHORT", p["qty"], px, p["sec_id"])
        if not (res.success and int(res.quantity or 0) >= 1 and res.fill_price):
            log.critical(f"SWING EXIT FAILED {sym} ({reason}): {res.message} — "
                         f"STILL HELD; will retry next run")
            return
        fill = float(res.fill_price)
        pnl = (fill - p["entry"]) * p["qty"]
        log.warning(f"SWING EXIT {reason} {sym} @ {fill:.2f} (entry {p['entry']:.2f}) "
                    f"{(fill/p['entry']-1)*100:+.2f}% (₹{pnl:+.0f}) held")
        self.positions.pop(sym, None)

    # ── scan for new entries ──────────────────────────────────────────────────
    def _signal(self, d) -> bool:
        c = d["close"]; o = d["open"]; v = d["volume"]
        day_ret = float(c.iloc[-1] / c.iloc[-2] - 1.0)
        if self.cfg["kind"] == "revert":
            return day_ret <= self.cfg["day_ret_max"]
        if self.cfg["kind"] == "drift":
            vavg = float(v.iloc[-21:-1].mean())
            return (day_ret >= self.cfg["day_ret_min"] and vavg > 0
                    and float(v.iloc[-1]) >= self.cfg["vol_mult"] * vavg)
        return False

    def scan(self, is_friday: bool):
        if is_friday:
            log.info("Friday — no new entries (would carry the weekend).")
            return
        for sym in UNIVERSE:
            if len(self.positions) >= MAX_POS:
                break
            if sym in self.positions or KILL_FILE.exists():
                continue
            try:
                d = _daily_bars(self.dfu, sym)
                if d is None or not self._signal(d):
                    continue
                self._enter(sym, float(d["close"].iloc[-1]))
            except Exception as e:
                log.debug(f"scan {sym}: {e}")

    def _enter(self, sym, close):
        sec_id = self.dfu.get_security_id(sym)
        if not sec_id:
            return
        budget = min(self.capital / MAX_POS, self.capital - self.deployed())
        qty = int(budget // close)
        if qty < 1:
            return
        res = self.exec.place_delivery_order(sym, "LONG", qty, close, sec_id)
        filled = int(res.quantity or 0)
        if not (res.success and filled >= 1 and res.fill_price):
            log.warning(f"{sym}: swing entry not filled ({res.message})")
            return
        fill = float(res.fill_price)
        self.positions[sym] = {
            "qty": filled, "entry": fill, "sec_id": sec_id,
            "entry_date": now_ist().date().isoformat(),
            "target": round(fill * (1 + self.cfg["target"]), 2),
            "stop": round(fill * (1 + self.cfg["stop"]), 2),
        }
        log.warning(f"SWING ENTER {self.strat_name} {sym} qty={filled} @ {fill:.2f} "
                    f"tgt +{self.cfg['target']*100:.0f}% stop {self.cfg['stop']*100:.0f}% "
                    f"maxhold {self.cfg['max_hold']}d notional ₹{filled*fill:.0f}")

    def run(self):
        if KILL_FILE.exists():
            log.warning("KILL present — exiting all held positions.")
            for sym in list(self.positions.keys()):
                d = _daily_bars(self.dfu, sym)
                self._exit(sym, float(d["close"].iloc[-1]) if d is not None
                           else self.positions[sym]["entry"], "KILL")
            self._save_state()
            return
        is_friday = now_ist().weekday() == 4
        self.manage(is_friday)          # exits first (free up slots + cash)
        self.capital = self._capital()  # refresh after exits
        self.scan(is_friday)
        self._save_state()
        log.warning(f"=== run done | {len(self.positions)} held "
                    f"| deployed ₹{self.deployed():,.0f} ===")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    import fcntl
    (_ROOT / "logs").mkdir(exist_ok=True)
    lk = open(_ROOT / "logs" / ".swing_pilot.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("another swing_pilot is running — exiting"); return
    SwingPilot().run()


if __name__ == "__main__":
    main()
