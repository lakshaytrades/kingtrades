"""
pairs_trading.py — NSE Momentum Groww AI Bot
Statistical Arbitrage Engine — Trade Correlated NSE Stock Pairs

Strategy: When highly correlated sector-peer stocks diverge, bet on reversion.
Works best during midday (11:00 AM – 1:30 PM IST) when directional momentum fades.
Market-neutral: profit from convergence regardless of overall market direction.

Short side requires F&O eligibility — equity-only stocks cannot be shorted intraday.
Z-score uses log price ratio over a 20-day rolling window. Entry at |z| > 2.0.

⚠️ REAL MONEY — both legs execute as MIS intraday orders via execution_groww.py
"""

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from utils import format_ist_timestamp, get_current_ist_time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

NSE_PAIRS: List[Tuple[str, str]] = [
    ("HDFCBANK",   "ICICIBANK"),
    ("AXISBANK",   "INDUSINDBK"),
    ("TCS",        "INFY"),
    ("WIPRO",      "HCLTECH"),
    ("JSWSTEEL",   "TATASTEEL"),
    ("RELIANCE",   "ONGC"),
    ("SUNPHARMA",  "CIPLA"),
    ("MARUTI",     "TATAMOTORS"),
    ("HINDUNILVR", "ITC"),
    ("NTPC",       "POWERGRID"),
]

_MIDDAY_START = time(11, 0)
_MIDDAY_END   = time(13, 30)

_PAIR_CACHE_TTL_SECS = 900  # 15 minutes


@dataclass
class PairSignal:
    symbol_long:   str
    symbol_short:  str
    zscore:        float
    spread_pct:    float
    entry_long:    float
    entry_short:   float
    target_zscore: float
    stop_zscore:   float
    confidence:    float
    reason:        str


