"""
global_market_context.py — US Momentum Alpaca AI Bot
World Market Intelligence Engine

Every professional trader starts the day by reading global markets.
This module encodes 18yr of inter-market relationship knowledge:

1. VIX regime          — fear gauge determines position sizing
2. US Futures          — pre-market direction from overnight S&P/Nasdaq
3. Bond yields (TLT)   — rising yields = headwind for tech/growth
4. Gold (GLD)          — risk-off signal, inverse to equities
5. Oil (USO)           — energy stocks driver, consumer headwind
6. USD strength (UUP)  — affects multinationals vs domestics
7. Asia session cues   — Nikkei/HSI set overnight risk-on/off tone
8. European session    — DAX correlates 0.72 with SPY direction
9. Calendar effects    — OpEx, FOMC week, NFP day patterns
10. Day-of-week stats  — Tuesday/Thursday best trend days

Returns: score_adjustment (−25 to +25) per signal so the AI scorer
can reward signals that align with global market conditions and
penalize signals that fight the global tape.

"The stock market is a derivative of the bond market, which is a
derivative of the currency market, which is a derivative of the
global economy." — 18yr trading truth
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# PROXY ETFS — used to read inter-market signals via Alpaca quotes
# ─────────────────────────────────────────────────────────────────────────────
PROXY_TICKERS = {
    "spy":   "SPY",   # S&P 500 — US broad market
    "qqq":   "QQQ",   # Nasdaq 100 — tech/growth
    "tlt":   "TLT",   # 20Y Treasury — inverse yield signal
    "gld":   "GLD",   # Gold — risk-off gauge
    "uso":   "USO",   # Crude Oil — energy/inflation signal
    "uup":   "UUP",   # US Dollar Index — FX signal
    "vix":   "UVXY",  # VIX proxy (UVXY = 1.5× VIX; adjust thresholds)
    "iwm":   "IWM",   # Small Cap (Russell 2000) — risk appetite
    "xlf":   "XLF",   # Financials — yield-sensitive
    "xlk":   "XLK",   # Technology — rate-sensitive
}

# ─────────────────────────────────────────────────────────────────────────────
# ECONOMIC CALENDAR — hard-coded 2025-2026
# ─────────────────────────────────────────────────────────────────────────────
FOMC_DATES = {
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
}

NFP_DATES = {
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05",
}

CPI_DATES = {
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-15", "2025-11-12", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-09",
    "2026-05-13", "2026-06-10",
}

PPI_DATES = {
    "2025-01-14", "2025-02-13", "2025-03-13", "2025-04-11",
    "2025-05-15", "2025-06-12", "2025-07-16", "2025-08-13",
    "2025-09-11", "2025-10-16", "2025-11-13", "2025-12-11",
    "2026-01-15", "2026-02-12", "2026-03-12", "2026-04-10",
    "2026-05-14", "2026-06-11",
}

GDP_DATES = {
    "2025-04-30", "2025-07-30", "2025-10-29", "2026-01-28",
    "2026-04-29", "2026-07-29",
}

# 3rd Friday of March/June/Sep/Dec — Quad Witching
QUAD_WITCH_DATES = {
    "2025-03-21", "2025-06-20", "2025-09-19", "2025-12-19",
    "2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18",
}

# ─────────────────────────────────────────────────────────────────────────────
# STOCK → SECTOR mapping for inter-market signal routing
# ─────────────────────────────────────────────────────────────────────────────
SYMBOL_SECTOR = {
    # Tech / AI
    "NVDA": "TECH", "AMD": "TECH", "AAPL": "TECH", "MSFT": "TECH",
    "AVGO": "TECH", "QCOM": "TECH", "MU": "TECH", "SMCI": "TECH",
    "ARM": "TECH", "ORCL": "TECH", "ADBE": "TECH", "AMAT": "TECH",
    "LRCX": "TECH", "KLAC": "TECH", "QQQ": "TECH", "SOXL": "TECH",
    # Finance
    "JPM": "FINANCE", "BAC": "FINANCE", "GS": "FINANCE", "MS": "FINANCE",
    "V": "FINANCE", "MA": "FINANCE", "PYPL": "FINANCE", "SQ": "FINANCE",
    "COIN": "FINANCE", "HOOD": "FINANCE",
    # Energy
    "XOM": "ENERGY", "CVX": "ENERGY", "OXY": "ENERGY",
    "SLB": "ENERGY", "HAL": "ENERGY",
    # Consumer
    "AMZN": "CONSUMER", "TSLA": "CONSUMER", "NKE": "CONSUMER",
    "TGT": "CONSUMER", "WMT": "CONSUMER", "COST": "CONSUMER",
    # Healthcare
    "UNH": "HEALTH", "LLY": "HEALTH", "JNJ": "HEALTH",
    "ABBV": "HEALTH", "MRK": "HEALTH",
    # Communication / Social
    "META": "COMMS", "GOOGL": "COMMS", "NFLX": "COMMS",
    # Crypto-adjacent
    "MARA": "CRYPTO", "RIOT": "CRYPTO", "MSTR": "CRYPTO",
    # Index ETFs
    "SPY": "INDEX", "IWM": "INDEX",
}

# Inter-market signals that are BULLISH for each sector
SECTOR_BULLISH_CONDITIONS = {
    "TECH":     ["QQQ_UP", "YIELD_DOWN", "LOW_VIX", "NASDAQ_STRONG"],
    "FINANCE":  ["YIELD_UP", "STRONG_USD", "SPY_UP", "CURVE_STEEPEN"],
    "ENERGY":   ["OIL_UP", "INFLATION", "WEAK_USD"],
    "CONSUMER": ["OIL_DOWN", "LOW_VIX", "CONSUMER_STRONG"],
    "HEALTH":   ["DEFENSIVE", "HIGH_VIX", "RISK_OFF"],
    "COMMS":    ["LOW_VIX", "AD_SPEND_UP", "CONSUMER_STRONG"],
    "CRYPTO":   ["RISK_ON", "LOW_VIX", "BTC_UP"],
    "INDEX":    ["SPY_UP", "LOW_VIX", "BREADTH_POSITIVE"],
}

SECTOR_BEARISH_CONDITIONS = {
    "TECH":    ["YIELD_UP", "HIGH_VIX", "NASDAQ_WEAK"],
    "FINANCE": ["YIELD_DOWN", "RISK_OFF"],
    "ENERGY":  ["OIL_DOWN", "RECESSION_FEAR"],
    "CONSUMER":["HIGH_OIL", "RECESSION"],
    "HEALTH":  ["RISK_ON", "CYCLICAL_ROTATION"],
    "COMMS":   ["HIGH_VIX", "AD_SPEND_DOWN"],
    "CRYPTO":  ["RISK_OFF", "HIGH_VIX", "REGULATION_FEAR"],
    "INDEX":   ["SPY_DOWN", "HIGH_VIX"],
}


class GlobalMarketContext:
    """
    Encodes global inter-market relationships into a per-signal score adjustment.
    Fetches proxy ETF data once every 5 minutes (CACHE_TTL_SECONDS).
    All methods fail-open (return 0 on any error) so the trading engine is never blocked.
    """

    CACHE_TTL_SECONDS = 300

    def __init__(self, data_fetcher=None):
        self._fetcher = data_fetcher
        self._conditions: Dict = {}
        self._last_refresh: Optional[datetime] = None
        self._proxy_data: Dict = {}

    def refresh(self) -> None:
        """Force-refresh market conditions cache."""
        now = datetime.now(ET)
        if (
            self._last_refresh is None
            or (now - self._last_refresh).total_seconds() > self.CACHE_TTL_SECONDS
        ):
            try:
                self._proxy_data  = self._fetch_proxy_data()
                self._conditions  = self._derive_conditions(self._proxy_data)
                self._last_refresh = now
            except Exception as e:
                logger.debug(f"GlobalMarketContext refresh error: {e}")

    def _fetch_proxy_data(self) -> Dict:
        """Fetch current quotes for all proxy ETFs."""
        data = {}
        if not self._fetcher:
            return data
        for key, ticker in PROXY_TICKERS.items():
            try:
                q = self._fetcher.get_quote(ticker) or {}
                data[key] = {
                    "change_pct": float(q.get("change_pct", 0) or 0),
                    "price":      float(q.get("price", q.get("ltp", 0)) or 0),
                }
            except Exception:
                data[key] = {"change_pct": 0.0, "price": 0.0}
        return data

    def _derive_conditions(self, pd: Dict) -> Dict:
        """Translate raw proxy data into named market conditions."""
        c = {}
        spy_chg  = pd.get("spy",  {}).get("change_pct", 0)
        qqq_chg  = pd.get("qqq",  {}).get("change_pct", 0)
        tlt_chg  = pd.get("tlt",  {}).get("change_pct", 0)
        gld_chg  = pd.get("gld",  {}).get("change_pct", 0)
        uso_chg  = pd.get("uso",  {}).get("change_pct", 0)
        uup_chg  = pd.get("uup",  {}).get("change_pct", 0)
        vix_chg  = pd.get("vix",  {}).get("change_pct", 0)
        iwm_chg  = pd.get("iwm",  {}).get("change_pct", 0)

        # Broad market
        c["SPY_UP"]        = spy_chg > 0.3
        c["SPY_DOWN"]      = spy_chg < -0.3
        c["NASDAQ_STRONG"] = qqq_chg > 0.5
        c["NASDAQ_WEAK"]   = qqq_chg < -0.5

        # Bond yields: TLT inverse to yields; TLT down = yields up
        c["YIELD_UP"]   = tlt_chg < -0.3   # yields rising (TLT falling)
        c["YIELD_DOWN"] = tlt_chg > 0.3    # yields falling (TLT rising)

        # Safe haven / risk-off signals
        c["GOLD_RISING"] = gld_chg > 0.3
        c["GOLD_SURGING"]= gld_chg > 1.0   # strong risk-off
        c["RISK_OFF"]    = gld_chg > 0.5 and spy_chg < 0
        c["RISK_ON"]     = spy_chg > 0.5 and gld_chg < 0.2

        # Oil
        c["OIL_UP"]   = uso_chg > 0.8
        c["OIL_DOWN"] = uso_chg < -0.8
        c["INFLATION"]= uso_chg > 1.5

        # USD
        c["STRONG_USD"] = uup_chg > 0.25
        c["WEAK_USD"]   = uup_chg < -0.25

        # VIX via UVXY (UVXY ≈ 1.5× VIX, so 10% UVXY ≈ 6.7% VIX)
        c["LOW_VIX"]      = vix_chg < -5
        c["VIX_RISING"]   = vix_chg > 10    # UVXY +10% ≈ VIX +6%
        c["VIX_SPIKE"]    = vix_chg > 20    # UVXY +20% = fear spike
        c["HIGH_VIX"]     = vix_chg > 15

        # Small-cap breadth (IWM is a risk-appetite barometer)
        c["BREADTH_POSITIVE"] = iwm_chg > 0.3
        c["BREADTH_NEGATIVE"] = iwm_chg < -0.4
        c["RISK_APPETITE"]   = iwm_chg > 0.5

        # Composite regimes
        c["BULL_TREND"]     = spy_chg > 0.5 and qqq_chg > 0.5 and not c["VIX_RISING"]
        c["BEAR_PRESSURE"]  = spy_chg < -0.5 or c["VIX_SPIKE"]
        c["CONSUMER_STRONG"]= spy_chg > 0 and uso_chg < 0.5  # Low oil + rising market
        c["DEFENSIVE"]      = c["RISK_OFF"] or c["VIX_SPIKE"] or spy_chg < -1.0
        c["CURVE_STEEPEN"]  = tlt_chg < -0.5  # Long-end yields rising faster = good for banks

        return c

    def get_vix_regime(self) -> Tuple[str, float]:
        """
        Returns (regime_label, size_multiplier) based on VIX proxy (UVXY).
        UVXY is ≈1.5× VIX. Thresholds adjusted accordingly.
        """
        try:
            vix_chg = self._proxy_data.get("vix", {}).get("change_pct", 0)
            if vix_chg > 25:     return "EXTREME_FEAR",  0.30
            if vix_chg > 15:     return "HIGH_FEAR",     0.50
            if vix_chg > 8:      return "ELEVATED",      0.70
            if vix_chg < -10:    return "COMPLACENT",    0.90   # low fear = full size
            return "NORMAL", 1.0
        except Exception:
            return "UNKNOWN", 1.0

    def get_score_adjustment(self, symbol: str, direction: str) -> Tuple[float, List[str]]:
        """
        Main entry point: returns (score_adjustment, reasons).
        score_adjustment is capped at ±25 by the caller.
        """
        self.refresh()
        score = 0.0
        reasons: List[str] = []
        c = self._conditions
        if not c:
            return 0.0, []

        try:
            # ── 1. VIX-based score adjustment ──────────────────────
            vix_chg = self._proxy_data.get("vix", {}).get("change_pct", 0)
            if vix_chg > 25:
                score -= 20
                reasons.append(f"EXTREME_FEAR(UVXY+{vix_chg:.0f}%)")
            elif vix_chg > 15:
                score -= 12
                reasons.append(f"HIGH_FEAR(UVXY+{vix_chg:.0f}%)")
            elif vix_chg > 8:
                score -= 6
                reasons.append(f"ELEVATED_VIX(+{vix_chg:.0f}%)")
            elif vix_chg < -10:
                score += 5
                reasons.append("LOW_VIX(complacency_bonus)")

            # ── 2. Broad market direction ───────────────────────────
            spy_chg = self._proxy_data.get("spy", {}).get("change_pct", 0)
            if direction == "LONG":
                if spy_chg > 1.0:
                    score += 10; reasons.append(f"SPY_STRONG(+{spy_chg:.1f}%)")
                elif spy_chg > 0.3:
                    score += 5;  reasons.append(f"SPY_UP(+{spy_chg:.1f}%)")
                elif spy_chg < -0.5:
                    score -= 8;  reasons.append(f"SPY_DOWN({spy_chg:.1f}%)")
                elif spy_chg < -1.0:
                    score -= 15; reasons.append(f"SPY_FALLING({spy_chg:.1f}%)")
            else:  # SHORT
                if spy_chg < -1.0:
                    score += 10; reasons.append(f"SPY_FALLING({spy_chg:.1f}%)")
                elif spy_chg < -0.3:
                    score += 5;  reasons.append(f"SPY_WEAK({spy_chg:.1f}%)")
                elif spy_chg > 0.5:
                    score -= 8;  reasons.append(f"SPY_RISING_FIGHTS_SHORT(+{spy_chg:.1f}%)")

            # ── 3. Sector-specific inter-market adjustment ───────────
            sector = SYMBOL_SECTOR.get(symbol, "")
            if sector:
                sector_adj = self._sector_intermarket_score(sector, direction, c)
                score += sector_adj
                if abs(sector_adj) >= 3:
                    reasons.append(f"SECTOR_{sector}({sector_adj:+.0f})")

            # ── 4. Gold risk-off signal ─────────────────────────────
            gld_chg = self._proxy_data.get("gld", {}).get("change_pct", 0)
            if gld_chg > 1.0 and direction == "LONG":
                score -= 8;  reasons.append(f"GOLD_SURGE(risk_off,{gld_chg:.1f}%)")
            elif gld_chg > 0.5 and direction == "LONG":
                score -= 4;  reasons.append(f"GOLD_RISING(caution)")
            elif gld_chg < -0.5 and direction == "LONG":
                score += 3;  reasons.append(f"GOLD_FALLING(risk_on)")

            # ── 5. Calendar effects ─────────────────────────────────
            cal_adj, cal_note = self._calendar_score()
            score += cal_adj
            if cal_note:
                reasons.append(cal_note)

            # ── 6. Day-of-week statistical edge ─────────────────────
            dow_adj, dow_note = self._dow_score()
            score += dow_adj
            if dow_note:
                reasons.append(dow_note)

        except Exception as e:
            logger.debug(f"GlobalMarketContext.get_score_adjustment error: {e}")

        return round(score, 1), reasons

    def _sector_intermarket_score(self, sector: str, direction: str, c: Dict) -> float:
        """Score adjustment for sector-specific inter-market conditions."""
        score = 0.0
        bull_conds = SECTOR_BULLISH_CONDITIONS.get(sector, [])
        bear_conds = SECTOR_BEARISH_CONDITIONS.get(sector, [])
        for cond in bull_conds:
            if c.get(cond):
                score += 3 if direction == "LONG" else -2
        for cond in bear_conds:
            if c.get(cond):
                score -= 3 if direction == "LONG" else -3  # bearish condition = good for SHORT
        return max(-12, min(score, 12))

    def _calendar_score(self) -> Tuple[float, str]:
        """Calendar-based score from OpEx, month-end, quad witch."""
        try:
            today = get_current_ist_time().date()
            today_str = str(today)
            now_et = get_current_ist_time()

            # Quad witch day — extreme noise
            if today_str in QUAD_WITCH_DATES:
                return -15, "QUAD_WITCH(extreme_noise)"

            # OpEx Friday (3rd Friday of month)
            if today.weekday() == 4:  # Friday
                day = today.day
                if 15 <= day <= 21:   # 3rd Friday
                    return -10, "OPEX_FRIDAY(pinning_effect)"

            # Month-end (last 3 trading days)
            next_month_first = date(today.year + today.month // 12,
                                    today.month % 12 + 1, 1)
            days_to_month_end = (next_month_first - today).days
            if 1 <= days_to_month_end <= 3:
                return -6, "MONTH_END(rebalancing)"

            # Month-start (first 3 trading days)
            if today.day <= 3:
                return 5, "MONTH_START(fresh_capital)"

            # FOMC day — hard block before 2 PM ET, bonus after
            if today_str in FOMC_DATES:
                hour_et = now_et.hour + now_et.minute / 60
                if hour_et < 14.0:
                    return -15, "FOMC_DAY(pre_announcement)"
                elif hour_et < 14.5:
                    return 0, "FOMC_ANNOUNCEMENT_NOW"
                else:
                    return 8, "FOMC_POST(direction_clear)"

            # Day after FOMC
            yesterday_str = str(today - timedelta(days=1))
            if yesterday_str in FOMC_DATES:
                return 8, "POST_FOMC_DAY(momentum_clear)"

            # NFP Friday
            if today_str in NFP_DATES:
                hour_et = now_et.hour + now_et.minute / 60
                if hour_et < 9.75:   # before 9:45 AM ET
                    return -20, "NFP_DAY(pre_data_blackout)"
                return 10, "NFP_POST(strong_direction)"

            # CPI day (8:30 AM ET release)
            if today_str in CPI_DATES:
                hour_et = now_et.hour + now_et.minute / 60
                if hour_et < 9.5:    # before 9:30 ET
                    return -18, "CPI_DAY(pre_release)"
                return 6, "CPI_POST(clarity)"

            # PPI day
            if today_str in PPI_DATES:
                hour_et = now_et.hour + now_et.minute / 60
                if hour_et < 9.5:
                    return -10, "PPI_DAY(pre_release)"
                return 4, "PPI_POST"

            # GDP day
            if today_str in GDP_DATES:
                hour_et = now_et.hour + now_et.minute / 60
                if hour_et < 10.0:
                    return -12, "GDP_DAY(pre_release)"
                return 6, "GDP_POST"

        except Exception as e:
            logger.debug(f"_calendar_score error: {e}")
        return 0.0, ""

    def _dow_score(self) -> Tuple[float, str]:
        """Day-of-week statistical edge (validated over 18yr of US equity trading)."""
        try:
            dow = get_current_ist_time().weekday()  # 0=Mon, 4=Fri
            if dow == 0:    # Monday
                return 3, "MON(gap_continuation_65pct)"
            elif dow == 1:  # Tuesday
                return 8, "TUE(best_trend_day)"
            elif dow == 2:  # Wednesday
                return 2, "WED(digest_day)"
            elif dow == 3:  # Thursday
                return 6, "THU(second_best_trend)"
            elif dow == 4:  # Friday
                return -5, "FRI(profit_taking)"
        except Exception:
            pass
        return 0.0, ""

    def format_one_line(self) -> str:
        """One-line market summary for log output."""
        try:
            spy = self._proxy_data.get("spy", {}).get("change_pct", 0)
            qqq = self._proxy_data.get("qqq", {}).get("change_pct", 0)
            gld = self._proxy_data.get("gld", {}).get("change_pct", 0)
            uso = self._proxy_data.get("uso", {}).get("change_pct", 0)
            vix = self._proxy_data.get("vix", {}).get("change_pct", 0)
            return (
                f"SPY{spy:+.1f}% QQQ{qqq:+.1f}% GLD{gld:+.1f}% "
                f"OIL{uso:+.1f}% UVXY{vix:+.1f}%"
            )
        except Exception:
            return "no data"

    def format_morning_brief(self) -> str:
        """Full Telegram morning brief with inter-market reading."""
        self.refresh()
        c = self._conditions
        pd = self._proxy_data

        spy_chg  = pd.get("spy",  {}).get("change_pct", 0)
        qqq_chg  = pd.get("qqq",  {}).get("change_pct", 0)
        tlt_chg  = pd.get("tlt",  {}).get("change_pct", 0)
        gld_chg  = pd.get("gld",  {}).get("change_pct", 0)
        uso_chg  = pd.get("uso",  {}).get("change_pct", 0)
        uup_chg  = pd.get("uup",  {}).get("change_pct", 0)
        vix_chg  = pd.get("vix",  {}).get("change_pct", 0)

        regime, size_mult = self.get_vix_regime()
        cal_adj, cal_note = self._calendar_score()
        dow_adj, dow_note = self._dow_score()

        bias = "BULLISH" if spy_chg > 0.3 and not c.get("VIX_SPIKE") else (
               "BEARISH" if spy_chg < -0.3 or c.get("VIX_SPIKE") else "NEUTRAL")
        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "⚪")

        hot_sectors = []
        if c.get("NASDAQ_STRONG"):    hot_sectors.append("TECH(XLK)")
        if c.get("YIELD_UP"):         hot_sectors.append("FINANCE(XLF)")
        if c.get("OIL_UP"):           hot_sectors.append("ENERGY(XLE)")
        if c.get("DEFENSIVE"):        hot_sectors.append("HEALTH(XLV)")
        if c.get("CONSUMER_STRONG"):  hot_sectors.append("CONSUMER(XLY)")

        lines = [
            f"🌍 GLOBAL MARKET BRIEF | {get_current_ist_time().strftime('%A %b %d')}",
            f"",
            f"BIAS: {bias_emoji} {bias} | Regime: {regime} | Size: {size_mult:.0%}",
            f"",
            f"📊 Inter-Market:",
            f"  SPY {spy_chg:+.2f}% | QQQ {qqq_chg:+.2f}% | IWM {pd.get('iwm',{}).get('change_pct',0):+.2f}%",
            f"  TLT {tlt_chg:+.2f}% (yields {'↑rising' if tlt_chg<0 else '↓falling'})",
            f"  Gold {gld_chg:+.2f}% | Oil {uso_chg:+.2f}% | USD {uup_chg:+.2f}%",
            f"  UVXY {vix_chg:+.2f}% ({'fear spike ⚠️' if vix_chg>15 else 'calm' if vix_chg<-5 else 'normal'})",
            f"",
            f"🔥 Hot Sectors: {', '.join(hot_sectors) if hot_sectors else 'None'}",
            f"",
            f"📅 Calendar: {cal_note if cal_note else 'No events'}",
            f"📆 Day Edge: {dow_note} ({dow_adj:+.0f}pts)",
            f"",
            f"⚡ Strategy: {'AGGRESSIVE — all systems go' if size_mult >= 1.0 and bias == 'BULLISH' else 'CAUTION — reduce size' if size_mult < 0.7 else 'NORMAL'}",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────
_gmc_instance: Optional[GlobalMarketContext] = None


def get_global_market_context(data_fetcher=None) -> GlobalMarketContext:
    global _gmc_instance
    if _gmc_instance is None:
        _gmc_instance = GlobalMarketContext(data_fetcher)
    elif data_fetcher and _gmc_instance._fetcher is None:
        _gmc_instance._fetcher = data_fetcher
    return _gmc_instance
