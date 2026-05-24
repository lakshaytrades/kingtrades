"""
smart_money_advanced.py — Institutional / Smart Money Strategy Engine

Detects the footprints of large players (banks, hedge funds, market makers)
using OHLCV data. These methods are taught in ICT (Inner Circle Trader),
Wyckoff Method, and professional prop desk training.

Strategies implemented:
  1. Liquidity Sweep      — stop hunt then hard reversal (most common big-player move)
  2. Breaker Block        — a failed order block that becomes a magnet on retest
  3. Power of 3 (PO3)    — accumulation → manipulation → distribution in single session
  4. Wyckoff Spring       — false breakdown at support with volume climax → buy
  5. Wyckoff Upthrust     — false breakout at resistance with volume climax → sell
  6. Wyckoff Accumulation — sideways base after downtrend, institutions buying quietly
  7. Wyckoff Distribution — sideways top after uptrend, institutions selling quietly
  8. Market Maker Model   — 4-phase cycle: accumulation, markup, distribution, markdown
  9. Gamma Squeeze Setup  — near-ATM strikes, expiry approaching, MM delta hedging
 10. Dealing Range Premium/Discount — buy below equilibrium, sell above

All results are PatternResult-compatible for direct integration
with the main signal pipeline.
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from pattern_recognition import PatternResult
except ImportError:
    @dataclass
    class PatternResult:
        name: str; direction: str; confidence: float; description: str


# ─────────────────────────────────────────────────────────────────────────────
# 1. LIQUIDITY SWEEP
# ─────────────────────────────────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Liquidity Sweep (Stop Hunt) — the #1 institutional move in 2024/2025.

    Concept: Market makers KNOW where retail stop-losses sit (just below
    recent lows for longs, just above recent highs for shorts). They
    deliberately push price through those levels to trigger stops, collect
    the liquidity (fill their own large orders), then reverse sharply.

    Signal:
      - Price spikes below a KEY swing low (or above KEY swing high)
      - Immediately reverses and closes BACK above (or below) that level
      - Volume spike on the sweep candle = institutional absorption
      - Current candle closes strongly in reversal direction

    This is the most reliable smart money signal on any timeframe.
    """
    if len(df) < 15:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    # Key level: lowest low of last 10-15 bars (excluding last 2)
    key_low  = min(lows[-15:-2])
    key_high = max(highs[-15:-2])

    curr_low   = lows[-1]
    curr_high  = highs[-1]
    curr_close = closes[-1]
    curr_open  = df["open"].values[-1]
    curr_vol   = vols[-1]
    avg_vol    = vols[-15:-1].mean()

    # Bullish sweep: wick below key low, close above it (stop hunt of sellers)
    if curr_low < key_low and curr_close > key_low:
        sweep_depth = (key_low - curr_low) / key_low * 100
        vol_spike   = curr_vol / max(avg_vol, 1)
        close_above = (curr_close - key_low) / max(key_low, 0.01) * 100

        if sweep_depth >= 0.1 and close_above >= 0.05:
            confidence = 65
            if vol_spike >= 1.5:
                confidence += 15     # institutional absorption = high conviction
            if sweep_depth <= 0.5:
                confidence += 8      # shallow sweep = precise
            if curr_close > curr_open:
                confidence += 7      # bullish close confirms reversal
            confidence = min(confidence, 92)
            return PatternResult(
                "Bullish Liquidity Sweep", "LONG", confidence,
                f"Stop hunt: swept below ${key_low:.2f} by {sweep_depth:.2f}% "
                f"then reversed | vol {vol_spike:.1f}× avg"
            )

    # Bearish sweep: wick above key high, close below it
    if curr_high > key_high and curr_close < key_high:
        sweep_depth = (curr_high - key_high) / key_high * 100
        vol_spike   = curr_vol / max(avg_vol, 1)
        close_below = (key_high - curr_close) / max(key_high, 0.01) * 100

        if sweep_depth >= 0.1 and close_below >= 0.05:
            confidence = 65
            if vol_spike >= 1.5:
                confidence += 15
            if sweep_depth <= 0.5:
                confidence += 8
            if curr_close < curr_open:
                confidence += 7
            confidence = min(confidence, 92)
            return PatternResult(
                "Bearish Liquidity Sweep", "SHORT", confidence,
                f"Stop hunt: swept above ${key_high:.2f} by {sweep_depth:.2f}% "
                f"then reversed | vol {vol_spike:.1f}× avg"
            )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. BREAKER BLOCK
