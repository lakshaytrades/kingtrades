"""
squareoff_watchdog.py — independent EOD safety net. Runs from cron at 15:03 IST.

WHY THIS EXISTS
---------------
The pilot squares off at 15:00 IST, but if the PROCESS is dead (crash, reboot,
OOM) or its exits keep failing, nothing else closes the positions — Upstox RMS
force-squares from ~15:15 and CHARGES a penalty per position (this happened for
real). This watchdog is a separate process with one job: at 15:03, if the broker
still shows ANY open intraday position, market-sell it and verify flat — all
before Upstox's ~15:12 order-acceptance cutoff.

SAFETY
------
* Runs only when INDIA_LIVE_TRADING_ENABLED=true (a paper pilot has no broker
  positions; also prevents flattening manual positions during paper testing).
* Time-guarded: acts only between 14:50 and 15:20 IST even if cron mis-fires.
* DEFERS to a live pilot process until 15:08 (the pilot owns the squareoff and
  retries until then) — prevents both processes selling the same position.
* Skips any symbol that already has an order in flight in the order book (a
  pilot exit may be mid-flight — selling again would open a short).
* Sells exactly the broker-reported net quantity. Verifies flat afterwards.

Cron (installed by setup_cron.sh):  33 9 * * 1-5  (= 15:03 IST on the UTC VPS)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time as _time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

IST = ZoneInfo("Asia/Kolkata")
WINDOW_START = dtime(14, 50)
WINDOW_END   = dtime(15, 20)
# Hand-off: the pilot owns the squareoff until 15:08 (SQUAREOFF_RETRY_UNTIL).
# While a live pilot process exists, the watchdog WAITS until then — two
# processes selling the same position would open an unwanted short.
PILOT_OWNS_UNTIL = dtime(15, 8)

(_ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[IST %(asctime)s] [%(levelname)s] [WATCHDOG] %(message)s",
    handlers=[logging.FileHandler(_ROOT / "logs" /
                                  f"watchdog_{datetime.now(IST):%Y-%m-%d}.log"),
              logging.StreamHandler()],
)
logging.Formatter.converter = lambda *a: datetime.now(IST).timetuple()
log = logging.getLogger("watchdog")


def _pilot_alive() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "live_pilot.py"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        return bool(out)
    except Exception:
        return False


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    if os.getenv("INDIA_LIVE_TRADING_ENABLED", "").strip().lower() != "true":
        log.info("live trading not enabled — watchdog exits (nothing to protect)")
        return
    now = datetime.now(IST).time()
    if not (WINDOW_START <= now <= WINDOW_END):
        log.warning(f"outside the {WINDOW_START}-{WINDOW_END} IST window ({now}) — "
                    f"refusing to act (cron mis-schedule guard)")
        return
    # defer to a LIVE pilot until 15:08 — it is mid-squareoff; acting in parallel
    # risks both processes selling the same position (short). A DEAD pilot means
    # we act immediately.
    while _pilot_alive() and datetime.now(IST).time() < PILOT_OWNS_UNTIL:
        log.info(f"pilot process is alive and owns the squareoff until "
                 f"{PILOT_OWNS_UNTIL} — waiting")
        _time.sleep(15)

    from auth_upstox import get_upstox_client, verify_connection
    from execution_upstox import get_executor
    client = get_upstox_client()
    if not client or not verify_connection(client):
        log.critical("Upstox connection failed — CANNOT verify positions. "
                     "CHECK THE BROKER APP NOW.")
        sys.exit(1)
    ex = get_executor(client, live_enabled=True)

    for attempt in range(1, 5):
        try:
            pos = [p for p in ex.get_open_positions_strict() if p["netQty"] > 0]
        except Exception as e:
            log.critical(f"positions API failed ({e}) — retry {attempt}/4")
            _time.sleep(2 ** attempt)      # 2,4,8,16 (repo backoff convention)
            continue
        if not pos:
            log.info("broker is FLAT — pilot did its job, nothing to do.")
            return
        try:
            pending = ex.pending_order_symbols()
        except Exception as e:
            log.error(f"order-book check failed ({e}) — assuming none pending")
            pending = set()
        acted = False
        for p in pos:
            sym = p["symbol"]
            if sym.upper() in pending:
                log.warning(f"{sym}: an order is already pending — waiting, not re-selling")
                continue
            log.critical(f"OPEN POSITION AT {datetime.now(IST):%H:%M}: {sym} "
                         f"x{p['netQty']} — pilot missed it, WATCHDOG selling at market")
            try:
                r = ex.place_entry_order(sym, "SHORT", int(p["netQty"]), 0,
                                         p["security_id"])
                acted = True
                if r.success and int(r.quantity or 0) >= 1:
                    log.warning(f"{sym}: watchdog sell FILLED @ {r.fill_price}")
                else:
                    log.critical(f"{sym}: watchdog sell NOT confirmed ({r.message})")
            except Exception as e:
                log.critical(f"{sym}: watchdog sell failed: {e}")
        _time.sleep(2 ** attempt if (acted or pending) else 2)
    # final verdict
    try:
        left = [p for p in ex.get_open_positions_strict() if p["netQty"] > 0]
        if left:
            log.critical(f"STILL OPEN after all attempts: "
                         f"{[(p['symbol'], p['netQty']) for p in left]} — "
                         f"Upstox RMS will square these WITH A CHARGE. "
                         f"CHECK THE BROKER APP NOW.")
            sys.exit(2)
        log.info("verified FLAT after watchdog action.")
    except Exception as e:
        log.critical(f"final verification failed ({e}) — CHECK THE BROKER APP NOW.")
        sys.exit(3)


if __name__ == "__main__":
    main()
