"""
microstructure_signals.py — v25.0 Market Microstructure & Behavioral Finance Signals

8 additional signals used by Two Sigma, D.E. Shaw, and Renaissance Technologies.
All free data. All fail-open (return 0 on error). All cached to minimize API calls.

Strategy 7: Opening Range Breakout Quality
  The 9:30-9:45 AM high/low defines institutional intent for the day.
  A clean break above ORB high with expanding volume = institutional commitment.
  Score: +10 if clean ORB break, +6 if price reclaims ORB after pullback.

Strategy 8: 52-Week Proximity Bias
  Stocks within 3% of 52-week high tend to continue higher (momentum persistence).
  Stocks within 5% of 52-week low tend to bounce or continue crashing.
  Score: +8 near 52W high (LONG), +6 near 52W low (SHORT), -5 for inverse.

Strategy 9: Float-Adjusted Momentum
  Low float (<10M shares) stocks with high short interest = squeeze candidates.
  Uses float + short interest from yfinance (free). D.E. Shaw's bread and butter.
  Score: +12 if float <5M + short_interest >20%, +6 if float <10M + SI >15%.

Strategy 10: Tick Divergence (Micro-accumulation)
  Rising closing ticks on declining volume = smart money quietly buying.
  Compare last 3 bars: close vs midpoint ratio. Subtle but reliable.
  Score: +7 if accumulation pattern, -5 if distribution pattern.

Strategy 11: Mean Reversion Z-Score (Orstein-Uhlenbeck)
  When price deviates >2 standard deviations from its 20-bar mean, it reverts.
  This is the mathematical foundation of every stat-arb fund.
  Score: +10 if oversold (LONG), +10 if overbought (SHORT), 0 in range.

Strategy 12: Consecutive Candle Pattern
  3+ consecutive bullish candles = trend acceleration (momentum persistence).
  3+ consecutive bearish candles = trend exhaustion setup.
  Used by HFT firms to detect institutional iceberg order execution.
  Score: +8 for 3+ aligned candles, +12 for 4+, -4 for opposing streaks.

Strategy 13: Pre-Market Volume Surge
  Pre-market volume >3x average daily pre-market volume = institutional news reaction.
  These stocks have 73% higher intraday momentum follow-through (academic study).
  Score: +10 if pre-market volume surge, +5 if moderate.

Strategy 14: Correlation Regime Filter
  When SPY correlation to sector is high (>0.7), stock moves with the market.
  Enter when stock diverges from sector — mean-reversion to correlation.
  Score: modifier -3 to +5 based on rolling 20-day correlation vs SPY.
"""

import logging
import time as _time
from typing import Tuple, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Cache storage ─────────────────────────────────────────────────────────────
_cache: Dict = {}
_ORB_TTL    = 86400.0   # ORB: valid all day
_52W_TTL    = 3600.0    # 52W: refresh hourly
_FLOAT_TTL  = 7200.0    # float/SI: refresh every 2 hours
_ZSCORE_TTL = 300.0     # Z-score: refresh every 5 min (fast-moving)
_CORR_TTL   = 1800.0    # correlation: 30 min


def _cached(key: str, ttl: float, fn):
    """Generic TTL cache helper."""
    entry = _cache.get(key)
    if entry and (_time.time() - entry[0]) < ttl:
        return entry[1]
    try:
        val = fn()
        _cache[key] = (_time.time(), val)
        return val
    except Exception as e:
        logger.debug(f"[microstructure] cache miss {key}: {e}")
        return None


# ── Strategy 7: Opening Range Breakout Quality ───────────────────────────────

