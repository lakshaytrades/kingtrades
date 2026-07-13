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

import numpy as np
import pandas as pd

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

from fetch_midcaps import HIGH_VOL_UNIVERSE

IST = ZoneInfo("Asia/Kolkata")
KILL_FILE = _ROOT / "KILL"
STATE_FILE = _ROOT / "swing_state.json"
# The passing validation (new_edge_lab 2026-07-08) ran on the 127 symbols of
# HIGH_VOL_UNIVERSE that resolve in the Upstox instrument map (the list has 130
# entries; 3 never fetch and are skipped identically live and in the backtest).
UNIVERSE = HIGH_VOL_UNIVERSE

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

# AUDIT FIX: the validated sim (orb_fast._simulate live_sizing) used 2 slots at
# 50% of capital each — 3 slots was an unvalidated deviation.
MAX_POS   = 2            # concurrent swing positions (MUST match the validation)
# LARGE-CAPITAL SAFEGUARD: a single position may never exceed this fraction of
# the stock's ~20-day median daily traded value. At small capital this never
# binds; at ₹5L+ per slot it caps (or skips) a name too thin to absorb the
# order without a bad fill / price impact. 1% of ADV is very conservative for
# a once-daily delivery buy.
MAX_POS_PCT_OF_ADV = 0.01
MIN_ADV_VALUE      = 5_00_00_000   # ₹5 cr/day floor — skip names thinner than this
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


def _tg_send(text: str):
    """Best-effort Telegram notification (trade events + holdings digest).
    Never raises — a notification failure must never block trading."""
    try:
        import requests
        tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not tok or not chat:
            return
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text[:3900]}, timeout=10)
    except Exception as e:
        log.debug(f"telegram notify failed: {e}")


def _daily_bars(dfu, sym):
    """Daily OHLCV history + TODAY's live bar (built from intraday 1-min).

    AUDIT FIX: the old path used get_ohlcv('5m','40d') — whose period argument
    is IGNORED (hardcoded ~7 days) — so the >=25-session requirement failed for
    EVERY symbol: no entries ever, and (fatal) no exits for held positions.
    Now: full daily candles via the '1d' interval, with today's partial bar
    aggregated from the intraday feed. If there is NO intraday data for today
    (exchange holiday), the last bar is a PRIOR session — callers use that to
    detect 'market closed today' and refuse to trade stale data."""
    hist = dfu.get_ohlcv(sym, "1d")
    if hist is None or len(hist) < 25:
        return None
    d = hist.copy()
    today = now_ist().date()
    # drop any (possibly stale/partial) today row from history, then rebuild it
    d = d[d.index.date < today]
    try:
        intra = dfu.get_ohlcv(sym, "5m")           # last few days 1-min, resampled
        if intra is not None and len(intra):
            t = intra[intra.index.date == today]
            if len(t):
                row = pd.DataFrame({"open": [float(t["open"].iloc[0])],
                                    "high": [float(t["high"].max())],
                                    "low": [float(t["low"].min())],
                                    "close": [float(t["close"].iloc[-1])],
                                    "volume": [float(t["volume"].sum())]},
                                   index=pd.DatetimeIndex(
                                       [pd.Timestamp(today, tz=IST)]))
                d = pd.concat([d, row])
    except Exception:
        pass                                        # history alone still usable
    return d if len(d) >= 25 else None


