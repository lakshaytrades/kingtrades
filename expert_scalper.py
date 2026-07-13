"""
Expert Scalper — OODA-regime-aware 4-mode ultra-short-term signal generator.
Modes auto-selected by current regime:
  MOMENTUM      — trending: chase burst moves (target 0.4%, stop 0.15%)
  VWAP_BOUNCE   — ranging: fade VWAP deviation (target VWAP, stop 0.12%)
  ORB           — first 15 min: opening range breakout/breakdown
  VOLATILITY    — high VIX: compression→expansion plays
NSE windows (IST): 09:15-09:30=ORB, 09:30-10:30=MOMENTUM, 10:30-14:30=VWAP/REGIME, 14:30-15:15=MOMENTUM
Max hold: 10 min. Max simultaneous: 2. Risk per scalp: 0.2% of capital.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from enum import Enum
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo
import numpy as np

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
ET  = ZoneInfo("America/New_York")

_ORB_START_IST      = dtime(9, 15)
_ORB_END_IST        = dtime(9, 30)
_OPEN_DRIVE_END_IST = dtime(10, 30)
_MIDDAY_END_IST     = dtime(14, 30)
_POWER_HOUR_END_IST = dtime(15, 15)
_US_OPEN_END_ET     = dtime(10, 30)
_US_POWER_START_ET  = dtime(14, 0)
_US_POWER_END_ET    = dtime(15, 30)


class ScalpMode(Enum):
    MOMENTUM    = "MOMENTUM_SCALP"
    VWAP_BOUNCE = "VWAP_BOUNCE_SCALP"
    ORB         = "ORB_SCALP"
    VOLATILITY  = "VOLATILITY_SCALP"
    NONE        = "NO_SCALP"


@dataclass
class ExpertScalpSignal:
    symbol: str
    direction: str
    mode: ScalpMode
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    max_hold_minutes: int = 10
    confidence: float = 0.0
    score: float = 0.0
    reason: str = ""
    atr: float = 0.0
    vwap: float = 0.0


class ExpertScalper:
    MAX_ACTIVE = 2

    def __init__(self):
        self._active: Dict[str, float] = {}
        self._orb_levels: Dict[str, dict] = {}

    def get_scalp_mode(self, use_ist: bool = True, regime: Optional[str] = None) -> ScalpMode:
        regime = regime or self._get_regime()
        if regime == "CRISIS":
            return ScalpMode.NONE
        now = datetime.now(IST if use_ist else ET)
        t = now.time()
        if use_ist:
            if _ORB_START_IST <= t < _ORB_END_IST:
                return ScalpMode.ORB
            elif _ORB_END_IST <= t < _OPEN_DRIVE_END_IST:
                return ScalpMode.MOMENTUM if regime in ("TRENDING_BULL","TRENDING_BEAR") else ScalpMode.VWAP_BOUNCE
            elif _OPEN_DRIVE_END_IST <= t < _MIDDAY_END_IST:
                if regime == "VOLATILE": return ScalpMode.VOLATILITY
                return ScalpMode.VWAP_BOUNCE
            elif _MIDDAY_END_IST <= t < _POWER_HOUR_END_IST:
                return ScalpMode.MOMENTUM if regime in ("TRENDING_BULL","TRENDING_BEAR","VOLATILE") else ScalpMode.VWAP_BOUNCE
            return ScalpMode.NONE
        else:
            if dtime(9,30) <= t < _US_OPEN_END_ET: return ScalpMode.MOMENTUM
            elif _US_OPEN_END_ET <= t < _US_POWER_START_ET: return ScalpMode.VWAP_BOUNCE
            elif _US_POWER_START_ET <= t < _US_POWER_END_ET: return ScalpMode.MOMENTUM
            return ScalpMode.NONE

    def scan(self, symbol: str, closes: np.ndarray, highs: np.ndarray,
             lows: np.ndarray, volumes: np.ndarray, use_ist: bool = True,
             regime: Optional[str] = None) -> Optional[ExpertScalpSignal]:
        if len(self._active) >= self.MAX_ACTIVE: return None
        # Prefer the regime supplied by the caller (the symbol actually being
        # scanned); only fall back to a self-lookup keyed on this symbol.
        mode = self.get_scalp_mode(use_ist, regime=regime or self._get_regime(symbol))
        if mode == ScalpMode.NONE: return None
        closes = np.asarray(closes, dtype=float); highs = np.asarray(highs, dtype=float)
        lows = np.asarray(lows, dtype=float); volumes = np.asarray(volumes, dtype=float)
        if mode == ScalpMode.MOMENTUM: return self._momentum(symbol, closes, highs, lows, volumes)
        elif mode == ScalpMode.VWAP_BOUNCE: return self._vwap_bounce(symbol, closes, highs, lows, volumes)
        elif mode == ScalpMode.ORB: return self._orb(symbol, closes, highs, lows, volumes)
        elif mode == ScalpMode.VOLATILITY: return self._volatility(symbol, closes, highs, lows, volumes)
        return None

    def record_entry(self, symbol: str): self._active[symbol] = time.time()
    def record_exit(self, symbol: str): self._active.pop(symbol, None)
    def check_expired(self) -> List[str]:
        now = time.time(); expired = [s for s,ts in self._active.items() if now-ts > 600]
        for s in expired: self._active.pop(s, None)
        return expired

    def _momentum(self, symbol, closes, highs, lows, volumes):
        if len(closes) < 20: return None
        try:
            c = closes[-1]; atr = self._atr(highs,lows,closes)
            ret = (closes[-1]-closes[-2])/closes[-2] if closes[-2]!=0 else 0
            avg_v = np.mean(volumes[-10:-1]) if len(volumes) >= 10 else 0
            vol_r = volumes[-1] / avg_v if avg_v > 0 else 1
            if vol_r < 2.0: return None
            rsi = self._rsi(closes)
            direction = None
            if ret > 0.003 and rsi > 55: direction = "LONG"
            elif ret < -0.003 and rsi < 45: direction = "SHORT"
            if not direction: return None
            sd = max(atr*0.5, c*0.0015)
            stop = c-sd if direction=="LONG" else c+sd
            t1 = c+sd*1.5 if direction=="LONG" else c-sd*1.5
            t2 = c+sd*2.5 if direction=="LONG" else c-sd*2.5
            score = min(95.0, 60+vol_r*3+abs(ret)/0.001*2)
            return ExpertScalpSignal(symbol=symbol, direction=direction, mode=ScalpMode.MOMENTUM,
                entry_price=c, stop_loss=stop, target_1=t1, target_2=t2,
                confidence=min(0.90, 0.60+vol_r/10+abs(ret)/0.01*0.05), score=score, atr=atr,
                reason=f"Momentum {ret*100:.2f}% vol={vol_r:.1f}x RSI={rsi:.0f}")
        except Exception as e:
            logger.debug(f"[ExpertScalp] momentum {symbol}: {e}"); return None

    def _vwap_bounce(self, symbol, closes, highs, lows, volumes):
        if len(closes) < 20: return None
        try:
            c = closes[-1]; atr = self._atr(highs,lows,closes)
            vwap = self._vwap(closes,highs,lows,volumes)
            if vwap == 0: return None
            dev = (c-vwap)/vwap
            if abs(dev) < 0.004: return None
            rsi = self._rsi(closes)
            direction = None
            if dev < -0.004 and rsi < 42: direction = "LONG"
            elif dev > 0.004 and rsi > 58: direction = "SHORT"
            if not direction: return None
            sd = max(atr*0.4, c*0.0012)
            stop = c-sd if direction=="LONG" else c+sd
            t1 = vwap*0.999 if direction=="LONG" else vwap*1.001
            t2 = vwap*1.001 if direction=="LONG" else vwap*0.999
            score = min(92.0, 62+abs(dev)*2000)
            return ExpertScalpSignal(symbol=symbol, direction=direction, mode=ScalpMode.VWAP_BOUNCE,
                entry_price=c, stop_loss=stop, target_1=t1, target_2=t2,
                confidence=min(0.88, 0.55+abs(dev)*20), score=score, atr=atr, vwap=vwap,
                reason=f"VWAP bounce dev={dev*100:.2f}% RSI={rsi:.0f}", max_hold_minutes=8)
        except Exception as e:
            logger.debug(f"[ExpertScalp] vwap {symbol}: {e}"); return None

    def _orb(self, symbol, closes, highs, lows, volumes):
        if len(closes) < 3: return None
        try:
            if symbol not in self._orb_levels or not self._orb_levels[symbol].get("locked"):
                self._orb_levels[symbol] = {"high": highs[:3].max(), "low": lows[:3].min(), "locked": len(closes)>=3}
            orb = self._orb_levels.get(symbol, {}); oh = orb.get("high",0); ol = orb.get("low",0)
            if not oh or not ol: return None
            c = closes[-1]; orb_range = oh - ol
            if orb_range == 0 or orb_range/c < 0.002: return None
            atr = self._atr(highs,lows,closes)
            avg_v_orb = np.mean(volumes[:-1]) if len(volumes) > 1 else 0
            vol_r = volumes[-1] / avg_v_orb if avg_v_orb > 0 else 1
            direction = None
            if c > oh*1.001 and vol_r > 1.5: direction = "LONG"
            elif c < ol*0.999 and vol_r > 1.5: direction = "SHORT"
            if not direction: return None
            sd = max(atr*0.6, orb_range*0.5)
            stop = max(oh, c-sd) if direction=="LONG" else min(ol, c+sd)
            t1 = c+orb_range if direction=="LONG" else c-orb_range
            t2 = c+orb_range*2 if direction=="LONG" else c-orb_range*2
            score = min(97.0, 75+vol_r*2+orb_range/c*500)
            return ExpertScalpSignal(symbol=symbol, direction=direction, mode=ScalpMode.ORB,
                entry_price=c, stop_loss=stop, target_1=t1, target_2=t2,
                confidence=min(0.92, 0.70+vol_r*0.03), score=score, atr=atr,
                reason=f"ORB {'breakout' if direction=='LONG' else 'breakdown'} range={orb_range/c*100:.2f}%",
                max_hold_minutes=12)
        except Exception as e:
            logger.debug(f"[ExpertScalp] orb {symbol}: {e}"); return None

    def _volatility(self, symbol, closes, highs, lows, volumes):
        if len(closes) < 25: return None
        try:
            c = closes[-1]; atr = self._atr(highs,lows,closes)
            w = closes[-20:]; std = np.std(w); ma = np.mean(w)
            bb_w = 2*std/ma if ma!=0 else 0
            if len(closes) >= 25:
                pw = closes[-25:-5]; ps = np.std(pw); pm = np.mean(pw)
                prev_bb = 2*ps/pm if pm!=0 else 0
                expanding = bb_w > prev_bb*1.3
            else: expanding = False
            if not expanding or bb_w < 0.005: return None
            ret = (closes[-1]-closes[-2])/closes[-2] if closes[-2]!=0 else 0
            direction = "LONG" if ret > 0 else "SHORT"
            sd = max(atr*0.5, c*0.0015)
            stop = c-sd if direction=="LONG" else c+sd
            t1 = c+sd*1.5 if direction=="LONG" else c-sd*1.5
            t2 = c+sd*2.5 if direction=="LONG" else c-sd*2.5
            score = min(88.0, 62+bb_w*300)
            return ExpertScalpSignal(symbol=symbol, direction=direction, mode=ScalpMode.VOLATILITY,
                entry_price=c, stop_loss=stop, target_1=t1, target_2=t2,
                confidence=min(0.82, 0.55+bb_w*5), score=score, atr=atr,
                reason=f"Vol expansion bb_width={bb_w*100:.2f}%", max_hold_minutes=8)
        except Exception as e:
            logger.debug(f"[ExpertScalp] vol {symbol}: {e}"); return None

    def _atr(self, highs, lows, closes, p=14):
        if len(highs) < p+1: return closes[-1]*0.005 if len(closes)>0 else 1.0
        tr = np.maximum(highs[-p:]-lows[-p:],
             np.maximum(np.abs(highs[-p:]-np.roll(closes[-p:],1)),
                        np.abs(lows[-p:]-np.roll(closes[-p:],1))))
        return float(np.mean(tr[1:]))

    def _rsi(self, closes, p=14):
        if len(closes) < p+1: return 50.0
        d = np.diff(closes[-(p+1):]); g = np.where(d>0,d,0); l = np.where(d<0,-d,0)
        return 100 - 100/(1+np.mean(g[-p:])/(np.mean(l[-p:])+1e-10))

    def _vwap(self, closes, highs, lows, volumes):
        if len(closes) < 2: return 0.0
        tp = (highs+lows+closes)/3; tv = np.sum(volumes)
        return float(np.sum(tp*volumes)/tv) if tv>0 else float(closes[-1])

    def _get_regime(self, symbol: str = ""):
        # Regime for the SYMBOL being scanned — not a hardcoded US index. On an
        # NSE-only deployment "SPY" had no OODA context and always returned UNKNOWN.
        try:
            from ooda_engine import get_engine
            return get_engine().get_context(symbol or "^NSEI").regime
        except Exception: pass
        return "UNKNOWN"


_scalper: Optional[ExpertScalper] = None
def get_expert_scalper() -> ExpertScalper:
    global _scalper
    if _scalper is None: _scalper = ExpertScalper()
    return _scalper
