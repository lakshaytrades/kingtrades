"""
tier1_elite.py — Tier 1/2 Elite Quantitative Strategies
Implements 6 institutional-grade strategies used by Renaissance/Two Sigma/Citadel/Goldman/DE Shaw.
All use free data via yfinance. All fail-open (return 0 on error). All cached to avoid repeated API calls.
"""

import logging
import time as _time
from typing import Tuple, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache infrastructure — all caches use _time.monotonic() for TTL
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def _cache_get(key: str) -> object:
    """Return cached value if still fresh, else None."""
    entry = _CACHE.get(key)
    if entry is None:
        return None
    value, expiry = entry
    if _time.monotonic() < expiry:
        return value
    return None


def _cache_set(key: str, value: object, ttl_seconds: float) -> None:
    """Store value in cache with TTL in seconds."""
    _CACHE[key] = (value, _time.monotonic() + ttl_seconds)


# ---------------------------------------------------------------------------
# Strategy 1: Multi-Factor Score (Two Sigma style)
# Factors: momentum (20d return rank), quality (ROE+EPS), low-vol (beta),
#          value (fwd P/E). Total possible range: +3 to +12.
# ---------------------------------------------------------------------------

def get_multifactor_score(symbol: str, watchlist: list) -> Tuple[float, str]:
    """
    Two Sigma-style multi-factor score.
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    Cache TTL: 60 minutes per symbol.
    """
    cache_key = f"multifactor:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        score = 0.0
        reasons = []

        # --- Momentum factor: 20-day return rank vs peers ---
        try:
            hist = ticker.history(period="30d", auto_adjust=True)
            if hist is not None and len(hist) >= 20:
                close = hist["Close"]
                ret_20d = float((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20])
                # Compare vs peers in watchlist
                peer_returns = []
                for peer in (watchlist or [])[:10]:
                    if peer == symbol:
                        continue
                    try:
                        ph = yf.Ticker(peer).history(period="30d", auto_adjust=True)
                        if ph is not None and len(ph) >= 20:
                            pc = ph["Close"]
                            peer_returns.append(float((pc.iloc[-1] - pc.iloc[-20]) / pc.iloc[-20]))
                    except Exception:
                        pass
                if peer_returns:
                    rank = sum(1 for r in peer_returns if ret_20d > r) / len(peer_returns)
                    if rank >= 0.8:
                        score += 3.0
                        reasons.append(f"momentum_rank_top20pct(+3)")
                    elif rank >= 0.6:
                        score += 1.5
                        reasons.append(f"momentum_rank_top40pct(+1.5)")
                else:
                    # No peers — use absolute threshold
                    if ret_20d > 0.05:
                        score += 2.0
                        reasons.append(f"momentum_20d+{ret_20d*100:.1f}%(+2)")
                    elif ret_20d > 0.02:
                        score += 1.0
                        reasons.append(f"momentum_20d+{ret_20d*100:.1f}%(+1)")
        except Exception as _me:
            logger.debug(f"[multifactor] momentum error for {symbol}: {_me}")

        # --- Quality factor: ROE > 15% AND positive EPS growth ---
        try:
            roe = info.get("returnOnEquity")
            eps = info.get("trailingEps")
            rev_growth = info.get("revenueGrowth")
            quality_score = 0.0
            if roe is not None and roe > 0.15:
                quality_score += 1.5
            if eps is not None and eps > 0:
                quality_score += 1.0
            if rev_growth is not None and rev_growth > 0.05:
                quality_score += 0.5
            if quality_score > 0:
                score += quality_score
                reasons.append(
                    f"quality(ROE={roe*100:.1f}%,EPS={eps})(+{quality_score:.1f})"
                    if roe is not None and eps is not None
                    else f"quality(+{quality_score:.1f})"
                )
        except Exception as _qe:
            logger.debug(f"[multifactor] quality error for {symbol}: {_qe}")

        # --- Low-vol factor: beta < 1.2 earns bonus ---
        try:
            beta = info.get("beta")
            if beta is not None and beta < 1.2:
                score += 1.5
                reasons.append(f"low_vol_beta={beta:.2f}(+1.5)")
        except Exception as _ve:
            logger.debug(f"[multifactor] low-vol error for {symbol}: {_ve}")

        # --- Value factor: forward P/E < 25 earns bonus ---
        try:
            fwd_pe = info.get("forwardPE") or info.get("trailingPE")
            if fwd_pe is not None and 0 < fwd_pe < 25:
                score += 1.5
                reasons.append(f"value_fwdPE={fwd_pe:.1f}(+1.5)")
            elif fwd_pe is not None and 0 < fwd_pe < 35:
                score += 0.5
                reasons.append(f"value_fwdPE={fwd_pe:.1f}(+0.5)")
        except Exception as _vale:
            logger.debug(f"[multifactor] value error for {symbol}: {_vale}")

        score = min(12.0, max(0.0, score))
        reason_str = f"MULTIFACTOR[{','.join(reasons)}]" if reasons else ""
        result = (score, reason_str)
        _cache_set(cache_key, result, 3600)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] multifactor for {symbol}: {e}")
        _cache_set(cache_key, (0.0, ""), 300)
        return (0.0, "")


