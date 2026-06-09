"""
swing_research.py — Honest swing / relative-strength research on NSE.
Does NOT edit repo files. Imports backtest_strategies as B.
Costs: B.COST_RT = 0.0021 (realistic round-trip).
"""
import backtest_strategies as B
import datetime as dt, math, statistics as st

B.COST_RT = 0.0021   # realistic NSE round-trip cost
OOS = dt.date(2020, 1, 1)

UNIV = ["RELIANCE.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS","TCS.NS","SBIN.NS",
        "AXISBANK.NS","LT.NS","ITC.NS","KOTAKBANK.NS","BHARTIARTL.NS","MARUTI.NS",
        "SUNPHARMA.NS","TITAN.NS","HCLTECH.NS","BAJFINANCE.NS","ASIANPAINT.NS",
        "ULTRACEMCO.NS","NESTLEIND.NS","TATAMOTORS.NS"]

# ---- weekly momentum helper: store trailing N-day return on each bar ----
def add_returns(b, look=(20, 60, 120)):
    c = [x["c"] for x in b]
    for i, x in enumerate(b):
        for L in look:
            x[f"ret{L}"] = (c[i] / c[i - L] - 1) if i >= L else None
    return b

# ---- swing signal functions (operate on prev bar) ----
def s_trend_swing(b):
    # close>e50, e21>e50, positive ~1-month momentum, trend strength
    return (b["c"] > b["e50"] and b["e21"] > b["e50"]
            and (b.get("ret20") or 0) > 0 and b["adx"] >= 18
            and 45 <= b["rsi"] <= 75)

def s_52wk_breakout(b):
    # breakout to new ~20d highs in a confirmed uptrend (Jegadeesh-Titman flavor)
    return (b["c"] >= b["hh"] * 1.001 and b["e21"] > b["e50"]
            and (b.get("ret120") or 0) > 0 and b["rsi"] < 80)

SWING_STRATS = {"trend_swing": s_trend_swing, "breakout52": s_52wk_breakout}

# Need ret fields available inside run(); patch enrich cache after pull.
_orig_enrich = B.enrich
def enrich_plus(s):
    b = _orig_enrich(s)
    if b and "ret20" not in b[-1]:
        add_returns(b)
    return b
B.enrich = enrich_plus

def fmt(name, hold, r):
    if not r or r.get("n", 0) == 0:
        print(f"  {name:24} {hold:>8}  no trades")
        return None
    surv = (r["n"] >= 20 and r["pf"] > 1.15 and r["cagr"] > 0)
    mark = "  <== SURVIVOR" if surv else ""
    print(f"  {name:24} {hold:>8}  n={r['n']:4} WR={r['wr']:5.1f}% "
          f"PF={r['pf']:5.2f} Sharpe={r['sharpe']:5.2f} CAGR={r['cagr']:+6.1f}% "
          f"MDD={r['mdd']:5.1f}%{mark}")
    return surv

# ============================================================
# PART 1 & 2: trend-following swing + breakout, run() infra
# ============================================================
print("Loading 15y NSE data + computing trailing returns...\n")
for s in UNIV:
    enrich_plus(s)

print("="*96)
print("PART A — TREND-FOLLOWING SWING + BREAKOUT (run() infra), OOS 2020-2026, cost 0.21% RT")
print("="*96)
results = []
exit_grid = [
    ("ts20 tp4",  {"risk":0.0075,"sl":1.5,"tp":4.0,"ts":20,"trail":True,"partial":False}),
    ("ts40 tp6",  {"risk":0.0075,"sl":1.5,"tp":6.0,"ts":40,"trail":True,"partial":False}),
    ("ts60 tp8",  {"risk":0.0075,"sl":1.5,"tp":8.0,"ts":60,"trail":True,"partial":False}),
    ("ts40 tp6 part", {"risk":0.0075,"sl":1.5,"tp":6.0,"ts":40,"trail":True,"partial":True}),
]
for name, fn in SWING_STRATS.items():
    for hold, P in exit_grid:
        r = B.run(UNIV, {name: fn}, P, d_from=OOS)
        surv = fmt(name, hold, r)
        if r and r.get("n"): results.append((f"{name}|{hold}", r, surv))