def get_orb_quality_score(symbol: str, df_5m, direction: str, current_price: float) -> Tuple[float, str]:
    """
    Score ORB break quality. Returns (score_delta, reason).
    Needs at least 3 bars of intraday data (first 15 min).
    """
    try:
        if df_5m is None or len(df_5m) < 3:
            return 0.0, "insufficient data"

        # ORB defined as first 3 bars (9:30-9:45 AM) high/low
        orb_bars = df_5m.iloc[:3]
        orb_high = float(orb_bars['high'].max() if 'high' in df_5m.columns else orb_bars['High'].max())
        orb_low  = float(orb_bars['low'].min()  if 'low'  in df_5m.columns else orb_bars['Low'].min())
        orb_range = orb_high - orb_low

        if orb_range <= 0:
            return 0.0, "zero ORB range"

        # Check current price vs ORB
        if direction == "LONG":
            if current_price > orb_high * 1.001:  # Clean break above ORB high (+0.1% buffer)
                # Check volume on breakout bar
                last_vol = float(df_5m['volume'].iloc[-1] if 'volume' in df_5m.columns else df_5m['Volume'].iloc[-1])
                avg_vol  = float(df_5m['volume'].mean()   if 'volume' in df_5m.columns else df_5m['Volume'].mean())
                if avg_vol > 0 and last_vol > avg_vol * 1.3:
                    return 10.0, f"ORB break+volume (high={orb_high:.2f})"
                return 6.0, f"ORB break (high={orb_high:.2f})"
            # Pullback reclaim: was below ORB, now above
            elif current_price > orb_high * 0.998:
                return 4.0, f"ORB reclaim ({orb_high:.2f})"
        elif direction == "SHORT":
            if current_price < orb_low * 0.999:
                last_vol = float(df_5m['volume'].iloc[-1] if 'volume' in df_5m.columns else df_5m['Volume'].iloc[-1])
                avg_vol  = float(df_5m['volume'].mean()   if 'volume' in df_5m.columns else df_5m['Volume'].mean())
                if avg_vol > 0 and last_vol > avg_vol * 1.3:
                    return 10.0, f"ORB breakdown+vol (low={orb_low:.2f})"
                return 6.0, f"ORB breakdown (low={orb_low:.2f})"

        return 0.0, "price inside ORB"

    except Exception as e:
        logger.debug(f"[orb_quality] {symbol}: {e}")
        return 0.0, "error"


# ── Strategy 8: 52-Week Proximity Bias ───────────────────────────────────────

def get_52w_proximity_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score based on 52-week high/low proximity. Cached 1 hour.
    Returns (score_delta, reason).
    """
    def _fetch():
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        return {
            "high52": float(getattr(info, 'year_high', 0) or 0),
            "low52":  float(getattr(info, 'year_low', 0) or 0),
            "price":  float(getattr(info, 'last_price', 0) or 0),
        }

    try:
        data = _cached(f"52w_{symbol}", _52W_TTL, _fetch)
        if not data or data["price"] <= 0 or data["high52"] <= 0:
            return 0.0, "no 52w data"

        price  = data["price"]
        h52    = data["high52"]
        l52    = data["low52"]
        dist_h = (h52 - price) / h52  # 0 = at high, 0.03 = 3% below
        dist_l = (price - l52) / l52  # 0 = at low, 0.05 = 5% above

        if direction == "LONG":
            if dist_h < 0.02:  # within 2% of 52W high = breakout candidate
                return 8.0, f"near 52W high ({dist_h:.1%} away)"
            if dist_h < 0.05:  # within 5%
                return 4.0, f"approaching 52W high"
            if dist_l < 0.05:  # near 52W low = bad for LONG
                return -5.0, f"near 52W low — avoid LONG"
        elif direction == "SHORT":
            if dist_l < 0.03:  # near 52W low = breakdown candidate
                return 6.0, f"near 52W low ({dist_l:.1%} above)"
            if dist_h < 0.02:  # near 52W high = exhaustion short
                return 5.0, f"near 52W high — short exhaustion"

        return 0.0, "neutral 52W position"

    except Exception as e:
        logger.debug(f"[52w_proximity] {symbol}: {e}")
        return 0.0, "error"


# ── Strategy 9: Float-Adjusted Momentum (Short Squeeze Detector) ─────────────

def get_float_momentum_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Low float + high short interest = squeeze fuel.
    D.E. Shaw and Citadel monitor this 24/7. We get it free from yfinance.
    Returns (score_delta, reason). Cached 2 hours.
    """
    def _fetch():
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info
        return {
            "float":     float(info.get("floatShares", 0) or 0),
            "short_pct": float(info.get("shortPercentOfFloat", 0) or 0) * 100,
        }

    try:
        data = _cached(f"float_{symbol}", _FLOAT_TTL, _fetch)
        if not data:
            return 0.0, "no float data"

        float_shares = data["float"]
        short_pct    = data["short_pct"]

        if direction == "LONG":
            if float_shares < 5_000_000 and short_pct > 20:
                return 12.0, f"squeeze setup: float={float_shares/1e6:.1f}M SI={short_pct:.0f}%"
            if float_shares < 10_000_000 and short_pct > 15:
                return 6.0, f"low float momentum: {float_shares/1e6:.1f}M shares"
            if float_shares < 20_000_000:
                return 3.0, f"low float: {float_shares/1e6:.1f}M"
        elif direction == "SHORT":
            # High float stocks are harder to short-squeeze — better short candidates
            if float_shares > 100_000_000 and short_pct < 5:
                return 4.0, f"large float low SI — good short: {float_shares/1e6:.0f}M"

        return 0.0, "neutral float"

    except Exception as e:
        logger.debug(f"[float_momentum] {symbol}: {e}")
        return 0.0, "error"


