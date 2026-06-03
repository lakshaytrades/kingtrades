"""
pretrain_ml.py — Pre-train ML ensemble on 2 years of real OHLCV history.

Standalone script — run once (or nightly) before starting the bot:
    python3 pretrain_ml.py

Downloads daily OHLCV for 30 symbols via yfinance, engineers 16 features per row,
trains GradientBoosting, RandomForest, ExtraTrees, LogisticRegression, and saves
each model to data/ml_pretrained/{model_name}.pkl.

ml_ensemble.py detects these files at startup and loads them instead of training
on synthetic data.
"""

import os
import pickle
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Configuration ─────────────────────────────────────────────────────────────

SYMBOLS = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD", "INTC", "QCOM",
    # Financials
    "JPM", "GS", "BAC", "MS", "WFC",
    # Energy
    "XOM", "CVX",
    # ETFs
    "SPY", "QQQ", "IWM",
    # High-beta / crypto-adjacent
    "COIN", "MSTR", "PLTR", "SMCI", "MARA", "SOFI", "HOOD", "RIVN", "LCID", "PLUG",
]

PERIOD = "2y"           # 2 years of daily data
LABEL_THRESHOLD = 0.003  # +0.3% next-day close = label 1
TRAIN_SPLIT = 0.80
SAVE_DIR = Path(__file__).parent / "data" / "ml_pretrained"
SLEEP_BETWEEN = 0.5      # seconds between yfinance downloads (rate limit guard)


# ── Feature Engineering ───────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _stoch_k(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, smooth: int = 3) -> pd.Series:
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100 * (close - lowest_low) / denom
    return raw_k.rolling(smooth).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    dm_plus = (high - prev_high).clip(lower=0).where(
        (high - prev_high) > (prev_low - low), other=0.0)
    dm_minus = (prev_low - low).clip(lower=0).where(
        (prev_low - low) > (high - prev_high), other=0.0)
    tr_s = tr.ewm(com=period - 1, adjust=False).mean()
    dm_plus_s = dm_plus.ewm(com=period - 1, adjust=False).mean()
    dm_minus_s = dm_minus.ewm(com=period - 1, adjust=False).mean()
    di_plus = 100 * dm_plus_s / tr_s.replace(0, np.nan)
    di_minus = 100 * dm_minus_s / tr_s.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.ewm(com=period - 1, adjust=False).mean()


def engineer_features(df: pd.DataFrame, spy_5d_returns: pd.Series) -> pd.DataFrame:
    """
    Build 16 features per row.  Returns DataFrame with feature columns + 'label'.
    Drops rows with NaN.
    """
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)
    open_ = df["Open"].astype(float)
    vol   = df["Volume"].astype(float)

    feat = pd.DataFrame(index=df.index)

    # ── Momentum indicators ────────────────────────────────────────────────
    feat["rsi_14"]    = _rsi(close, 14)
    feat["rsi_5"]     = _rsi(close, 5)

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line = ema12 - ema26
    signal    = _ema(macd_line, 9)
    feat["macd_hist"]     = (macd_line - signal) / close.replace(0, np.nan) * 100

    ema9  = _ema(close, 9)
    ema21 = _ema(close, 21)
    feat["ema9_21_ratio"] = ema9 / ema21.replace(0, np.nan)

    # ── Bollinger Bands ────────────────────────────────────────────────────
    bb_mid   = close.rolling(20).mean()
    bb_std   = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    feat["bb_position"] = (close - bb_lower) / bb_range

    # ── Volatility ─────────────────────────────────────────────────────────
    atr = _atr(high, low, close, 14)
    feat["atr_pct"]  = atr / close.replace(0, np.nan)
    log_ret = np.log(close / close.shift(1))
    feat["hvol_20"]  = log_ret.rolling(20).std() * np.sqrt(252)

    # ── Volume ─────────────────────────────────────────────────────────────
    vol_ma = vol.rolling(20).mean().replace(0, np.nan)
    feat["vol_ratio"] = vol / vol_ma

    # ── Momentum returns ───────────────────────────────────────────────────
    feat["mom_5"]   = close.pct_change(5)
    feat["mom_10"]  = close.pct_change(10)
    feat["mom_20"]  = close.pct_change(20)

    # ── Relative strength vs SPY ───────────────────────────────────────────
    sym_5d = close.pct_change(5)
    # Align index — spy_5d_returns may have different trading days
    spy_aligned = spy_5d_returns.reindex(df.index, method="ffill")
    feat["rs_spy"] = sym_5d - spy_aligned

    # ── Stochastic %K ──────────────────────────────────────────────────────
    feat["stoch_k"] = _stoch_k(high, low, close)

    # ── ADX ────────────────────────────────────────────────────────────────
    feat["adx_14"]  = _adx(high, low, close, 14)

    # ── Gap pct (open vs prev close) ───────────────────────────────────────
    feat["gap_pct"]     = (open_ - close.shift(1)) / close.shift(1).replace(0, np.nan)

    # ── Price vs 52-week high ──────────────────────────────────────────────
    rolling_52h = close.rolling(252).max().replace(0, np.nan)
    feat["price_vs_52h"] = close / rolling_52h

    # ── Label: 1 if next-day close > today × (1 + threshold) ─────────────
    feat["label"] = (close.shift(-1) > close * (1 + LABEL_THRESHOLD)).astype(int)

    feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
    return feat