# ---------------------------------------------------------------------------
# Strategy 2: Options Flow Sentiment (Citadel style)
# Put/call ratio + unusual activity detection for near-the-money strikes.
# Cached 30 min per symbol.
# ---------------------------------------------------------------------------

def get_options_flow_sentiment(symbol: str, direction: str, ltp: float) -> Tuple[float, str]:
    """
    Citadel-style options flow sentiment.
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    Bullish unusual call flow: +8. Bearish unusual put flow: +8 (for SHORT direction).
    Cache TTL: 30 minutes per symbol.
    """
    cache_key = f"options_flow:{symbol}:{direction}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            result = (0.0, "")
            _cache_set(cache_key, result, 1800)
            return result

        # Nearest expiry
        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
        calls = chain.calls
        puts = chain.puts

        if calls is None or puts is None or calls.empty or puts.empty:
            result = (0.0, "")
            _cache_set(cache_key, result, 1800)
            return result

        # Near-the-money: strikes within 5% of ltp
        ref_price = ltp if ltp and ltp > 0 else None
        if ref_price is None:
            all_strikes = list(calls["strike"].dropna())
            ref_price = all_strikes[len(all_strikes) // 2] if all_strikes else 100.0

        ntm_low = ref_price * 0.95
        ntm_high = ref_price * 1.05

        calls_ntm = calls[(calls["strike"] >= ntm_low) & (calls["strike"] <= ntm_high)]
        puts_ntm = puts[(puts["strike"] >= ntm_low) & (puts["strike"] <= ntm_high)]

        call_vol = float(calls_ntm["volume"].fillna(0).sum()) if not calls_ntm.empty else 0.0
        put_vol = float(puts_ntm["volume"].fillna(0).sum()) if not puts_ntm.empty else 0.0

        # Average volumes across all strikes (baseline)
        avg_call_vol = float(calls["volume"].fillna(0).mean()) if not calls.empty else 1.0
        avg_put_vol = float(puts["volume"].fillna(0).mean()) if not puts.empty else 1.0
        avg_call_vol = max(avg_call_vol, 1.0)
        avg_put_vol = max(avg_put_vol, 1.0)

        score = 0.0
        reasons = []

        # Unusual call activity (>3x avg) → bullish signal
        if call_vol > avg_call_vol * 3.0:
            if direction == "LONG":
                score += 8.0
                reasons.append(f"unusual_calls_vol={call_vol:.0f}vs_avg={avg_call_vol:.0f}(+8)")
            else:
                score -= 4.0
                reasons.append(f"unusual_calls_bearish_for_SHORT(-4)")

        # Unusual put activity (>3x avg) → bearish signal
        if put_vol > avg_put_vol * 3.0:
            if direction == "SHORT":
                score += 8.0
                reasons.append(f"unusual_puts_vol={put_vol:.0f}vs_avg={avg_put_vol:.0f}(+8)")
            else:
                score -= 4.0
                reasons.append(f"unusual_puts_bearish_for_LONG(-4)")

        # Put/call ratio context
        if call_vol > 0 and put_vol > 0:
            pc_ratio = put_vol / call_vol
            if pc_ratio < 0.5 and direction == "LONG":
                score += 2.0
                reasons.append(f"PCR={pc_ratio:.2f}_bullish(+2)")
            elif pc_ratio > 2.0 and direction == "SHORT":
                score += 2.0
                reasons.append(f"PCR={pc_ratio:.2f}_bearish(+2)")

        reason_str = f"OPTIONS_FLOW[{','.join(reasons)}]" if reasons else ""
        result = (score, reason_str)
        _cache_set(cache_key, result, 1800)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] options_flow for {symbol}: {e}")
        _cache_set(cache_key, (0.0, ""), 300)
        return (0.0, "")


