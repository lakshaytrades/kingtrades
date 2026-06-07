"""
ML Pre-trainer — bootstraps ML ensemble with historical market data.

Downloads 2 years of daily data for 50+ symbols, computes the full
55-feature vector that ml_ensemble.py uses, labels each bar with
binary outcome (next 5 bars: +1% return = WIN, -0.5% = LOSS, else skip),
and pre-trains all 5 models. Saves to data/ml_pretrained/.

Run: python ml_pretrain.py
     python ml_pretrain.py --symbols SPY QQQ AAPL MSFT --period 1y
"""

import argparse
import logging
import os
import pickle
import sys
from datetime import datetime

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Symbols to train on — diverse mix of sectors and volatility profiles
TRAINING_SYMBOLS = [
    # US Large Caps
    "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "JPM", "BAC", "GS", "V", "MA", "XOM", "CVX", "JNJ", "PFE", "UNH",
    "HD", "WMT", "KO", "PG", "ABBV", "MRK", "AMD", "INTC", "CRM",
    # ETFs for context
    "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "XLI", "GLD", "TLT",
    # High-beta momentum stocks
    "MARA", "RIOT", "COIN", "PLTR", "SOFI", "LCID", "F", "GM",
    # India proxy (listed on US)
    "INFY", "WIT", "HDB", "IBN",
]

FEATURE_NAMES = [
    # Price momentum
    "ret_1d", "ret_5d", "ret_10d", "ret_20d",
    # RSI variants
    "rsi_14", "rsi_7", "rsi_21",
    # MACD
    "macd", "macd_signal", "macd_hist",
    # Bollinger
    "bb_upper_dist", "bb_lower_dist", "bb_width", "bb_pct",
    # Volume
    "volume_ratio_5", "volume_ratio_20", "obv_roc",
    # ATR
    "atr_14", "atr_pct",
    # EMA spreads
    "ema9_spread", "ema21_spread", "ema50_spread",
    # Stochastic
    "stoch_k", "stoch_d",
    # ADX
    "adx", "di_plus", "di_minus",
    # Cross features
    "rsi_x_volume", "macd_x_adx", "rsi_sq",
    # Price position
    "high_52w_pct", "low_52w_pct", "range_pct",
    # Time features (cyclical)
    "time_sin", "time_cos", "dow_sin", "dow_cos",
    "month_sin", "month_cos",
    # Market context
    "spy_return_1d", "spy_return_5d", "vix_level", "vix_change",
    # Trend quality
    "trend_strength", "r2_5", "r2_10",
    # Volatility
    "hist_vol_10", "hist_vol_20", "vol_ratio",
    # Regime proxy
    "above_ema50", "above_ema200", "golden_cross",
    # Momentum quality
    "mom_acceleration", "mom_consistency",
    # Placeholder features (for ensemble compatibility)
    "f53", "f54", "f55",
]


def download_data(symbols: list, period: str = "2y", interval: str = "1d"):
    """Download OHLCV data for all symbols."""
    import yfinance as yf
    all_data = {}
    for sym in symbols:
        try:
            df = yf.download(sym, period=period, interval=interval,
                              progress=False, auto_adjust=True)
            if df is not None and len(df) >= 60:
                all_data[sym] = df
                logger.info(f"  Downloaded {sym}: {len(df)} bars")
            else:
                logger.warning(f"  Skipping {sym}: insufficient data")
        except Exception as e:
            logger.warning(f"  Failed {sym}: {e}")
    return all_data