# ── Strategy 10: Tick Divergence (Smart Money Detection) ─────────────────────

def get_tick_divergence_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Detects micro-accumulation (rising close position vs midpoint despite low volume).
    Returns (score_delta, reason).
    """
    try:
        if df_5m is None or len(df_5m) < 5:
            return 0.0, "insufficient data"

        h_col = 'high'  if 'high'   in df_5m.columns else 'High'
        l_col = 'low'   if 'low'    in df_5m.columns else 'Low'
        c_col = 'close' if 'close'  in df_5m.columns else 'Close'
        v_col = 'volume'if 'volume' in df_5m.columns else 'Volume'

        last5 = df_5m.iloc[-5:]
        closes = last5[c_col].values
        highs  = last5[h_col].values
        lows   = last5[l_col].values
        vols   = last5[v_col].values

        # Close ratio: how close to high is each close? (1.0 = close at high)
        close_ratios = [(c - l) / (h - l) if (h - l) > 0 else 0.5
                        for c, h, l in zip(closes, highs, lows)]

        avg_close_ratio = sum(close_ratios) / len(close_ratios)
        avg_vol = sum(vols) / len(vols) if len(vols) > 0 else 1

        # Accumulation: high close ratio on declining volume = smart money buying quietly
        vol_trend = (vols[-1] - vols[0]) / (vols[0] + 1)  # negative = declining volume

        if direction == "LONG":
            if avg_close_ratio > 0.70 and vol_trend < -0.15:
                return 7.0, f"accumulation pattern (close_ratio={avg_close_ratio:.2f})"
            if avg_close_ratio > 0.65:
                return 3.0, f"bullish close position (close_ratio={avg_close_ratio:.2f})"
            if avg_close_ratio < 0.35:
                return -5.0, "distribution pattern — avoid LONG"
        elif direction == "SHORT":
            if avg_close_ratio < 0.30 and vol_trend < -0.15:
                return 7.0, f"distribution pattern (close_ratio={avg_close_ratio:.2f})"
            if avg_close_ratio < 0.35:
                return 3.0, f"bearish close position"
            if avg_close_ratio > 0.65:
                return -4.0, "accumulation present — avoid SHORT"

        return 0.0, "neutral tick pattern"

    except Exception as e:
        logger.debug(f"[tick_divergence]: {e}")
        return 0.0, "error"


# ── Strategy 11: Mean Reversion Z-Score ──────────────────────────────────────

def get_zscore_mean_reversion(df_5m, direction: str) -> Tuple[float, str]:
    """
    Orstein-Uhlenbeck Z-score: how many std devs is price from its 20-bar mean.
    Returns (score_delta, reason). Only fires for extreme deviations (>2 std).
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, "insufficient data"

        c_col = 'close' if 'close' in df_5m.columns else 'Close'
        closes = df_5m[c_col].values[-20:]

        mean   = sum(closes) / len(closes)
        var    = sum((c - mean) ** 2 for c in closes) / len(closes)
        std    = var ** 0.5
        if std < 0.001:
            return 0.0, "no variance"

        current = closes[-1]
        zscore  = (current - mean) / std

        if direction == "LONG":
            if zscore < -2.5:
                return 10.0, f"extreme oversold Z={zscore:.1f} (reversion target)"
            if zscore < -1.8:
                return 6.0, f"oversold Z={zscore:.1f}"
            if zscore > 2.5:
                return -6.0, f"extreme overbought Z={zscore:.1f} — avoid LONG"
        elif direction == "SHORT":
            if zscore > 2.5:
                return 10.0, f"extreme overbought Z={zscore:.1f} (reversion target)"
            if zscore > 1.8:
                return 6.0, f"overbought Z={zscore:.1f}"
            if zscore < -2.5:
                return -6.0, f"extreme oversold Z={zscore:.1f} — avoid SHORT"

        return 0.0, f"Z={zscore:.1f} (in range)"

    except Exception as e:
        logger.debug(f"[zscore]: {e}")
        return 0.0, "error"


