"""
Regime-filtered / ensemble research. Does NOT edit repo files.
Imports backtest_strategies as B for pull/enrich/signal fns.
Custom daily loop mirrors B.run() exit logic but gates entries by NIFTY regime.
"""
import backtest_strategies as B
import math, statistics as st, datetime as dt

B.COST_RT = 0.0021  # realistic round-trip

SYMS = ["RELIANCE.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS","TCS.NS","SBIN.NS",
        "AXISBANK.NS","LT.NS","ITC.NS","KOTAKBANK.NS","BHARTIARTL.NS","MARUTI.NS",
        "SUNPHARMA.NS","TITAN.NS","HCLTECH.NS"]
OOS = dt.date(2020,1,1)

# ── Build NIFTY regime map: date -> 'UP' / 'DOWN' / 'RANGE' ───────────────────
def build_regime():
    b = B.enrich("^NSEI")  # gives adx; we add sma200 + slope
    c = [x["c"] for x in b]
    reg = {}
    for i, x in enumerate(b):
        if i < 200:
            reg[x["d"]] = None; continue
        sma200 = sum(c[i-200:i]) / 200
        sma200_prev = sum(c[i-220:i-20]) / 200  # 20 bars earlier
        rising = sma200 > sma200_prev
        adx = x["adx"]
        px = x["c"]
        if px > sma200 and rising and adx >= 20:
            r = "UP"
        elif px < sma200 and not rising and adx >= 20:
            r = "DOWN"
        elif adx < 20:
            r = "RANGE"
        else:
            r = "MIXED"
        reg[x["d"]] = r
    return reg

REGIME = build_regime()

# ── Custom daily backtest with regime gate ───────────────────────────────────
def run_gated(symbols, sigfns, P, allowed_regimes=None, d_from=OOS):
    """allowed_regimes: dict strat_name -> set of allowed regime labels (or None=any).
       sigfns: dict name->fn."""
    data = {s: e for s in symbols if (e := B.enrich(s))}
    if not data: return {"n":0}
    idx = {s: {x["d"]: x for x in d} for s, d in data.items()}
    dates = sorted({x["d"] for d in data.values() for x in d if x["d"] >= d_from})
    eq = B.START; peak = eq; mdd = 0; op = {}; tr = []; curve = []
    C = B.COST_RT
    for day in dates:
        # manage open
        for s in list(op.keys()):
            b = idx[s].get(day)
            if not b: continue
            p = op[s]; p["held"] += 1; ex = None
            if P["trail"]:
                if b["c"] >= p["entry"] + (p["entry"] - p["istop"]):
                    p["stop"] = max(p["stop"], b["c"] - P["sl"]*b["atr"])
            if b["l"] <= p["stop"]: ex = p["stop"]
            elif not p.get("part") and P["partial"] and b["h"] >= p["entry"] + (p["entry"]-p["istop"]):
                half = p["qty"]/2
                g = half*(p["entry"]+(p["entry"]-p["istop"]) - p["entry"])
                cost = C*half*p["entry"]
                eq += g - cost
                p["bookedpnl"] = p.get("bookedpnl",0) + g - cost
                p["realR"] = p.get("realR",0) + (g-cost)/p["risk0"] if p["risk0"]>0 else 0
                p["qty"] -= half; p["stop"] = max(p["stop"], p["entry"]); p["part"] = True
            elif b["h"] >= p["tgt"]: ex = p["tgt"]
            elif p["held"] >= P["ts"]: ex = b["c"]
            if ex is not None:
                g = p["qty"]*(ex - p["entry"]); cost = C*p["qty"]*(p["entry"]+ex)/2
                pnl = g - cost + p.get("bookedpnl",0); eq += g - cost
                R = p.get("realR",0) + ((g-cost)/p["risk0"] if p["risk0"]>0 else 0)
                tr.append({"R":R,"pnl":pnl,"win":pnl>0,"y":day.year,"strat":p["strat"]})
                del op[s]
        # entries (regime gate)
        regime_today = REGIME.get(day)
        if len(op) < B.MAX_POS:
            for s, d in data.items():
                if s in op or len(op) >= B.MAX_POS: continue
                pos = idx[s].get(day)
                if not pos or pos["i"] < 60: continue
                sg = d[pos["i"]-1]
                for nm, fn in sigfns.items():
                    allow = allowed_regimes.get(nm) if allowed_regimes else None
                    if allow is not None and regime_today not in allow:
                        continue
                    try: hit = fn(sg) and sg["atr"] > 0
                    except Exception: hit = False
                    if hit:
                        entry = pos["o"]; stop = entry - P["sl"]*sg["atr"]
                        tgt = entry + P["tp"]*sg["atr"]; rps = entry - stop
                        if rps <= 0: break
                        qty = (P["risk"]*eq)/rps
                        op[s] = {"entry":entry,"stop":stop,"istop":stop,"tgt":tgt,"qty":qty,
                                 "risk0":P["risk"]*eq,"held":0,"strat":nm,"realR":0,"bookedpnl":0}
                        break
        peak = max(peak, eq); mdd = max(mdd,(peak-eq)/peak); curve.append((day,eq))
    n = len(tr)
    if n == 0: return {"n":0}
    wins = [t for t in tr if t["win"]]; los = [t for t in tr if not t["win"]]
    rets = [(curve[i][1]-curve[i-1][1])/curve[i-1][1] for i in range(1,len(curve)) if curve[i-1][1]>0]
    yrs = (dates[-1]-dates[0]).days/365.25
    return {"n":n,"wr":len(wins)/n*100,
            "pf":(sum(t["pnl"] for t in wins)/abs(sum(t["pnl"] for t in los))) if los and sum(t["pnl"] for t in los)!=0 else 99,
            "cagr":((eq/B.START)**(1/yrs)-1)*100 if eq>0 else -100,"mdd":mdd*100,
            "sharpe":(st.mean(rets)/st.pstdev(rets)*math.sqrt(252)) if len(rets)>2 and st.pstdev(rets)>0 else 0}