# ---------------------------------------------------------------------------
# Strategy 3: VIX Term Structure (Goldman macro style)
# VIX/VXV ratio signals contango (calm) vs backwardation (fear).
# Cached 15 min (global).
# ---------------------------------------------------------------------------

def get_vix_term_structure() -> Tuple[float, str]:
    """
    Goldman macro-style VIX term structure.
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    VIX/VXV < 0.9 → contango, calm market → +5 (LONG bias).
    VIX/VXV > 1.1 → backwardation, fear → -5 (LONG penalty; +5 for SHORT when direction-adjusted).
    Cache TTL: 15 minutes.
    """
    cache_key = "vix_term_structure"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        import yfinance as yf

        vix_hist = yf.Ticker("^VIX").history(period="2d", auto_adjust=True)
        vxv_hist = yf.Ticker("^VXV").history(period="2d", auto_adjust=True)

        if vix_hist is None or vxv_hist is None or vix_hist.empty or vxv_hist.empty:
            result = (0.0, "")
            _cache_set(cache_key, result, 900)
            return result

        vix_val = float(vix_hist["Close"].iloc[-1])
        vxv_val = float(vxv_hist["Close"].iloc[-1])

        if vxv_val <= 0:
            result = (0.0, "")
            _cache_set(cache_key, result, 900)
            return result

        ratio = vix_val / vxv_val

        if ratio < 0.9:
            # Contango: calm market, long bias
            score = 5.0
            reason = f"VIX_TERM_STRUCTURE[contango_ratio={ratio:.3f}_LONG_bias(+5)]"
        elif ratio > 1.1:
            # Backwardation: fear, penalise LONG (caller applies direction flip for SHORT)
            score = -5.0
            reason = f"VIX_TERM_STRUCTURE[backwardation_ratio={ratio:.3f}_LONG_penalty(-5)]"
        else:
            score = 0.0
            reason = f"VIX_TERM_STRUCTURE[neutral_ratio={ratio:.3f}(0)]"

        result = (score, reason)
        _cache_set(cache_key, result, 900)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] vix_term_structure: {e}")
        _cache_set("vix_term_structure", (0.0, ""), 300)
        return (0.0, "")


def _apply_vix_term_structure_to_direction(direction: str) -> Tuple[float, str]:
    """
    Apply VIX term structure score with direction awareness.
    Backwardation: +5 for SHORT, -5 for LONG. Contango: +5 for LONG only.
    """
    raw_score, raw_reason = get_vix_term_structure()
    if raw_score == 0.0:
        return (0.0, raw_reason)

    if raw_score > 0:
        # Contango — LONG bias
        if direction == "LONG":
            return (5.0, raw_reason)
        else:
            return (0.0, raw_reason)
    else:
        # Backwardation — SHORT bias, LONG penalty
        if direction == "SHORT":
            return (5.0, raw_reason.replace("LONG_penalty(-5)", "SHORT_bias(+5)"))
        else:
            return (-5.0, raw_reason)


# ---------------------------------------------------------------------------
# Strategy 4: Yield Curve Signal (macro hedge fund style)
# ^TNX (10Y) vs ^IRX (3M): spread guides risk-on/off bias.
# Cached 30 min (global).
# ---------------------------------------------------------------------------