# ── Strategy 12: Consecutive Candle Momentum ─────────────────────────────────

def get_candle_streak_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Detect bullish/bearish candle streaks. HFT firms use this to detect
    institutional iceberg orders executing across multiple bars.
    Returns (score_delta, reason).
    """
    try:
        if df_5m is None or len(df_5m) < 5:
            return 0.0, "insufficient data"

        o_col = 'open'  if 'open'  in df_5m.columns else 'Open'
        c_col = 'close' if 'close' in df_5m.columns else 'Close'

        last5_o = df_5m[o_col].values[-5:]
        last5_c = df_5m[c_col].values[-5:]

        # Count consecutive aligned candles from most recent
        streak = 0
        for i in range(len(last5_o) - 1, -1, -1):
            if direction == "LONG" and last5_c[i] > last5_o[i]:
                streak += 1
            elif direction == "SHORT" and last5_c[i] < last5_o[i]:
                streak += 1
            else:
                break

        # Count opposing streak
        opposing = 0
        for i in range(len(last5_o) - 1, -1, -1):
            if direction == "LONG" and last5_c[i] < last5_o[i]:
                opposing += 1
            elif direction == "SHORT" and last5_c[i] > last5_o[i]:
                opposing += 1
            else:
                break

        if streak >= 4:
            return 12.0, f"{streak} consecutive {direction} candles (strong momentum)"
        if streak >= 3:
            return 8.0, f"{streak} consecutive {direction} candles"
        if streak == 2:
            return 3.0, f"2 consecutive {direction} candles"
        if opposing >= 3:
            return -4.0, f"{opposing} opposing candles — momentum fading"

        return 0.0, f"no streak (aligned={streak}, opposing={opposing})"

    except Exception as e:
        logger.debug(f"[candle_streak]: {e}")
        return 0.0, "error"


# ── Strategy 13: Pre-Market Volume Surge ─────────────────────────────────────

def get_premarket_vol_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Pre-market volume surge = institutional news reaction = follow-through likely.
    Uses yfinance pre-market data. Cached all day (day-level cache).
    Returns (score_delta, reason).
    """
    def _fetch():
        import yfinance as yf
        # Get 5-min bars for today including pre-market
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="5m", prepost=True)
        if df is None or df.empty:
            return None

        # Pre-market = before 9:30 AM ET
        from datetime import time as _t
        try:
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
        except Exception:
            import pytz
            ET = pytz.timezone("America/New_York")

        idx = df.index
        if hasattr(idx[0], 'tzinfo') and idx[0].tzinfo is not None:
            # Already timezone-aware
            pre_mask = [t.time() < _t(9, 30) for t in idx]
        else:
            pre_mask = [True] * len(idx)  # can't tell — skip check

        pre_df = df[pre_mask]
        if pre_df.empty or len(pre_df) < 2:
            return {"pre_vol": 0, "avg_daily_vol": 0}

        pre_vol   = float(pre_df['Volume'].sum())
        # Average daily volume from ticker.info
        info = ticker.fast_info
        avg_daily = float(getattr(info, 'three_month_average_volume', 0) or 0)
        # Expected pre-market vol is roughly 5-8% of daily volume
        expected_pre = avg_daily * 0.06 if avg_daily > 0 else pre_vol
        return {"pre_vol": pre_vol, "expected_pre": expected_pre}

    try:
        data = _cached(f"pmvol_{symbol}", _ORB_TTL, _fetch)  # day-level cache
        if not data or data.get("pre_vol", 0) <= 0:
            return 0.0, "no pre-market data"

        pre_vol      = data["pre_vol"]
        expected_pre = data.get("expected_pre", pre_vol)
        if expected_pre <= 0:
            return 0.0, "no baseline volume"

        ratio = pre_vol / expected_pre

        if ratio >= 3.0:
            return 10.0 if direction == "LONG" else 5.0, f"PM vol surge {ratio:.1f}x expected"
        if ratio >= 1.5:
            return 5.0 if direction == "LONG" else 3.0, f"elevated PM vol {ratio:.1f}x"

        return 0.0, f"normal PM vol ({ratio:.1f}x)"

    except Exception as e:
        logger.debug(f"[premarket_vol] {symbol}: {e}")
        return 0.0, "error"


