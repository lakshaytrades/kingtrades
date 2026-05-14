"""
overnight_analyzer.py — US Momentum Alpaca AI Bot
Pre-Market Intelligence Engine

Runs before 9:30 AM ET to build today's US market trading bias.

What it checks:
1. S&P500, Nasdaq, Dow overnight futures/close
2. US VIX (fear gauge)
3. SPY pre-market gap vs previous close
4. Crude oil + Gold prices
5. USD Index (DXY) as risk indicator
6. Top market headlines (if NewsAPI key set)

18yr Rule: "The night before a trade is as important as the trade itself."
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from utils import (
    format_et_timestamp, get_current_et_time, get_current_et_date,
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
)

logger = logging.getLogger(__name__)

OVERNIGHT_DIR = Path("logs/overnight")
OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)


class OvernightAnalyzer:
    """
    Fetches and analyses US overnight / pre-market data to build morning bias.
    Runs daily before 9:30 AM ET (NYSE open).
    """

    def __init__(self):
        self._today_analysis: Optional[Dict] = None
        self._cache_path = OVERNIGHT_DIR / f"analysis_{get_current_et_date()}.json"
        try:
            if self._cache_path.exists():
                with open(self._cache_path) as f:
                    self._today_analysis = json.load(f)
                logger.info(f"[{format_et_timestamp()}] Loaded cached overnight analysis")
        except Exception:
            pass

    def run_analysis(self, ai_brain=None) -> Dict:
        """Run full pre-market analysis and return structured result."""
        result = {
            "date":           str(get_current_et_date()),
            "day_bias":       "NEUTRAL",
            "bias_score":     0,
            "confidence":     50,
            "key_risks":      [],
            "sectors_favour": [],
            "sectors_avoid":  [],
            "ai_thesis":      "",
            "trade_advice":   "",
        }

        bias_score = 0

        # 1. US market close + futures
        global_data = self._fetch_global_markets()
        result["global_markets"] = global_data

        sp500  = global_data.get("sp500_change",  0)
        nasdaq = global_data.get("nasdaq_change", 0)
        dow    = global_data.get("dow_change",    0)

        if sp500 > 0.5:    bias_score += 15
        elif sp500 < -0.5: bias_score -= 20
        if nasdaq > 0.5:   bias_score += 10
        elif nasdaq < -1:  bias_score -= 15
        if dow > 0.3:      bias_score += 5
        elif dow < -0.5:   bias_score -= 8

        # 2. SPY pre-market gap (best US open predictor)
        spy_data = self._fetch_spy_gap()
        result["spy_gap"] = spy_data
        gap_pct = spy_data.get("gap_pct", 0)

        if gap_pct > 0.5:    bias_score += 20
        elif gap_pct > 0.2:  bias_score += 10
        elif gap_pct < -0.5: bias_score -= 25
        elif gap_pct < -0.2: bias_score -= 12

        if abs(gap_pct) > 1.5:
            result["gap_analysis"] = {
                "type":   "LARGE_GAP",
                "pct":    gap_pct,
                "advice": (
                    "Gap >1.5%: Wait 5-10 min after open for equilibrium. "
                    "Trade WITH gap after pullback, not immediately at open."
                    if gap_pct > 0 else
                    "Gap down >1.5%: Don't short immediately at open — wait for bounce/rejection first."
                )
            }
            result["key_risks"].append(
                f"Large gap {'up' if gap_pct > 0 else 'down'} {gap_pct:+.1f}% — choppy open likely"
            )
        else:
            result["gap_analysis"] = {
                "type":   "NORMAL_OPEN",
                "pct":    gap_pct,
                "advice": "Normal open. Wait for ORB (9:30-9:45 AM ET) to set direction."
            }

        # 3. Commodities
        comms = self._fetch_commodities()
        result["commodities"] = comms
        crude = comms.get("crude_change", 0)
        gold  = comms.get("gold_change",  0)

        if crude > 2:
            bias_score -= 5
            result["sectors_avoid"].append("AIRLINES")
            result["sectors_favour"].append("ENERGY")
            result["key_risks"].append(f"Crude up {crude:+.1f}% — inflation risk, avoid airlines/transports")
        elif crude < -2:
            bias_score += 5
            result["sectors_favour"].append("AIRLINES")
            result["sectors_avoid"].append("ENERGY")

        if gold > 1:
            result["key_risks"].append("Gold up — risk-off signal. Reduce position sizes.")
            bias_score -= 5

        # 4. USD Index (DXY) — strong dollar = pressure on commodities/multinationals
        dxy = self._fetch_dxy()
        result["dxy"] = dxy
        dxy_change = dxy.get("dxy_change", 0)
        if dxy_change > 0.5:
            bias_score -= 5
            result["key_risks"].append(f"Dollar strengthening ({dxy_change:+.2f}%) — watch multinationals")
        elif dxy_change < -0.5:
            bias_score += 5

        # 5. US VIX (fear gauge)
        vix_data = self._fetch_us_vix()
        result["vix"] = vix_data
        vix = vix_data.get("vix", 15)

        if vix > 25:
            result["key_risks"].append(f"VIX = {vix:.1f} (HIGH) — use 50% position size today")
            bias_score -= 15
        elif vix > 20:
            result["key_risks"].append(f"VIX = {vix:.1f} (ELEVATED) — widen stops")
            bias_score -= 8
        elif vix < 12:
            result["key_risks"].append(f"VIX = {vix:.1f} (LOW) — low volatility, fewer breakout signals")

        # 6. Final bias
        result["bias_score"] = bias_score
        if bias_score >= 20:
            result["day_bias"]   = "BULLISH"
            result["confidence"] = min(50 + bias_score, 90)
            result["trade_advice"] = "Prefer LONG setups. Buy dips toward VWAP. Avoid shorts."
        elif bias_score <= -20:
            result["day_bias"]   = "BEARISH"
            result["confidence"] = min(50 + abs(bias_score), 90)
            result["trade_advice"] = "Prefer SHORT setups. Sell rallies. Reduce position sizes."
        else:
            result["day_bias"]   = "NEUTRAL"
            result["confidence"] = 50
            result["trade_advice"] = "No clear bias. Wait for ORB. Trade only high-confidence setups (score > 75)."

        # 7. AI thesis
        if ai_brain:
            try:
                thesis = ai_brain.generate_morning_thesis(
                    nifty_prev_close = spy_data.get("prev_close", 0),
                    gift_nifty       = spy_data.get("current", 0),
                    us_market_change = sp500,
                    asian_markets    = {"Nasdaq": nasdaq, "Dow": dow},
                    top_news         = self._fetch_top_headlines(),
                    economic_events  = [],
                )
                result["ai_thesis"]  = thesis.get("thesis", "")
                result["day_bias"]   = thesis.get("bias", result["day_bias"])
                result["confidence"] = thesis.get("confidence", result["confidence"])
                for s in thesis.get("sectors_buy", []):
                    if s not in result["sectors_favour"]:
                        result["sectors_favour"].append(s)
                for s in thesis.get("sectors_avoid", []):
                    if s not in result["sectors_avoid"]:
                        result["sectors_avoid"].append(s)
            except Exception as e:
                logger.warning(f"[{format_et_timestamp()}] AI thesis failed: {e}")

        self._today_analysis = result
        try:
            with open(self._cache_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
        except Exception:
            pass

        self._log_summary(result)
        return result

    # --------------------------------------------------------
    # DATA FETCHERS
    # --------------------------------------------------------

    def _fetch_global_markets(self) -> Dict:
        """Fetch US market overnight data via yfinance."""
        result = {}
        try:
            import yfinance as yf
            tickers = {
                "sp500":  "^GSPC",
                "nasdaq": "^IXIC",
                "dow":    "^DJI",
            }
            for name, ticker in tickers.items():
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev = float(data["Close"].iloc[-2])
                        last = float(data["Close"].iloc[-1])
                        chg  = (last - prev) / prev * 100
                        result[f"{name}_close"]  = round(last, 2)
                        result[f"{name}_change"] = round(chg, 2)
                    else:
                        result[f"{name}_change"] = 0
                except Exception:
                    result[f"{name}_change"] = 0
        except ImportError:
            logger.warning("yfinance not installed: pip install yfinance")
            result = {"sp500_change": 0, "nasdaq_change": 0, "dow_change": 0}
        return result

    def _fetch_spy_gap(self) -> Dict:
        """
        SPY gap vs previous close — best US open predictor.
        Uses 5-min pre-market data when available.
        """
        result = {"current": 0, "prev_close": 0, "gap_pct": 0}
        try:
            import yfinance as yf
            spy = yf.download("SPY", period="5d", interval="1d",
                              progress=False, auto_adjust=True)
            if len(spy) >= 2:
                prev_close = float(spy["Close"].iloc[-2])
                last_close = float(spy["Close"].iloc[-1])
                result["prev_close"] = round(prev_close, 2)
                result["current"]    = round(last_close, 2)
                result["gap_pct"]    = round((last_close - prev_close) / prev_close * 100, 2)

            # Try pre-market price from Yahoo for current gap
            try:
                import requests
                resp = requests.get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/SPY",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=5
                )
                if resp.status_code == 200:
                    data  = resp.json()
                    price = data["chart"]["result"][0]["meta"].get("regularMarketPrice")
                    if price and result["prev_close"] > 0:
                        result["current"] = round(float(price), 2)
                        result["gap_pct"] = round(
                            (float(price) - result["prev_close"]) / result["prev_close"] * 100, 2
                        )
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"SPY gap fetch failed: {e}")
        return result

    def _fetch_commodities(self) -> Dict:
        """Fetch crude oil (WTI) and gold prices."""
        result = {"crude_change": 0, "gold_change": 0, "crude_price": 0, "gold_price": 0}
        try:
            import yfinance as yf
            for name, ticker in [("crude", "CL=F"), ("gold", "GC=F")]:
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev = float(data["Close"].iloc[-2])
                        last = float(data["Close"].iloc[-1])
                        result[f"{name}_price"]  = round(last, 2)
                        result[f"{name}_change"] = round((last - prev) / prev * 100, 2)
                except Exception:
                    pass
        except ImportError:
            pass
        return result

    def _fetch_dxy(self) -> Dict:
        """Fetch US Dollar Index (DXY)."""
        result = {"dxy": 0, "dxy_change": 0}
        try:
            import yfinance as yf
            data = yf.download("DX-Y.NYB", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1])
                result["dxy"]        = round(last, 3)
                result["dxy_change"] = round((last - prev) / prev * 100, 3)
        except Exception as e:
            logger.debug(f"DXY fetch failed: {e}")
        return result

    def _fetch_us_vix(self) -> Dict:
        """Fetch CBOE VIX — US market fear gauge."""
        result = {"vix": 15, "vix_change": 0}
        try:
            import yfinance as yf
            data = yf.download("^VIX", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1])
                result["vix"]        = round(last, 2)
                result["vix_change"] = round(last - prev, 2)
            elif len(data) == 1:
                result["vix"] = round(float(data["Close"].iloc[-1]), 2)
        except Exception as e:
            logger.debug(f"VIX fetch failed: {e}")
        return result

    def _fetch_top_headlines(self) -> List[str]:
        """Fetch top US market headlines (requires NEWS_API_KEY)."""
        headlines = []
        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key:
            return ["No NEWS_API_KEY configured"]
        try:
            import requests
            resp = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "q":        "NYSE OR NASDAQ OR S&P OR Fed OR stock market",
                    "language": "en",
                    "pageSize": 5,
                    "apiKey":   api_key,
                },
                timeout=8
            )
            if resp.status_code == 200:
                articles = resp.json().get("articles", [])
                headlines = [a.get("title", "") for a in articles[:5]]
        except Exception as e:
            logger.debug(f"Headlines fetch failed: {e}")
        return headlines

    # --------------------------------------------------------
    # QUERY HELPERS
    # --------------------------------------------------------

    def get_day_bias(self) -> str:
        if self._today_analysis:
            return self._today_analysis.get("day_bias", "NEUTRAL")
        return "NEUTRAL"

    def get_bias_score(self) -> int:
        if self._today_analysis:
            return self._today_analysis.get("bias_score", 0)
        return 0

    def get_vix(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("vix", {}).get("vix", 15)
        return 15

    def get_gap_pct(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("spy_gap", {}).get("gap_pct", 0)
        return 0

    def get_size_multiplier(self) -> float:
        """Position size multiplier based on overnight risk."""
        vix  = self.get_vix()
        bias = abs(self.get_bias_score())
        if vix > 25:
            return 0.5
        elif vix > 20:
            return 0.7
        elif bias > 30:
            return 1.1
        return 1.0

    def get_sectors_to_favour(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("sectors_favour", [])
        return []

    def get_sectors_to_avoid(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("sectors_avoid", [])
        return []

    def get_key_risks(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("key_risks", [])
        return []

    def format_morning_brief(self) -> str:
        """Telegram-ready morning brief message."""
        if not self._today_analysis:
            return "📊 Overnight analysis not yet available."
        a = self._today_analysis
        bias   = a.get("day_bias",    "NEUTRAL")
        score  = a.get("bias_score",  0)
        conf   = a.get("confidence",  50)
        gm     = a.get("global_markets", {})
        vix    = a.get("vix",         {}).get("vix",      15)
        gap    = a.get("spy_gap",     {}).get("gap_pct",   0)
        crude  = a.get("commodities", {}).get("crude_change", 0)
        dxy    = a.get("dxy",         {}).get("dxy_change",   0)
        risks  = a.get("key_risks",   [])
        advice = a.get("trade_advice", "")
        thesis = a.get("ai_thesis",   "")

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")

        msg = (
            f"🌅 MORNING BRIEF — {get_current_et_date()} ET\n"
            f"{'─'*38}\n"
            f"{bias_emoji} Bias: {bias} (Score: {score:+d}, Conf: {conf}%)\n"
            f"🎯 SPY Gap: {gap:+.2f}%\n"
            f"🌍 S&P {gm.get('sp500_change',0):+.1f}%  Nasdaq {gm.get('nasdaq_change',0):+.1f}%"
            f"  Dow {gm.get('dow_change',0):+.1f}%\n"
            f"🛢 Crude: {crude:+.1f}%  💵 DXY: {dxy:+.3f}%  😨 VIX: {vix:.1f}\n"
        )
        if risks:
            msg += "⚠️ Risks:\n" + "\n".join(f"  • {r}" for r in risks[:3]) + "\n"
        msg += f"💡 {advice}\n"
        if thesis:
            msg += f"\n🧠 AI: {thesis[:200]}"
        return msg

    def _log_summary(self, result: Dict):
        logger.info(
            f"[{format_et_timestamp()}] Overnight: "
            f"bias={result['day_bias']}({result['bias_score']:+d}) "
            f"gap={result.get('spy_gap',{}).get('gap_pct',0):+.1f}% "
            f"VIX={result.get('vix',{}).get('vix',15):.1f} "
            f"US={result.get('global_markets',{}).get('sp500_change',0):+.1f}%"
        )


# Singleton
_analyzer: Optional[OvernightAnalyzer] = None

def get_overnight_analyzer() -> OvernightAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = OvernightAnalyzer()
    return _analyzer