def _bar_is_today(d) -> bool:
    """True when the frame's last bar is TODAY (market traded today)."""
    try:
        return d is not None and d.index[-1].date() == now_ist().date()
    except Exception:
        return False


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
        # ── OPTIONAL MTF LEVERAGE (default 1.0 = OFF, full-cash delivery) ──────
        # Multi-day swing CAN use MTF (interest only for days held) — but it
        # multiplies drawdown 1:1 and amplifies a not-yet-live-proven edge. See
        # mtf_analysis.py. Hard-clamped to 2.0x; requires an EXPLICIT ack so it
        # can never turn on by accident. Verify your account's MTF product code
        # + interest terms with a ₹1 test order before trusting it.
        lev = float(os.getenv("INDIA_MTF_LEVERAGE", 1.0) or 1.0)
        ack = os.getenv("INDIA_MTF_ACK", "").lower() == "true"
        if lev > 1.0 and not ack:
            log.critical(f"INDIA_MTF_LEVERAGE={lev} ignored — set INDIA_MTF_ACK=true "
                         f"to accept doubled drawdown + interest on an unproven edge.")
            lev = 1.0
        self.leverage = min(max(lev, 1.0), 2.0)
        self.product = "MTF" if self.leverage > 1.0 else "D"
        if self.leverage > 1.0:
            log.critical(f"MTF LEVERAGE {self.leverage:.1f}x ACTIVE — drawdown scales "
                         f"{self.leverage:.1f}x and interest accrues per day held. "
                         f"Recommended only AFTER the base strategy is live-profitable.")

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
        if self.live and self.capital > 3_00_000:
            # the strategy is validated to ~₹2L; larger is unproven-live. Warn
            # loudly and set a mental drawdown number (10% is normal).
            log.critical(f"LARGE CAPITAL ₹{self.capital:,.0f} — validated only to "
                         f"~₹2L and NOT yet confirmed live. A normal 10% drawdown "
                         f"= ₹{self.capital*0.10:,.0f}. Cap exposure with "
                         f"INDIA_MAX_CAPITAL until live months prove the edge.")
        self._reconcile_holdings()

    def _reconcile_holdings(self):
        """Warn LOUDLY when the state ledger and the broker's HOLDINGS book
        disagree (delivery positions are invisible to the intraday positions
        API — the holdings book is the only broker truth for swing)."""
        if not self.live:
            return
        try:
            held = self.exec.get_delivery_holdings()
        except Exception as e:
            log.warning(f"holdings reconcile unavailable ({e})")
            return
        uni = {s.upper() for s in UNIVERSE}
        broker_syms = {s for s in held if s in uni}
        state_syms = {s.upper() for s in self.positions}
        for s in broker_syms - state_syms:
            log.critical(f"RECONCILE: broker holds {held[s]} x {s} but state does "
                         f"NOT — an UNMANAGED holding. Sell it manually in the app "
                         f"or add it to swing_state.json.")
        for s in state_syms - broker_syms:
            log.critical(f"RECONCILE: state tracks {s} but the broker holdings "
                         f"book doesn't show it (T+1 lag is normal on day 1; if "
                         f"older, the state is stale — verify in the app).")

    # ── state persistence (once-a-day bot must remember across runs) ──────────
    def _load_state(self) -> dict:
        """Positions held across days live here. Falls back to the .bak copy if
        the primary is corrupt — a lost state file with REAL delivery holdings
        at the broker would orphan them (unmanaged, no exits)."""
        for f in (STATE_FILE, STATE_FILE.with_suffix(".bak")):
            try:
                if f.exists():
                    return json.loads(f.read_text())
            except Exception as e:
                log.error(f"state load failed from {f.name}: {e}")
        if STATE_FILE.exists() or STATE_FILE.with_suffix(".bak").exists():
            log.critical("STATE UNREADABLE — if the broker app shows swing "
                         "holdings, they are UNMANAGED until state is restored. "
                         "CHECK THE APP.")
        return {}

    def _save_state(self):
        """Write order: tmp first, THEN back up the primary, THEN swap. The old
        order moved the primary to .bak before the new write — a write failure
        in between left only the stale .bak (this run's real trades lost)."""
        try:
            import shutil
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.positions, indent=2, default=str))
            if STATE_FILE.exists():                       # keep last good copy
                shutil.copy2(STATE_FILE, STATE_FILE.with_suffix(".bak"))
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
                log.critical(f"{sym}: NO DATA to evaluate — holding blind; if this "
                             f"repeats, check the feed / sell manually in the app")
                continue
            if not _bar_is_today(d):
                # exchange holiday: market is CLOSED — an exit order would just
                # be rejected. Evaluate again next session.
                log.warning(f"{sym}: no trading today (holiday?) — cannot exit; "
                            f"will act next session")
                continue
            close = float(d["close"].iloc[-1])
            ret = close / p["entry"] - 1.0
            entry_d = datetime.fromisoformat(p["entry_date"]).date()
            today_d = now_ist().date()
            held = int(np.busday_count(entry_d, today_d))      # trading days
            carried_weekend = (today_d.isocalendar()[:2]
                               != entry_d.isocalendar()[:2])   # different ISO week
            reason = None
            if is_friday:
                reason = "FRIDAY_FLAT"        # hard no-weekend rule
            elif carried_weekend:
                # a Friday-holiday (or missed run) let this cross a weekend —
                # clean it up on the FIRST session of the new week
                reason = "WEEKEND_CARRIED"
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
        # idempotency: if a PREVIOUS exit attempt's order actually filled after
        # we gave up on it, selling again would over-sell. Check it first.
        old_oid = p.get("last_exit_oid")
        if old_oid and self.live:
            try:
                st, fp, fq = self.exec._order_status(old_oid)
                if st in ("complete", "filled") and fq >= int(p["qty"]):
                    log.warning(f"{sym}: earlier exit DID fill @ {fp:.2f} — booking")
                    self.positions.pop(sym, None)
                    return
                if st in ("complete", "filled") and 0 < fq < int(p["qty"]):
                    p["qty"] = int(p["qty"]) - fq       # earlier partial
                    p["last_exit_oid"] = None
            except Exception as e:
                log.critical(f"{sym}: cannot verify earlier exit ({e}) — NOT "
                             f"re-selling blind; retrying next run")
                return
        res = self.exec.place_delivery_order(sym, "SHORT", p["qty"], px, p["sec_id"], product=p.get("product", self.product))
        filled = int(res.quantity or 0)
        if not (res.success and filled >= 1 and res.fill_price):
            p["last_exit_oid"] = res.order_id or p.get("last_exit_oid")
            log.critical(f"SWING EXIT FAILED {sym} ({reason}): {res.message} — "
                         f"STILL HELD; will retry next run. If this repeats, "
                         f"check DDPI authorization / sell manually in the app")
            _tg_send(f"⚠️ SELL FAILED {sym} ({reason}): {res.message}\n"
                     f"Still holding {p['qty']} — will retry tomorrow. If this "
                     f"repeats, check DDPI / sell manually in the Upstox app.")
            return
        fill = float(res.fill_price)
        if filled < int(p["qty"]):
            # PARTIAL fill (e.g. circuit-bound crash): keep the remainder
            # tracked — popping it would orphan REAL shares at the broker.
            pnl = (fill - p["entry"]) * filled
            p["qty"] = int(p["qty"]) - filled
            p["last_exit_oid"] = None
            log.critical(f"SWING PARTIAL EXIT {reason} {sym}: {filled} @ {fill:.2f} "
                         f"(₹{pnl:+.0f}), {p['qty']} REMAIN HELD — retrying next run")
            _tg_send(f"🟠 PARTIAL SELL {sym} ({reason}): {filled} @ ₹{fill:.2f} "
                     f"(₹{pnl:+.0f}). {p['qty']} still held — retrying tomorrow.")
            return
        pnl = (fill - p["entry"]) * filled
        pct = (fill / p["entry"] - 1) * 100
        log.warning(f"SWING EXIT {reason} {sym} @ {fill:.2f} (entry {p['entry']:.2f}) "
                    f"{pct:+.2f}% (₹{pnl:+.0f})")
        _tg_send(f"{'🟢' if pnl >= 0 else '🔴'} SOLD {sym} ({reason})\n"
                 f"{filled} @ ₹{fill:.2f} (bought ₹{p['entry']:.2f})\n"
                 f"P&L: ₹{pnl:+,.0f} ({pct:+.2f}%)")
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
            # deadline: never let a slow scan push orders past the NSE close
            if now_ist().time() >= dtime(15, 22):
                log.warning("scan deadline 15:22 IST reached — stopping entries")
                break
            try:
                d = _daily_bars(self.dfu, sym)
                # entries require TODAY's live bar — never trade a stale
                # (holiday / feed-gap) close
                if d is None or not _bar_is_today(d) or not self._signal(d):
                    continue
                self._enter(sym, float(d["close"].iloc[-1]), d)
            except Exception as e:
                log.debug(f"scan {sym}: {e}")

    def _adv_value(self, d) -> float:
        """~20-day median daily traded value (₹) — the liquidity of this name."""
        try:
            tv = (d["close"] * d["volume"]).iloc[-20:]
            return float(tv.median()) if len(tv) else 0.0
        except Exception:
            return 0.0

    def _enter(self, sym, close, d=None):
        sec_id = self.dfu.get_security_id(sym)
        if not sec_id:
            return
        # AUDIT FIX: capital (available_margin) is cash NET of paid-for
        # holdings; subtracting deployed() again double-counted them and
        # starved the 2nd slot forever. Per-slot budget comes from TOTAL
        # equity (cash + holdings cost); actual spend is capped by cash.
        # buying power = equity x leverage (leverage 1.0 = plain full-cash).
        # per-slot budget is a slice of buying power; actual cash cap only binds
        # for full-cash delivery (MTF finances the rest).
        equity_total = self.capital + self.deployed()
        buying_power = equity_total * self.leverage
        cash_cap = self.capital if self.leverage <= 1.0 else buying_power
        budget = min(buying_power / MAX_POS, cash_cap)
        # LARGE-CAPITAL liquidity guard: never let one order exceed 1% of the
        # name's daily traded value, and skip names thinner than the ADV floor.
        if d is not None:
            adv = self._adv_value(d)
            if adv < MIN_ADV_VALUE:
                log.info(f"{sym}: too thin (ADV ₹{adv/1e7:.1f}cr < "
                         f"₹{MIN_ADV_VALUE/1e7:.0f}cr) — skip")
                return
            liq_cap = MAX_POS_PCT_OF_ADV * adv
            if budget > liq_cap:
                log.warning(f"{sym}: sizing capped by liquidity — ₹{liq_cap:,.0f} "
                            f"(1% of ₹{adv/1e7:.1f}cr ADV) vs ₹{budget:,.0f} budget")
                budget = liq_cap
        qty = int(budget // close)
        if qty < 1:
            return
        res = self.exec.place_delivery_order(sym, "LONG", qty, close, sec_id, product=self.product)
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
            "product": self.product,   # sell under the same product it was bought
        }
        log.warning(f"SWING ENTER {self.strat_name} {sym} qty={filled} @ {fill:.2f} "
                    f"tgt +{self.cfg['target']*100:.0f}% stop {self.cfg['stop']*100:.0f}% "
                    f"maxhold {self.cfg['max_hold']}d notional ₹{filled*fill:.0f}")
        _tg_send(f"🛒 BOUGHT {sym} (panic {self.strat_name})\n"
                 f"{filled} @ ₹{fill:.2f} = ₹{filled*fill:,.0f}\n"
                 f"Target ₹{self.positions[sym]['target']:.2f} (+2%) | "
                 f"Stop ₹{self.positions[sym]['stop']:.2f} (−5%) | "
                 f"flat by Friday")

    def run(self):
        # ── IST GUARD: the bot trusts ITS OWN CLOCK (Asia/Kolkata), never the
        # server's. Cron fires at 09:40 UTC assuming a UTC server; if the VPS
        # timezone ever changes, this guard makes the mistake harmless — it
        # refuses to act outside the 14:45–15:25 IST decision window or on a
        # weekend. Override for research: INDIA_SWING_FORCE=true.
        now = now_ist()
        forced = os.getenv("INDIA_SWING_FORCE", "").lower() == "true"
        if not forced:
            if now.weekday() >= 5:
                log.warning(f"IST guard: {now:%A} is a weekend — not running.")
                return
            if not (dtime(14, 45) <= now.time() <= dtime(15, 25)):
                log.warning(f"IST guard: {now:%H:%M} IST is outside the "
                            f"14:45-15:25 decision window — not running. "
                            f"(Check the server clock / cron if unexpected.)")
                return
        if KILL_FILE.exists():
            log.warning("KILL present — exiting all held positions.")
            for sym in list(self.positions.keys()):
                d = _daily_bars(self.dfu, sym)
                if d is None or not _bar_is_today(d):
                    log.critical(f"{sym}: KILL requested but market is CLOSED "
                                 f"(holiday/off-hours) — sell will happen next "
                                 f"session; KILL stays armed")
                    continue
                self._exit(sym, float(d["close"].iloc[-1]), "KILL")
            self._save_state()
            if self.positions:
                log.critical(f"KILL incomplete — {len(self.positions)} still held "
                             f"(market closed or sells failed). File stays; will "
                             f"retry next session.")
            return
        is_friday = now_ist().weekday() == 4
        held_before = set(self.positions)
        self.manage(is_friday)          # exits first (free up slots + cash)
        self.capital = self._capital()  # refresh after exits
        self.scan(is_friday)
        self._save_state()
        log.warning(f"=== run done | {len(self.positions)} held "
                    f"| deployed ₹{self.deployed():,.0f} ===")
        # daily HOLDINGS DIGEST on Telegram — only when something is held or
        # something changed this run (no noise on quiet no-trade days)
        if self.positions or held_before != set(self.positions):
            lines = [f"📋 Swing {now_ist():%a %d %b %H:%M} — "
                     f"{len(self.positions)} holding(s)"]
            for s, p in self.positions.items():
                lines.append(f"• {s}: {p['qty']} @ ₹{p['entry']:.2f} "
                             f"(tgt ₹{p['target']:.2f} / stop ₹{p['stop']:.2f}, "
                             f"since {p['entry_date']})")
            if not self.positions:
                lines.append("• flat — no positions held")
            lines.append(f"Deployed ₹{self.deployed():,.0f} | "
                         f"cash ₹{self.capital:,.0f}")
            _tg_send("\n".join(lines))


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