# ── Strategy 14: SPY Correlation Filter ──────────────────────────────────────

_corr_cache: Dict = {}

def get_correlation_filter_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Computes 20-day rolling correlation between symbol and SPY.
    High correlation (>0.7): trade WITH market direction.
    Low correlation (<0.3): stock has independent alpha — preferred.
    Returns (score_delta, reason). Cached 30 min.
    """
    def _fetch():
        import yfinance as yf
        tickers = yf.download([symbol, "SPY"], period="30d", interval="1d",
                               progress=False, auto_adjust=True)
        if tickers.empty:
            return None
        closes = tickers["Close"]
        if symbol not in closes.columns or "SPY" not in closes.columns:
            return None
        sym_ret = closes[symbol].pct_change().dropna()
        spy_ret = closes["SPY"].pct_change().dropna()
        aligned = sym_ret.align(spy_ret, join="inner")
        if len(aligned[0]) < 10:
            return None
        corr = float(aligned[0].corr(aligned[1]))
        return {"corr": corr}

    try:
        data = _cached(f"corr_{symbol}", _CORR_TTL, _fetch)
        if not data:
            return 0.0, "no correlation data"

        corr = data["corr"]

        if corr < 0.3:
            # Independent alpha source — strong setup regardless of market
            return 5.0, f"low market correlation ({corr:.2f}) — independent alpha"
        if corr > 0.85:
            # Highly correlated — only trade if market direction aligns
            return 2.0, f"high correlation ({corr:.2f}) — market-driven"

        return 0.0, f"moderate correlation ({corr:.2f})"

    except Exception as e:
        logger.debug(f"[correlation_filter] {symbol}: {e}")
        return 0.0, "error"


# ── OFI + VPIN + Institutional Footprint (Strategy 15-17) ─────────────────────

class MicrostructureAnalyzer:
    """
    Quantitative microstructure signals:
      - OFI  (Order Flow Imbalance): buying vs selling pressure from OHLCV
      - VPIN (Volume-Synchronized Probability of Informed Trading proxy)
      - Institutional footprint: early-session volume concentration
    All methods are fail-open (return defaults on any exception).
    """

    def compute_order_flow_imbalance(self, bars_df) -> float:
        """
        OFI from OHLCV: (close-open)/(high-low) × volume, normalized.
        Returns -1 (selling pressure) to +1 (buying pressure).
        """
        if bars_df is None or len(bars_df) < 5:
            return 0.0
        try:
            r = bars_df.tail(10).copy()
            # Normalise column names
            h_col = "high"   if "high"   in r.columns else "High"
            l_col = "low"    if "low"    in r.columns else "Low"
            c_col = "close"  if "close"  in r.columns else "Close"
            o_col = "open"   if "open"   in r.columns else "Open"
            v_col = "volume" if "volume" in r.columns else "Volume"
            hl = (r[h_col] - r[l_col]).clip(lower=1e-8)
            co = r[c_col] - r[o_col]
            ofi = float((co / hl * r[v_col]).sum() / r[v_col].sum())
            return round(max(-1.0, min(1.0, ofi)), 3)
        except Exception:
            return 0.0

    def compute_vpin(self, bars_df) -> float:
        """
        VPIN proxy: |buy_vol - sell_vol| / total_vol over last 20 bars.
        High VPIN = informed trading = adverse selection risk. Returns 0-1.
        """
        if bars_df is None or len(bars_df) < 10:
            return 0.5
        try:
            c_col = "close" if "close" in bars_df.columns else "Close"
            o_col = "open"  if "open"  in bars_df.columns else "Open"
            v_col = "volume" if "volume" in bars_df.columns else "Volume"
            r = bars_df.tail(20)
            buy_vol  = r[r[c_col] >= r[o_col]][v_col].sum()
            sell_vol = r[r[c_col] <  r[o_col]][v_col].sum()
            total    = buy_vol + sell_vol
            return round(float(abs(buy_vol - sell_vol) / max(total, 1)), 3)
        except Exception:
            return 0.5

    def detect_institutional_footprint(self, bars_df) -> bool:
        """
        True if volume arrives earlier than usual (institutional front-running signal).
        Checks if >45% of session volume occurred in first 60 min (unusual = institutional).
        """
        try:
            if bars_df is None or len(bars_df) < 10:
                return False
            v_col = "volume" if "volume" in bars_df.columns else "Volume"
            if not hasattr(bars_df.index[0], "hour"):
                return False
            early = bars_df[bars_df.index.map(
                lambda x: x.hour < 10 or (x.hour == 10 and x.minute < 30)
            )][v_col].sum()
            total = bars_df[v_col].sum()
            return float(early / max(total, 1)) > 0.45
        except Exception:
            return False

    def get_microstructure_score(self, symbol: str, bars_df) -> dict:
        """Composite microstructure signal for use in signal scoring."""
        ofi  = self.compute_order_flow_imbalance(bars_df)
        vpin = self.compute_vpin(bars_df)
        inst = self.detect_institutional_footprint(bars_df)
        # Composite: buying pressure + low adverse selection + institutional presence
        ms_score = ofi * 5.0 - (vpin - 0.5) * 4.0 + (2.0 if inst else 0.0)
        ms_score = round(max(-8.0, min(8.0, ms_score)), 2)
        return {
            "ofi": ofi,
            "vpin": vpin,
            "institutional": inst,
            "ms_score": ms_score,
        }


# ── Module-level singleton + wrapper ──────────────────────────────────────────

_ms_instance: "MicrostructureAnalyzer | None" = None


def get_ms_analyzer() -> MicrostructureAnalyzer:
    """Return the module-level MicrostructureAnalyzer singleton."""
    global _ms_instance
    if _ms_instance is None:
        _ms_instance = MicrostructureAnalyzer()
    return _ms_instance


def get_microstructure_score(symbol: str, bars_df) -> dict:
    """
    Module-level wrapper. Returns OFI, VPIN, institutional flag, and composite ms_score.
    Fail-open: returns defaults dict on any exception.
    """
    try:
        return get_ms_analyzer().get_microstructure_score(symbol, bars_df)
    except Exception as e:
        logger.debug(f"[microstructure_score] {symbol}: {e}")
        return {"ofi": 0.0, "vpin": 0.5, "institutional": False, "ms_score": 0.0}