# ─────────────────────────────────────────────────────────────────────────────

def detect_breaker_block(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Breaker Block — Failed Order Block that flips polarity.

    Order Block: the last up-candle before a bearish move, or last
    down-candle before a bullish move. When price BREAKS THROUGH an
    order block (the institution was wrong), that old level becomes
    a 'Breaker Block' — now acts as resistance if it was support.

    Signal:
      - Identify the last bearish candle (order block) before recent rally
      - Price breaks below that candle's low  → old support = new resistance
      - Price retests that zone from below → short entry
      (And reverse for bullish breaker)

    Institutional traders use breakers to position AGAINST the retail crowd
    that missed the initial move and is now entering at the wrong side.
    """
    if len(df) < 20:
        return None

    opens  = df["open"].values
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    # Look for a bearish candle (order block) in bars 10-20 ago
    for i in range(-20, -10):
        if closes[i] >= opens[i]:
            continue   # must be bearish

        block_high = highs[i]
        block_low  = lows[i]
        block_mid  = (block_high + block_low) / 2

        # Was there a bullish rally after this block?
        post_high = max(highs[i+1:-2]) if len(highs[i+1:-2]) > 0 else 0
        if post_high <= block_high:
            continue   # no rally = not an order block

        # Did price then BREAK BACK below the block?
        post_low = min(lows[i+1:-2]) if len(lows[i+1:-2]) > 0 else 0
        if post_low >= block_low:
            continue   # did not break through → not a breaker

        # Is current price retesting the breaker zone from below?
        curr = closes[-1]
        if block_low <= curr <= block_high:
            confidence = 70
            return PatternResult(
                "Breaker Block Bearish", "SHORT", confidence,
                f"Breaker Block ${block_low:.2f}-${block_high:.2f}: "
                f"failed bullish order block → now resistance retest"
            )

    # Bullish Breaker: prior BULLISH candle that got swept through (price broke below it),
    # then price rallied back ABOVE the breaker block — it now acts as support on retest.
    for i in range(-20, -10):
        if closes[i] <= opens[i]:
            continue   # must be a bullish candle (the original block)

        block_high = highs[i]
        block_low  = lows[i]

        # Price must have broken DOWN below the block after the bullish candle
        post_low = min(lows[i+1:i+6]) if len(lows[i+1:i+6]) > 0 else block_low
        if post_low >= block_low:
            continue  # price never broke below the block — not a breaker

        # Then price must have rallied BACK ABOVE the block high (the "break" that makes it a breaker)
        post_high_recovery = max(highs[i+1:-2]) if len(highs[i+1:-2]) > 0 else 0
        if post_high_recovery <= block_high:
            continue  # price never recovered above block high

        # Current price returning to retest the breaker zone as support
        curr = closes[-1]
        if block_low <= curr <= block_high:
            confidence = 70
            return PatternResult(
                "Breaker Block Bullish", "LONG", confidence,
                f"Breaker Block ${block_low:.2f}-${block_high:.2f}: "
                f"bullish candle swept then recovered → retesting as support"
            )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. POWER OF 3 (ICT)
# ─────────────────────────────────────────────────────────────────────────────

def detect_power_of_3(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    ICT Power of 3 (PO3) — The daily candle 3-phase structure.

    Every day (and every session), price moves in 3 phases:
      1. ACCUMULATION: Asia session / early morning — tight range, choppy
      2. MANIPULATION: London open / US pre-market — fake move to trap retail
         (a spike UP to collect sell-stops, then reversal; or spike DOWN to collect buy-stops)
      3. DISTRIBUTION: Main session — the REAL directional move

    Signal:
      Using intraday bars, detect if we're entering the Distribution phase
      after a clear Manipulation spike has reversed.

    This pattern explains why first 30 min of market is often a FAKE move
    that reverses, then the REAL trend starts from 10:00-10:30 ET.
    """
    if len(df) < 30:
        return None

    # We need at least a few hours of intraday data
    # Look at price action in segments: early, mid, current
    n = len(df)
    early = df.iloc[:n//3]
    mid   = df.iloc[n//3:2*n//3]
    later = df.iloc[2*n//3:]

    early_range = early["high"].max() - early["low"].min()
    mid_range   = mid["high"].max()   - mid["low"].min()
    late_range  = later["high"].max() - later["low"].min()

    if early_range <= 0:
        return None

    # Accumulation: early range is tight (< mid range)
    accumulation_ok = early_range < mid_range * 0.7

    # Manipulation: mid-session made a spike and reversed
    mid_spike_high  = mid["high"].max()
    mid_spike_low   = mid["low"].min()
    late_close      = later["close"].iloc[-1]
    early_mid_close = early["close"].iloc[-1]

    # Bullish PO3: spike down (manipulation) then close above spike area
    bull_manip = (
        mid_spike_low < early["low"].min() and       # spike below early low
        late_close > early_mid_close                  # current above early close
    )

    # Bearish PO3: spike up (manipulation) then close below spike area
    bear_manip = (
        mid_spike_high > early["high"].max() and
        late_close < early_mid_close
    )

    if accumulation_ok and bull_manip:
        confidence = 62 + (10 if early_range < mid_range * 0.5 else 0)
        return PatternResult(
            "Power of 3 — Bullish Distribution", "LONG",
            min(confidence, 82),
            "ICT PO3: accumulation → bearish manipulation spike → "
            "now entering bullish distribution phase"
        )

    if accumulation_ok and bear_manip:
        confidence = 62 + (10 if early_range < mid_range * 0.5 else 0)
        return PatternResult(
            "Power of 3 — Bearish Distribution", "SHORT",
            min(confidence, 82),
            "ICT PO3: accumulation → bullish manipulation spike → "
            "now entering bearish distribution phase"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. WYCKOFF SPRING (Richard Wyckoff, 1930s — still works perfectly)
# ─────────────────────────────────────────────────────────────────────────────

def detect_wyckoff_spring(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Wyckoff Spring — the most powerful bullish signal in technical analysis.

    After a prolonged accumulation range (sideways), price makes a FINAL
    false breakdown below support, accompanied by climactic volume (the
    'spring' — like a compressed spring that then launches upward).

    Composite Operator (big institutions) WANT price to go below support
    to:
      a) Fill their large buy orders at discount prices
      b) Trigger retail stop-losses (buying their shares cheap)
      c) Shake out weak hands before the markup begins

    Signs of a Spring:
      1. Prior sideways accumulation (tight range for 10+ bars)
      2. Quick spike below range support (usually 1-2 candles)
      3. Immediate recovery back into range
      4. Volume climax on the spring candle (absorption)
      5. Price shows strength: closes near high of spring candle
    """
    if len(df) < 25:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    # Define accumulation range: last 15-20 bars
    acc_bars   = 15
    range_high = max(highs[-acc_bars:-3])
    range_low  = min(lows[-acc_bars:-3])
    range_size = range_high - range_low

    if range_size <= 0:
        return None

    # Range tightness: tight range = accumulation
    avg_candle_range = np.mean(highs[-acc_bars:-3] - lows[-acc_bars:-3])
    range_tightness  = avg_candle_range / range_size
    if range_tightness > 0.45:
        return None   # too wide → not accumulation

    # Spring candle: the most recent low that dipped below support
    spring_bar = df.iloc[-3]   # look 2-3 bars back for the spring
    spring_low  = spring_bar["low"]
    spring_close= spring_bar["close"]
    spring_vol  = spring_bar["volume"]
    avg_vol     = vols[-acc_bars:-3].mean()

    # Spring must breach support
    if spring_low >= range_low:
        return None

    # And close back above support (quick recovery)
    if spring_close < range_low * 0.995:
        return None

    # Current price should be inside or above range
    if closes[-1] < range_low * 0.99:
        return None

    # Volume climax check
    vol_ratio = spring_vol / max(avg_vol, 1)

    confidence = 70
    if vol_ratio >= 2.0:
        confidence += 12  # climactic volume = institutional absorption
    elif vol_ratio >= 1.5:
        confidence += 6
    if spring_close > spring_bar["open"]:
        confidence += 8   # bullish close on spring = immediate rejection
    if closes[-1] > range_high * 0.95:
        confidence += 5   # price already testing top of range

    return PatternResult(
        "Wyckoff Spring (Bullish)", "LONG", min(confidence, 93),
        f"Wyckoff Spring: false break below ${range_low:.2f} (spring) "
        f"with {vol_ratio:.1f}× volume → markup phase imminent"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. WYCKOFF UPTHRUST
# ─────────────────────────────────────────────────────────────────────────────

def detect_wyckoff_upthrust(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Wyckoff Upthrust After Distribution (UTAD) — the bearish Spring.

    After a distribution range (sideways at top), price makes a final
    false breakout above resistance — a bull trap — then reverses.

    This is where institutions SELL their entire position into retail FOMO
    buyers who see a breakout. The upthrust candle has:
      - High wick above resistance
      - Close back below resistance (the trap)
      - Volume climax (all the retail buying absorbed by institutions selling)
    """
    if len(df) < 25:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    dist_bars  = 15
    range_high = max(highs[-dist_bars:-3])
    range_low  = min(lows[-dist_bars:-3])
    range_size = range_high - range_low

    if range_size <= 0:
        return None

    avg_candle_range = np.mean(highs[-dist_bars:-3] - lows[-dist_bars:-3])
    range_tightness  = avg_candle_range / range_size
    if range_tightness > 0.45:
        return None

    ut_bar  = df.iloc[-3]
    ut_high  = ut_bar["high"]
    ut_close = ut_bar["close"]
    ut_vol   = ut_bar["volume"]
    avg_vol  = vols[-dist_bars:-3].mean()

    if ut_high <= range_high:
        return None

    if ut_close > range_high * 1.005:
        return None

    if closes[-1] > range_high * 1.01:
        return None

    vol_ratio  = ut_vol / max(avg_vol, 1)
    confidence = 70
    if vol_ratio >= 2.0:
        confidence += 12
    elif vol_ratio >= 1.5:
        confidence += 6
    if ut_close < ut_bar["open"]:
        confidence += 8
    if closes[-1] < range_low * 1.05:
        confidence += 5

    return PatternResult(
        "Wyckoff Upthrust (Bearish)", "SHORT", min(confidence, 93),
        f"Wyckoff UTAD: false break above ${range_high:.2f} "
        f"with {vol_ratio:.1f}× volume → markdown phase imminent"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. WYCKOFF ACCUMULATION PHASE (sideways base after downtrend)
# ─────────────────────────────────────────────────────────────────────────────

def detect_wyckoff_accumulation(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Wyckoff Accumulation: After sustained downtrend, price enters a
    sideways base where institutions quietly buy.

    Signs:
      - Prior downtrend (lower highs, lower lows in last 30 bars)
      - Current sideways range (narrow for 10+ bars)
      - Volume declining during range (institutional absorption)
      - On down bars within range, volume is LOWER than on up bars
        (institutions support price, no one is selling aggressively)
    """
    if len(df) < 40:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    # Prior downtrend: price 30-40 bars ago was significantly higher
    prior_high = max(highs[-40:-15])
    prior_low  = min(lows[-40:-15])
    current_zone_high = max(highs[-12:])
    current_zone_low  = min(lows[-12:])
    current_range = current_zone_high - current_zone_low

    # Must be below prior mid
    prior_mid = (prior_high + prior_low) / 2
    if current_zone_high > prior_mid:
        return None   # not in lower territory

    downtrend_decline = (prior_high - current_zone_high) / prior_high * 100
    if downtrend_decline < 8:
        return None   # not enough decline

    # Sideways: current 12-bar range is tight
    if prior_high - prior_low <= 0:
        return None
    tightness = current_range / (prior_high - prior_low)
    if tightness > 0.30:
        return None   # not tight enough

    # Volume analysis: separate up-bars and down-bars within range
    up_vol = []
    dn_vol = []
    for i in range(-12, -1):
        if closes[i] > closes[i-1]:
            up_vol.append(vols[i])
        else:
            dn_vol.append(vols[i])

    if len(up_vol) < 3 or len(dn_vol) < 3:
        return None

    # Volume on down bars should be LOWER (no aggressive selling = institutions holding)
    vol_ratio = np.mean(up_vol) / max(np.mean(dn_vol), 1)
    absorption_ok = vol_ratio >= 1.1   # up-bar volume >= down-bar volume

    confidence = 65 + (downtrend_decline * 0.5) + (absorption_ok * 12)
    confidence = min(confidence, 85)

    return PatternResult(
        "Wyckoff Accumulation Base", "LONG", confidence,
        f"Wyckoff Accumulation: {downtrend_decline:.0f}% decline → "
        f"sideways base ({tightness:.1%} of range), "
        f"up/dn vol ratio={vol_ratio:.2f} — institutions loading"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. WYCKOFF DISTRIBUTION PHASE
# ─────────────────────────────────────────────────────────────────────────────

def detect_wyckoff_distribution(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Wyckoff Distribution: After sustained uptrend, price enters a
    sideways zone where institutions quietly SELL their positions.

    Signs:
      - Prior uptrend (higher highs, higher lows)
      - Sideways range for 10+ bars at the top
      - Volume on up-bars LOWER than on down-bars (no buyers left, sellers driving)
      - Price shows weakness: closes near lows of range on high-volume bars
    """
    if len(df) < 40:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    prior_high = max(highs[-40:-15])
    prior_low  = min(lows[-40:-15])
    current_zone_high = max(highs[-12:])
    current_zone_low  = min(lows[-12:])
    current_range = current_zone_high - current_zone_low

    # Must be near top of prior range
    prior_mid = (prior_high + prior_low) / 2
    if current_zone_low < prior_mid:
        return None

    uptrend_gain = (current_zone_high - prior_low) / max(prior_low, 0.01) * 100
    if uptrend_gain < 8:
        return None

    if prior_high - prior_low <= 0:
        return None
    tightness = current_range / (prior_high - prior_low)
    if tightness > 0.30:
        return None

    up_vol, dn_vol = [], []
    for i in range(-12, -1):
        if closes[i] > closes[i-1]:
            up_vol.append(vols[i])
        else:
            dn_vol.append(vols[i])

    if len(up_vol) < 3 or len(dn_vol) < 3:
        return None

    vol_ratio = np.mean(dn_vol) / max(np.mean(up_vol), 1)
    distribution_ok = vol_ratio >= 1.1

    confidence = 65 + (uptrend_gain * 0.4) + (distribution_ok * 12)
    confidence = min(confidence, 85)

    return PatternResult(
        "Wyckoff Distribution Top", "SHORT", confidence,
        f"Wyckoff Distribution: {uptrend_gain:.0f}% rally → "
        f"sideways at top, dn/up vol ratio={vol_ratio:.2f} — institutions offloading"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. MARKET MAKER MODEL — 4-Phase Cycle
# ─────────────────────────────────────────────────────────────────────────────

def detect_market_maker_cycle(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    Market Maker Model — Tom Hougaard / Institutional prop desk method.

    Market makers cycle through 4 phases:
      Phase 1: ACCUMULATION  — MM buys quietly in range
      Phase 2: MARKUP        — MM drives price UP to offload to retail
      Phase 3: DISTRIBUTION  — MM sells at top into retail FOMO
      Phase 4: MARKDOWN      — Price collapses, MM repeats cycle

    Detection approach:
      - Identify the 4 price phases using range analysis over 50 bars
      - Current position in cycle → trade accordingly
      - Phase 1 end / Phase 2 start = BUY signal
      - Phase 3 end / Phase 4 start = SELL signal
    """
    if len(df) < 50:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    # Divide into 4 equal segments
    seg = len(closes) // 4
    s1 = closes[:seg]
    s2 = closes[seg:2*seg]
    s3 = closes[2*seg:3*seg]
    s4 = closes[3*seg:]

    s1_range = max(highs[:seg]) - min(lows[:seg])
    s2_high  = max(highs[seg:2*seg])
    s3_high  = max(highs[2*seg:3*seg])
    s4_low   = min(lows[3*seg:])

    s1_mean = s1.mean()
    s2_mean = s2.mean()
    s3_mean = s3.mean()
    s4_mean = s4.mean()

    # Detect: accum → markup → dist → markdown
    is_full_cycle = (
        s1_mean < s2_mean and        # markup after accumulation
        s3_mean >= s2_mean * 0.97 and  # distribution at or near top
        s4_mean < s3_mean            # markdown starting
    )

    if not is_full_cycle:
        return None

    # Volume pattern: high in markup, lower in distribution, climax in markdown
    v1 = vols[:seg].mean()
    v2 = vols[seg:2*seg].mean()
    v3 = vols[2*seg:3*seg].mean()
    v4 = vols[3*seg:].mean()

    current = closes[-1]

    # Currently in Phase 4 (markdown) and price oversold → cycle restart = BUY
    if s4_mean < s1_mean * 0.95 and v4 > v3 * 1.2:
        confidence = 68
        return PatternResult(
            "Market Maker Cycle — Accumulation Phase", "LONG", confidence,
            "MM Model: full cycle detected (accum→markup→dist→markdown) — "
            "markdown complete, new accumulation starting"
        )

    # Currently in Phase 3 (distribution) → SELL
    if s3_mean >= s2_mean * 0.98 and s4_mean < s3_mean and v3 > v2:
        confidence = 65
        return PatternResult(
            "Market Maker Cycle — Distribution Phase", "SHORT", confidence,
            "MM Model: markup complete, distribution detected — markdown imminent"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 9. GAMMA SQUEEZE SETUP
# ─────────────────────────────────────────────────────────────────────────────

def detect_gamma_squeeze_setup(df: pd.DataFrame, current_price: float = 0) -> Optional[PatternResult]:
    """
    Gamma Squeeze Setup (from price action alone — no options data needed).

    A gamma squeeze occurs when:
      - Stock is near a heavy options strike (round numbers: 100, 200, 500, etc.)
      - Short-dated expiry (0DTE/1DTE) creates massive gamma at that strike
      - As price moves toward strike, Market Makers MUST delta-hedge
        by buying the stock (for calls) or selling (for puts)
      - This hedge-buying accelerates the move → self-reinforcing loop

    Price action signature (detectable without options data):
      1. Price approaching a round number or prior key level
      2. Volume increasing as price nears the level
      3. Tight consolidation just below (for calls) or above (for puts)
      4. Increasing velocity: each candle's close closer to the level

    Used by options scalpers to enter the underlying stock (or OTM calls/puts)
    before the squeeze accelerates.
    """
    if len(df) < 20:
        return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values

    price = current_price if current_price > 0 else closes[-1]

    # Find nearest round number (institutional gamma magnet)
    magnitude   = 10 ** (len(str(int(price))) - 1)
    round_below = (price // magnitude) * magnitude
    round_above = round_below + magnitude

    nearest_round = round_above if (round_above - price) < (price - round_below) else round_below
    distance_pct  = abs(price - nearest_round) / price * 100

    # Must be within 2% of a round number
    if distance_pct > 2.0:
        return None

    # Volume increasing as price approaches
    vol_trend = vols[-5:].mean() / max(vols[-20:-5].mean(), 1)
    if vol_trend < 1.2:
        return None   # need accelerating volume

    # Tight consolidation (low volatility = spring-loaded)
    recent_range = (max(highs[-8:]) - min(lows[-8:])) / price * 100
    if recent_range > 3.0:
        return None

    # Velocity: closes accelerating toward round number
    approaching_up   = price < nearest_round and closes[-1] > closes[-5]
    approaching_down = price > nearest_round and closes[-1] < closes[-5]

    if approaching_up:
        confidence = 66 + vol_trend * 8 + (2.0 - distance_pct) * 5
        return PatternResult(
            "Gamma Squeeze Setup (Bullish)", "LONG", min(confidence, 86),
            f"Gamma Magnet: price approaching ${nearest_round:.0f} round level "
            f"({distance_pct:.1f}% away) | vol {vol_trend:.1f}× | "
            f"tight range {recent_range:.2f}% — MM delta-hedging will accelerate move"
        )

    if approaching_down:
        confidence = 66 + vol_trend * 8 + (2.0 - distance_pct) * 5
        return PatternResult(
            "Gamma Squeeze Setup (Bearish)", "SHORT", min(confidence, 86),
            f"Gamma Magnet: price falling toward ${nearest_round:.0f} "
            f"({distance_pct:.1f}% away) | vol {vol_trend:.1f}× | "
            f"MM put-hedging will accelerate decline"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 10. DEALING RANGE — Premium / Discount
# ─────────────────────────────────────────────────────────────────────────────

def detect_dealing_range(df: pd.DataFrame) -> Optional[PatternResult]:
    """
    ICT Dealing Range — Premium vs Discount zone framework.

    The current dealing range = high to low of last significant swing.
    Equilibrium = 50% of the range.

    Rule:
      - Price BELOW equilibrium = Discount zone → look for LONGS
      - Price ABOVE equilibrium = Premium zone  → look for SHORTS

    Combined with directional bias (trend), this tells you:
      Uptrend + Price in Discount zone → HIGH probability long
      Downtrend + Price in Premium zone → HIGH probability short

    18-year insight: "Most retail traders buy when price is in Premium
    (expensive) and sell when price is in Discount (cheap). Do the opposite."
    """
    if len(df) < 30:
        return None

    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    # Dealing range = last significant swing
    swing_high = max(highs[-30:-5])
    swing_low  = min(lows[-30:-5])
    range_size = swing_high - swing_low

    if range_size <= 0:
        return None

    equilibrium = (swing_high + swing_low) / 2
    current     = closes[-1]

    # Position in range (0% = bottom, 100% = top)
    range_position = (current - swing_low) / range_size * 100

    # Trend direction: slope of last 20 closes
    x     = np.arange(20)
    slope = np.polyfit(x, closes[-20:], 1)[0]
    trend = "UP" if slope > 0 else "DOWN"

    if range_position <= 37:  # Discount zone (below 37%)
        discount_depth = 37 - range_position
        confidence = 60 + discount_depth * 0.5
        if trend == "UP":
            confidence += 15   # bullish trend + discount = high probability long
        return PatternResult(
            "ICT Discount Zone — Long Bias", "LONG", min(confidence, 85),
            f"Dealing Range: price at {range_position:.0f}% (Discount) "
            f"| EQ=${equilibrium:.2f} | trend={trend} "
            f"{'→ HIGH probability long' if trend == 'UP' else '→ counter-trend long (lower confidence)'}"
        )

    if range_position >= 63:  # Premium zone (above 63%)
        premium_height = range_position - 63
        confidence = 60 + premium_height * 0.5
        if trend == "DOWN":
            confidence += 15
        return PatternResult(
            "ICT Premium Zone — Short Bias", "SHORT", min(confidence, 85),
            f"Dealing Range: price at {range_position:.0f}% (Premium) "
            f"| EQ=${equilibrium:.2f} | trend={trend} "
            f"{'→ HIGH probability short' if trend == 'DOWN' else '→ counter-trend short (lower confidence)'}"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MASTER SCANNER
# ─────────────────────────────────────────────────────────────────────────────

class SmartMoneyAdvancedScanner:
    """Runs all smart money detectors on a DataFrame."""

    DETECTORS = [
        detect_liquidity_sweep,
        detect_breaker_block,
        detect_power_of_3,
        detect_wyckoff_spring,
        detect_wyckoff_upthrust,
        detect_wyckoff_accumulation,
        detect_wyckoff_distribution,
        detect_market_maker_cycle,
        detect_gamma_squeeze_setup,
        detect_dealing_range,
    ]

    def scan(self, df: pd.DataFrame, current_price: float = 0) -> List[PatternResult]:
        results = []
        for detector in self.DETECTORS:
            try:
                if detector == detect_gamma_squeeze_setup:
                    r = detector(df, current_price)
                else:
                    r = detector(df)
                if r and r.confidence >= 60:
                    results.append(r)
            except Exception as e:
                logger.debug(f"SmartMoney {detector.__name__}: {e}")
        return results


_scanner: Optional[SmartMoneyAdvancedScanner] = None


def get_smart_money_scanner() -> SmartMoneyAdvancedScanner:
    global _scanner
    if _scanner is None:
        _scanner = SmartMoneyAdvancedScanner()
    return _scanner
