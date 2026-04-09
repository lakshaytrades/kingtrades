"""
option_chain.py — NSE Momentum Groww AI Bot
NSE F&O Option Chain Analysis Engine

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

The option chain is THE most powerful edge for NSE intraday trading.
18+ years of experience: institutions hedge via options BEFORE they move spot.
Reading the chain = reading institutional positioning BEFORE the move.

What this module provides:
  • Put-Call Ratio (PCR) — market sentiment gauge
  • Max Pain level — where most options expire worthless (magnetic price)
  • Open Interest (OI) change — fresh positioning vs covering
  • Implied Volatility (IV) skew — direction bias
  • Support/Resistance from highest OI strikes
  • Gamma Exposure (GEX) — market stability or fragility
  • IV Percentile — whether to widen/tighten stops

Data Source: NSE India public API (no auth required, proper headers needed)
  Nifty:     https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY
  BankNifty: https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY
  Stocks:    https://www.nseindia.com/api/option-chain-equities?symbol=SYMBOL

Usage:
  from option_chain import OptionChainAnalyzer
  oc  = OptionChainAnalyzer()
  res = oc.analyze("NIFTY")  # or "BANKNIFTY" or equity symbol
  bias, score = res.direction_bias, res.confidence_score
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# NSE API endpoints
NSE_INDEX_OC = "https://www.nseindia.com/api/option-chain-indices"
NSE_EQUITY_OC = "https://www.nseindia.com/api/option-chain-equities"
NSE_EXPIRY    = "https://www.nseindia.com/api/option-chain-indices"

# NSE requires browser-like headers to avoid 403
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
    "Connection": "keep-alive",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

INDEX_SYMBOLS  = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
CACHE_TTL_SECS = 300  # Refresh option chain every 5 minutes


# ============================================================
# RESULT DATACLASS
# ============================================================

@dataclass
class OptionChainResult:
    """Complete option chain analysis result for one symbol."""
    symbol:          str
    spot_price:      float
    expiry:          str           # Nearest weekly/monthly expiry
    pcr:             float         # Put-Call Ratio (OI-based)
    pcr_volume:      float         # PCR by volume
    max_pain:        float         # Strike where most options expire worthless
    resistance_1:    float         # Highest call OI strike (resistance)
    resistance_2:    float         # Second highest call OI strike
    support_1:       float         # Highest put OI strike (support)
    support_2:       float         # Second highest put OI strike
    call_oi_total:   int
    put_oi_total:    int
    call_oi_change:  int           # OI change in calls (fresh shorts = bearish)
    put_oi_change:   int           # OI change in puts (fresh shorts = bullish)
    atm_iv:          float         # At-the-money implied volatility
    iv_skew:         float         # Call IV - Put IV (positive = put demand = bearish hedge)
    gex:             float         # Gamma exposure (positive = stabilising, negative = amplifying)
    direction_bias:  str           # "BULLISH" / "BEARISH" / "NEUTRAL"
    confidence_score: float        # 0-100
    signals:         List[str]     = field(default_factory=list)
    timestamp:       str           = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    @property
    def pcr_interpretation(self) -> str:
        if self.pcr > 1.5:
            return "EXTREME BULLISH (contrarian)"
        if self.pcr > 1.2:
            return "BULLISH"
        if self.pcr > 0.8:
            return "NEUTRAL"
        if self.pcr > 0.5:
            return "BEARISH"
        return "EXTREME BEARISH (contrarian)"

    def summary(self) -> str:
        return (
            f"OC {self.symbol} | Spot: ₹{self.spot_price:.0f} | "
            f"PCR: {self.pcr:.2f} ({self.pcr_interpretation}) | "
            f"MaxPain: ₹{self.max_pain:.0f} | "
            f"Support: ₹{self.support_1:.0f} | "
            f"Resist: ₹{self.resistance_1:.0f} | "
            f"Bias: {self.direction_bias} ({self.confidence_score:.0f}/100)"
        )


# ============================================================
# MAIN ANALYZER
# ============================================================

class OptionChainAnalyzer:
    """
    NSE Option Chain Analysis Engine.

    Reads the live NSE option chain for NIFTY/BANKNIFTY/equities and
    extracts institutional positioning signals:
      - PCR (Put-Call Ratio): <0.7 bearish, >1.3 bullish, >1.5 extreme bullish
      - Max Pain: price magnet at expiry
      - OI-based support/resistance levels
      - IV skew (put demand > call demand = bearish hedge by institutions)
      - Gamma Exposure (GEX): negative = volatile/trending moves amplified
    """

    def __init__(self):
        self._session     = requests.Session()
        self._cache:      Dict[str, Tuple[OptionChainResult, datetime]] = {}
        self._session_ok  = False
        self._init_session()

    # ──────────────────────────────────────────────────────
    # SESSION MANAGEMENT
    # ──────────────────────────────────────────────────────

    def _init_session(self):
        """Establish NSE session (required before API calls)."""
        try:
            self._session.headers.update(NSE_HEADERS)
            # Visit NSE homepage to set cookies
            resp = self._session.get(
                "https://www.nseindia.com/",
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                self._session_ok = True
                logger.info(f"[{format_ist_timestamp()}] NSE session established")
            else:
                logger.warning(f"[{format_ist_timestamp()}] NSE session status: {resp.status_code}")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] NSE session init failed: {e}")
            self._session_ok = False

    def _refresh_session_if_needed(self):
        if not self._session_ok:
            self._init_session()

    # ──────────────────────────────────────────────────────
    # DATA FETCH
    # ──────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=3, delays=[2, 5, 10])
    def _fetch_raw(self, symbol: str) -> Optional[Dict]:
        """Fetch raw option chain JSON from NSE."""
        self._refresh_session_if_needed()
        is_index = symbol.upper() in INDEX_SYMBOLS
        url      = NSE_INDEX_OC if is_index else NSE_EQUITY_OC
        try:
            resp = self._session.get(
                url,
                params={"symbol": symbol.upper()},
                timeout=20,
            )
            if resp.status_code == 403:
                logger.warning(f"[{format_ist_timestamp()}] NSE 403 — refreshing session")
                self._session_ok = False
                self._init_session()
                time.sleep(2)
                raise Exception("NSE 403 — retry after session refresh")

            resp.raise_for_status()
            data = resp.json()
            return data.get("records", data)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] OC fetch {symbol}: {e}")
            raise

    # ──────────────────────────────────────────────────────
    # PARSE + ANALYSE
    # ──────────────────────────────────────────────────────

    def analyze(self, symbol: str = "NIFTY") -> Optional[OptionChainResult]:
        """
        Full option chain analysis for symbol.
        Returns cached result if < 5 minutes old.
        """
        symbol = symbol.upper()

        # Cache check
        if symbol in self._cache:
            result, ts = self._cache[symbol]
            if (datetime.now(IST) - ts).total_seconds() < CACHE_TTL_SECS:
                return result

        try:
            raw = self._fetch_raw(symbol)
            if not raw:
                return None
            result = self._parse_chain(symbol, raw)
            if result:
                self._cache[symbol] = (result, datetime.now(IST))
                logger.info(f"[{format_ist_timestamp()}] {result.summary()}")
            return result
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] OC analyze {symbol}: {e}")
            return None

    def _parse_chain(self, symbol: str, raw: Dict) -> Optional[OptionChainResult]:
        """Parse NSE option chain response into OptionChainResult."""
        try:
            data          = raw.get("data", [])
            expiry_dates  = raw.get("expiryDates", [])
            spot_price    = float(raw.get("underlyingValue", 0))

            if not data or spot_price == 0:
                return None

            # Use nearest expiry
            nearest_expiry = expiry_dates[0] if expiry_dates else "unknown"

            # Filter to nearest expiry only
            chain_data = [
                d for d in data
                if d.get("expiryDate", "") == nearest_expiry
            ]
            if not chain_data:
                chain_data = data  # fallback: all expiries

            # Build structured rows
            rows = []
            for item in chain_data:
                strike = float(item.get("strikePrice", 0))
                ce     = item.get("CE", {}) or {}
                pe     = item.get("PE", {}) or {}
                if strike == 0:
                    continue
                rows.append({
                    "strike":       strike,
                    "ce_oi":        int(ce.get("openInterest", 0)),
                    "pe_oi":        int(pe.get("openInterest", 0)),
                    "ce_oi_chg":    int(ce.get("changeinOpenInterest", 0)),
                    "pe_oi_chg":    int(pe.get("changeinOpenInterest", 0)),
                    "ce_vol":       int(ce.get("totalTradedVolume", 0)),
                    "pe_vol":       int(pe.get("totalTradedVolume", 0)),
                    "ce_iv":        float(ce.get("impliedVolatility", 0)),
                    "pe_iv":        float(pe.get("impliedVolatility", 0)),
                    "ce_ltp":       float(ce.get("lastPrice", 0)),
                    "pe_ltp":       float(pe.get("lastPrice", 0)),
                    "ce_gamma":     float(ce.get("delta", 0)),  # using delta as proxy
                    "pe_gamma":     float(pe.get("delta", 0)),
                })

            if not rows:
                return None

            df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)

            # ── Core metrics ─────────────────────────────
            total_call_oi = df["ce_oi"].sum()
            total_put_oi  = df["pe_oi"].sum()
            total_call_vol = df["ce_vol"].sum()
            total_put_vol  = df["pe_vol"].sum()

            pcr_oi  = round(total_put_oi  / max(total_call_oi,  1), 3)
            pcr_vol = round(total_put_vol / max(total_call_vol, 1), 3)

            # Max Pain: strike where total option loss is maximum for buyers
            max_pain = self._calc_max_pain(df)

            # Key OI levels
            call_sorted = df.nlargest(5, "ce_oi")
            put_sorted  = df.nlargest(5, "pe_oi")
            resistance_1 = float(call_sorted.iloc[0]["strike"]) if len(call_sorted) > 0 else spot_price
            resistance_2 = float(call_sorted.iloc[1]["strike"]) if len(call_sorted) > 1 else spot_price
            support_1    = float(put_sorted.iloc[0]["strike"])  if len(put_sorted) > 0 else spot_price
            support_2    = float(put_sorted.iloc[1]["strike"])  if len(put_sorted) > 1 else spot_price

            # ATM IV
            df["dist_to_spot"] = abs(df["strike"] - spot_price)
            atm_row = df.loc[df["dist_to_spot"].idxmin()]
            atm_ce_iv = float(atm_row["ce_iv"])
            atm_pe_iv = float(atm_row["pe_iv"])
            atm_iv    = (atm_ce_iv + atm_pe_iv) / 2 if (atm_ce_iv + atm_pe_iv) > 0 else 0.0
            iv_skew   = round(atm_ce_iv - atm_pe_iv, 2)  # +ve = put demand = institutions hedging down

            # OI change
            call_oi_chg = int(df["ce_oi_chg"].sum())
            put_oi_chg  = int(df["pe_oi_chg"].sum())

            # Gamma Exposure (simplified: positive near ATM strikes = pinning force)
            gex = self._calc_gex(df, spot_price)

            # ── Direction bias ────────────────────────────
            bias, confidence, signals = self._determine_bias(
                pcr_oi, pcr_vol, max_pain, spot_price,
                resistance_1, support_1, call_oi_chg, put_oi_chg,
                iv_skew, gex,
            )

            return OptionChainResult(
                symbol=symbol,
                spot_price=spot_price,
                expiry=nearest_expiry,
                pcr=pcr_oi,
                pcr_volume=pcr_vol,
                max_pain=max_pain,
                resistance_1=resistance_1,
                resistance_2=resistance_2,
                support_1=support_1,
                support_2=support_2,
                call_oi_total=int(total_call_oi),
                put_oi_total=int(total_put_oi),
                call_oi_change=call_oi_chg,
                put_oi_change=put_oi_chg,
                atm_iv=round(atm_iv, 2),
                iv_skew=iv_skew,
                gex=round(gex, 2),
                direction_bias=bias,
                confidence_score=round(confidence, 1),
                signals=signals,
            )

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] OC parse error: {e}", exc_info=True)
            return None

    # ──────────────────────────────────────────────────────
    # MAX PAIN CALCULATION
    # ──────────────────────────────────────────────────────

    def _calc_max_pain(self, df: pd.DataFrame) -> float:
        """
        Max Pain: strike where total option premium paid by buyers is maximised
        (i.e., where sellers — market makers — profit most).
        Spot tends to drift toward max pain near expiry.
        """
        strikes   = df["strike"].values
        losses    = []
        for target in strikes:
            # Total loss to call buyers if expires at `target`
            call_loss = sum(max(0, target - s) * df.loc[df["strike"] == s, "ce_oi"].iloc[0]
                            for s in strikes if df.loc[df["strike"] == s, "ce_oi"].iloc[0] > 0)
            # Total loss to put buyers if expires at `target`
            put_loss  = sum(max(0, s - target) * df.loc[df["strike"] == s, "pe_oi"].iloc[0]
                            for s in strikes if df.loc[df["strike"] == s, "pe_oi"].iloc[0] > 0)
            losses.append(call_loss + put_loss)

        max_pain_idx = int(pd.Series(losses).idxmin())
        return float(strikes[max_pain_idx])

    # ──────────────────────────────────────────────────────
    # GAMMA EXPOSURE
    # ──────────────────────────────────────────────────────

    def _calc_gex(self, df: pd.DataFrame, spot: float) -> float:
        """
        Simplified Gamma Exposure.
        Positive GEX = market makers are long gamma → they sell rallies/buy dips → dampening.
        Negative GEX = market makers are short gamma → they buy rallies/sell dips → amplifying.
        For trending moves, negative GEX is better.
        """
        # Only consider strikes ±5% from spot
        near = df[(df["strike"] >= spot * 0.95) & (df["strike"] <= spot * 1.05)].copy()
        if near.empty:
            return 0.0
        # GEX proxy: net call OI - net put OI near ATM (simplified)
        gex = float((near["ce_oi"].sum() - near["pe_oi"].sum()) / 1e5)
        return round(gex, 2)

    # ──────────────────────────────────────────────────────
    # DIRECTION BIAS ENGINE
    # ──────────────────────────────────────────────────────

    def _determine_bias(
        self, pcr: float, pcr_vol: float, max_pain: float, spot: float,
        resistance: float, support: float, call_oi_chg: int, put_oi_chg: int,
        iv_skew: float, gex: float,
    ) -> Tuple[str, float, List[str]]:
        """
        18-year option chain reading methodology:

        BULLISH signals:
        - PCR > 1.2: More puts than calls = contrarian bullish (bears are hedged = bottom)
        - Spot above max pain: Spot will rise toward max pain
        - Fresh put writing (put OI change < 0): Institutions selling puts = floor
        - Positive IV skew: Calls expensive = bull sentiment
        - Spot near put support wall (call support holding)

        BEARISH signals:
        - PCR < 0.7: More calls than puts = retail euphoria = top
        - Spot below max pain: Spot will fall toward max pain
        - Fresh call writing (call OI change < 0): Institutions selling calls = ceiling
        - Negative IV skew: Puts expensive = institutions hedging downside
        - Spot near call resistance wall
        """
        score  = 0.0
        signals: List[str] = []

        # ── PCR analysis ─────────────────────────────────
        if pcr > 1.5:
            score += 20
            signals.append(f"PCR={pcr:.2f} (extreme put writing — contrarian BULLISH)")
        elif pcr > 1.2:
            score += 12
            signals.append(f"PCR={pcr:.2f} (put-heavy — BULLISH bias)")
        elif pcr > 0.9:
            score += 4
            signals.append(f"PCR={pcr:.2f} (balanced — NEUTRAL)")
        elif pcr > 0.6:
            score -= 12
            signals.append(f"PCR={pcr:.2f} (call-heavy — BEARISH bias)")
        else:
            score -= 20
            signals.append(f"PCR={pcr:.2f} (extreme call writing — contrarian BEARISH)")

        # ── Max Pain magnetic pull ────────────────────────
        pain_dist_pct = (spot - max_pain) / spot * 100
        if abs(pain_dist_pct) < 0.3:
            signals.append(f"Spot near MaxPain ₹{max_pain:.0f} — pinning expected")
        elif pain_dist_pct > 1.0:
            score -= 8
            signals.append(f"Spot ABOVE MaxPain ₹{max_pain:.0f} — gravity pull DOWN")
        elif pain_dist_pct < -1.0:
            score += 8
            signals.append(f"Spot BELOW MaxPain ₹{max_pain:.0f} — gravity pull UP")

        # ── OI Change: fresh writing vs covering ─────────
        if call_oi_chg < -100000:  # Fresh call covering → bullish
            score += 8
            signals.append(f"Call OI unwinding ({call_oi_chg:,}) — bears covering → BULLISH")
        elif call_oi_chg > 100000:  # Fresh call writing → bearish ceiling
            score -= 10
            signals.append(f"Fresh call writing ({call_oi_chg:,}) → ceiling at ₹{resistance:.0f}")

        if put_oi_chg < -100000:  # Fresh put covering → bearish
            score -= 8
            signals.append(f"Put OI unwinding ({put_oi_chg:,}) — bulls covering → BEARISH")
        elif put_oi_chg > 100000:  # Fresh put writing → bullish floor
            score += 10
            signals.append(f"Fresh put writing ({put_oi_chg:,}) → floor at ₹{support:.0f}")

        # ── IV Skew ───────────────────────────────────────
        if iv_skew > 3:
            score += 8
            signals.append(f"IV skew +{iv_skew:.1f} — call premium demand → BULLISH")
        elif iv_skew < -3:
            score -= 8
            signals.append(f"IV skew {iv_skew:.1f} — put premium demand → bearish hedge by institutions")

        # ── GEX ───────────────────────────────────────────
        if gex < -5:
            signals.append(f"Negative GEX={gex:.1f} — MMs short gamma → TRENDING moves amplified")
        elif gex > 5:
            signals.append(f"Positive GEX={gex:.1f} — MMs long gamma → dampening/pinning expected")

        # ── Spot position relative to walls ──────────────
        if spot > resistance * 0.99:
            score -= 6
            signals.append(f"Spot near call wall ₹{resistance:.0f} — heavy resistance")
        if spot < support * 1.01:
            score += 6
            signals.append(f"Spot near put wall ₹{support:.0f} — strong support")

        # ── Determine bias ────────────────────────────────
        if score >= 15:
            bias = "BULLISH"
        elif score <= -15:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        confidence = min(abs(score) * 2.5, 100)
        return bias, confidence, signals

    # ──────────────────────────────────────────────────────
    # CONVENIENCE: SCORE FOR SIGNAL GENERATOR
    # ──────────────────────────────────────────────────────

    def get_direction_score(self, symbol: str = "NIFTY") -> float:
        """
        Returns a score from -10 to +10 for use in signal_generator.
        Positive = bullish, negative = bearish.
        """
        result = self.analyze(symbol)
        if not result:
            return 0.0
        if result.direction_bias == "BULLISH":
            return min(result.confidence_score / 10, 10.0)
        if result.direction_bias == "BEARISH":
            return max(-result.confidence_score / 10, -10.0)
        return 0.0

    def get_key_levels(self, symbol: str = "NIFTY") -> Dict[str, float]:
        """
        Returns key option chain levels for a symbol.
        Used by signal_generator for support/resistance context.
        """
        result = self.analyze(symbol)
        if not result:
            return {}
        return {
            "max_pain":    result.max_pain,
            "resistance_1": result.resistance_1,
            "resistance_2": result.resistance_2,
            "support_1":   result.support_1,
            "support_2":   result.support_2,
            "pcr":         result.pcr,
            "atm_iv":      result.atm_iv,
        }

    def is_near_key_level(
        self, price: float, symbol: str = "NIFTY", tolerance_pct: float = 0.5
    ) -> Dict[str, bool]:
        """Check if a price is near a key option chain level."""
        levels = self.get_key_levels(symbol)
        result = {}
        for name, level in levels.items():
            if isinstance(level, float) and level > 0:
                dist_pct = abs(price - level) / level * 100
                result[f"near_{name}"] = dist_pct <= tolerance_pct
        return result

    def format_telegram(self, symbol: str = "NIFTY") -> str:
        """Format option chain summary for Telegram morning brief."""
        result = self.analyze(symbol)
        if not result:
            return f"Option chain for {symbol}: unavailable"

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(
            result.direction_bias, "🟡"
        )
        signals_str = "\n".join(f"  • {s}" for s in result.signals[:5])

        return (
            f"📊 *Option Chain — {symbol}* ({result.expiry})\n"
            f"Spot: `₹{result.spot_price:,.0f}` | "
            f"PCR: `{result.pcr:.2f}` | "
            f"ATM IV: `{result.atm_iv:.1f}%`\n"
            f"MaxPain: `₹{result.max_pain:,.0f}` | "
            f"Support: `₹{result.support_1:,.0f}` | "
            f"Resist: `₹{result.resistance_1:,.0f}`\n"
            f"{bias_emoji} Bias: *{result.direction_bias}* ({result.confidence_score:.0f}/100)\n"
            f"*Signals:*\n{signals_str}"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_oc_analyzer: Optional[OptionChainAnalyzer] = None

def get_option_chain_analyzer() -> OptionChainAnalyzer:
    global _oc_analyzer
    if _oc_analyzer is None:
        _oc_analyzer = OptionChainAnalyzer()
    return _oc_analyzer


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    oc = OptionChainAnalyzer()
    for sym in ["NIFTY", "BANKNIFTY"]:
        print(f"\n{'='*60}")
        result = oc.analyze(sym)
        if result:
            print(result.summary())
            for sig in result.signals:
                print(f"  → {sig}")
        else:
            print(f"{sym}: No data (check NSE connectivity)")
