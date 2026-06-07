"""
Live Data Manager — unified real-time data with TTL caching and priority fallbacks.
Single source of truth for: quotes, India VIX, NIFTY PCR, SGX gap, FII flow, US VIX.
"""
import logging, time, threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

@dataclass
class Quote:
    symbol: str; last: float = 0.0; bid: float = 0.0; ask: float = 0.0
    volume: int = 0; change_pct: float = 0.0; high: float = 0.0; low: float = 0.0
    vwap: float = 0.0; timestamp: float = field(default_factory=time.time)
    stale: bool = False; source: str = "unknown"

class DataCache:
    def __init__(self):
        self._d: Dict[str,Any] = {}; self._ts: Dict[str,float] = {}
        self._lock = threading.RLock()
    def get(self, key: str, ttl: float) -> Optional[Any]:
        with self._lock:
            if key not in self._d: return None
            if time.time() - self._ts.get(key,0) > ttl: return None
            return self._d[key]
    def get_stale(self, key: str) -> Optional[Any]:
        with self._lock: return self._d.get(key)
    def set(self, key: str, val: Any):
        with self._lock: self._d[key]=val; self._ts[key]=time.time()

class LiveDataManager:
    QUOTE_TTL=30; INDEX_TTL=60; PCR_TTL=300; FII_TTL=14400
    def __init__(self): self._c = DataCache()

    def get_quote(self, symbol: str) -> Quote:
        cached = self._c.get(f"q_{symbol}", self.QUOTE_TTL)
        if cached: return cached
        q = self._fetch_quote(symbol)
        if q: self._c.set(f"q_{symbol}", q); return q
        stale = self._c.get_stale(f"q_{symbol}")
        if stale: stale.stale = True; return stale
        return Quote(symbol=symbol, stale=True)

    def _fetch_quote(self, symbol: str) -> Optional[Quote]:
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).fast_info
            last = getattr(info, "last_price", None)
            if last is not None and last > 0:
                return Quote(symbol=symbol, last=float(last),
                    volume=int(getattr(info,"three_month_average_volume",0) or 0), source="yfinance")
        except Exception as e: logger.debug(f"quote {symbol}: {e}")
        return None

    def get_india_vix(self) -> float:
        c = self._c.get("india_vix", self.INDEX_TTL)
        if c is not None: return float(c)
        try:
            from india_intel import get_india_intel
            v = get_india_intel().vix.get_vix(); self._c.set("india_vix",v); return v
        except Exception: return 16.0

    def get_nifty_pcr(self) -> float:
        c = self._c.get("nifty_pcr", self.PCR_TTL)
        if c is not None: return float(c)
        try:
            from india_intel import get_india_intel
            v = get_india_intel().pcr.get_pcr(); self._c.set("nifty_pcr",v); return v
        except Exception: return 1.0

    def get_us_vix(self) -> float:
        c = self._c.get("us_vix", self.INDEX_TTL)
        if c is not None: return float(c)
        try:
            import yfinance as yf
            df = yf.download("^VIX","2d","1d",progress=False,auto_adjust=True)
            if df is not None and len(df)>0:
                v = float(df["Close"].iloc[-1]); self._c.set("us_vix",v); return v
        except Exception: pass
        return 20.0

    def get_spy_return_today(self) -> float:
        c = self._c.get("spy_ret",300)
        if c is not None: return float(c)
        try:
            import yfinance as yf
            df = yf.download("SPY","2d","1d",progress=False,auto_adjust=True)
            if df is not None and len(df)>=2:
                r = float(df["Close"].iloc[-1])/float(df["Close"].iloc[-2])-1
                self._c.set("spy_ret",r); return r
        except Exception: pass
        return 0.0

    def get_fii_flow(self) -> dict:
        c = self._c.get("fii",self.FII_TTL)
        if c: return c
        try:
            from fii_dii_tracker import get_fii_dii_tracker
            flow = get_fii_dii_tracker().get_today_flow()
            if flow:
                r = {"fii_net":getattr(flow,"fii_net_cash",0),
                     "dii_net":getattr(flow,"dii_net",0),
                     "combined_score":getattr(flow,"flow_score",0)}
                self._c.set("fii",r); return r
        except Exception: pass
        return {"fii_net":0,"dii_net":0,"combined_score":0}

    def get_market_summary(self) -> dict:
        return {"india_vix":self.get_india_vix(),"us_vix":self.get_us_vix(),
                "nifty_pcr":self.get_nifty_pcr(),"spy_return":self.get_spy_return_today(),
                "fii":self.get_fii_flow()}

_ldm: Optional[LiveDataManager] = None
def get_live_data() -> LiveDataManager:
    global _ldm
    if _ldm is None: _ldm = LiveDataManager()
    return _ldm
