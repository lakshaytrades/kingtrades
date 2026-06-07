"""
Market Condition Router — determines what the market offers today and routes strategy.
TREND/RANGE/GAP/VOLATILE/DULL/CRISIS → recommended strategies + score adjustments.
"""
import logging, time
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

@dataclass
class MarketCondition:
    day_type: str = "UNKNOWN"; confidence: float = 0.5
    recommended: list = field(default_factory=list)
    avoided: list = field(default_factory=list)
    min_score_override: float = 0.0; size_mult: float = 0.0
    description: str = ""

PROFILES = {
    "TREND":    MarketCondition("TREND",0.80,["MOMENTUM","ORB","BREAKOUT","BULL_FLAG"],["VWAP_BOUNCE"],72,1.2,"Trend day — ride momentum"),
    "RANGE":    MarketCondition("RANGE",0.75,["VWAP_BOUNCE","BOLLINGER","RSI_EXTREME"],["MOMENTUM","BREAKOUT"],78,0.8,"Range day — fade extremes"),
    "GAP":      MarketCondition("GAP",0.85,["ORB","GAP_GO","GAP_FADE"],["MIDDAY"],75,1.1,"Gap day — ORB within first 30 min"),
    "VOLATILE": MarketCondition("VOLATILE",0.70,["SCALP","COMPRESSION","ORB"],["MOMENTUM"],82,0.6,"Volatile — scalps, tight stops"),
    "DULL":     MarketCondition("DULL",0.65,["VWAP_BOUNCE","IDLE_SCALP"],["MOMENTUM","ORB"],85,0.5,"Dull — tiny scalps or sit out"),
    "CRISIS":   MarketCondition("CRISIS",0.90,["CASH"],["MOMENTUM","ORB","BREAKOUT"],95,0.25,"Crisis — cash, preserve capital"),
}

class MarketConditionRouter:
    def __init__(self):
        self._current: Optional[MarketCondition] = None
        self._ts: float = 0; self._ttl = 1800

    def get_condition(self) -> MarketCondition:
        if self._current and time.time()-self._ts < self._ttl: return self._current
        self._current = self._determine(); self._ts = time.time()
        return self._current

    def _determine(self) -> MarketCondition:
        try:
            from ooda_engine import get_engine
            r = get_engine().get_context("SPY").regime
            if r == "CRISIS": return PROFILES["CRISIS"]
        except Exception: pass
        try:
            from india_intel import get_india_intel
            if not get_india_intel().is_safe_to_trade(): return PROFILES["CRISIS"]
        except Exception: pass
        try:
            from all_weather_strategy import AllWeatherEngine
            p = AllWeatherEngine.load_config_from_file()
            regime = p.get("regime","UNKNOWN")
            if regime in ("TRENDING_BULL","TRENDING_BEAR"): return PROFILES["TREND"]
            elif regime == "RANGING": return PROFILES["RANGE"]
            elif regime == "VOLATILE": return PROFILES["VOLATILE"]
            elif regime == "CHOPPY": return PROFILES["DULL"]
            elif regime == "CRISIS": return PROFILES["CRISIS"]
        except Exception: pass
        return MarketCondition("UNKNOWN",0.4,["MOMENTUM","VWAP_BOUNCE"],[],77,0.9,"Unknown — conservative")

    def is_recommended(self, strategy: str) -> bool:
        c = self.get_condition(); n = strategy.upper()
        return any(r in n or n in r for r in c.recommended)

    def is_avoided(self, strategy: str) -> bool:
        c = self.get_condition(); n = strategy.upper()
        return any(a in n or n in a for a in c.avoided)

    def get_score_adj(self, strategy: str) -> float:
        if self.is_recommended(strategy): return 5.0
        if self.is_avoided(strategy): return -8.0
        return 0.0

_router: Optional[MarketConditionRouter] = None
def get_router() -> MarketConditionRouter:
    global _router
    if _router is None: _router = MarketConditionRouter()
    return _router
