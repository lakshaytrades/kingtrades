"""
nse_data.py — NSE Momentum Groww AI Bot
Supplementary Institutional & Microstructure Data from NSE Public APIs

No API key required — uses NSE public website endpoints with session cookie.

Data sources:
  1. Bulk Deals     — orders >0.5% of listed equity (smart-money footprint)
  2. Block Deals    — negotiated institutional trades >₹5cr or 500k shares
  3. Delivery %     — delivered qty / traded qty (high % = conviction, not speculation)
  4. FII Futures    — real-time FII long/short in index futures (ahead of equity FII)
  5. 52-Week Range  — breakout momentum context
  6. Short Interest — stock-level F&O OI changes as proxy for short buildup

18yr Rule: "Volume tells you what happened. Delivery tells you who is serious."
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

NSE_BASE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/",
}


class NSEDataFetcher:
    """
    Fetches supplementary institutional and microstructure data from NSE.
    All data is from NSE public endpoints — no API key needed.

    Cache strategy:
      - Bulk/Block deals:   60 min (updated once per day)
      - Delivery data:      60 min
      - FII futures OI:     30 min (more dynamic)
      - 52wk range:         60 min
      - Short interest:     30 min
    """

    # Cache TTLs in seconds
    _CACHE_TTL: Dict[str, int] = {
        "bulk_deals":    3600,
        "block_deals":   3600,
        "fii_futures":   1800,
        "52wk":          3600,
        "delivery":      3600,
        "short_oi":      1800,
    }

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._session_valid = False
        self._cache:       Dict[str, object]  = {}
        self._cache_times: Dict[str, float]   = {}
        self._refresh_session()

    # ── SESSION MANAGEMENT ─────────────────────────────────────

    def _refresh_session(self) -> bool:
        """Warm the NSE session cookie — required before API calls."""
        try:
            r = self._session.get(f"{NSE_BASE}/", timeout=12)
            if r.status_code == 200:
                self._session_valid = True
                return True
        except Exception as e:
            logger.debug(f"NSE session refresh failed: {e}")
        self._session_valid = False
        return False

    def _get_cached(self, key: str) -> Optional[object]:
        ttl = self._CACHE_TTL.get(key, 1800)
        if key in self._cache:
            age = time.time() - self._cache_times.get(key, 0)
            if age < ttl:
                return self._cache[key]
        return None

    def _set_cache(self, key: str, value: object) -> None:
        self._cache[key] = value
        self._cache_times[key] = time.time()

    def _get(self, url: str, cache_key: str = "") -> Optional[dict]:
        """HTTP GET with cache, session auto-refresh, and retry."""
        if cache_key:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        if not self._session_valid:
            self._refresh_session()

        try:
            r = self._session.get(url, timeout=15)
            if r.status_code in (401, 403):
                self._refresh_session()
                r = self._session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if cache_key:
                    self._set_cache(cache_key, data)
                return data
        except Exception as e:
            logger.debug(f"NSE GET failed [{url}]: {e}")
        return None

    # ── BULK DEALS ─────────────────────────────────────────────

    def get_bulk_deals(self, date_str: str = "") -> List[Dict]:
        """
        Bulk Deals: single orders > 0.5% of listed equity.
        Published by NSE by ~6 PM each day.
        Strong signal when same direction (BUY/SELL) repeats across multiple clients.

        Returns list of {symbol, client, buy_sell, quantity, price, value_cr}
        """
        if not date_str:
            date_str = get_current_ist_time().strftime("%d-%m-%Y")

        url = f"{NSE_BASE}/api/bulk-deals?from={date_str}&to={date_str}"
        data = self._get(url, "bulk_deals")
        if not data:
            return []

        raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        result = []
        for d in raw:
            try:
                qty   = int(float(d.get("QUANTITY_TRADED", 0) or 0))
                price = float(d.get("TRADE_PRICE", 0) or 0)
                result.append({
                    "symbol":    str(d.get("SYMBOL", "")).strip().upper(),
                    "client":    str(d.get("CLIENT_NAME", "")),
                    "buy_sell":  str(d.get("BUY_SELL", "")).upper(),
                    "quantity":  qty,
                    "price":     price,
                    "value_cr":  round(qty * price / 1e7, 2),
                    "date":      str(d.get("TRADE_DATE", date_str)),
                })
            except Exception:
                continue
        return result

    # ── BLOCK DEALS ────────────────────────────────────────────

    def get_block_deals(self, date_str: str = "") -> List[Dict]:
        """
        Block Deals: negotiated large trades between institutional parties.
        Threshold: > 500,000 shares OR > ₹5 crore.
        More reliable signal than bulk deals — prearranged between smart money.
        """
        if not date_str:
            date_str = get_current_ist_time().strftime("%d-%m-%Y")

        url = f"{NSE_BASE}/api/block-deals?from={date_str}&to={date_str}"
        data = self._get(url, "block_deals")
        if not data:
            return []

        raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        result = []
        for d in raw:
            try:
                qty   = int(float(d.get("QUANTITY_TRADED", 0) or 0))
                price = float(d.get("TRADE_PRICE", 0) or 0)
                result.append({
                    "symbol":   str(d.get("SYMBOL", "")).strip().upper(),
                    "client":   str(d.get("CLIENT_NAME", "")),
                    "buy_sell": str(d.get("BUY_SELL", "")).upper(),
                    "quantity": qty,
                    "price":    price,
                    "value_cr": round(qty * price / 1e7, 2),
                })
            except Exception:
                continue
        return result

    # ── DELIVERY % ─────────────────────────────────────────────

    def get_delivery_data(self, symbol: str) -> Optional[Dict]:
        """
        Delivery % = delivered shares / total traded shares.

        Interpretation:
          > 60%  → STRONG  — institutions taking delivery, real conviction
          30-60% → NEUTRAL — mix of speculators and investors
          < 30%  → WEAK    — mostly intraday speculation, avoid for momentum

        High delivery on a breakout day = institutional accumulation signal.
        Low delivery on a rally = short covering / speculative froth.
        """
        url = f"{NSE_BASE}/api/quote-equity?symbol={symbol}&section=trade_info"
        data = self._get(url, f"delivery_{symbol}")
        if not data:
            return None

        try:
            # NSE returns different structures depending on endpoint
            ti = (data.get("tradeInfo") or
                  data.get("marketDeptOrderBook", {}).get("tradeInfo") or {})

            total_vol = float(ti.get("totalTradedVolume", 0) or
                              data.get("totalTradedVolume", 0) or 0)
            delivered = float(ti.get("deliveryQuantity", 0) or
                              data.get("deliveryQuantity", 0) or 0)
            delivery_pct = (delivered / total_vol * 100) if total_vol > 0 else 0.0

            signal = (
                "STRONG"  if delivery_pct > 60 else
                "NEUTRAL" if delivery_pct > 30 else
                "WEAK"
            )
            return {
                "symbol":        symbol,
                "delivery_pct":  round(delivery_pct, 1),
                "total_volume":  int(total_vol),
                "delivered_qty": int(delivered),
                "signal":        signal,
            }
        except Exception as e:
            logger.debug(f"Delivery data parse error {symbol}: {e}")
        return None

    # ── FII INDEX FUTURES POSITIONING ──────────────────────────

    def get_fii_futures_positioning(self) -> Optional[Dict]:
        """
        FII index futures positioning — REAL-TIME (updated intraday).
        This is more current than equity FII/DII (which is end-of-day).

        When FII long % > 55% → Institutional bias BULLISH
        When FII long % < 45% → Institutional bias BEARISH

        Returns score -10 to +10 for signal_generator integration.
        """
        url = f"{NSE_BASE}/api/participant-wise-open-interest"
        data = self._get(url, "fii_futures")
        if not data:
            return None

        try:
            rows = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            fii_row = next(
                (r for r in rows if "FII" in str(r.get("clientType", "")).upper()),
                None
            )
            if not fii_row:
                return None

            long_idx  = int(fii_row.get("futureIndexLong",  0) or 0)
            short_idx = int(fii_row.get("futureIndexShort", 0) or 0)
            total     = long_idx + short_idx
            if total == 0:
                return None

            long_pct = long_idx / total * 100
            net      = long_idx - short_idx

            # Score: every 1% above/below 50% = 1 point, capped ±10
            raw_score = (long_pct - 50) / 1.0
            score     = max(-10, min(10, int(raw_score)))

            return {
                "fii_future_long":  long_idx,
                "fii_future_short": short_idx,
                "fii_future_net":   net,
                "fii_long_pct":     round(long_pct, 1),
                "bias":             ("BULLISH" if long_pct > 55 else
                                     "BEARISH" if long_pct < 45 else "NEUTRAL"),
                "score":            score,
                "formatted": (
                    f"FII Futures: {'🟢' if long_pct > 55 else ('🔴' if long_pct < 45 else '⚪')} "
                    f"Long {long_pct:.1f}% ({net:+,} net contracts)"
                ),
            }
        except Exception as e:
            logger.debug(f"FII futures parse error: {e}")
        return None

    # ── 52-WEEK RANGE CONTEXT ───────────────────────────────────

    def get_52wk_context(self, symbol: str) -> Optional[Dict]:
        """
        52-week high/low context for momentum classification.

        Near 52wk high (within 2%):  momentum breakout candidate → +3 to +5 pts
        AT 52wk high (within 0.5%):  breakout in progress → +5 pts
        Near 52wk low (within 5%):   avoid for LONG momentum trades
        """
        url = f"{NSE_BASE}/api/quote-equity?symbol={symbol}"
        data = self._get(url, f"52wk_{symbol}")
        if not data:
            return None

        try:
            pi     = data.get("priceInfo", {}) or {}
            whl    = pi.get("weekHighLow", {}) or {}
            high52 = float(whl.get("max", 0) or 0)
            low52  = float(whl.get("min", 0) or 0)
            ltp    = float(pi.get("lastPrice", 0) or 0)

            if not ltp or not high52 or not low52:
                return None

            pct_from_high = (high52 - ltp) / high52 * 100
            pct_from_low  = (ltp - low52)  / low52  * 100

            return {
                "symbol":           symbol,
                "high_52w":         high52,
                "low_52w":          low52,
                "ltp":              ltp,
                "pct_from_high":    round(pct_from_high, 2),
                "pct_from_low":     round(pct_from_low, 2),
                "at_52wk_high":     pct_from_high <= 0.5,
                "near_52wk_high":   pct_from_high <= 2.0,
                "near_52wk_low":    pct_from_low  <= 5.0,
            }
        except Exception as e:
            logger.debug(f"52wk parse error {symbol}: {e}")
        return None

    # ── STOCK F&O SHORT INTEREST ────────────────────────────────

    def get_short_interest(self, symbol: str) -> Optional[Dict]:
        """
        Stock F&O OI analysis as proxy for short buildup.
        Rising OI + falling price → short buildup (bearish)
        Rising OI + rising price → long buildup (bullish)
        """
        url = f"{NSE_BASE}/api/quote-derivative?symbol={symbol}"
        data = self._get(url, f"short_oi_{symbol}")
        if not data:
            return None

        try:
            stocks = data.get("stocks", []) or []
            # Get near-month futures
            near_futures = [
                s for s in stocks
                if s.get("metadata", {}).get("instrumentType") == "Stock Futures"
            ]
            if not near_futures:
                return None

            nf = near_futures[0].get("marketDeptOrderBook", {}) or {}
            oi_change_pct = float(nf.get("otherInfo", {}).get("changeinOpenInterest", 0) or 0)
            price_change  = float(data.get("info", {}).get("change", 0) or 0)

            # Classify OI + price combination
            if oi_change_pct > 5 and price_change > 0:
                signal = "LONG_BUILDUP"
                score  = 5
            elif oi_change_pct > 5 and price_change < 0:
                signal = "SHORT_BUILDUP"
                score  = -5
            elif oi_change_pct < -5 and price_change > 0:
                signal = "SHORT_COVERING"
                score  = 3
            elif oi_change_pct < -5 and price_change < 0:
                signal = "LONG_UNWINDING"
                score  = -3
            else:
                signal = "NEUTRAL"
                score  = 0

            return {
                "symbol":       symbol,
                "oi_change_pct": round(oi_change_pct, 2),
                "price_change":  round(price_change, 2),
                "signal":        signal,
                "score":         score,
            }
        except Exception as e:
            logger.debug(f"Short interest parse error {symbol}: {e}")
        return None

    # ── COMPOSITE SIGNAL SCORE ─────────────────────────────────

    def get_composite_score(self, symbol: str, direction: str) -> Tuple[int, str]:
        """
        Combine all NSE data sources into a single score adjustment.
        Returns (score_adjustment, reason_string) where score is -20 to +20.

        Breakdown:
          Bulk/Block deals:   ±10 pts  (smart money footprint)
          Delivery %:         ±5  pts  (conviction vs speculation)
          52wk context:       ±5  pts  (momentum context)
          Short interest:     ±5  pts  (F&O buildup signal)
        """
        total_score = 0
        reasons     = []

        # ── 1. Bulk + Block deals ──────────────────────────
        try:
            all_deals  = self.get_bulk_deals() + self.get_block_deals()
            sym_deals  = [d for d in all_deals if d.get("symbol") == symbol]
            if sym_deals:
                buy_val  = sum(d["value_cr"] for d in sym_deals if d["buy_sell"] == "BUY")
                sell_val = sum(d["value_cr"] for d in sym_deals if d["buy_sell"] == "SELL")
                total_val = buy_val + sell_val
                if total_val > 0:
                    net_bias = (buy_val - sell_val) / total_val * 100
                    if direction == "LONG":
                        deal_pts = 10 if net_bias > 60 else (5 if net_bias > 20 else
                                   (-10 if net_bias < -60 else (-5 if net_bias < -20 else 0)))
                    else:
                        deal_pts = -10 if net_bias > 60 else (-5 if net_bias > 20 else
                                    (10 if net_bias < -60 else (5 if net_bias < -20 else 0)))
                    total_score += deal_pts
                    if abs(deal_pts) >= 5:
                        emoji = "🟢" if deal_pts > 0 else "🔴"
                        reasons.append(
                            f"{emoji}Bulk/Block ₹{total_val:.1f}Cr "
                            f"({'Buy' if net_bias > 0 else 'Sell'} net)"
                        )
        except Exception:
            pass

        # ── 2. Delivery % ──────────────────────────────────
        try:
            delivery = self.get_delivery_data(symbol)
            if delivery:
                dp = delivery["delivery_pct"]
                if dp > 60:
                    total_score += 5
                    reasons.append(f"📦Delivery {dp:.0f}% (conviction)")
                elif dp < 30:
                    total_score -= 3
                    reasons.append(f"⚠️Delivery {dp:.0f}% (speculative)")
        except Exception:
            pass

        # ── 3. 52-week context ──────────────────────────────
        try:
            w52 = self.get_52wk_context(symbol)
            if w52:
                if direction == "LONG":
                    if w52["at_52wk_high"]:
                        total_score += 5
                        reasons.append("🚀At 52wk high (breakout)")
                    elif w52["near_52wk_high"]:
                        total_score += 3
                        reasons.append(f"📈Near 52wk high (-{w52['pct_from_high']:.1f}%)")
                    elif w52["near_52wk_low"]:
                        total_score -= 5
                        reasons.append("⚠️Near 52wk low — avoid LONG")
                elif direction == "SHORT" and w52["near_52wk_low"]:
                    total_score += 3
                    reasons.append("📉Near 52wk low (SHORT context)")
        except Exception:
            pass

        # ── 4. F&O short interest ───────────────────────────
        try:
            si = self.get_short_interest(symbol)
            if si and si["signal"] != "NEUTRAL":
                s = si["score"]
                adjusted = s if direction == "LONG" else -s
                total_score += adjusted
                if abs(adjusted) >= 3:
                    reasons.append(f"📊F&O:{si['signal']}")
        except Exception:
            pass

        total_score = max(-20, min(total_score, 20))
        reason_str  = " | ".join(reasons) if reasons else ""
        return total_score, reason_str

    # ── MORNING BRIEF SUMMARY ──────────────────────────────────

    def get_morning_summary(self) -> str:
        """
        Format NSE institutional data for Telegram morning brief.
        Called by continuous_learner at 8:30 AM IST.
        """
        lines = ["📊 <b>NSE Institutional Data</b>"]

        # FII futures positioning
        fii_fut = self.get_fii_futures_positioning()
        if fii_fut:
            lines.append(fii_fut["formatted"])

        # Bulk/Block deal activity
        bulk   = self.get_bulk_deals()
        blocks = self.get_block_deals()
        if bulk or blocks:
            total_deals = len(bulk) + len(blocks)
            buy_deals   = sum(1 for d in bulk + blocks if d["buy_sell"] == "BUY")
            sell_deals  = total_deals - buy_deals
            lines.append(
                f"💼 Deals today: {total_deals} total | "
                f"Buy:{buy_deals} / Sell:{sell_deals}"
            )
            # Top 3 by value
            all_deals = sorted(bulk + blocks, key=lambda x: x["value_cr"], reverse=True)
            for d in all_deals[:3]:
                arrow = "🟢" if d["buy_sell"] == "BUY" else "🔴"
                lines.append(
                    f"  {arrow} {d['symbol']} ₹{d['value_cr']:.1f}Cr {d['buy_sell']}"
                )
        else:
            lines.append("💼 No bulk/block deals data yet")

        return "\n".join(lines)


# ── SECTOR MAP ─────────────────────────────────────────────────
# Used by risk_manager for correlation guard
SECTOR_MAP: Dict[str, str] = {
    # Banking & Finance
    "HDFCBANK": "BANKING",   "ICICIBANK": "BANKING",  "SBIN":      "BANKING",
    "KOTAKBANK":"BANKING",   "AXISBANK":  "BANKING",  "INDUSINDBK":"BANKING",
    "BANKBARODA":"BANKING",  "PNB":       "BANKING",  "FEDERALBNK":"BANKING",
    "BAJFINANCE":"FINANCE",  "BAJAJFINSV":"FINANCE",  "HDFCAMC":   "FINANCE",
    "MUTHOOTFIN":"FINANCE",  "CHOLAFIN":  "FINANCE",

    # IT / Technology
    "TCS":     "IT",  "INFY":    "IT",  "WIPRO":   "IT",
    "HCLTECH": "IT",  "TECHM":   "IT",  "LTIM":    "IT",
    "MPHASIS": "IT",  "COFORGE": "IT",  "PERSISTENT":"IT",

    # Oil & Energy
    "RELIANCE": "ENERGY",  "ONGC":  "ENERGY",  "IOC":    "ENERGY",
    "BPCL":     "ENERGY",  "NTPC":  "ENERGY",  "POWERGRID":"ENERGY",
    "TATAPOWER":"ENERGY",  "ADANIGREEN":"ENERGY",

    # Auto
    "MARUTI":    "AUTO",  "TATAMOTORS": "AUTO",  "M&M":       "AUTO",
    "BAJAJ-AUTO":"AUTO",  "EICHERMOT":  "AUTO",  "HEROMOTOCO":"AUTO",
    "ASHOKLEY":  "AUTO",  "TVSMOTOR":   "AUTO",

    # Pharma
    "SUNPHARMA": "PHARMA",  "DRREDDY":   "PHARMA",  "CIPLA":    "PHARMA",
    "DIVISLAB":  "PHARMA",  "AUROPHARMA":"PHARMA",  "BIOCON":   "PHARMA",
    "ALKEM":     "PHARMA",  "TORNTPHARM":"PHARMA",

    # FMCG
    "ITC":       "FMCG",  "HINDUNILVR":"FMCG",  "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",  "DABUR":     "FMCG",  "MARICO":    "FMCG",
    "GODREJCP":  "FMCG",

    # Metals & Mining
    "TATASTEEL": "METALS",  "HINDALCO": "METALS",  "JSWSTEEL":   "METALS",
    "SAIL":      "METALS",  "VEDL":     "METALS",  "NATIONALUM":  "METALS",
    "COALINDIA": "METALS",

    # Cement & Infrastructure
    "ULTRACEMCO":"CEMENT",  "SHREECEM": "CEMENT",  "AMBUJACEMENT":"CEMENT",
    "LT":        "INFRA",   "ADANIPORTS":"INFRA",  "GMRINFRA":    "INFRA",

    # Telecom
    "BHARTIARTL":"TELECOM",  "IDEA": "TELECOM",

    # Consumer / Retail
    "TITAN":   "CONSUMER",  "DMART":   "CONSUMER",  "ASIANPAINT":"CONSUMER",
    "PIDILITIND":"CONSUMER", "VOLTAS":  "CONSUMER",

    # Diversified
    "ADANIENT": "DIVERSIFIED",  "ADANIENTERPRISES":"DIVERSIFIED",
}


def get_sector(symbol: str) -> str:
    """Get sector for a symbol. Returns 'OTHER' if not in map."""
    return SECTOR_MAP.get(symbol.upper(), "OTHER")


# ── SINGLETON ──────────────────────────────────────────────────

_fetcher: Optional[NSEDataFetcher] = None


def get_nse_data_fetcher() -> NSEDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = NSEDataFetcher()
    return _fetcher
