"""
ab_testing.py — Statistical A/B testing for trading strategy improvements.
Uses two-proportion z-test. Max 3 concurrent tests. Forbidden: risk params.
Symbol assignment is deterministic (same symbol always same variant).
"""
import json, math, hashlib, logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)
_STATE_FILE = Path("data/ab_tests.json")

FORBIDDEN_PARAMS = {"daily_loss_limit", "circuit_breaker", "max_risk_pct",
                    "max_positions", "force_squareoff", "atr_sl"}


@dataclass
class ABTest:
    name: str
    description: str
    parameter: str
    control_value: float
    treatment_value: float
    control_wins: int = 0
    control_losses: int = 0
    control_pnl: float = 0.0
    treatment_wins: int = 0
    treatment_losses: int = 0
    treatment_pnl: float = 0.0
    start_date: str = ""
    status: str = "RUNNING"
    min_samples: int = 30
    alpha: float = 0.05
    max_days: int = 14

    def n_control(self) -> int: return self.control_wins + self.control_losses
    def n_treatment(self) -> int: return self.treatment_wins + self.treatment_losses
    def wr_control(self) -> float: return self.control_wins / max(self.n_control(), 1)
    def wr_treatment(self) -> float: return self.treatment_wins / max(self.n_treatment(), 1)

    def is_significant(self) -> Tuple[bool, float]:
        n1, n2 = self.n_control(), self.n_treatment()
        if n1 < self.min_samples or n2 < self.min_samples:
            return False, 1.0
        p1, p2 = self.wr_control(), self.wr_treatment()
        p_pool = (self.control_wins + self.treatment_wins) / (n1 + n2)
        if p_pool <= 0 or p_pool >= 1:
            return False, 1.0
        se = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
        if se == 0:
            return False, 1.0
        z = abs(p1 - p2) / se
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
        return p_val < self.alpha, round(p_val, 4)

    def is_expired(self) -> bool:
        if not self.start_date:
            return False
        try:
            start = datetime.fromisoformat(self.start_date)
            return (datetime.now(ET) - start.replace(tzinfo=ET)).days > self.max_days
        except Exception:
            return False

    def summary(self) -> str:
        sig, p = self.is_significant()
        return (f"{self.name}: Control {self.wr_control():.0%}(n={self.n_control()}) "
                f"vs Treatment {self.wr_treatment():.0%}(n={self.n_treatment()}) "
                f"p={p:.3f} {'✅SIG' if sig else '⏳'} [{self.status}]")


class ABFramework:
    MAX_CONCURRENT = 3

    def __init__(self):
        self.tests: Dict[str, ABTest] = {}
        self._load()

    def register(self, name: str, description: str, parameter: str,
                 control: float, treatment: float, min_samples: int = 30) -> bool:
        if parameter in FORBIDDEN_PARAMS:
            logger.error(f"[AB] Forbidden param: {parameter}")
            return False
        if len([t for t in self.tests.values() if t.status == "RUNNING"]) >= self.MAX_CONCURRENT:
            logger.warning(f"[AB] Max {self.MAX_CONCURRENT} concurrent tests reached")
            return False
        if name in self.tests:
            return False
        self.tests[name] = ABTest(
            name=name, description=description, parameter=parameter,
            control_value=control, treatment_value=treatment,
            min_samples=min_samples, start_date=datetime.now(ET).isoformat())
        self._save()
        logger.info(f"[AB] Registered: {name} | {parameter}: {control} vs {treatment}")
        return True

    def get_variant(self, symbol: str, test_name: str) -> str:
        if test_name not in self.tests or self.tests[test_name].status != "RUNNING":
            return "control"
        return "treatment" if int(hashlib.md5(symbol.encode()).hexdigest(), 16) % 2 == 0 else "control"

    def get_value(self, symbol: str, test_name: str, default: float) -> float:
        if test_name not in self.tests:
            return default
        t = self.tests[test_name]
        return t.treatment_value if self.get_variant(symbol, test_name) == "treatment" else t.control_value

    def record(self, test_name: str, variant: str, pnl: float, was_win: bool):
        if test_name not in self.tests:
            return
        t = self.tests[test_name]
        if variant == "control":
            if was_win: t.control_wins += 1
            else: t.control_losses += 1
            t.control_pnl += pnl
        else:
            if was_win: t.treatment_wins += 1
            else: t.treatment_losses += 1
            t.treatment_pnl += pnl
        sig, _ = t.is_significant()
        if sig and t.n_treatment() >= t.min_samples and t.status == "RUNNING":
            t.status = "ADOPTED" if t.wr_treatment() > t.wr_control() else "REJECTED"
            logger.info(f"[AB] {t.status}: {t.summary()}")
        if t.is_expired() and t.status == "RUNNING":
            t.status = "EXPIRED"
        self._save()

    def adopted_values(self) -> Dict[str, float]:
        return {t.parameter: t.treatment_value for t in self.tests.values() if t.status == "ADOPTED"}

    def status_report(self) -> str:
        if not self.tests:
            return "No A/B tests registered."
        icons = {"RUNNING": "⏳", "ADOPTED": "✅", "REJECTED": "❌", "EXPIRED": "⏰"}
        return "📊 A/B Tests:\n" + "\n".join(
            f"{icons.get(t.status,'?')} {t.summary()}" for t in self.tests.values())

    def _save(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({k: asdict(v) for k, v in self.tests.items()}, indent=2))
        except Exception as e:
            logger.warning(f"[AB] save failed: {e}")

    def _load(self):
        try:
            if _STATE_FILE.exists():
                for k, v in json.loads(_STATE_FILE.read_text()).items():
                    self.tests[k] = ABTest(**v)
        except Exception as e:
            logger.warning(f"[AB] load failed: {e}")


_ab: Optional[ABFramework] = None

def get_ab() -> ABFramework:
    global _ab
    if _ab is None:
        _ab = ABFramework()
    return _ab
