"""
Secret Alpha — high-edge systematic signals:
Gamma walls, short squeeze scoring, PEAD, seasonal patterns, smart money divergence.
"""

import logging
import time
from datetime import datetime, date
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL = 900  # 15 minutes for options data


def _cached(key: str, fn, ttl: int = _TTL):
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["ts"] < ttl:
        return _CACHE[key]["val"]
    val = fn()
    _CACHE[key] = {"val": val, "ts": now}
    return val


class GammaWallDetector:
    """
    Gamma Exposure walls from options open interest.
    High call OI = ceiling; high put OI = floor (dealer hedging creates price magnets).
    """

    def get_score(self, symbol: str) -> float:
        """Returns score in [-10, +10]. Positive = gamma tailwind, negative = headwind."""
        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            expiries = tk.options
            if not expiries:
                return 0.0

            # Use front-month expiry
            chain = tk.option_chain(expiries[0])
            calls = chain.calls
            puts = chain.puts

            # Max pain: price where total option value is minimized for buyers
            spot = None
            try:
                info = tk.fast_info
                spot = info.last_price
            except Exception:
                pass

            if spot is None or spot == 0:
                return 0.0

            # Put/Call OI ratio
            total_call_oi = calls["openInterest"].sum()
            total_put_oi = puts["openInterest"].sum()
            pcr = total_put_oi / (total_call_oi + 1e-9)

            # ATM IV skew: put IV vs call IV at nearest strikes
            strikes = calls["strike"].values
            atm_strike = strikes[np.argmin(np.abs(strikes - spot))]
            atm_calls = calls[calls["strike"] == atm_strike]
            atm_puts = puts[puts["strike"] == atm_strike]

            call_iv = float(atm_calls["impliedVolatility"].iloc[0]) if len(atm_calls) > 0 else 0.25
            put_iv = float(atm_puts["impliedVolatility"].iloc[0]) if len(atm_puts) > 0 else 0.25
            skew = put_iv - call_iv  # positive = put skew = bearish

            # Gamma walls: strikes with very high OI create support/resistance
            call_wall = calls.loc[calls["openInterest"].idxmax(), "strike"]
            put_wall = puts.loc[puts["openInterest"].idxmax(), "strike"]

            # Position relative to walls
            above_call_wall = spot > call_wall  # broken through resistance = bullish
            below_put_wall = spot < put_wall    # broken through support = bearish
            between_walls = put_wall <= spot <= call_wall

            score = 0.0

            # PCR contrarian signal
            if pcr > 1.5:
                score += 3   # extreme bearishness = contrarian bull
            elif pcr > 1.2:
                score += 1.5
            elif pcr < 0.6:
                score -= 3   # extreme bullishness = contrarian bear
            elif pcr < 0.8:
                score -= 1.5

            # Skew signal
            if skew > 0.10:
                score -= 2   # heavy put skew = fear
            elif skew < -0.05:
                score += 2   # call skew = greed/demand

            # Gamma wall position
            if above_call_wall:
                score += 2   # gamma squeeze territory
            elif below_put_wall:
                score -= 2
            elif between_walls:
                score *= 0.7  # pinned = lower conviction

            return max(-10.0, min(10.0, score))
        except Exception as e:
            logger.debug(f"GammaWall {symbol}: {e}")
            return 0.0


class ShortSqueezeScorer:
    """Score short squeeze potential: high short float + low days-to-cover + rising price."""

    def get_score(self, symbol: str, closes: Optional[np.ndarray] = None) -> float:
        """Returns score in [0, 10]. Higher = more squeeze potential."""
        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            info = tk.info

            short_pct = info.get("shortPercentOfFloat", 0) or 0
            if short_pct < 1:  # might be fraction not percent
                short_pct *= 100

            shares_short = info.get("sharesShort", 0) or 0
            avg_vol = info.get("averageVolume", 1) or 1
            days_to_cover = shares_short / avg_vol if avg_vol > 0 else 0

            score = 0.0

            # Short interest component (max 4 points)
            if short_pct > 30:
                score += 4
            elif short_pct > 20:
                score += 3
            elif short_pct > 10:
                score += 2
            elif short_pct > 5:
                score += 1

            # Days to cover component (max 3 points)
            if days_to_cover > 10:
                score += 3
            elif days_to_cover > 5:
                score += 2
            elif days_to_cover > 2:
                score += 1

            # Price momentum component (max 3 points)
            if closes is not None and len(closes) >= 5:
                closes = np.asarray(closes, dtype=float)
                mom = closes[-1] / closes[-5] - 1
                if mom > 0.05:
                    score += 3
                elif mom > 0.02:
                    score += 1.5
                elif mom < -0.03:
                    score -= 2  # short side winning, no squeeze yet

            return max(0.0, min(10.0, score))
        except Exception as e:
            logger.debug(f"ShortSqueeze {symbol}: {e}")
            return 0.0


