"""
backtest_sweep.py — REAL multi-market backtest + WALK-FORWARD parameter sweep.

Honest methodology (what separates elite systems from curve-fit fantasy):
  1. Pull 15y REAL daily candles (Yahoo) for an NSE basket AND a US basket.
  2. Baseline both with the bot's core momentum gates.
  3. Walk-forward: optimize parameters on IN-SAMPLE (2011-2019), then measure
     performance on OUT-OF-SAMPLE (2020-2026) data the optimizer never saw.
     OOS results are the only ones that mean anything.

Still a DAILY-BAR proxy of the live 5-min engine — directional truth, not the
exact intraday number.
"""
import urllib.request, json, math, datetime as dt, statistics as st
from typing import List, Dict, Optional

NSE = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS","SBIN.NS",
       "AXISBANK.NS","LT.NS","ITC.NS","HINDUNILVR.NS","KOTAKBANK.NS","BHARTIARTL.NS",
       "MARUTI.NS","SUNPHARMA.NS","BAJFINANCE.NS","TITAN.NS","ASIANPAINT.NS","WIPRO.NS","TATASTEEL.NS"]
US  = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","JPM","V","UNH",
       "HD","MA","XOM","JNJ","WMT","PG","AVGO","COST","CRM","AMD"]

COST_RT=0.0010; START=500000.0; MAX_POS=5
_CACHE={}

def pull(sym):
    if sym in _CACHE: return _CACHE[sym]
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=15y&interval=1d"
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=30)
        res=json.loads(r.read())["chart"]["result"][0]; ts=res["timestamp"]; q=res["indicators"]["quote"][0]
        bars=[]
        for i in range(len(ts)):
            o,h,l,c,v=q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i]
            if None in (o,h,l,c,v): continue
            bars.append({"d":dt.date.fromtimestamp(ts[i]),"o":o,"h":h,"l":l,"c":c,"v":v})
        _CACHE[sym]=bars if len(bars)>200 else None
    except Exception:
        _CACHE[sym]=None
    return _CACHE[sym]

def ema(v,s):
    k=2/(s+1);o=[v[0]]
    for x in v[1:]:o.append(x*k+o[-1]*(1-k))
    return o
def rsi(c,n=14):
    o=[50.0]*len(c);ag=al=0
    for i in range(1,len(c)):
        ch=c[i]-c[i-1];g=max(ch,0);l=max(-ch,0)
        if i<=n:ag=(ag*(i-1)+g)/i;al=(al*(i-1)+l)/i
        else:ag=(ag*(n-1)+g)/n;al=(al*(n-1)+l)/n
        o[i]=100-100/(1+ag/al) if al>0 else 100.0
    return o
def atr(b,n=14):
    tr=[b[0]["h"]-b[0]["l"]]
    for i in range(1,len(b)):
        tr.append(max(b[i]["h"]-b[i]["l"],abs(b[i]["h"]-b[i-1]["c"]),abs(b[i]["l"]-b[i-1]["c"])))
    a=tr[0];o=[a]
    for i in range(1,len(b)):
        a=(a*(n-1)+tr[i])/n if i>=n else (a*i+tr[i])/(i+1);o.append(a)
    return o
def adx(b,n=14):
    plus=[0];minus=[0];tr=[b[0]["h"]-b[0]["l"]]
    for i in range(1,len(b)):
        up=b[i]["h"]-b[i-1]["h"];dn=b[i-1]["l"]-b[i]["l"]
        plus.append(up if(up>dn and up>0)else 0);minus.append(dn if(dn>up and dn>0)else 0)
        tr.append(max(b[i]["h"]-b[i]["l"],abs(b[i]["h"]-b[i-1]["c"]),abs(b[i]["l"]-b[i-1]["c"])))
    def sm(x):
        s=x[0];o=[s]
        for i in range(1,len(x)):s=s-s/n+x[i];o.append(s)
        return o
    str_,sp,sm_=sm(tr),sm(plus),sm(minus);o=[0.0]*len(b);dxs=[]
    for i in range(len(b)):
        if str_[i]>0:
            pdi=100*sp[i]/str_[i];mdi=100*sm_[i]/str_[i]
            dx=100*abs(pdi-mdi)/(pdi+mdi) if(pdi+mdi)>0 else 0;dxs.append(dx)
            o[i]=sum(dxs[-n:])/min(len(dxs),n)
    return o

def enrich(sym):
    b=pull(sym)
    if not b: return None
    c=[x["c"] for x in b];e9,e21,e50=ema(c,9),ema(c,21),ema(c,50);r=rsi(c);a=atr(b);ad=adx(b)
    for i,x in enumerate(b):
        x["e9"],x["e21"],x["e50"],x["rsi"],x["atr"],x["adx"]=e9[i],e21[i],e50[i],r[i],a[i],ad[i]
        x["hh"]=max(y["h"] for y in b[max(0,i-25):i]) if i>=25 else x["h"]
        x["vsma"]=sum(y["v"] for y in b[max(0,i-20):i])/max(1,min(i,20)) if i>=20 else x["v"]
        x["i"]=i
    return b