def compute_features(df, spy_df=None, vix_df=None) -> np.ndarray:
    """Compute 55-feature vector for each bar. Returns (n_bars, 55) array."""
    import pandas as pd

    closes = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(closes)

    if n < 60:
        return np.zeros((0, 55))

    features = []

    for i in range(60, n - 5):  # leave 5 bars for label
        c = closes[i]

        # ─── Price momentum ───
        r1 = closes[i] / closes[i-1] - 1 if closes[i-1] != 0 else 0
        r5 = closes[i] / closes[i-5] - 1 if closes[i-5] != 0 else 0
        r10 = closes[i] / closes[i-10] - 1 if closes[i-10] != 0 else 0
        r20 = closes[i] / closes[i-20] - 1 if closes[i-20] != 0 else 0

        # ─── RSI ───
        def rsi(period):
            deltas = np.diff(closes[max(0, i-period-1):i+1])
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            ag = np.mean(gains[-period:]) if len(gains) >= period else 0
            al = np.mean(losses[-period:]) if len(losses) >= period else 0
            return 100 - 100 / (1 + ag / (al + 1e-10))

        rsi14 = rsi(14) / 100.0
        rsi7 = rsi(7) / 100.0
        rsi21 = rsi(21) / 100.0

        # ─── MACD ───
        def ema(data, p):
            a = 2 / (p + 1)
            e = data[0]
            for x in data[1:]:
                e = a * x + (1 - a) * e
            return e

        macd_slice = closes[i-35:i+1]
        ema12 = ema(macd_slice, 12)
        ema26 = ema(macd_slice, 26)
        macd_val = (ema12 - ema26) / c if c != 0 else 0

        # Signal is simplified here
        macd_sig = macd_val * 0.9  # approximation
        macd_hist_v = macd_val - macd_sig

        # ─── Bollinger ───
        w20 = closes[i-19:i+1]
        bb_ma = np.mean(w20)
        bb_std = np.std(w20)
        bb_upper = bb_ma + 2 * bb_std
        bb_lower = bb_ma - 2 * bb_std
        bb_width = (2 * bb_std) / bb_ma if bb_ma != 0 else 0
        bb_pct = (c - bb_lower) / (bb_upper - bb_lower + 1e-10)
        bb_upper_dist = (bb_upper - c) / c if c != 0 else 0
        bb_lower_dist = (c - bb_lower) / c if c != 0 else 0

        # ─── Volume ───
        vol_ratio5 = volumes[i] / (np.mean(volumes[i-5:i]) + 1e-10)
        vol_ratio20 = volumes[i] / (np.mean(volumes[i-20:i]) + 1e-10)
        obv_roc = (volumes[i] * np.sign(r1)) / (np.mean(volumes[i-5:i]) + 1e-10)

        # ─── ATR ───
        tr_vals = np.maximum(highs[i-14:i+1] - lows[i-14:i+1],
                   np.maximum(np.abs(highs[i-14:i+1] - np.roll(closes[i-14:i+1], 1)),
                              np.abs(lows[i-14:i+1] - np.roll(closes[i-14:i+1], 1))))
        atr = np.mean(tr_vals[1:])
        atr_pct = atr / c if c != 0 else 0

        # ─── EMAs ───
        ema9 = ema(closes[i-9:i+1], 9)
        ema21 = ema(closes[i-21:i+1], 21)
        ema50 = ema(closes[i-50:i+1], 50)
        ema9_sp = (c - ema9) / c if c != 0 else 0
        ema21_sp = (c - ema21) / c if c != 0 else 0
        ema50_sp = (c - ema50) / c if c != 0 else 0

        # ─── Stochastic ───
        h14 = highs[i-13:i+1].max()
        l14 = lows[i-13:i+1].min()
        stoch_k = (c - l14) / (h14 - l14 + 1e-10)
        stoch_d = stoch_k  # simplified

        # ─── ADX proxy ───
        diffs = np.diff(closes[i-14:i+1])
        adx_val = abs(np.mean(diffs)) / (atr + 1e-10)
        di_plus = max(0, np.sum(diffs[diffs > 0])) / (atr * 14 + 1e-10)
        di_minus = max(0, np.sum(-diffs[diffs < 0])) / (atr * 14 + 1e-10)

        # ─── Cross features ───
        rsi_x_vol = rsi14 * vol_ratio5
        macd_x_adx = macd_val * adx_val
        rsi_sq = rsi14 ** 2

        # ─── Price position ───
        h52 = highs[max(0, i-252):i+1].max()
        l52 = lows[max(0, i-252):i+1].min()
        h52_pct = (c - h52) / h52 if h52 != 0 else 0
        l52_pct = (c - l52) / l52 if l52 != 0 else 0
        range_pct = (c - l52) / (h52 - l52 + 1e-10)

        # ─── Time features ───
        import math
        day_of_year = i % 252
        dow = i % 5
        month = (i // 21) % 12
        time_sin = math.sin(2 * math.pi * day_of_year / 252)
        time_cos = math.cos(2 * math.pi * day_of_year / 252)
        dow_sin = math.sin(2 * math.pi * dow / 5)
        dow_cos = math.cos(2 * math.pi * dow / 5)
        month_sin = math.sin(2 * math.pi * month / 12)
        month_cos = math.cos(2 * math.pi * month / 12)

        # ─── Market context ───
        spy_ret1 = 0.0
        spy_ret5 = 0.0
        vix_level = 0.20
        vix_chg = 0.0
        if spy_df is not None and i < len(spy_df):
            spy_closes = spy_df["Close"].values.astype(float)
            if i < len(spy_closes) and i >= 5:
                spy_ret1 = spy_closes[i] / spy_closes[i-1] - 1 if spy_closes[i-1] != 0 else 0
                spy_ret5 = spy_closes[i] / spy_closes[i-5] - 1 if spy_closes[i-5] != 0 else 0
        if vix_df is not None and i < len(vix_df):
            vix_closes = vix_df["Close"].values.astype(float)
            if i < len(vix_closes):
                vix_level = vix_closes[i] / 100.0
                vix_chg = (vix_closes[i] / vix_closes[i-1] - 1) if i > 0 and vix_closes[i-1] != 0 else 0

        # ─── Trend quality ───
        r2_5_slope = np.polyfit(range(5), closes[i-4:i+1], 1)[0]
        r2_5 = np.corrcoef(range(5), closes[i-4:i+1])[0, 1] ** 2
        r2_10 = np.corrcoef(range(10), closes[i-9:i+1])[0, 1] ** 2 if i >= 9 else 0
        trend_strength = abs(r2_5_slope) / (c + 1e-10)

        # ─── Historical volatility ───
        hv10 = np.std(np.diff(np.log(closes[i-10:i+1] + 1e-10)))
        hv20 = np.std(np.diff(np.log(closes[i-20:i+1] + 1e-10)))
        vol_ratio_v = hv10 / (hv20 + 1e-10)

        # ─── Regime ───
        above_ema50 = float(c > ema50)
        ema200 = ema(closes[max(0, i-200):i+1], 200) if i >= 200 else ema50
        above_ema200 = float(c > ema200)
        golden_cross = float(ema50 > ema200) if i >= 200 else 0.5

        # ─── Momentum quality ───
        rets = np.diff(closes[i-5:i+1]) / closes[i-5:i]
        mom_accel = rets[-1] - rets[0] if len(rets) >= 2 else 0
        mom_consistency = np.sum(rets > 0) / len(rets) if len(rets) > 0 else 0.5

        fvec = [
            r1, r5, r10, r20,
            rsi14, rsi7, rsi21,
            macd_val, macd_sig, macd_hist_v,
            bb_upper_dist, bb_lower_dist, bb_width, bb_pct,
            vol_ratio5, vol_ratio20, obv_roc,
            atr_pct, atr_pct,  # atr_14 (normalized), atr_pct
            ema9_sp, ema21_sp, ema50_sp,
            stoch_k, stoch_d,
            adx_val, di_plus, di_minus,
            rsi_x_vol, macd_x_adx, rsi_sq,
            h52_pct, l52_pct, range_pct,
            time_sin, time_cos, dow_sin, dow_cos, month_sin, month_cos,
            spy_ret1, spy_ret5, vix_level, vix_chg,
            trend_strength, r2_5, r2_10,
            hv10, hv20, vol_ratio_v,
            above_ema50, above_ema200, golden_cross,
            mom_accel, mom_consistency,
            0.0, 0.0, 0.0,  # f53, f54, f55 placeholders
        ]
        features.append(fvec)

    arr = np.array(features, dtype=float)
    # Replace inf/nan
    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
    arr = np.clip(arr, -10.0, 10.0)
    return arr


def compute_labels(df) -> np.ndarray:
    """Binary label: 1 if next 5 bars return > +1%, 0 if < -0.5%, skip otherwise."""
    closes = df["Close"].values.astype(float)
    n = len(closes)
    labels = []
    for i in range(60, n - 5):
        fwd_ret = closes[i+5] / closes[i] - 1
        if fwd_ret > 0.01:
            labels.append(1)
        elif fwd_ret < -0.005:
            labels.append(0)
        else:
            labels.append(-1)  # skip (noise zone)
    return np.array(labels)


def train_models(X: np.ndarray, y: np.ndarray, output_dir: str):
    """Train all 5 models and save to output_dir."""
    os.makedirs(output_dir, exist_ok=True)

    # Filter out "skip" labels
    mask = y != -1
    X_clean = X[mask]
    y_clean = y[mask]

    logger.info(f"Training on {len(X_clean)} samples ({y_clean.sum()} wins, {(1-y_clean).sum()} losses)")

    if len(X_clean) < 100:
        logger.warning("Too few samples for training")
        return

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_clean)

    # Save scaler
    with open(os.path.join(output_dir, "scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)

    models = {}

    # 1. GradientBoosting
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        logger.info("Training GBM...")
        gbm = GradientBoostingClassifier(n_estimators=200, max_depth=4,
                                          learning_rate=0.05, subsample=0.8,
                                          random_state=42)
        gbm.fit(X_clean, y_clean)  # GBM doesn't need scaled features
        models["gbm"] = gbm
        logger.info(f"  GBM score: {gbm.score(X_clean, y_clean):.3f}")
    except Exception as e:
        logger.warning(f"GBM failed: {e}")

    # 2. RandomForest
    try:
        from sklearn.ensemble import RandomForestClassifier
        logger.info("Training RF...")
        rf = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_leaf=5,
                                     n_jobs=-1, random_state=42)
        rf.fit(X_clean, y_clean)
        models["rf"] = rf
        logger.info(f"  RF score: {rf.score(X_clean, y_clean):.3f}")
    except Exception as e:
        logger.warning(f"RF failed: {e}")

    # 3. ExtraTrees
    try:
        from sklearn.ensemble import ExtraTreesClassifier
        logger.info("Training ET...")
        et = ExtraTreesClassifier(n_estimators=200, max_depth=10, n_jobs=-1, random_state=42)
        et.fit(X_clean, y_clean)
        models["et"] = et
        logger.info(f"  ET score: {et.score(X_clean, y_clean):.3f}")
    except Exception as e:
        logger.warning(f"ET failed: {e}")

    # 4. Logistic Regression
    try:
        from sklearn.linear_model import LogisticRegression
        logger.info("Training LR...")
        lr = LogisticRegression(C=0.1, max_iter=1000, random_state=42)
        lr.fit(X_scaled, y_clean)
        models["lr"] = lr
        logger.info(f"  LR score: {lr.score(X_scaled, y_clean):.3f}")
    except Exception as e:
        logger.warning(f"LR failed: {e}")

    # 5. MLP
    try:
        from sklearn.neural_network import MLPClassifier
        logger.info("Training MLP...")
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32, 16), activation="relu",
                             alpha=0.001, max_iter=300, random_state=42, early_stopping=True)
        mlp.fit(X_scaled, y_clean)
        models["mlp"] = mlp
        logger.info(f"  MLP score: {mlp.score(X_scaled, y_clean):.3f}")
    except Exception as e:
        logger.warning(f"MLP failed: {e}")

    # Save all models
    for name, model in models.items():
        path = os.path.join(output_dir, f"{name}.pkl")
        with open(path, "wb") as f:
            pickle.dump(model, f)
        logger.info(f"  Saved {name}.pkl ({os.path.getsize(path)//1024}KB)")

    # Save training metadata
    meta = {
        "trained_at": datetime.now().isoformat(),
        "n_samples": int(len(X_clean)),
        "n_features": int(X.shape[1]),
        "win_rate": float(y_clean.mean()),
        "models": list(models.keys()),
    }
    import json
    with open(os.path.join(output_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    logger.info(f"All models saved to {output_dir}/")
    return models


def main():
    parser = argparse.ArgumentParser(description="Pre-train KingTrades ML models")
    parser.add_argument("--symbols", nargs="+", default=TRAINING_SYMBOLS,
                        help="Symbols to train on")
    parser.add_argument("--period", default="2y", help="Data period (1y, 2y, 5y)")
    parser.add_argument("--output", default="data/ml_pretrained",
                        help="Output directory for models")
    args = parser.parse_args()

    logger.info(f"KingTrades ML Pre-trainer")
    logger.info(f"Symbols: {len(args.symbols)} | Period: {args.period} | Output: {args.output}")

    logger.info("Downloading market data...")
    all_data = download_data(args.symbols, period=args.period)

    # Download SPY and VIX for context features
    spy_data = all_data.get("SPY")
    logger.info("Downloading VIX...")
    try:
        import yfinance as yf
        vix_data = yf.download("^VIX", period=args.period, interval="1d",
                                progress=False, auto_adjust=True)
    except Exception:
        vix_data = None

    logger.info("Computing features and labels...")
    all_X = []
    all_y = []

    for sym, df in all_data.items():
        X = compute_features(df, spy_data, vix_data)
        y = compute_labels(df)
        min_len = min(len(X), len(y))
        if min_len >= 50:
            all_X.append(X[:min_len])
            all_y.append(y[:min_len])
            logger.info(f"  {sym}: {min_len} samples")

    if not all_X:
        logger.error("No training data!")
        sys.exit(1)

    X_combined = np.vstack(all_X)
    y_combined = np.concatenate(all_y)

    # Shuffle
    idx = np.random.RandomState(42).permutation(len(X_combined))
    X_combined = X_combined[idx]
    y_combined = y_combined[idx]

    logger.info(f"Total training data: {len(X_combined)} samples, {X_combined.shape[1]} features")
    logger.info("Training models...")

    train_models(X_combined, y_combined, args.output)
    logger.info("Pre-training complete!")


if __name__ == "__main__":
    main()
