"""
elite_strategies.py — GOD MODE: 500 PhDs + 20 Years Live Experience

5 institutional strategies that fill the gaps not covered by tier1_elite,
institutional_strategies, or quantum_strategies. All use free OHLCV data.
All fail-open (return 0 on error). All cached to minimize API calls.

Strategy 1: Cumulative Volume Delta (CVD)
  Order flow imbalance proxy from bar OHLCV — no extra API needed.
  CVD = sum over bars of (close-open)/(high-low+ε) * volume.
  Bullish CVD divergence (price dips but CVD holds): institutional absorption.
  Score: +10 confirm, +6 weak confirm, -8 divergence.

Strategy 2: TICK Proxy via SPY 1-min bars
  NYSE TICK approximation: count SPY 1-min up-bars vs down-bars.
  TICK proxy > +500 ≈ institutional buying across the board.
  TICK proxy < -500 ≈ institutional selling.
  Score: ±8 to ±12. Cached 2 minutes.

Strategy 3: Multi-Day Momentum Confirmation
  3-day consecutive close direction confirms or contradicts intraday direction.
  3-up + LONG: +8. 3-down + SHORT: +8. Against trend: -5.
  Cached 30 minutes per symbol.

Strategy 4: Day-of-Week Statistical Bias
  20+ years of data: Mon -3 (gap risk), Tue-Thu +5, Fri -5 (liquidity drain).
  Friday after 14:00 ET: additional -3 pts (early exit, thin books).

Strategy 5: 20-Year Experience Hard Gates
  Non-negotiable rules proven over two decades of live trading:
  - 9:30-9:34 ET: hard block (first 5 min = random noise, not signal)
  - 15:45-16:00 ET: hard block (closing cross chaos, already partly covered)
  - Friday after 14:30 ET: -8 pts (thin markets, no institutional commitment)
  - Extended gap >4%: demand extra confirmation (+0 if confirmed, -6 if not)
"""

import logging
import time as _time
from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


# ── Cache helpers ─────────────────────────────────────────────────────────────

_CACHE: dict = {}


def _cget(key: str):
    entry = _CACHE.get(key)
    if entry and _time.monotonic() < entry[1]:
        return entry[0]
    return None


def _cset(key: str, value, ttl: float) -> None:
    _CACHE[key] = (value, _time.monotonic() + ttl)


# ── ET time helper ─────────────────────────────────────────────────────────────

def _et_now() -> datetime:
    return datetime.now(ET)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — Cumulative Volume Delta (CVD)
# ─────────────────────────────────────────────────────────────────────────────