def get_yield_curve_signal(direction: str) -> Tuple[float, str]:
    """
    Macro hedge fund-style yield curve signal.
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    Spread > 1.5%: normal, risk-on → +4 if trade aligns.
    Spread < 0%: inverted (risk-off) → -4 LONG / +4 SHORT.
    Cache TTL: 30 minutes (raw spread).
    """
    cache_key = "yield_curve_raw"
    raw_cached = _cache_get(cache_key)

    if raw_cached is not None:
        spread = raw_cached
    else:
        try:
            import yfinance as yf

            tnx = yf.Ticker("^TNX").history(period="2d", auto_adjust=True)
            irx = yf.Ticker("^IRX").history(period="2d", auto_adjust=True)

            if tnx is None or irx is None or tnx.empty or irx.empty:
                return (0.0, "")

            ten_year = float(tnx["Close"].iloc[-1])
            three_month = float(irx["Close"].iloc[-1])
            spread = ten_year - three_month
            _cache_set(cache_key, spread, 1800)
        except Exception as e:
            logger.debug(f"[suppressed] yield_curve raw fetch: {e}")
            return (0.0, "")

    try:
        if spread > 1.5:
            # Normal yield curve: risk-on environment — reward any directional trade
            return (4.0, f"YIELD_CURVE[spread={spread:.2f}%_normal_risk_on(+4)]")
        elif spread < 0:
            # Inverted: risk-off environment
            if direction == "LONG":
                return (-4.0, f"YIELD_CURVE[spread={spread:.2f}%_inverted_LONG_penalty(-4)]")
            elif direction == "SHORT":
                return (4.0, f"YIELD_CURVE[spread={spread:.2f}%_inverted_SHORT_boost(+4)]")
        else:
            # Flat (0 to 1.5): mild caution
            if direction == "SHORT":
                return (1.0, f"YIELD_CURVE[spread={spread:.2f}%_flat_SHORT(+1)]")
        return (0.0, "")
    except Exception as e:
        logger.debug(f"[suppressed] yield_curve apply: {e}")
        return (0.0, "")


# ---------------------------------------------------------------------------
# Strategy 5: Statistical Arbitrage Extended (DE Shaw style)
# 20 pairs across 8 sectors. Z-score based convergence signal.
# Cached 45 min per pair (global).
# ---------------------------------------------------------------------------

# 20 pairs across 8 sectors
_STAT_ARB_PAIRS = [
    # Tech
    ("NVDA", "AMD"),
    ("AAPL", "MSFT"),
    ("META", "GOOGL"),
    ("CRM", "NOW"),
    # Finance
    ("GS", "JPM"),
    ("BAC", "WFC"),
    # Energy
    ("XOM", "CVX"),
    ("COP", "OXY"),
    # Healthcare
    ("JNJ", "PFE"),
    ("MRNA", "BNTX"),
    # Consumer
    ("AMZN", "WMT"),
    ("COST", "TGT"),
    # Semiconductors
    ("TSM", "INTC"),
    ("QCOM", "AVGO"),
    # Crypto proxy
    ("COIN", "MSTR"),
    ("RIOT", "MARA"),
    # Transport
    ("UAL", "DAL"),
    # Additional pairs to reach 20
    ("ORCL", "SAP"),
    ("UBER", "LYFT"),
    ("NFLX", "DIS"),
]


def _compute_stat_arb_zscore(sym_a: str, sym_b: str) -> float:
    """
    Compute 30-day normalised spread Z-score between sym_a and sym_b.
    Returns Z-score or 0.0 on error.
    """
    try:
        import yfinance as yf
        import math
        import statistics

        data_a = yf.Ticker(sym_a).history(period="35d", auto_adjust=True)
        data_b = yf.Ticker(sym_b).history(period="35d", auto_adjust=True)

        if data_a is None or data_b is None or data_a.empty or data_b.empty:
            return 0.0

        # Align on common dates
        idx_a = set(data_a.index.date)
        idx_b = set(data_b.index.date)
        common = sorted(idx_a & idx_b)
        if len(common) < 20:
            return 0.0

        closes_a = [float(data_a[data_a.index.date == d]["Close"].iloc[-1]) for d in common]
        closes_b = [float(data_b[data_b.index.date == d]["Close"].iloc[-1]) for d in common]

        # Normalised spread: log(a) - log(b)
        spreads = [
            math.log(ca) - math.log(cb)
            for ca, cb in zip(closes_a, closes_b)
            if ca > 0 and cb > 0
        ]
        if len(spreads) < 20:
            return 0.0

        recent = spreads[-30:]
        mean_s = statistics.mean(recent)
        stdev_s = statistics.stdev(recent)
        if stdev_s < 1e-10:
            return 0.0

        current_spread = spreads[-1]
        zscore = (current_spread - mean_s) / stdev_s
        return zscore

    except Exception as e:
        logger.debug(f"[stat_arb_zscore] {sym_a}/{sym_b}: {e}")
        return 0.0


