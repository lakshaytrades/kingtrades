"""
sector_rotation.py — NSE Momentum Groww AI Bot
Identifies the 3 hottest NSE sectors each morning using ETF momentum,
relative strength vs Nifty50, RSI, and volume trend. Filters the
watchlist to concentrate on sectors with positive institutional flow.
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

SECTOR_ETFS: Dict[str, str] = {
    "BANKING": "^NSEBANK",
    "IT":      "^CNXIT",
    "PHARMA":  "^CNXPHARMA",
    "AUTO":    "^CNXAUTO",
    "FMCG":    "^CNXFMCG",
    "METAL":   "^CNXMETAL",
    "ENERGY":  "^CNXENERGY",
    "REALTY":  "^CNXREALTY",
    "INFRA":   "^CNXINFRA",
    "MEDIA":   "^CNXMEDIA",
}

SECTOR_STOCKS: Dict[str, List[str]] = {
    "BANKING": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
                "INDUSINDBK", "BANKBARODA", "PNB", "FEDERALBNK", "BANDHANBNK"],
    "IT":      ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM",
                "LTIM", "MPHASIS", "COFORGE", "PERSISTENT"],
    "PHARMA":  ["SUNPHARMA", "CIPLA", "DIVISLAB", "DRREDDY",
                "APOLLOHOSP", "LUPIN", "AUROPHARMA"],
    "AUTO":    ["MARUTI", "TATAMOTORS", "BAJAJ-AUTO", "EICHERMOT",
                "HEROMOTOCO", "M&M", "ASHOKLEY"],
    "FMCG":    ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA",
                "TATACONSUM", "DABUR", "MARICO"],
    "METAL":   ["JSWSTEEL", "TATASTEEL", "HINDALCO", "SAIL",
                "NMDC", "NATIONALUM", "HINDCOPPER"],
    "ENERGY":  ["RELIANCE", "ONGC", "BPCL", "IOC", "NTPC",
                "POWERGRID", "TATAPOWER", "ADANIGREEN"],
    "REALTY":  ["DLF", "GODREJPROP", "OBEROIRLTY", "BRIGADE",
                "PRESTIGE", "PHOENIXLTD"],
    "INFRA":   ["LT", "ADANIPORTS", "ADANIENT", "HAL",
                "BEL", "BHEL", "RECLTD", "PFC"],
    "MEDIA":   ["ZOMATO", "IRCTC", "NYKAA", "DMART", "JUBLFOOD", "WESTLIFE"],
}

NIFTY50_SYMBOL = "^NSEI"
CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

# Scoring weights — must sum to 1.0
W_MOMENTUM_5D  = 0.40
W_RS_VS_NIFTY  = 0.30
W_RSI          = 0.20
W_VOLUME_TREND = 0.10

TREND_THRESHOLDS = {
    "HOT":  75.0,
    "WARM": 55.0,
    "COOL": 40.0,
}


@dataclass
class SectorScore:
    sector: str
    score: float           # 0–100
    momentum_5d: float     # % price change over 5 days
    rs_vs_nifty: float     # relative strength: sector_return - nifty_return
    rsi: float
    trend: str             # "HOT", "WARM", "COOL", "COLD"
    top_stocks: List[str] = field(default_factory=list)


class SectorRotationEngine:

    def __init__(self):
        self._cache: List[SectorScore] = []
        self._cached_at: float = 0.0
        # Nifty return cached alongside sector scores to avoid redundant fetches
        self._nifty_5d_return: float = 0.0

    # ------------------------------------------------------------------
    # DATA HELPERS
    # ------------------------------------------------------------------

    def _fetch_yf_history(self, ticker: str, period: str = "1mo") -> Optional[object]:
        """Download yfinance history. Returns DataFrame or None on failure."""
        try:
            import yfinance as yf
            df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            return df
        except Exception as exc:
            logger.debug(
                f"[{format_ist_timestamp()}] yfinance download failed for {ticker}: {exc}"
            )
            return None

    def _nse_ticker(self, symbol: str) -> str:
        """Convert plain NSE symbol to Yahoo Finance ticker (appends .NS)."""
        if "^" in symbol or "." in symbol:
            return symbol
        return f"{symbol}.NS"

    def _calc_rsi(self, closes: "np.ndarray", period: int = 14) -> float:
        """Wilder RSI on a 1-D numpy array of close prices. Returns 50.0 if insufficient data."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas,  0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i])  / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

    def _compute_metrics_from_df(self, df) -> Dict[str, float]:
        """
        Given a DataFrame with Close and Volume columns, returns:
        momentum_5d (%), rsi(14), volume_trend (today / 5-day avg).
        """
        closes  = df["Close"].values.flatten().astype(float)
        volumes = df["Volume"].values.flatten().astype(float)

        momentum_5d = 0.0
        if len(closes) >= 6:
            momentum_5d = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 4)

        rsi = self._calc_rsi(closes)

        volume_trend = 1.0
        if len(volumes) >= 6 and np.mean(volumes[-6:-1]) > 0:
            volume_trend = round(float(volumes[-1]) / float(np.mean(volumes[-6:-1])), 4)

        return {"momentum_5d": momentum_5d, "rsi": rsi, "volume_trend": volume_trend}

    def _get_nifty_5d_return(self) -> float:
        df = self._fetch_yf_history(NIFTY50_SYMBOL, period="1mo")
        if df is None or len(df) < 6:
            return 0.0
        closes = df["Close"].values.flatten().astype(float)
        return round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 4)

    def _get_top_stocks_by_momentum(self, symbols: List[str], n: int = 3) -> List[str]:
        """Return top-n stock symbols from the given list ranked by 5-day % change."""
        scored: List[tuple] = []
        for sym in symbols:
            df = self._fetch_yf_history(self._nse_ticker(sym), period="10d")
            if df is None or len(df) < 6:
                continue
            closes = df["Close"].values.flatten().astype(float)
            mom = ((closes[-1] - closes[-6]) / closes[-6]) * 100
            scored.append((sym, mom))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:n]]

    def _classify_trend(self, score: float) -> str:
        if score >= TREND_THRESHOLDS["HOT"]:
            return "HOT"
        if score >= TREND_THRESHOLDS["WARM"]:
            return "WARM"
        if score >= TREND_THRESHOLDS["COOL"]:
            return "COOL"
        return "COLD"

    # ------------------------------------------------------------------
    # SCORING
    # ------------------------------------------------------------------

    def _score_sector(self, sector: str, symbols: List[str]) -> SectorScore:
        """
        Score one sector using its ETF (preferred) or constituent averages (fallback).
        `symbols` is the sector's stock list from SECTOR_STOCKS, used for the fallback
        path and for computing top_stocks.
        Nifty 5-day return is read from self._nifty_5d_return (set by score_all_sectors).
        """
        etf_ticker = SECTOR_ETFS.get(sector)
        metrics: Optional[Dict[str, float]] = None

        if etf_ticker:
            df = self._fetch_yf_history(etf_ticker, period="1mo")
            if df is not None and len(df) >= 6:
                metrics = self._compute_metrics_from_df(df)

        if metrics is None:
            # Fallback: average metrics across the first 5 constituent stocks
            logger.debug(
                f"[{format_ist_timestamp()}] ETF fallback for {sector} — using constituents"
            )
            mom_list, rsi_list, vol_list = [], [], []
            for sym in symbols[:5]:
                df = self._fetch_yf_history(self._nse_ticker(sym), period="1mo")
                if df is None or len(df) < 6:
                    continue
                m = self._compute_metrics_from_df(df)
                mom_list.append(m["momentum_5d"])
                rsi_list.append(m["rsi"])
                vol_list.append(m["volume_trend"])
            if not mom_list:
                return SectorScore(
                    sector=sector, score=50.0, momentum_5d=0.0,
                    rs_vs_nifty=1.0, rsi=50.0, trend="COOL", top_stocks=[],
                )
            metrics = {
                "momentum_5d":  float(np.mean(mom_list)),
                "rsi":          float(np.mean(rsi_list)),
                "volume_trend": float(np.mean(vol_list)),
            }

        momentum_5d  = metrics["momentum_5d"]
        rsi          = metrics["rsi"]
        volume_trend = metrics["volume_trend"]

        # Relative strength vs Nifty50.
        # rs_raw > 0 means sector outperformed Nifty over 5 days.
        # Map to 0–100: 0% diff → 50, +3% → ~80, -3% → ~20.
        nifty_ret = self._nifty_5d_return
        rs_raw    = momentum_5d - nifty_ret
        rs_score  = float(np.clip(50.0 + rs_raw * 10, 0, 100))

        # Momentum score: 0% → 50, +3% → 100, -3% → 0
        mom_score = float(np.clip(50.0 + momentum_5d * (50 / 3.0), 0, 100))

        # RSI is already 0–100; use directly
        rsi_score = float(np.clip(rsi, 0, 100))

        # Volume trend: 1.0 = neutral (score 50). 2.0x = bullish (score 100). 0.5x = 0.
        vol_score = float(np.clip((volume_trend - 0.5) * (100 / 1.5), 0, 100))

        composite = round(
            W_MOMENTUM_5D  * mom_score
            + W_RS_VS_NIFTY  * rs_score
            + W_RSI          * rsi_score
            + W_VOLUME_TREND * vol_score,
            2,
        )

        top_stocks = self._get_top_stocks_by_momentum(symbols, n=3)

        return SectorScore(
            sector=sector,
            score=composite,
            momentum_5d=round(momentum_5d, 4),
            rs_vs_nifty=round(rs_raw, 4),
            rsi=round(rsi, 2),
            trend=self._classify_trend(composite),
            top_stocks=top_stocks,
        )

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def score_all_sectors(self) -> List[SectorScore]:
        """Score all sectors, sorted best-to-worst. Results are cached for 30 minutes."""
        now = _time.monotonic()
        if self._cache and (now - self._cached_at) < CACHE_TTL_SECONDS:
            return self._cache

        logger.info(f"[{format_ist_timestamp()}] Scoring all sectors...")
        # Fetch Nifty return once; stored on self so _score_sector can read it
        self._nifty_5d_return = self._get_nifty_5d_return()

        scores: List[SectorScore] = []
        for sector, symbols in SECTOR_STOCKS.items():
            try:
                s = self._score_sector(sector, symbols)
                scores.append(s)
                logger.debug(
                    f"[{format_ist_timestamp()}] {sector}: score={s.score} trend={s.trend}"
                )
            except Exception as exc:
                logger.warning(
                    f"[{format_ist_timestamp()}] Sector scoring failed for {sector}: {exc}"
                )

        scores.sort(key=lambda x: x.score, reverse=True)
        self._cache = scores
        self._cached_at = now
        logger.info(
            f"[{format_ist_timestamp()}] Sector scoring complete. "
            f"Top: {[s.sector for s in scores[:3]]}"
        )
        return scores

    def get_hot_sectors(self, top_n: int = 3) -> List[str]:
        """Return the top-N sector names by composite score."""
        return [s.sector for s in self.score_all_sectors()[:top_n]]

    def filter_watchlist_by_sector(self, watchlist: List[str], top_n: int = 3) -> List[str]:
        """
        Keep only symbols that belong to the top-N sectors.
        Symbols whose sector is unknown are kept (fail-open) so a misconfigured
        SECTOR_STOCKS doesn't silently drop valid signals.
        """
        hot = set(self.get_hot_sectors(top_n))
        filtered = []
        for sym in watchlist:
            sector = self.get_sector_for_symbol(sym)
            if sector == "OTHER" or sector in hot:
                filtered.append(sym)
        return filtered

    def get_sector_for_symbol(self, symbol: str) -> str:
        """Return sector name for a symbol, or 'OTHER' if not in any sector map."""
        upper = symbol.upper()
        for sector, stocks in SECTOR_STOCKS.items():
            if upper in [s.upper() for s in stocks]:
                return sector
        return "OTHER"

    # ------------------------------------------------------------------
    # TELEGRAM
    # ------------------------------------------------------------------

    def format_telegram_summary(self) -> str:
        scores = self.score_all_sectors()
        today_str = get_current_ist_time().strftime("%Y-%m-%d")
        lines = [f"🔥 SECTOR ROTATION — {today_str}"]

        hot_warm = [s for s in scores if s.trend in ("HOT", "WARM")]
        cold_cool = [s for s in scores if s.trend in ("COLD", "COOL")]

        trade_sectors = hot_warm[:3]
        if trade_sectors:
            lines.append("HOT (trade these today):")
            for i, s in enumerate(trade_sectors, 1):
                sign    = "+" if s.momentum_5d >= 0 else ""
                rs_sign = "+" if s.rs_vs_nifty  >= 0 else ""
                lines.append(
                    f"  {i}. {s.sector:<10} Score:{s.score:.0f}  "
                    f"{sign}{s.momentum_5d:.1f}% 5d  RS:{rs_sign}{s.rs_vs_nifty:.1f}"
                )

        avoid = cold_cool[-2:]
        if avoid:
            lines.append("COLD (avoid today):")
            for s in avoid:
                sign = "+" if s.momentum_5d >= 0 else ""
                lines.append(
                    f"  ❄️ {s.sector:<6} Score:{s.score:.0f}  {sign}{s.momentum_5d:.1f}% 5d"
                )

        return "\n".join(lines)

    def send_telegram_summary(self) -> None:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured — skipping sector summary")
            return
        import requests
        msg = self.format_telegram_summary()
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=8,
            )
            resp.raise_for_status()
            logger.info(
                f"[{format_ist_timestamp()}] Sector rotation summary sent to Telegram"
            )
        except Exception as exc:
            logger.warning(
                f"[{format_ist_timestamp()}] Telegram sector summary failed: {exc}"
            )


# ------------------------------------------------------------------
# MODULE-LEVEL SINGLETON
# ------------------------------------------------------------------

_engine: Optional[SectorRotationEngine] = None


def get_sector_rotation_engine() -> SectorRotationEngine:
    global _engine
    if _engine is None:
        _engine = SectorRotationEngine()
    return _engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = get_sector_rotation_engine()
    scores = engine.score_all_sectors()
    print("\n=== SECTOR SCORES ===")
    for s in scores:
        print(
            f"{s.sector:<10} {s.score:5.1f}  {s.trend:<5}  "
            f"5d:{s.momentum_5d:+.2f}%  RS:{s.rs_vs_nifty:+.2f}  "
            f"RSI:{s.rsi:.0f}  top:{s.top_stocks}"
        )
    print()
    print(engine.format_telegram_summary())
