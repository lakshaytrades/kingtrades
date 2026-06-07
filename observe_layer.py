"""
Observe Layer — global macro, sentiment, institutional flow, and alt-data scoring.
All scores return floats in [-10, +10].
"""

import logging
import time
from functools import lru_cache
import numpy as np

logger = logging.getLogger(__name__)

_MACRO_CACHE = {"score": 0.0, "ts": 0.0}
_SENT_CACHE = {"score": 0.0, "ts": 0.0}
_FLOW_CACHE = {"score": 0.0, "ts": 0.0}
_ALT_CACHE: dict = {}
_TTL = 300


def _fetch(ticker: str, period: str = "5d", interval: str = "1d"):
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        return df
    except Exception as e:
        logger.debug(f"yfinance fetch {ticker}: {e}")
        return None


class ObserveLayer:
    def get_macro_score(self) -> float:
        now = time.time()
        if now - _MACRO_CACHE["ts"] < _TTL:
            return _MACRO_CACHE["score"]
        score = self._compute_macro()
        _MACRO_CACHE.update({"score": score, "ts": now})
        return score

    def _compute_macro(self) -> float:
        score = 0.0
        try:
            # Yield curve: 10Y - 2Y
            t10 = _fetch("^TNX", "5d", "1d")
            t2 = _fetch("^IRX", "5d", "1d")
            if t10 is not None and t2 is not None and len(t10) > 0 and len(t2) > 0:
                spread = float(t10["Close"].iloc[-1]) - float(t2["Close"].iloc[-1]) / 100.0
                if spread > 1.5:
                    score += 3
                elif spread > 0.5:
                    score += 1.5
                elif spread > -0.2:
                    score += 0
                else:
                    score -= 3  # Inverted = recession signal
        except Exception as e:
            logger.debug(f"yield curve: {e}")

        try:
            # DXY proxy: strong dollar = headwind for equities
            uup = _fetch("UUP", "10d", "1d")
            if uup is not None and len(uup) >= 5:
                ret = float(uup["Close"].iloc[-1]) / float(uup["Close"].iloc[-5]) - 1
                if ret > 0.02:
                    score -= 2   # strong dollar = risk-off
                elif ret < -0.02:
                    score += 2   # weak dollar = risk-on
        except Exception as e:
            logger.debug(f"DXY proxy: {e}")

        try:
            # VIX level
            vix = _fetch("^VIX", "5d", "1d")
            if vix is not None and len(vix) > 0:
                vix_val = float(vix["Close"].iloc[-1])
                if vix_val < 15:
                    score += 3
                elif vix_val < 20:
                    score += 1
                elif vix_val < 30:
                    score -= 2
                else:
                    score -= 5
        except Exception as e:
            logger.debug(f"VIX macro: {e}")

        try:
            # Gold momentum: gold up = risk-off
            gold = _fetch("GC=F", "10d", "1d")
            if gold is not None and len(gold) >= 5:
                ret = float(gold["Close"].iloc[-1]) / float(gold["Close"].iloc[-5]) - 1
                if ret > 0.02:
                    score -= 1.5
                elif ret < -0.02:
                    score += 1.5
        except Exception as e:
            logger.debug(f"Gold: {e}")

        try:
            # Oil momentum: moderate oil up = growth signal
            oil = _fetch("CL=F", "10d", "1d")
            if oil is not None and len(oil) >= 5:
                ret = float(oil["Close"].iloc[-1]) / float(oil["Close"].iloc[-5]) - 1
                if 0.01 < ret < 0.05:
                    score += 1
                elif ret > 0.05:
                    score -= 1  # too high = stagflation risk
        except Exception as e:
            logger.debug(f"Oil: {e}")

        return max(-10.0, min(10.0, score))

    def get_sentiment_score(self) -> float:
        now = time.time()
        if now - _SENT_CACHE["ts"] < _TTL:
            return _SENT_CACHE["score"]
        score = self._compute_sentiment()
        _SENT_CACHE.update({"score": score, "ts": now})
        return score

    def _compute_sentiment(self) -> float:
        score = 0.0
        try:
            vix = _fetch("^VIX", "10d", "1d")
            vix3m = _fetch("^VIX3M", "10d", "1d")
            if vix is not None and vix3m is not None and len(vix) > 0 and len(vix3m) > 0:
                ratio = float(vix["Close"].iloc[-1]) / float(vix3m["Close"].iloc[-1])
                if ratio < 0.85:
                    score += 4   # term structure calm = bullish
                elif ratio < 0.95:
                    score += 2
                elif ratio < 1.05:
                    score += 0
                elif ratio < 1.15:
                    score -= 3
                else:
                    score -= 6   # contango panic
        except Exception as e:
            logger.debug(f"VIX term: {e}")

        try:
            # SPY RSI as sentiment proxy
            spy = _fetch("SPY", "60d", "1d")
            if spy is not None and len(spy) >= 14:
                closes = spy["Close"].values.astype(float)
                delta = np.diff(closes)
                gain = np.where(delta > 0, delta, 0)
                loss = np.where(delta < 0, -delta, 0)
                avg_gain = np.mean(gain[-14:])
                avg_loss = np.mean(loss[-14:])
                rsi = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
                if rsi > 70:
                    score -= 2   # overbought = sentiment extreme
                elif rsi > 60:
                    score += 1
                elif rsi > 45:
                    score += 2
                elif rsi > 35:
                    score -= 1
                else:
                    score -= 3   # oversold = fear
        except Exception as e:
            logger.debug(f"SPY RSI: {e}")

        try:
            # Put/Call ratio from SPY options
            import yfinance as yf
            spy = yf.Ticker("SPY")
            expiries = spy.options
            if expiries:
                chain = spy.option_chain(expiries[0])
                put_oi = chain.puts["openInterest"].sum()
                call_oi = chain.calls["openInterest"].sum()
                if call_oi > 0:
                    pcr = put_oi / call_oi
                    if pcr > 1.5:
                        score += 3   # extreme fear = contrarian bull
                    elif pcr > 1.2:
                        score += 1.5
                    elif pcr > 0.8:
                        score += 0
                    elif pcr > 0.6:
                        score -= 1.5
                    else:
                        score -= 3   # extreme greed = contrarian bear
        except Exception as e:
            logger.debug(f"P/C ratio: {e}")

        return max(-10.0, min(10.0, score))

    def get_flow_score(self) -> float:
        now = time.time()
        if now - _FLOW_CACHE["ts"] < _TTL:
            return _FLOW_CACHE["score"]
        score = self._compute_flow()
        _FLOW_CACHE.update({"score": score, "ts": now})
        return score

    def _compute_flow(self) -> float:
        score = 0.0
        try:
            # Credit spreads proxy: HYG (high-yield) vs LQD (investment-grade)
            hyg = _fetch("HYG", "10d", "1d")
            lqd = _fetch("LQD", "10d", "1d")
            if hyg is not None and lqd is not None and len(hyg) >= 5 and len(lqd) >= 5:
                hyg_ret = float(hyg["Close"].iloc[-1]) / float(hyg["Close"].iloc[-5]) - 1
                lqd_ret = float(lqd["Close"].iloc[-1]) / float(lqd["Close"].iloc[-5]) - 1
                spread_chg = hyg_ret - lqd_ret
                if spread_chg > 0.01:
                    score += 3   # HY outperforming = risk appetite
                elif spread_chg > 0:
                    score += 1
                elif spread_chg > -0.01:
                    score -= 1
                else:
                    score -= 3   # HY underperforming = risk-off
        except Exception as e:
            logger.debug(f"HYG/LQD: {e}")

        try:
            # Financials vs SPY (leading indicator)
            xlf = _fetch("XLF", "10d", "1d")
            spy = _fetch("SPY", "10d", "1d")
            if xlf is not None and spy is not None and len(xlf) >= 5 and len(spy) >= 5:
                xlf_ret = float(xlf["Close"].iloc[-1]) / float(xlf["Close"].iloc[-5]) - 1
                spy_ret = float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-5]) - 1
                rs = xlf_ret - spy_ret
                if rs > 0.01:
                    score += 2
                elif rs < -0.01:
                    score -= 2
        except Exception as e:
            logger.debug(f"XLF/SPY: {e}")

        try:
            # Sector rotation: risk-on (XLK > XLU > XLP order = good)
            xlu = _fetch("XLU", "5d", "1d")
            xlp = _fetch("XLP", "5d", "1d")
            xlk = _fetch("XLK", "5d", "1d")
            rets = {}
            for name, df in [("XLU", xlu), ("XLP", xlp), ("XLK", xlk)]:
                if df is not None and len(df) >= 2:
                    rets[name] = float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1
            if "XLK" in rets and "XLU" in rets:
                if rets["XLK"] > rets["XLU"]:
                    score += 2   # Growth > Defensive = risk-on
                else:
                    score -= 2
        except Exception as e:
            logger.debug(f"Sector rotation: {e}")

        try:
            # Small-cap vs large-cap (risk appetite indicator)
            iwm = _fetch("IWM", "10d", "1d")
            spy = _fetch("SPY", "10d", "1d")
            if iwm is not None and spy is not None and len(iwm) >= 5 and len(spy) >= 5:
                iwm_ret = float(iwm["Close"].iloc[-1]) / float(iwm["Close"].iloc[-5]) - 1
                spy_ret = float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-5]) - 1
                if iwm_ret > spy_ret + 0.005:
                    score += 2   # Small caps outperforming = risk-on
                elif iwm_ret < spy_ret - 0.005:
                    score -= 2
        except Exception as e:
            logger.debug(f"IWM/SPY: {e}")

        return max(-10.0, min(10.0, score))

    def get_alt_data_score(self, symbol: str) -> float:
        now = time.time()
        if symbol in _ALT_CACHE and now - _ALT_CACHE[symbol]["ts"] < _TTL:
            return _ALT_CACHE[symbol]["score"]
        score = self._compute_alt(symbol)
        _ALT_CACHE[symbol] = {"score": score, "ts": now}
        return score

    def _compute_alt(self, symbol: str) -> float:
        score = 0.0
        try:
            # Options skew: put IV > call IV = bearish; call IV > put IV = bullish
            import yfinance as yf
            tk = yf.Ticker(symbol)
            expiries = tk.options
            if expiries:
                chain = tk.option_chain(expiries[0])
                atm_calls = chain.calls.sort_values("inTheMoney")
                atm_puts = chain.puts.sort_values("inTheMoney")
                if len(atm_calls) > 0 and len(atm_puts) > 0:
                    call_iv = float(atm_calls["impliedVolatility"].iloc[0])
                    put_iv = float(atm_puts["impliedVolatility"].iloc[0])
                    skew = put_iv - call_iv
                    if skew > 0.10:
                        score -= 3   # put skew = bearish
                    elif skew > 0.05:
                        score -= 1.5
                    elif skew < -0.05:
                        score += 2   # call skew = bullish
        except Exception as e:
            logger.debug(f"options skew {symbol}: {e}")

        try:
            # Relative strength vs SPY
            stk = _fetch(symbol, "20d", "1d")
            spy = _fetch("SPY", "20d", "1d")
            if stk is not None and spy is not None and len(stk) >= 10 and len(spy) >= 10:
                stk_ret = float(stk["Close"].iloc[-1]) / float(stk["Close"].iloc[-10]) - 1
                spy_ret = float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-10]) - 1
                rs = stk_ret - spy_ret
                if rs > 0.05:
                    score += 4
                elif rs > 0.02:
                    score += 2
                elif rs < -0.05:
                    score -= 4
                elif rs < -0.02:
                    score -= 2
        except Exception as e:
            logger.debug(f"RS {symbol}: {e}")

        return max(-10.0, min(10.0, score))
