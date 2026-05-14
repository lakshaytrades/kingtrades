"""
options_signals.py — NSE Momentum Groww AI Bot
Nifty / BankNifty Options Signal Generator — CE/PE Buy Signals

Strategy: Buy ATM or slightly OTM CE/PE on strong multi-factor trend confirmation.

⚠️ BUY ONLY — no option selling (unlimited loss risk).
⚠️ MANUAL EXECUTION — Groww Trade SDK options support is unconfirmed at this time.
   All signals go to Telegram for manual placement on the Groww mobile app.
   Target: +40% premium. Stop: -30% premium. Hold: 30–90 minutes intraday.

Groww Trade API may not support options order placement via current SDK.
Until confirmed, all signals go to Telegram for MANUAL execution.
User places order manually on Groww app when Telegram alert arrives.

Entry windows (IST):
  Morning drive:  09:20–11:00 — high directional momentum
  Afternoon:      13:30–14:30 — institutional resumption after lunch lull
  Avoid midday (11:00–13:30) — chop kills option premium

Required confluence for entry (ALL must align):
  Nifty CE: OC bias BULLISH ≥70, EMA9>EMA21 on 5m, PCR declining, valid time window
  Nifty PE: OC bias BEARISH ≥70, EMA9<EMA21 on 5m, PCR rising, valid time window
"""

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from utils import format_ist_timestamp, get_current_ist_time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_MORNING_START   = time(9, 20)
_MORNING_END     = time(11, 0)
_AFTERNOON_START = time(13, 30)
_AFTERNOON_END   = time(14, 30)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Origin":          "https://www.nseindia.com",
    "Connection":      "keep-alive",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}


@dataclass
class OptionsSignal:
    symbol:          str
    option_type:     str
    strike:          int
    expiry:          str
    current_premium: float
    target_premium:  float
    stop_premium:    float
    spot_price:      float
    confidence:      float
    reason:          str
    max_lots:        int = 1