class PairsTradingEngine:
    LOOKBACK     = 20
    ENTRY_ZSCORE = 2.0
    EXIT_ZSCORE  = 0.5
    STOP_ZSCORE  = 3.5

    def __init__(self):
        self._price_cache:  Dict[str, Tuple[pd.Series, float]] = {}
        self._corr_cache:   Dict[str, Tuple[float, float]] = {}

    # ──────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────

    def scan_pairs(self, data_fetcher=None) -> List[PairSignal]:
        signals: List[PairSignal] = []
        for sym1, sym2 in NSE_PAIRS:
            try:
                sig = self._evaluate_pair(sym1, sym2)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                logger.warning(
                    f"[{format_ist_timestamp()}] pairs_scan skipping "
                    f"{sym1}/{sym2}: {e}"
                )
        signals.sort(key=lambda s: abs(s.zscore), reverse=True)
        return signals

    def get_active_signals(self) -> List[PairSignal]:
        return self.scan_pairs()

    def calculate_zscore(self, sym1: str, sym2: str) -> Tuple[float, float]:
        """
        Returns (zscore, spread_pct) using 20-day log ratio.
        spread_pct is the current ratio deviation from its historical mean (as %).
        """
        hist1, live1 = self._get_price_data(sym1)
        hist2, live2 = self._get_price_data(sym2)

        if hist1 is None or hist2 is None or live1 == 0 or live2 == 0:
            return (0.0, 0.0)

        aligned = pd.DataFrame({"s1": hist1, "s2": hist2}).dropna().tail(self.LOOKBACK)
        if len(aligned) < 10:
            return (0.0, 0.0)

        spread   = np.log(aligned["s1"] / aligned["s2"])
        mean_val = float(spread.mean())
        std_val  = float(spread.std())

        if std_val == 0:
            return (0.0, 0.0)

        current_log_ratio = np.log(live1 / live2)
        zscore            = (current_log_ratio - mean_val) / std_val

        # spread_pct: how far the current ratio is from the historical mean ratio, in %
        hist_mean_ratio = np.exp(mean_val)
        current_ratio   = live1 / live2
        spread_pct      = (current_ratio / hist_mean_ratio - 1.0) * 100.0

        return (float(zscore), float(spread_pct))

    def is_midday(self) -> bool:
        now_ist = get_current_ist_time()
        return _MIDDAY_START <= now_ist.time() <= _MIDDAY_END

    def should_scan(self) -> bool:
        return self.is_midday()

    def format_telegram_signal(self, sig: PairSignal) -> str:
        direction = "+" if sig.zscore > 0 else ""
        est_profit = abs(sig.spread_pct) * 100_000 / 100  # rough ₹ on ₹1L notional
        return (
            f"⚖️ PAIRS TRADE SIGNAL\n"
            f"LONG  {sig.symbol_long:<12} @ ₹{sig.entry_long:,.2f}\n"
            f"SHORT {sig.symbol_short:<12} @ ₹{sig.entry_short:,.2f}\n"
            f"Z-Score: {direction}{sig.zscore:.2f} "
            f"(spread is {direction}{sig.spread_pct:.1f}% vs mean)\n"
            f"Target: Z-Score returns to {sig.target_zscore:.1f} "
            f"(est. ₹{est_profit:,.0f} profit on 1L)\n"
            f"Stop: Z-Score reaches {sig.stop_zscore:.1f}\n"
            f"Confidence: {sig.confidence:.0f}%\n"
            f"[{format_ist_timestamp()}]"
        )

    # ──────────────────────────────────────────────────────
    # INTERNAL PAIR EVALUATION
    # ──────────────────────────────────────────────────────

    def _evaluate_pair(self, sym1: str, sym2: str) -> Optional[PairSignal]:
        from nse_fo_list import is_fo_eligible

        zscore, spread_pct = self.calculate_zscore(sym1, sym2)

        if abs(zscore) <= self.ENTRY_ZSCORE:
            return None

        _, live1 = self._get_price_data(sym1)
        _, live2 = self._get_price_data(sym2)

        if live1 == 0 or live2 == 0:
            return None

        # Positive zscore → sym1 expensive vs sym2 → SHORT sym1, LONG sym2
        if zscore > 0:
            sym_short, sym_long   = sym1, sym2
            entry_short, entry_long = live1, live2
        else:
            sym_short, sym_long   = sym2, sym1
            entry_short, entry_long = live2, live1

        if not is_fo_eligible(sym_short):
            logger.debug(
                f"[{format_ist_timestamp()}] pairs skip {sym1}/{sym2}: "
                f"{sym_short} not F&O eligible — cannot short"
            )
            return None

        correlation = self._get_correlation(sym1, sym2)
        confidence  = min(100.0, abs(zscore) / self.ENTRY_ZSCORE * 60.0 + correlation * 40.0)

        reason = (
            f"{sym_short} expensive vs {sym_long} by {abs(spread_pct):.1f}%; "
            f"z={zscore:.2f}, corr={correlation:.2f}"
        )

        return PairSignal(
            symbol_long=sym_long,
            symbol_short=sym_short,
            zscore=round(zscore, 3),
            spread_pct=round(spread_pct, 2),
            entry_long=round(entry_long, 2),
            entry_short=round(entry_short, 2),
            target_zscore=self.EXIT_ZSCORE,
            stop_zscore=self.STOP_ZSCORE,
            confidence=round(confidence, 1),
            reason=reason,
        )

    # ──────────────────────────────────────────────────────
    # PRICE DATA — yfinance with 15-min cache
    # ──────────────────────────────────────────────────────

    def _get_price_data(self, symbol: str) -> Tuple[Optional[pd.Series], float]:
        """
        Returns (historical_close_series, live_price).
        Caches for 15 minutes to limit yfinance calls during a scan.
        """
        now_epoch = _time.time()
        cached = self._price_cache.get(symbol)
        if cached is not None:
            series, ts = cached
            if now_epoch - ts < _PAIR_CACHE_TTL_SECS:
                live = float(series.iloc[-1]) if series is not None and len(series) > 0 else 0.0
                return series, live

        ticker = symbol + ".NS"
        try:
            raw = yf.download(
                ticker,
                period="30d",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
            if raw is None or raw.empty:
                logger.warning(
                    f"[{format_ist_timestamp()}] yfinance returned empty for {ticker}"
                )
                self._price_cache[symbol] = (None, now_epoch)
                return None, 0.0

            # yfinance may return MultiIndex columns when downloading single ticker
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)

            close = raw["Close"].dropna()
            if len(close) < 5:
                self._price_cache[symbol] = (None, now_epoch)
                return None, 0.0

            self._price_cache[symbol] = (close, now_epoch)
            return close, float(close.iloc[-1])

        except Exception as e:
            logger.warning(
                f"[{format_ist_timestamp()}] price fetch failed for {symbol}: {e}"
            )
            self._price_cache[symbol] = (None, now_epoch)
            return None, 0.0

    def _get_correlation(self, sym1: str, sym2: str) -> float:
        """30-day daily return correlation. Cached per pair."""
        key      = f"{sym1}_{sym2}"
        now_epoch = _time.time()
        cached   = self._corr_cache.get(key)
        if cached is not None:
            corr, ts = cached
            if now_epoch - ts < _PAIR_CACHE_TTL_SECS:
                return corr

        try:
            hist1, _ = self._get_price_data(sym1)
            hist2, _ = self._get_price_data(sym2)

            if hist1 is None or hist2 is None:
                return 0.0

            aligned = pd.DataFrame({"s1": hist1, "s2": hist2}).dropna()
            if len(aligned) < 10:
                return 0.0

            returns1 = aligned["s1"].pct_change().dropna()
            returns2 = aligned["s2"].pct_change().dropna()
            corr     = float(returns1.corr(returns2))
            corr     = max(0.0, min(1.0, corr))  # clip to [0, 1]

            self._corr_cache[key] = (corr, now_epoch)
            return corr

        except Exception as e:
            logger.debug(f"correlation calc failed {sym1}/{sym2}: {e}")
            return 0.0


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_pairs_engine: Optional[PairsTradingEngine] = None


def get_pairs_engine() -> PairsTradingEngine:
    global _pairs_engine
    if _pairs_engine is None:
        _pairs_engine = PairsTradingEngine()
    return _pairs_engine


# ──────────────────────────────────────────────────────────────
# SELF-TEST
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = PairsTradingEngine()
    print(f"Midday window active: {engine.is_midday()}")
    print("Scanning pairs (may take ~30s)...")
    signals = engine.scan_pairs()
    if not signals:
        print("No divergent pairs found at this time.")
    for sig in signals:
        print("\n" + engine.format_telegram_signal(sig))
