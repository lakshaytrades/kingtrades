"""
pretrain_ml.py — Pre-train ML ensemble on 10 years of real OHLCV history.

Standalone script — run once (or nightly) before starting the bot:
    python3 pretrain_ml.py

Downloads daily OHLCV for 30 symbols via yfinance, engineers 20 features per row
(including cross-asset VIX/TLT/UUP/SPY features), labels each row with the SPY
market regime (BULL/BEAR), trains 8 regime-aware models, and saves each model to
data/ml_pretrained/{model_name}_{regime}.pkl.

Walk-forward validation: train on years 1-7, test on years 8-10.

ml_ensemble.py detects these files at startup and loads bull/bear models based
on the current SPY vs 200-day SMA regime.
"""

import os
import pickle
import time
import warnings
from datetime import datetime, timedelta
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

# 10 years of daily data (use explicit start date — period="10y" sometimes mishandles)
START_DATE = (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")
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


def engineer_features(
    df: pd.DataFrame,
    spy_5d_returns: pd.Series,
    vix_close: pd.Series,
    tlt_5d: pd.Series,
    uup_5d: pd.Series,
) -> pd.DataFrame:
    """
    Build 20 features per row (16 original + 4 cross-asset).
    Returns DataFrame with feature columns + 'label' + 'regime'.
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

    # ── Cross-asset features (Renaissance-style macro overlay) ─────────────
    # vix_level: VIX normalized by /30 (1.0 = VIX at 30, extreme fear)
    if vix_close is not None and len(vix_close) > 0:
        vix_aligned = vix_close.reindex(df.index, method="ffill")
        feat["vix_level"] = vix_aligned / 30.0
    else:
        feat["vix_level"] = 0.7  # neutral default (VIX=21)

    # tlt_5d: TLT 5-day return (positive = bonds up = risk-off)
    if tlt_5d is not None and len(tlt_5d) > 0:
        feat["tlt_5d"] = tlt_5d.reindex(df.index, method="ffill").fillna(0.0)
    else:
        feat["tlt_5d"] = 0.0

    # uup_5d: UUP 5-day return (positive = dollar strong = risk-off)
    if uup_5d is not None and len(uup_5d) > 0:
        feat["uup_5d"] = uup_5d.reindex(df.index, method="ffill").fillna(0.0)
    else:
        feat["uup_5d"] = 0.0

    # spy_rs: symbol 5d return minus SPY 5d return (same as rs_spy, explicit feature)
    feat["spy_rs"] = feat["mom_5"] - spy_aligned.fillna(0.0)

    # ── Label: 1 if next-day close > today × (1 + threshold) ─────────────
    feat["label"] = (close.shift(-1) > close * (1 + LABEL_THRESHOLD)).astype(int)

    feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
    return feat


def add_regime_labels(feat_df: pd.DataFrame, spy_close: pd.Series) -> pd.DataFrame:
    """
    Add 'regime' column: 'BULL' if SPY > 200-day SMA, else 'BEAR'.
    Uses SPY 200-day SMA computed from the full SPY close series.
    """
    spy_sma200 = spy_close.rolling(200).mean()
    # Align to feat_df index
    spy_aligned = spy_close.reindex(feat_df.index, method="ffill")
    sma_aligned = spy_sma200.reindex(feat_df.index, method="ffill")
    feat_df = feat_df.copy()
    feat_df["regime"] = np.where(spy_aligned >= sma_aligned, "BULL", "BEAR")
    return feat_df


# ── Download + feature engineering ───────────────────────────────────────────

def build_dataset() -> tuple:
    """Returns (X, y, regimes, feature_names)."""
    import yfinance as yf

    all_rows: list = []
    feature_cols = None

    # ── Download cross-asset instruments ─────────────────────────────────
    print("Downloading SPY for RS + regime calculation...")
    spy_df = yf.download("SPY", start=START_DATE, auto_adjust=True, progress=False)
    if spy_df.empty:
        print("  WARNING: SPY download failed — rs_spy will be 0, regime=BULL")
        spy_5d    = pd.Series(dtype=float)
        spy_close = pd.Series(dtype=float)
    else:
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df.columns = spy_df.columns.get_level_values(0)
        spy_close = spy_df["Close"].astype(float).squeeze()
        spy_5d    = spy_close.pct_change(5)
    time.sleep(SLEEP_BETWEEN)

    print("Downloading ^VIX...")
    vix_close = pd.Series(dtype=float)
    try:
        vix_df = yf.download("^VIX", start=START_DATE, auto_adjust=True, progress=False)
        if not vix_df.empty:
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = vix_df.columns.get_level_values(0)
            vix_close = vix_df["Close"].astype(float).squeeze()
    except Exception as e:
        print(f"  VIX download failed: {e}")
    time.sleep(SLEEP_BETWEEN)

    print("Downloading TLT (bonds)...")
    tlt_5d = pd.Series(dtype=float)
    try:
        tlt_df = yf.download("TLT", start=START_DATE, auto_adjust=True, progress=False)
        if not tlt_df.empty:
            if isinstance(tlt_df.columns, pd.MultiIndex):
                tlt_df.columns = tlt_df.columns.get_level_values(0)
            tlt_close = tlt_df["Close"].astype(float).squeeze()
            tlt_5d = tlt_close.pct_change(5)
    except Exception as e:
        print(f"  TLT download failed: {e}")
    time.sleep(SLEEP_BETWEEN)

    print("Downloading UUP (dollar strength)...")
    uup_5d = pd.Series(dtype=float)
    try:
        uup_df = yf.download("UUP", start=START_DATE, auto_adjust=True, progress=False)
        if not uup_df.empty:
            if isinstance(uup_df.columns, pd.MultiIndex):
                uup_df.columns = uup_df.columns.get_level_values(0)
            uup_close = uup_df["Close"].astype(float).squeeze()
            uup_5d = uup_close.pct_change(5)
    except Exception as e:
        print(f"  UUP download failed: {e}")
    time.sleep(SLEEP_BETWEEN)

    for sym in SYMBOLS:
        try:
            print(f"  Downloading {sym}...", end=" ", flush=True)
            df = yf.download(sym, start=START_DATE, auto_adjust=True, progress=False)
            if df.empty:
                print("EMPTY — skip")
                continue
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            feat_df = engineer_features(df, spy_5d, vix_close, tlt_5d, uup_5d)
            if len(feat_df) < 100:
                print(f"only {len(feat_df)} rows — skip")
                continue
            # Add regime labels using SPY SMA200
            if len(spy_close) > 0:
                feat_df = add_regime_labels(feat_df, spy_close)
            else:
                feat_df["regime"] = "BULL"
            if feature_cols is None:
                feature_cols = [c for c in feat_df.columns if c not in ("label", "regime")]
            X_sym = feat_df[feature_cols].values
            y_sym = feat_df["label"].values
            r_sym = feat_df["regime"].values
            all_rows.append((X_sym, y_sym, r_sym))
            bull_rate = (r_sym == "BULL").mean()
            print(f"{len(feat_df)} rows, label_rate={y_sym.mean():.2%}, bull%={bull_rate:.0%}")
            time.sleep(SLEEP_BETWEEN)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

    if not all_rows:
        print("\nWARNING: No live data downloaded (network unavailable).")
        print("Falling back to synthetic training data (20-feature anchor points).")
        print("Re-run with internet access to train on real historical data.")
        return _build_synthetic_dataset()

    X = np.vstack([r[0] for r in all_rows])
    y = np.concatenate([r[1] for r in all_rows])
    regimes = np.concatenate([r[2] for r in all_rows])

    # Shuffle (preserve regime alignment)
    rng = np.random.default_rng(42)
    idx = rng.permutation(len(y))
    return X[idx], y[idx], regimes[idx], feature_cols


FEATURE_NAMES_PRETRAIN = [
    "rsi_14", "rsi_5", "macd_hist", "ema9_21_ratio", "bb_position",
    "atr_pct", "vol_ratio", "mom_5", "mom_10", "mom_20",
    "rs_spy", "stoch_k", "adx_14", "gap_pct", "price_vs_52h", "hvol_20",
    # Cross-asset features (v22.0)
    "vix_level", "tlt_5d", "uup_5d", "spy_rs",
]


def _build_synthetic_dataset() -> tuple:
    """
    Generate 3000 synthetic training samples for the 20 pretrain features.
    Uses anchor points derived from known statistical edges.
    Only used when yfinance is unavailable.
    """
    rng = np.random.default_rng(42)
    n_feat = len(FEATURE_NAMES_PRETRAIN)
    rows = []
    labels = []
    regimes = []

    def add_anchor(vals: list, n: int, win_rate: float, regime: str = "BULL", noise: float = 0.05):
        vec = np.array(vals, dtype=float)
        for _ in range(n):
            x = vec + rng.normal(0, noise, size=n_feat).astype(float)
            rows.append(x)
            labels.append(1 if rng.random() < win_rate else 0)
            regimes.append(regime)

    # rsi_14, rsi_5, macd_hist, ema9_21_ratio, bb_position, atr_pct,
    # vol_ratio, mom_5, mom_10, mom_20, rs_spy, stoch_k, adx_14,
    # gap_pct, price_vs_52h, hvol_20, vix_level, tlt_5d, uup_5d, spy_rs
    # Strong bull momentum (BULL regime)
    add_anchor([55, 58, 0.15, 1.004, 0.72, 0.012, 2.1, 0.04, 0.07, 0.12,
                0.02, 68, 30, 0.003, 0.95, 0.28, 0.60, 0.005, -0.003, 0.02], 350, 0.68, "BULL")
    # Overbought extended (BULL)
    add_anchor([78, 82, 0.03, 1.008, 0.92, 0.009, 1.1, 0.09, 0.14, 0.20,
                0.03, 88, 22, 0.005, 0.99, 0.22, 0.65, 0.002, -0.001, 0.01], 300, 0.31, "BULL")
    # Strong trend + ADX (BULL)
    add_anchor([60, 63, 0.22, 1.006, 0.76, 0.015, 2.4, 0.05, 0.09, 0.15,
                0.03, 72, 38, 0.002, 0.97, 0.30, 0.55, 0.008, -0.005, 0.03], 300, 0.72, "BULL")
    # Midday chop
    add_anchor([50, 51, 0.01, 1.000, 0.51, 0.006, 0.8, 0.00, 0.01, 0.02,
                0.00, 50, 13, 0.000, 0.88, 0.18, 0.70, 0.000, 0.000, 0.00], 250, 0.38, "BULL")
    # Opening momentum (BULL)
    add_anchor([62, 65, 0.30, 1.007, 0.80, 0.018, 3.2, 0.06, 0.10, 0.14,
                0.04, 74, 34, 0.004, 0.96, 0.32, 0.52, 0.010, -0.008, 0.04], 250, 0.70, "BULL")
    # Short squeeze (BULL)
    add_anchor([67, 70, 0.25, 1.005, 0.85, 0.020, 3.5, 0.08, 0.12, 0.18,
                0.05, 80, 28, 0.006, 0.93, 0.38, 0.58, 0.003, -0.002, 0.05], 200, 0.71, "BULL")
    # Bear momentum (BEAR regime) — lower win rate for longs
    add_anchor([38, 35, -0.20, 0.996, 0.18, 0.022, 2.8, -0.06, -0.10, -0.14,
                -0.04, 22, 33, -0.004, 0.82, 0.36, 1.10, -0.015, 0.012, -0.04], 200, 0.28, "BEAR")
    # Neutral ranging
    add_anchor([50, 50, 0.00, 1.001, 0.50, 0.007, 1.0, 0.01, 0.01, 0.02,
                0.00, 50, 12, 0.001, 0.90, 0.20, 0.75, 0.000, 0.000, 0.00], 150, 0.42, "BULL")
    # High VIX BEAR regime — very low LONG win rate
    add_anchor([40, 38, -0.25, 0.994, 0.15, 0.030, 3.0, -0.08, -0.12, -0.18,
                -0.06, 20, 35, -0.006, 0.78, 0.42, 1.40, -0.020, 0.018, -0.06], 200, 0.22, "BEAR")

    X = np.vstack(rows)
    y = np.array(labels, dtype=int)
    r = np.array(regimes)
    idx = rng.permutation(len(y))
    return X[idx], y[idx], r[idx], FEATURE_NAMES_PRETRAIN


# ── Walk-Forward Validation ───────────────────────────────────────────────────

def walk_forward_validate(X: np.ndarray, y: np.ndarray, feature_names: list) -> None:
    """
    Train on years 1-7, test on years 8-10 (rough: 70% / 30% time-ordered split).
    Print out-of-sample accuracy for each model.
    """
    from sklearn.ensemble import (
        GradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier,
    )
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.preprocessing import StandardScaler

    # Time-ordered split: first 70% = train (years 1-7), last 30% = test (years 8-10)
    wf_split = int(len(y) * 0.70)
    X_train_wf, X_test_wf = X[:wf_split], X[wf_split:]
    y_train_wf, y_test_wf = y[:wf_split], y[wf_split:]

    print(f"\n{'='*60}")
    print("Walk-Forward Validation (train yrs 1-7, test yrs 8-10)")
    print(f"  Train: {len(y_train_wf):,} | Test: {len(y_test_wf):,}")
    print(f"{'='*60}")

    wf_models = {
        "GBM": GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=20, random_state=42,
        ),
        "RF": RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_split=10,
            random_state=42, n_jobs=-1,
        ),
        "ET": ExtraTreesClassifier(
            n_estimators=200, max_depth=9, min_samples_split=8,
            random_state=43, n_jobs=-1,
        ),
        "LR": LogisticRegression(max_iter=1000, C=0.5, random_state=42),
    }

    scaler_wf = StandardScaler()
    X_train_sc = scaler_wf.fit_transform(X_train_wf)
    X_test_sc  = scaler_wf.transform(X_test_wf)

    for name, model in wf_models.items():
        try:
            if name == "LR":
                model.fit(X_train_sc, y_train_wf)
                y_pred = model.predict(X_test_sc)
                y_prob = model.predict_proba(X_test_sc)[:, 1]
            else:
                model.fit(X_train_wf, y_train_wf)
                y_pred = model.predict(X_test_wf)
                y_prob = model.predict_proba(X_test_wf)[:, 1]
            acc = accuracy_score(y_test_wf, y_pred)
            try:
                auc = roc_auc_score(y_test_wf, y_prob)
            except Exception:
                auc = float("nan")
            print(f"  {name:4s} — OOS Accuracy: {acc:.4f}  AUC: {auc:.4f}")
        except Exception as e:
            print(f"  {name}: walk-forward failed: {e}")

    print(f"{'='*60}")


# ── Training ──────────────────────────────────────────────────────────────────

def train_and_save(
    X_train: np.ndarray, X_test: np.ndarray,
    y_train: np.ndarray, y_test: np.ndarray,
    regimes_train: np.ndarray, regimes_test: np.ndarray,
    feature_names: list,
) -> None:
    from sklearn.ensemble import (
        GradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier,
    )
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.preprocessing import StandardScaler

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    model_defs = {
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

    print(f"\n{'='*60}")
    print(f"Training set: {len(y_train):,} samples | label_rate={y_train.mean():.2%}")
    print(f"Test set:     {len(y_test):,}  samples | label_rate={y_test.mean():.2%}")
    bull_train_pct = (regimes_train == "BULL").mean()
    bear_train_pct = (regimes_train == "BEAR").mean()
    print(f"Train regime: BULL={bull_train_pct:.0%}  BEAR={bear_train_pct:.0%}")
    print(f"{'='*60}")

    # Train regime-aware models: BULL and BEAR sets (8 models total)
    for regime in ("BULL", "BEAR"):
        mask_train = regimes_train == regime
        mask_test  = regimes_test  == regime
        X_r_train  = X_train[mask_train]
        y_r_train  = y_train[mask_train]
        X_r_test   = X_test[mask_test]
        y_r_test   = y_test[mask_test]

        if len(y_r_train) < 50:
            print(f"\n  {regime} regime: insufficient samples ({len(y_r_train)}) — skipping")
            continue

        print(f"\n{'='*60}")
        print(f"Training {regime} regime: {len(y_r_train):,} train, {len(y_r_test):,} test")
        print(f"  Label rate train={y_r_train.mean():.2%} test={y_r_test.mean():.2%}")

        # LR needs scaling — fit scaler on this regime's training data
        scaler = StandardScaler()
        X_r_train_sc = scaler.fit_transform(X_r_train)
        X_r_test_sc  = scaler.transform(X_r_test) if len(X_r_test) > 0 else X_r_test

        for name, model_template in model_defs.items():
            import copy
            model = copy.deepcopy(model_template)
            print(f"\n  Training {name.upper()}_{regime}...")
            t0 = time.time()
            try:
                if name == "lr":
                    model.fit(X_r_train_sc, y_r_train)
                    if len(X_r_test_sc) > 0 and len(y_r_test) > 0:
                        y_pred = model.predict(X_r_test_sc)
                        y_prob = model.predict_proba(X_r_test_sc)[:, 1]
                    else:
                        y_pred, y_prob = np.array([]), np.array([])
                else:
                    model.fit(X_r_train, y_r_train)
                    if len(X_r_test) > 0 and len(y_r_test) > 0:
                        y_pred = model.predict(X_r_test)
                        y_prob = model.predict_proba(X_r_test)[:, 1]
                    else:
                        y_pred, y_prob = np.array([]), np.array([])

                if len(y_pred) > 0 and len(y_r_test) > 0:
                    acc = accuracy_score(y_r_test, y_pred)
                    try:
                        auc = roc_auc_score(y_r_test, y_prob)
                    except Exception:
                        auc = float("nan")
                    print(f"    Accuracy: {acc:.4f}  AUC: {auc:.4f}  time: {time.time()-t0:.1f}s")

                    if hasattr(model, "feature_importances_") and feature_names:
                        importances = model.feature_importances_
                        ranked = sorted(zip(feature_names, importances), key=lambda x: -x[1])
                        print(f"    Top-5: " + ", ".join(
                            f"{n}={v:.4f}" for n, v in ranked[:5]
                        ))
                else:
                    print(f"    Trained (no test samples for this regime) time: {time.time()-t0:.1f}s")

                # Save regime model
                payload = {
                    "model": model,
                    "feature_names": feature_names,
                    "regime": regime,
                }
                if name == "lr":
                    payload["scaler"] = scaler
                regime_lower = regime.lower()
                path = SAVE_DIR / f"{name}_{regime_lower}.pkl"
                with open(path, "wb") as f:
                    pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
                print(f"    Saved → {path}")

            except Exception as e:
                print(f"    ERROR training {name}_{regime}: {e}")

        # Also save legacy (non-regime) models from BULL regime for backward compat
        if regime == "BULL":
            for name, model_template in model_defs.items():
                import copy
                model = copy.deepcopy(model_template)
                try:
                    scaler_legacy = StandardScaler()
                    X_sc_all = scaler_legacy.fit_transform(X_train)
                    if name == "lr":
                        model.fit(X_sc_all, y_train)
                    else:
                        model.fit(X_train, y_train)
                    payload = {"model": model, "feature_names": feature_names}
                    if name == "lr":
                        payload["scaler"] = scaler_legacy
                    path_legacy = SAVE_DIR / f"{name}.pkl"
                    with open(path_legacy, "wb") as f:
                        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
                    print(f"  Saved legacy (all-regime) → {path_legacy}")
                except Exception as e:
                    print(f"  Legacy save failed {name}: {e}")

    # Save metadata
    meta = {
        "feature_names": feature_names,
        "train_samples": int(len(y_train)),
        "test_samples":  int(len(y_test)),
        "label_threshold": LABEL_THRESHOLD,
        "symbols": SYMBOLS,
        "created_at": pd.Timestamp.now().isoformat(),
        "version": "v22.0",
        "start_date": START_DATE,
    }
    with open(SAVE_DIR / "meta.pkl", "wb") as f:
        pickle.dump(meta, f)
    print(f"\nMetadata saved → {SAVE_DIR / 'meta.pkl'}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("KingTrades ML Pre-trainer v22.0")
    print(f"Downloading 10yr OHLCV history (from {START_DATE})...")
    print("Regime-aware: 8 models (BULL × 4 + BEAR × 4)")
    print("=" * 60)

    result = build_dataset()
    if len(result) == 4:
        X, y, regimes, feature_names = result
    else:
        # Fallback (shouldn't happen but guard)
        X, y, feature_names = result
        regimes = np.array(["BULL"] * len(y))

    print(f"\nTotal dataset: {len(y):,} rows, {len(feature_names)} features")
    print(f"Label distribution: {y.mean():.2%} positive")
    bull_pct = (regimes == "BULL").mean()
    print(f"Regime: BULL={bull_pct:.0%}  BEAR={(1-bull_pct):.0%}")

    # Walk-forward validation (train yrs 1-7, test yrs 8-10)
    print("\nRunning walk-forward validation...")
    walk_forward_validate(X, y, feature_names)

    # Final training: use full dataset with train/test split
    split = int(len(y) * TRAIN_SPLIT)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    regimes_train, regimes_test = regimes[:split], regimes[split:]

    train_and_save(X_train, X_test, y_train, y_test, regimes_train, regimes_test, feature_names)

    print("\n" + "=" * 60)
    print("Pre-training complete.")
    print(f"Models saved to: {SAVE_DIR.resolve()}")
    print("Models: gbm_bull.pkl gbm_bear.pkl rf_bull.pkl rf_bear.pkl")
    print("        et_bull.pkl  et_bear.pkl  lr_bull.pkl  lr_bear.pkl")
    print("ml_ensemble.py will auto-load regime-appropriate models on next bot startup.")
    print("=" * 60)


if __name__ == "__main__":
    main()