def get_stat_arb_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    DE Shaw-style statistical arbitrage extended signal.
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    |Z| > 2.0 and trade direction aligns with convergence → +8.
    Cache TTL: 45 minutes per pair.
    """
    best_score = 0.0
    best_reason = ""

    for sym_a, sym_b in _STAT_ARB_PAIRS:
        if symbol not in (sym_a, sym_b):
            continue

        pair_key = f"stat_arb:{sym_a}:{sym_b}"
        cached = _cache_get(pair_key)
        if cached is not None:
            zscore = cached
        else:
            zscore = _compute_stat_arb_zscore(sym_a, sym_b)
            _cache_set(pair_key, zscore, 2700)

        if abs(zscore) <= 2.0:
            continue

        # Determine convergence direction for symbol:
        # sym_a Z > +2 → sym_a overpriced vs sym_b → SHORT sym_a for convergence
        # sym_a Z < -2 → sym_a underpriced vs sym_b → LONG sym_a for convergence
        if symbol == sym_a:
            convergence_dir = "SHORT" if zscore > 2.0 else "LONG"
        else:
            convergence_dir = "LONG" if zscore > 2.0 else "SHORT"

        if direction == convergence_dir:
            score = 8.0
            reason = (
                f"STAT_ARB[{sym_a}/{sym_b}_Z={zscore:.2f}"
                f"_convergence_{convergence_dir}(+8)]"
            )
            if score > best_score:
                best_score = score
                best_reason = reason

    return (best_score, best_reason)


# ---------------------------------------------------------------------------
# Strategy 6: Pre-Market Gap Continuation (universal elite strategy)
# Uses yfinance preMarketPrice vs prev_close to detect gap-up/down.
# Cached 5 min per symbol. Gap continuation only (no fades).
# ---------------------------------------------------------------------------

def get_premarket_gap_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Pre-market gap continuation signal (continuation only, no fade trades).
    Returns (score_delta, reason_string). Fail-open returns (0, '').
    Gap up >2% LONG → +10; >5% LONG → +15.
    Gap down >2% SHORT → +10; >5% SHORT → +15.
    Cache TTL: 5 minutes per symbol.
    """
    cache_key = f"premarket_gap:{symbol}:{direction}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        premarket_price = info.get("preMarketPrice")
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

        if not premarket_price or not prev_close or prev_close <= 0:
            result = (0.0, "")
            _cache_set(cache_key, result, 300)
            return result

        gap_pct = (premarket_price - prev_close) / prev_close * 100.0

        score = 0.0
        reason = ""

        if gap_pct > 5.0:
            if direction == "LONG":
                score = 15.0
                reason = f"PREMARKET_GAP[gap_up={gap_pct:.1f}%_HIGH_CONVICTION_LONG(+15)]"
            else:
                score = -5.0
                reason = f"PREMARKET_GAP[gap_up={gap_pct:.1f}%_against_SHORT(-5)]"
        elif gap_pct > 2.0:
            if direction == "LONG":
                score = 10.0
                reason = f"PREMARKET_GAP[gap_up={gap_pct:.1f}%_LONG_continuation(+10)]"
            else:
                score = -3.0
                reason = f"PREMARKET_GAP[gap_up={gap_pct:.1f}%_against_SHORT(-3)]"
        elif gap_pct < -5.0:
            if direction == "SHORT":
                score = 15.0
                reason = f"PREMARKET_GAP[gap_down={gap_pct:.1f}%_HIGH_CONVICTION_SHORT(+15)]"
            else:
                score = -5.0
                reason = f"PREMARKET_GAP[gap_down={gap_pct:.1f}%_against_LONG(-5)]"
        elif gap_pct < -2.0:
            if direction == "SHORT":
                score = 10.0
                reason = f"PREMARKET_GAP[gap_down={gap_pct:.1f}%_SHORT_continuation(+10)]"
            else:
                score = -3.0
                reason = f"PREMARKET_GAP[gap_down={gap_pct:.1f}%_against_LONG(-3)]"

        result = (score, reason)
        _cache_set(cache_key, result, 300)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] premarket_gap for {symbol}: {e}")
        _cache_set(f"premarket_gap:{symbol}:{direction}", (0.0, ""), 300)
        return (0.0, "")