class PEADEngine:
    """Post-Earnings Announcement Drift: detect recent earnings surprise and estimate drift."""

    def get_score(self, symbol: str, closes: Optional[np.ndarray] = None) -> float:
        """Returns score in [-5, +5]. Positive = upward PEAD, negative = downward."""
        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)

            # Check for recent earnings (within last 5 trading days)
            calendar = tk.calendar
            if calendar is None:
                return 0.0

            earnings_date = None
            if hasattr(calendar, 'columns') and "Earnings Date" in calendar.columns:
                dates = calendar["Earnings Date"].dropna()
                if len(dates) > 0:
                    earnings_date = dates.iloc[0]
                    if hasattr(earnings_date, 'date'):
                        earnings_date = earnings_date.date()

            if earnings_date is None:
                return 0.0

            today = date.today()
            days_since = (today - earnings_date).days if hasattr(earnings_date, '__sub__') else 999

            if days_since > 10 or days_since < 0:
                return 0.0  # too old or upcoming, no PEAD signal

            # Estimate surprise from gap on earnings day
            if closes is not None and len(closes) >= 10:
                closes = np.asarray(closes, dtype=float)
                # Approximate: large gap = large surprise
                # Look for biggest single-day move in last 10 bars
                moves = np.abs(np.diff(closes[-10:])) / closes[-10:-1]
                max_move = moves.max()
                max_move_idx = moves.argmax()

                # Estimate direction of gap (surprise direction)
                direction = 1 if closes[-10 + max_move_idx + 1] > closes[-10 + max_move_idx] else -1

                # PEAD decays over days: day 1 = strongest drift, day 10 = weak
                decay = max(0.1, 1 - days_since * 0.09)
                magnitude = min(max_move / 0.02, 1.0)  # normalize: 2% gap = 1.0

                score = direction * magnitude * 5.0 * decay
                return max(-5.0, min(5.0, score))

            return 0.0
        except Exception as e:
            logger.debug(f"PEAD {symbol}: {e}")
            return 0.0


class SeasonalAlpha:
    """Pure calendar-based seasonal effects. No data fetch required."""

    # Day-of-week alphas (Monday=0 ... Friday=4)
    DOW_SCORES = {0: -1.0, 1: 0.5, 2: 0.5, 3: 1.0, 4: -0.5}

    # Month effects (1=Jan ... 12=Dec)
    MONTH_SCORES = {
        1: 2.5,   # January effect
        2: 0.5,
        3: -0.5,
        4: 1.5,   # April seasonal strength
        5: -1.0,  # Sell in May
        6: -0.5,
        7: 1.0,
        8: -0.5,
        9: -3.0,  # September effect (worst month statistically)
        10: -1.0, # October volatility
        11: 2.0,  # November seasonal strength
        12: 1.5,  # Santa rally
    }

    def get_score(self, dt: Optional[datetime] = None) -> float:
        """Returns score in [-5, +5]."""
        if dt is None:
            dt = datetime.now()

        score = 0.0

        # Day of week
        dow = dt.weekday()
        score += self.DOW_SCORES.get(dow, 0)

        # Month effect
        score += self.MONTH_SCORES.get(dt.month, 0) * 0.5

        # Turn of month: last 2 days + first 3 days = bullish
        day = dt.day
        if day <= 3 or day >= 28:
            score += 1.5

        # OPEX week: options expiry Friday (3rd Friday of month) — volatile/bearish
        # Approximate: if it's mid-month (13-19) and Thursday/Friday
        if 13 <= day <= 19 and dow >= 3:
            score -= 1.0

        # Pre-holiday: day before market holiday = bullish (approximated by Friday before long weekend)
        # This is a simple proxy; a real implementation would use a holiday calendar
        if dow == 3 and day >= 28:  # Thursday near month-end before potential long weekend
            score += 0.5

        return max(-5.0, min(5.0, score))


