"""
overnight_analyzer.py — NSE Momentum Groww AI Bot
Overnight & Pre-Market Intelligence Engine

Runs at 8:00–8:45 AM IST BEFORE market opens.
Fetches global market cues and builds today's trading bias.

What it checks:
1. US markets close (S&P500, Nasdaq, Dow) — biggest influence on NSE
2. Asian markets (Nikkei, Hang Seng, SGX/Gift Nifty) — morning cues
3. Crude oil price — impacts ONGC, BPCL, RELIANCE, aviation stocks
4. Gold price — risk-off indicator
5. USD/INR — FII flows indicator
6. Gift Nifty futures — direct NSE open predictor
7. VIX India — fear gauge (high VIX = volatile, dangerous day)
8. Global news headlines from Reuters/Bloomberg

18yr Rule: "The night before a trade is as important as the trade itself.
Know what happened globally BEFORE you place a single order."
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

OVERNIGHT_DIR = Path("logs/overnight")
OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)


class OvernightAnalyzer:
    """
    Fetches and analyses global overnight data to build NSE morning bias.
    Runs daily at 8:00 AM IST (before Groww login at 8:45 AM IST).
    """

    def __init__(self):
        self._today_analysis: Optional[Dict] = None
        self._cache_path = OVERNIGHT_DIR / f"analysis_{get_current_ist_date()}.json"
        # Try loading existing analysis for today
        if self._cache_path.exists():
            try:
                with open(self._cache_path) as f:
                    self._today_analysis = json.load(f)
                logger.info(f"[{format_ist_timestamp()}] Loaded cached overnight analysis")
            except Exception:
                pass

    # --------------------------------------------------------
    # MAIN ANALYSIS RUNNER
    # --------------------------------------------------------

    def run(self, ai_brain=None) -> Dict:
        """
        Full overnight analysis. Returns comprehensive bias dict.
        Call at 8:00–8:30 AM IST. Results cached for the day.
        """
        logger.info(f"[{format_ist_timestamp()}] Running overnight analysis...")

        result = {
            "date_ist":       str(get_current_ist_date()),
            "generated_at":   format_ist_timestamp(),
            "global_markets": {},
            "gift_nifty":     {},
            "commodities":    {},
            "fx":             {},
            "vix":            {},
            "gap_analysis":   {},
            "day_bias":       "NEUTRAL",
            "bias_score":     0,      # -100 to +100 (negative=bearish, positive=bullish)
            "confidence":     50,
            "key_risks":      [],
            "sectors_favour": [],
            "sectors_avoid":  [],
            "ai_thesis":      "",
            "trade_advice":   "",
        }

        bias_score = 0

        # 1. Fetch global market data
        global_data = self._fetch_global_markets()
        result["global_markets"] = global_data

        # Score global markets
        sp500  = global_data.get("sp500_change",   0)
        nasdaq = global_data.get("nasdaq_change",  0)
        nikkei = global_data.get("nikkei_change",  0)
        hsi    = global_data.get("hangseng_change",0)

        if sp500 > 0.5:    bias_score += 15
        elif sp500 < -0.5: bias_score -= 20   # US fall hits NSE harder than US rise helps
        if nasdaq > 0.5:   bias_score += 10
        elif nasdaq < -1:  bias_score -= 15
        if nikkei > 0.5:   bias_score += 8
        elif nikkei < -1:  bias_score -= 10
        if hsi > 0:        bias_score += 5
        elif hsi < -1:     bias_score -= 8

        # 2. Gift Nifty (most direct predictor)
        gift = self._fetch_gift_nifty()
        result["gift_nifty"] = gift
        gap_pct = gift.get("gap_pct", 0)
        if gap_pct > 0.5:    bias_score += 20
        elif gap_pct > 0.2:  bias_score += 10
        elif gap_pct < -0.5: bias_score -= 25
        elif gap_pct < -0.2: bias_score -= 12

        # Gap analysis (extreme gaps often fade)
        if abs(gap_pct) > 1.5:
            result["gap_analysis"] = {
                "type":   "LARGE_GAP",
                "pct":    gap_pct,
                "advice": (
                    "Gap >1.5%: Wait 15-20 min for equilibrium. "
                    "Trade WITH gap after pullback, not immediately at open."
                    if gap_pct > 0 else
                    "Gap down >1.5%: Don't short immediately at open — wait for bounce/rejection first."
                )
            }
            result["key_risks"].append(f"Large gap {'up' if gap_pct > 0 else 'down'} {gap_pct:+.1f}% — choppy open likely")
        else:
            result["gap_analysis"] = {
                "type":   "NORMAL_OPEN",
                "pct":    gap_pct,
                "advice": "Normal open. Wait for ORB (9:15-9:30 AM) to set direction."
            }

        # 3. Commodities
        comms = self._fetch_commodities()
        result["commodities"] = comms
        crude = comms.get("crude_change", 0)
        gold  = comms.get("gold_change", 0)

        if crude > 2:
            bias_score -= 5   # High crude = bad for import-heavy India
            result["sectors_avoid"].append("AVIATION")
            result["sectors_favour"].append("ENERGY")
            result["key_risks"].append(f"Crude up {crude:+.1f}% — inflation risk, avoid aviation/paints")
        elif crude < -2:
            bias_score += 5
            result["sectors_favour"].append("AVIATION")
            result["sectors_avoid"].append("ENERGY")

        if gold > 1:
            result["key_risks"].append("Gold up — risk-off signal. Reduce position sizes.")
            bias_score -= 5

        # 4. USD/INR (Rupee strength)
        fx = self._fetch_fx()
        result["fx"] = fx
        usdinr_change = fx.get("usdinr_change", 0)
        if usdinr_change > 0.3:   # Rupee weakening (bad for FII inflows)
            bias_score -= 8
            result["key_risks"].append(f"Rupee weakening ({usdinr_change:+.2f}%) — FII may sell")
        elif usdinr_change < -0.3:  # Rupee strengthening (good)
            bias_score += 5

        # 5. India VIX
        vix_data = self._fetch_india_vix()
        result["vix"] = vix_data
        vix = vix_data.get("vix", 15)
        if vix > 22:
            result["key_risks"].append(f"India VIX = {vix:.1f} (HIGH) — use 50% position size today")
            bias_score -= 10
        elif vix > 18:
            result["key_risks"].append(f"India VIX = {vix:.1f} (ELEVATED) — widen stops")
            bias_score -= 5
        elif vix < 12:
            result["key_risks"].append(f"India VIX = {vix:.1f} (LOW) — low volatility, fewer signals")

        # 6. Final bias
        result["bias_score"] = bias_score
        if bias_score >= 20:
            result["day_bias"]    = "BULLISH"
            result["confidence"]  = min(50 + bias_score, 90)
            result["trade_advice"] = "Prefer LONG setups. Buy dips toward VWAP. Avoid shorts."
        elif bias_score <= -20:
            result["day_bias"]    = "BEARISH"
            result["confidence"]  = min(50 + abs(bias_score), 90)
            result["trade_advice"] = "Prefer SHORT setups. Sell rallies. Reduce position sizes."
        else:
            result["day_bias"]    = "NEUTRAL"
            result["confidence"]  = 50
            result["trade_advice"] = "No clear bias. Wait for ORB. Trade only high-confidence setups (score > 75)."

        # 7. AI thesis (if available)
        if ai_brain:
            try:
                asian = {
                    "Nikkei": nikkei,
                    "HangSeng": hsi,
                    "SGX/Gift": gap_pct,
                }
                thesis = ai_brain.generate_morning_thesis(
                    nifty_prev_close = gift.get("prev_close", 0),
                    gift_nifty       = gift.get("gift_nifty", 0),
                    us_market_change = sp500,
                    asian_markets    = asian,
                    top_news         = self._fetch_top_headlines(),
                    economic_events  = [],
                )
                result["ai_thesis"]   = thesis.get("thesis", "")
                result["day_bias"]    = thesis.get("bias", result["day_bias"])
                result["confidence"]  = thesis.get("confidence", result["confidence"])
                # Merge AI sector recommendations
                for s in thesis.get("sectors_buy", []):
                    if s not in result["sectors_favour"]:
                        result["sectors_favour"].append(s)
                for s in thesis.get("sectors_avoid", []):
                    if s not in result["sectors_avoid"]:
                        result["sectors_avoid"].append(s)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] AI thesis failed: {e}")

        # Cache result
        self._today_analysis = result
        try:
            with open(self._cache_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
        except Exception:
            pass

        self._log_summary(result)
        return result

    # --------------------------------------------------------
    # DATA FETCHERS (using yfinance as free data source)
    # --------------------------------------------------------

    def _fetch_global_markets(self) -> Dict:
        """Fetch US + Asian market data via yfinance."""
        result = {}
        try:
            import yfinance as yf
            tickers = {
                "sp500":   "^GSPC",
                "nasdaq":  "^IXIC",
                "dow":     "^DJI",
                "nikkei":  "^N225",
                "hangseng":"^HSI",
                "ftse":    "^FTSE",
            }
            for name, ticker in tickers.items():
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev  = float(data["Close"].iloc[-2].item() if hasattr(data["Close"].iloc[-2], 'item') else data["Close"].iloc[-2])
                        last  = float(data["Close"].iloc[-1].item() if hasattr(data["Close"].iloc[-1], 'item') else data["Close"].iloc[-1])
                        chg   = (last - prev) / prev * 100
                        result[f"{name}_close"]  = round(last, 2)
                        result[f"{name}_change"] = round(chg, 2)
                except Exception:
                    result[f"{name}_change"] = 0
        except ImportError:
            logger.warning("yfinance not installed. pip install yfinance")
            result = {"sp500_change": 0, "nasdaq_change": 0, "nikkei_change": 0, "hangseng_change": 0}
        return result

    def _fetch_gift_nifty(self) -> Dict:
        """
        Gift Nifty (formerly SGX Nifty) — best pre-market Nifty predictor.
        Available from ~6 AM IST. Uses yfinance as proxy (Nifty futures).
        """
        result = {"gift_nifty": 0, "prev_close": 0, "gap_pct": 0}
        try:
            import yfinance as yf
            # Use Nifty50 index as proxy since Gift Nifty isn't on yfinance
            nifty = yf.download("^NSEI", period="5d", interval="1d",
                                 progress=False, auto_adjust=True)
            if len(nifty) >= 1:
                prev_close = float(nifty["Close"].iloc[-1].item() if hasattr(nifty["Close"].iloc[-1], 'item') else nifty["Close"].iloc[-1])
                result["prev_close"] = round(prev_close, 2)
                result["gift_nifty"] = round(prev_close, 2)  # Placeholder until Gift Nifty API

                # Try to get Gift Nifty from investing.com via requests
                try:
                    import requests
                    resp = requests.get(
                        "https://query1.finance.yahoo.com/v8/finance/chart/NIFTY50.NS",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=5
                    )
                    if resp.status_code == 200:
                        data  = resp.json()
                        price = data["chart"]["result"][0]["meta"].get("regularMarketPrice", prev_close)
                        result["gift_nifty"] = round(float(price), 2)
                        gap   = (float(price) - prev_close) / prev_close * 100
                        result["gap_pct"] = round(gap, 2)
                except Exception:
                    result["gap_pct"] = 0
        except Exception as e:
            logger.debug(f"Gift Nifty fetch failed: {e}")
        return result

    def _fetch_commodities(self) -> Dict:
        """Fetch crude oil (Brent) and gold prices."""
        result = {"crude_change": 0, "gold_change": 0, "crude_price": 0, "gold_price": 0}
        try:
            import yfinance as yf
            for name, ticker in [("crude", "BZ=F"), ("gold", "GC=F")]:
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev = float(data["Close"].iloc[-2].item() if hasattr(data["Close"].iloc[-2], 'item') else data["Close"].iloc[-2])
                        last = float(data["Close"].iloc[-1].item() if hasattr(data["Close"].iloc[-1], 'item') else data["Close"].iloc[-1])
                        result[f"{name}_price"]  = round(last, 2)
                        result[f"{name}_change"] = round((last - prev) / prev * 100, 2)
                except Exception:
                    pass
        except ImportError:
            pass
        return result

    def _fetch_fx(self) -> Dict:
        """Fetch USD/INR exchange rate."""
        result = {"usdinr": 0, "usdinr_change": 0}
        try:
            import yfinance as yf
            data = yf.download("INR=X", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2].item() if hasattr(data["Close"].iloc[-2], 'item') else data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1].item() if hasattr(data["Close"].iloc[-1], 'item') else data["Close"].iloc[-1])
                result["usdinr"]        = round(last, 4)
                result["usdinr_change"] = round((last - prev) / prev * 100, 3)
        except Exception as e:
            logger.debug(f"FX fetch failed: {e}")
        return result

    def _fetch_india_vix(self) -> Dict:
        """Fetch India VIX — volatility fear gauge."""
        result = {"vix": 15, "vix_change": 0}
        try:
            import yfinance as yf
            data = yf.download("^INDIAVIX", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2].item() if hasattr(data["Close"].iloc[-2], 'item') else data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1].item() if hasattr(data["Close"].iloc[-1], 'item') else data["Close"].iloc[-1])
                result["vix"]        = round(last, 2)
                result["vix_change"] = round(last - prev, 2)
            elif len(data) == 1:
                result["vix"] = round(float(data["Close"].iloc[-1].item() if hasattr(data["Close"].iloc[-1], 'item') else data["Close"].iloc[-1]), 2)
        except Exception as e:
            logger.debug(f"VIX fetch failed: {e}")
        return result

    def _fetch_top_headlines(self) -> List[str]:
        """Fetch top market headlines from NewsAPI."""
        headlines = []
        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key:
            return ["No news API key configured"]
        try:
            import requests
            resp = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "q": "NSE OR Nifty OR RBI OR India stock market",
                    "language": "en",
                    "pageSize": 5,
                    "apiKey": api_key,
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
    # QUERY HELPERS (used during trading day)
    # --------------------------------------------------------

    def get_day_bias(self) -> str:
        """Current day's bias: BULLISH / BEARISH / NEUTRAL."""
        if self._today_analysis:
            return self._today_analysis.get("day_bias", "NEUTRAL")
        return "NEUTRAL"

    def get_bias_score(self) -> int:
        """Raw bias score from -100 to +100."""
        if self._today_analysis:
            return self._today_analysis.get("bias_score", 0)
        return 0

    def get_vix(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("vix", {}).get("vix", 15)
        return 15

    def get_gap_pct(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("gift_nifty", {}).get("gap_pct", 0)
        return 0

    def get_size_multiplier(self) -> float:
        """
        Position size multiplier based on overnight risk.
        High VIX or large gap = smaller size.
        """
        vix  = self.get_vix()
        bias = abs(self.get_bias_score())

        if vix > 22:
            return 0.5   # Very dangerous — half size
        elif vix > 18:
            return 0.7
        elif bias > 30:
            return 1.1   # Strong overnight signal — slight boost
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
        bias   = a.get("day_bias", "NEUTRAL")
        score  = a.get("bias_score", 0)
        conf   = a.get("confidence", 50)
        gm     = a.get("global_markets", {})
        vix    = a.get("vix", {}).get("vix", 15)
        gap    = a.get("gift_nifty", {}).get("gap_pct", 0)
        crude  = a.get("commodities", {}).get("crude_change", 0)
        usdinr = a.get("fx", {}).get("usdinr_change", 0)
        risks  = a.get("key_risks", [])
        advice = a.get("trade_advice", "")
        thesis = a.get("ai_thesis", "")

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")

        msg = (
            f"🌅 MORNING BRIEF — {get_current_ist_date()}\n"
            f"{'─'*38}\n"
            f"{bias_emoji} Bias: {bias} (Score: {score:+d}, Conf: {conf}%)\n"
            f"🎯 Gift Nifty Gap: {gap:+.2f}%\n"
            f"🌍 US: S&P {gm.get('sp500_change',0):+.1f}%  Nasdaq {gm.get('nasdaq_change',0):+.1f}%\n"
            f"🗾 Asia: Nikkei {gm.get('nikkei_change',0):+.1f}%  HangSeng {gm.get('hangseng_change',0):+.1f}%\n"
            f"🛢 Crude: {crude:+.1f}%  💵 USD/INR: {usdinr:+.3f}%  😨 VIX: {vix:.1f}\n"
        )
        if risks:
            msg += f"⚠️ Risks:\n" + "\n".join(f"  • {r}" for r in risks[:3]) + "\n"
        msg += f"💡 {advice}\n"
        if thesis:
            msg += f"\n🧠 AI: {thesis[:200]}"
        return msg

    def _log_summary(self, result: Dict):
        logger.info(
            f"[{format_ist_timestamp()}] Overnight: "
            f"bias={result['day_bias']}({result['bias_score']:+d}) "
            f"gap={result.get('gift_nifty',{}).get('gap_pct',0):+.1f}% "
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