# ── Download + feature engineering ───────────────────────────────────────────

def build_dataset() -> tuple:
    """Returns (X, y, feature_names)."""
    import yfinance as yf

    all_rows: list = []
    feature_cols = None

    # Download SPY first for rs_spy computation
    print("Downloading SPY for RS calculation...")
    spy_df = yf.download("SPY", period=PERIOD, auto_adjust=True, progress=False)
    if spy_df.empty:
        print("  WARNING: SPY download failed — rs_spy will be 0")
        spy_5d = pd.Series(dtype=float)
    else:
        spy_close = spy_df["Close"].astype(float)
        if hasattr(spy_close, 'squeeze'):
            spy_close = spy_close.squeeze()
        spy_5d = spy_close.pct_change(5)

    for sym in SYMBOLS:
        try:
            print(f"  Downloading {sym}...", end=" ", flush=True)
            df = yf.download(sym, period=PERIOD, auto_adjust=True, progress=False)
            if df.empty:
                print("EMPTY — skip")
                continue
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            feat_df = engineer_features(df, spy_5d)
            if len(feat_df) < 100:
                print(f"only {len(feat_df)} rows — skip")
                continue
            if feature_cols is None:
                feature_cols = [c for c in feat_df.columns if c != "label"]
            X_sym = feat_df[feature_cols].values
            y_sym = feat_df["label"].values
            all_rows.append((X_sym, y_sym))
            print(f"{len(feat_df)} rows, label_rate={y_sym.mean():.2%}")
            time.sleep(SLEEP_BETWEEN)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

    if not all_rows:
        print("\nWARNING: No live data downloaded (network unavailable).")
        print("Falling back to synthetic training data (16-feature anchor points).")
        print("Re-run with internet access to train on real historical data.")
        return _build_synthetic_dataset()

    X = np.vstack([r[0] for r in all_rows])
    y = np.concatenate([r[1] for r in all_rows])

    # Shuffle
    rng = np.random.default_rng(42)
    idx = rng.permutation(len(y))
    return X[idx], y[idx], feature_cols


FEATURE_NAMES_PRETRAIN = [
    "rsi_14", "rsi_5", "macd_hist", "ema9_21_ratio", "bb_position",
    "atr_pct", "vol_ratio", "mom_5", "mom_10", "mom_20",
    "rs_spy", "stoch_k", "adx_14", "gap_pct", "price_vs_52h", "hvol_20",
]


def _build_synthetic_dataset() -> tuple:
    """
    Generate 3000 synthetic training samples for the 16 pretrain features.
    Uses anchor points derived from known statistical edges.
    Only used when yfinance is unavailable.
    """
    rng = np.random.default_rng(42)
    n_feat = len(FEATURE_NAMES_PRETRAIN)
    rows = []
    labels = []

    def add_anchor(vals: list, n: int, win_rate: float, noise: float = 0.05):
        vec = np.array(vals, dtype=float)
        for _ in range(n):
            x = vec + rng.normal(0, noise, size=n_feat).astype(float)
            rows.append(x)
            labels.append(1 if rng.random() < win_rate else 0)

    # rsi_14, rsi_5, macd_hist, ema9_21_ratio, bb_position, atr_pct,
    # vol_ratio, mom_5, mom_10, mom_20, rs_spy, stoch_k, adx_14,
    # gap_pct, price_vs_52h, hvol_20
    # Strong bull momentum
    add_anchor([55, 58, 0.15, 1.004, 0.72, 0.012, 2.1, 0.04, 0.07, 0.12,
                0.02, 68, 30, 0.003, 0.95, 0.28], 350, 0.68)
    # Overbought extended
    add_anchor([78, 82, 0.03, 1.008, 0.92, 0.009, 1.1, 0.09, 0.14, 0.20,
                0.03, 88, 22, 0.005, 0.99, 0.22], 300, 0.31)
    # Strong trend + ADX
    add_anchor([60, 63, 0.22, 1.006, 0.76, 0.015, 2.4, 0.05, 0.09, 0.15,
                0.03, 72, 38, 0.002, 0.97, 0.30], 300, 0.72)
    # Midday chop
    add_anchor([50, 51, 0.01, 1.000, 0.51, 0.006, 0.8, 0.00, 0.01, 0.02,
                0.00, 50, 13, 0.000, 0.88, 0.18], 250, 0.38)
    # Opening momentum
    add_anchor([62, 65, 0.30, 1.007, 0.80, 0.018, 3.2, 0.06, 0.10, 0.14,
                0.04, 74, 34, 0.004, 0.96, 0.32], 250, 0.70)
    # Short squeeze
    add_anchor([67, 70, 0.25, 1.005, 0.85, 0.020, 3.5, 0.08, 0.12, 0.18,
                0.05, 80, 28, 0.006, 0.93, 0.38], 200, 0.71)
    # Bear momentum
    add_anchor([38, 35, -0.20, 0.996, 0.18, 0.022, 2.8, -0.06, -0.10, -0.14,
                -0.04, 22, 33, -0.004, 0.82, 0.36], 200, 0.29)
    # Neutral ranging
    add_anchor([50, 50, 0.00, 1.001, 0.50, 0.007, 1.0, 0.01, 0.01, 0.02,
                0.00, 50, 12, 0.001, 0.90, 0.20], 150, 0.42)

    X = np.vstack(rows)
    y = np.array(labels, dtype=int)
    idx = rng.permutation(len(y))
    return X[idx], y[idx], FEATURE_NAMES_PRETRAIN