class SmartMoneyDivergence:
    """Detect divergence between price and volume/RSI — smart money signals."""

    def get_score(self, closes: np.ndarray, volumes: Optional[np.ndarray] = None,
                  highs: Optional[np.ndarray] = None, lows: Optional[np.ndarray] = None) -> float:
        """Returns score in [-8, +8]. Positive = bullish divergence, negative = bearish."""
        if closes is None or len(closes) < 14:
            return 0.0
        try:
            closes = np.asarray(closes, dtype=float)
            score = 0.0

            # === RSI Divergence ===
            rsi = self._compute_rsi(closes, 14)
            if len(rsi) >= 10:
                price_trend = closes[-1] - closes[-10]
                rsi_trend = rsi[-1] - rsi[-10]

                # Bullish divergence: price lower lows, RSI higher lows
                if price_trend < -0.005 * closes[-1] and rsi_trend > 2:
                    score += 4
                # Bearish divergence: price higher highs, RSI lower highs
                elif price_trend > 0.005 * closes[-1] and rsi_trend < -2:
                    score -= 4

            # === Volume-Price Divergence ===
            if volumes is not None and len(volumes) >= 10:
                volumes = np.asarray(volumes, dtype=float)
                price_chg = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] != 0 else 0
                vol_chg = (np.mean(volumes[-3:]) - np.mean(volumes[-8:-3])) / (np.mean(volumes[-8:-3]) + 1e-9)

                # Rising price, declining volume = distribution (bearish)
                if price_chg > 0.01 and vol_chg < -0.20:
                    score -= 3
                # Falling price, declining volume = absorption (bullish)
                elif price_chg < -0.01 and vol_chg < -0.20:
                    score += 3
                # Rising price, rising volume = healthy uptrend (bullish confirmation)
                elif price_chg > 0.01 and vol_chg > 0.20:
                    score += 2
                # Falling price, rising volume = distribution (bearish confirmation)
                elif price_chg < -0.01 and vol_chg > 0.20:
                    score -= 2

            # === Institutional-size candle divergence ===
            # If price makes new high but candle closes in lower half = smart money selling
            if highs is not None and lows is not None and len(highs) >= 5:
                highs = np.asarray(highs, dtype=float)
                lows = np.asarray(lows, dtype=float)
                new_high = closes[-1] >= np.max(highs[-10:]) * 0.995 if len(highs) >= 10 else False
                spread = highs[-1] - lows[-1]
                close_pos = (closes[-1] - lows[-1]) / spread if spread > 0 else 0.5
                if new_high and close_pos < 0.4:
                    score -= 2  # Bearish — new high but weak close
                if closes[-1] <= np.min(lows[-10:]) * 1.005 and close_pos > 0.6:
                    score += 2  # Bullish — new low but strong close (absorption)

            return max(-8.0, min(8.0, score))
        except Exception as e:
            logger.debug(f"SmartMoney error: {e}")
            return 0.0

    def _compute_rsi(self, closes: np.ndarray, period: int = 14) -> np.ndarray:
        delta = np.diff(closes)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        rsi_vals = []
        if len(gain) < period:
            return np.array(rsi_vals)
        avg_gain = np.mean(gain[:period])
        avg_loss = np.mean(loss[:period])
        for i in range(period, len(gain)):
            avg_gain = (avg_gain * (period - 1) + gain[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i]) / period
            rs = avg_gain / (avg_loss + 1e-10)
            rsi_vals.append(100 - 100 / (1 + rs))
        return np.array(rsi_vals)


class SecretAlpha:
    """Facade that combines all secret alpha signals into a score dict."""

    def __init__(self):
        self.gamma = GammaWallDetector()
        self.squeeze = ShortSqueezeScorer()
        self.pead = PEADEngine()
        self.seasonal = SeasonalAlpha()
        self.smart_money = SmartMoneyDivergence()

    def get_scores(self, symbol: str, closes: Optional[np.ndarray] = None,
                   volumes: Optional[np.ndarray] = None,
                   highs: Optional[np.ndarray] = None,
                   lows: Optional[np.ndarray] = None,
                   dt: Optional[datetime] = None) -> dict:
        result = {
            "gamma_score": 0.0,
            "short_squeeze_score": 0.0,
            "pead_score": 0.0,
            "seasonal_score": 0.0,
            "smart_money_score": 0.0,
        }

        def safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                logger.debug(f"SecretAlpha safe call error: {e}")
                return 0.0

        result["gamma_score"] = safe(lambda: _cached(
            f"gamma_{symbol}", lambda: self.gamma.get_score(symbol), ttl=900
        ))

        result["short_squeeze_score"] = safe(lambda: _cached(
            f"squeeze_{symbol}", lambda: self.squeeze.get_score(symbol, closes), ttl=1800
        ))

        result["pead_score"] = safe(lambda: _cached(
            f"pead_{symbol}", lambda: self.pead.get_score(symbol, closes), ttl=3600
        ))

        result["seasonal_score"] = safe(lambda: self.seasonal.get_score(dt))

        result["smart_money_score"] = safe(lambda: self.smart_money.get_score(
            closes, volumes, highs, lows
        ) if closes is not None else 0.0)

        return result