P = B.P_BETTER  # risk .75%, sl1.5, tp5, ts20, trail+partial

def row(label, flt, r):
    surv = ""
    if r["n"]>=20 and r["pf"]>1.15 and r["cagr"]>0: surv=" <-- SURVIVOR"
    if r["n"]==0:
        print(f"{label:28} {flt:12} n=0"); return
    print(f"{label:28} {flt:12} n={r['n']:4} WR={r['wr']:5.1f}% PF={r['pf']:5.2f} "
          f"Sharpe={r['sharpe']:+5.2f} CAGR={r['cagr']:+6.1f}% MDD={r['mdd']:4.1f}%{surv}")

# regime distribution
from collections import Counter
oos_reg = [v for k,v in REGIME.items() if k>=OOS and v]
print("Regime distribution (OOS days):", dict(Counter(oos_reg)), "\n")

print(f"COST_RT={B.COST_RT}  exits={P}\n")
print(f"{'config':28} {'regime':12} stats")
print("-"*100)

# 1. Baselines (no filter)
row("breakout (all days)", "ANY", run_gated(SYMS, {"breakout":B.s_breakout_vol}, P))
row("meanrev (all days)",  "ANY", run_gated(SYMS, {"meanrev":B.s_meanrev}, P))
row("momentum (all days)", "ANY", run_gated(SYMS, {"momentum":B.s_momentum}, P))

print()
# 2. Regime-filtered
row("breakout", "UP", run_gated(SYMS, {"breakout":B.s_breakout_vol}, P, {"breakout":{"UP"}}))
row("breakout", "UP+RANGE", run_gated(SYMS, {"breakout":B.s_breakout_vol}, P, {"breakout":{"UP","RANGE"}}))
row("meanrev", "RANGE", run_gated(SYMS, {"meanrev":B.s_meanrev}, P, {"meanrev":{"RANGE"}}))
row("meanrev", "RANGE+UP", run_gated(SYMS, {"meanrev":B.s_meanrev}, P, {"meanrev":{"RANGE","UP"}}))
row("momentum", "UP", run_gated(SYMS, {"momentum":B.s_momentum}, P, {"momentum":{"UP"}}))

print()
# 3. Ensemble: breakout on UP + meanrev on RANGE, one equity curve
row("ENSEMBLE bo:UP+mr:RANGE", "split",
    run_gated(SYMS, {"breakout":B.s_breakout_vol,"meanrev":B.s_meanrev}, P,
              {"breakout":{"UP"},"meanrev":{"RANGE"}}))
# ensemble variant incl momentum on UP
row("ENSEMBLE +mom:UP", "split",
    run_gated(SYMS, {"breakout":B.s_breakout_vol,"meanrev":B.s_meanrev,"momentum":B.s_momentum}, P,
              {"breakout":{"UP"},"meanrev":{"RANGE"},"momentum":{"UP"}}))