# ── Training ──────────────────────────────────────────────────────────────────

def train_and_save(X_train: np.ndarray, X_test: np.ndarray,
                   y_train: np.ndarray, y_test: np.ndarray,
                   feature_names: list) -> None:
    from sklearn.ensemble import (
        GradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier,
    )
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.preprocessing import StandardScaler

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    models = {
        "gbm": GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=20, random_state=42,
        ),
        "rf": RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_split=10,
            random_state=42, n_jobs=-1,
        ),
        "et": ExtraTreesClassifier(
            n_estimators=200, max_depth=9, min_samples_split=8,
            random_state=43, n_jobs=-1,
        ),
        "lr": LogisticRegression(max_iter=1000, C=0.5, random_state=42),
    }

    # LR needs scaling — fit scaler on training data
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled  = scaler.transform(X_test)

    print(f"\n{'='*60}")
    print(f"Training set: {len(y_train):,} samples | label_rate={y_train.mean():.2%}")
    print(f"Test set:     {len(y_test):,}  samples | label_rate={y_test.mean():.2%}")
    print(f"{'='*60}")

    for name, model in models.items():
        print(f"\nTraining {name.upper()}...")
        t0 = time.time()
        if name == "lr":
            model.fit(X_train_scaled, y_train)
            y_pred = model.predict(X_test_scaled)
            y_prob = model.predict_proba(X_test_scaled)[:, 1]
        else:
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            y_prob = model.predict_proba(X_test)[:, 1]

        acc = accuracy_score(y_test, y_pred)
        try:
            auc = roc_auc_score(y_test, y_prob)
        except Exception:
            auc = float("nan")

        print(f"  Accuracy: {acc:.4f}  |  AUC-ROC: {auc:.4f}  |  time: {time.time()-t0:.1f}s")

        # Feature importances (skip LR)
        if hasattr(model, "feature_importances_") and feature_names:
            importances = model.feature_importances_
            ranked = sorted(zip(feature_names, importances), key=lambda x: -x[1])
            print(f"  Top-5 features: " + ", ".join(
                f"{n}={v:.4f}" for n, v in ranked[:5]
            ))

        # Save model (and scaler alongside LR)
        payload = {"model": model, "feature_names": feature_names}
        if name == "lr":
            payload["scaler"] = scaler
        path = SAVE_DIR / f"{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  Saved → {path}")

    # Save metadata
    meta = {
        "feature_names": feature_names,
        "train_samples": int(len(y_train)),
        "test_samples":  int(len(y_test)),
        "label_threshold": LABEL_THRESHOLD,
        "symbols": SYMBOLS,
        "created_at": pd.Timestamp.now().isoformat(),
    }
    with open(SAVE_DIR / "meta.pkl", "wb") as f:
        pickle.dump(meta, f)
    print(f"\nMetadata saved → {SAVE_DIR / 'meta.pkl'}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("KingTrades ML Pre-trainer v21.0")
    print("Downloading 2yr OHLCV history for 30 symbols...")
    print("=" * 60)

    X, y, feature_names = build_dataset()
    print(f"\nTotal dataset: {len(y):,} rows, {len(feature_names)} features")
    print(f"Label distribution: {y.mean():.2%} positive")

    split = int(len(y) * TRAIN_SPLIT)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    train_and_save(X_train, X_test, y_train, y_test, feature_names)

    print("\n" + "=" * 60)
    print("Pre-training complete.")
    print(f"Models saved to: {SAVE_DIR.resolve()}")
    print("ml_ensemble.py will auto-load these on next bot startup.")
    print("=" * 60)


if __name__ == "__main__":
    main()