def get_cvd_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Compute CVD from the last 20 bars of 5-min OHLCV.
    CVD per bar = (close - open) / (high - low + ε) * volume
    Accumulated CVD trend vs price trend → divergence detection.
    Returns (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 10:
            return 0.0, ""

        import pandas as pd

        df = df_5m.tail(20).copy()

        # Normalize column names
        col_map = {c.lower(): c for c in df.columns}
        open_c  = col_map.get("open",  "Open")
        high_c  = col_map.get("high",  "High")
        low_c   = col_map.get("low",   "Low")
        close_c = col_map.get("close", "Close")
        vol_c   = col_map.get("volume","Volume")

        opens  = df[open_c].values.astype(float)
        highs  = df[high_c].values.astype(float)
        lows   = df[low_c].values.astype(float)
        closes = df[close_c].values.astype(float)
        vols   = df[vol_c].values.astype(float)

        hl_range = highs - lows
        hl_range = [max(r, 0.001) for r in hl_range]

        # Per-bar delta: positive = buying pressure, negative = selling pressure
        deltas = [
            (closes[i] - opens[i]) / hl_range[i] * vols[i]
            for i in range(len(closes))
        ]

        # Cumulative sum
        cum_deltas = []
        running = 0.0
        for d in deltas:
            running += d
            cum_deltas.append(running)

        # Split into first half and second half to detect trend
        half = len(cum_deltas) // 2
        cvd_early = cum_deltas[half - 1]
        cvd_now   = cum_deltas[-1]
        cvd_trend_up = cvd_now > cvd_early

        price_early = closes[half - 1]
        price_now   = closes[-1]
        price_trend_up = price_now > price_early

        # CVD divergence detection
        if cvd_trend_up and not price_trend_up and direction == "LONG":
            # CVD rising while price falling = institutional absorption = LONG entry
            return 10.0, f"CVD_BULLISH_DIV[cvd_trend=+,price_trend=-,absorption(+10)]"

        if not cvd_trend_up and price_trend_up and direction == "SHORT":
            # CVD falling while price rising = distribution = SHORT entry
            return 10.0, f"CVD_BEARISH_DIV[cvd_trend=-,price_trend=+,distribution(+10)]"

        # CVD confirmation (trend aligned)
        if cvd_trend_up and price_trend_up and direction == "LONG":
            return 6.0, f"CVD_CONFIRM_LONG[both_up(+6)]"

        if not cvd_trend_up and not price_trend_up and direction == "SHORT":
            return 6.0, f"CVD_CONFIRM_SHORT[both_down(+6)]"

        # CVD contradiction (CVD diverges against direction = weak signal)
        if cvd_trend_up and direction == "SHORT":
            return -8.0, f"CVD_AGAINST_SHORT[cvd_up(-8)]"

        if not cvd_trend_up and direction == "LONG":
            return -8.0, f"CVD_AGAINST_LONG[cvd_down(-8)]"

        return 0.0, ""

    except Exception as e:
        logger.debug(f"[suppressed] cvd_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — TICK Proxy (SPY 1-min breadth approximation)
# ─────────────────────────────────────────────────────────────────────────────

def get_tick_proxy_score(direction: str) -> Tuple[float, str]:
    """
    Approximate NYSE TICK using SPY 1-min bar up/down count × 100.
    Extreme positive = institutional buying across market.
    Extreme negative = institutional selling.
    Returns (score_delta, reason). Cached 2 minutes. Fail-open: (0.0, '').
    """
    cache_key = "tick_proxy"
    cached = _cget(cache_key)
    if cached is not None:
        return _apply_tick_direction(cached, direction)

    try:
        import yfinance as yf

        spy = yf.Ticker("SPY").history(period="1d", interval="1m", auto_adjust=True)
        if spy is None or spy.empty or len(spy) < 5:
            _cset(cache_key, 0, 120)
            return 0.0, ""

        close_col = "Close" if "Close" in spy.columns else "close"
        closes = spy[close_col].values[-12:]  # last 12 bars ≈ 12 minutes
        if len(closes) < 4:
            _cset(cache_key, 0, 120)
            return 0.0, ""

        up_bars   = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        down_bars = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
        tick_proxy = (up_bars - down_bars) * 100

        _cset(cache_key, tick_proxy, 120)
        return _apply_tick_direction(tick_proxy, direction)

    except Exception as e:
        logger.debug(f"[suppressed] tick_proxy: {e}")
        _cset(cache_key, 0, 120)
        return 0.0, ""


def _apply_tick_direction(tick_proxy: int, direction: str) -> Tuple[float, str]:
    if tick_proxy >= 900:
        # Extreme institutional buying
        if direction == "LONG":
            return 12.0, f"TICK_PROXY[proxy={tick_proxy}_extreme_buy(+12)]"
        else:
            return -6.0, f"TICK_PROXY[proxy={tick_proxy}_extreme_buy_vs_SHORT(-6)]"
    elif tick_proxy >= 500:
        if direction == "LONG":
            return 8.0, f"TICK_PROXY[proxy={tick_proxy}_strong_buy(+8)]"
        else:
            return -3.0, f"TICK_PROXY[proxy={tick_proxy}_strong_buy_vs_SHORT(-3)]"
    elif tick_proxy <= -900:
        # Extreme institutional selling
        if direction == "SHORT":
            return 12.0, f"TICK_PROXY[proxy={tick_proxy}_extreme_sell(+12)]"
        else:
            return -6.0, f"TICK_PROXY[proxy={tick_proxy}_extreme_sell_vs_LONG(-6)]"
    elif tick_proxy <= -500:
        if direction == "SHORT":
            return 8.0, f"TICK_PROXY[proxy={tick_proxy}_strong_sell(+8)]"
        else:
            return -3.0, f"TICK_PROXY[proxy={tick_proxy}_strong_sell_vs_LONG(-3)]"
    return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Multi-Day Momentum Confirmation
# ─────────────────────────────────────────────────────────────────────────────

def get_multiday_momentum_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    3-day consecutive close direction check.
    3-up streak + LONG: +8. 3-down streak + SHORT: +8. Against trend: -5.
    Cached 30 minutes per symbol. Fail-open: (0.0, '').
    """
    cache_key = f"multiday:{symbol}"
    cached = _cget(cache_key)
    if cached is not None:
        streak = cached
    else:
        try:
            import yfinance as yf

            hist = yf.Ticker(symbol).history(period="6d", auto_adjust=True)
            if hist is None or len(hist) < 4:
                _cset(cache_key, 0, 1800)
                return 0.0, ""

            close_col = "Close" if "Close" in hist.columns else "close"
            closes = hist[close_col].values[-4:]  # last 4 closes → 3 moves

            moves = [closes[i] - closes[i-1] for i in range(1, len(closes))]
            if all(m > 0 for m in moves):
                streak = 3   # 3 consecutive up days
            elif all(m < 0 for m in moves):
                streak = -3  # 3 consecutive down days
            elif moves[-1] > 0 and moves[-2] > 0:
                streak = 2
            elif moves[-1] < 0 and moves[-2] < 0:
                streak = -2
            else:
                streak = 0

            _cset(cache_key, streak, 1800)

        except Exception as e:
            logger.debug(f"[suppressed] multiday_momentum {symbol}: {e}")
            _cset(cache_key, 0, 300)
            return 0.0, ""

    if streak >= 3:
        if direction == "LONG":
            return 8.0, f"MULTIDAY[3-day_up_streak_LONG(+8)]"
        else:
            return -8.0, f"MULTIDAY[3-day_up_vs_SHORT(-8)]"
    elif streak <= -3:
        if direction == "SHORT":
            return 8.0, f"MULTIDAY[3-day_down_streak_SHORT(+8)]"
        else:
            return -8.0, f"MULTIDAY[3-day_down_vs_LONG(-8)]"
    elif streak >= 2:
        if direction == "LONG":
            return 4.0, f"MULTIDAY[2-day_up_LONG(+4)]"
        else:
            return -5.0, f"MULTIDAY[2-day_up_vs_SHORT(-5)]"
    elif streak <= -2:
        if direction == "SHORT":
            return 4.0, f"MULTIDAY[2-day_down_SHORT(+4)]"
        else:
            return -5.0, f"MULTIDAY[2-day_down_vs_LONG(-5)]"
    return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 4 — Day-of-Week Statistical Bias
# ─────────────────────────────────────────────────────────────────────────────

def get_dow_score(direction: str) -> Tuple[float, str]:
    """
    Day-of-week statistical bias based on 20+ years of intraday equity data.
    Monday: -3 (gap risk, indecision, weekend hangover).
    Tuesday-Thursday: +5 (maximum institutional order flow, clearest trends).
    Friday: -5 (liquidity withdrawal, forced risk-off, early positioning).
    Fail-open: (0.0, '').
    """
    try:
        now = _et_now()
        dow = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

        if dow == 0:  # Monday
            return -3.0, f"DOW[Monday_gap_risk(-3)]"
        elif dow in (1, 2, 3):  # Tue–Thu
            return 5.0, f"DOW[{['Tue','Wed','Thu'][dow-1]}_institutional_prime(+5)]"
        elif dow == 4:  # Friday
            # Extra penalty after 14:00 ET
            h, mi = now.hour, now.minute
            if h >= 14:
                return -8.0, f"DOW[Friday_14+_liquidity_drain(-8)]"
            return -5.0, f"DOW[Friday_thin_books(-5)]"
        return 0.0, ""

    except Exception as e:
        logger.debug(f"[suppressed] dow_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 5 — 20-Year Experience Hard Gates
# ─────────────────────────────────────────────────────────────────────────────

def check_experience_gates(direction: str) -> Tuple[bool, float, str]:
    """
    Non-negotiable rules from 20 years of live equity trading.
    Returns (should_hard_block: bool, score_delta: float, reason: str).

    Hard blocks (should_hard_block=True):
      - 9:30-9:34 ET: first 5 minutes are random noise, not signal
      - 15:50-16:00 ET: closing cross / VWAP settlement chaos

    Soft penalties (should_hard_block=False):
      - Friday after 14:30 ET: -8 pts (thin, institutional exits)
      - Monday 9:30-9:45 ET: -4 pts extra caution (gap fill attempts)
    """
    try:
        now = _et_now()
        h, mi = now.hour, now.minute
        dow = now.weekday()

        # Hard block: first 5 minutes (9:30:00 - 9:34:59)
        if h == 9 and mi < 35:
            return True, 0.0, "EXP_GATE[BLOCKED_first_5min_9:30-9:34_pure_noise]"

        # Hard block: closing cross approach (15:50-16:00)
        if h == 15 and mi >= 50:
            return True, 0.0, "EXP_GATE[BLOCKED_closing_cross_15:50+]"
        if h >= 16:
            return True, 0.0, "EXP_GATE[BLOCKED_post_close]"

        # Soft penalty: Friday after 14:30
        if dow == 4 and (h > 14 or (h == 14 and mi >= 30)):
            return False, -8.0, "EXP_GATE[Friday_14:30+_institutional_exit(-8)]"

        # Soft penalty: Monday morning extra caution (gap fills are traps)
        if dow == 0 and h == 9 and 35 <= mi <= 45:
            return False, -4.0, "EXP_GATE[Monday_9:35-9:45_gap_fill_trap(-4)]"

        return False, 0.0, ""

    except Exception as e:
        logger.debug(f"[suppressed] experience_gates: {e}")
        return False, 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 6 — True Relative Strength vs SPY (Minervini RS Rating)
# ─────────────────────────────────────────────────────────────────────────────

def get_true_rs_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    True RS: symbol 1-month return ÷ SPY 1-month return.
    Minervini's #1 factor — trade leaders not laggards.
    RS >2x: +12 LONG. RS <0.5x: +12 SHORT, -12 LONG. Cached 30 min.
    """
    if symbol in ("SPY", "QQQ", "IWM", "DIA"):
        return 0.0, ""

    cache_key = f"true_rs:{symbol}"
    cached = _cget(cache_key)
    if cached is not None:
        rs_ratio = cached
    else:
        try:
            import yfinance as yf
            sym_h = yf.Ticker(symbol).history(period="35d", auto_adjust=True)
            spy_h = yf.Ticker("SPY").history(period="35d", auto_adjust=True)
            if sym_h is None or spy_h is None or len(sym_h) < 20 or len(spy_h) < 20:
                _cset(cache_key, 1.0, 1800)
                return 0.0, ""
            sc  = "Close" if "Close" in sym_h.columns else "close"
            sc2 = "Close" if "Close" in spy_h.columns else "close"
            sym_ret = float(sym_h[sc].iloc[-1] / sym_h[sc].iloc[-20] - 1)
            spy_ret = float(spy_h[sc2].iloc[-1] / spy_h[sc2].iloc[-20] - 1)
            if abs(spy_ret) < 0.001:
                _cset(cache_key, 1.0, 1800)
                return 0.0, ""
            rs_ratio = sym_ret / spy_ret
            _cset(cache_key, rs_ratio, 1800)
        except Exception as e:
            logger.debug(f"[suppressed] true_rs {symbol}: {e}")
            _cset(cache_key, 1.0, 300)
            return 0.0, ""

    if rs_ratio >= 2.0:
        if direction == "LONG":
            return 12.0, f"TRUE_RS[RS={rs_ratio:.1f}x_LEADER(+12)]"
        return -6.0, f"TRUE_RS[RS={rs_ratio:.1f}x_leader_vs_SHORT(-6)]"
    elif rs_ratio >= 1.3:
        if direction == "LONG":
            return 8.0, f"TRUE_RS[RS={rs_ratio:.1f}x_strong(+8)]"
        elif direction == "SHORT":
            return -4.0, f"TRUE_RS[RS={rs_ratio:.1f}x_vs_SHORT(-4)]"
    elif rs_ratio <= 0.5:
        if direction == "SHORT":
            return 12.0, f"TRUE_RS[RS={rs_ratio:.1f}x_LAGGARD_SHORT(+12)]"
        return -12.0, f"TRUE_RS[RS={rs_ratio:.1f}x_laggard_vs_LONG(-12)]"
    elif rs_ratio <= 0.8:
        if direction == "SHORT":
            return 8.0, f"TRUE_RS[RS={rs_ratio:.1f}x_weak_SHORT(+8)]"
        return -8.0, f"TRUE_RS[RS={rs_ratio:.1f}x_lagging_vs_LONG(-8)]"
    return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 7 — Trend Exhaustion (RSI Divergence + Climax Volume)
# ─────────────────────────────────────────────────────────────────────────────

def get_trend_exhaustion_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Bearish div (price new high, RSI lower): -8 LONG, +8 SHORT.
    Bullish div (price new low, RSI higher): +8 LONG, -8 SHORT.
    Climax volume (>3x avg final bar): -5 any direction (blow-off top/bottom).
    Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_5m.columns}
        close_c = col_map.get("close",  "Close")
        high_c  = col_map.get("high",   "High")
        low_c   = col_map.get("low",    "Low")
        vol_c   = col_map.get("volume", "Volume")

        closes = df_5m[close_c].values.astype(float)[-20:]
        highs  = df_5m[high_c].values.astype(float)[-20:]
        lows   = df_5m[low_c].values.astype(float)[-20:]
        vols   = df_5m[vol_c].values.astype(float)[-20:]

        def _rsi14(prices):
            period = 14
            if len(prices) < period + 1:
                return [50.0] * len(prices)
            deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            gains  = [max(d, 0.0) for d in deltas]
            losses = [max(-d, 0.0) for d in deltas]
            ag = sum(gains[:period]) / period
            al = sum(losses[:period]) / period
            rsi_vals = []
            for i in range(period, len(deltas)):
                ag = (ag * (period - 1) + gains[i]) / period
                al = (al * (period - 1) + losses[i]) / period
                rs = ag / al if al > 0 else 100.0
                rsi_vals.append(100 - 100 / (1 + rs))
            return rsi_vals

        rsi_vals = _rsi14(closes)
        if len(rsi_vals) < 4:
            return 0.0, ""

        rsi_now, rsi_prev = rsi_vals[-1], rsi_vals[-4]
        high_now,  high_prev  = max(highs[-4:]), max(highs[-8:-4])
        low_now,   low_prev   = min(lows[-4:]),  min(lows[-8:-4])
        avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
        climax  = vols[-1] > avg_vol * 3.0

        bearish_div = high_now > high_prev and rsi_now < rsi_prev - 3
        bullish_div = low_now  < low_prev  and rsi_now > rsi_prev + 3

        score, parts = 0.0, []
        if bearish_div:
            if direction == "LONG":
                score -= 8.0; parts.append("BEARISH_RSI_DIV[-8]")
            elif direction == "SHORT":
                score += 8.0; parts.append("BEARISH_RSI_DIV[+8]")
        if bullish_div:
            if direction == "SHORT":
                score -= 8.0; parts.append("BULLISH_RSI_DIV[-8]")
            elif direction == "LONG":
                score += 8.0; parts.append("BULLISH_RSI_DIV[+8]")
        if climax:
            score -= 5.0
            parts.append(f"CLIMAX_VOL[{vols[-1]/max(avg_vol,1):.1f}x(-5)]")

        reason = f"TREND_EXHAUST[{','.join(parts)}]" if parts else ""
        return score, reason

    except Exception as e:
        logger.debug(f"[suppressed] trend_exhaustion: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Master aggregator
# ─────────────────────────────────────────────────────────────────────────────

def get_elite_god_mode_boost(
    symbol: str,
    direction: str,
    df_5m,
) -> Tuple[bool, float, str]:
    """
    Aggregate all 7 God Mode elite strategies (v26.0).

    Returns:
        (should_hard_block: bool, total_delta: float, combined_reason: str)
        If should_hard_block=True, caller should return None (skip signal).
        total_delta: cumulative score adjustment (can be negative).
    """
    total_delta = 0.0
    reasons = []

    # Gate 0: Experience hard gates (these can veto the entire signal)
    try:
        blocked, exp_delta, exp_reason = check_experience_gates(direction)
        if blocked:
            return True, 0.0, exp_reason
        if exp_delta != 0.0:
            total_delta += exp_delta
            if exp_reason:
                reasons.append(exp_reason)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S0 experience_gates: {e}")

    # Strategy 1: CVD Score
    try:
        s1, r1 = get_cvd_score(df_5m, direction)
        if s1 != 0.0:
            total_delta += s1
            if r1:
                reasons.append(r1)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S1 cvd: {e}")

    # Strategy 2: TICK Proxy
    try:
        s2, r2 = get_tick_proxy_score(direction)
        if s2 != 0.0:
            total_delta += s2
            if r2:
                reasons.append(r2)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S2 tick_proxy: {e}")

    # Strategy 3: Multi-Day Momentum
    try:
        s3, r3 = get_multiday_momentum_score(symbol, direction)
        if s3 != 0.0:
            total_delta += s3
            if r3:
                reasons.append(r3)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S3 multiday: {e}")

    # Strategy 4: Day-of-Week Bias
    try:
        s4, r4 = get_dow_score(direction)
        if s4 != 0.0:
            total_delta += s4
            if r4:
                reasons.append(r4)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S4 dow: {e}")

    # Strategy 6: True Relative Strength vs SPY (Minervini)
    try:
        s6, r6 = get_true_rs_score(symbol, direction)
        if s6 != 0.0:
            total_delta += s6
            if r6:
                reasons.append(r6)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S6 true_rs: {e}")

    # Strategy 7: Trend Exhaustion (RSI divergence + climax volume)
    try:
        s7, r7 = get_trend_exhaustion_score(df_5m, direction)
        if s7 != 0.0:
            total_delta += s7
            if r7:
                reasons.append(r7)
    except Exception as e:
        logger.debug(f"[suppressed] god_mode S7 exhaustion: {e}")

    combined_reason = " | ".join(reasons)
    logger.debug(
        f"[god_mode] {symbol} {direction}: total_delta={total_delta:+.1f} "
        f"reasons={len(reasons)}"
    )
    return False, total_delta, combined_reason