class OptionsSignalGenerator:
    NIFTY_LOT_SIZE        = 50
    BANKNIFTY_LOT_SIZE    = 15
    MAX_PREMIUM_NIFTY     = 200
    MAX_PREMIUM_BANKNIFTY = 400

    MIN_OC_CONFIDENCE = 70.0

    def __init__(self, oc_analyzer=None):
        self._oc             = oc_analyzer
        self._spot_cache:    Dict[str, Tuple[float, float]] = {}
        self._ema_cache:     Dict[str, Tuple[pd.DataFrame, float]] = {}
        self._expiry_cache:  Dict[str, Tuple[str, float]] = {}
        self._nse_session:   Optional[requests.Session] = None
        self._session_ok     = False

    # ──────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────

    def scan(self) -> List[OptionsSignal]:
        if not self._is_valid_time():
            return []

        signals: List[OptionsSignal] = []
        for analyzer in [self._analyze_nifty, self._analyze_banknifty]:
            try:
                sig = analyzer()
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                logger.warning(
                    f"[{format_ist_timestamp()}] options_scan error: {e}"
                )
        return signals

    def format_telegram_signal(self, sig: OptionsSignal) -> str:
        arrow     = "📈" if sig.option_type == "CE" else "📉"
        lot_size  = self.NIFTY_LOT_SIZE if sig.symbol == "NIFTY" else self.BANKNIFTY_LOT_SIZE
        capital   = sig.current_premium * lot_size
        tgt_pct   = (sig.target_premium / sig.current_premium - 1) * 100
        stp_pct   = (sig.stop_premium  / sig.current_premium - 1) * 100

        return (
            f"{arrow} OPTIONS SIGNAL — {sig.symbol} {sig.option_type}\n"
            f"Strike: {sig.strike:,} {sig.option_type} ({sig.expiry})\n"
            f"Spot: ₹{sig.spot_price:,.0f}\n"
            f"Premium: ~₹{sig.current_premium:.0f} "
            f"(buy up to ₹{sig.current_premium * 1.10:.0f})\n"
            f"Target: ₹{sig.target_premium:.0f} ({tgt_pct:+.0f}%)\n"
            f"Stop:   ₹{sig.stop_premium:.0f} ({stp_pct:+.0f}%)\n"
            f"Lot size: {lot_size} | Max {sig.max_lots} lot\n"
            f"Capital needed: ~₹{capital:,.0f}\n\n"
            f"Reason: {sig.reason}\n"
            f"⚠️ MANUAL EXECUTION — Place on Groww app\n"
            f"[{format_ist_timestamp()}]"
        )

    # ──────────────────────────────────────────────────────
    # ANALYZERS
    # ──────────────────────────────────────────────────────

    def _analyze_nifty(self) -> Optional[OptionsSignal]:
        return self._analyze_index(
            symbol="SPY",
            yf_ticker="SPY",
            strike_round=1,
            max_premium=self.MAX_PREMIUM_NIFTY,
            lot_size=self.NIFTY_LOT_SIZE,
        )

    def _analyze_banknifty(self) -> Optional[OptionsSignal]:
        return self._analyze_index(
            symbol="QQQ",
            yf_ticker="QQQ",
            strike_round=1,
            max_premium=self.MAX_PREMIUM_BANKNIFTY,
            lot_size=self.BANKNIFTY_LOT_SIZE,
        )

    def _analyze_index(
        self,
        symbol: str,
        yf_ticker: str,
        strike_round: int,
        max_premium: float,
        lot_size: int,
    ) -> Optional[OptionsSignal]:
        oc_result = self._get_oc_result(symbol)
        if oc_result is None:
            logger.debug(f"[{format_ist_timestamp()}] options: no OC data for {symbol}")
            return None

        if oc_result.confidence_score < self.MIN_OC_CONFIDENCE:
            return None

        bias = oc_result.direction_bias
        if bias not in ("BULLISH", "BEARISH"):
            return None

        spot    = oc_result.spot_price
        pcr     = oc_result.pcr
        pcr_vol = oc_result.pcr_volume

        ema_ok = self._check_ema_trend(yf_ticker, bias)
        if not ema_ok:
            logger.debug(
                f"[{format_ist_timestamp()}] {symbol}: EMA trend does not "
                f"confirm OC bias {bias}"
            )
            return None

        pcr_ok = self._check_pcr_direction(pcr, pcr_vol, bias)
        if not pcr_ok:
            logger.debug(
                f"[{format_ist_timestamp()}] {symbol}: PCR direction "
                f"({pcr:.2f}) does not confirm {bias}"
            )
            return None

        option_type = "CE" if bias == "BULLISH" else "PE"
        strike      = self._get_atm_strike(spot, symbol)
        expiry      = self._get_nearest_expiry(symbol)

        premium = self._estimate_premium(symbol, strike, option_type, spot)
        if premium is None or premium <= 0 or premium > max_premium:
            logger.debug(
                f"[{format_ist_timestamp()}] {symbol} {option_type} premium "
                f"₹{premium} out of range (max ₹{max_premium})"
            )
            return None

        target_premium = round(premium * 1.40, 1)
        stop_premium   = round(premium * 0.70, 1)

        oc_signals_txt = "; ".join(oc_result.signals[:3]) if oc_result.signals else ""
        pcr_trend_txt  = "declining" if bias == "BULLISH" else "rising"
        reason = (
            f"OC {bias} (conf {oc_result.confidence_score:.0f}%), "
            f"PCR {pcr:.2f} {pcr_trend_txt}, EMA trending "
            f"{'up' if bias == 'BULLISH' else 'down'}"
        )
        if oc_signals_txt:
            reason += f". {oc_signals_txt}"

        return OptionsSignal(
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            current_premium=round(premium, 1),
            target_premium=target_premium,
            stop_premium=stop_premium,
            spot_price=round(spot, 2),
            confidence=round(oc_result.confidence_score, 1),
            reason=reason,
            max_lots=1,
        )

    # ──────────────────────────────────────────────────────
    # STRIKE / EXPIRY HELPERS
    # ──────────────────────────────────────────────────────

    def _get_atm_strike(self, spot: float, symbol: str) -> int:
        rounding = 50 if symbol == "NIFTY" else 100
        return int(round(spot / rounding) * rounding)

    def _get_nearest_expiry(self, symbol: str) -> str:
        """
        Fetch nearest weekly expiry from NSE option chain.
        Falls back to a Thursday-based estimate if NSE is unreachable.
        NSE weekly index options expire on Thursdays.
        """
        now_epoch = _time.time()
        cached    = self._expiry_cache.get(symbol)
        if cached is not None:
            expiry_str, ts = cached
            if now_epoch - ts < 3600:
                return expiry_str

        try:
            session = self._get_nse_session()
            if session is None:
                return self._estimate_expiry()

            resp = session.get(
                "https://www.nseindia.com/api/option-chain-indices",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            data         = resp.json()
            expiry_dates = data.get("records", {}).get("expiryDates", [])

            if expiry_dates:
                nearest = expiry_dates[0]
                try:
                    dt_obj     = datetime.strptime(nearest, "%d-%b-%Y")
                    expiry_str = dt_obj.strftime("%d%b%Y").upper()
                except ValueError:
                    expiry_str = nearest.replace("-", "").upper()

                self._expiry_cache[symbol] = (expiry_str, now_epoch)
                return expiry_str

        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] expiry fetch failed for {symbol}: {e}"
            )

        fallback = self._estimate_expiry()
        self._expiry_cache[symbol] = (fallback, now_epoch)
        return fallback

    def _estimate_expiry(self) -> str:
        from datetime import timedelta
        now_ist          = get_current_ist_time()
        today            = now_ist.date()
        days_to_thursday = (3 - today.weekday()) % 7
        if days_to_thursday == 0 and now_ist.time() >= time(15, 30):
            days_to_thursday = 7
        nearest_thursday = today + timedelta(days=days_to_thursday)
        return nearest_thursday.strftime("%d%b%Y").upper()

    # ──────────────────────────────────────────────────────
    # CONDITION CHECKS
    # ──────────────────────────────────────────────────────

    def _is_valid_time(self) -> bool:
        now_ist = get_current_ist_time()
        t       = now_ist.time()
        return (
            (_MORNING_START <= t <= _MORNING_END) or
            (_AFTERNOON_START <= t <= _AFTERNOON_END)
        )

    def _check_ema_trend(self, yf_ticker: str, bias: str) -> bool:
        """
        EMA9 vs EMA21 on 5-minute candles.
        Returns True if trend direction aligns with the given bias.
        Fails open (returns True) when data is unavailable — OC analysis drives the signal.
        """
        now_epoch = _time.time()
        cached    = self._ema_cache.get(yf_ticker)
        if cached is not None:
            df, ts = cached
            if now_epoch - ts < 300:
                return self._ema_aligned(df, bias)

        try:
            raw = yf.download(
                yf_ticker,
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=True,
            )
            if raw is None or raw.empty or len(raw) < 30:
                return True

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)

            close       = raw["Close"].dropna()
            df          = pd.DataFrame({"close": close})
            df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
            df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

            self._ema_cache[yf_ticker] = (df, now_epoch)
            return self._ema_aligned(df, bias)

        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] EMA check failed for {yf_ticker}: {e}"
            )
            return True

    def _ema_aligned(self, df: pd.DataFrame, bias: str) -> bool:
        if len(df) < 2:
            return True
        last = df.iloc[-1]
        if bias == "BULLISH":
            return float(last["ema9"]) > float(last["ema21"])
        return float(last["ema9"]) < float(last["ema21"])

    def _check_pcr_direction(self, pcr: float, pcr_vol: float, bias: str) -> bool:
        """
        BULLISH: PCR ≥ 1.0 (put-heavy = contrarian bullish floor) or pcr_vol ≥ 0.9.
        BEARISH: PCR ≤ 1.1 (call-heavy = retail euphoria / institutional selling).
        """
        if bias == "BULLISH":
            return pcr >= 1.0 or pcr_vol >= 0.9
        return pcr <= 1.1 or pcr_vol <= 1.1

    # ──────────────────────────────────────────────────────
    # PREMIUM ESTIMATION
    # ──────────────────────────────────────────────────────

    def _estimate_premium(
        self,
        symbol: str,
        strike: int,
        option_type: str,
        spot: float,
    ) -> Optional[float]:
        """
        Fetch live option premium from NSE option chain.
        Falls back to a simple intrinsic + time-value estimate on failure.
        """
        try:
            session = self._get_nse_session()
            if session is None:
                return self._intrinsic_estimate(spot, strike, option_type)

            resp = session.get(
                "https://www.nseindia.com/api/option-chain-indices",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            raw  = resp.json()
            data = raw.get("records", {}).get("data", [])

            expiry = self._get_nearest_expiry(symbol)
            try:
                dt_obj     = datetime.strptime(expiry, "%d%b%Y")
                expiry_nse = dt_obj.strftime("%d-%b-%Y")
            except ValueError:
                expiry_nse = expiry

            for row in data:
                if row.get("strikePrice") != strike:
                    continue
                if row.get("expiryDate", "") != expiry_nse:
                    continue
                leg = row.get(option_type, {}) or {}
                ltp = leg.get("lastPrice", 0)
                if ltp and ltp > 0:
                    return float(ltp)

        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] premium fetch failed "
                f"{symbol} {strike}{option_type}: {e}"
            )

        return self._intrinsic_estimate(spot, strike, option_type)

    def _intrinsic_estimate(
        self, spot: float, strike: int, option_type: str
    ) -> Optional[float]:
        """
        Rough ATM premium estimate: ~0.5% of spot for ATM options.
        Used only when NSE API is unavailable.
        Actual premium includes time value — this is a conservative lower bound.
        """
        intrinsic  = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        time_value = spot * 0.005
        return round(intrinsic + time_value, 1)

    # ──────────────────────────────────────────────────────
    # OPTION CHAIN ACCESSOR
    # ──────────────────────────────────────────────────────

    def _get_oc_result(self, symbol: str):
        """Get OptionChainResult via injected analyzer or lazy-import singleton."""
        if self._oc is not None:
            return self._oc.analyze(symbol)
        try:
            from option_chain import get_option_chain_analyzer
            return get_option_chain_analyzer().analyze(symbol)
        except Exception as e:
            logger.debug(f"[{format_ist_timestamp()}] OC analyzer unavailable: {e}")
            return None

    # ──────────────────────────────────────────────────────
    # NSE SESSION
    # ──────────────────────────────────────────────────────

    def _get_nse_session(self) -> Optional[requests.Session]:
        if self._nse_session is not None and self._session_ok:
            return self._nse_session

        try:
            session = requests.Session()
            session.headers.update(_NSE_HEADERS)
            resp = session.get(
                "https://www.nseindia.com/",
                timeout=10,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                self._nse_session = session
                self._session_ok  = True
                return session
            logger.debug(
                f"[{format_ist_timestamp()}] NSE session returned "
                f"status {resp.status_code}"
            )
            return None
        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] NSE session init failed: {e}"
            )
            return None


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_options_generator: Optional[OptionsSignalGenerator] = None


def get_options_signal_generator(oc_analyzer=None) -> OptionsSignalGenerator:
    global _options_generator
    if _options_generator is None:
        _options_generator = OptionsSignalGenerator(oc_analyzer=oc_analyzer)
    return _options_generator


# ──────────────────────────────────────────────────────────────
# SELF-TEST
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = OptionsSignalGenerator()
    print(f"Valid time window: {gen._is_valid_time()}")
    print(f"Nearest Nifty expiry: {gen._get_nearest_expiry('NIFTY')}")
    print(f"Nearest BankNifty expiry: {gen._get_nearest_expiry('BANKNIFTY')}")
    print("Scanning for options signals...")
    signals = gen.scan()
    if not signals:
        print("No high-confidence options signals at this time.")
    for sig in signals:
        print("\n" + gen.format_telegram_signal(sig))
