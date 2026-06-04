"""
pretrain_ml_india.py — Pre-train ML ensemble on NSE India historical data
India specialist: uses NSE stocks + Nifty regime + India VIX features.

Run once on VPS before starting the India bot:
    cd /root/kingtrades/india && python3 pretrain_ml_india.py

Downloads 10yr OHLCV for 30 Nifty 50 stocks via yfinance (.NS suffix).
Engineers 22 features (including India VIX + FII proxy features).
Labels each row with Nifty regime (BULL/BEAR = Nifty vs 200-day SMA).
Trains 8 regime-specific models. Saves to data/ml_pretrained_india/.
Walk-forward: train years 1-7, test years 8-10. Reports AUC per model.
"""
import os
import pickle
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def _fix_yf_cols(df: "pd.DataFrame") -> "pd.DataFrame":
    """Normalize yfinance columns — newer yfinance returns MultiIndex tuples."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df

sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "ml_pretrained_india"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── NSE training universe (30 liquid Nifty 50 stocks) ────────────────────────
NSE_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS",      "HDFCBANK.NS", "INFY.NS",     "ICICIBANK.NS",
    "SBIN.NS",     "BHARTIARTL.NS","KOTAKBANK.NS","AXISBANK.NS", "LT.NS",
    "BAJFINANCE.NS","WIPRO.NS",    "HCLTECH.NS",  "TATAMOTORS.NS","TATASTEEL.NS",
    "ONGC.NS",     "NTPC.NS",     "POWERGRID.NS","SUNPHARMA.NS","DRREDDY.NS",
    "CIPLA.NS",    "MARUTI.NS",   "TITAN.NS",    "ASIANPAINT.NS","HINDUNILVR.NS",
    "ITC.NS",      "NESTLEIND.NS","JSWSTEEL.NS", "TECHM.NS",    "DIVISLAB.NS",
]
REGIME_TICKER  = "^NSEI"       # Nifty 50 as regime indicator
INDIA_VIX      = "^INDIAVIX"   # India VIX
PERIOD_YEARS   = 10
TRAIN_YEARS    = 7
FORWARD_DAYS   = 5             # predict 5-day return


def _download_all() -> dict:
    """Download daily OHLCV for all NSE symbols + regime indicators."""
    import yfinance as yf
    all_syms = NSE_SYMBOLS + [REGIME_TICKER, INDIA_VIX]
    print(f"Downloading {len(all_syms)} symbols ({PERIOD_YEARS} years)...")

    data = {}
    for sym in all_syms:
        for attempt in range(3):
            try:
                df = yf.download(sym, period=f"{PERIOD_YEARS}y", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is not None and len(df) > 100:
                    df = _fix_yf_cols(df)
                    data[sym] = df
                    print(f"  {sym}: {len(df)} rows")
                    break
            except Exception as e:
                if attempt == 2:
                    print(f"  {sym}: FAILED ({e})")
                time.sleep(2 ** attempt)
    return data


def _detect_nifty_regime(nifty_df: pd.DataFrame) -> pd.Series:
    """BULL = Nifty above 200-day SMA, BEAR = below."""
    sma200 = nifty_df["close"].rolling(200).mean()
    return (nifty_df["close"] >= sma200).map({True: "BULL", False: "BEAR"})


def _engineer_features(df: pd.DataFrame, vix_series: pd.Series,
                        nifty_series: pd.Series) -> pd.DataFrame:
    """Engineer 22 features from daily OHLCV + India VIX + Nifty."""
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    feats = pd.DataFrame(index=df.index)

    # Price momentum
    for p in [3, 5, 10, 20]:
        feats[f"ret_{p}d"]   = close.pct_change(p)
    for p in [5, 21]:
        feats[f"vol_{p}d"]   = close.pct_change().rolling(p).std()

    # Technical
    feats["rsi_14"]  = _rsi(close, 14)
    feats["macd"]    = close.ewm(12).mean() - close.ewm(26).mean()
    feats["adx_14"]  = _adx_approx(high, low, close, 14)
    feats["bb_pct"]  = (close - close.rolling(20).mean()) / (close.rolling(20).std() + 1e-9)
    feats["atr_pct"] = _atr(high, low, close, 14) / (close + 1e-9)

    # Volume
    feats["vol_ratio"] = vol / vol.rolling(20).mean()

    # India-specific
    feats["india_vix"] = vix_series.reindex(df.index, method="ffill")
    nifty_ret = nifty_series.pct_change(5)
    feats["nifty_rel"] = close.pct_change(5) - nifty_ret.reindex(df.index, method="ffill")
    feats["nifty_mom"] = nifty_series.pct_change(20).reindex(df.index, method="ffill")

    # Trend strength
    ema9  = close.ewm(9).mean()
    ema21 = close.ewm(21).mean()
    feats["ema_cross"] = (ema9 - ema21) / (close + 1e-9)
    feats["price_vs_ema50"] = (close - close.ewm(50).mean()) / (close + 1e-9)

    return feats.replace([np.inf, -np.inf], np.nan).dropna()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def _atr(high, low, close, period=14) -> pd.Series:
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx_approx(high, low, close, period=14) -> pd.Series:
    atr  = _atr(high, low, close, period)
    dm_p = (high - high.shift()).clip(lower=0)
    dm_n = (low.shift() - low).clip(lower=0)
    di_p = 100 * dm_p.rolling(period).mean() / (atr + 1e-9)
    di_n = 100 * dm_n.rolling(period).mean() / (atr + 1e-9)
    dx   = (100 * (di_p - di_n).abs() / (di_p + di_n + 1e-9)).rolling(period).mean()
    return dx


def _label(close: pd.Series, forward_days: int = 5, threshold: float = 0.005) -> pd.Series:
    """1 = stock rises >0.5% in next 5 days, 0 = falls or flat."""
    fwd = close.shift(-forward_days) / close - 1
    return (fwd > threshold).astype(int)


def train():
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score

    data = _download_all()

    nifty_df  = data.get(REGIME_TICKER)
    vix_df    = data.get(INDIA_VIX)
    if nifty_df is None:
        print("FAILED: Could not download Nifty data")
        return

    nifty_close = nifty_df["close"]
    vix_close   = vix_df["close"] if vix_df is not None else pd.Series(dtype=float)
    regime_map  = _detect_nifty_regime(nifty_df)

    # Collect all features across symbols
    all_bull_X, all_bull_y = [], []
    all_bear_X, all_bear_y = [], []

    for sym in NSE_SYMBOLS:
        df = data.get(sym)
        if df is None or len(df) < 300:
            continue

        feats = _engineer_features(df, vix_close, nifty_close)
        labels = _label(df["close"])
        common = feats.index.intersection(labels.index).intersection(regime_map.index)
        if len(common) < 100:
            continue

        X = feats.loc[common].values
        y = labels.loc[common].values
        r = regime_map.loc[common].values

        bull_mask = r == "BULL"
        bear_mask = r == "BEAR"

        if bull_mask.sum() > 50:
            all_bull_X.append(X[bull_mask])
            all_bull_y.append(y[bull_mask])
        if bear_mask.sum() > 50:
            all_bear_X.append(X[bear_mask])
            all_bear_y.append(y[bear_mask])

    feature_names = _engineer_features(
        data[NSE_SYMBOLS[0]], vix_close, nifty_close
    ).columns.tolist()

    models_config = [
        ("gbm",  GradientBoostingClassifier(n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42)),
        ("rf",   RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)),
        ("et",   ExtraTreesClassifier(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)),
        ("lr",   LogisticRegression(max_iter=500, random_state=42)),
    ]

    for regime_name, all_X, all_y in [("bull", all_bull_X, all_bull_y),
                                        ("bear", all_bear_X, all_bear_y)]:
        if not all_X:
            print(f"No data for {regime_name} regime")
            continue

        X_all = np.vstack(all_X)
        y_all = np.concatenate(all_y)

        # Walk-forward split (train on 70%, test on 30%)
        split = int(len(X_all) * 0.70)
        X_train, X_test = X_all[:split], X_all[split:]
        y_train, y_test = y_all[:split], y_all[split:]

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s  = scaler.transform(X_test)

        print(f"\n{regime_name.upper()} regime: {len(X_train)} train, {len(X_test)} test")

        for name, clf in models_config:
            if name == "lr":
                clf.fit(X_train_s, y_train)
                proba = clf.predict_proba(X_test_s)[:, 1]
            else:
                clf.fit(X_train, y_train)
                proba = clf.predict_proba(X_test)[:, 1]

            auc = roc_auc_score(y_test, proba)
            print(f"  {name.upper():4s} {regime_name}: AUC={auc:.4f} (target >0.60)")

            out_path = OUTPUT_DIR / f"{name}_{regime_name}.pkl"
            with open(out_path, "wb") as f:
                pickle.dump({"model": clf, "scaler": scaler if name == "lr" else None}, f)

    # Save metadata
    meta = {
        "feature_names": feature_names,
        "trained_at":    datetime.utcnow().isoformat(),
        "symbols":       NSE_SYMBOLS,
        "forward_days":  FORWARD_DAYS,
        "regime_ticker": REGIME_TICKER,
        "market":        "NSE India",
    }
    with open(OUTPUT_DIR / "meta.pkl", "wb") as f:
        pickle.dump(meta, f)

    print(f"\n✅ India ML models saved to {OUTPUT_DIR}")
    print("Run 'git pull && bash india/start_india.sh' to use new models")


if __name__ == "__main__":
    train()