# ---------------------------------------------------------------------------
# Master aggregator: get_elite_score_boost
# Calls all 6 strategies and returns (total_delta, reasons_list).
# Used by signal_generator.py.
# ---------------------------------------------------------------------------

def get_elite_score_boost(
    symbol: str,
    direction: str,
    ltp: float,
    watchlist: list,
) -> Tuple[float, List[str]]:
    """
    Aggregate all 6 Tier 1/2 elite strategies.

    Args:
        symbol:    Trading symbol (e.g. 'RELIANCE.NS')
        direction: 'LONG' or 'SHORT'
        ltp:       Last traded price
        watchlist: List of active/open position symbols (peers for ranking)

    Returns:
        (total_delta, reasons_list)
        total_delta: cumulative score adjustment (can be negative)
        reasons_list: list of non-empty reason strings from each strategy
    """
    total_delta = 0.0
    reasons: List[str] = []

    # Strategy 1: Multi-Factor Score (Two Sigma)
    try:
        s1, r1 = get_multifactor_score(symbol, watchlist)
        if s1 != 0.0:
            total_delta += s1
            if r1:
                reasons.append(r1)
    except Exception as e:
        logger.debug(f"[suppressed] elite S1 multifactor: {e}")

    # Strategy 2: Options Flow Sentiment (Citadel)
    try:
        s2, r2 = get_options_flow_sentiment(symbol, direction, ltp)
        if s2 != 0.0:
            total_delta += s2
            if r2:
                reasons.append(r2)
    except Exception as e:
        logger.debug(f"[suppressed] elite S2 options_flow: {e}")

    # Strategy 3: VIX Term Structure (Goldman) — direction-adjusted
    try:
        s3, r3 = _apply_vix_term_structure_to_direction(direction)
        if s3 != 0.0:
            total_delta += s3
            if r3:
                reasons.append(r3)
    except Exception as e:
        logger.debug(f"[suppressed] elite S3 vix_term_structure: {e}")

    # Strategy 4: Yield Curve Signal (macro hedge fund)
    try:
        s4, r4 = get_yield_curve_signal(direction)
        if s4 != 0.0:
            total_delta += s4
            if r4:
                reasons.append(r4)
    except Exception as e:
        logger.debug(f"[suppressed] elite S4 yield_curve: {e}")

    # Strategy 5: Statistical Arbitrage Extended (DE Shaw)
    try:
        s5, r5 = get_stat_arb_signal(symbol, direction)
        if s5 != 0.0:
            total_delta += s5
            if r5:
                reasons.append(r5)
    except Exception as e:
        logger.debug(f"[suppressed] elite S5 stat_arb: {e}")

    # Strategy 6: Pre-Market Gap Continuation
    try:
        s6, r6 = get_premarket_gap_signal(symbol, direction)
        if s6 != 0.0:
            total_delta += s6
            if r6:
                reasons.append(r6)
    except Exception as e:
        logger.debug(f"[suppressed] elite S6 premarket_gap: {e}")

    logger.debug(
        f"[elite] {symbol} {direction}: total_delta={total_delta:+.1f} "
        f"reasons={len(reasons)}"
    )
    return (total_delta, reasons)