# ============================================================
# PART 3: CROSS-SECTIONAL RELATIVE STRENGTH (custom loop)
# ============================================================
# Each rebalance: rank universe by trailing LOOK-day return, hold TOP_N
# strongest. Equal-weight. Rebalance every REBAL days. Cost 0.21% round trip
# applied on each name turned over.
def relative_strength_bt(look, top_n, rebal, d_from=OOS):
    data = {s: enrich_plus(s) for s in UNIV}
    data = {s: b for s, b in data.items() if b}
    idx = {s: {x["d"]: x for x in b} for s, b in data.items()}
    dates = sorted({x["d"] for b in data.values() for x in b if x["d"] >= d_from})
    cash = B.START
    holdings = {}            # symbol -> {qty, entry}
    peak = cash; mdd = 0
    curve = []
    trades = []
    last_rebal = -10**9
    di = 0
    for day in dates:
        # mark-to-market portfolio value
        port = cash
        for s, h in holdings.items():
            b = idx[s].get(day)
            if b: port += h["qty"] * b["c"]
        # rebalance?
        if di - last_rebal >= rebal:
            # compute trailing return for each symbol that has data today
            scores = {}
            for s, b in data.items():
                x = idx[s].get(day)
                if x and x["i"] >= look:
                    c = b; j = x["i"]
                    scores[s] = c[j]["c"] / c[j - look]["c"] - 1
            if scores:
                ranked = sorted(scores, key=scores.get, reverse=True)
                target = set(ranked[:top_n])
                # sell positions no longer in target
                for s in list(holdings.keys()):
                    if s not in target:
                        b = idx[s].get(day)
                        if not b:  # no price today, hold
                            continue
                        px = b["c"]; h = holdings.pop(s)
                        proceeds = h["qty"] * px
                        cost = B.COST_RT * proceeds  # exit leg
                        cash += proceeds - cost
                        trades.append(px / h["entry"] - 1)
                # buy new entrants (equal weight of current port value / top_n)
                # recompute available value
                pv = cash + sum(holdings[s]["qty"] * idx[s][day]["c"]
                                for s in holdings if idx[s].get(day))
                per = pv / top_n
                for s in target:
                    if s in holdings: continue
                    b = idx[s].get(day)
                    if not b: continue
                    px = b["c"]
                    spend = min(per, cash)
                    if spend <= 0: continue
                    qty = spend / px
                    cost = B.COST_RT * spend  # entry leg
                    cash -= spend
                    cash -= cost
                    holdings[s] = {"qty": qty, "entry": px}
                last_rebal = di
        # equity curve
        port = cash + sum(holdings[s]["qty"] * idx[s][day]["c"]
                          for s in holdings if idx[s].get(day))
        peak = max(peak, port); mdd = max(mdd, (peak - port) / peak)
        curve.append((day, port)); di += 1
    if len(curve) < 3:
        return {"n": 0}
    finalv = curve[-1][1]
    rets = [(curve[i][1] - curve[i-1][1]) / curve[i-1][1]
            for i in range(1, len(curve)) if curve[i-1][1] > 0]
    yrs = (dates[-1] - dates[0]).days / 365.25
    wins = [t for t in trades if t > 0]; los = [t for t in trades if t <= 0]
    return {
        "n": len(trades),
        "wr": (len(wins)/len(trades)*100) if trades else 0,
        "pf": (sum(wins)/abs(sum(los))) if los and sum(los) != 0 else 99,
        "cagr": ((finalv/B.START)**(1/yrs)-1)*100 if finalv > 0 else -100,
        "mdd": mdd*100,
        "sharpe": (st.mean(rets)/st.pstdev(rets)*math.sqrt(252))
                  if len(rets) > 2 and st.pstdev(rets) > 0 else 0,
    }

print()
print("="*96)
print("PART B — CROSS-SECTIONAL RELATIVE STRENGTH (custom loop), OOS 2020-2026, cost 0.21% per leg")
print("="*96)
print("  rule: rank universe by trailing-return, hold TOP-N strongest, rebalance every R days")
for look in (60, 120, 250):
    for top_n in (3, 5):
        for rebal in (10, 20):
            r = relative_strength_bt(look, top_n, rebal)
            fmt(f"RS look{look} top{top_n}", f"reb{rebal}", r)
            if r and r.get("n"): results.append((f"RS_look{look}_top{top_n}_reb{rebal}", r, (r["n"]>=20 and r["pf"]>1.15 and r["cagr"]>0)))

# ---- buy & hold benchmark (equal weight whole universe) ----
def buyhold():
    data = {s: enrich_plus(s) for s in UNIV}
    data = {s: b for s, b in data.items() if b}
    idx = {s: {x["d"]: x for x in b} for s, b in data.items()}
    dates = sorted({x["d"] for b in data.values() for x in b if x["d"] >= OOS})
    first = dates[0]
    start_px = {s: idx[s][first]["c"] for s in data if idx[s].get(first)}
    curve = []
    peak = B.START; mdd = 0
    for day in dates:
        vals = [idx[s][day]["c"]/start_px[s] for s in start_px if idx[s].get(day)]
        port = B.START * (sum(vals)/len(vals)) if vals else B.START
        peak = max(peak, port); mdd = max(mdd, (peak-port)/peak)
        curve.append(port)
    rets = [(curve[i]-curve[i-1])/curve[i-1] for i in range(1,len(curve)) if curve[i-1]>0]
    yrs = (dates[-1]-dates[0]).days/365.25
    return {"cagr": ((curve[-1]/B.START)**(1/yrs)-1)*100, "mdd": mdd*100,
            "sharpe": st.mean(rets)/st.pstdev(rets)*math.sqrt(252)}
bh = buyhold()
print(f"\n  BENCHMARK equal-weight buy&hold: CAGR={bh['cagr']:+.1f}% MDD={bh['mdd']:.1f}% Sharpe={bh['sharpe']:.2f}")

# ---- summary of survivors ----
print("\n" + "="*96)
print("SURVIVORS (n>=20, PF>1.15, CAGR>0 after 0.21% cost):")
print("="*96)
survs = [(nm, r) for nm, r, s in results if s]
if not survs:
    print("  NONE.")
else:
    survs.sort(key=lambda kv: kv[1]["sharpe"], reverse=True)
    for nm, r in survs:
        print(f"  {nm:30} n={r['n']:4} WR={r['wr']:5.1f}% PF={r['pf']:.2f} "
              f"Sharpe={r['sharpe']:.2f} CAGR={r['cagr']:+.1f}% MDD={r['mdd']:.1f}%")
    best = survs[0]
    print(f"\n  BEST (by Sharpe): {best[0]}  ->  {best[1]}")
