"""
All-Weather Strategy Engine — auto-adapts to ANY market condition every 30 min.
TRENDING_BULL  → momentum breakouts, full size, wider stops
TRENDING_BEAR  → short-only momentum, careful with longs
RANGING        → VWAP bounce, mean reversion, tight TP
VOLATILE       → compression plays, scalps, reduced size
CHOPPY         → sit out or idle scalp, very high bar
CRISIS         → cash mode, no longs, counter-trend only
OPTIMAL        → all strategies, full power, max size
"""
import json, logging, os, threading, time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

@dataclass
class StrategyConfig:
    regime: str = "UNKNOWN"
    strategy_type: str = "MOMENTUM"
    min_score: float = 75.0
    position_size_mult: float = 1.0
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    max_positions: int = 5
    allow_longs: bool = True
    allow_shorts: bool = True
    scalp_mode: bool = False
    bias_description: str = "Standard"
    confidence: float = 0.5

    def to_adaptive_params(self) -> dict:
        return {"min_score": self.min_score, "position_size_mult": self.position_size_mult,
                "sl_multiplier": self.sl_atr_mult, "tp_multiplier": self.tp_atr_mult,
                "strategy_type": self.strategy_type, "allow_longs": self.allow_longs,
                "allow_shorts": self.allow_shorts, "max_positions": self.max_positions,
                "regime": self.regime}

PROFILES = {
    "OPTIMAL":       StrategyConfig("OPTIMAL","FULL_POWER",68.0,1.50,1.8,3.5,8,True,True,False,"All systems GO — full power"),
    "TRENDING_BULL": StrategyConfig("TRENDING_BULL","MOMENTUM",72.0,1.20,1.8,3.0,6,True,False,False,"Trending UP — momentum breakouts"),
    "TRENDING_BEAR": StrategyConfig("TRENDING_BEAR","SHORT_MOMENTUM",74.0,1.10,1.8,3.0,5,False,True,False,"Trending DOWN — shorts only"),
    "RANGING":       StrategyConfig("RANGING","MEAN_REVERSION",78.0,0.80,1.2,1.5,4,True,True,False,"RANGING — VWAP bounce, mean reversion"),
    "VOLATILE":      StrategyConfig("VOLATILE","SCALP",82.0,0.60,1.5,2.5,3,True,True,True,"HIGH VOL — scalps, tight stops"),
    "CHOPPY":        StrategyConfig("CHOPPY","SCALP",88.0,0.40,1.0,1.5,2,True,True,True,"CHOPPY — elite scalps only"),
    "CRISIS":        StrategyConfig("CRISIS","CASH",95.0,0.25,0.8,1.5,1,False,True,True,"CRISIS — cash, no longs"),
    "UNKNOWN":       StrategyConfig("UNKNOWN","MOMENTUM",77.0,0.90,1.5,2.5,4,True,True,False,"Unknown — conservative baseline"),
}

class AllWeatherEngine:
    OUTPUT_FILE = "data/strategy_config.json"
    CYCLE_INTERVAL = 1800

    def __init__(self):
        self._current = PROFILES["UNKNOWN"]
        self._last_regime = "UNKNOWN"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cycle = 0
        os.makedirs("data", exist_ok=True)

    def start_background(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AllWeatherEngine")
        self._thread.start()
        logger.info("[AllWeather] Background regime-strategy loop started (30-min)")

    def stop(self): self._stop.set()
    def get_current_config(self) -> StrategyConfig: return self._current

    def _loop(self):
        while not self._stop.is_set():
            try: self._cycle_run()
            except Exception as e: logger.warning(f"[AllWeather] {e}")
            self._stop.wait(self.CYCLE_INTERVAL)

    def _cycle_run(self):
        self._cycle += 1
        regime = self._detect()
        cfg = PROFILES.get(regime, PROFILES["UNKNOWN"])
        changed = regime != self._last_regime
        self._current = cfg; self._last_regime = regime
        try:
            p = cfg.to_adaptive_params()
            p["updated_at"] = datetime.now(IST).strftime("%H:%M IST")
            p["cycle"] = self._cycle
            with open(self.OUTPUT_FILE,"w") as f: json.dump(p,f,indent=2)
        except Exception as e: logger.debug(f"[AllWeather] write: {e}")
        EMOJI = {"TRENDING_BULL":"🚀","TRENDING_BEAR":"📉","RANGING":"↔️",
                 "VOLATILE":"⚡","CHOPPY":"🔀","CRISIS":"🚨","OPTIMAL":"🎯"}
        logger.info(f"[AllWeather] #{self._cycle} regime={regime}{'(CHANGED)' if changed else ''} "
                    f"strategy={cfg.strategy_type} min_score={cfg.min_score} size={cfg.position_size_mult:.2f}x")
        if changed: self._telegram(regime, cfg, EMOJI.get(regime,"📊"))

    def _detect(self) -> str:
        try:
            from ooda_engine import get_engine
            r = get_engine().get_context("SPY").regime
            if r in ("CRISIS","TRENDING_BULL","TRENDING_BEAR","VOLATILE","RANGING"): return r
        except Exception: pass
        try:
            from market_regime_v2 import get_composite_regime
            s = get_composite_regime()
            c = getattr(s,"composite","NEUTRAL")
            if c == "OPTIMAL": return "OPTIMAL"
            elif c == "AVOID": return "CRISIS"
            elif c == "CAUTION": return "CHOPPY"
            elif c == "GOOD": return "TRENDING_BULL"
        except Exception: pass
        return "UNKNOWN"

    def _telegram(self, regime, cfg, emoji):
        try:
            from alerts_telegram import TelegramAlerts
            TelegramAlerts().send_message(
                f"{emoji} <b>REGIME → {regime}</b>\nStrategy: <code>{cfg.strategy_type}</code>\n"
                f"Longs:{'✅' if cfg.allow_longs else '❌'} Shorts:{'✅' if cfg.allow_shorts else '❌'}\n"
                f"MinScore:<b>{cfg.min_score}</b> Size:<b>{cfg.position_size_mult:.2f}x</b>\n"
                f"SL:{cfg.sl_atr_mult:.1f}×ATR TP:{cfg.tp_atr_mult:.1f}×ATR MaxPos:{cfg.max_positions}\n"
                f"💡 {cfg.bias_description}")
        except Exception as e: logger.debug(f"[AllWeather] telegram: {e}")

    @classmethod
    def load_config_from_file(cls) -> dict:
        try:
            with open(cls.OUTPUT_FILE) as f: return json.load(f)
        except Exception: return {}

_engine: Optional[AllWeatherEngine] = None
def get_all_weather_engine() -> AllWeatherEngine:
    global _engine
    if _engine is None: _engine = AllWeatherEngine()
    return _engine
