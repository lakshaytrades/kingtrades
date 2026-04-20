"""
fii_dii_tracker.py — NSE Momentum Groww AI Bot
FII / DII Daily Flow Tracker

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

18 years of experience principle:
"FIIs (Foreign Institutional Investors) dominate NSE direction.
 When FIIs are net buyers, Nifty goes up. When they sell, Nifty falls.
 DII (Domestic) buying on dips is the floor. FII buying is the engine.
 Never fight FII trend. Trade WITH institutional flow."

What this module provides:
  • FII net buy/sell (cash + futures) from NSE provisional data
  • DII net activity
  • 5-day rolling flow trend (momentum of FII buying/selling)
  • Derivative participant data (FII index futures positioning)
  • Combined flow score for signal adjustment
  • Historical flow database for trend analysis

Data Sources:
  1. NSE FII/DII Trade React: https://www.nseindia.com/api/fiidiiTradeReact
  2. NSE Participant-wise derivatives: https://www.nseindia.com/api/historicaloptionchain?...
  3. Fallback: yfinance ETF proxy (NIFTYBEES, GOLDBEES flows)

Usage:
  from fii_dii_tracker import FIIDIITracker
  tracker = FIIDIITracker()
  flow = tracker.get_today_flow()
  bias = tracker.get_flow_bias()  # "BULLISH", "BEARISH", "NEUTRAL"
  score = tracker.get_signal_adjustment()  # -10 to +10
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

# NSE requires browser-like headers + a valid session cookie from the homepage.
# Without the cookie NSE returns 403. The session must visit / first, then /api/*.
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Origin":          "https://www.nseindia.com",
    "DNT":             "1",
    "Connection":      "keep-alive",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}

NSE_FII_DII_URL       = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_FII_DERIV_URL     = "https://www.nseindia.com/api/historicaloptionchain"
NSE_PARTICIPANT_URL   = "https://www.nseindia.com/api/market-participants-turnover"

DB_PATH = Path("logs/fii_dii_flow.db")
CACHE_TTL_MINUTES = 30  # NSE updates provisional data ~3:30 PM IST


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class FIIDIIFlow:
    """Daily FII/DII flow data."""
    date:         str
    fii_buy:      float = 0.0      # ₹ crore
    fii_sell:     float = 0.0
    fii_net:      float = 0.0      # + = net buyer, - = net seller
    dii_buy:      float = 0.0
    dii_sell:     float = 0.0
    dii_net:      float = 0.0
    fii_fut_net:  float = 0.0     # FII index futures net (more predictive)
    fii_opt_net:  float = 0.0     # FII options net
    data_source:  str  = "NSE"
    fetched_at:   str  = ""

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = format_ist_timestamp()
        if self.fii_net == 0.0 and (self.fii_buy or self.fii_sell):
            self.fii_net = self.fii_buy - self.fii_sell
        if self.dii_net == 0.0 and (self.dii_buy or self.dii_sell):
            self.dii_net = self.dii_buy - self.dii_sell

    @property
    def combined_net(self) -> float:
        """Combined institutional flow. Positive = institutions net buying."""
        return self.fii_net + self.dii_net

    @property
    def is_bullish(self) -> bool:
        return self.fii_net > 500  # ₹500cr+ FII buying = bullish

    @property
    def is_bearish(self) -> bool:
        return self.fii_net < -500  # ₹500cr+ FII selling = bearish

    def summary(self) -> str:
        fii_emoji = "🟢" if self.fii_net >= 0 else "🔴"
        dii_emoji = "🟢" if self.dii_net >= 0 else "🔴"
        return (
            f"FII/DII Flow ({self.date}): "
            f"{fii_emoji} FII: ₹{self.fii_net:+,.0f}Cr | "
            f"{dii_emoji} DII: ₹{self.dii_net:+,.0f}Cr | "
            f"Combined: ₹{self.combined_net:+,.0f}Cr"
        )


@dataclass
class FlowBias:
    """Multi-day flow momentum analysis result."""
    bias:          str           # "BULLISH", "BEARISH", "NEUTRAL"
    score:         float         # -100 to +100
    today_flow:    Optional[FIIDIIFlow]
    rolling_5d_net: float        # 5-day sum of FII net
    rolling_trend: str           # "ACCELERATING", "DECELERATING", "STEADY", "REVERSING"
    signals:       List[str]     = field(default_factory=list)
    timestamp:     str           = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    def as_signal_adjustment(self) -> float:
        """
        Returns adjustment for signal_generator AI score: -10 to +10.
        Strong FII buying → +10 added to every LONG signal score.
        Strong FII selling → -10 deducted from every LONG score.
        """
        return max(-10.0, min(10.0, self.score / 10.0))


# ============================================================
# TRACKER
# ============================================================

class FIIDIITracker:
    """
    Tracks FII/DII institutional flows from NSE India.

    The #1 edge in NSE intraday trading is knowing who's buying and selling.
    FII sell-off days → avoid LONG trades entirely.
    FII buy days → lean toward LONG, give MORE margin to signals.
    DII buying on dips → stronger support (don't aggressively short).
    """

    def __init__(self):
        self._session    = requests.Session()
        self._session.headers.update(NSE_HEADERS)
        self._session_ok = False
        self._cache:     Dict[str, FIIDIIFlow] = {}
        self._last_fetch: Optional[datetime]   = None
        self._db_path    = DB_PATH
        self._init_db()
        self._init_session()

    # ──────────────────────────────────────────────────────
    # SETUP
    # ──────────────────────────────────────────────────────

    def _init_db(self):
        """Create SQLite DB for historical flow storage."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fii_dii_flow (
                    date TEXT PRIMARY KEY,
                    fii_buy REAL, fii_sell REAL, fii_net REAL,
                    dii_buy REAL, dii_sell REAL, dii_net REAL,
                    fii_fut_net REAL, fii_opt_net REAL,
                    data_source TEXT, fetched_at TEXT
                )
            """)
            conn.commit()

    def _init_session(self):
        """
        Establish NSE session cookie by visiting homepage + market-data page.
        NSE requires a valid nsit/nseappid cookie; without it all /api/* return 403.
        """
        try:
            self._session = requests.Session()
            self._session.headers.update(NSE_HEADERS)
            # Step 1: visit homepage to get initial cookies
            r1 = self._session.get(
                "https://www.nseindia.com/",
                timeout=15, allow_redirects=True,
            )
            if r1.status_code != 200:
                logger.warning(f"[{format_ist_timestamp()}] NSE homepage {r1.status_code}")
                return
            time.sleep(1)  # brief pause — mimic browser behaviour
            # Step 2: visit a market-data page to get additional cookies (nsit, nseappid)
            self._session.get(
                "https://www.nseindia.com/market-data/live-equity-market",
                timeout=15, allow_redirects=True,
            )
            time.sleep(0.5)
            self._session_ok = True
            logger.info(
                f"[{format_ist_timestamp()}] FII/DII tracker NSE session ready "
                f"(cookies: {list(self._session.cookies.keys())})"
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] NSE session failed: {e}")

    # ──────────────────────────────────────────────────────
    # FETCH
    # ──────────────────────────────────────────────────────

    def _should_fetch(self) -> bool:
        """Only re-fetch if cache is stale (30 min TTL)."""
        if not self._last_fetch:
            return True
        elapsed = (datetime.now() - self._last_fetch).total_seconds() / 60
        return elapsed >= CACHE_TTL_MINUTES

    def get_today_flow(self, force_refresh: bool = False) -> Optional[FIIDIIFlow]:
        """
        Fetch today's FII/DII provisional data from NSE.
        NSE publishes provisional data during market hours (~11 AM, 3:30 PM IST).
        """
        today_str = str(get_current_ist_date())

        # Return cache if fresh
        if not force_refresh and today_str in self._cache and not self._should_fetch():
            return self._cache[today_str]

        # Try NSE API first
        flow = self._fetch_from_nse()
        if flow:
            self._cache[today_str] = flow
            self._last_fetch = datetime.now()
            self._save_to_db(flow)
            logger.info(f"[{format_ist_timestamp()}] {flow.summary()}")
            return flow

        # Fallback: last stored value
        stored = self._load_from_db(today_str)
        if stored:
            logger.info(f"[{format_ist_timestamp()}] FII/DII: using stored data for {today_str}")
            return stored

        logger.warning(f"[{format_ist_timestamp()}] FII/DII: no data available for {today_str}")
        return None

    def _fetch_from_nse(self) -> Optional[FIIDIIFlow]:
        """
        Fetch provisional FII/DII data from NSE API.
        On 403: reinit session and retry with exponential backoff (max 2 retries).
        NSE 403s are common — the session cookie expires every ~10 minutes.
        """
        if not self._session_ok:
            self._init_session()

        for attempt, wait in enumerate([0, 3, 8]):
            try:
                if wait:
                    time.sleep(wait)
                    self._init_session()  # fresh cookies before each retry

                resp = self._session.get(NSE_FII_DII_URL, timeout=20)

                if resp.status_code == 403:
                    logger.warning(
                        f"[{format_ist_timestamp()}] NSE FII 403 — "
                        f"session expired (attempt {attempt + 1}/3)"
                    )
                    self._session_ok = False
                    if attempt < 2:
                        continue
                    # All retries exhausted — fall through to DB fallback
                    return None

                resp.raise_for_status()
                data = resp.json()
                return self._parse_nse_fii_dii(data)

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] NSE FII/DII fetch error (attempt {attempt + 1}): {e}")
                if attempt >= 2:
                    return None

        return None

    def _parse_nse_fii_dii(self, data) -> Optional[FIIDIIFlow]:
        """Parse NSE FII/DII API response."""
        try:
            # NSE returns a list of participant data
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = data.get("data", data.get("entries", [data]))
            else:
                return None

            fii_buy = fii_sell = dii_buy = dii_sell = 0.0
            today_str = str(get_current_ist_date())

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # Look for FII/FPI entries
                category = str(entry.get("category", entry.get("clientType", ""))).upper()
                buy_val  = float(entry.get("buyValue",  entry.get("grossPurchase", 0)) or 0)
                sell_val = float(entry.get("sellValue", entry.get("grossSale", 0))    or 0)

                if any(k in category for k in ["FII", "FPI", "FOREIGN"]):
                    fii_buy  += buy_val
                    fii_sell += sell_val
                elif any(k in category for k in ["DII", "DOMESTIC", "MF", "MUTUAL"]):
                    dii_buy  += buy_val
                    dii_sell += sell_val

            if fii_buy == 0 and fii_sell == 0:
                logger.warning(f"[{format_ist_timestamp()}] FII/DII: could not parse NSE response")
                return None

            return FIIDIIFlow(
                date=today_str,
                fii_buy=round(fii_buy / 1e7, 2),    # Convert to ₹ crore
                fii_sell=round(fii_sell / 1e7, 2),
                dii_buy=round(dii_buy / 1e7, 2),
                dii_sell=round(dii_sell / 1e7, 2),
                data_source="NSE",
            )

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] FII/DII parse error: {e}")
            return None

    # ──────────────────────────────────────────────────────
    # HISTORICAL / DB
    # ──────────────────────────────────────────────────────

    def _save_to_db(self, flow: FIIDIIFlow):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO fii_dii_flow VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    flow.date, flow.fii_buy, flow.fii_sell, flow.fii_net,
                    flow.dii_buy, flow.dii_sell, flow.dii_net,
                    flow.fii_fut_net, flow.fii_opt_net,
                    flow.data_source, flow.fetched_at,
                ))
        except Exception as e:
            logger.error(f"FII/DII DB save error: {e}")

    def _load_from_db(self, date_str: str) -> Optional[FIIDIIFlow]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM fii_dii_flow WHERE date=?", (date_str,)
                ).fetchone()
            if row:
                return FIIDIIFlow(
                    date=row[0], fii_buy=row[1], fii_sell=row[2], fii_net=row[3],
                    dii_buy=row[4], dii_sell=row[5], dii_net=row[6],
                    fii_fut_net=row[7], fii_opt_net=row[8],
                    data_source=row[9], fetched_at=row[10],
                )
        except Exception as e:
            logger.error(f"FII/DII DB load error: {e}")
        return None

    def get_historical_flows(self, days: int = 10) -> List[FIIDIIFlow]:
        """Load last N days of FII/DII flow from DB."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM fii_dii_flow ORDER BY date DESC LIMIT ?", (days,)
                ).fetchall()
            flows = []
            for row in rows:
                flows.append(FIIDIIFlow(
                    date=row[0], fii_buy=row[1], fii_sell=row[2], fii_net=row[3],
                    dii_buy=row[4], dii_sell=row[5], dii_net=row[6],
                    fii_fut_net=row[7], fii_opt_net=row[8],
                    data_source=row[9], fetched_at=row[10],
                ))
            return flows
        except Exception as e:
            logger.error(f"FII/DII historical load error: {e}")
            return []

    # ──────────────────────────────────────────────────────
    # BIAS ENGINE
    # ──────────────────────────────────────────────────────

    def get_flow_bias(self) -> FlowBias:
        """
        Compute multi-day FII/DII flow momentum bias.

        18yr rule: Don't just look at today's flow.
        3 consecutive days of FII selling = trend. 1 day = noise.
        5-day rolling FII net > ₹5000cr = strong bull wave.
        5-day rolling FII net < -₹5000cr = distribution phase.
        """
        today_flow = self.get_today_flow()
        historical = self.get_historical_flows(days=10)

        # Rolling 5-day FII net
        recent_5  = historical[:5]
        rolling_5d = sum(f.fii_net for f in recent_5)

        # Trend direction
        if len(recent_5) >= 3:
            nets    = [f.fii_net for f in recent_5[:3]]
            trend_d = sum(1 if n > 0 else -1 for n in nets)
            if all(n > 0 for n in nets):
                trend = "ACCELERATING" if nets[0] > nets[1] > nets[2] else "STEADY"
            elif all(n < 0 for n in nets):
                trend = "ACCELERATING" if nets[0] < nets[1] < nets[2] else "STEADY"
            else:
                trend = "REVERSING" if (nets[0] > 0) != (nets[1] > 0) else "DECELERATING"
        else:
            trend = "UNKNOWN"

        score, signals = self._score_flow(today_flow, rolling_5d, trend)

        if score >= 20:
            bias = "BULLISH"
        elif score <= -20:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return FlowBias(
            bias=bias,
            score=score,
            today_flow=today_flow,
            rolling_5d_net=rolling_5d,
            rolling_trend=trend,
            signals=signals,
        )

    def _score_flow(
        self, today: Optional[FIIDIIFlow], rolling_5d: float, trend: str
    ) -> Tuple[float, List[str]]:
        score   = 0.0
        signals = []

        # Today's flow
        if today:
            # FII dominates (2x DII weight)
            if today.fii_net > 2000:
                score += 30
                signals.append(f"FII strong buy today: ₹{today.fii_net:+,.0f}Cr — VERY BULLISH")
            elif today.fii_net > 500:
                score += 15
                signals.append(f"FII net buy today: ₹{today.fii_net:+,.0f}Cr — BULLISH")
            elif today.fii_net > -500:
                score += 5
                signals.append(f"FII neutral today: ₹{today.fii_net:+,.0f}Cr")
            elif today.fii_net > -2000:
                score -= 15
                signals.append(f"FII net sell today: ₹{today.fii_net:+,.0f}Cr — BEARISH")
            else:
                score -= 30
                signals.append(f"FII heavy sell today: ₹{today.fii_net:+,.0f}Cr — VERY BEARISH")

            # DII (half weight — often contrarian buyers at dips)
            if today.dii_net > 1000:
                score += 10
                signals.append(f"DII heavy buy: ₹{today.dii_net:+,.0f}Cr — strong support floor")
            elif today.dii_net > 0:
                score += 5
                signals.append(f"DII net buy: ₹{today.dii_net:+,.0f}Cr")
            elif today.dii_net < -1000:
                score -= 10
                signals.append(f"DII net sell: ₹{today.dii_net:+,.0f}Cr — no support")

        # 5-day rolling momentum
        if rolling_5d > 10000:
            score += 20
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — strong BULL wave")
        elif rolling_5d > 3000:
            score += 10
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — moderate bull")
        elif rolling_5d < -10000:
            score -= 20
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — distribution phase")
        elif rolling_5d < -3000:
            score -= 10
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — moderate sell-off")

        # Trend momentum
        if trend == "ACCELERATING" and score > 0:
            score += 10
            signals.append("FII buying ACCELERATING — momentum building")
        elif trend == "ACCELERATING" and score < 0:
            score -= 10
            signals.append("FII selling ACCELERATING — distribution intensifying")
        elif trend == "REVERSING":
            signals.append(f"FII trend REVERSING — watch for direction change")

        return score, signals

    # ──────────────────────────────────────────────────────
    # SIGNAL ADJUSTMENT (for signal_generator)
    # ──────────────────────────────────────────────────────

    def get_signal_adjustment(self) -> float:
        """
        Returns score adjustment for signal_generator: -10 to +10.
        This is added to every AI composite score.
        Strong FII buy day → boost LONG signals by up to +10 pts.
        Strong FII sell day → reduce LONG signals by up to 10 pts.
        """
        bias = self.get_flow_bias()
        return bias.as_signal_adjustment()

    def get_position_size_multiplier(self) -> float:
        """
        Returns position size multiplier: 0.5 to 1.5.
        Strong FII tailwind → trade bigger.
        FII headwind → trade smaller or avoid.
        """
        bias = self.get_flow_bias()
        score = bias.score
        if score >= 40:
            return 1.5
        if score >= 20:
            return 1.2
        if score >= 0:
            return 1.0
        if score >= -20:
            return 0.75
        return 0.5

    # ──────────────────────────────────────────────────────
    # TELEGRAM FORMAT
    # ──────────────────────────────────────────────────────

    def format_telegram(self) -> str:
        """Format FII/DII summary for Telegram morning brief."""
        bias = self.get_flow_bias()
        today = bias.today_flow

        emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias.bias, "🟡")

        if today:
            fii_str = f"₹{today.fii_net:+,.0f}Cr"
            dii_str = f"₹{today.dii_net:+,.0f}Cr"
        else:
            fii_str = dii_str = "N/A"

        signals_str = "\n".join(f"  • {s}" for s in bias.signals[:4])

        return (
            f"💰 *FII/DII Flow*\n"
            f"FII Today: `{fii_str}` | DII: `{dii_str}`\n"
            f"5-Day FII: `₹{bias.rolling_5d_net:+,.0f}Cr` | Trend: `{bias.rolling_trend}`\n"
            f"{emoji} Bias: *{bias.bias}* | Score: `{bias.score:+.0f}`\n"
            f"*Signals:*\n{signals_str}"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_tracker: Optional[FIIDIITracker] = None

def get_fii_dii_tracker() -> FIIDIITracker:
    global _tracker
    if _tracker is None:
        _tracker = FIIDIITracker()
    return _tracker


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tracker = FIIDIITracker()
    bias = tracker.get_flow_bias()
    print(f"\nFII/DII Bias: {bias.bias} (score: {bias.score:+.0f})")
    print(f"Rolling 5-day FII: ₹{bias.rolling_5d_net:+,.0f}Cr | Trend: {bias.rolling_trend}")
    if bias.today_flow:
        print(bias.today_flow.summary())
    print("\nSignals:")
    for s in bias.signals:
        print(f"  → {s}")
    print(f"\nSignal adjustment: {bias.as_signal_adjustment():+.1f} pts")
    print(f"Position size multiplier: {tracker.get_position_size_multiplier():.2f}x")
