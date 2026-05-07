"""
win_predictor.py — Random Forest win-probability predictor
Trained on the bot's own SQLite trade journal. Smarter with every trade.
After 30+ trades: predicts win probability for incoming signals.
Auto-retrains every 20 new trades.
"""

import logging
import os
import pickle
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)

DB_PATH = "logs/trades/trade_journal.db"

FEATURES = [
    "signal_score",
    "rsi",
    "volume_ratio",
    "hour",
    "day_of_week",
    "direction_bin",
    "session_bin",
    "macd_positive",
    "vwap_above",
]

_SESSION_MAP = {"OPENING": 0, "OPENING_DRIVE": 0, "MORNING": 1, "MIDDAY_CHOP": 2, "AFTERNOON": 2, "CLOSING": 2}


class WinPredictor:
    MIN_SAMPLES = 30
    RETRAIN_EVERY = 20
    MODEL_FILE = "data/win_predictor.pkl"

    def __init__(self):
        self._model = None
        self._samples_at_last_train = 0
        self._accuracy: float = 0.0
        self._feature_importance: Dict[str, float] = {}
        self._total_samples: int = 0
        Path("data").mkdir(parents=True, exist_ok=True)
        self._load_model()

    def predict(self, signal_data: dict) -> Tuple[float, bool]:
        if self._model is None:
            return 0.55, False

        try:
            features = self._signal_to_features(signal_data)
            prob = float(self._model.predict_proba([features])[0][1])
            is_confident = self._total_samples >= 50
            return round(prob, 4), is_confident
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] WinPredictor.predict failed: {e}")
            return 0.55, False

    def train(self) -> bool:
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import cross_val_score
        except ImportError:
            logger.error(f"[{format_ist_timestamp()}] scikit-learn not installed — WinPredictor disabled")
            return False

        try:
            df = self._load_trades()
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] WinPredictor: cannot load trades — {e}")
            return False

        if df.empty or len(df) < self.MIN_SAMPLES:
            logger.info(
                f"[{format_ist_timestamp()}] WinPredictor: only {len(df)} trades "
                f"(need {self.MIN_SAMPLES}) — skipping train"
            )
            return False

        try:
            X_df = self._build_features(df)
            y = (df["pnl"] > 0).astype(int).values
            X = X_df[FEATURES].values

            model = RandomForestClassifier(
                n_estimators=100,
                max_depth=5,
                min_samples_leaf=3,
                class_weight="balanced",
                random_state=42,
            )
            model.fit(X, y)

            cv_scores = cross_val_score(model, X, y, cv=min(5, len(df) // 6), scoring="accuracy")
            self._accuracy = float(cv_scores.mean())
            self._feature_importance = dict(zip(FEATURES, model.feature_importances_.tolist()))
            self._total_samples = len(df)
            self._samples_at_last_train = len(df)
            self._model = model

            pickle.dump(
                {"model": model, "accuracy": self._accuracy,
                 "feature_importance": self._feature_importance,
                 "samples": self._total_samples},
                open(self.MODEL_FILE, "wb"),
            )

            logger.info(
                f"[{format_ist_timestamp()}] WinPredictor trained: "
                f"{self._total_samples} samples, accuracy={self._accuracy:.2%}"
            )
            return True

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] WinPredictor train error: {e}")
            return False

    def get_model_stats(self) -> dict:
        return {
            "samples": self._total_samples,
            "accuracy": round(self._accuracy, 4),
            "feature_importance": self._feature_importance,
            "trained": self._model is not None,
        }

    def should_retrain(self) -> bool:
        try:
            current_count = self._db_trade_count()
        except Exception:
            return False
        return (current_count - self._samples_at_last_train) >= self.RETRAIN_EVERY

    def _load_trades(self) -> pd.DataFrame:
        if not Path(DB_PATH).exists():
            return pd.DataFrame()

        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql(
                """
                SELECT symbol, direction, signal_score, rsi_at_entry AS rsi,
                       macd_hist, volume_ratio, session,
                       pnl, exit_reason, entry_time_ist
                FROM trades
                WHERE exit_reason IS NOT NULL AND exit_reason != ''
                  AND pnl IS NOT NULL
                ORDER BY entry_time_ist ASC
                """,
                conn,
            )

        if df.empty:
            return df

        df = df.rename(columns={"rsi_at_entry": "rsi"} if "rsi_at_entry" in df.columns else {})

        df["signal_score"] = pd.to_numeric(df["signal_score"], errors="coerce").fillna(50.0)
        df["rsi"] = pd.to_numeric(df["rsi"], errors="coerce").fillna(50.0)
        df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(1.0)
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)

        if "macd_hist" not in df.columns:
            df["macd_hist"] = 0.0
        df["macd_hist"] = pd.to_numeric(df["macd_hist"], errors="coerce").fillna(0.0)

        if "vwap_deviation" not in df.columns:
            df["vwap_deviation"] = 0.0

        return df

    def _build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()

        def _parse_hour(ts_str):
            try:
                for fmt in ("%Y-%m-%d %H:%M:%S IST", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return pd.to_datetime(ts_str, format=fmt).hour
                    except Exception:
                        continue
                return pd.to_datetime(ts_str).hour
            except Exception:
                return 11

        def _parse_dow(ts_str):
            try:
                for fmt in ("%Y-%m-%d %H:%M:%S IST", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return pd.to_datetime(ts_str, format=fmt).weekday()
                    except Exception:
                        continue
                return pd.to_datetime(ts_str).weekday()
            except Exception:
                return 2

        if "entry_time_ist" in out.columns:
            out["hour"] = out["entry_time_ist"].apply(_parse_hour)
            out["day_of_week"] = out["entry_time_ist"].apply(_parse_dow)
        else:
            out["hour"] = 11
            out["day_of_week"] = 2

        out["direction_bin"] = (out["direction"].str.upper() == "LONG").astype(int)
        out["session_bin"] = out["session"].str.upper().map(_SESSION_MAP).fillna(1).astype(int)
        out["macd_positive"] = (out["macd_hist"] > 0).astype(int)
        out["vwap_above"] = (out.get("vwap_deviation", pd.Series([0.0] * len(out))) > 0).astype(int)

        for col in FEATURES:
            if col not in out.columns:
                out[col] = 0

        return out

    def _signal_to_features(self, signal_data: dict) -> np.ndarray:
        direction = signal_data.get("direction", "LONG").upper()
        session = signal_data.get("session", "MORNING").upper()

        row = {
            "signal_score": float(signal_data.get("signal_score", 50.0)),
            "rsi": float(signal_data.get("rsi", 50.0)),
            "volume_ratio": float(signal_data.get("volume_ratio", 1.0)),
            "hour": int(signal_data.get("hour", 11)),
            "day_of_week": int(signal_data.get("day_of_week", 2)),
            "direction_bin": 1 if direction == "LONG" else 0,
            "session_bin": _SESSION_MAP.get(session, 1),
            "macd_positive": 1 if float(signal_data.get("macd_hist", 0.0)) > 0 else 0,
            "vwap_above": 1 if float(signal_data.get("vwap_deviation", 0.0)) > 0 else 0,
        }
        return np.array([row[f] for f in FEATURES], dtype=float)

    def _load_model(self):
        if not Path(self.MODEL_FILE).exists():
            return
        try:
            data = pickle.load(open(self.MODEL_FILE, "rb"))
            self._model = data["model"]
            self._accuracy = data.get("accuracy", 0.0)
            self._feature_importance = data.get("feature_importance", {})
            self._total_samples = data.get("samples", 0)
            self._samples_at_last_train = self._total_samples
            logger.info(
                f"[{format_ist_timestamp()}] WinPredictor loaded: "
                f"{self._total_samples} samples, accuracy={self._accuracy:.2%}"
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] WinPredictor: could not load saved model — {e}")

    def _db_trade_count(self) -> int:
        if not Path(DB_PATH).exists():
            return 0
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE exit_reason IS NOT NULL AND exit_reason != ''"
            ).fetchone()
        return row[0] if row else 0


_predictor: Optional[WinPredictor] = None


def get_win_predictor() -> WinPredictor:
    global _predictor
    if _predictor is None:
        _predictor = WinPredictor()
        _predictor.train()
    elif _predictor.should_retrain():
        _predictor.train()
    return _predictor


def conviction_adjustment(win_prob: float) -> int:
    """
    Returns integer delta to apply to conviction score based on win probability.
    Caller: dynamic_leverage.py
    """
    if win_prob > 0.70:
        return +15
    if win_prob > 0.60:
        return +8
    if win_prob < 0.30:
        return -999
    if win_prob < 0.40:
        return -15
    return 0


def should_block_by_win_prob(win_prob: float) -> bool:
    return win_prob < 0.30