def run(symbols, P, d_from=None, d_to=None):
    data={}
    for s in symbols:
        e=enrich(s)
        if e: data[s]=e
    if not data: return None
    idx={s:{x["d"]:x for x in d} for s,d in data.items()}
    dates=sorted({x["d"] for d in data.values() for x in d if (not d_from or x["d"]>=d_from) and (not d_to or x["d"]<=d_to)})
    eq=START;peak=eq;mdd=0;openp={};trades=[];curve=[]
    def sig(b):
        return (b["e9"]>b["e21"]>b["e50"] and P["rlo"]<=b["rsi"]<=P["rhi"] and
                b["c"]>=b["hh"]*0.999 and b["v"]>=P["vol"]*b["vsma"] and b["adx"]>=P["adx"] and b["atr"]>0)
    for day in dates:
        for s in list(openp.keys()):
            b=idx[s].get(day)
            if not b: continue
            p=openp[s];p["held"]+=1;ex=None
            # trailing stop
            if P.get("trail") and b["c"]-P["sl"]*b["atr"]>p["stop"]:
                p["stop"]=max(p["stop"],b["c"]-P["sl"]*b["atr"])
            if b["l"]<=p["stop"]:ex=p["stop"]
            elif b["h"]>=p["tgt"]:ex=p["tgt"]
            elif p["held"]>=P["ts"]:ex=b["c"]
            if ex is not None:
                g=p["qty"]*(ex-p["entry"]);cost=COST_RT*p["qty"]*(p["entry"]+ex)/2;pnl=g-cost;eq+=pnl
                trades.append({"R":pnl/p["risk"] if p["risk"]>0 else 0,"pnl":pnl,"win":pnl>0,"y":day.year})
                del openp[s]
        if len(openp)<MAX_POS:
            for s,d in data.items():
                if s in openp or len(openp)>=MAX_POS: continue
                pos=idx[s].get(day)
                if not pos or pos["i"]<60: continue
                sg=d[pos["i"]-1]
                if sig(sg):
                    entry=pos["o"];stop=entry-P["sl"]*sg["atr"];tgt=entry+P["tp"]*sg["atr"]
                    rps=entry-stop
                    if rps<=0: continue
                    qty=(P["risk"]*eq)/rps
                    openp[s]={"entry":entry,"stop":stop,"tgt":tgt,"qty":qty,"risk":P["risk"]*eq,"held":0}
        peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak);curve.append((day,eq))
    n=len(trades)
    if n==0: return {"n":0}
    wins=[t for t in trades if t["win"]];los=[t for t in trades if not t["win"]]
    rets=[(curve[i][1]-curve[i-1][1])/curve[i-1][1] for i in range(1,len(curve)) if curve[i-1][1]>0]
    yrs=(dates[-1]-dates[0]).days/365.25
    return {"n":n,"wr":len(wins)/n*100,"exp":sum(t["R"] for t in trades)/n,
            "pf":(sum(t["pnl"] for t in wins)/abs(sum(t["pnl"] for t in los))) if los and sum(t["pnl"] for t in los)!=0 else 99,
            "cagr":((eq/START)**(1/yrs)-1)*100 if eq>0 and yrs>0 else -100,"mdd":mdd*100,
            "sharpe":(st.mean(rets)/st.pstdev(rets)*math.sqrt(252)) if len(rets)>2 and st.pstdev(rets)>0 else 0,
            "eq":eq}

BASE={"risk":0.005,"sl":1.5,"tp":3.0,"rlo":50,"rhi":72,"adx":20,"vol":1.5,"ts":12,"trail":False}

def show(tag,r):
    if not r or r["n"]==0: print(f"  {tag:22} no trades"); return
    print(f"  {tag:22} trades={r['n']:4} WR={r['wr']:4.1f}% exp={r['exp']:+.3f}R PF={r['pf']:.2f} "
          f"CAGR={r['cagr']:+5.1f}% MDD={r['mdd']:4.1f}% Sharpe={r['sharpe']:.2f}")

if __name__=="__main__":
    print("Loading 15y real data (NSE + US)...")
    print("\n===== BASELINE (full 15y, 0.5% risk) =====")
    show("NSE baseline", run(NSE,BASE))
    show("US baseline",  run(US,BASE))

    print("\n===== WALK-FORWARD (optimize IS 2011-2019 → test OOS 2020-2026) =====")
    IS_TO=dt.date(2019,12,31); OOS_FROM=dt.date(2020,1,1)
    grid=[]
    for risk in (0.005,0.0075,0.01):
        for sl,tp in ((1.5,3.0),(2.0,4.0),(1.0,2.5)):
            for trail in (False,True):
                for adxm in (20,25):
                    grid.append({**BASE,"risk":risk,"sl":sl,"tp":tp,"trail":trail,"adx":adxm})
    for mkt,bask in (("NSE",NSE),("US",US)):
        best=None;bestp=None
        for P in grid:
            r=run(bask,P,d_to=IS_TO)
            if r and r["n"]>=30 and (best is None or r["sharpe"]>best["sharpe"]):
                best=r;bestp=P
        if bestp:
            print(f"\n  [{mkt}] best IN-SAMPLE params: risk={bestp['risk']*100:.2f}% "
                  f"SL={bestp['sl']} TP={bestp['tp']} trail={bestp['trail']} ADX={bestp['adx']}")
            show(f"{mkt} in-sample", best)
            show(f"{mkt} OUT-of-sample", run(bask,bestp,d_from=OOS_FROM))
    print("\n  ⚠ OOS row = the only honest number. Daily-bar proxy, not live 5-min.")
